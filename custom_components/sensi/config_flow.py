"""Config flow for Sensi thermostat."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .auth import (
    AuthenticationConfig,
    AuthenticationError,
    SensiConnectionError,
    async_save_config,
    is_user_id,
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


# hass.data key: ids of the user flows that have reached the credential save.
_SAVE_CLAIMS = f"{SENSI_DOMAIN}_save_claims"


class SensiFlowHandler(config_entries.ConfigFlow, domain=SENSI_DOMAIN):
    """Config flow for Sensi thermostat."""

    VERSION = 1
    # 2: entry.data holds no credentials; the domain-keyed store is the only
    # copy. async_migrate_entry strips what older versions left there.
    MINOR_VERSION = 2

    @callback
    def async_remove(self) -> None:
        """Release this flow's claim on the credential save, if it made one."""
        self.hass.data.get(_SAVE_CLAIMS, set()).discard(self.flow_id)

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
                # A different account may have taken the only allowed entry
                # while the token was being validated. Home Assistant aborts
                # this flow when that happens, but only outside this step, so
                # the shared store would still get this account's tokens.
                # Home Assistant adds the entry only after awaits of its own
                # once this step returns, so a flow whose validation ends in
                # that gap sees no entry yet. The first flow to reach the save
                # claims it, with no await between the check and the claim,
                # and holds the claim until the flow is removed; by then its
                # entry exists or it never will.
                claims: set[str] = self.hass.data.setdefault(_SAVE_CLAIMS, set())
                if self._async_current_entries() or claims - {self.flow_id}:
                    # The integration's own string, not Home Assistant's
                    # through translation_domain: async_abort takes that
                    # keyword only from 2026.9.0, and hacs.json promises
                    # 2026.3.0, where passing it raises TypeError (#449).
                    return self.async_abort(reason="single_instance_allowed")
                claims.add(self.flow_id)
                try:
                    await async_save_config(self.hass, result.config)
                except BaseException:
                    # Nothing was accepted, so another flow may try again.
                    claims.discard(self.flow_id)
                    raise
                return self.async_create_entry(
                    title=SENSI_NAME,
                    # The token lives in the store async_save_config just
                    # wrote, and nothing reads entry.data. A copy here went
                    # stale on the first rotation and stayed on disk.
                    data={},
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
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Handle reauthentication."""
        errors: dict[str, str] = {}
        # By entry_id, not by unique_id: async_set_unique_id(None) returns
        # None for an entry that has no unique_id, which aborted reauth for
        # every entry upstream v1.3.1 to v1.4.1 created.
        existing_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if existing_entry is None:
            # The entry was removed while the reauth flow was open.
            return self.async_abort(reason="entry_not_found")

        if user_input is not None:
            config = AuthenticationConfig(
                refresh_token=user_input[CONFIG_REFRESH_TOKEN],
            )
            result = await self._try_login(config)
            if not result.errors:
                # An entry keyed by a user_id has an account to hold the token
                # to. One upstream keyed by the login, or not at all, has no
                # user_id to compare against (see is_user_id): the confirmed
                # account becomes its key instead, below.
                if (
                    is_user_id(existing_entry.unique_id)
                    and result.config.user_id != existing_entry.unique_id
                ):
                    # The token is valid but belongs to a different Sensi
                    # account. Accepting it would silently repoint this entry.
                    errors = {"base": "wrong_account"}
                else:
                    # Only now, with the account confirmed to be this entry's,
                    # do the new credentials reach disk.
                    await async_save_config(self.hass, result.config)
                    self.hass.config_entries.async_update_entry(
                        existing_entry,
                        # Unchanged for an entry already keyed by this user_id;
                        # for the other two generations this is what makes
                        # the account guard apply from now on. If the token
                        # endpoint sent no user_id there is nothing to adopt.
                        unique_id=result.config.user_id or existing_entry.unique_id,
                        # Emptied rather than carried forward: entries from
                        # upstream v1.0.0 to v1.2.x held the login and the
                        # plaintext password here, and the store above is
                        # the only copy of the credential anything reads.
                        data={},
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
