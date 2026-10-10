"""Sensi thermostat sensors."""

from collections.abc import Callable
from dataclasses import dataclass
import itertools
from typing import Any, Final, override

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import ATTR_BATTERY_VOLTAGE
from .coordinator import SensiConfigEntry, SensiDevice
from .data import ActiveSavingsEventState
from .entity import SensiDescriptionEntity, SensiEntity

# Two alkaline AA cells in series, as (pack voltage, estimated % remaining),
# highest first. Most of an alkaline cell's life is spent between 2.4V and
# 2.7V, so a straight line from 3.0V to 2.0V would read far too high for most
# of it.
BATTERY_DISCHARGE_CURVE: Final = (
    (3.0, 100),
    (2.8, 90),
    (2.6, 70),
    (2.4, 40),
    (2.2, 15),
    (2.0, 0),
)


def calculate_battery_level(voltage: float | None) -> int | None:
    """Calculate the battery level as a percentage of the pack voltage.

    Interpolates linearly between the points of BATTERY_DISCHARGE_CURVE,
    an approximated alkaline discharge curve.
    """
    # https://devzone.nordicsemi.com/f/nordic-q-a/28101/how-to-calculate-battery-voltage-into-percentage-for-aa-2-batteries-without-fluctuations
    # https://forum.arduino.cc/t/calculate-battery-percentage-of-alkaline-batteries-using-the-voltage/669958/17
    if voltage is None:
        return None

    v_max, p_max = BATTERY_DISCHARGE_CURVE[0]
    v_min, p_min = BATTERY_DISCHARGE_CURVE[-1]
    if voltage >= v_max:
        return p_max
    if voltage <= v_min:
        return p_min

    # The bounds above leave v_min < voltage < v_max, so some segment always
    # matches; the last one is reached at worst.
    (v_high, p_high), (v_low, p_low) = next(
        segment
        for segment in itertools.pairwise(BATTERY_DISCHARGE_CURVE)
        if voltage >= segment[1][0]
    )

    return round(p_low + (voltage - v_low) / (v_high - v_low) * (p_high - p_low))


@dataclass(frozen=True)
class SensiSensorEntityDescription(SensorEntityDescription):
    """Representation of a Sensi thermostat sensor."""

    extra_state_attributes_fn: Callable[[Any], dict[str, str]] | None = None
    value_fn: Callable[[SensiDevice], StateType] = None


SENSOR_TYPES: Final = [
    SensiSensorEntityDescription(
        device_class=SensorDeviceClass.TEMPERATURE,
        key="temperature",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.state.display_temp,
    ),
    SensiSensorEntityDescription(
        device_class=SensorDeviceClass.HUMIDITY,
        key="humidity",
        name="Humidity",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.state.humidity,
    ),
    SensiSensorEntityDescription(
        device_class=SensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        extra_state_attributes_fn=lambda device: {
            ATTR_BATTERY_VOLTAGE: device.state.battery_voltage
        },
        key="battery",
        name="Battery",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: calculate_battery_level(device.state.battery_voltage),
    ),
    SensiSensorEntityDescription(
        device_class=SensorDeviceClass.TEMPERATURE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:thermometer-low",
        key="cool_min_temp",
        name="Min setpoint",
        value_fn=lambda device: device.state.cool_min_temp,
    ),
    SensiSensorEntityDescription(
        device_class=SensorDeviceClass.TEMPERATURE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:thermometer-high",
        key="heat_max_temp",
        name="Max setpoint",
        value_fn=lambda device: device.state.heat_max_temp,
    ),
    SensiSensorEntityDescription(
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:fan",
        key="fan_speed",
        name="Fan speed",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.state.demand_status.fan,
    ),
    SensiSensorEntityDescription(
        # No device_class: wifi_connection_quality is a 0-100 quality
        # percentage, not a signal level. SIGNAL_STRENGTH would be a claim that
        # the number is in dB or dBm, and Home Assistant has no device class
        # for a bare percentage. The key stays "wifi_strength" because it is
        # part of the unique_id; only what is reported changes.
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:wifi-strength-outline",
        key="wifi_strength",
        name="Wifi quality",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.state.wifi_connection_quality,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensiConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up Sensi thermostat sensors."""
    coordinator = entry.runtime_data
    entities = [
        SensiSensorEntity(hass, device, description, entry)
        for device in coordinator.get_devices()
        for description in SENSOR_TYPES
    ]

    entities.extend(
        [
            ActiveSavingsEventEntity(hass, device, entry)
            for device in coordinator.get_devices()
        ]
    )

    async_add_entities(entities)


class SensiSensorEntity(SensiDescriptionEntity, SensorEntity):
    """Representation of a Sensi thermostat sensor."""

    entity_description: SensiSensorEntityDescription = None

    def __init__(
        self,
        hass: HomeAssistant,
        device: SensiDevice,
        description: SensiSensorEntityDescription,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(device, description, entry)

        self._set_entity_id(hass, ENTITY_ID_FORMAT, description.key)

    @property
    def native_value(self) -> StateType:
        """Return the value reported by the sensor."""
        return (
            self.entity_description.value_fn(self._device)
            if self.entity_description.value_fn
            else None
        )

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement of the sensor, if any."""
        return (
            self._state.temperature_unit
            if self.entity_description.device_class == SensorDeviceClass.TEMPERATURE
            else self.entity_description.native_unit_of_measurement
        )

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return the state attributes."""
        return (
            self.entity_description.extra_state_attributes_fn(self._device)
            if self.entity_description.extra_state_attributes_fn
            else None
        )

    @property
    def icon(self) -> str | None:
        """Return icon for sensor."""
        return self.entity_description.icon


class ActiveSavingsEventEntity(SensiEntity, SensorEntity):
    """Representation of an active energy savings event status sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "active_savings"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        hass: HomeAssistant,
        device: SensiDevice,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(device, entry)

        self._attr_unique_id = f"{device.identifier}_active_savings"
        self._attr_options = [state.value for state in ActiveSavingsEventState]

        # Setup does no first refresh, so the coordinator update that sets the
        # state comes a whole poll interval later. Start from the loaded state.
        self._update_state()

        self._set_entity_id(hass, ENTITY_ID_FORMAT, "active_savings")

    def _update_state(self) -> None:
        """Update the state of the sensor from the device's demand response."""

        demand_response = self._state.demand_response
        if demand_response:
            (current_state, start_time, end_time) = (
                demand_response.get_active_savings_event_state()
            )
        else:
            current_state = ActiveSavingsEventState.UNKNOWN
            start_time = None
            end_time = None

        self._attr_extra_state_attributes = {
            "start_time": start_time,
            "end_time": end_time,
        }
        self._attr_native_value = current_state.value

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Update state when the coordinator updates."""

        self._update_state()

        super()._handle_coordinator_update()
