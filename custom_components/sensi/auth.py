"""Sensi Thermostat authentication helpers."""

import asyncio
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Any, Final

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, storage

from .const import LOGGER, STORAGE_KEY, STORAGE_VERSION
from .data import AuthenticationConfig
from .utils import redact_token, to_int

DEFAULT_TIMEOUT = 10

# Defined in CreateRefreshParams.java
OAUTH_URL: Final = "https://oauth.sensiapi.io/token?device={}"
CLIENT_SECRET: Final = "XBF?Z9U6;x3bUwe^FugbL=4ksvGjLnCQ"

# The following constants are mentioned in AuthenticationService.java
CLIENT_ID: Final = "android"
KEY_DEVICE_ID: Final = "device_id"
KEY_ACCESS_TOKEN: Final = "access_token"
KEY_REFRESH_TOKEN: Final = "refresh_token"
KEY_EXPIRES_AT: Final = "expires_at"
KEY_USER_ID: Final = "user_id"

CLIENT_ID2: Final = "fleet"
CLIENT_SECRET2: Final = (
    "JLFjJmketRhj>M9uoDhusYKyi?zUyNqhGB)H2XiwLEF#KcGKrRD2JZsDQ7ufNven"
)
OAUTH_URL2: Final = "https://oauth.sensiapi.io/token"


async def _get_new_tokens(hass: HomeAssistant, refresh_token: str) -> any:
    """Obtain new access_token and refresh_token for the given refresh_token.

    This can raise SensiConnectionError, AuthenticationError.
    """

    result = {}
    LOGGER.debug(
        "Getting access token using refresh_token=%s", redact_token(refresh_token)
    )

    post_data = {
        "client_id": CLIENT_ID2,
        "client_secret": CLIENT_SECRET2,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "accept-language": "en-US,en;q=0.9",
        "accept": "*/*",
    }

    try:
        session = aiohttp_client.async_get_clientsession(hass)
        async with asyncio.timeout(DEFAULT_TIMEOUT):
            response = await session.post(
                OAUTH_URL2,
                data=post_data,
                headers=headers,
                allow_redirects=True,
            )
    except (TimeoutError, aiohttp.ClientError) as err:
        LOGGER.warning("Timed out getting access token", exc_info=True)
        raise SensiConnectionError("Timed out getting access token") from err

    if response.status != HTTPStatus.OK:
        # Only a client error (e.g. 400/401/403 invalid_grant) means the refresh
        # token is genuinely bad and reauth is required. A 5xx or other
        # non-success is a transient backend failure and must be retried rather
        # than escalated to a reauth flow.
        if 400 <= response.status < 500:
            LOGGER.warning("Refresh token rejected (HTTP %s)", response.status)
            raise AuthenticationError("Invalid token")

        LOGGER.warning("Token refresh failed (HTTP %s)", response.status)
        raise SensiConnectionError(
            f"Token refresh failed with status {response.status}"
        )

    response_json = await response.json()
    result[KEY_ACCESS_TOKEN] = response_json.get(KEY_ACCESS_TOKEN)
    result[KEY_REFRESH_TOKEN] = response_json.get(KEY_REFRESH_TOKEN)
    result[KEY_USER_ID] = response_json.get(
        KEY_USER_ID
    )  # This is used as unique_id in config flow

    expires_in = to_int(response_json.get("expires_in"), 0)
    result[KEY_EXPIRES_AT] = (
        datetime.now() + timedelta(seconds=expires_in)
    ).timestamp()

    return result


async def validate_refresh_token(
    hass: HomeAssistant, refresh_token: str
) -> AuthenticationConfig:
    """Exchange a refresh token and return the result without storing it.

    The config flow has to know which Sensi account a token belongs to before
    it can decide whether to accept it, and there is exactly one store for the
    whole integration (STORAGE_KEY is the domain, not the entry id). Validating
    through `refresh_access_token` therefore overwrote the running entry's
    credentials with the candidate's before the flow had looked at them - so a
    reauth rejected as `wrong_account` still left the other account's tokens on
    disk. Call `async_save_config` once the flow has accepted the account.

    This can raise SensiConnectionError, AuthenticationError.
    """

    result = await _get_new_tokens(hass, refresh_token)

    return AuthenticationConfig(
        user_id=result[KEY_USER_ID],
        access_token=result[KEY_ACCESS_TOKEN],
        expires_at=result[KEY_EXPIRES_AT],
        refresh_token=result[KEY_REFRESH_TOKEN],
    )


async def async_save_config(hass: HomeAssistant, config: AuthenticationConfig) -> None:
    """Persist validated credentials to the integration store.

    Sensi rotates the refresh token on every exchange, so what has to be saved
    is the token that came back from validation - the one the user pasted is
    already spent by the time this is called.
    """

    store = storage.Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
    persistent_data = await store.async_load()
    if persistent_data is None:
        persistent_data = {}

    persistent_data[KEY_ACCESS_TOKEN] = config.access_token
    persistent_data[KEY_REFRESH_TOKEN] = config.refresh_token
    persistent_data[KEY_EXPIRES_AT] = config.expires_at
    persistent_data[KEY_USER_ID] = config.user_id

    # Only dict or simple values can be saved into store
    await store.async_save(persistent_data)


async def get_stored_config(
    hass: HomeAssistant, expected_user_id: str | None = None
) -> AuthenticationConfig:
    """Retrieve stored configuration. This will throw AuthenticationError for missing data.

    `expected_user_id` is the entry's unique_id. The store is shared by the
    whole integration while the unique_id belongs to the entry, so the two can
    disagree - and a mismatch means the credentials on disk are for a different
    Sensi account than the one this entry's devices belong to. Connecting
    anyway would bring up the other account's thermostats as new devices and
    take this entry's offline, with nothing in the log to explain it. Raising
    AuthenticationError sends the user to reauth instead, which is the only
    thing that can actually fix it.
    """

    store = storage.Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
    persistent_data = await store.async_load()
    if persistent_data is None:
        persistent_data = {}

    refresh_token = persistent_data.get(KEY_REFRESH_TOKEN)
    if refresh_token is None:
        raise AuthenticationError("Stored config is missing refresh_token")

    stored_user_id = persistent_data.get(KEY_USER_ID)
    # Both have to be present to compare: older installations stored no
    # user_id, and an entry created before unique_ids were set has none
    # either. An absent value is "cannot verify", not "does not match".
    if expected_user_id and stored_user_id and stored_user_id != expected_user_id:
        LOGGER.warning(
            "Stored credentials belong to a different Sensi account than this "
            "entry; reauthentication is required"
        )
        raise AuthenticationError(
            "Stored credentials are for a different Sensi account"
        )

    return AuthenticationConfig(
        user_id=stored_user_id,
        access_token=persistent_data.get(KEY_ACCESS_TOKEN),
        expires_at=persistent_data.get(KEY_EXPIRES_AT),
        refresh_token=refresh_token,
    )


async def refresh_access_token(
    hass: HomeAssistant, refresh_token: str | None = None
) -> AuthenticationConfig:
    """Obtain new access_token and refresh_token for the given/stored refresh_token.

    This is the runtime path: the client calls it to rotate the token of an
    entry that is already set up, so persisting the result is the point. The
    config flow must not use it - see `validate_refresh_token`.

    This can raise SensiConnectionError, AuthenticationError.
    """

    store = storage.Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
    persistent_data = await store.async_load()
    if persistent_data is None:
        persistent_data = {}

    # Data can be missing in older installations, use get()
    if refresh_token is None:
        refresh_token = persistent_data.get(KEY_REFRESH_TOKEN)
        LOGGER.debug("Using stored refresh_token %s", redact_token(refresh_token))
    else:
        LOGGER.debug("Using supplied refresh_token %s", redact_token(refresh_token))

    if refresh_token is None:
        raise AuthenticationError("Stored config is missing refresh_token")

    result = await _get_new_tokens(hass, refresh_token)

    config = AuthenticationConfig(
        user_id=result[KEY_USER_ID],
        access_token=result[KEY_ACCESS_TOKEN],
        expires_at=result[KEY_EXPIRES_AT],
        refresh_token=result[KEY_REFRESH_TOKEN],
    )
    # One writer, so this path and the config flow's cannot drift apart.
    await async_save_config(hass, config)

    return config


# async def login(
#     hass: HomeAssistant, config: AuthenticationConfig, new_token: bool = False
# ):
#     """Login."""

#     store = storage.Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
#     persistent_data = await store.async_load() or {}
#     device_id = persistent_data.get(KEY_DEVICE_ID)

#     if not new_token:
#         access_token = persistent_data.get(KEY_ACCESS_TOKEN)
#         refresh_token = persistent_data.get(KEY_REFRESH_TOKEN)
#         expires_at = persistent_data.get(KEY_EXPIRES_AT)

#         if device_id and access_token and expires_at:
#             config.access_token = access_token
#             config.expires_at = expires_at

#             LOGGER.debug("Using saved authentication")
#             return

#     if not device_id:
#         device_id = uuid.uuid4()
#         persistent_data[KEY_DEVICE_ID] = device_id

#     post_data = {
#         "username": config.username,
#         "password": config.password,
#         "client_id": CLIENT_ID,
#         "client_secret": CLIENT_SECRET,
#         "grant_type": "password",
#     }

#     headers = {
#         "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
#         "x-platform": "android",
#         "accept": "*/*",
#     }

#     try:
#         session = aiohttp_client.async_get_clientsession(hass)
#         async with asyncio.timeout(DEFAULT_TIMEOUT):
#             response = await session.post(
#                 OAUTH_URL.format(device_id),
#                 data=post_data,
#                 headers=headers,
#                 allow_redirects=True,
#             )
#     except (asyncio.TimeoutError, aiohttp.ClientError) as err:
#         LOGGER.warning("Timed out getting access token", exc_info=True)
#         raise SensiConnectionError from err

#     persistent_data["device_id"] = device_id

#     # Uncomment this to test async_step_reauth
#     # raise AuthenticationError("Invalid login credentials")
#     if response.status != HTTPStatus.OK:
#         await store.async_save(persistent_data)
#         raise AuthenticationError("Invalid login credentials")

#     response_json = await response.json()
#     access_token = response_json.get(KEY_ACCESS_TOKEN)
#     refresh_token = response_json.get(KEY_REFRESH_TOKEN)
#     expires_in = int(response_json.get("expires_in"))
#     expires_at = (datetime.now() + timedelta(seconds=expires_in)).timestamp()

#     config.access_token = access_token
#     config.expires_at = expires_at

#     persistent_data[KEY_ACCESS_TOKEN] = access_token
#     persistent_data[KEY_REFRESH_TOKEN] = refresh_token
#     persistent_data[KEY_EXPIRES_AT] = expires_at

#     await store.async_save(persistent_data)
#     return


class AuthenticationError(Exception):
    """API exception occurred when fail to authenticate."""

    def __init__(self, message: str) -> None:
        """Create instance of AuthenticationError."""
        self.message = message
        super().__init__(self.message)


class SensiConnectionError(Exception):
    """API exception occurred when fail to connect."""

    def __init__(self, message: str) -> None:
        """Create instance of SensiConnectionError."""
        self.message = message
        super().__init__(self.message)
