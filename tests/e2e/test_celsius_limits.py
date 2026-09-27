"""End-to-end setpoint limits for a Celsius thermostat with no reported limit.

`cool_min_temp` and `heat_max_temp` fall back to the Sensi app's limits when the
backend sends them null or leaves them out. Those limits are in °F, and the
fields are in the thermostat's own scale, so on a °C thermostat the fallback
has to be converted before Home Assistant uses it as a bound.
"""

from collections.abc import AsyncIterator

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensi.const import CONFIG_REFRESH_TOKEN, SENSI_DOMAIN
from homeassistant.components.climate import (
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import METRIC_SYSTEM

from .conftest import FakeSensiBackend

CLIMATE = "climate.sensi_living_room"


@pytest.fixture
async def celsius_entry(
    hass: HomeAssistant,
    sensi_backend: FakeSensiBackend,
    stored_credentials: None,
    enable_custom_integrations: None,
    request: pytest.FixtureRequest,
) -> AsyncIterator[MockConfigEntry]:
    """Set up a °C thermostat whose state is patched by the test's parameter."""
    state = next(iter(sensi_backend.devices.values()))["state"]
    state.update(
        display_scale="c",
        current_cool_temp=24,
        current_heat_temp=20,
        display_temp=23.5,
        **request.param,
    )
    hass.config.units = METRIC_SYSTEM

    entry = MockConfigEntry(
        domain=SENSI_DOMAIN,
        data={CONFIG_REFRESH_TOKEN: "e2e_refresh_token"},
        unique_id="e2e_user",
        title="Sensi Thermostat",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    yield entry

    await sensi_backend.shutdown()


@pytest.mark.parametrize(
    "celsius_entry",
    [{"operating_mode": "cool", "cool_min_temp": None}],
    indirect=True,
)
async def test_a_cooling_setpoint_is_accepted_with_no_cool_min_temp(
    hass: HomeAssistant, sensi_backend: FakeSensiBackend, celsius_entry
) -> None:
    """The floor is 45 °F in °C, not 45 °C above a 37.2 °C ceiling."""
    attributes = hass.states.get(CLIMATE).attributes
    assert attributes[ATTR_MIN_TEMP] < attributes[ATTR_MAX_TEMP]

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 25},
        blocking=True,
    )

    assert sensi_backend.last_emitted("set_temperature")["target_temp"] == 25


@pytest.mark.parametrize(
    "celsius_entry",
    [{"operating_mode": "heat", "heat_max_temp": None}],
    indirect=True,
)
async def test_the_heating_ceiling_is_99_fahrenheit_with_no_heat_max_temp(
    hass: HomeAssistant, celsius_entry
) -> None:
    """99 °F is 37.2 °C; a ceiling of 99 °C would accept any setpoint."""
    assert hass.states.get(CLIMATE).attributes[ATTR_MAX_TEMP] == pytest.approx(
        37.2, abs=0.1
    )
