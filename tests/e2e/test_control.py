"""End-to-end control and refresh behaviour.

These drive Home Assistant services against the loaded integration and assert
on what reached the wire, so they cover the whole path from service call
through the entity and client down to the emitted socket.io event.
"""

import asyncio
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from socketio.exceptions import ConnectionError as SocketIOConnectionError

from custom_components.sensi.client import EMIT_LOOP_DELAY
from custom_components.sensi.const import CONFIG_REFRESH_TOKEN, SENSI_DOMAIN
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    PERCENTAGE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util.unit_system import METRIC_SYSTEM

from .conftest import FakeSensiBackend

CLIMATE = "climate.sensi_living_room"
DISPLAY_HUMIDITY = "switch.sensi_living_room_display_humidity"
AUX_HEAT = "switch.sensi_living_room_aux_heat"
CIRCULATING_FAN = "switch.sensi_living_room_circulating_fan"
CIRCULATING_DUTY_CYCLE = "number.sensi_living_room_circulating_duty_cycle"
TEMPERATURE_OFFSET = "number.sensi_living_room_temperature_offset"
HUMIDITY_OFFSET = "number.sensi_living_room_humidity_offset"
FAN_SUPPORT = "switch.sensi_living_room_fan_support"
ONLINE = "binary_sensor.sensi_living_room_online"
ICD_ID = "aa-bb-cc-dd-ee-ff-00-01"


async def test_set_temperature_reaches_the_wire_and_updates_state(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Setting a target temperature emits set_temperature and moves the state."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 72},
        blocking=True,
    )
    await hass.async_block_till_done()

    emitted = sensi_backend.last_emitted("set_temperature")
    assert emitted["icd_id"] == ICD_ID
    assert emitted["mode"] == "heat"
    assert emitted["target_temp"] == 72
    assert emitted["scale"] == "f"

    assert hass.states.get(CLIMATE).attributes["temperature"] == 72


async def test_set_temperature_with_hvac_mode_switches_mode_first(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """hvac_mode in set_temperature changes the mode, then sets its setpoint (#315).

    The sample thermostat is in HEAT. Without the switch the 70 went to the
    heat setpoint and the thermostat stayed in HEAT.
    """
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.COOL, ATTR_TEMPERATURE: 70},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sensi_backend.emitted_names()[-2:] == [
        "set_operating_mode",
        "set_temperature",
    ]
    assert sensi_backend.last_emitted("set_operating_mode") == {
        "icd_id": ICD_ID,
        "value": "cool",
    }
    emitted = sensi_backend.last_emitted("set_temperature")
    assert emitted["mode"] == "cool"
    assert emitted["target_temp"] == 70

    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.COOL
    assert state.attributes["temperature"] == 70


async def test_set_temperature_with_hvac_mode_turns_an_off_thermostat_on(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """From OFF, hvac_mode heat with a temperature heats to it (#315)."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.HEAT, ATTR_TEMPERATURE: 70},
        blocking=True,
    )
    await hass.async_block_till_done()

    emitted = sensi_backend.last_emitted("set_temperature")
    assert emitted["mode"] == "heat"
    assert emitted["target_temp"] == 70

    state = hass.states.get(CLIMATE)
    assert state.state == HVACMode.HEAT
    assert state.attributes["temperature"] == 70


# README "Limitations" (#382, closing #315 rows 2 and 3): a set_temperature
# carrying hvac_mode is refused by Home Assistant when its values do not fit
# the *current* mode, before the integration is called, and the workaround is
# set_hvac_mode first, then set_temperature. Each case is (hvac_mode, the
# set_temperature arguments, Home Assistant's translation key for the refusal,
# the set_temperature payloads the workaround puts on the wire). The sample
# thermostat is in HEAT with heat_max_temp 72.
_REFUSED_BEFORE_THE_INTEGRATION = {
    "cool setpoint above the heat bound": (
        HVACMode.COOL,
        {ATTR_TEMPERATURE: 74},
        "temp_out_of_range",
        [{"mode": "cool", "target_temp": 74}],
    ),
    "low/high range when switching to auto": (
        HVACMode.AUTO,
        {ATTR_TARGET_TEMP_LOW: 66, ATTR_TARGET_TEMP_HIGH: 78},
        "missing_target_temperature_range_entity_feature",
        [
            {"mode": "heat", "target_temp": 66},
            {"mode": "cool", "target_temp": 78},
        ],
    ),
}


@pytest.mark.parametrize(
    ("hvac_mode", "arguments", "translation_key", "_payloads"),
    _REFUSED_BEFORE_THE_INTEGRATION.values(),
    ids=_REFUSED_BEFORE_THE_INTEGRATION.keys(),
)
async def test_set_temperature_with_hvac_mode_outside_the_current_range_is_refused(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    hvac_mode: HVACMode,
    arguments: dict,
    translation_key: str,
    _payloads: list,
) -> None:
    """Home Assistant refuses the call against the current mode (README).

    The translation key is Home Assistant's own, not one this integration
    raises, so the refusal is pinned to where README says it happens. If this
    starts passing the call through, the README bullet "Changing mode and
    setpoint in one call" is out of date.
    """
    emitted_before = list(sensi_backend.emitted)

    with pytest.raises(ServiceValidationError) as refused:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: hvac_mode, **arguments},
            blocking=True,
        )
    await hass.async_block_till_done()

    assert refused.value.translation_domain == CLIMATE_DOMAIN
    assert refused.value.translation_key == translation_key
    assert sensi_backend.emitted == emitted_before
    assert hass.states.get(CLIMATE).state == HVACMode.HEAT


@pytest.mark.parametrize(
    ("hvac_mode", "arguments", "_translation_key", "payloads"),
    _REFUSED_BEFORE_THE_INTEGRATION.values(),
    ids=_REFUSED_BEFORE_THE_INTEGRATION.keys(),
)
async def test_set_hvac_mode_then_set_temperature_is_the_documented_workaround(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    hvac_mode: HVACMode,
    arguments: dict,
    _translation_key: str,
    payloads: list,
) -> None:
    """The two calls README gives for a refused set_temperature both land."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: hvac_mode},
        blocking=True,
    )
    await hass.async_block_till_done()
    emitted_between = len(sensi_backend.emitted)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, **arguments},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sensi_backend.last_emitted("set_operating_mode")["value"] == hvac_mode
    sent = sensi_backend.emitted[emitted_between:]
    assert [name for name, _ in sent] == ["set_temperature"] * len(payloads)
    assert [
        {"mode": payload["mode"], "target_temp": payload["target_temp"]}
        for _, payload in sent
    ] == payloads

    state = hass.states.get(CLIMATE)
    assert state.state == hvac_mode
    for attribute, value in arguments.items():
        assert state.attributes[attribute] == value


async def test_set_hvac_mode_reaches_the_wire_and_updates_state(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Changing the HVAC mode emits set_operating_mode and moves the state."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.COOL},
        blocking=True,
    )
    await hass.async_block_till_done()

    emitted = sensi_backend.last_emitted("set_operating_mode")
    assert emitted == {"icd_id": ICD_ID, "value": "cool"}

    assert hass.states.get(CLIMATE).state == HVACMode.COOL


async def _climate_call(hass: HomeAssistant, service: str, **data) -> None:
    """Call a climate service on the sample thermostat and let it settle."""
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: CLIMATE, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def test_turn_on_keeps_the_current_mode(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """turn_on on a thermostat that is already cooling leaves it cooling (#316)."""
    await _climate_call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
    await _climate_call(hass, SERVICE_SET_FAN_MODE, **{ATTR_FAN_MODE: "on"})
    sensi_backend.emitted.clear()

    await _climate_call(hass, SERVICE_TURN_ON)

    assert "set_operating_mode" not in sensi_backend.emitted_names()
    assert hass.states.get(CLIMATE).state == HVACMode.COOL
    # The fan still goes back to auto, as turn_on has always done.
    assert sensi_backend.last_emitted("set_fan_mode") == {
        "icd_id": ICD_ID,
        "value": "auto",
    }


async def test_turn_on_restores_the_mode_it_was_turned_off_from(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """COOL, turn_off, turn_on comes back in COOL rather than HEAT (#316)."""
    await _climate_call(hass, SERVICE_SET_HVAC_MODE, **{ATTR_HVAC_MODE: HVACMode.COOL})
    await _climate_call(hass, SERVICE_TURN_OFF)
    assert hass.states.get(CLIMATE).state == HVACMode.OFF

    await _climate_call(hass, SERVICE_TURN_ON)

    assert sensi_backend.last_emitted("set_operating_mode") == {
        "icd_id": ICD_ID,
        "value": "cool",
    }
    assert hass.states.get(CLIMATE).state == HVACMode.COOL


async def test_turn_on_with_fan_support_off_sends_no_fan_mode(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """With the Fan support switch off, turn_on leaves the fan alone (#316)."""
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: FAN_SUPPORT}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes.get("fan_modes") is None
    sensi_backend.emitted.clear()

    await _climate_call(hass, SERVICE_TURN_ON)

    assert "set_fan_mode" not in sensi_backend.emitted_names()


async def test_switch_round_trip(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """A capability switch emits its setting event and reflects the new value."""
    assert hass.states.get(DISPLAY_HUMIDITY).state == STATE_ON

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: DISPLAY_HUMIDITY},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sensi_backend.last_emitted("set_display_humidity") == {
        "icd_id": ICD_ID,
        "value": "off",
    }
    assert hass.states.get(DISPLAY_HUMIDITY).state == STATE_OFF

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DISPLAY_HUMIDITY},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sensi_backend.last_emitted("set_display_humidity") == {
        "icd_id": ICD_ID,
        "value": "on",
    }
    assert hass.states.get(DISPLAY_HUMIDITY).state == STATE_ON


async def test_circulating_fan_switch_round_trip(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """The switch emits set_circulating_fan and the other entities follow it.

    The sample thermostat reports the fan enabled at a 10% duty cycle. The
    climate entity's deprecated `circulating_fan` attribute carries the same
    setting and must keep agreeing with the switch until it is removed.
    """
    assert hass.states.get(CIRCULATING_FAN).state == STATE_ON
    assert hass.states.get(CLIMATE).attributes["circulating_fan"] is True
    assert hass.states.get(CIRCULATING_DUTY_CYCLE).state == "10"

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: CIRCULATING_FAN},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Turning off sends the thermostat's own duty cycle back, so the value
    # the user set in the app is still there when the fan is turned on again.
    assert sensi_backend.last_emitted("set_circulating_fan") == {
        "icd_id": ICD_ID,
        "value": {"enabled": "off", "duty_cycle": 10},
    }
    assert hass.states.get(CIRCULATING_FAN).state == STATE_OFF
    assert hass.states.get(CLIMATE).attributes["circulating_fan"] is False
    # A duty cycle means nothing while the fan is off.
    assert hass.states.get(CIRCULATING_DUTY_CYCLE).state == STATE_UNAVAILABLE

    await hass.services.async_call(
        "switch",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: CIRCULATING_FAN},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sensi_backend.last_emitted("set_circulating_fan") == {
        "icd_id": ICD_ID,
        "value": {"enabled": "on", "duty_cycle": 10},
    }
    assert hass.states.get(CIRCULATING_FAN).state == STATE_ON
    assert hass.states.get(CLIMATE).attributes["circulating_fan"] is True
    assert hass.states.get(CIRCULATING_DUTY_CYCLE).state == "10"


async def test_climate_fan_mode_refreshes_the_circulating_fan_entities(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """A fan mode picked on the climate card updates the switch and number (#318).

    Leaving Circulate turns circulation off on the thermostat. The Circulating
    Fan switch and the duty cycle number read the same setting, so they must
    follow at once rather than at the next poll.
    """
    assert hass.states.get(CIRCULATING_FAN).state == STATE_ON
    assert hass.states.get(CIRCULATING_DUTY_CYCLE).state == "10"

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_FAN_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_FAN_MODE: "auto"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(CLIMATE).attributes["circulating_fan"] is False
    assert hass.states.get(CIRCULATING_FAN).state == STATE_OFF
    assert hass.states.get(CIRCULATING_DUTY_CYCLE).state == STATE_UNAVAILABLE


async def test_circulating_fan_duty_cycle_reaches_the_wire(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Setting the duty cycle emits set_circulating_fan with the fan left on."""
    state = hass.states.get(CIRCULATING_DUTY_CYCLE)
    # The range and step the sample thermostat reports, unconverted.
    assert state.attributes["unit_of_measurement"] == PERCENTAGE
    assert state.attributes["min"] == 10
    assert state.attributes["max"] == 100
    assert state.attributes["step"] == 5

    # The number refreshes the coordinator after a write, and the fake
    # backend serves its scripted state on every connect. Script the value
    # the thermostat would report once it has accepted the write.
    sensi_backend.devices[ICD_ID]["state"]["circulating_fan"]["duty_cycle"] = 35

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: CIRCULATING_DUTY_CYCLE, ATTR_VALUE: 35},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sensi_backend.last_emitted("set_circulating_fan") == {
        "icd_id": ICD_ID,
        "value": {"enabled": "on", "duty_cycle": 35},
    }
    assert hass.states.get(CIRCULATING_DUTY_CYCLE).state == "35"
    assert hass.states.get(CIRCULATING_FAN).state == STATE_ON


async def test_circulating_fan_duty_cycle_is_snapped_to_the_thermostats_step(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """A value off the step grid goes out rounded to it, as the app would send."""
    sensi_backend.devices[ICD_ID]["state"]["circulating_fan"]["duty_cycle"] = 40

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: CIRCULATING_DUTY_CYCLE, ATTR_VALUE: 38},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sensi_backend.last_emitted("set_circulating_fan")["value"] == {
        "enabled": "on",
        "duty_cycle": 40,
    }


@pytest.mark.parametrize(
    ("entity_id", "event", "state_key", "value"),
    [
        (TEMPERATURE_OFFSET, "set_temp_offset", "temp_offset", -2),
        (HUMIDITY_OFFSET, "set_humidity_offset", "humidity_offset", 7),
    ],
    ids=["temperature_offset", "humidity_offset"],
)
async def test_offset_number_reaches_the_wire(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    entity_id: str,
    event: str,
    state_key: str,
    value: int,
) -> None:
    """Each offset number emits its own setting event with the offset as `value`.

    Every other control has its event name and payload pinned here. These two
    did not: the fake backend acks any event name it does not know, so a
    renamed event or payload key kept the whole suite green while the real
    thermostat would never have seen the change.
    """
    assert hass.states.get(entity_id).state == "0"

    # The number refreshes the coordinator after a write, and the fake
    # backend serves its scripted state on every connect. Script the value
    # the thermostat would report once it has accepted the write.
    sensi_backend.devices[ICD_ID]["state"][state_key] = value

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sensi_backend.last_emitted(event) == {"icd_id": ICD_ID, "value": value}
    assert hass.states.get(entity_id).state == str(value)


async def test_backend_error_surfaces_to_the_caller(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """An error ack from the thermostat is raised, not swallowed."""
    sensi_backend.acks["set_display_humidity"] = (
        {"error": {"description": "ThermostatOffline"}, "icd_id": ICD_ID},
    )

    with pytest.raises(HomeAssistantError, match="ThermostatOffline"):
        await hass.services.async_call(
            "switch",
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: DISPLAY_HUMIDITY},
            blocking=True,
        )

    # The failed write must not be reflected locally.
    assert hass.states.get(DISPLAY_HUMIDITY).state == STATE_ON


async def test_unsupported_capability_is_unavailable(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
) -> None:
    """The sample thermostat reports no aux stage, so aux heat stays unavailable."""
    assert hass.states.get(AUX_HEAT).state == STATE_UNAVAILABLE


async def test_coordinator_refresh_reconnects_and_picks_up_new_state(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """A coordinator update reconnects and applies the thermostat's new state."""
    before = hass.states.get(CLIMATE)
    assert before.attributes["current_temperature"] is not None

    sensi_backend.devices[ICD_ID]["state"]["display_temp"] = 61
    sensi_backend.devices[ICD_ID]["state"]["humidity"] = 33

    connections_before = len(sensi_backend.connections)

    await sensi_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert sensi_entry.runtime_data.last_update_success
    assert len(sensi_backend.connections) == connections_before + 1
    assert sensi_backend.disconnects >= 1

    after = hass.states.get(CLIMATE)
    # The climate entity reports whole degrees (PRECISION_WHOLE).
    assert after.attributes["current_temperature"] == 61
    assert after.attributes["current_humidity"] == 33


async def test_a_refresh_does_not_wait_out_state_delivered_inside_connect(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """A ``state`` event that lands inside ``connect()`` ends the refresh wait.

    The real library dispatches events from its read loop while ``connect()``
    is still waiting to be woken, so the ``state`` pushes right behind the
    handshake can reach the client before ``connect()`` returns. The refresh
    created its per-device waiters only afterwards. That state resolved
    nothing, and every such refresh sat out PREPARE_DEVICES_TIMEOUT - 20
    seconds late, stretching the 30-second poll to about 50.
    """
    client = sensi_entry.runtime_data.client
    sensi_backend.state_before_connect_returns = True

    # A timeout far above the bound below, so waiting it out fails the test.
    with patch("custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 30):
        try:
            await asyncio.wait_for(client.async_update_devices(), 2)
        except TimeoutError:
            pytest.fail("the refresh waited for state that had already arrived")


async def test_a_refresh_waits_for_a_setter_whose_ack_is_in_flight(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """The 30-second reconnect does not drop the ack of a setter it overtakes.

    python-socketio drops pending ack callbacks on disconnect. A refresh tick
    landing between a setter's emit and its ack tore the socket down, and the
    setter timed out with "Future not done" although the thermostat had
    accepted the command - the new value went unshown, and a ``mode: single``
    automation stopped at that step.
    """
    client = sensi_entry.runtime_data.client
    socket = sensi_backend.sockets[-1]
    original_emit = socket.emit
    setter_emitted = asyncio.Event()

    async def emit_with_slow_ack(name, data=None, namespace=None, callback=None):
        if name != "set_temperature":
            await original_emit(name, data, namespace, callback)
            return
        sensi_backend.emitted.append((name, data))
        ack = sensi_backend.ack_for(name, data)

        async def late_ack() -> None:
            # The backend's round trip before it acknowledges.
            await asyncio.sleep(0.2)
            if socket.connected:
                callback(*ack)

        sensi_backend.schedule(late_ack())
        setter_emitted.set()

    socket.emit = emit_with_slow_ack
    connections_before = len(sensi_backend.connections)

    with (
        patch("custom_components.sensi.client.SET_EVENT_TIMEOUT", 1),
        patch("custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 1),
    ):
        call = hass.async_create_task(
            hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 72},
                blocking=True,
            )
        )
        await asyncio.wait_for(setter_emitted.wait(), 3)

        # The tick lands while the setter's ack is still on its way.
        await client.async_update_devices()
        await call

    # Emitted once and acknowledged, and the refresh still reconnected.
    assert sensi_backend.emitted_names().count("set_temperature") == 1
    assert len(sensi_backend.connections) == connections_before + 1


async def test_a_refresh_does_not_wait_for_a_setter_still_queued(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """A setter that never reached the wire is carried by the new connection.

    The refresh waits only for acks a disconnect would drop. A setter queued
    while the socket is down has none on the wire, and the reconnect is what
    gets it there - waiting for its ack first would only let it time out.
    """
    client = sensi_entry.runtime_data.client
    sensi_backend.sockets[-1].connected = False

    with patch("custom_components.sensi.client.SET_EVENT_TIMEOUT", 3):
        call = hass.async_create_task(
            hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 71},
                blocking=True,
            )
        )
        async with asyncio.timeout(1):
            while client._event_queue.empty():
                await asyncio.sleep(0.01)

        # Well inside the setter's timeout: the refresh must not wait it out.
        await asyncio.wait_for(client.async_update_devices(), 2)
        await call

    assert sensi_backend.last_emitted("set_temperature")["target_temp"] == 71


async def test_entities_go_unavailable_after_repeated_failed_refreshes(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Two failed refreshes in a row reach the state machine as unavailable.

    One failure is tolerated (MAX_CONSECUTIVE_CONNECTION_FAILURES); the
    second makes SensiEntity.available False. Home Assistant's coordinator
    notifies listeners only on the failure that flips last_update_success,
    so before the coordinator notified again on the second one, no state
    was written after it and the climate entity kept showing its last
    values as current for as long as the outage lasted.
    """
    coordinator = sensi_entry.runtime_data
    sensi_backend.connect_error = SocketIOConnectionError("Sensi is down")

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    for _ in range(2):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE
        assert hass.states.get(ONLINE).state == STATE_UNAVAILABLE

    # The first refresh that works brings them back.
    sensi_backend.connect_error = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.consecutive_connection_failures == 0
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE
    assert hass.states.get(ONLINE).state == STATE_ON


async def test_a_refresh_with_no_state_counts_as_a_failure(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """A reconnect that brings no state is an outage, not a quiet success.

    The backend accepts the socket and then sends nothing. The refresh used to
    swallow its own timeout, so the coordinator reset the failure count and
    the entities showed the last values as current for the whole outage. It
    now fails like a connect that could not be made, and the entities go
    unavailable after the same number of refreshes.
    """
    coordinator = sensi_entry.runtime_data
    sensi_backend.withhold_state = True

    with patch("custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.05):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert not coordinator.last_update_success
        assert coordinator.consecutive_connection_failures == 1
        assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

        for _ in range(2):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
            assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

        # The first refresh that brings state back ends the outage.
        sensi_backend.withhold_state = False
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert coordinator.consecutive_connection_failures == 0
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_a_setter_that_timed_out_is_not_replayed_on_reconnect(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """The reproduction from issue #68, end to end.

    A setter issued while the socket is down times out and the user is told it
    failed. The event stayed on the outgoing queue, and because the connection
    is torn down and rebuilt on every 30-second refresh, it went out on the
    next connect - the thermostat acting on a command the user was told had
    not happened, potentially an hour later.
    """
    socket = sensi_backend.sockets[-1]
    socket.connected = False

    with (
        patch("custom_components.sensi.client.SET_EVENT_TIMEOUT", 0.05),
        pytest.raises(HomeAssistantError, match="Future not done"),
    ):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 70},
            blocking=True,
        )

    assert "set_temperature" not in sensi_backend.emitted_names()

    # The connection comes back, and the emit loop gets its chance to drain.
    socket.connected = True
    await asyncio.sleep(EMIT_LOOP_DELAY * 3)
    await hass.async_block_till_done()

    assert "set_temperature" not in sensi_backend.emitted_names(), (
        "the setter the user was told had failed was emitted anyway"
    )


async def test_a_setter_still_in_flight_when_the_socket_returns_is_emitted(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Discarding stale work must not break the reconnect-and-send case.

    The socket drops on every refresh, so a setter issued during one of those
    windows and emitted once the connection returns - inside its own timeout -
    is an ordinary success that has to keep working.
    """
    socket = sensi_backend.sockets[-1]
    socket.connected = False

    async def reconnect_shortly() -> None:
        await asyncio.sleep(EMIT_LOOP_DELAY)
        socket.connected = True

    hass.async_create_task(reconnect_shortly())

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 71},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sensi_backend.last_emitted("set_temperature")["target_temp"] == 71


async def test_a_celsius_thermostat_without_a_cool_limit_accepts_a_cooling_setpoint(
    hass: HomeAssistant,
    sensi_backend: FakeSensiBackend,
    stored_credentials: None,
    enable_custom_integrations: None,
) -> None:
    """A null cool_min_temp must not leave COOL with an empty valid range.

    This sets up its own entry rather than using `sensi_entry`, because the
    bug needs a Celsius thermostat on a metric instance. The null limit used
    to fall back to 45 - the app's °F limit - read as 45 °C, above max_temp,
    so Home Assistant refused every cooling setpoint.
    """
    hass.config.units = METRIC_SYSTEM
    sensi_backend.devices[ICD_ID]["state"].update(
        display_scale="c",
        operating_mode="cool",
        current_cool_temp=24,
        current_heat_temp=20,
        display_temp=23.5,
        cool_min_temp=None,
    )

    entry = MockConfigEntry(
        domain=SENSI_DOMAIN,
        data={CONFIG_REFRESH_TOKEN: "e2e_refresh_token"},
        unique_id="e2e_user",
        title="Sensi Thermostat",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 25},
        blocking=True,
    )
    await hass.async_block_till_done()

    emitted = sensi_backend.last_emitted("set_temperature")
    assert emitted["mode"] == "cool"
    assert emitted["target_temp"] == 25
    assert emitted["scale"] == "c"

    await sensi_backend.shutdown()
