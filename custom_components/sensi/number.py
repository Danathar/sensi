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
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import ActionResponse, SensiClient, raise_if_error
from .const import SENSI_DOMAIN
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
        name="Temperature offset",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        step=STEP,
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
        name="Humidity offset",
        native_unit_of_measurement=PERCENTAGE,
        step=STEP,
        update_fn=lambda client, device, value: client.async_set_humidity_offset(
            device, value
        ),
        value_fn=lambda device: get_state(device).humidity_offset,
    ),
]


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

        # Note: self.hass is not set at this point
        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT,
            f"{SENSI_DOMAIN}_{device.name}_{description.key}",
            hass=hass,
        )

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
        raise_if_error(response, self.entity_description.name, value)
        self.async_write_ha_state()

        # Force data update since offsets control the thermostat state
        await self.coordinator.async_refresh()
