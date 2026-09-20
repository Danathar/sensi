"""The Sensi device component."""

from copy import deepcopy

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.typing import StateType

from .auth import (
    AuthenticationError,
    SensiConnectionError,
    get_stored_config,
    is_user_id,
)
from .client import SensiClient
from .const import LOGGER, SENSI_DOMAIN
from .coordinator import SensiConfigEntry, SensiUpdateCoordinator
from .data import AuthenticationConfig, SensiDevice

SUPPORTED_PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: SensiConfigEntry):
    """Set up the Sensi component."""

    client: SensiClient | None = None
    setup_succeeded = False

    try:
        # The entry's unique_id is the Sensi user_id the entry was set up
        # with. Passing it lets get_stored_config refuse a store that belongs
        # to a different account rather than connecting as the wrong one. An
        # entry from an older upstream is not keyed that way yet; it is keyed
        # here, from the store, as soon as the store has loaded.
        config = await get_stored_config(hass, entry.unique_id)
        _adopt_stored_user_id(hass, entry, config)
        # The entry lets the client start a reauth flow itself when a setter's
        # recovery discovers the refresh token is dead - a service call has no
        # coordinator on its path to do that translation.
        client = SensiClient(hass, config, config_entry=entry)
        await client.wait_for_devices()

        entry.runtime_data = SensiUpdateCoordinator(hass, client, entry)
        await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_PLATFORMS)
        setup_succeeded = True
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
    finally:
        # Nothing else owns the client until the coordinator is handed to
        # entry.runtime_data and the platforms are up. wait_for_devices raises
        # ConfigEntryNotReady from a state where _connect() has already
        # succeeded, and Home Assistant retries with a brand-new SensiClient
        # every time - so without this each failed attempt left a client
        # connected to rt.sensiapi.io with its own emit-loop task, parsing
        # every state push into a device dict nobody reads, until Home
        # Assistant restarted.
        #
        # stop() rather than the __aexit__ this class already implements:
        # __aexit__ only disconnects, and the emit-loop task has to be
        # cancelled too. It is idempotent, so the async_unload_entry path
        # stopping the same client again is harmless.
        if client is not None and not setup_succeeded:
            try:
                await client.stop()
            except Exception:  # pylint: disable=broad-except # noqa: BLE001
                # A teardown problem must never replace the setup failure that
                # caused it: that exception is what tells Home Assistant
                # whether to retry or to ask for reauthentication.
                LOGGER.debug(
                    "Error while stopping the client after a failed setup",
                    exc_info=True,
                )

    return True


def _adopt_stored_user_id(
    hass: HomeAssistant, entry: SensiConfigEntry, config: AuthenticationConfig
) -> None:
    """Key an entry that upstream left without a user_id by the store's.

    Entries created by upstream v1.0.0 to v1.4.1 carry the login username or
    no unique_id at all (see `is_user_id`). Every token refresh writes the
    account's user_id into the store, so once such an entry has loaded the
    store is the one place its user_id is known. Writing it into the entry
    here is what lets the account guard in `get_stored_config` and the reauth
    flow's wrong_account check apply to that entry from then on, instead of
    refusing it forever - which is what they did before, and the only way out
    of that was to remove the integration and add it again.

    The store belongs to this entry: `single_config_entry` means there is no
    other entry it could have been written for. The one case left is an
    install that predates that flag and has two entries; if another entry
    already holds this user_id, leave this one as it is and say so, rather
    than hand Home Assistant a duplicate unique_id.
    """
    if is_user_id(entry.unique_id) or not config.user_id:
        return

    holder = hass.config_entries.async_entry_for_domain_unique_id(
        SENSI_DOMAIN, config.user_id
    )
    if holder is not None and holder.entry_id != entry.entry_id:
        LOGGER.warning(
            "Not re-keying this entry by the stored Sensi user_id: another "
            "entry already uses it"
        )
        return

    LOGGER.info(
        "Keying this entry by the Sensi user_id found in the stored credentials; "
        "it was set up by a version that keyed it by login or not at all"
    )
    hass.config_entries.async_update_entry(entry, unique_id=config.user_id)


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
