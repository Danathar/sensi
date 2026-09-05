"""End-to-end control and refresh behaviour.

These drive Home Assistant services against the loaded integration and assert
on what reached the wire, so they cover the whole path from service call
through the entity and client down to the emitted socket.io event.
"""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import FakeSensiBackend

CLIMATE = "climate.sensi_living_room"
DISPLAY_HUMIDITY = "switch.sensi_living_room_display_humidity"
AUX_HEAT = "switch.sensi_living_room_aux_heat"
ICD_ID = "36-6f-92-ff-fe-0c-0b-07"


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
