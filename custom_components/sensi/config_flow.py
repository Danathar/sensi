"""Config flow for Sensi thermostat."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .auth import (
    AuthenticationConfig,
    AuthenticationError,
    SensiConnectionError,
    async_save_config,
    validate_refresh_token,
)
from .const import (
    CONFIG_REFRESH_TOKEN,
    LOGGER,
    SENSI_DOMAIN,
    SENSI_LOGIN_URL,
    SENSI_NAME,
)

AUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONFIG_REFRESH_TOKEN): str,
    }
)


class SensiFlowHandler(config_entries.ConfigFlow, domain=SENSI_DOMAIN):
    """Config flow for Sensi thermostat."""

    VERSION = 1

    def __init__(self) -> None:
        """Start a config flow."""
        self._reauth_unique_id = None

    async def _try_login(self, config: AuthenticationConfig) -> LoginResponse:
        """Check the credentials, without committing them.

        validate_refresh_token rather than refresh_access_token: the latter
        writes to the integration's single store, which would overwrite a
        running entry's credentials with the candidate's before this flow has
        decided whether to accept them. The caller saves on acceptance.
        """
        try:
            new_config = await validate_refresh_token(self.hass, config.refresh_token)
        except SensiConnectionError:
            return LoginResponse(errors={"base": "cannot_connect"}, config=None)
        except AuthenticationError:
            return LoginResponse(errors={"base": "invalid_auth"}, config=None)
        except Exception as err:  # pylint: disable=broad-except # noqa: BLE001
            LOGGER.exception(str(err))
            return LoginResponse(errors={"base": "unknown"}, config=None)

        return LoginResponse(errors=None, config=new_config)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initialized by the user."""

        errors: dict[str, str] = {}
        if user_input is not None:
            config = AuthenticationConfig(
                refresh_token=user_input[CONFIG_REFRESH_TOKEN],
            )
            result = await self._try_login(config)
            if not result.errors:
                # Use the user_id obtained via login as the  unique_id
                await self.async_set_unique_id(result.config.user_id)
                # Before the save: an abort here means this account is already
                # set up, and its stored credentials must be left alone.
                self._abort_if_unique_id_configured()

                await async_save_config(self.hass, result.config)
                return self.async_create_entry(
                    title=SENSI_NAME,
                    # The rotated token, not the one the user pasted - Sensi
                    # rotates on every exchange, so what they typed is already
                    # spent by the time validation returns.
                    data={CONFIG_REFRESH_TOKEN: result.config.refresh_token},
                )

            errors = result.errors

        return self.async_show_form(
            step_id="user",
            data_schema=AUTH_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"sensi_url": SENSI_LOGIN_URL},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        # pylint: disable=unused-argument
        """Handle reauthentication."""
        self._reauth_unique_id = self.context["unique_id"]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Handle reauthentication."""
        errors: dict[str, str] = {}
        existing_entry = await self.async_set_unique_id(self._reauth_unique_id)
        if existing_entry is None:
            # The entry was removed while the reauth flow was open.
            return self.async_abort(reason="entry_not_found")

        if user_input is not None:
            config = AuthenticationConfig(
                refresh_token=user_input[CONFIG_REFRESH_TOKEN],
            )
            result = await self._try_login(config)
            if not result.errors:
                if result.config.user_id != self._reauth_unique_id:
                    # The token is valid but belongs to a different Sensi
                    # account. Accepting it would silently repoint this entry.
                    errors = {"base": "wrong_account"}
                else:
                    # Only now, with the account confirmed to be this entry's,
                    # do the new credentials reach disk.
                    await async_save_config(self.hass, result.config)
                    self.hass.config_entries.async_update_entry(
                        existing_entry,
                        data={
                            **existing_entry.data,
                            CONFIG_REFRESH_TOKEN: result.config.refresh_token,
                        },
                    )
                    await self.hass.config_entries.async_reload(existing_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")
            else:
                errors = result.errors

        # The schema is the same as the user step, but the step_id is not:
        # Home Assistant routes a submitted form to async_step_<step_id>, so
        # "user" would hand the reply to async_step_user, which aborts with
        # already_configured because this unique_id is already set up.
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=AUTH_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"sensi_url": SENSI_LOGIN_URL},
        )


@dataclass
class LoginResponse:
    """Response from login attempt."""

    errors: dict[str, str] | None
    config: AuthenticationConfig
