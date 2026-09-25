"""Base Sensi entity."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import (
    DeviceInfo,
    EntityDescription,
    async_generate_entity_id,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import MAX_CONSECUTIVE_CONNECTION_FAILURES, SENSI_ATTRIBUTION, SENSI_DOMAIN
from .coordinator import SensiConfigEntry, SensiUpdateCoordinator
from .data import SensiDevice, State


class SensiEntity(CoordinatorEntity[SensiUpdateCoordinator]):
    """Representation of a Sensi entity."""

    _attr_has_entity_name = True
    _attr_attribution = SENSI_ATTRIBUTION

    def __init__(
        self,
        device: SensiDevice,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the entity."""

        super().__init__(entry.runtime_data)
        self._device = device
        self._entry = entry
        self._attr_unique_id = device.identifier

        self._attr_device_info = DeviceInfo(
            identifiers={(SENSI_DOMAIN, device.identifier)},
            name=device.name,
            manufacturer="Sensi",
            model=device.info.model_number,
            serial_number=device.info.serial_number,
        )

    def _set_entity_id(
        self, hass: HomeAssistant, entity_id_format: str, key: str | None = None
    ) -> None:
        """Set the entity_id to `sensi_<device name>`, plus `_<key>` if given.

        An entity_id is what automations and dashboards refer to, so every
        platform builds it here rather than spelling out the pattern itself.
        `self.hass` is not set until the entity is added, so the caller passes
        it: `async_generate_entity_id` needs it to avoid an entity_id that is
        already taken.
        """
        object_id = f"{SENSI_DOMAIN}_{self._device.name}"
        if key is not None:
            object_id = f"{object_id}_{key}"

        self.entity_id = async_generate_entity_id(
            entity_id_format, object_id, hass=hass
        )

    @property
    def _state(self) -> State:
        """Current device state."""
        return self._device.state

    @property
    def available(self) -> bool:
        """Return if the entity is available.

        The entity is not available if the fetch failed or if the device is offline.
        """
        return (
            self.coordinator.consecutive_connection_failures
            <= MAX_CONSECUTIVE_CONNECTION_FAILURES
            and self._state.is_online
        )


class SensiDescriptionEntity(SensiEntity):
    """Representation of a Sensi description entity."""

    def __init__(
        self,
        device: SensiDevice,
        description: EntityDescription,
        entry: SensiConfigEntry,
    ) -> None:
        """Initialize the entity."""

        super().__init__(device, entry)
        self.entity_description = description

        # Override the _attr_unique_id to include description.key
        # description would be passed for sensor and switch domains.
        # https://developers.home-assistant.io/docs/entity_registry_index/
        self._attr_unique_id = f"{device.identifier}_{description.key}"
