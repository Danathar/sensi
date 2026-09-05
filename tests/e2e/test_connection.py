"""End-to-end coverage of the connection lifecycle.

Reconnect and mid-session token refresh are what a unit test is worst at and an
end-to-end test is best at: the behaviour is entirely about ordering between a
server that drops the connection, a handler that records why, and a retry that
has to decide whether the token or the network was at fault.

Everything here drives ``SensiClient`` through the same scripted backend the
rest of ``tests/e2e/`` uses, rather than reaching into its private state.
"""

from unittest.mock import AsyncMock, patch

import pytest
from socketio.exceptions import ConnectionError as SocketIOConnectionError

from custom_components.sensi.auth import SensiConnectionError
from custom_components.sensi.client import SensiClient
from custom_components.sensi.data import AuthenticationConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .conftest import NOT_EXPIRED, FakeSensiBackend

ICD_ID = "36-6f-92-ff-fe-0c-0b-07"

EXPIRED_TOKEN_ERROR = {
    "message": "jwt expired",
    "data": {
        "message": "jwt expired",
        "code": "invalid_token",
        "type": "UnauthorizedError",
    },
}


@pytest.fixture
def client(hass: HomeAssistant, sensi_backend: FakeSensiBackend) -> SensiClient:
    """Return a client wired to the scripted backend, with unexpired credentials."""
    return SensiClient(
        hass,
        AuthenticationConfig(
            refresh_token="refresh",
            access_token="access",
            expires_at=NOT_EXPIRED,
            user_id="user",
        ),
    )


async def test_expired_token_is_refreshed_and_the_connection_retried(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """A `jwt expired` rejection refreshes the token and reconnects.

    This is the path that keeps a long-running installation working: Sensi
    rejects the connection, the reason arrives through `connect_error` rather
    than through the exception, and the retry has to notice that before
    deciding it was a network failure.
    """
    sensi_backend.connect_failures = [
        (SocketIOConnectionError("Connection rejected"), EXPIRED_TOKEN_ERROR)
    ]

    refreshed = AuthenticationConfig(
        refresh_token="refresh2",
        access_token="access2",
        expires_at=NOT_EXPIRED,
        user_id="user",
    )

    with patch(
        "custom_components.sensi.client.refresh_access_token",
        AsyncMock(return_value=refreshed),
    ) as mock_refresh:
        await client._connect()

    mock_refresh.assert_awaited_once()

    # One rejected attempt, then one that succeeded with the new token.
    assert len(sensi_backend.connections) == 1
    assert sensi_backend.connections[0]["headers"]["Authorization"] == (
        "bearer access2"
    )

    await sensi_backend.shutdown()


async def test_a_rejection_that_is_not_a_token_problem_is_not_retried(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """A plain connection failure must not burn a token refresh on every retry."""
    sensi_backend.connect_failures = [
        (SocketIOConnectionError("Connection error"), "Connection error")
    ]

    with (
        patch(
            "custom_components.sensi.client.refresh_access_token", AsyncMock()
        ) as mock_refresh,
        pytest.raises(SensiConnectionError, match="token was not expired"),
    ):
        await client._connect()

    mock_refresh.assert_not_awaited()
    assert sensi_backend.connections == []

    await sensi_backend.shutdown()


async def test_a_failure_after_the_refresh_gives_up(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """If the retry also fails, the caller gets a clear error rather than a loop."""
    sensi_backend.connect_failures = [
        (SocketIOConnectionError("Connection rejected"), EXPIRED_TOKEN_ERROR),
        (SocketIOConnectionError("Connection rejected again"), EXPIRED_TOKEN_ERROR),
    ]

    refreshed = AuthenticationConfig(
        refresh_token="refresh2",
        access_token="access2",
        expires_at=NOT_EXPIRED,
        user_id="user",
    )

    with (
        patch(
            "custom_components.sensi.client.refresh_access_token",
            AsyncMock(return_value=refreshed),
        ),
        pytest.raises(SensiConnectionError, match="after token refresh failed"),
    ):
        await client._connect()

    await sensi_backend.shutdown()


async def test_a_dropped_connection_marks_the_socket_disconnected(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """The server dropping the connection runs the client's disconnect handler.

    That is a different path from the client calling `disconnect()` itself, and
    it is the one that actually happens in production - the annotated log
    samples in client.py are all of this case.
    """
    await client._connect()
    socket = sensi_backend.sockets[-1]
    assert socket.connected

    await socket.fire_disconnect("transport error")

    assert not socket.connected

    await sensi_backend.shutdown()


async def test_a_state_event_with_no_devices_completes_setup(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """An account with nothing usable in it must not hang waiting for devices.

    The initial `state` event resolves, no device is tracked, and the
    info/capabilities round trip is skipped rather than timing out.
    """
    sensi_backend.state_override = [{"registration": {"name": "Nameless"}}]

    await client.wait_for_devices()

    assert client.get_devices() == []
    assert sensi_backend.emitted_names() == []

    await sensi_backend.shutdown()


async def test_devices_that_never_answer_fail_setup_cleanly(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """A thermostat that accepts the getters and never replies is not fatal-by-hang.

    `wait_for_devices` retries once and then raises ConfigEntryNotReady, so
    Home Assistant schedules a retry instead of leaving the entry wedged.
    """
    # Answer nothing to get_info / get_capabilities.
    sensi_backend.responses_for = lambda name, data: []

    with (
        patch("custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.05),
        pytest.raises(ConfigEntryNotReady, match="Unable to gather device information"),
    ):
        await client.wait_for_devices()

    await sensi_backend.shutdown()


async def test_a_timeout_on_the_post_refresh_retry_is_reported(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """Token refreshed, then the network went away - still a clean error.

    The second attempt fails differently from the first, so it exercises the
    retry block's own error mapping rather than the outer one.
    """
    sensi_backend.connect_failures = [
        (SocketIOConnectionError("Connection rejected"), EXPIRED_TOKEN_ERROR),
        (TimeoutError("no route to host"), None),
    ]

    refreshed = AuthenticationConfig(
        refresh_token="refresh2",
        access_token="access2",
        expires_at=NOT_EXPIRED,
        user_id="user",
    )

    with (
        patch(
            "custom_components.sensi.client.refresh_access_token",
            AsyncMock(return_value=refreshed),
        ),
        pytest.raises(SensiConnectionError, match="Timed out making the connection"),
    ):
        await client._connect()

    await sensi_backend.shutdown()


async def test_a_connection_error_while_retrying_device_info_is_not_ready(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """Losing the connection during the retry is ConfigEntryNotReady, not a crash.

    `wait_for_devices` gets one retry after a timeout. If that retry fails
    because the connection has gone rather than because it timed out again,
    the entry must still land in a retryable state.
    """
    sensi_backend.responses_for = lambda name, data: []

    real_send = client._send_event
    sends = 0

    async def failing_send(name, data, callback=None):
        nonlocal sends
        sends += 1
        # The first attempt sends get_info and get_capabilities and then times
        # out. The retry's first send is where the connection is lost.
        if sends > 2:
            raise SensiConnectionError("socket gone")
        await real_send(name, data, callback)

    with (
        patch("custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.05),
        patch.object(client, "_send_event", failing_send),
        pytest.raises(ConfigEntryNotReady),
    ):
        await client.wait_for_devices()

    assert sends > 2, "the retry never ran"

    await sensi_backend.shutdown()
