"""Sensi thermostat numeric settings."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.number import (
    ENTITY_ID_FORMAT,
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import ActionResponse, SensiClient, raise_if_error
from .coordinator import SensiConfigEntry, SensiDevice
from .data import State
from .entity import SensiDescriptionEntity

STEP: Final = 1


def get_state(device: SensiDevice) -> State:
    """Return the state of the device. This provides typing."""
    return device.state


@dataclass(frozen=True, kw_only=True)
class SensiNumberEntityDescription(NumberEntityDescription):
    """Representation of a Sensi thermostat numeric setting."""

    update_fn: Callable[
        [SensiClient, SensiDevice, int], Coroutine[Any, Any, ActionResponse]
    ]
    value_fn: Callable[[SensiDevice], int | None]

    # The bounds are per device and are reported in the thermostat's own
    # display scale, so they cannot be constants on the description.
    min_fn: Callable[[SensiDevice], int]
    max_fn: Callable[[SensiDevice], int]

    # The step is per device too for the circulating fan duty cycle; the
    # offsets keep the constant `native_step` on the description.
    step_fn: Callable[[SensiDevice], int] | None = None

    # Some settings only mean something while another one is on: the duty
    # cycle is unavailable until the circulating fan is enabled. None keeps the
    # entity available whenever the device is.
    available_fn: Callable[[SensiDevice], bool] | None = None

    # Report the thermostat's own display scale as the unit, rather than the
    # constant on the description. Set for the temperature offset, whose scale
    # is a per-device setting. This used to be inferred from
    # `device_class == TEMPERATURE`, which stopped working once that class had
    # to be dropped - see the temperature offset description below.
    unit_from_display_scale: bool = False


NUMBER_TYPES: Final = [
    SensiNumberEntityDescription(
        # No device_class. This value is a *delta* - how far to shift what the
        # thermostat displays - but NumberDeviceClass.TEMPERATURE makes Home
        # Assistant convert it to the instance's temperature unit with the
        # absolute converter. On a metric Home Assistant with a Fahrenheit
        # thermostat that turned an offset of 0 into -17.8 °C, and the -5..+5
        # range into -20.6..-15 °C; dragging the slider ran the same
        # conversion in reverse, so every value that read sensibly was outside
        # the range the backend accepts. Without a temperature device class
        # Home Assistant performs no conversion, and the unit below keeps the
        # label honest.
        entity_category=EntityCategory.CONFIG,
        key="temperature_offset",
        max_fn=lambda device: device.capabilities.temp_offset_upper_bound,
        min_fn=lambda device: device.capabilities.temp_offset_lower_bound,
        native_step=STEP,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        translation_key="temperature_offset",
        unit_from_display_scale=True,
        update_fn=lambda client, device, value: client.async_set_temperature_offset(
            device, value
        ),
        value_fn=lambda device: get_state(device).temp_offset,
    ),
    SensiNumberEntityDescription(
        device_class=NumberDeviceClass.HUMIDITY,
        entity_category=EntityCategory.CONFIG,
        key="humidity_offset",
        max_fn=lambda device: device.capabilities.humidity_offset_upper_bound,
        min_fn=lambda device: device.capabilities.humidity_offset_lower_bound,
        native_step=STEP,
        native_unit_of_measurement=PERCENTAGE,
        translation_key="humidity_offset",
        update_fn=lambda client, device, value: client.async_set_humidity_offset(
            device, value
        ),
        value_fn=lambda device: get_state(device).humidity_offset,
    ),
]

# Only offered when the thermostat reports the circulating fan capability.
# The bounds and step are the ones the thermostat reports under
# capabilities.circulating_fan, the same ones the client snaps a value to.
# Writing a value keeps the fan's current enabled state: the number changes
# how often the fan runs, the Circulating Fan switch decides whether it does.
# The key is upstream's (iprak/sensi v2.2.0), so an install moving between the
# two keeps its registry entry.
CIRCULATING_FAN_DUTY_CYCLE: Final = SensiNumberEntityDescription(
    available_fn=lambda device: get_state(device).circulating_fan.enabled,
    entity_category=EntityCategory.CONFIG,
    key="circulating_duty_cycle",
    max_fn=lambda device: device.capabilities.circulating_fan.max_duty_cycle,
    min_fn=lambda device: device.capabilities.circulating_fan.min_duty_cycle,
    native_unit_of_measurement=PERCENTAGE,
    step_fn=lambda device: device.capabilities.circulating_fan.step,
    translation_key="circulating_duty_cycle",
    update_fn=lambda client, device, value: client.async_set_circulating_fan_mode(
        device, get_state(device).circulating_fan.enabled, value
    ),
    value_fn=lambda device: get_state(device).circulating_fan.duty_cycle,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensiConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up Sensi thermostat numbers."""
    coordinator = entry.runtime_data

    entities = [
        SensiNumberEntity(hass, device, description, entry)
        for device in coordinator.get_devices()
        for description in NUMBER_TYPES
    ]

    entities.extend(
        SensiNumberEntity(hass, device, CIRCULATING_FAN_DUTY_CYCLE, entry)
        for device in coordinator.get_devices()
        if device.capabilities.circulating_fan.capable
    )

    async_add_entities(entities)


class SensiNumberEntity(SensiDescriptionEntity, NumberEntity):
    """Representation of a Sensi number entity."""

    entity_description: SensiNumberEntityDescription = None

    def __init__(
        self,
        hass: HomeAssistant,
        device: SensiDevice,
        description: SensiNumberEntityDescription,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the entity."""
        super().__init__(device, description, entry)

        self._set_entity_id(hass, ENTITY_ID_FORMAT, description.key)

    @property
    def native_value(self) -> float:
        """Return the value reported by the entity."""
        return self.entity_description.value_fn(self._device)

    @property
    def native_min_value(self) -> float:
        """Return the minimum value reported by the thermostat."""
        return self.entity_description.min_fn(self._device)

    @property
    def native_max_value(self) -> float:
        """Return the maximum value reported by the thermostat."""
        return self.entity_description.max_fn(self._device)

    @property
    def native_step(self) -> float | None:
        """Return the step reported by the thermostat, or the description's."""
        if self.entity_description.step_fn is not None:
            return self.entity_description.step_fn(self._device)

        return super().native_step

    @property
    def available(self) -> bool:
        """Return if the entity is available.

        A description with `available_fn` is also unavailable while the
        setting it depends on is off.
        """
        if not super().available:
            return False

        available_fn = self.entity_description.available_fn
        return available_fn is None or available_fn(self._device)

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement of the entity, if any."""
        return (
            self._state.temperature_unit
            if self.entity_description.unit_from_display_scale
            else self.entity_description.native_unit_of_measurement
        )

    async def async_set_native_value(self, value: float) -> None:
        """Update the setting."""
        response = await self.entity_description.update_fn(
            self.coordinator.client, self._device, int(value)
        )
        # The display name lives in translations now, so the key names the
        # setting in the error: "Unable to set temperature offset to 6".
        raise_if_error(response, self.entity_description.key.replace("_", " "), value)
        self.async_write_ha_state()

        # Force data update since offsets control the thermostat state
        await self.coordinator.async_refresh()
