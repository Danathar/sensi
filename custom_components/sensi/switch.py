"""Sensi thermostat setting switches."""

from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.switch import (
    ENTITY_ID_FORMAT,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import get_config_option, set_config_option
from .client import raise_if_error
from .const import (
    CONFIG_AUX_HEATING,
    CONFIG_FAN_SUPPORT,
    DEFAULT_CONFIG_FAN_SUPPORT,
    FAN_CIRCULATE_DUTY_CYCLE_DEFAULT,
)
from .coordinator import SensiConfigEntry, SensiDevice
from .data import OperatingMode
from .entity import SensiDescriptionEntity
from .event import SettingEventName


@dataclass
class SensiCapabilityEntityDescriptionMixin:
    """Mixin for Sensi thermostat setting."""

    setting: SettingEventName


@dataclass
class SensiCapabilityEntityDescription(
    SwitchEntityDescription, SensiCapabilityEntityDescriptionMixin
):
    """Representation of a Sensi thermostat setting."""

    # This needs the annotation: a bare assignment is not a dataclass field, so
    # the inherited `entity_category=None` default would overwrite it.
    entity_category: EntityCategory | None = EntityCategory.CONFIG


SWITCH_TYPES: Final = [
    # The `key` represents the attribute on State and Capabilities
    # These switches do not need to update the climate entity.
    # The names come from strings.json through `translation_key`, so they
    # can be localised; the entity IDs are generated from `key` below and do
    # not change with the name.
    SensiCapabilityEntityDescription(
        key="display_humidity",
        setting=SettingEventName.DISPLAY_HUMIDITY,
        translation_key="display_humidity",
        icon="mdi:water-percent",
    ),
    SensiCapabilityEntityDescription(
        key="continuous_backlight",
        setting=SettingEventName.CONTINUOUS_BACKLIGHT,
        translation_key="continuous_backlight",
        icon="mdi:wall-sconce-round",
    ),
    SensiCapabilityEntityDescription(
        key="display_time",
        setting=SettingEventName.DISPLAY_TIME,
        translation_key="display_time",
        icon="mdi:clock",
    ),
    SensiCapabilityEntityDescription(
        key="keypad_lockout",
        setting=SettingEventName.KEYPAD_LOCKOUT,
        translation_key="keypad_lockout",
        icon="mdi:lock",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensiConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up Sensi thermostat setting switches."""
    coordinator = entry.runtime_data

    entities = []
    for device in coordinator.get_devices():
        capabilities = device.capabilities

        # A device might not support a setting e.g. Continuous Backlight
        entities.extend(
            SensiCapabilitySettingSwitch(hass, device, description, entry)
            for description in SWITCH_TYPES
            if getattr(capabilities, description.key)
        )

        entities.append(SensiFanSupportSwitch(hass, device, entry))
        entities.append(SensiAuxHeatSwitch(hass, device, entry))
        entities.append(SensiCirculatingFanSwitch(hass, device, entry))

        if device.capabilities.humidity_control.humidification:
            entities.append(SensiHumidificationSwitch(hass, device, entry))

    async_add_entities(entities)


class SensiCapabilitySettingSwitch(SensiDescriptionEntity, SwitchEntity):
    """Representation of a Sensi thermostat capability setting."""

    entity_description: SensiCapabilityEntityDescription

    def __init__(
        self,
        hass: HomeAssistant,
        device: SensiDevice,
        description: SensiCapabilityEntityDescription,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the setting."""
        super().__init__(device, description, entry)

        self._set_entity_id(hass, ENTITY_ID_FORMAT, description.key)

    @property
    def is_on(self) -> bool | None:
        """Return True if entity is on."""
        return getattr(self._state, self.entity_description.key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""

        await self._set_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._set_value(False)

    async def _set_value(self, value: bool) -> None:
        response = await self.coordinator.client.async_set_bool_setting(
            self._device, self.entity_description.setting, value
        )
        # The display name lives in translations now, so the key names the
        # setting in the error: "Unable to set keypad lockout to True".
        raise_if_error(response, self.entity_description.key.replace("_", " "), value)
        self.async_write_ha_state()
        # The setting should not change thermostat operation, so let update happen on regular schedule


class SensiFanSupportSwitch(SensiDescriptionEntity, SwitchEntity):
    """Representation of Sensi thermostat fan support setting."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: SensiDevice,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the setting."""

        description = SwitchEntityDescription(
            key=CONFIG_FAN_SUPPORT,
            name="Fan",
            icon="mdi:fan",
            entity_category=EntityCategory.CONFIG,
        )

        super().__init__(device, description, entry)

        # Cache status to avoid querying ConfigEntry
        self._status: bool | None = None

        self._set_entity_id(hass, ENTITY_ID_FORMAT, description.key)

    @property
    def is_on(self) -> bool | None:
        """Return True if entity is on."""
        if self._status is None:
            self._status = get_config_option(
                self._device,
                self._entry,
                CONFIG_FAN_SUPPORT,
                DEFAULT_CONFIG_FAN_SUPPORT,
            )
        return self._status

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._set_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._set_value(False)

    async def _set_value(self, value: bool) -> None:
        set_config_option(
            self.hass, self._device, self._entry, CONFIG_FAN_SUPPORT, value
        )
        self._status = value
        self.async_write_ha_state()

        # Use coordinator to notify climate entity
        self.coordinator.async_update_listeners()


def _mode_to_restore(mode: OperatingMode | None) -> OperatingMode:
    """Return the operating mode to restore when aux heating is turned off.

    AUX is never a valid mode to restore, otherwise turning the switch off
    would re-apply aux heating and the switch could never go off. This happens
    when the thermostat is already in AUX as the entity is created.
    """
    if mode is None or mode == OperatingMode.AUX:
        return OperatingMode.HEAT

    return mode


class SensiAuxHeatSwitch(SensiDescriptionEntity, SwitchEntity):
    """Representation of Sensi thermostat aux heating setting."""

    _last_operating_mode_before_aux_heat: OperatingMode | None

    def __init__(
        self,
        hass: HomeAssistant,
        device: SensiDevice,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the setting."""

        description = SwitchEntityDescription(
            key=CONFIG_AUX_HEATING,
            name="Auxiliary Heating",
            icon="mdi:heat-pump",
            entity_category=EntityCategory.CONFIG,
        )

        super().__init__(device, description, entry)

        self._set_entity_id(hass, ENTITY_ID_FORMAT, description.key)

        self._last_operating_mode_before_aux_heat = _mode_to_restore(
            device.state.operating_mode
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available and self._device.capabilities.operating_mode_settings.aux
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if aux heating is on."""
        return self._state.operating_mode == OperatingMode.AUX

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn aux heating on."""

        self._last_operating_mode_before_aux_heat = _mode_to_restore(
            self._state.operating_mode
        )

        response = await self.coordinator.client.async_set_operating_mode(
            self._device, OperatingMode.AUX
        )
        raise_if_error(response, "operating mode", OperatingMode.AUX.value)
        self.async_write_ha_state()

        # Use coordinator to notify climate entity
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn aux heating off."""

        response = await self.coordinator.client.async_set_operating_mode(
            self._device, self._last_operating_mode_before_aux_heat
        )
        raise_if_error(
            response,
            "operating mode",
            self._last_operating_mode_before_aux_heat.value,
        )
        self.async_write_ha_state()

        # Use coordinator to notify climate entity
        self.coordinator.async_update_listeners()


class SensiHumidificationSwitch(SensiDescriptionEntity, SwitchEntity):
    """Representation of Sensi thermostat humidification support setting."""

    def __init__(
        self,
        hass: HomeAssistant,
        device: SensiDevice,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the setting."""

        description = SwitchEntityDescription(
            key="Humidification",
            name="Humidification",
            icon="mdi:air-humidifier",
            entity_category=EntityCategory.CONFIG,
        )

        super().__init__(device, description, entry)

        self._set_entity_id(hass, ENTITY_ID_FORMAT, description.key)

    @property
    def is_on(self) -> bool | None:
        """Return True if entity is on."""
        return self._state.humidity_control.humidification.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._set_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._set_value(False)

    async def _set_value(self, enabled: bool) -> None:
        response = await self.coordinator.client.async_enable_humidification(
            self._device, enabled
        )

        raise_if_error(response, "humidification", enabled)
        self.async_write_ha_state()

        # Use coordinator to notify climate entity
        self.coordinator.async_update_listeners()


class SensiCirculatingFanSwitch(SensiDescriptionEntity, SwitchEntity):
    """Representation of Sensi thermostat circulating fan setting.

    This is the same setting the climate entity's Circulate fan mode toggles;
    the switch exposes it on its own so the duty cycle number entity has
    something to depend on, and so an automation can read it without going
    through the climate attributes.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        device: SensiDevice,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the setting."""

        description = SwitchEntityDescription(
            key="circulating_fan",
            translation_key="circulating_fan",
            icon="mdi:fan",
            entity_category=EntityCategory.CONFIG,
        )

        super().__init__(device, description, entry)

        self._set_entity_id(hass, ENTITY_ID_FORMAT, description.key)

    @property
    def available(self) -> bool:
        """Return True if the entity is available and the thermostat has the fan."""
        return super().available and self._device.capabilities.circulating_fan.capable

    @property
    def is_on(self) -> bool | None:
        """Return True if the circulating fan is enabled."""
        return self._state.circulating_fan.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the circulating fan on."""
        await self._set_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the circulating fan off."""
        await self._set_value(False)

    async def _set_value(self, enabled: bool) -> None:
        # Both directions send the thermostat's own duty cycle back, as the
        # climate entity's fan mode does: enabling runs the fan at the value the
        # user set, disabling leaves that value alone for next time. The state
        # reports 0 when none was ever set and the backend rejects 0, so that
        # case falls back to the default.
        duty_cycle = (
            self._state.circulating_fan.duty_cycle or FAN_CIRCULATE_DUTY_CYCLE_DEFAULT
        )

        response = await self.coordinator.client.async_set_circulating_fan_mode(
            self._device, enabled, duty_cycle
        )

        raise_if_error(response, "circulating fan", enabled)
        self.async_write_ha_state()

        # Use coordinator to notify the climate and duty cycle entities
        self.coordinator.async_update_listeners()
