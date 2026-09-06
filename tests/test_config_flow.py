"""Tests for Sensi config flow."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.sensi.auth import (
    AuthenticationConfig,
    AuthenticationError,
    SensiConnectionError,
)
from custom_components.sensi.config_flow import (
    AUTH_DATA_SCHEMA,
    LoginResponse,
    SensiFlowHandler,
)
from custom_components.sensi.const import CONFIG_REFRESH_TOKEN, SENSI_DOMAIN, SENSI_NAME
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

STRINGS_FILES = (
    Path(__file__).parents[1] / "custom_components" / "sensi" / "strings.json",
    Path(__file__).parents[1]
    / "custom_components"
    / "sensi"
    / "translations"
    / "en.json",
)


class TestLoginResponse:
    """Test cases for LoginResponse dataclass."""

    def test_login_response_success(self):
        """Test LoginResponse with successful login."""
        config = AuthenticationConfig(
            refresh_token="test_token",
            access_token="access_token",
            expires_at=12345,
            user_id="user123",
        )
        response = LoginResponse(errors=None, config=config)

        assert response.errors is None
        assert response.config == config

    def test_login_response_error(self):
        """Test LoginResponse with error."""
        response = LoginResponse(
            errors={"base": "invalid_auth"},
            config=None,
        )

        assert response.errors == {"base": "invalid_auth"}
        assert response.config is None

    def test_login_response_multiple_errors(self):
        """Test LoginResponse with multiple errors."""
        response = LoginResponse(
            errors={"base": "invalid_auth", "refresh_token": "Required"},
            config=None,
        )

        assert "base" in response.errors
        assert "refresh_token" in response.errors


class TestAuthDataSchema:
    """Test cases for AUTH_DATA_SCHEMA."""

    def test_auth_schema_valid_input(self):
        """Test AUTH_DATA_SCHEMA with valid input."""
        valid_data = {CONFIG_REFRESH_TOKEN: "test_token"}
        result = AUTH_DATA_SCHEMA(valid_data)

        assert result == valid_data

    def test_auth_schema_missing_refresh_token(self):
        """Test AUTH_DATA_SCHEMA with missing refresh token."""
        with pytest.raises(vol.MultipleInvalid):
            AUTH_DATA_SCHEMA({})

    def test_auth_schema_extra_fields_ignored(self):
        """Test AUTH_DATA_SCHEMA rejects extra fields."""
        data = {
            CONFIG_REFRESH_TOKEN: "test_token",
            "extra_field": "extra_value",
        }
        # Schema should reject extra fields by default
        with pytest.raises(vol.MultipleInvalid):
            AUTH_DATA_SCHEMA(data)


class TestSensiFlowHandler:
    """Test cases for SensiFlowHandler config flow."""

    def test_flow_handler_version(self):
        """Test SensiFlowHandler has correct version."""
        assert SensiFlowHandler.VERSION == 1

    @pytest.mark.asyncio
    async def test_try_login_success(self, hass: HomeAssistant):
        """Test _try_login with successful authentication."""
        handler = SensiFlowHandler()
        handler.hass = hass

        config = AuthenticationConfig(refresh_token="test_token")
        new_config = AuthenticationConfig(
            refresh_token="test_token",
            access_token="new_access_token",
            expires_at=12345,
            user_id="user123",
        )

        with patch(
            "custom_components.sensi.config_flow.validate_refresh_token"
        ) as mock_refresh:
            mock_refresh.return_value = new_config
            result = await handler._try_login(config)  # noqa: SLF001

        assert result.errors is None
        assert result.config == new_config

    @pytest.mark.asyncio
    async def test_try_login_connection_error(self, hass: HomeAssistant):
        """Test _try_login with connection error."""
        handler = SensiFlowHandler()
        handler.hass = hass

        config = AuthenticationConfig(refresh_token="test_token")

        with patch(
            "custom_components.sensi.config_flow.validate_refresh_token"
        ) as mock_refresh:
            mock_refresh.side_effect = SensiConnectionError("Connection failed")
            result = await handler._try_login(config)  # noqa: SLF001

        assert result.errors == {"base": "cannot_connect"}
        assert result.config is None

    @pytest.mark.asyncio
    async def test_try_login_authentication_error(self, hass: HomeAssistant):
        """Test _try_login with authentication error."""
        handler = SensiFlowHandler()
        handler.hass = hass

        config = AuthenticationConfig(refresh_token="invalid_token")

        with patch(
            "custom_components.sensi.config_flow.validate_refresh_token"
        ) as mock_refresh:
            mock_refresh.side_effect = AuthenticationError("Invalid credentials")
            result = await handler._try_login(config)  # noqa: SLF001

        assert result.errors == {"base": "invalid_auth"}
        assert result.config is None

    @pytest.mark.asyncio
    async def test_try_login_generic_exception(self, hass: HomeAssistant):
        """Test _try_login with generic exception."""
        handler = SensiFlowHandler()
        handler.hass = hass

        config = AuthenticationConfig(refresh_token="test_token")

        with patch(
            "custom_components.sensi.config_flow.validate_refresh_token"
        ) as mock_refresh:
            mock_refresh.side_effect = ValueError("Unexpected error")
            result = await handler._try_login(config)  # noqa: SLF001

        assert result.errors == {"base": "unknown"}
        assert result.config is None

    @pytest.mark.asyncio
    async def test_async_step_user_no_input(self, hass: HomeAssistant):
        """Test async_step_user with no user input."""
        handler = SensiFlowHandler()
        handler.hass = hass

        result = await handler.async_step_user(None)

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert result["data_schema"] == AUTH_DATA_SCHEMA

    @pytest.mark.asyncio
    async def test_async_step_user_successful_login(self, hass: HomeAssistant):
        """Test async_step_user with successful login."""
        handler = SensiFlowHandler()
        handler.hass = hass

        user_input = {CONFIG_REFRESH_TOKEN: "test_token"}
        new_config = AuthenticationConfig(
            refresh_token="test_token",
            access_token="access_token",
            expires_at=12345,
            user_id="user123",
        )

        with (
            patch.object(handler, "_try_login") as mock_login,
            patch.object(handler, "async_set_unique_id") as mock_unique_id,
            patch.object(handler, "_abort_if_unique_id_configured") as mock_abort,
            patch.object(handler, "async_create_entry") as mock_create,
        ):
            mock_login.return_value = LoginResponse(errors=None, config=new_config)
            mock_unique_id.return_value = None
            mock_abort.return_value = None
            mock_create.return_value = {"type": "create_entry"}

            await handler.async_step_user(user_input)

            mock_login.assert_called_once()
            mock_unique_id.assert_called_once_with("user123")
            mock_abort.assert_called_once()
            mock_create.assert_called_once_with(title=SENSI_NAME, data=user_input)

    @pytest.mark.asyncio
    async def test_async_step_user_login_failure(self, hass: HomeAssistant):
        """Test async_step_user with login failure."""
        handler = SensiFlowHandler()
        handler.hass = hass

        user_input = {CONFIG_REFRESH_TOKEN: "invalid_token"}

        with (
            patch.object(handler, "_try_login") as mock_login,
            patch.object(handler, "async_show_form") as mock_form,
        ):
            mock_login.return_value = LoginResponse(
                errors={"base": "invalid_auth"}, config=None
            )
            mock_form.return_value = {
                "type": "form",
                "errors": {"base": "invalid_auth"},
            }

            await handler.async_step_user(user_input)

            mock_login.assert_called_once()
            mock_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_step_reauth(self, hass: HomeAssistant):
        """Test async_step_reauth."""
        handler = SensiFlowHandler()
        handler.hass = hass
        handler.context = {"unique_id": "user123"}

        with patch.object(handler, "async_step_reauth_confirm") as mock_reauth_confirm:
            mock_reauth_confirm.return_value = {
                "type": "form",
                "step_id": "reauth_confirm",
            }

            await handler.async_step_reauth({"refresh_token": "token"})

            assert handler._reauth_unique_id == "user123"  # noqa: SLF001
            mock_reauth_confirm.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_step_reauth_confirm_no_input(self, hass: HomeAssistant):
        """Test async_step_reauth_confirm with no user input."""
        handler = SensiFlowHandler()
        handler.hass = hass

        handler.context = {"unique_id": "user123"}

        with patch.object(handler, "async_step_reauth_confirm"):
            await handler.async_step_reauth({})

        mock_entry = MockConfigEntry(
            domain=SENSI_DOMAIN,
            data={CONFIG_REFRESH_TOKEN: "old_token"},
            entry_id="test_entry",
            unique_id="user123",
        )

        with (
            patch.object(handler, "async_set_unique_id") as mock_unique_id,
            patch.object(handler, "async_show_form") as mock_form,
        ):
            mock_unique_id.return_value = mock_entry
            mock_form.return_value = {"type": "form", "step_id": "reauth_confirm"}

            await handler.async_step_reauth_confirm(None)

            mock_unique_id.assert_called_once_with("user123")
            mock_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_step_reauth_confirm_successful(self, hass: HomeAssistant):
        """Test async_step_reauth_confirm with successful reauthentication."""
        handler = SensiFlowHandler()
        handler.hass = hass

        handler.context = {"unique_id": "user123"}

        with patch.object(handler, "async_step_reauth_confirm"):
            await handler.async_step_reauth({})

        user_input = {CONFIG_REFRESH_TOKEN: "new_token"}
        new_config = AuthenticationConfig(
            refresh_token="new_token",
            access_token="new_access_token",
            expires_at=12345,
            user_id="user123",
        )

        mock_entry = MockConfigEntry(
            domain=SENSI_DOMAIN,
            data={CONFIG_REFRESH_TOKEN: "old_token"},
            entry_id="test_entry",
            unique_id="user123",
        )

        with (
            patch.object(handler, "async_set_unique_id") as mock_unique_id,
            patch.object(handler, "_try_login") as mock_login,
            patch.object(handler, "async_abort") as mock_abort,
        ):
            mock_unique_id.return_value = mock_entry
            mock_login.return_value = LoginResponse(errors=None, config=new_config)
            mock_abort.return_value = {"type": "abort", "reason": "reauth_successful"}

            # Mock the config_entries update and reload
            hass.config_entries.async_update_entry = Mock()
            hass.config_entries.async_reload = AsyncMock()

            await handler.async_step_reauth_confirm(user_input)

            mock_login.assert_called_once()
            hass.config_entries.async_update_entry.assert_called_once()
            hass.config_entries.async_reload.assert_called_once()
            mock_abort.assert_called_once_with(reason="reauth_successful")

    @pytest.mark.asyncio
    async def test_async_step_reauth_confirm_wrong_account(self, hass: HomeAssistant):
        """A token for a different account must not repoint the entry."""
        handler = SensiFlowHandler()
        handler.hass = hass
        handler.context = {"unique_id": "user123"}

        with patch.object(handler, "async_step_reauth_confirm"):
            await handler.async_step_reauth({})

        user_input = {CONFIG_REFRESH_TOKEN: "token_for_someone_else"}
        other_config = AuthenticationConfig(
            refresh_token="token_for_someone_else",
            access_token="access_token",
            expires_at=12345,
            user_id="someone_else",
        )

        mock_entry = MockConfigEntry(
            domain=SENSI_DOMAIN,
            data={CONFIG_REFRESH_TOKEN: "old_token"},
            entry_id="test_entry",
            unique_id="user123",
        )

        with (
            patch.object(handler, "async_set_unique_id") as mock_unique_id,
            patch.object(handler, "_try_login") as mock_login,
            patch.object(handler, "async_show_form") as mock_form,
        ):
            mock_unique_id.return_value = mock_entry
            mock_login.return_value = LoginResponse(errors=None, config=other_config)
            mock_form.return_value = {"type": "form", "step_id": "reauth_confirm"}

            hass.config_entries.async_update_entry = Mock()
            hass.config_entries.async_reload = AsyncMock()

            await handler.async_step_reauth_confirm(user_input)

            hass.config_entries.async_update_entry.assert_not_called()
            hass.config_entries.async_reload.assert_not_called()
            mock_form.assert_called_once()
            assert mock_form.call_args.kwargs["errors"] == {"base": "wrong_account"}

    @pytest.mark.asyncio
    async def test_async_step_reauth_confirm_missing_entry(self, hass: HomeAssistant):
        """An entry removed while the flow was open aborts cleanly."""
        handler = SensiFlowHandler()
        handler.hass = hass
        handler.context = {"unique_id": "user123"}

        with patch.object(handler, "async_step_reauth_confirm"):
            await handler.async_step_reauth({})

        with (
            patch.object(handler, "async_set_unique_id") as mock_unique_id,
            patch.object(handler, "async_abort") as mock_abort,
        ):
            mock_unique_id.return_value = None
            mock_abort.return_value = {"type": "abort", "reason": "entry_not_found"}

            await handler.async_step_reauth_confirm({CONFIG_REFRESH_TOKEN: "new_token"})

            mock_abort.assert_called_once_with(reason="entry_not_found")

    @pytest.mark.asyncio
    async def test_async_step_reauth_confirm_login_failure(self, hass: HomeAssistant):
        """Test async_step_reauth_confirm with login failure."""
        handler = SensiFlowHandler()
        handler.hass = hass
        handler.context = {"unique_id": "user123"}

        with patch.object(handler, "async_step_reauth_confirm"):
            await handler.async_step_reauth({})

        user_input = {CONFIG_REFRESH_TOKEN: "invalid_token"}

        mock_entry = MockConfigEntry(
            domain=SENSI_DOMAIN,
            data={CONFIG_REFRESH_TOKEN: "old_token"},
            entry_id="test_entry",
            unique_id="user123",
        )

        with (
            patch.object(handler, "async_set_unique_id") as mock_unique_id,
            patch.object(handler, "_try_login") as mock_login,
            patch.object(handler, "async_show_form") as mock_form,
        ):
            mock_unique_id.return_value = mock_entry
            mock_login.return_value = LoginResponse(
                errors={"base": "invalid_auth"}, config=None
            )
            mock_form.return_value = {"type": "form", "step_id": "reauth_confirm"}

            await handler.async_step_reauth_confirm(user_input)

            mock_login.assert_called_once()
            mock_form.assert_called_once()


@pytest.mark.usefixtures("enable_custom_integrations")
class TestReauthFlowRouting:
    """Reauth driven through Home Assistant's flow manager.

    The tests above call the step methods directly, which skips the part that
    decides where a submitted form goes. Home Assistant routes a submission to
    `async_step_<step_id>`, so a reauth form shown with `step_id="user"` hands
    the reply to `async_step_user` and aborts with `already_configured` - the
    whole of `async_step_reauth_confirm`'s `user_input` branch never runs.
    These tests go through `hass.config_entries.flow`, which is the only way
    that routing gets exercised.
    """

    @pytest.fixture
    def entry(self, hass: HomeAssistant) -> MockConfigEntry:
        """Return an already configured entry whose token has gone stale."""
        entry = MockConfigEntry(
            domain=SENSI_DOMAIN,
            data={CONFIG_REFRESH_TOKEN: "old_token"},
            unique_id="user123",
            title=SENSI_NAME,
        )
        entry.add_to_hass(hass)
        return entry

    async def test_reauth_form_is_shown_for_its_own_step(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ):
        """The form has to come back to reauth_confirm, not to user."""
        result = await entry.start_reauth_flow(hass)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

    async def test_reauth_stores_the_new_token(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ):
        """Submitting a valid token for the same account updates the entry."""
        result = await entry.start_reauth_flow(hass)

        with (
            patch(
                "custom_components.sensi.config_flow.validate_refresh_token",
                return_value=AuthenticationConfig(
                    refresh_token="new_token", user_id="user123"
                ),
            ),
            patch("custom_components.sensi.async_setup_entry", return_value=True),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONFIG_REFRESH_TOKEN: "new_token"}
            )
            await hass.async_block_till_done()

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "reauth_successful"
        assert entry.data[CONFIG_REFRESH_TOKEN] == "new_token"
        assert len(hass.config_entries.async_entries(SENSI_DOMAIN)) == 1

    async def test_reauth_rejects_a_token_for_another_account(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ):
        """A valid token for a different Sensi account must not be accepted."""
        result = await entry.start_reauth_flow(hass)

        with patch(
            "custom_components.sensi.config_flow.validate_refresh_token",
            return_value=AuthenticationConfig(
                refresh_token="someone_elses_token", user_id="someone_else"
            ),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONFIG_REFRESH_TOKEN: "someone_elses_token"}
            )

        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "reauth_confirm"
        assert result2["errors"] == {"base": "wrong_account"}
        assert entry.data[CONFIG_REFRESH_TOKEN] == "old_token"

    async def test_reauth_can_be_retried_after_a_bad_token(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ):
        """The re-shown form still routes back here, so a retry can succeed."""
        result = await entry.start_reauth_flow(hass)

        with patch(
            "custom_components.sensi.config_flow.validate_refresh_token",
            side_effect=AuthenticationError("expired"),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONFIG_REFRESH_TOKEN: "still_bad"}
            )

        assert result2["type"] == FlowResultType.FORM
        assert result2["errors"] == {"base": "invalid_auth"}

        with (
            patch(
                "custom_components.sensi.config_flow.validate_refresh_token",
                return_value=AuthenticationConfig(
                    refresh_token="new_token", user_id="user123"
                ),
            ),
            patch("custom_components.sensi.async_setup_entry", return_value=True),
        ):
            result3 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONFIG_REFRESH_TOKEN: "new_token"}
            )
            await hass.async_block_till_done()

        assert result3["type"] == FlowResultType.ABORT
        assert result3["reason"] == "reauth_successful"
        assert entry.data[CONFIG_REFRESH_TOKEN] == "new_token"


@pytest.mark.usefixtures("enable_custom_integrations")
class TestCredentialsReachDiskOnlyOnAcceptance:
    """Validating a token must not commit it.

    STORAGE_KEY is the domain, so there is exactly one credential store for
    the whole integration, and `get_stored_config` is what `async_setup_entry`
    reads. Validating through `refresh_access_token` wrote the candidate's
    tokens there before the flow had looked at the account, so a reauth the
    flow *rejected* as wrong_account still left the other account's
    credentials on disk. The running client keeps working from memory until
    the next restart, and then the entry - still carrying account A's
    unique_id and A's devices - connects as B.

    These go through `hass.config_entries.flow` rather than calling the step,
    because the whole point is what the flow does around the validation call.
    """

    _VALIDATED_A = AuthenticationConfig(
        refresh_token="a_rotated",
        access_token="a_access",
        expires_at=123.0,
        user_id="user123",
    )
    _VALIDATED_B = AuthenticationConfig(
        refresh_token="b_rotated",
        access_token="b_access",
        expires_at=456.0,
        user_id="someone_else",
    )

    @pytest.fixture
    def entry(self, hass: HomeAssistant) -> MockConfigEntry:
        """Return an entry for account user123 whose token has gone stale."""
        entry = MockConfigEntry(
            domain=SENSI_DOMAIN,
            data={CONFIG_REFRESH_TOKEN: "old_token"},
            unique_id="user123",
            title=SENSI_NAME,
        )
        entry.add_to_hass(hass)
        return entry

    async def test_a_wrong_account_reauth_leaves_the_store_untouched(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ):
        """The bug, as an assertion: rejected means nothing was written."""
        result = await entry.start_reauth_flow(hass)

        with (
            patch(
                "custom_components.sensi.config_flow.validate_refresh_token",
                return_value=self._VALIDATED_B,
            ),
            patch("custom_components.sensi.auth.async_save_config") as mock_save_auth,
            patch(
                "custom_components.sensi.config_flow.async_save_config"
            ) as mock_save_flow,
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONFIG_REFRESH_TOKEN: "token_for_someone_else"}
            )

        assert result2["errors"] == {"base": "wrong_account"}
        mock_save_flow.assert_not_called()
        mock_save_auth.assert_not_called()
        # The entry keeps the token it had, too.
        assert entry.data[CONFIG_REFRESH_TOKEN] == "old_token"

    async def test_an_accepted_reauth_saves_the_rotated_token(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ):
        """Accepting is what commits, and it commits what came back.

        Sensi rotates the refresh token on every exchange, so the token the
        user pasted is spent by the time validation returns. Writing that one
        into entry.data - which is what happened before - stored a credential
        guaranteed to fail on first use.
        """
        result = await entry.start_reauth_flow(hass)

        with (
            patch(
                "custom_components.sensi.config_flow.validate_refresh_token",
                return_value=self._VALIDATED_A,
            ),
            patch("custom_components.sensi.config_flow.async_save_config") as mock_save,
            patch("custom_components.sensi.async_setup_entry", return_value=True),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONFIG_REFRESH_TOKEN: "typed_by_the_user"}
            )
            await hass.async_block_till_done()

        assert result2["type"] == FlowResultType.ABORT
        assert result2["reason"] == "reauth_successful"
        mock_save.assert_called_once_with(hass, self._VALIDATED_A)
        assert entry.data[CONFIG_REFRESH_TOKEN] == "a_rotated"

    async def test_a_rejected_token_saves_nothing(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ):
        """A token the backend refuses never gets as far as the store."""
        result = await entry.start_reauth_flow(hass)

        with (
            patch(
                "custom_components.sensi.config_flow.validate_refresh_token",
                side_effect=AuthenticationError("expired"),
            ),
            patch("custom_components.sensi.config_flow.async_save_config") as mock_save,
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONFIG_REFRESH_TOKEN: "still_bad"}
            )

        assert result2["errors"] == {"base": "invalid_auth"}
        mock_save.assert_not_called()

    async def test_initial_setup_saves_the_rotated_token(self, hass: HomeAssistant):
        """The user step commits too - nothing else writes the store."""
        result = await hass.config_entries.flow.async_init(
            SENSI_DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        with (
            patch(
                "custom_components.sensi.config_flow.validate_refresh_token",
                return_value=self._VALIDATED_A,
            ),
            patch("custom_components.sensi.config_flow.async_save_config") as mock_save,
            patch("custom_components.sensi.async_setup_entry", return_value=True),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONFIG_REFRESH_TOKEN: "typed_by_the_user"}
            )
            await hass.async_block_till_done()

        assert result2["type"] == FlowResultType.CREATE_ENTRY
        mock_save.assert_called_once_with(hass, self._VALIDATED_A)
        assert result2["data"] == {CONFIG_REFRESH_TOKEN: "a_rotated"}

    async def test_a_second_setup_never_gets_as_far_as_validating(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ):
        """manifest.json sets single_config_entry, so the form never opens.

        This is why the store cannot be rewritten by re-adding an account that
        is already set up: the flow aborts before any token is exchanged. The
        save in async_step_user is still placed after
        `_abort_if_unique_id_configured` rather than before it, so the
        ordering does not depend on that manifest flag staying set - but this
        is the route the flag closes, and it is worth pinning that it does.
        """
        with (
            patch(
                "custom_components.sensi.config_flow.validate_refresh_token"
            ) as mock_validate,
            patch("custom_components.sensi.config_flow.async_save_config") as mock_save,
        ):
            result = await hass.config_entries.flow.async_init(
                SENSI_DOMAIN, context={"source": config_entries.SOURCE_USER}
            )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "single_instance_allowed"
        mock_validate.assert_not_called()
        mock_save.assert_not_called()


@pytest.mark.parametrize("path", STRINGS_FILES, ids=lambda p: p.name)
def test_every_flow_step_has_strings(path: Path):
    """Check that every step the config flow shows has strings to render.

    Home Assistant builds the form from strings.json, and hassfest validates
    that file, so a new step_id needs an entry to go with it.
    """
    config = json.loads(path.read_text(encoding="utf-8"))["config"]

    assert set(config["step"]) == {"user", "reauth_confirm"}
    for step in config["step"].values():
        assert CONFIG_REFRESH_TOKEN in step["data"]
        assert step["description"]
    assert "reauth_successful" in config["abort"]
