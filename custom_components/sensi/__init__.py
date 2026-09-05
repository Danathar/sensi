"""The Sensi device component."""

from copy import deepcopy

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.typing import StateType

from .auth import AuthenticationError, SensiConnectionError, get_stored_config
from .client import SensiClient
from .const import LOGGER
from .coordinator import SensiConfigEntry, SensiUpdateCoordinator
from .data import SensiDevice

SUPPORTED_PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: SensiConfigEntry):
    """Set up the Sensi component."""

    try:
        config = await get_stored_config(hass)
        client = SensiClient(hass, config)
        await client.wait_for_devices()

        entry.runtime_data = SensiUpdateCoordinator(hass, client, entry)
        await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_PLATFORMS)
    except ConfigEntryAuthFailed, ConfigEntryNotReady:
        # Both already say the right thing and carry their own reason.
        # ConfigEntryAuthFailed can be raised from the coordinator, and
        # wait_for_devices raises ConfigEntryNotReady naming what timed out.
        # Re-wrapping either would replace that reason with a worse one.
        raise
    except AuthenticationError as err:
        # The stored credential is the problem. ConfigEntryAuthFailed puts the
        # entry in a failure state and starts a reauth flow.
        # https://developers.home-assistant.io/docs/integration_setup_failures/
        raise ConfigEntryAuthFailed(str(err)) from err
    except (SensiConnectionError, TimeoutError) as err:
        # The backend was unreachable. That says nothing about the stored
        # token, so retry with backoff rather than sending the user off to
        # find a new refresh token for one that was never invalid.
        raise ConfigEntryNotReady(f"Unable to reach the Sensi service: {err}") from err
    except Exception as err:
        LOGGER.warning("Unexpected error setting up Sensi", exc_info=True)
        raise ConfigEntryNotReady(f"Unexpected error setting up Sensi: {err}") from err

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SensiConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = entry.runtime_data
    if coordinator:
        await coordinator.client.stop()
    return await hass.config_entries.async_unload_platforms(entry, SUPPORTED_PLATFORMS)


def get_config_option(
    device: SensiDevice, entry: SensiConfigEntry, key: str, default: StateType
) -> StateType:
    """Get the value of a config option."""

    options = entry.options.get(key, {})
    return options.get(device.identifier, default)


def set_config_option(
    hass: HomeAssistant,
    device: SensiDevice,
    entry: SensiConfigEntry,
    key: str,
    value: StateType,
) -> None:
    """Set the value of a config option."""

    new_options = deepcopy({**entry.options})
    options = new_options.get(key, {})
    options[device.identifier] = value
    new_options[key] = options

    hass.config_entries.async_update_entry(entry, options=new_options)
