"""Tests for the parts of SensiClient that need no socket.io connection.

`tests/e2e/` covers the client the way Home Assistant drives it - connect,
handshake, setters, refresh. What it cannot reach are the branches that only
happen when something goes wrong at an awkward moment: an ack that arrives after
its future was cancelled, an emit loop that finds the socket gone mid-drain, a
token that expires between two connection attempts.

Those are pure in-process logic, so they are covered here directly rather than
by contorting the fake backend into producing them.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sensi.auth import SensiConnectionError
from custom_components.sensi.client import (
    EventInfo,
    SensiClient,
    extract_icd_id,
    get_error_description_from_event_callback,
    is_token_expired,
)
from custom_components.sensi.data import AuthenticationConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

ICD_ID = "36-6f-92-ff-fe-0c-0b-07"


@pytest.fixture
def client(hass: HomeAssistant) -> SensiClient:
    """Return a client with credentials that are valid and not near expiry."""
    config = AuthenticationConfig(
        refresh_token="refresh",
        access_token="access",
        expires_at=99_999_999_999,
        user_id="user",
    )
    return SensiClient(hass, config)


class TestContextManager:
    """`async with SensiClient(...)` disconnects on the way out."""

    async def test_aenter_returns_the_client(self, client: SensiClient) -> None:
        """Entering yields the same client instance."""
        async with client as entered:
            assert entered is client

    async def test_aexit_disconnects_and_does_not_swallow(
        self, client: SensiClient
    ) -> None:
        """Leaving disconnects, and returns False so exceptions propagate."""
        with patch.object(client, "_async_disconnect", AsyncMock()) as mock_disconnect:
            assert await client.__aexit__(None, None, None) is False
            mock_disconnect.assert_awaited_once()

    async def test_aexit_lets_an_exception_through(self, client: SensiClient) -> None:
        """A failure inside the block is not suppressed by the context manager."""
        with (
            patch.object(client, "_async_disconnect", AsyncMock()),
            pytest.raises(ValueError, match="boom"),
        ):
            async with client:
                raise ValueError("boom")


class TestStateDispatch:
    """`_update_state` turns raw `state` payloads into devices."""

    def test_empty_payload_is_ignored(self, client: SensiClient) -> None:
        """An empty state event creates nothing and resolves nothing."""
        future = client._hass.loop.create_future()
        client._futures[("state", None)] = [future]

        client._update_state([])

        assert client.get_devices() == []
        assert not future.done()
        future.cancel()

    def test_none_payload_is_ignored(self, client: SensiClient) -> None:
        """A null state event is treated the same as an empty one."""
        client._update_state(None)
        assert client.get_devices() == []

    def test_entry_without_an_icd_id_is_skipped(
        self, client: SensiClient, mock_json
    ) -> None:
        """A device document with no identifier cannot be tracked."""
        anonymous = {k: v for k, v in mock_json.items() if k != "icd_id"}

        client._update_state([anonymous])

        assert client.get_devices() == []

    def test_registration_only_event_resolves_the_initial_future_only(
        self, client: SensiClient, mock_json
    ) -> None:
        """The first state event on connect usually carries no state.

        It must still resolve the initial `("state", None)` waiter - that is
        what `wait_for_devices` is blocked on - while leaving the per-device
        waiters pending until real state arrives.
        """
        stateless = {k: v for k, v in mock_json.items() if k != "state"}

        initial = client._hass.loop.create_future()
        per_device = client._hass.loop.create_future()
        client._futures[("state", None)] = [initial]
        client._futures[("state", ICD_ID)] = [per_device]

        client._update_state([stateless])

        assert initial.done()
        assert not per_device.done()
        assert len(client.get_devices()) == 1

        per_device.cancel()

    def test_second_event_updates_the_existing_device(
        self, client: SensiClient, mock_json
    ) -> None:
        """A later state event updates in place rather than creating a duplicate."""
        client._update_state([mock_json])
        assert len(client.get_devices()) == 1

        warmer = {**mock_json, "state": {**mock_json["state"], "display_temp": 71}}
        client._update_state([warmer])

        assert len(client.get_devices()) == 1
        assert client.get_devices()[0].state.display_temp == 71

    def test_info_and_capabilities_for_an_unknown_device_still_resolve(
        self, client: SensiClient
    ) -> None:
        """A waiter is released even when the device is not tracked.

        Otherwise `wait_for_devices` would hang for its full timeout on a
        device that vanished between the state event and the getter response.
        """
        info_future = client._hass.loop.create_future()
        capabilities_future = client._hass.loop.create_future()
        client._futures[("info", ICD_ID)] = [info_future]
        client._futures[("capabilities", ICD_ID)] = [capabilities_future]

        client._update_info({"icd_id": ICD_ID, "serial_number": "SN"})
        client._update_capabilities({"icd_id": ICD_ID, "degrees_fc": "yes"})

        assert info_future.done()
        assert capabilities_future.done()


class TestEventFutures:
    """The `(event, icd_id)` future registry."""

    async def test_futures_are_keyed_separately(self, client: SensiClient) -> None:
        """Two devices waiting on the same event do not resolve each other."""
        first = await client._create_event_future("state", "a")
        second = await client._create_event_future("state", "b")

        client._resolve_futures("state", "a", {"icd_id": "a"})

        assert first.done()
        assert not second.done()
        second.cancel()

    async def test_every_waiter_on_a_key_is_resolved_and_the_key_cleared(
        self, client: SensiClient
    ) -> None:
        """Resolving a key releases all of its waiters exactly once."""
        waiters = [await client._create_event_future("info", ICD_ID) for _ in range(3)]

        client._resolve_futures("info", ICD_ID, {"ok": True})

        assert all(future.done() for future in waiters)
        assert ("info", ICD_ID) not in client._futures

        # A second resolution must not raise on the now-missing key.
        client._resolve_futures("info", ICD_ID, {"ok": True})

    async def test_resolving_an_already_resolved_future_is_ignored(
        self, client: SensiClient
    ) -> None:
        """An ack racing a timeout must not raise InvalidStateError."""
        future = await client._create_event_future("info", ICD_ID)
        future.set_result("first")

        client._resolve_futures("info", ICD_ID, "second")

        assert future.result() == "first"

    async def test_wait_for_event_times_out_without_raising(
        self, client: SensiClient
    ) -> None:
        """A timed-out wait logs and returns None rather than propagating."""
        assert await client._wait_for_event("state", ICD_ID, timeout=0.01) is None

    async def test_on_event_routes_unknown_events_to_the_registry(
        self, client: SensiClient
    ) -> None:
        """Anything that is not state/info/capabilities resolves by name."""
        future = await client._create_event_future("connected", None)

        client._on_event("connected", None)

        assert future.done()


class TestSetterAcks:
    """`_async_invoke_setter` and the ack callback it installs."""

    async def _invoke(self, client: SensiClient, ack: tuple | None):
        """Run a setter, replying with `ack` (or not replying at all)."""

        async def fake_send(name, data, callback=None):
            if ack is not None and callback is not None:
                callback(*ack)

        with patch.object(client, "_send_event", fake_send):
            return await client._async_invoke_setter("set_thing", {"icd_id": ICD_ID})

    async def test_empty_ack_is_a_success(self, client: SensiClient) -> None:
        """A callback invoked with no arguments means "accepted"."""
        response = await self._invoke(client, ())
        assert response.error is None
        assert response.data == {}

    async def test_none_payload_ack_is_a_success(self, client: SensiClient) -> None:
        """An explicit null payload is also a success, not a failure."""
        response = await self._invoke(client, (None,))
        assert response.error is None
        assert response.data == {}

    async def test_error_payload_becomes_the_error_description(
        self, client: SensiClient
    ) -> None:
        """The thermostat's error code is what the caller sees."""
        response = await self._invoke(
            client, ({"error": {"description": "ThermostatOffline"}},)
        )
        assert response.error == "ThermostatOffline"
        assert response.data is None

    async def test_two_argument_ack_is_split_into_error_and_data(
        self, client: SensiClient
    ) -> None:
        """socket.io acks arrive as (error, data) when both are present."""
        response = await self._invoke(client, (None, {"mode": "heat"}))
        assert response.error is None
        assert response.data == {"mode": "heat"}

    async def test_no_ack_at_all_is_reported_as_a_failure(
        self, client: SensiClient
    ) -> None:
        """A thermostat that never answers must not leave the caller hanging."""
        with patch("custom_components.sensi.client.SET_EVENT_TIMEOUT", 0.01):
            response = await self._invoke(client, None)

        assert response.error == "Future not done"
        assert response.data is None

    async def test_ack_arriving_after_cancellation_is_dropped(
        self, client: SensiClient
    ) -> None:
        """A late ack must not touch a future that was already cancelled."""
        captured: list = []

        async def capture(name, data, callback=None):
            captured.append(callback)

        with (
            patch.object(client, "_send_event", capture),
            patch("custom_components.sensi.client.SET_EVENT_TIMEOUT", 0.01),
        ):
            response = await client._async_invoke_setter("set_thing", {})

        assert response.error == "Future not done"

        # The late reply lands after the setter has given up. It must be a
        # no-op rather than an InvalidStateError in a socket.io callback.
        captured[0]({"late": True})


class TestEmitLoop:
    """The background queue drain."""

    async def _run_once(self, client: SensiClient) -> None:
        """Run the emit loop just long enough for one pass."""
        task = asyncio.get_running_loop().create_task(client._emit_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_queued_events_are_emitted_while_connected(
        self, client: SensiClient
    ) -> None:
        """The happy path: drain the queue onto the socket."""
        sio = MagicMock()
        sio.connected = True
        sio.emit = AsyncMock()
        client._sio = sio

        await client._send_event("get_info", {"icd_id": ICD_ID})
        await self._run_once(client)

        sio.emit.assert_awaited_once_with("get_info", {"icd_id": ICD_ID}, None, None)

    async def test_a_disconnect_mid_drain_requeues_the_event(
        self, client: SensiClient
    ) -> None:
        """Work is put back rather than dropped when the socket goes away.

        The loop checks `connected` once for itself and again for each item it
        pulls off the queue. This socket answers True to the first check and
        False to the second, which is exactly the race the requeue branch
        exists for - the connection drops between deciding to drain and
        emitting.
        """
        checks = 0

        class FlakySocket:
            emit = AsyncMock()

            @property
            def connected(self) -> bool:
                nonlocal checks
                checks += 1
                return checks == 1

        client._sio = FlakySocket()
        client._event_queue.put_nowait(EventInfo("get_info", {}, None))

        await self._run_once(client)

        assert checks >= 2, "the loop never reached the per-item check"
        FlakySocket.emit.assert_not_awaited()
        assert client._event_queue.qsize() == 1

    async def test_an_emit_type_error_is_logged_not_fatal(
        self, client: SensiClient
    ) -> None:
        """A malformed payload must not kill the background task."""
        sio = MagicMock()
        sio.connected = True
        sio.emit = AsyncMock(side_effect=TypeError("not serialisable"))
        client._sio = sio

        await client._send_event("set_thing", object())
        await self._run_once(client)

        sio.emit.assert_awaited()

    async def test_the_loop_idles_while_disconnected(self, client: SensiClient) -> None:
        """With no socket the loop waits instead of spinning or crashing."""
        client._sio = None

        await client._send_event("get_info", {"icd_id": ICD_ID})
        await self._run_once(client)

        # Nothing was consumed - the event is still waiting for a connection.
        assert client._event_queue.qsize() == 1


class TestCirculatingFan:
    """Capability gating on the circulating fan setter."""

    async def test_setting_it_on_an_incapable_device_raises(
        self, client: SensiClient, mock_device
    ) -> None:
        """The thermostat is never asked for something it cannot do."""
        mock_device.capabilities.circulating_fan.capable = False

        with pytest.raises(HomeAssistantError, match="does not support it"):
            await client.async_set_circulating_fan_mode(mock_device, True, 20)


class TestTokenRefresh:
    """`try_refresh_access_token` and the refresh branches of `_connect`."""

    async def test_a_refresh_failure_becomes_a_connection_error(
        self, client: SensiClient
    ) -> None:
        """Whatever the auth layer raises is normalised for the caller."""
        with (
            patch(
                "custom_components.sensi.client.refresh_access_token",
                side_effect=RuntimeError("no network"),
            ),
            pytest.raises(SensiConnectionError, match="Error refreshing tokens"),
        ):
            await client.try_refresh_access_token()

    async def test_an_expired_token_is_refreshed_before_connecting(
        self, hass: HomeAssistant
    ) -> None:
        """A token known to be expired is renewed rather than tried and failed."""
        expired = AuthenticationConfig(
            refresh_token="refresh",
            access_token="access",
            expires_at=0,
            user_id="user",
        )
        client = SensiClient(hass, expired)
        fresh = AuthenticationConfig(
            refresh_token="refresh2",
            access_token="access2",
            expires_at=99_999_999_999,
            user_id="user",
        )

        with (
            patch(
                "custom_components.sensi.client.refresh_access_token",
                AsyncMock(return_value=fresh),
            ) as mock_refresh,
            patch.object(client, "_connect_client", AsyncMock()),
            patch("custom_components.sensi.client.socketio.AsyncClient", MagicMock()),
        ):
            await client._connect()

        mock_refresh.assert_awaited_once()

    async def test_a_connection_timeout_is_wrapped(self, client: SensiClient) -> None:
        """A timeout reaching Sensi is reported as a connection error."""
        with (
            patch.object(
                client, "_connect_client", AsyncMock(side_effect=TimeoutError)
            ),
            patch("custom_components.sensi.client.socketio.AsyncClient", MagicMock()),
            pytest.raises(
                SensiConnectionError, match="Timed out making the connection"
            ),
        ):
            await client._connect()

    async def test_an_unexpected_failure_is_wrapped(self, client: SensiClient) -> None:
        """Anything else is still a connection error, never a bare exception."""
        with (
            patch.object(
                client, "_connect_client", AsyncMock(side_effect=RuntimeError("odd"))
            ),
            patch("custom_components.sensi.client.socketio.AsyncClient", MagicMock()),
            pytest.raises(SensiConnectionError, match="Failed to connect"),
        ):
            await client._connect()


class TestResponseHelpers:
    """The module-level helpers used to interpret responses."""

    def test_no_error_gives_an_empty_description(self) -> None:
        """A missing error is not an error."""
        assert get_error_description_from_event_callback(None) == ""
        assert get_error_description_from_event_callback({}) == ""

    def test_a_description_is_extracted(self) -> None:
        """The nested description is what the user needs to see."""
        assert (
            get_error_description_from_event_callback(
                {"error": {"description": "OutOfRange"}, "icd_id": ICD_ID}
            )
            == "OutOfRange"
        )

    def test_an_error_without_a_description_gives_an_empty_string(self) -> None:
        """An unexpected error shape degrades rather than raising."""
        assert get_error_description_from_event_callback({"error": {}}) == ""

    @pytest.mark.parametrize(
        ("details", "expected"),
        [
            ({"message": "jwt expired"}, True),
            ({"message": "something else"}, False),
            ({}, False),
            (None, False),
            ("Connection error", False),
            (["jwt expired"], False),
        ],
    )
    def test_token_expiry_detection(self, details, expected) -> None:
        """Only a dict carrying the exact message counts as an expired token.

        The retry path sends a bare string here, so the non-dict guard is a
        live branch rather than defensive dead code.
        """
        assert is_token_expired(details) is expected

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"icd_id": ICD_ID}, ICD_ID),
            ({}, ""),
            (None, ""),
        ],
    )
    def test_icd_id_extraction(self, data, expected) -> None:
        """A payload with no identifier yields an empty string, not a KeyError."""
        assert extract_icd_id(data) == expected
