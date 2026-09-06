"""Tests for Sensi."""

from unittest.mock import patch

import pytest

from custom_components.sensi.auth import (
    KEY_USER_ID,
    AuthenticationError,
    SensiConnectionError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_init_failed_missing_refresh_token(
    hass: HomeAssistant, mock_coordinator
) -> None:
    """Test AuthenticationConfig initialization with all fields."""

    mock_config = {}
    mock_entry = mock_coordinator.config_entry

    with patch(
        "homeassistant.helpers.storage.Store.async_load", return_value=mock_config
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_init_auth_failed(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """Test AuthenticationConfig initialization with authentication failure."""

    mock_entry = mock_coordinator.config_entry

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices"
        ) as mock_wait_for_devices,
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
    ):
        mock_wait_for_devices.side_effect = ConfigEntryAuthFailed("Mocked exception")

        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        mock_wait_for_devices.assert_called_once()
        assert mock_entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_init_exception_retry(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """Test AuthenticationConfig initialization with exception."""

    mock_entry = mock_coordinator.config_entry

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices"
        ) as mock_wait_for_devices,
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
    ):
        mock_wait_for_devices.side_effect = Exception("Mocked exception")

        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        mock_wait_for_devices.assert_called_once()
        assert mock_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_init_success(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """Test AuthenticationConfig initialization with all fields."""

    mock_entry = mock_coordinator.config_entry

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices"
        ) as mock_wait_for_devices,
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

        mock_wait_for_devices.assert_called_once()
        assert mock_entry.state is ConfigEntryState.LOADED


REAUTH_TARGET = "homeassistant.config_entries.ConfigEntry.async_start_reauth"
"""What Home Assistant calls to put a "reauthenticate" prompt in front of the user.

Asserting on this rather than on the flows in progress keeps these tests about
the classification made here: whether a reauth flow then survives is the
config flow's business, and it currently aborts for an entry with no
unique_id.
"""


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    "error",
    [
        SensiConnectionError("Sensi is down"),
        TimeoutError("no answer"),
    ],
    ids=["connection_error", "timeout"],
)
async def test_init_connection_failure_retries_without_reauth(
    hass: HomeAssistant, mock_coordinator, mock_auth_data, error
) -> None:
    """A backend that cannot be reached is a retry, not bad credentials.

    ConfigEntryAuthFailed would start a reauth flow and ask the user to go
    find a new refresh token for one that was never invalid.
    """

    mock_entry = mock_coordinator.config_entry

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices"
        ) as mock_wait_for_devices,
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch(REAUTH_TARGET) as mock_start_reauth,
    ):
        mock_wait_for_devices.side_effect = error

        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    mock_start_reauth.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_init_authentication_error_starts_reauth(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """A rejected credential still asks the user to reauthenticate."""

    mock_entry = mock_coordinator.config_entry

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices"
        ) as mock_wait_for_devices,
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch(REAUTH_TARGET) as mock_start_reauth,
    ):
        mock_wait_for_devices.side_effect = AuthenticationError("token rejected")

        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    mock_start_reauth.assert_called_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_init_keeps_the_config_entry_not_ready_reason(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """wait_for_devices names what timed out; that reason has to survive.

    It used to fall into the catch-all and be re-raised as "Unable to
    authenticate", pointing a user at their credentials over a device that
    simply did not answer.
    """

    reason = "Unable to gather device information in 20 seconds"
    mock_entry = mock_coordinator.config_entry

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices"
        ) as mock_wait_for_devices,
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch(REAUTH_TARGET) as mock_start_reauth,
    ):
        mock_wait_for_devices.side_effect = ConfigEntryNotReady(reason)

        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_entry.reason == reason
    mock_start_reauth.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_init_unexpected_error_does_not_blame_authentication(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """The catch-all retries, and says something true while doing it."""

    mock_entry = mock_coordinator.config_entry

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices"
        ) as mock_wait_for_devices,
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch(REAUTH_TARGET) as mock_start_reauth,
    ):
        mock_wait_for_devices.side_effect = ValueError("something else entirely")

        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    assert "authenticate" not in mock_entry.reason.lower()
    mock_start_reauth.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_refuses_a_store_for_a_different_account(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """A store whose user_id is not this entry's must not be connected with.

    There is one credential store for the whole integration but a unique_id
    per entry, so the two can disagree. Connecting anyway brings up the other
    account's thermostats as new devices and takes this entry's offline, with
    nothing in the log to explain it. AuthenticationError sends the user to
    reauth, which is the only thing that can fix it.
    """

    mock_entry = mock_coordinator.config_entry
    hass.config_entries.async_update_entry(mock_entry, unique_id="account_a")
    other_account = {**mock_auth_data, KEY_USER_ID: "account_b"}

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices"
        ) as mock_wait_for_devices,
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=other_account,
        ),
        patch(REAUTH_TARGET) as mock_start_reauth,
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        await hass.async_block_till_done()

    # Refused before the client was ever constructed.
    mock_wait_for_devices.assert_not_called()
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    mock_start_reauth.assert_called_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_accepts_a_store_for_this_account(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """The matching case still loads, so the guard is not just refusing."""

    mock_entry = mock_coordinator.config_entry
    hass.config_entries.async_update_entry(
        mock_entry, unique_id=mock_auth_data[KEY_USER_ID]
    )

    with (
        patch("custom_components.sensi.client.SensiClient.wait_for_devices"),
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.LOADED


STOP_TARGET = "custom_components.sensi.client.SensiClient.stop"
"""What tears a client down: disconnect *and* cancel the emit-loop task."""


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_failure_after_connecting_stops_the_client(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """Nothing else owns the client until setup finishes.

    wait_for_devices raises ConfigEntryNotReady from a state where the socket
    is already up, and Home Assistant retries with a brand-new SensiClient, so
    a client that is not stopped here stays connected with its own emit-loop
    task until Home Assistant restarts.
    """

    mock_entry = mock_coordinator.config_entry

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices",
            side_effect=ConfigEntryNotReady("no device answered"),
        ),
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch(STOP_TARGET) as mock_stop,
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    mock_stop.assert_awaited_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_platform_forwarding_failure_stops_the_client(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """The later failure path, after the coordinator has been created.

    The e2e reproduction only covers the wait_for_devices route; this one is
    reached with entry.runtime_data already assigned, which is the case an
    early return placed before the forwarding call would have missed.
    """

    mock_entry = mock_coordinator.config_entry

    with (
        patch("custom_components.sensi.client.SensiClient.wait_for_devices"),
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=RuntimeError("platform blew up"),
        ),
        patch(STOP_TARGET) as mock_stop,
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        await hass.async_block_till_done()

    mock_stop.assert_awaited_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_successful_setup_leaves_the_client_running(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """The guard that stops this being a self-inflicted outage.

    On success the coordinator owns the client and async_unload_entry is what
    stops it. Tearing it down here would disconnect every entry the moment it
    finished setting up.
    """

    mock_entry = mock_coordinator.config_entry

    with (
        patch("custom_components.sensi.client.SensiClient.wait_for_devices"),
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch(STOP_TARGET) as mock_stop,
    ):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.LOADED
    mock_stop.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_failure_before_the_client_exists_is_not_an_error(
    hass: HomeAssistant, mock_coordinator
) -> None:
    """get_stored_config runs first, so there may be nothing to stop."""

    mock_entry = mock_coordinator.config_entry

    with patch("homeassistant.helpers.storage.Store.async_load", return_value={}):
        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        await hass.async_block_till_done()

    # AuthenticationError -> ConfigEntryAuthFailed, not an AttributeError from
    # the cleanup reaching for a client that was never constructed.
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_teardown_error_does_not_mask_the_setup_failure(
    hass: HomeAssistant, mock_coordinator, mock_auth_data
) -> None:
    """The setup exception is what tells HA to retry; it has to survive.

    Cleanup in a finally block replaces the in-flight exception if it raises,
    which would turn a retryable ConfigEntryNotReady into something else.
    """

    mock_entry = mock_coordinator.config_entry
    reason = "no device answered"

    with (
        patch(
            "custom_components.sensi.client.SensiClient.wait_for_devices",
            side_effect=ConfigEntryNotReady(reason),
        ),
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch(STOP_TARGET, side_effect=RuntimeError("teardown went wrong")),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id) is False
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_entry.reason == reason
