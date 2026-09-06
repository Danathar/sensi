"""Tests for Sensi authentication module."""

from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest

from custom_components.sensi.auth import (
    KEY_ACCESS_TOKEN,
    KEY_EXPIRES_AT,
    KEY_REFRESH_TOKEN,
    KEY_USER_ID,
    OAUTH_URL2,
    AuthenticationError,
    SensiConnectionError,
    async_save_config,
    get_stored_config,
    refresh_access_token,
    validate_refresh_token,
)
from custom_components.sensi.data import AuthenticationConfig
from homeassistant.core import HomeAssistant


class TestAuthenticationConfig:
    """Test cases for AuthenticationConfig class."""

    def test_authentication_config_init_all_fields(self):
        """Test AuthenticationConfig initialization with all fields."""
        config = AuthenticationConfig(
            user_id="user123",
            access_token="access_token_123",
            expires_at=1234567890.0,
            refresh_token="refresh_token_123",
        )
        assert config.user_id == "user123"
        assert config.access_token == "access_token_123"
        assert config.expires_at == 1234567890.0
        assert config.refresh_token == "refresh_token_123"

        headers = config.headers
        assert headers == {"Authorization": "bearer access_token_123"}

    def test_authentication_config_partial_init(self):
        """Test AuthenticationConfig initialization with default values."""
        config = AuthenticationConfig(
            refresh_token="refresh_token_123",
        )
        assert config.user_id is None
        assert config.access_token is None
        assert config.expires_at is None
        assert config.refresh_token == "refresh_token_123"

    def test_authentication_config_headers_multiple_calls_consistent(self):
        """Test headers property returns consistent value."""
        config = AuthenticationConfig(
            user_id="user123",
            access_token="access_token_123",
            expires_at=1234567890.0,
            refresh_token="refresh_token_123",
        )
        headers1 = config.headers
        headers2 = config.headers
        assert headers1 == headers2


@pytest.mark.parametrize(("message"), [("Test error message"), ("")])
def test_authentication_error(message) -> None:
    """Test AuthenticationError is an Exception."""
    error = AuthenticationError(message)
    assert isinstance(error, Exception)
    assert error.message == message


class TestSensiConnectionError:
    """Test cases for SensiConnectionError exception."""

    def test_sensi_connection_error_creation(self):
        """Test creating SensiConnectionError."""
        error = SensiConnectionError("Connection timeout")
        assert error.message == "Connection timeout"
        assert str(error) == "Connection timeout"

    def test_sensi_connection_error_is_exception(self):
        """Test SensiConnectionError is an Exception."""
        error = SensiConnectionError("Test")
        assert isinstance(error, Exception)

    def test_sensi_connection_error_with_empty_message(self):
        """Test SensiConnectionError with empty message."""
        error = SensiConnectionError("")
        assert error.message == ""

    def test_sensi_connection_error_can_be_raised_and_caught(self):
        """Test SensiConnectionError can be raised and caught."""
        with pytest.raises(SensiConnectionError) as exc_info:
            raise SensiConnectionError("Network error")
        assert exc_info.value.message == "Network error"

    def test_sensi_connection_error_different_from_authentication_error(self):
        """Test SensiConnectionError is different from AuthenticationError."""
        with pytest.raises(SensiConnectionError):
            raise SensiConnectionError("Connection failed")

        with pytest.raises(AuthenticationError):
            raise AuthenticationError("Auth failed")

    def test_sensi_connection_error_message_with_timeout(self):
        """Test SensiConnectionError with timeout message."""
        error = SensiConnectionError("Timed out getting access token")
        assert error.message == "Timed out getting access token"

    def test_sensi_connection_error_message_with_network_details(self):
        """Test SensiConnectionError with network details."""
        error = SensiConnectionError("Failed to connect to oauth.sensiapi.io")
        assert error.message == "Failed to connect to oauth.sensiapi.io"


async def test_refresh_access_token(
    hass: HomeAssistant,
    mock_auth_data,
    aioclient_mock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test refresh_access_token function."""

    refresh_token = "refresh_token_123"

    # Return different value in POST request to simulate getting new access token
    expires_in = 3100
    json = {
        KEY_ACCESS_TOKEN: "new_access_token_999",
        KEY_REFRESH_TOKEN: "new_refresh_token_999",
        "expires_in": expires_in,
        KEY_USER_ID: "user123",
    }

    freezer.tick()
    expected_persistent_data = deepcopy(json)

    expected_persistent_data[KEY_EXPIRES_AT] = (
        datetime.now() + timedelta(seconds=expires_in)
    ).timestamp()
    expected_persistent_data.pop("expires_in")

    aioclient_mock.post(OAUTH_URL2, json=json)

    with (
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch("homeassistant.helpers.storage.Store.async_save") as mock_async_save,
    ):
        result = await refresh_access_token(hass, refresh_token)

        mock_async_save.assert_called_once_with(expected_persistent_data)
        assert result is not None


async def test_refresh_access_token_auth_failure(
    hass: HomeAssistant, mock_auth_data, aioclient_mock
) -> None:
    """A 4xx from the token endpoint means the refresh token is invalid."""

    refresh_token = "refresh_token_123"
    aioclient_mock.post(OAUTH_URL2, status=401)

    with (
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        pytest.raises(AuthenticationError),
    ):
        await refresh_access_token(hass, refresh_token)


async def test_refresh_access_token_server_error_is_transient(
    hass: HomeAssistant, mock_auth_data, aioclient_mock
) -> None:
    """A 5xx from the token endpoint is transient, not an auth failure."""

    refresh_token = "refresh_token_123"
    aioclient_mock.post(OAUTH_URL2, status=503)

    with (
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        pytest.raises(SensiConnectionError),
    ):
        await refresh_access_token(hass, refresh_token)


async def test_refresh_access_token_timeout(
    hass: HomeAssistant, mock_auth_data, aioclient_mock
) -> None:
    """Test refresh_access_token function."""

    refresh_token = "refresh_token_123"
    aioclient_mock.post(OAUTH_URL2, exc=TimeoutError)

    with (
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        pytest.raises(SensiConnectionError),
    ):
        await refresh_access_token(hass, refresh_token)


async def test_get_stored_config(hass: HomeAssistant, mock_auth_data) -> None:
    """Stored credentials are returned as an AuthenticationConfig."""

    with patch(
        "homeassistant.helpers.storage.Store.async_load",
        return_value=mock_auth_data,
    ):
        config = await get_stored_config(hass)

    assert config.refresh_token == mock_auth_data[KEY_REFRESH_TOKEN]
    assert config.access_token == mock_auth_data[KEY_ACCESS_TOKEN]
    assert config.expires_at == mock_auth_data[KEY_EXPIRES_AT]
    assert config.user_id == mock_auth_data[KEY_USER_ID]


async def test_get_stored_config_with_nothing_stored(hass: HomeAssistant) -> None:
    """An empty store means there is nothing to authenticate with.

    Store.async_load returns None before anything has been written - the
    install that was never set up, or whose storage file was removed.
    """

    with (
        patch("homeassistant.helpers.storage.Store.async_load", return_value=None),
        pytest.raises(AuthenticationError),
    ):
        await get_stored_config(hass)


async def test_get_stored_config_without_a_refresh_token(hass: HomeAssistant) -> None:
    """Stored data predating the refresh token is rejected, not returned partial."""

    with (
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value={KEY_ACCESS_TOKEN: "access_token_123"},
        ),
        pytest.raises(AuthenticationError),
    ):
        await get_stored_config(hass)


async def test_refresh_access_token_uses_the_stored_refresh_token(
    hass: HomeAssistant, mock_auth_data, aioclient_mock
) -> None:
    """Called without a token, the refresh falls back to the stored one.

    This is the path the coordinator takes on every re-authentication after
    setup; the caller only supplies a token during the config flow.
    """

    aioclient_mock.post(
        OAUTH_URL2,
        json={
            KEY_ACCESS_TOKEN: "new_access_token_999",
            KEY_REFRESH_TOKEN: "new_refresh_token_999",
            "expires_in": 3100,
            KEY_USER_ID: "user123",
        },
    )

    # refresh_access_token writes the new tokens back into the loaded dict.
    stored_refresh_token = mock_auth_data[KEY_REFRESH_TOKEN]

    with (
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value=mock_auth_data,
        ),
        patch("homeassistant.helpers.storage.Store.async_save"),
    ):
        result = await refresh_access_token(hass)

    assert aioclient_mock.call_count == 1
    posted = aioclient_mock.mock_calls[0][2]
    assert posted[KEY_REFRESH_TOKEN] == stored_refresh_token
    assert result.refresh_token == "new_refresh_token_999"


async def test_refresh_access_token_with_no_token_anywhere(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """No supplied token and an empty store is an authentication failure.

    It must fail before the token endpoint is called - posting a null refresh
    token would earn a 401 and be reported as a rejected token instead.
    """

    with (
        patch("homeassistant.helpers.storage.Store.async_load", return_value=None),
        pytest.raises(AuthenticationError),
    ):
        await refresh_access_token(hass)

    assert aioclient_mock.call_count == 0


async def test_refresh_access_token_with_stored_data_lacking_a_token(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Stored data without a refresh token fails the same way as no data."""

    with (
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value={KEY_ACCESS_TOKEN: "access_token_123"},
        ),
        pytest.raises(AuthenticationError),
    ):
        await refresh_access_token(hass)

    assert aioclient_mock.call_count == 0


class TestValidateRefreshToken:
    """Validation must not write to the store.

    There is exactly one store for the whole integration - STORAGE_KEY is the
    domain, not the entry id - so a validation that saves overwrites whatever
    credentials the running entry has. The config flow needs to know which
    account a token belongs to *before* deciding whether to accept it, which
    is what this separation is for.
    """

    async def test_validation_leaves_the_store_alone(
        self, hass: HomeAssistant, mock_auth_data, aioclient_mock
    ) -> None:
        """The store is neither read nor written while validating."""
        aioclient_mock.post(
            OAUTH_URL2,
            json={
                KEY_ACCESS_TOKEN: "b_access",
                KEY_REFRESH_TOKEN: "b_rotated",
                "expires_in": 3600,
                KEY_USER_ID: "account_b",
            },
        )

        with (
            patch(
                "homeassistant.helpers.storage.Store.async_load",
                return_value=mock_auth_data,
            ),
            patch("homeassistant.helpers.storage.Store.async_save") as mock_save,
        ):
            config = await validate_refresh_token(hass, "a_token_for_b")

        mock_save.assert_not_called()
        assert config.user_id == "account_b"
        # The rotated token, which is what has to be saved if the flow accepts:
        # Sensi rotates on every exchange, so the input token is now spent.
        assert config.refresh_token == "b_rotated"
        assert config.access_token == "b_access"

    async def test_validation_still_reports_a_rejected_token(
        self, hass: HomeAssistant, aioclient_mock
    ) -> None:
        """A 4xx is an auth failure here exactly as it is at runtime."""
        aioclient_mock.post(OAUTH_URL2, status=401)

        with pytest.raises(AuthenticationError):
            await validate_refresh_token(hass, "bad")

    async def test_validation_still_reports_a_transient_failure(
        self, hass: HomeAssistant, aioclient_mock
    ) -> None:
        """A 5xx must not be escalated to reauth."""
        aioclient_mock.post(OAUTH_URL2, status=503)

        with pytest.raises(SensiConnectionError):
            await validate_refresh_token(hass, "token")


class TestAsyncSaveConfig:
    """Persisting an accepted credential."""

    async def test_save_writes_every_field_and_keeps_the_rest(
        self, hass: HomeAssistant
    ) -> None:
        """Unrelated keys already in the store survive the write."""
        config = AuthenticationConfig(
            user_id="user123",
            access_token="access",
            expires_at=1234567890.0,
            refresh_token="refresh",
        )

        with (
            patch(
                "homeassistant.helpers.storage.Store.async_load",
                return_value={"device_id": "keep-me"},
            ),
            patch("homeassistant.helpers.storage.Store.async_save") as mock_save,
        ):
            await async_save_config(hass, config)

        mock_save.assert_called_once_with(
            {
                "device_id": "keep-me",
                KEY_ACCESS_TOKEN: "access",
                KEY_REFRESH_TOKEN: "refresh",
                KEY_EXPIRES_AT: 1234567890.0,
                KEY_USER_ID: "user123",
            }
        )

    async def test_save_handles_an_empty_store(self, hass: HomeAssistant) -> None:
        """A first-time install has nothing on disk yet."""
        config = AuthenticationConfig(
            user_id="user123",
            access_token="access",
            expires_at=1.0,
            refresh_token="refresh",
        )

        with (
            patch("homeassistant.helpers.storage.Store.async_load", return_value=None),
            patch("homeassistant.helpers.storage.Store.async_save") as mock_save,
        ):
            await async_save_config(hass, config)

        assert mock_save.call_args.args[0][KEY_USER_ID] == "user123"


class TestGetStoredConfigAccountGuard:
    """The store is shared; the unique_id belongs to the entry.

    They can disagree, and when they do the credentials on disk are for a
    different Sensi account than the one this entry's devices belong to.
    Connecting anyway brings up the other account's thermostats as new devices
    and takes this entry's offline, with nothing in the log to explain it.
    """

    _STORED = {
        KEY_REFRESH_TOKEN: "refresh",
        KEY_ACCESS_TOKEN: "access",
        KEY_EXPIRES_AT: 1.0,
        KEY_USER_ID: "account_a",
    }

    async def test_a_matching_account_loads(self, hass: HomeAssistant) -> None:
        """The ordinary case is unchanged."""
        with patch(
            "homeassistant.helpers.storage.Store.async_load", return_value=self._STORED
        ):
            config = await get_stored_config(hass, "account_a")

        assert config.user_id == "account_a"
        assert config.refresh_token == "refresh"

    async def test_a_mismatched_account_is_refused(self, hass: HomeAssistant) -> None:
        """AuthenticationError, which async_setup_entry turns into reauth."""
        with (
            patch(
                "homeassistant.helpers.storage.Store.async_load",
                return_value=self._STORED,
            ),
            pytest.raises(AuthenticationError) as context,
        ):
            await get_stored_config(hass, "account_b")

        assert "different Sensi account" in str(context.value)

    @pytest.mark.parametrize(
        ("expected_user_id", "stored_user_id"),
        [(None, "account_a"), ("account_a", None), (None, None)],
        ids=["no_unique_id", "no_stored_user_id", "neither"],
    )
    async def test_an_absent_id_cannot_verify_and_does_not_refuse(
        self, hass: HomeAssistant, expected_user_id, stored_user_id
    ) -> None:
        """Absent is "cannot verify", not "does not match".

        Older installations stored no user_id, and an entry created before
        unique_ids were set has none either. Refusing those would lock out a
        working install on an upgrade.
        """
        stored = {**self._STORED, KEY_USER_ID: stored_user_id}

        with patch(
            "homeassistant.helpers.storage.Store.async_load", return_value=stored
        ):
            config = await get_stored_config(hass, expected_user_id)

        assert config.refresh_token == "refresh"

    async def test_a_missing_refresh_token_still_raises(
        self, hass: HomeAssistant
    ) -> None:
        """The pre-existing guard is unaffected by the new one."""
        with (
            patch("homeassistant.helpers.storage.Store.async_load", return_value={}),
            pytest.raises(AuthenticationError),
        ):
            await get_stored_config(hass, "account_a")

    async def test_an_absent_store_raises_rather_than_crashing(
        self, hass: HomeAssistant
    ) -> None:
        """Nothing saved yet: async_load returns None, not an empty dict."""
        with (
            patch("homeassistant.helpers.storage.Store.async_load", return_value=None),
            pytest.raises(AuthenticationError) as context,
        ):
            await get_stored_config(hass, "account_a")

        assert "missing refresh_token" in str(context.value)
