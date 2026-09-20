"""Tests for Sensi client component."""

import asyncio
import contextlib
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sensi.auth import AuthenticationError, SensiConnectionError
from custom_components.sensi.client import ActionResponse, round_humidity
from custom_components.sensi.data import (
    AuthenticationConfig,
    FanMode,
    OperatingMode,
    State,
)
from custom_components.sensi.event import (
    SetCirculatingFanEvent,
    SetCirculatingFanEventValue,
    SetFanModeEvent,
    SetHumidityEvent,
    SetHumidityEventValue,
    SetOperatingModeEvent,
    SetTemperatureEvent,
    SetTemperatureEventSuccess,
)
from custom_components.sensi.utils import redact_identifier
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from tests.e2e.conftest import FakeSensiBackend, FakeSensiSocket


class GatedSocket(FakeSensiSocket):
    """The e2e fake socket with the library's two connection-state quirks.

    ``socketio.AsyncClient`` sets ``connected`` only at the very end of
    ``connect()``, its ``shutdown()`` does nothing for a client that is neither
    connected nor reconnecting, and ``wait()`` on such a client blocks on the
    live engine.io transport. The e2e fake connects in one step and
    disconnects unconditionally, which hides the window between those two
    facts - the window a second caller replacing the socket falls into.

    ``gate``, when set, holds ``connect()`` mid-handshake until it is released.
    """

    def __init__(self, backend: FakeSensiBackend, gate: asyncio.Event | None) -> None:
        """Bind to the backend; hold connect() at the gate when one is given."""
        super().__init__(backend)
        self._gate = gate
        self.handshaking = False
        # True once someone called wait() on this socket mid-handshake - the
        # DISCONNECT_TIMEOUT stall the real library would have cost them.
        self.stalled = False
        # Event names emitted through this socket in particular; the backend
        # records emits without saying which socket carried them.
        self.emitted: list[str] = []

    async def emit(self, name: str, *args, **kwargs) -> None:
        """Record the emit against this socket, then hand it to the backend."""
        self.emitted.append(name)
        await super().emit(name, *args, **kwargs)

    async def connect(self, url: str, **kwargs) -> None:
        """Connect once the gate, if any, is released."""
        self.handshaking = True
        try:
            if self._gate is not None:
                await self._gate.wait()
            await super().connect(url, **kwargs)
        finally:
            self.handshaking = False

    async def shutdown(self) -> None:
        """Disconnect only an established connection, as the library does."""
        if self.connected:
            await self.disconnect()

    async def wait(self) -> None:
        """Block for as long as a handshake nothing can abort is in flight."""
        if self.handshaking:
            self.stalled = True
            await asyncio.Event().wait()


class TestRoundHumidity:
    """Test cases for round_humidity function."""

    # Basic rounding tests with step=5
    def test_round_up_to_nearest_5(self):
        """Test rounding up to nearest 5."""
        # humidity=14, current=10, step=5 -> rounds to 15
        assert round_humidity(14, 10, 5) == 15

    def test_round_down_to_nearest_5(self):
        """Test rounding down to nearest 5."""
        # humidity=12, current=15, step=5 -> rounds to 10
        assert round_humidity(12, 15, 5) == 10

    def test_already_multiple_of_5(self):
        """Test when humidity is already a multiple of 5."""
        # humidity=20, current=10, step=5 -> stays 20
        assert round_humidity(20, 10, 5) == 20

    # Tests for humidity > current_humidity
    def test_humidity_greater_than_current_exact_match(self):
        """Test when humidity > current and rounded value equals current."""
        # humidity=22, current=20, step=5 -> rounded to 20 == current, add step -> 25
        assert round_humidity(22, 20, 5) == 25

    def test_humidity_greater_than_current_normal(self):
        """Test when humidity > current and rounded value differs from current."""
        # humidity=14, current=10, step=5 -> rounded to 15 != 10, return 15
        assert round_humidity(14, 10, 5) == 15

    def test_humidity_greater_than_current_add_step(self):
        """Test when increasing humidity and need to add step."""
        # humidity=16, current=10, step=5 -> rounded to 15 != 10, return 15
        assert round_humidity(16, 10, 5) == 15

    # Tests for humidity < current_humidity
    def test_humidity_less_than_current_exact_match(self):
        """Test when humidity < current and rounded value equals current."""
        # humidity=18, current=20, step=5 -> rounded to 20 == current, subtract step -> 15
        assert round_humidity(18, 20, 5) == 15

    def test_humidity_less_than_current_normal(self):
        """Test when humidity < current and rounded value differs from current."""
        # humidity=32, current=40, step=5 -> rounded to 30 != 40, return 30
        assert round_humidity(32, 40, 5) == 30

    def test_humidity_less_than_current_subtract_step(self):
        """Test when decreasing humidity and need to subtract step."""
        # humidity=34, current=40, step=5 -> rounded to 35 != 40, return 35
        assert round_humidity(34, 40, 5) == 35

    # Tests for humidity == current_humidity
    def test_humidity_equal_to_current(self):
        """Test when humidity equals current - no change should occur."""
        assert round_humidity(20, 20, 5) == 20

    def test_humidity_equal_to_current_different_step(self):
        """Test when humidity equals current with different step."""
        assert round_humidity(30, 30, 10) == 30

    # Edge cases with different step values
    def test_step_10(self):
        """Test with step=10."""
        # humidity=22, current=10, step=10 -> rounded to 20 != 10, return 20
        assert round_humidity(22, 10, 10) == 20

    def test_step_10_exact_match(self):
        """Test with step=10 where rounded equals current."""
        # humidity=15, current=20, step=10 -> rounded to 20 == current, subtract -> 10
        assert round_humidity(15, 20, 10) == 10

    def test_step_1(self):
        """Test with step=1 (minimum practical step)."""
        # humidity=22, current=20, step=1 -> rounded to 22 != 20, return 22
        assert round_humidity(22, 20, 1) == 22

    # Edge cases at boundaries
    def test_edge_case_minimum_humidity(self):
        """Test at minimum humidity levels."""
        # humidity=7, current=5, step=5 -> rounded to 5 == current, add step -> 10
        assert round_humidity(7, 5, 5) == 10

    def test_edge_case_maximum_humidity(self):
        """Test at maximum humidity levels."""
        # humidity=48, current=50, step=5 -> rounded to 50 == current, subtract -> 45
        assert round_humidity(48, 50, 5) == 45

    def test_edge_case_zero_humidity(self):
        """Test with humidity at 0."""
        # humidity=0, current=10, step=5 -> rounded to 0 != 10, return 0
        assert round_humidity(0, 10, 5) == 0

    def test_edge_case_100_humidity(self):
        """Test with humidity at 100."""
        # humidity=100, current=50, step=5 -> rounded to 100 != 50, return 100
        assert round_humidity(100, 50, 5) == 100

    # Docstring examples
    def test_from_docstring_example_1(self):
        """Test example from docstring: 12 -> 10."""
        assert round_humidity(12, 15, 5) == 10

    def test_from_docstring_example_2(self):
        """Test example from docstring: 14 -> 15."""
        assert round_humidity(14, 10, 5) == 15

    def test_from_docstring_example_3(self):
        """Test example from docstring: 17 -> 15."""
        assert round_humidity(17, 20, 5) == 15

    def test_from_docstring_example_4(self):
        """Test example from docstring: 20 -> 20."""
        assert round_humidity(20, 10, 5) == 20

    # Rounding edge cases (banker's rounding)
    def test_midpoint_rounding_up(self):
        """Test midpoint value that rounds up."""
        # humidity=13, current=10, step=5 -> 13/5 = 2.6 -> rounds to 3 -> 15
        assert round_humidity(13, 10, 5) == 15

    def test_midpoint_rounding_down(self):
        """Test midpoint value that rounds down."""
        # humidity=12, current=10, step=5 -> 12/5 = 2.4 -> rounds to 2 -> 10
        assert round_humidity(12, 10, 5) == 15

    # Large jumps
    def test_large_jump_up(self):
        """Test large jump up from current humidity."""
        # humidity=35, current=10, step=5 -> rounded to 35 != 10, return 35
        assert round_humidity(35, 10, 5) == 35

    def test_large_jump_down(self):
        """Test large jump down from current humidity."""
        # humidity=15, current=50, step=5 -> rounded to 15 != 50, return 15
        assert round_humidity(15, 50, 5) == 15

    # Various step sizes
    def test_step_2(self):
        """Test with step=2."""
        # humidity=23, current=20, step=2 -> rounded to 24 != 20, return 24
        assert round_humidity(23, 20, 2) == 24

    def test_step_3(self):
        """Test with step=3."""
        # humidity=25, current=20, step=3 -> rounded to 24 != 20, return 24
        assert round_humidity(25, 20, 3) == 24

    # More complex scenarios
    def test_complex_scenario_going_up(self):
        """Test complex scenario with step=5 going up."""
        # humidity=11, current=5, step=5 -> 11/5 = 2.2 -> rounds to 2 -> 10
        # 10 == current+5, so return 10
        assert round_humidity(11, 5, 5) == 10

    def test_small_values(self):
        """Test with small humidity values."""
        assert round_humidity(2, 5, 1) == 2

    def test_repeated_same_value(self):
        """Test setting same humidity multiple times."""
        assert round_humidity(25, 25, 5) == 25
        assert round_humidity(25, 25, 5) == 25


class TestSetTemperature:
    """Test async_set_temperature method."""

    @pytest.mark.parametrize(
        ("value", "mode", "expected_error"),
        [
            (45, OperatingMode.HEAT, None),
            (50, OperatingMode.HEAT, None),
            (45, OperatingMode.HEAT, "Failed to set temperature"),
            (78, OperatingMode.COOL, None),
            (75, OperatingMode.COOL, None),
            (78, OperatingMode.COOL, "Failed to set temperature"),
        ],
    )
    async def test_set_temperature(
        self,
        mock_device,
        mock_coordinator,
        value: float,
        mode: OperatingMode,
        expected_error: str | None,
    ) -> None:
        """Test set_temperature method."""

        if expected_error:
            mock_response = None
            expected_response_data = None
        else:
            mock_response = {
                "current_temp": mock_device.state.display_temp,
                "mode": mode.value,  # mock_device.state.operating_mode.value
                "target_temp": value,
            }
            expected_response_data = SetTemperatureEventSuccess(**mock_response)

        expected_request = asdict(
            SetTemperatureEvent(
                mock_device.identifier,
                mock_device.state.display_scale,
                mode.value,
                value,
            )
        )

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(
                expected_error, mock_response
            )

            response = await mock_coordinator.client.async_set_temperature(
                mock_device, mode, value
            )

            mock_async_invoke_setter.assert_called_once_with(
                "set_temperature", expected_request
            )

            assert response.error is expected_error

            if not expected_error:
                assert response.data == expected_response_data

                if mode == OperatingMode.HEAT:
                    assert (
                        mock_device.state.current_heat_temp
                        == expected_response_data.target_temp
                    )
                if mode == OperatingMode.COOL:
                    assert (
                        mock_device.state.current_cool_temp
                        == expected_response_data.target_temp
                    )
            else:
                assert response.data is None

    @pytest.mark.parametrize(
        "ack",
        [{}, None, "accepted"],
        ids=["empty_dict", "no_payload", "accepted_string"],
    )
    async def test_an_ack_without_detail_still_applies_the_setpoint(
        self, mock_device, mock_coordinator, ack
    ) -> None:
        """An ack that carries no numbers is still an ack.

        A setter the thermostat never answered is already an error above -
        _async_invoke_setter's timeout returns "Future not done" - so anything
        reaching the unpack means the change was accepted. Unpacking these
        straight into the dataclass raised TypeError, which is not a
        HomeAssistantError: the climate.set_temperature service surfaced a raw
        traceback, and the setpoint was left unapplied even though the
        thermostat had taken it.
        """
        previous_display_temp = mock_device.state.display_temp

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, ack)

            response = await mock_coordinator.client.async_set_temperature(
                mock_device, OperatingMode.HEAT, 68
            )

        assert response.error is None
        # The requested value is the only figure available, and it is the one
        # the thermostat agreed to.
        assert mock_device.state.current_heat_temp == 68
        # display_temp is what the thermostat currently reads, not what was
        # asked for; with no reported value it must be left for the next state
        # event rather than overwritten with the setpoint.
        assert mock_device.state.display_temp == previous_display_temp

    async def test_a_string_ack_that_is_not_accepted_is_an_error(
        self, mock_device, mock_coordinator
    ) -> None:
        """Only "accepted" means accepted, as in async_set_operating_mode."""
        previous_heat_temp = mock_device.state.current_heat_temp

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, "rejected")

            response = await mock_coordinator.client.async_set_temperature(
                mock_device, OperatingMode.HEAT, 68
            )

        assert response.error == "rejected"
        assert mock_device.state.current_heat_temp == previous_heat_temp

    @pytest.mark.parametrize(
        "ack",
        [{}, "accepted", {"current_temp": 70, "mode": "heat", "target_temp": 71}],
        ids=["empty_dict", "accepted_string", "three_key_dict"],
    )
    async def test_an_aux_setpoint_is_recorded_as_the_heat_setpoint(
        self, mock_device, mock_coordinator, ack
    ) -> None:
        """A setpoint accepted while in AUX lands on current_heat_temp.

        AUX is forced heating. The climate entity passes AUX through to the
        wire unchanged, and that used to match neither branch of
        `_apply_target_temperature`, so an accepted ack updated nothing.
        """
        mock_device.state.operating_mode = OperatingMode.AUX
        previous_cool_temp = mock_device.state.current_cool_temp

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, ack)

            response = await mock_coordinator.client.async_set_temperature(
                mock_device, OperatingMode.AUX, 71
            )

        assert response.error is None
        assert mock_device.state.current_heat_temp == 71
        assert mock_device.state.current_cool_temp == previous_cool_temp
        assert mock_device.state.operating_mode == OperatingMode.AUX

    @pytest.mark.parametrize(
        "ack",
        [
            {"current_temp": 70, "mode": "heat"},
            {"current_temp": 70, "mode": "heat", "target_temp": 75, "extra": 1},
            {"unexpected": "shape"},
        ],
        ids=["missing_key", "extra_key", "wrong_keys"],
    )
    async def test_an_unreadable_dict_ack_is_an_error_not_a_traceback(
        self, mock_device, mock_coordinator, ack
    ) -> None:
        """A dict we cannot parse degrades to an ActionResponse error.

        raise_if_error then turns it into the integration's "Unable to set
        temperature to ..." HomeAssistantError, which the entity layer knows
        how to present, rather than a TypeError escaping the service call.
        """
        previous_heat_temp = mock_device.state.current_heat_temp

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, ack)

            response = await mock_coordinator.client.async_set_temperature(
                mock_device, OperatingMode.HEAT, 68
            )

        assert response.error is not None
        assert "Failed to parse" in response.error
        # Nothing was applied, because nothing was understood.
        assert mock_device.state.current_heat_temp == previous_heat_temp

    @pytest.mark.parametrize(
        ("ack_args", "expected_error"),
        [
            (({"error": {}},), "Unknown error"),
            (({"error": {"code": 403}},), "Unknown error"),
            (("Forbidden", None), "Forbidden"),
            (({"error": "Forbidden"},), "Forbidden"),
            (([{"error": "Forbidden"}], None), "Unknown error"),
        ],
        ids=[
            "empty_error_object",
            "error_object_without_description",
            "string_error_two_arg_ack",
            "string_error_object",
            "list_error_two_arg_ack",
        ],
    )
    async def test_an_unreadable_error_ack_is_an_error_not_a_write(
        self, mock_device, mock_coordinator, ack_args, expected_error
    ) -> None:
        """An error ack we cannot read a message out of is still a refusal.

        The ack parser used to read exactly one error shape,
        `{"error": {"description": ...}}`. An error object with no
        description came back as an empty-string error, which every setter
        reads as "accepted" and writes the requested value into the local
        state; a string error raised AttributeError out of the service call
        instead of the "Unable to set ..." HomeAssistantError (#216).

        This drives the real `_async_emit_setter` callback with `_send_event`
        faked, so the ack shapes reach the parser as the socket would deliver
        them. The recovery steps are stubbed because a `Forbidden` refusal is
        retried once on a fresh socket.
        """
        client = mock_coordinator.client
        previous_heat_temp = mock_device.state.current_heat_temp

        async def fake_send_event(name, data, callback=None, future=None):
            callback(*ack_args)

        with (
            patch.object(client, "_send_event", new=fake_send_event),
            patch.object(client, "try_refresh_access_token"),
            patch.object(client, "_async_disconnect"),
            patch.object(client, "_connect"),
        ):
            response = await client.async_set_temperature(
                mock_device, OperatingMode.HEAT, previous_heat_temp + 4
            )

        assert response.error == expected_error
        assert response.data is None
        # The refusal reached the caller, so nothing was applied.
        assert mock_device.state.current_heat_temp == previous_heat_temp

    async def test_a_cool_setpoint_is_applied_on_a_detail_free_ack(
        self, mock_device, mock_coordinator
    ) -> None:
        """The mode still decides which setpoint moves."""
        previous_heat_temp = mock_device.state.current_heat_temp

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, {})

            await mock_coordinator.client.async_set_temperature(
                mock_device, OperatingMode.COOL, 79
            )

        assert mock_device.state.current_cool_temp == 79
        assert mock_device.state.current_heat_temp == previous_heat_temp

    @pytest.mark.parametrize(
        "ack",
        [{}, "accepted", {"current_temp": 70, "mode": "heat", "target_temp": 72}],
        ids=["empty_dict", "accepted_string", "detailed"],
    )
    async def test_setpoint_survives_a_state_refresh_during_the_setter(
        self, mock_device, mock_coordinator, mock_json, ack
    ) -> None:
        """The accepted setpoint lands on the State the device holds now.

        update_state replaces device.state with a new State on every state
        event, and one arrives during the setter's await whenever its
        Forbidden recovery reconnects (every connect delivers a state event)
        or the coordinator's refresh runs. Writing the accepted value into
        the State captured before the await put it on an object nothing
        referenced any more: the entity kept showing the old setpoint until
        a later state event happened to carry the new one.
        """
        before = mock_device.state.current_heat_temp
        stale_state = mock_device.state

        async def invoke_with_state_refresh(event, request_data):
            # What a reconnect's state event does to the device mid-setter.
            mock_device.state = State(mock_json["state"])
            return ActionResponse(None, ack)

        with patch.object(
            mock_coordinator.client,
            "_async_invoke_setter",
            new=invoke_with_state_refresh,
        ):
            response = await mock_coordinator.client.async_set_temperature(
                mock_device, OperatingMode.HEAT, before + 4
            )

        assert response.error is None
        assert mock_device.state is not stale_state
        assert mock_device.state.current_heat_temp == before + 4
        if isinstance(ack, dict) and ack:
            assert mock_device.state.display_temp == ack["current_temp"]

    async def test_set_temperature_OFF_state(
        self, mock_device, mock_coordinator
    ) -> None:
        """Test set_temperature method when thermostat is OFF."""

        # Fake device to be in OFF state
        mock_device.state.operating_mode = OperatingMode.OFF

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            response = await mock_coordinator.client.async_set_temperature(
                mock_device, OperatingMode.HEAT, 45
            )

            mock_async_invoke_setter.assert_not_called()
            assert response.error
            assert response.data is None


class TestHumidification:
    """Test async_set_humidification method."""

    @pytest.mark.parametrize(
        ("enabled", "humidity", "expected_error"),
        [
            (True, 45, None),
            (False, 50, None),
            (True, 45, "Failed to set humidification"),
        ],
    )
    async def test_async_set_humidification(
        self,
        mock_device_with_humidification,
        mock_coordinator,
        enabled,
        humidity,
        expected_error,
    ) -> None:
        """Test of set_humidification method."""

        mock_response = None if expected_error else {}
        expected_request = asdict(
            SetHumidityEvent(
                mock_device_with_humidification.identifier,
                SetHumidityEventValue(enabled, humidity),
            )
        )

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(
                expected_error, mock_response
            )

            response = await mock_coordinator.client.async_set_humidification(
                mock_device_with_humidification, enabled, humidity
            )

            mock_async_invoke_setter.assert_called_once_with(
                "set_humidification", expected_request
            )

            assert response.error is expected_error

            if not expected_error:
                assert response.data == mock_response
            else:
                assert response.data is None

    @pytest.mark.parametrize(
        ("enabled"),
        [(True), (False)],
    )
    async def test_async_enable_humidification(
        self, mock_device_with_humidification, mock_coordinator, enabled
    ) -> None:
        """Test of enable_humidification method."""

        with patch.object(
            mock_coordinator.client, "async_set_humidification"
        ) as mock__set_humidification:
            await mock_coordinator.client.async_enable_humidification(
                mock_device_with_humidification, enabled
            )

            mock__set_humidification.assert_called_once_with(
                mock_device_with_humidification,
                enabled,
                mock_device_with_humidification.state.humidity_control.humidification.target_percent,
            )


class TestSetters:
    """Test async_set_circulating_fan_mode and async_set_fan_mode methods."""

    @pytest.mark.parametrize(
        ("enabled", "duty_cycle"),
        [(True, 35), (False, 10)],
    )
    async def test_set_circulating_fan_mode(
        self, mock_device, mock_coordinator, enabled, duty_cycle
    ) -> None:
        """Test async_set_circulating_fan_mode."""

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, "")

            expected_request = asdict(
                SetCirculatingFanEvent(
                    mock_device.identifier,
                    SetCirculatingFanEventValue(enabled, duty_cycle),
                )
            )

            await mock_coordinator.client.async_set_circulating_fan_mode(
                mock_device, enabled, duty_cycle
            )

            mock_async_invoke_setter.assert_called_once_with(
                "set_circulating_fan", expected_request
            )

            assert mock_device.state.circulating_fan.enabled == enabled
            assert mock_device.state.circulating_fan.duty_cycle == duty_cycle

    @pytest.mark.parametrize(
        ("duty_cycle", "expected"),
        [
            (33, 35),  # rounded to the nearest step of 5
            (32, 30),
            (3, 10),  # below min_duty_cycle: the backend rejects it
            (120, 100),  # above max_duty_cycle
            (0, 10),  # 0 is what State reports when nothing was ever set
        ],
    )
    async def test_set_circulating_fan_mode_clamps_to_capabilities(
        self, mock_device, mock_coordinator, duty_cycle, expected
    ) -> None:
        """The duty cycle on the wire fits the thermostat's min, max and step."""

        # sample.json: max_duty_cycle 100, min_duty_cycle 10, step 5
        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, "")

            await mock_coordinator.client.async_set_circulating_fan_mode(
                mock_device, True, duty_cycle
            )

            request = mock_async_invoke_setter.call_args.args[1]
            assert request["value"]["duty_cycle"] == expected
            assert mock_device.state.circulating_fan.duty_cycle == expected

    async def test_set_circulating_fan_mode_with_step_0_only_clamps(
        self, mock_device, mock_coordinator
    ) -> None:
        """A thermostat reporting step 0 must not divide by it."""

        mock_device.capabilities.circulating_fan.step = 0

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, "")

            await mock_coordinator.client.async_set_circulating_fan_mode(
                mock_device, True, 33
            )

            request = mock_async_invoke_setter.call_args.args[1]
            assert request["value"]["duty_cycle"] == 33

    async def test_set_fan_mode(self, mock_device, mock_coordinator) -> None:
        """Test async_set_fan_mode."""

        mode = FanMode.ON.value

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, "")

            expected_request = asdict(SetFanModeEvent(mock_device.identifier, mode))
            mock_device.state.fan_mode = (
                None  # Reset fan mode to ensure it gets updated
            )

            response = await mock_coordinator.client.async_set_fan_mode(
                mock_device, mode
            )

            mock_async_invoke_setter.assert_called_once_with(
                "set_fan_mode", expected_request
            )

            assert response.error is None
            assert mock_device.state.fan_mode == FanMode.ON

    async def test_set_fan_mode_unrecognised(
        self, mock_device, mock_coordinator
    ) -> None:
        """A mode the enum does not know falls back to UNKNOWN, not None.

        A None here would make SensiThermostat.fan_mode raise on `.value`;
        State parsing already applies the same fallback.
        """

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, "")

            response = await mock_coordinator.client.async_set_fan_mode(
                mock_device, "not_a_fan_mode"
            )

            assert response.error is None
            assert mock_device.state.fan_mode == FanMode.UNKNOWN

    @pytest.mark.parametrize(
        ("response_error", "response_data", "expect_error"),
        [
            (None, None, True),
            ("Failed", None, True),
            (None, "not_accepted", True),
            (None, {"invalid_property": "cool"}, True),
            (None, "accepted", False),
            (None, {"mode": "heat"}, False),
        ],
    )
    async def test_async_set_operating_mode(
        self, mock_device, mock_coordinator, response_error, response_data, expect_error
    ) -> None:
        """Test async_set_operating_mode."""

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(
                response_error, response_data
            )

            mode = OperatingMode.HEAT
            expected_request = asdict(
                SetOperatingModeEvent(mock_device.identifier, mode.value)
            )
            mock_device.state.operating_mode = None  # Reset to ensure it gets updated

            response = await mock_coordinator.client.async_set_operating_mode(
                mock_device, mode
            )

            mock_async_invoke_setter.assert_called_once_with(
                "set_operating_mode", expected_request
            )
            if expect_error:
                assert response.error is not None
            else:
                assert response.error is None
                assert mock_device.state.operating_mode == OperatingMode.HEAT

    @pytest.mark.parametrize(
        ("value", "should_succeed"),
        [(0, True), (5, True), (-5, True)],
    )
    async def test_async_set_temperature_offset_range(
        self, mock_device, mock_coordinator, value, should_succeed
    ) -> None:
        """Test async_set_temperature_offset value range validation."""

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, {})

            response = await mock_coordinator.client.async_set_temperature_offset(
                mock_device, value
            )

            if should_succeed:
                mock_async_invoke_setter.assert_called_once()
                assert response.error is None
                assert mock_device.state.temp_offset == value
            else:
                mock_async_invoke_setter.assert_not_called()
                assert response.error is not None
                assert "must be between" in response.error

    @pytest.mark.parametrize(
        ("value", "should_succeed"),
        [(0, True), (25, True), (-25, True)],
    )
    async def test_async_set_humidity_offset_range(
        self, mock_device, mock_coordinator, value, should_succeed
    ) -> None:
        """Test async_set_humidity_offset value range validation."""

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse(None, {})

            response = await mock_coordinator.client.async_set_humidity_offset(
                mock_device, value
            )

            if should_succeed:
                mock_async_invoke_setter.assert_called_once()
                assert response.error is None
                assert mock_device.state.humidity_offset == value
            else:
                mock_async_invoke_setter.assert_not_called()
                assert response.error is not None
                assert "must be between" in response.error


class TestWaitForDevices:
    """Test wait_for_devices method."""

    async def test_wait_for_devices_success(self, mock_coordinator, mock_device):
        """wait_for_devices completes when events resolve."""

        client = mock_coordinator.client
        client._devices = {mock_device.identifier: mock_device}  # noqa: SLF001

        async def _nop(*a, **k):
            return None

        async def _resolved_future(event, icd_id):
            fut = client._hass.loop.create_future()  # noqa: SLF001
            fut.set_result({})
            return fut

        with (
            patch.object(client, "_connect", new=_nop),
            patch.object(client, "_wait_for_event", new=_nop),
            patch.object(client, "_create_event_future", new=_resolved_future),
        ):
            await client.wait_for_devices()

    async def test_wait_for_devices_connect_error(self, mock_coordinator):
        """wait_for_devices raises ConfigEntryNotReady when connect fails."""

        client = mock_coordinator.client

        async def _raise(*a, **k):
            raise SensiConnectionError("boom")

        with (
            patch.object(client, "_connect", new=_raise),
            pytest.raises(ConfigEntryNotReady),
        ):
            await client.wait_for_devices()

    async def test_wait_for_devices_timeout_then_retry_fails(
        self, mock_coordinator, mock_device, monkeypatch, caplog
    ):
        """If device info times out, the retry path raises ConfigEntryNotReady."""

        # shorten timeouts so the test completes quickly
        monkeypatch.setattr(
            "custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.01
        )

        client = mock_coordinator.client
        client._devices = {mock_device.identifier: mock_device}  # noqa: SLF001

        async def _nop(*a, **k):
            return None

        async def _pending_future(event, icd_id):
            # Return a future that never completes to simulate timeout
            return client._hass.loop.create_future()  # noqa: SLF001

        with (
            patch.object(client, "_connect", new=_nop),
            patch.object(client, "_wait_for_event", new=_nop),
            patch.object(client, "_create_event_future", new=_pending_future),
            pytest.raises(ConfigEntryNotReady),
        ):
            await client.wait_for_devices()

        assert mock_device.state.fan_mode == FanMode.ON

        # This one is a WARNING, so it reaches a log with no debug logging
        # turned on - the log a user attaches to "my thermostats did not
        # appear". The identifier that addresses the device stays out of it.
        assert "Timed out waiting for info/capabilities" in caplog.text
        assert mock_device.identifier not in caplog.text
        assert redact_identifier(mock_device.identifier) in caplog.text

    async def test_connection_lost_while_asking_for_device_info_is_not_ready(
        self, mock_coordinator, mock_device
    ):
        """Losing the socket on the first getter is ConfigEntryNotReady, not a retry.

        Distinct from the e2e case where the connection goes during the
        retry: here the first attempt is the one that fails, and it must not
        be mistaken for a timeout worth retrying on the same dead socket.
        """
        client = mock_coordinator.client
        client._devices = {mock_device.identifier: mock_device}  # noqa: SLF001

        async def _nop(*a, **k):
            return None

        async def _raise(*a, **k):
            raise SensiConnectionError("socket gone")

        with (
            patch.object(client, "_connect", new=_nop),
            patch.object(client, "_wait_for_event", new=_nop),
            patch.object(client, "_send_event", new=_raise),
            pytest.raises(ConfigEntryNotReady),
        ):
            await client.wait_for_devices()

    async def test_initial_state_timeout_raises_not_ready(
        self, mock_coordinator, monkeypatch, caplog
    ):
        """No initial `state` event is ConfigEntryNotReady, not a loaded entry.

        The socket is up but the backend never lists the thermostats. This
        used to return normally: the timeout was swallowed, the device dict
        was empty, so there was nothing to retry the getters for, and setup
        finished with no devices and nothing raised - which is why Home
        Assistant never retried it either.
        """
        monkeypatch.setattr(
            "custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.01
        )

        client = mock_coordinator.client
        sent: list[str] = []

        async def _nop(*a, **k):
            return None

        async def _record_send(name, data, *a, **k):
            sent.append(name)

        with (
            patch.object(client, "_connect", new=_nop),
            patch.object(client, "_send_event", new=_record_send),
            pytest.raises(ConfigEntryNotReady, match="No state event within"),
        ):
            await client.wait_for_devices()

        # Nothing to ask for info about, so nothing went out on the wire and
        # the "retrying" branch - which is for a known device that stays
        # silent - was not taken.
        assert sent == []
        assert client.get_devices() == []
        assert "retrying" not in caplog.text

    async def test_a_state_event_delivered_inside_connect_is_not_a_timeout(
        self, mock_coordinator, mock_json, monkeypatch, caplog
    ):
        """The initial `state` may arrive before _connect returns; it still counts.

        socketio dispatches events from its read loop while connect() is
        waiting to be woken, so the first `state` can reach _update_state
        before wait_for_devices gets control back. The waiter has to be
        registered before connecting, or a backend that answered at once is
        reported as one that never answered.
        """
        monkeypatch.setattr(
            "custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.01
        )

        client = mock_coordinator.client
        loop = client._hass.loop  # noqa: SLF001

        async def _connect_and_deliver_state():
            client._on_event("state", [mock_json])  # noqa: SLF001

        async def _answer_getters(name, data, *a, **k):
            # The getter futures are created after the send, so answer on the
            # next loop iteration the way the emit loop would.
            event = {"get_info": "info", "get_capabilities": "capabilities"}[name]
            loop.call_soon(client._on_event, event, {"icd_id": data["icd_id"]})  # noqa: SLF001

        with (
            patch.object(client, "_connect", new=_connect_and_deliver_state),
            patch.object(client, "_send_event", new=_answer_getters),
        ):
            await client.wait_for_devices()

        assert [d.identifier for d in client.get_devices()] == [mock_json["icd_id"]]
        assert "Timed out waiting for event 'state'" not in caplog.text

    async def test_a_state_event_with_no_devices_completes_with_a_warning(
        self, mock_coordinator, caplog
    ):
        """A backend that lists no thermostats completes setup and says so.

        That is an answer, not a failure, so there is nothing to retry - but
        the WARNING is what a default-configured log shows, and it is the
        line that explains "my thermostats did not appear".
        """
        client = mock_coordinator.client
        sent: list[str] = []

        async def _nop(*a, **k):
            return None

        async def _record_send(name, data, *a, **k):
            sent.append(name)

        with (
            patch.object(client, "_connect", new=_nop),
            patch.object(client, "_wait_for_event", new=_nop),
            patch.object(client, "_send_event", new=_record_send),
        ):
            await client.wait_for_devices()

        assert sent == []
        assert client.get_devices() == []
        assert [
            record.levelname
            for record in caplog.records
            if "account lists no thermostats" in record.getMessage()
        ] == ["WARNING"]


class TestSetterErrorsLeaveStateAlone:
    """A rejected setter must not update the cached device state.

    Each setter writes the value it just sent into `device.state` guarded by
    `if not response.error`. Nothing exercised the other side of that guard,
    so a setter that wrote unconditionally would still pass the suite - and
    Home Assistant would then show a value the thermostat never accepted until
    the next payload from the backend corrected it.
    """

    async def test_circulating_fan_mode_error_keeps_state(
        self, mock_device, mock_coordinator
    ) -> None:
        """A rejected set_circulating_fan leaves enabled and duty cycle alone."""

        mock_device.state.circulating_fan.enabled = False
        mock_device.state.circulating_fan.duty_cycle = 20

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse("Failed", None)

            response = await mock_coordinator.client.async_set_circulating_fan_mode(
                mock_device, True, 55
            )

        assert response.error == "Failed"
        assert mock_device.state.circulating_fan.enabled is False
        assert mock_device.state.circulating_fan.duty_cycle == 20

    async def test_fan_mode_error_keeps_state(
        self, mock_device, mock_coordinator
    ) -> None:
        """A rejected set_fan_mode leaves the previous mode in place."""

        mock_device.state.fan_mode = FanMode.AUTO

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse("Failed", None)

            response = await mock_coordinator.client.async_set_fan_mode(
                mock_device, FanMode.ON.value
            )

        assert response.error == "Failed"
        assert mock_device.state.fan_mode == FanMode.AUTO

    async def test_temperature_offset_error_keeps_state(
        self, mock_device, mock_coordinator
    ) -> None:
        """A rejected set_temp_offset leaves the previous offset in place."""

        mock_device.state.temp_offset = 2

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse("Failed", None)

            response = await mock_coordinator.client.async_set_temperature_offset(
                mock_device, -4
            )

        assert response.error == "Failed"
        assert mock_device.state.temp_offset == 2

    async def test_humidity_offset_error_keeps_state(
        self, mock_device, mock_coordinator
    ) -> None:
        """A rejected set_humidity_offset leaves the previous offset in place."""

        mock_device.state.humidity_offset = 10

        with patch.object(
            mock_coordinator.client, "_async_invoke_setter"
        ) as mock_async_invoke_setter:
            mock_async_invoke_setter.return_value = ActionResponse("Failed", None)

            response = await mock_coordinator.client.async_set_humidity_offset(
                mock_device, -15
            )

        assert response.error == "Failed"
        assert mock_device.state.humidity_offset == 10


class TestSetterRetryOnForbidden:
    """Test the setter retry for auth-shaped refusals.

    The backend sporadically refuses a validly-authenticated write with
    `Forbidden` - observed once with 1h46m of token validity left, on a socket
    seconds old, with reads healthy either side of it. Before the retry that
    lost a `mode: single` automation's scheduled step, because nothing in the
    client noticed an in-band refusal on an established socket.

    These drive `_async_invoke_setter` directly with `_async_emit_setter`
    stubbed, so the retry decision is what is under test rather than the
    socket plumbing beneath it.
    """

    @staticmethod
    def _patches(client):
        """Patch the recovery steps the retry drives."""
        return (
            patch.object(client, "try_refresh_access_token"),
            patch.object(client, "_async_disconnect"),
            patch.object(client, "_connect"),
        )

    async def test_forbidden_then_accepted_retries_once_and_succeeds(
        self, mock_coordinator, caplog
    ) -> None:
        """A refused write is retried on a fresh token and socket."""

        client = mock_coordinator.client
        refresh, disconnect, connect = self._patches(client)

        with (
            refresh as mock_refresh,
            disconnect as mock_disconnect,
            connect as mock_connect,
            patch.object(client, "_async_emit_setter") as mock_emit,
        ):
            mock_emit.side_effect = [
                ActionResponse("Forbidden", None),
                ActionResponse(None, {"target_temp": 75}),
            ]

            response = await client._async_invoke_setter("set_temperature", {"a": 1})

        # The caller sees the second attempt's success, not the first refusal.
        assert response.error is None
        assert response.data == {"target_temp": 75}

        # Recovery ran, in the order that makes the retry meaningful: a new
        # token first, then a socket that carries it.
        mock_refresh.assert_awaited_once()
        mock_disconnect.assert_awaited_once()
        mock_connect.assert_awaited_once()

        # Re-emitted the same event and payload, exactly twice in total.
        assert mock_emit.await_count == 2
        assert mock_emit.await_args_list[0].args == ("set_temperature", {"a": 1})
        assert mock_emit.await_args_list[1].args == ("set_temperature", {"a": 1})

        # Exactly one WARNING, naming both attempts. A silent success would
        # hide the backend defect this exists to survive.
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "Forbidden" in warnings[0].message
        assert "succeeded" in warnings[0].message

    async def test_forbidden_twice_still_fails(self, mock_coordinator, caplog) -> None:
        """A refusal that survives the retry is reported, as before the retry."""

        client = mock_coordinator.client
        refresh, disconnect, connect = self._patches(client)

        with (
            refresh,
            disconnect,
            connect,
            patch.object(client, "_async_emit_setter") as mock_emit,
        ):
            mock_emit.side_effect = [
                ActionResponse("Forbidden", None),
                ActionResponse("Forbidden", None),
            ]

            response = await client._async_invoke_setter("set_temperature", {"a": 1})

        # climate.py turns this into the HomeAssistantError the user saw
        # before this change; the retry must not swallow it.
        assert response.error == "Forbidden"
        assert mock_emit.await_count == 2

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "was refused with 'Forbidden'" in warnings[0].message

    @pytest.mark.parametrize(
        "error",
        ["Bad Request", "InvalidScale", "OutOfRange", "ThermostatOffline"],
    )
    async def test_genuine_rejections_fail_fast(
        self, mock_coordinator, caplog, error: str
    ) -> None:
        """A rejected value is not retried.

        These arrive with an `icd_id`: the device rejected what was sent, so a
        second identical emit would fail identically and only delay the error.
        """

        client = mock_coordinator.client
        refresh, disconnect, connect = self._patches(client)

        with (
            refresh as mock_refresh,
            disconnect as mock_disconnect,
            connect as mock_connect,
            patch.object(client, "_async_emit_setter") as mock_emit,
        ):
            mock_emit.return_value = ActionResponse(error, None)

            response = await client._async_invoke_setter("set_temperature", {"a": 1})

        assert response.error == error
        mock_emit.assert_awaited_once()
        mock_refresh.assert_not_awaited()
        mock_disconnect.assert_not_awaited()
        mock_connect.assert_not_awaited()
        assert [r for r in caplog.records if r.levelname == "WARNING"] == []

    async def test_revoked_refresh_token_starts_reauth_and_fails_cleanly(
        self, mock_coordinator
    ) -> None:
        """A dead credential starts the reauth flow from the setter path.

        No coordinator wraps an entity service call - its AuthenticationError
        handler only wraps async_update_devices - so nothing downstream can
        translate the failure. The client starts reauth on its config entry
        itself and fails the call with the HomeAssistantError the entity
        layer surfaces cleanly, instead of a bare AuthenticationError that
        would reach the user as an unhandled traceback with the reauth prompt
        deferred until the access token dies of old age.
        """

        client = mock_coordinator.client
        entry = mock_coordinator.config_entry

        with (
            patch.object(
                client,
                "try_refresh_access_token",
                side_effect=AuthenticationError("refresh token revoked"),
            ),
            patch.object(entry, "async_start_reauth") as mock_reauth,
            patch.object(client, "_async_emit_setter") as mock_emit,
            pytest.raises(HomeAssistantError, match="re-authenticated"),
        ):
            mock_emit.return_value = ActionResponse("Forbidden", None)
            await client._async_invoke_setter("set_temperature", {"a": 1})

        # Never re-emitted: there is no point retrying on a dead credential.
        mock_emit.assert_awaited_once()
        mock_reauth.assert_called_once()

    async def test_without_an_entry_a_dead_credential_still_fails_cleanly(
        self, mock_coordinator
    ) -> None:
        """A dead credential without a config entry still fails cleanly.

        A client built without an entry cannot start reauth, but the service
        call must still fail as a HomeAssistantError, not a raw traceback.
        """

        client = mock_coordinator.client
        client._config_entry = None

        with (
            patch.object(
                client,
                "try_refresh_access_token",
                side_effect=AuthenticationError("refresh token revoked"),
            ),
            patch.object(client, "_async_emit_setter") as mock_emit,
            pytest.raises(HomeAssistantError, match="re-authenticated"),
        ):
            mock_emit.return_value = ActionResponse("Forbidden", None)
            await client._async_invoke_setter("set_temperature", {"a": 1})

        mock_emit.assert_awaited_once()

    async def test_concurrent_refusals_share_one_recovery(
        self, mock_coordinator, caplog
    ) -> None:
        """Two refusals in flight rotate the token once, not twice.

        Sensi rotates the refresh token on every exchange, so two recoveries
        presenting the same token would get the second exchange rejected - a
        healthy credential misread as revoked - while their two
        disconnect/connect pairs tear down each other's replacement socket.

        This one runs through the real machinery - _async_emit_setter,
        _send_event, the emit loop, a fake socket answering acks - rather
        than stubbing the emit, so the serialization is exercised where the
        concurrency actually lives. Only the two network edges are stubbed:
        the token exchange and the socket construction inside _connect.
        """

        client = mock_coordinator.client

        sockets: list[MagicMock] = []
        emits_seen = 0

        def make_socket() -> MagicMock:
            sio = MagicMock()
            sio.connected = True
            sio.shutdown = AsyncMock()
            sio.wait = AsyncMock()

            async def emit(_name, _data, _namespace, callback):
                nonlocal emits_seen
                emits_seen += 1
                # Refuse both first attempts; accept every retry.
                if emits_seen <= 2:
                    callback({"error": {"description": "Forbidden"}})
                else:
                    callback("accepted")

            sio.emit = AsyncMock(side_effect=emit)
            sockets.append(sio)
            return sio

        async def fake_connect():
            client._sio = make_socket()

        client._sio = make_socket()
        emit_loop = asyncio.get_running_loop().create_task(client._emit_loop())

        try:
            with (
                patch.object(client, "try_refresh_access_token") as mock_refresh,
                patch.object(
                    client, "_connect", side_effect=fake_connect
                ) as mock_connect,
            ):
                first, second = await asyncio.gather(
                    client._async_invoke_setter("set_temperature", {"a": 1}),
                    client._async_invoke_setter("set_temperature", {"b": 2}),
                )
        finally:
            emit_loop.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await emit_loop

        assert first.error is None
        assert second.error is None

        # One recovery served both refusals: the loser of the lock found the
        # epoch advanced and reused the fresh token and socket instead of
        # rotating the refresh token a second time.
        mock_refresh.assert_awaited_once()
        mock_connect.assert_awaited_once()

        # Exactly one replacement socket, four emits in total, and both
        # retries carried by the replacement - recovery always completes
        # before either caller re-emits.
        assert len(sockets) == 2
        assert emits_seen == 4
        assert sockets[1].emit.await_count >= 2

        # Each caller still reports its own attempt pair.
        refusals = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "was refused with" in r.message
        ]
        assert len(refusals) == 2

    async def test_failed_reconnect_reports_the_original_refusal(
        self, mock_coordinator, caplog
    ) -> None:
        """If recovery fails, report the refusal rather than the plumbing."""

        client = mock_coordinator.client

        with (
            patch.object(client, "try_refresh_access_token"),
            patch.object(client, "_async_disconnect"),
            patch.object(client, "_connect", side_effect=SensiConnectionError("nope")),
            patch.object(client, "_async_emit_setter") as mock_emit,
        ):
            mock_emit.return_value = ActionResponse("Forbidden", None)

            response = await client._async_invoke_setter("set_temperature", {"a": 1})

        # The setter still did not happen, and that is what the message says -
        # not a connection error about machinery the caller never asked for.
        assert response.error == "Forbidden"
        mock_emit.assert_awaited_once()

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "could not reconnect to retry" in warnings[0].message

    async def test_a_refresh_tick_during_recovery_does_not_leak_a_socket(
        self, hass, mock_coordinator, mock_device, mock_json, monkeypatch
    ) -> None:
        """The coordinator's reconnect waits for a recovery, and vice versa.

        Both run disconnect-then-connect on the same client. Before this was
        serialized, a 30-second tick landing while a recovery was suspended in
        its token refresh replaced the socket underneath it: the recovery then
        took a socket the tick was still connecting, shutdown() found nothing
        to tear down - the library sets `connected` only at the end of
        connect() - and the tick's handshake completed into a socket nothing
        referenced. It stayed connected, kept pushing state events into the
        client, and was never shut down until Home Assistant restarted. The
        recovery also sat in wait() on that socket for DISCONNECT_TIMEOUT.

        This runs the real machinery - the emit loop, the fake backend's acks,
        the client's own connect - and stubs only the token exchange, which
        is held open so the tick has a window to land in. The tick's connect
        is gated as well, so the interleaving is forced rather than left to
        scheduling; with the lock in place the gate simply holds whichever
        connect owns the lock at the time.
        """

        # The library-faithful wait() blocks forever on a mid-handshake
        # socket; keep a regression from stalling the test for ten seconds.
        monkeypatch.setattr("custom_components.sensi.client.DISCONNECT_TIMEOUT", 0.05)

        backend = FakeSensiBackend([mock_json])
        gate: asyncio.Event | None = None

        def factory(*_args, **_kwargs) -> GatedSocket:
            socket = GatedSocket(backend, gate)
            backend.sockets.append(socket)
            return socket

        client = mock_coordinator.client
        client._devices = {mock_device.identifier: mock_device}
        client._config = AuthenticationConfig(
            refresh_token="r", user_id="u", access_token="a", expires_at=9e9
        )

        refresh_started = asyncio.Event()
        refresh_release = asyncio.Event()

        async def slow_refresh() -> None:
            refresh_started.set()
            await refresh_release.wait()

        # First attempt refused, retry accepted; everything else is a getter.
        acks = iter([({"error": {"description": "Forbidden"}},), (None,)])
        backend.ack_for = lambda name, _data: (
            next(acks) if name == "set_fan_mode" else (None,)
        )

        with (
            patch("custom_components.sensi.client.socketio.AsyncClient", factory),
            patch.object(client, "try_refresh_access_token", new=slow_refresh),
        ):
            # Socket A, connected the way setup does it.
            await client._connect()

            setter = hass.async_create_task(
                client.async_set_fan_mode(mock_device, "on")
            )
            await asyncio.wait_for(refresh_started.wait(), 3)

            # The tick lands while the recovery is suspended in its refresh.
            gate = asyncio.Event()
            tick = hass.async_create_task(client.async_update_devices())
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # The recovery resumes and runs its own disconnect/connect pair.
            refresh_release.set()
            await asyncio.sleep(0.2)

            # Whichever connect the gate is holding now completes.
            gate.set()
            gate = None

            await asyncio.wait_for(tick, 3)
            await asyncio.wait_for(setter, 3)
            await hass.async_block_till_done()

            live = [s for s in backend.sockets if s.connected]
            installed = client._sio

            await client.stop()
            await backend.shutdown()

        # The retry went through: the caller sees success, not the refusal.
        assert setter.result().error is None

        # Three sockets in all - setup, then one per disconnect/connect pair -
        # and exactly one of them was connected at the end, the one the client
        # holds. Before the lock this was two: the client's, and the tick's
        # orphan.
        assert len(backend.sockets) == 3
        assert len(live) == 1
        assert installed is live[0]

        # Nothing was taken mid-handshake, so no caller sat in wait() on a
        # transport shutdown() could not reach.
        assert not any(s.stalled for s in backend.sockets)

    async def test_a_queued_refresh_tick_waits_for_the_retry(
        self, hass, mock_coordinator, mock_device, mock_json, monkeypatch
    ) -> None:
        """A tick that lands during a recovery does not run until the retry is acked.

        The tick is next in line for _reconnect_lock. If the recovery let go of
        the lock before emitting its retry, the tick's disconnect ran first:
        the retry either sat queued through the tick's reconnect - with
        SET_EVENT_TIMEOUT running the whole time, and the emit loop discarding
        it once it timed out - or went out on a socket the tick was already
        tearing down, so its ack never came. A slow second connection was
        enough to turn a refusal the recovery had fixed into "Future not
        done".

        Same machinery as the socket-leak test above, with the gate on the
        tick's connect only - the third socket - and held longer than
        SET_EVENT_TIMEOUT. The retry has to be acked on the recovery's own
        socket, before that connect is allowed to complete.
        """

        monkeypatch.setattr("custom_components.sensi.client.DISCONNECT_TIMEOUT", 0.05)
        # Long enough for one EMIT_LOOP_DELAY (0.5 s) to pass before the emit
        # loop picks the retry up; short enough that a regression fails fast.
        monkeypatch.setattr("custom_components.sensi.client.SET_EVENT_TIMEOUT", 1)

        backend = FakeSensiBackend([mock_json])
        gate = asyncio.Event()

        def factory(*_args, **_kwargs) -> GatedSocket:
            # Setup's socket and the recovery's connect straight through; the
            # tick's connect waits at the gate.
            socket = GatedSocket(backend, gate if len(backend.sockets) == 2 else None)
            backend.sockets.append(socket)
            return socket

        client = mock_coordinator.client
        client._devices = {mock_device.identifier: mock_device}
        client._config = AuthenticationConfig(
            refresh_token="r", user_id="u", access_token="a", expires_at=9e9
        )

        refresh_started = asyncio.Event()
        refresh_release = asyncio.Event()

        async def slow_refresh() -> None:
            refresh_started.set()
            await refresh_release.wait()

        acks = iter([({"error": {"description": "Forbidden"}},), (None,)])
        backend.ack_for = lambda name, _data: (
            next(acks) if name == "set_fan_mode" else (None,)
        )

        with (
            patch("custom_components.sensi.client.socketio.AsyncClient", factory),
            patch.object(client, "try_refresh_access_token", new=slow_refresh),
        ):
            await client._connect()

            setter = hass.async_create_task(
                client.async_set_fan_mode(mock_device, "on")
            )
            await asyncio.wait_for(refresh_started.wait(), 3)

            tick = hass.async_create_task(client.async_update_devices())
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # The recovery reconnects and retries; the gate is still closed,
            # so the tick cannot finish its own reconnect while this runs.
            refresh_release.set()
            await asyncio.wait_for(setter, 3)
            assert not tick.done()

            gate.set()
            await asyncio.wait_for(tick, 3)
            await hass.async_block_till_done()

            await client.stop()
            await backend.shutdown()

        # The retry went out on the recovery's socket and was acked there.
        assert setter.result().error is None
        assert len(backend.sockets) == 3
        assert backend.sockets[1].emitted == ["set_fan_mode"]
        assert backend.sockets[2].emitted == []
