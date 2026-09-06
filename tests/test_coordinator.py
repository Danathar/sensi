"""Tests for Sensi coordinator."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensi.auth import AuthenticationError, SensiConnectionError
from custom_components.sensi.client import SensiClient
from custom_components.sensi.const import COORDINATOR_UPDATE_INTERVAL, SENSI_DOMAIN
from custom_components.sensi.coordinator import SensiUpdateCoordinator
from custom_components.sensi.data import AuthenticationConfig, SensiDevice
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed


def test_coordinator_initialization(hass: HomeAssistant) -> None:
    """Test SensiUpdateCoordinator initialization."""

    client = MagicMock(spec=SensiClient)
    mock_entry = MockConfigEntry(domain=SENSI_DOMAIN, data={}, entry_id="id1")
    coordinator = SensiUpdateCoordinator(hass, client, mock_entry)

    assert coordinator.hass == hass
    assert coordinator.client == client
    assert coordinator.name == "SensiUpdateCoordinator"

    expected_interval = timedelta(seconds=COORDINATOR_UPDATE_INTERVAL)
    assert coordinator.update_interval == expected_interval


class TestSensiUpdateCoordinatorGetDevices:
    """Test cases for SensiUpdateCoordinator.get_devices()."""

    def test_get_devices_returns_client_devices(self, hass: HomeAssistant, mock_json):
        """Test that get_devices returns devices from client."""
        _have_state1, device1 = SensiDevice.create(mock_json)
        _have_state2, device2 = SensiDevice.create(mock_json)

        client = MagicMock(spec=SensiClient)
        client.get_devices.return_value = [device1, device2]
        mock_entry = MockConfigEntry(domain=SENSI_DOMAIN, data={}, entry_id="id1")

        coordinator = SensiUpdateCoordinator(hass, client, mock_entry)
        devices = coordinator.get_devices()

        assert len(devices) == 2
        assert devices[0] == device1
        assert devices[1] == device2

    def test_get_devices_returns_empty_list_when_no_devices(self, hass: HomeAssistant):
        """Test that get_devices returns empty list when no devices."""
        client = MagicMock(spec=SensiClient)
        client.get_devices.return_value = []
        mock_entry = MockConfigEntry(domain=SENSI_DOMAIN, data={}, entry_id="id1")

        coordinator = SensiUpdateCoordinator(hass, client, mock_entry)
        devices = coordinator.get_devices()

        assert devices == []


class TestSensiUpdateCoordinatorIntegration:
    """Integration tests for SensiUpdateCoordinator."""

    def test_coordinator_properties_are_immutable(self, hass: HomeAssistant):
        """Test that coordinator properties persist correctly."""

        client = MagicMock(spec=SensiClient)
        mock_entry = MockConfigEntry(domain=SENSI_DOMAIN, data={}, entry_id="id1")
        coordinator = SensiUpdateCoordinator(hass, client, mock_entry)

        # Store references
        original_client = coordinator.client

        # Get devices to ensure no side effects
        coordinator.get_devices()

        # Verify references didn't change
        assert coordinator.client is original_client


class TestCoordinatorUpdateErrorMapping:
    """Test how the coordinator maps client errors during an update."""

    async def test_auth_error_maps_to_config_entry_auth_failed(
        self, mock_coordinator
    ) -> None:
        """A bad refresh token surfaces as ConfigEntryAuthFailed (triggers reauth)."""
        mock_coordinator.client.async_update_devices = AsyncMock(
            side_effect=AuthenticationError("bad refresh")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await mock_coordinator.update_method()

    async def test_a_rejected_refresh_token_reaches_reauth_through_the_real_client(
        self, mock_coordinator
    ) -> None:
        """The same mapping, but with nothing between the 401 and the caller.

        The test above mocks `async_update_devices` into raising
        AuthenticationError, which the real client never did: both
        `try_refresh_access_token` and `_connect` wrapped it in
        SensiConnectionError, so every refresh with a revoked token became
        UpdateFailed every 30 seconds, forever, and the user was never asked
        for a new token. Driving the real client against a rejected refresh
        proves the path rather than the mapping.
        """
        client = mock_coordinator.client
        # Expired, so _connect refreshes before it tries to connect - the path
        # a revoked token actually takes.
        client._config = AuthenticationConfig(
            refresh_token="revoked",
            access_token="access",
            expires_at=0,
            user_id="user",
        )

        with (
            patch(
                "custom_components.sensi.client.refresh_access_token",
                side_effect=AuthenticationError("Invalid token"),
            ),
            pytest.raises(ConfigEntryAuthFailed) as context,
        ):
            await mock_coordinator.update_method()

        # The reason survives the trip rather than being replaced by a
        # connection message.
        assert isinstance(context.value.__cause__, AuthenticationError)

    async def test_a_backend_outage_still_retries_rather_than_asking_for_a_token(
        self, mock_coordinator
    ) -> None:
        """The other half: a 5xx must not become a reauth prompt.

        auth._get_new_tokens raises SensiConnectionError for anything that is
        not a 4xx, and that has to stay a retry - the stored token is fine.
        """
        client = mock_coordinator.client
        client._config = AuthenticationConfig(
            refresh_token="good",
            access_token="access",
            expires_at=0,
            user_id="user",
        )

        with (
            patch(
                "custom_components.sensi.client.refresh_access_token",
                side_effect=SensiConnectionError("Sensi is down"),
            ),
            pytest.raises(UpdateFailed),
        ):
            await mock_coordinator.update_method()

    async def test_connection_error_maps_to_update_failed(
        self, mock_coordinator
    ) -> None:
        """A transient connection error surfaces as UpdateFailed (retry + backoff)."""
        mock_coordinator.client.async_update_devices = AsyncMock(
            side_effect=SensiConnectionError("down")
        )

        with pytest.raises(UpdateFailed):
            await mock_coordinator.update_method()

    async def test_success_does_not_raise(self, mock_coordinator) -> None:
        """A successful update does not raise."""
        mock_coordinator.client.async_update_devices = AsyncMock(return_value=None)

        await mock_coordinator.update_method()
