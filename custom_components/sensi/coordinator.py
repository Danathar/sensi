"""The Sensi data coordinator."""

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .auth import AuthenticationError, SensiConnectionError
from .client import SensiClient
from .const import (
    COORDINATOR_UPDATE_INTERVAL,
    LOGGER,
    MAX_CONSECUTIVE_CONNECTION_FAILURES,
)
from .data import SensiDevice

type SensiConfigEntry = ConfigEntry[SensiUpdateCoordinator]


class SensiUpdateCoordinator(DataUpdateCoordinator):
    """The Sensi data update coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SensiClient,
        config_entry: SensiConfigEntry,
    ) -> None:
        """Initialize Sensi coordinator."""

        self._consecutive_failed_count = 0

        async def async_update_devices() -> None:
            """Update device data."""

            try:
                await self.client.async_update_devices()
                self._consecutive_failed_count = 0
            except AuthenticationError as err:
                # The refresh token itself is invalid, trigger HA's reauth flow
                # instead of retrying forever.
                raise ConfigEntryAuthFailed from err
            except SensiConnectionError as err:
                self._consecutive_failed_count += 1
                LOGGER.info(
                    "Failed to connect to Sensi API, consecutive failed count: %d",
                    self._consecutive_failed_count,
                )
                # SensiEntity.available goes False once the count passes
                # MAX_CONSECUTIVE_CONNECTION_FAILURES, but Home Assistant only
                # notifies listeners on the failure that flips
                # last_update_success - the first one, while the entities are
                # still available. Nothing wrote their state again after that,
                # so they showed the last values for the whole outage. Notify
                # once more on the failure that makes them unavailable.
                if (
                    self._consecutive_failed_count
                    == MAX_CONSECUTIVE_CONNECTION_FAILURES + 1
                ):
                    self.async_update_listeners()
                raise UpdateFailed(str(err)) from err

        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name="SensiUpdateCoordinator",
            update_method=async_update_devices,
            update_interval=timedelta(seconds=COORDINATOR_UPDATE_INTERVAL),
        )

        self.client = client

    def get_devices(self) -> list[SensiDevice]:
        """Sensi devices."""
        return self.client.get_devices()

    @property
    def consecutive_connection_failures(self) -> int:
        """Return the number of consecutive failed updates."""
        return self._consecutive_failed_count
