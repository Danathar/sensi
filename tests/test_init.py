"""Tests for Sensi."""

from unittest.mock import patch

import pytest

from custom_components.sensi.auth import AuthenticationError, SensiConnectionError
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
