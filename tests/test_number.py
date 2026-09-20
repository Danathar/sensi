"""Tests for Sensi number component."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from custom_components.sensi.client import ActionResponse
from custom_components.sensi.number import (
    CIRCULATING_FAN_DUTY_CYCLE,
    NUMBER_TYPES,
    STEP,
    SensiNumberEntity,
    async_setup_entry,
)
from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.number.const import UNIT_CONVERTERS
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import UNDEFINED

_COMPONENT = Path(__file__).parents[1] / "custom_components" / "sensi"
STRINGS_FILES = (
    _COMPONENT / "strings.json",
    _COMPONENT / "translations" / "en.json",
)


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
    # Both sample thermostats have a circulating fan: two offsets plus the
    # duty cycle for each.
    assert len(async_add_entities.call_args[0][0]) == 6


async def test_setup_platform_skips_the_duty_cycle_without_a_circulating_fan(
    hass: HomeAssistant, mock_coordinator, mock_device
) -> None:
    """A thermostat without the capability gets the offsets only."""

    mock_device.capabilities.circulating_fan.capable = False
    mock_coordinator.get_devices = MagicMock(return_value=[mock_device])

    async_add_entities = MagicMock()
    await async_setup_entry(hass, mock_coordinator.config_entry, async_add_entities)

    keys = [
        entity.entity_description.key for entity in async_add_entities.call_args[0][0]
    ]
    assert keys == ["temperature_offset", "humidity_offset"]


def test_the_offsets_declare_their_step_where_home_assistant_reads_it() -> None:
    """`step` on a NumberEntityDescription is typed None and ignored.

    Home Assistant reads `native_step`; with only `step` set it fell back to
    its own default, which happened to be 1 as well. Pin the field so the
    step is declared rather than coincidental.
    """
    for description in NUMBER_TYPES:
        assert description.native_step == STEP
        assert description.step is None


def test_numbers_are_named_through_translations() -> None:
    """Each number names itself by `translation_key`, never a literal name."""
    for description in [*NUMBER_TYPES, CIRCULATING_FAN_DUTY_CYCLE]:
        assert description.translation_key == description.key
        assert description.name is UNDEFINED


@pytest.mark.parametrize("path", STRINGS_FILES, ids=lambda p: p.name)
def test_every_number_translation_key_has_a_name(path: Path) -> None:
    """A translation_key without a string shows up as a blank entity name."""
    names = json.loads(path.read_text(encoding="utf-8"))["entity"]["number"]

    for description in [*NUMBER_TYPES, CIRCULATING_FAN_DUTY_CYCLE]:
        assert names[description.translation_key]["name"], description.key


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


async def test_set_value_names_the_setting_in_the_error(
    hass: HomeAssistant, mock_device, mock_coordinator
) -> None:
    """The name now comes from translations, so the error names the key."""

    humidity_desc = next((s for s in NUMBER_TYPES if s.key == "humidity_offset"), None)
    entity = SensiNumberEntity(
        hass, mock_device, humidity_desc, mock_coordinator.config_entry
    )

    with patch.object(
        mock_coordinator.client, "async_set_humidity_offset"
    ) as mock_async_set_humidity_offset:
        mock_async_set_humidity_offset.return_value = ActionResponse("OutOfRange", None)

        with pytest.raises(
            HomeAssistantError, match="Unable to set humidity offset to 50.0"
        ):
            await entity.async_set_native_value(50.0)


class TestCirculatingFanDutyCycle:
    """The duty cycle number ported from upstream v2.2.0 (iprak/sensi#165)."""

    @staticmethod
    def _create_entity(
        hass: HomeAssistant, mock_device, mock_coordinator
    ) -> SensiNumberEntity:
        return SensiNumberEntity(
            hass, mock_device, CIRCULATING_FAN_DUTY_CYCLE, mock_coordinator.config_entry
        )

    def test_the_description_is_a_config_percentage(self) -> None:
        """The metadata that decides where and how the entity shows up."""

        assert CIRCULATING_FAN_DUTY_CYCLE.key == "circulating_duty_cycle"
        assert CIRCULATING_FAN_DUTY_CYCLE.translation_key == "circulating_duty_cycle"
        assert CIRCULATING_FAN_DUTY_CYCLE.entity_category == EntityCategory.CONFIG
        assert CIRCULATING_FAN_DUTY_CYCLE.native_unit_of_measurement == PERCENTAGE
        assert CIRCULATING_FAN_DUTY_CYCLE.device_class not in UNIT_CONVERTERS

    def test_the_entity_id_and_unique_id_follow_the_key(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """The key is upstream's, so a registry entry survives a move."""

        entity = self._create_entity(hass, mock_device, mock_coordinator)

        assert entity.entity_id == "number.sensi_living_room_circulating_duty_cycle"
        assert entity.unique_id == f"{mock_device.identifier}_circulating_duty_cycle"

    def test_the_value_and_bounds_come_from_the_thermostat(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Value from the state, range and step from the capabilities."""

        mock_device.state.circulating_fan.duty_cycle = 35
        mock_device.capabilities.circulating_fan.min_duty_cycle = 20
        mock_device.capabilities.circulating_fan.max_duty_cycle = 80
        mock_device.capabilities.circulating_fan.step = 10

        entity = self._create_entity(hass, mock_device, mock_coordinator)

        assert entity.native_value == 35
        assert entity.native_min_value == 20
        assert entity.native_max_value == 80
        assert entity.native_step == 10
        assert entity.native_unit_of_measurement == PERCENTAGE

    def test_the_step_is_the_sample_thermostats(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """sample.json reports step 5; the offsets keep their constant step."""

        entity = self._create_entity(hass, mock_device, mock_coordinator)
        assert entity.native_step == 5

        humidity_desc = next(s for s in NUMBER_TYPES if s.key == "humidity_offset")
        offset = SensiNumberEntity(
            hass, mock_device, humidity_desc, mock_coordinator.config_entry
        )
        assert offset.native_step == STEP

    @pytest.mark.parametrize("enabled", [True, False])
    def test_availability_follows_the_circulating_fan(
        self, hass: HomeAssistant, mock_device, mock_coordinator, enabled
    ) -> None:
        """A duty cycle means nothing while the fan is off."""

        mock_device.state.circulating_fan.enabled = enabled
        entity = self._create_entity(hass, mock_device, mock_coordinator)

        assert entity.available is enabled

    def test_an_offline_thermostat_is_unavailable_whatever_the_fan_does(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """`available_fn` narrows availability; it never widens it."""

        mock_device.state.circulating_fan.enabled = True
        mock_device.state.status = "offline"
        entity = self._create_entity(hass, mock_device, mock_coordinator)

        assert entity.available is False

    def test_the_offsets_stay_available_without_an_available_fn(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Descriptions without the hook keep the base entity's answer."""

        humidity_desc = next(s for s in NUMBER_TYPES if s.key == "humidity_offset")
        entity = SensiNumberEntity(
            hass, mock_device, humidity_desc, mock_coordinator.config_entry
        )

        mock_device.state.circulating_fan.enabled = False
        assert entity.available is True

    @pytest.mark.parametrize("enabled", [True, False])
    async def test_set_value_keeps_the_fans_enabled_state(
        self, hass: HomeAssistant, mock_device, mock_coordinator, enabled
    ) -> None:
        """Changing the duty cycle does not switch the fan on or off."""

        mock_device.state.circulating_fan.enabled = enabled
        entity = self._create_entity(hass, mock_device, mock_coordinator)

        with (
            patch.object(entity, "async_write_ha_state") as mock_async_write_ha_state,
            patch.object(mock_coordinator, "async_refresh") as mock_async_refresh,
            patch.object(
                mock_coordinator.client, "async_set_circulating_fan_mode"
            ) as mock_async_set_circulating_fan_mode,
        ):
            mock_async_set_circulating_fan_mode.return_value = ActionResponse(None, {})

            # Pass float and verify that int is passed down to client
            await entity.async_set_native_value(35.0)

            mock_async_set_circulating_fan_mode.assert_called_once_with(
                mock_device, enabled, 35
            )
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
