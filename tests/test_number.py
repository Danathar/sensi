"""Tests for Sensi number component."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.sensi.client import ActionResponse
from custom_components.sensi.number import (
    NUMBER_TYPES,
    SensiNumberEntity,
    async_setup_entry,
)
from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.number.const import UNIT_CONVERTERS
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant


async def test_setup_platform(
    hass: HomeAssistant,
    mock_coordinator,
    mock_device,
    mock_device_with_humidification,
) -> None:
    """Test platform setup."""

    mock_coordinator.get_devices = MagicMock(
        return_value=[mock_device, mock_device_with_humidification]
    )

    async_add_entities = MagicMock()
    await async_setup_entry(hass, mock_coordinator.config_entry, async_add_entities)

    assert async_add_entities.called
    assert len(async_add_entities.call_args[0][0]) == 4


async def test_get_value(hass: HomeAssistant, mock_device, mock_coordinator) -> None:
    """Test native_value for humidity entity."""

    humidity_desc = next((s for s in NUMBER_TYPES if s.key == "humidity_offset"), None)
    entity = SensiNumberEntity(
        hass, mock_device, humidity_desc, mock_coordinator.config_entry
    )

    value = 35
    mock_device.state.humidity_offset = value

    assert entity.native_value == value


async def test_native_unit_of_measurement(
    hass: HomeAssistant, mock_device, mock_coordinator
) -> None:
    """Test native_unit_of_measurement."""

    humidity_desc = next((s for s in NUMBER_TYPES if s.key == "humidity_offset"), None)
    entity1 = SensiNumberEntity(
        hass, mock_device, humidity_desc, mock_coordinator.config_entry
    )
    assert (
        entity1.native_unit_of_measurement == humidity_desc.native_unit_of_measurement
    )

    temp_desc = next((s for s in NUMBER_TYPES if s.key == "temperature_offset"), None)
    entity2 = SensiNumberEntity(
        hass, mock_device, temp_desc, mock_coordinator.config_entry
    )
    assert entity2.native_unit_of_measurement == mock_device.state.temperature_unit


async def test_set_value(hass: HomeAssistant, mock_device, mock_coordinator) -> None:
    """Test async_set_native_value for humidity entity."""

    humidity_desc = next((s for s in NUMBER_TYPES if s.key == "humidity_offset"), None)
    entity = SensiNumberEntity(
        hass, mock_device, humidity_desc, mock_coordinator.config_entry
    )

    with (
        patch.object(entity, "async_write_ha_state") as mock_async_write_ha_state,
        patch.object(mock_coordinator, "async_refresh") as mock_async_refresh,
        patch.object(
            mock_coordinator.client, "async_set_humidity_offset"
        ) as mock_async_set_humidity_offset,
    ):
        mock_async_set_humidity_offset.return_value = ActionResponse(None, {})

        # Pass float and verify that int is passed down to client
        await entity.async_set_native_value(50.0)

        mock_async_set_humidity_offset.assert_called_once_with(mock_device, 50)
        mock_async_write_ha_state.assert_called_once()
        mock_async_refresh.assert_called_once()


class TestTemperatureOffsetIsADelta:
    """The offset is a shift, not a temperature.

    Home Assistant converts a number entity whose device class appears in
    `UNIT_CONVERTERS` into the instance's own unit. For
    NumberDeviceClass.TEMPERATURE that is the *absolute* converter, so on a
    metric instance with a Fahrenheit thermostat an offset of 0 was shown as
    -17.8 °C and the -5..+5 range became -20.6..-15 °C - every value that read
    sensibly to the user was outside what the backend accepts.
    """

    @staticmethod
    def _description():
        return next(s for s in NUMBER_TYPES if s.key == "temperature_offset")

    def test_the_offset_declares_no_converted_device_class(self):
        """Any device class in UNIT_CONVERTERS would reintroduce the bug.

        Asserting against the converter table rather than against
        `is not NumberDeviceClass.TEMPERATURE` keeps this honest if the offset
        is ever given a different class that Home Assistant also converts.
        """
        device_class = self._description().device_class

        assert device_class not in UNIT_CONVERTERS

    def test_the_humidity_offset_keeps_its_device_class(self):
        """Only the temperature one had to lose it; humidity is not converted."""
        humidity = next(s for s in NUMBER_TYPES if s.key == "humidity_offset")

        assert humidity.device_class == NumberDeviceClass.HUMIDITY
        assert humidity.device_class not in UNIT_CONVERTERS

    @pytest.mark.parametrize(
        ("display_scale", "expected"),
        [
            ("f", UnitOfTemperature.FAHRENHEIT),
            ("c", UnitOfTemperature.CELSIUS),
        ],
        ids=["fahrenheit_thermostat", "celsius_thermostat"],
    )
    async def test_the_unit_still_follows_the_thermostat(
        self,
        hass: HomeAssistant,
        mock_device,
        mock_coordinator,
        display_scale,
        expected,
    ) -> None:
        """Dropping the device class must not cost the per-device unit.

        The unit used to be selected by `device_class == TEMPERATURE`; it is
        now an explicit flag on the description. Without it the offset would
        report the constant on the description and mislabel a Celsius
        thermostat.
        """
        mock_device.state.display_scale = display_scale
        mock_device.state.temperature_unit = expected

        entity = SensiNumberEntity(
            hass, mock_device, self._description(), mock_coordinator.config_entry
        )

        assert entity.native_unit_of_measurement == expected

    async def test_the_value_and_bounds_are_passed_through_untouched(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Nothing between the thermostat and the native value rescales it."""
        mock_device.state.temp_offset = 3

        entity = SensiNumberEntity(
            hass, mock_device, self._description(), mock_coordinator.config_entry
        )

        assert entity.native_value == 3
        assert (
            entity.native_min_value == mock_device.capabilities.temp_offset_lower_bound
        )
        assert (
            entity.native_max_value == mock_device.capabilities.temp_offset_upper_bound
        )
