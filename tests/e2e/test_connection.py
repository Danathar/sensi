"""End-to-end coverage of the connection lifecycle.

Reconnect and mid-session token refresh are what a unit test is worst at and an
end-to-end test is best at: the behaviour is entirely about ordering between a
server that drops the connection, a handler that records why, and a retry that
has to decide whether the token or the network was at fault.

Everything here drives ``SensiClient`` through the same scripted backend the
rest of ``tests/e2e/`` uses, rather than reaching into its private state.
"""

import asyncio
import copy
from unittest.mock import AsyncMock, patch

import pytest
from socketio.exceptions import ConnectionError as SocketIOConnectionError

from custom_components.sensi.auth import SensiConnectionError
from custom_components.sensi.client import SensiClient
from custom_components.sensi.data import AuthenticationConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .conftest import NOT_EXPIRED, FakeSensiBackend, FakeSensiSocket

ICD_ID = "aa-bb-cc-dd-ee-ff-00-01"
SECOND_ICD_ID = "aa-bb-cc-dd-ee-ff-00-02"

EXPIRED_TOKEN_ERROR = {
    "message": "jwt expired",
    "data": {
        "message": "jwt expired",
        "code": "invalid_token",
        "type": "UnauthorizedError",
    },
}


class ReconnectingSocket(FakeSensiSocket):
    """The fake socket with socket.io's own reconnect, caught mid-handshake.

    When the server drops a connection, the library retries it by itself.
    shutdown() on a socket in that state only sets an abort flag, which the
    reconnect task checks between attempts, and then awaits that task. An
    attempt already inside connect() therefore runs to completion, and the
    socket is connected again by the time shutdown() returns. This fake always
    takes that timing: its retry is mid-handshake whenever shutdown() lands.
    """

    def __init__(self, backend: FakeSensiBackend) -> None:
        """Bind to the backend; nothing is being retried yet."""
        super().__init__(backend)
        self.reconnecting = False
        self._connect_args: tuple[str, dict] | None = None

    async def connect(self, url: str, **kwargs) -> None:
        """Connect, remembering the arguments the library would retry with."""
        self._connect_args = (url, kwargs)
        await super().connect(url, **kwargs)

    async def fire_disconnect(self, reason: str) -> None:
        """Drop the connection and start the library's own retry."""
        await super().fire_disconnect(reason)
        self.reconnecting = True

    async def shutdown(self) -> None:
        """Disconnect, or wait out the retry - which completes, and connects."""
        if self.connected:
            await self.disconnect()
        elif self.reconnecting:
            self.reconnecting = False
            url, kwargs = self._connect_args
            await super().connect(url, **kwargs)


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


async def test_a_refresh_during_the_librarys_own_reconnect_leaves_no_socket_behind(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """The socket socket.io was reconnecting is torn down, not left connected.

    After a server-side drop the library retries once on its own. A 30-second
    refresh that lands while that retry is mid-handshake called shutdown() on
    a socket that was not connected yet. shutdown() waited for the retry and
    returned with the socket connected again, and nothing checked. The client
    installed a replacement and forgot the old one: it stayed connected past
    stop(), still pushing state events into an unloaded client.
    """

    def factory(*_args, **_kwargs) -> ReconnectingSocket:
        socket = ReconnectingSocket(sensi_backend)
        sensi_backend.sockets.append(socket)
        return socket

    with patch("custom_components.sensi.client.socketio.AsyncClient", factory):
        await client._connect()
        dropped = sensi_backend.sockets[-1]

        # The server drops the transport, and socket.io starts its own retry.
        await dropped.fire_disconnect("transport error")

        # The refresh lands while that retry is mid-handshake; then unload.
        await client.async_update_devices()
        await client.stop()

    assert not dropped.connected, "the socket socket.io reconnected was left connected"
    assert [socket for socket in sensi_backend.sockets if socket.connected] == []

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


async def test_an_empty_state_event_completes_setup_at_once(
    client: SensiClient, sensi_backend: FakeSensiBackend, caplog
) -> None:
    """An account with no thermostats is an answer, and a fast one.

    The backend sends `state` with an empty list. That used to be dropped
    before the initial-state waiter was resolved, so setup sat out the full
    PREPARE_DEVICES_TIMEOUT and then looked exactly like a backend that never
    answered. Now it completes immediately, with a WARNING saying why there
    are no entities.
    """
    sensi_backend.state_override = []

    # PREPARE_DEVICES_TIMEOUT is left at its real value on purpose: the bound
    # here is far below it, so a wait that still runs to the timeout fails
    # the test instead of passing slowly.
    await asyncio.wait_for(client.wait_for_devices(), timeout=2)

    assert client.get_devices() == []
    assert sensi_backend.emitted_names() == []
    assert "account lists no thermostats" in caplog.text

    await sensi_backend.shutdown()


async def test_no_state_event_at_all_is_not_ready(
    client: SensiClient, sensi_backend: FakeSensiBackend, caplog
) -> None:
    """A backend that connects and never lists the thermostats is retried.

    Nothing arrives after the handshake. `wait_for_devices` used to return
    normally here with an empty device list, so the entry loaded with no
    entities and Home Assistant had no reason to try again. It has to raise
    ConfigEntryNotReady so the retry-with-backoff path runs instead.
    """
    sensi_backend.withhold_state = True

    with (
        patch("custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.05),
        pytest.raises(ConfigEntryNotReady, match="No state event within"),
    ):
        await client.wait_for_devices()

    # The socket was up, so this is the failure the getters would have gone
    # out on - and none did, because there was no device to ask about.
    assert len(sensi_backend.connections) == 1
    assert sensi_backend.emitted_names() == []
    assert "Timed out waiting for event 'state'" in caplog.text

    await sensi_backend.shutdown()


async def test_a_refresh_with_one_silent_device_still_succeeds(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """One thermostat that sends no state does not fail the whole refresh.

    A refresh that brings no state at all raises, so the coordinator counts
    it as a failure. With two devices and only one reporting, the one that
    reported did refresh; failing here would make every thermostat on the
    account unavailable because one of them is offline.
    """
    second = copy.deepcopy(sensi_backend.devices[ICD_ID])
    second["icd_id"] = SECOND_ICD_ID
    sensi_backend.devices[SECOND_ICD_ID] = second

    await client.wait_for_devices()
    assert {device.identifier for device in client.get_devices()} == {
        ICD_ID,
        SECOND_ICD_ID,
    }

    sensi_backend.state_override = [copy.deepcopy(sensi_backend.devices[ICD_ID])]
    with patch("custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.05):
        await client.async_update_devices()

    # Neither device is dropped by a refresh that did not mention it.
    assert len(client.get_devices()) == 2

    # And with neither reporting, the refresh fails.
    sensi_backend.withhold_state = True
    with (
        patch("custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.05),
        pytest.raises(SensiConnectionError, match="No state event within"),
    ):
        await client.async_update_devices()

    await client.stop()
    await sensi_backend.shutdown()


async def test_a_refresh_queued_behind_stop_does_not_reconnect(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """A refresh that waited for stop() to let go of the lock stays stopped.

    stop() holds _reconnect_lock while it drains the socket. A coordinator
    tick that arrived meanwhile queued for the lock and, once stop() let go,
    ran its own disconnect/connect pair on the stopped client. The new socket
    stayed connected, and nothing was left that would ever shut it down.
    """
    await client.wait_for_devices()

    draining = asyncio.Event()
    drained = asyncio.Event()

    async def slow_wait(_socket: FakeSensiSocket) -> None:
        draining.set()
        await drained.wait()

    with patch.object(FakeSensiSocket, "wait", slow_wait):
        stop = asyncio.create_task(client.stop())
        await draining.wait()

        refresh = asyncio.create_task(client.async_update_devices())
        # Let the refresh run until it blocks on the lock stop() holds.
        await asyncio.sleep(0)
        assert not refresh.done()

        drained.set()
        await stop
        with pytest.raises(SensiConnectionError, match="stopped"):
            await refresh

    assert [socket for socket in sensi_backend.sockets if socket.connected] == []
    assert client._sio is None
    assert client._emit_loop_task is None

    await sensi_backend.shutdown()


async def test_a_refresh_after_stop_has_returned_does_not_reconnect(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """A stopped client stays stopped after stop() returns, not only during it.

    async_unload_entry awaits async_unload_platforms after stop(), and the
    coordinator is shut down only after that. A tick that lands in that
    window finds stop() already finished and the lock free. The stopped flag
    is never cleared, so that tick is refused too.
    """
    await client.wait_for_devices()
    await client.stop()

    with pytest.raises(SensiConnectionError, match="stopped"):
        await client.async_update_devices()

    assert len(sensi_backend.sockets) == 1
    assert [socket for socket in sensi_backend.sockets if socket.connected] == []
    assert client._sio is None
    assert client._emit_loop_task is None

    await sensi_backend.shutdown()


async def test_a_setter_recovery_queued_behind_stop_does_not_reconnect(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """A refused setter's recovery that waited for stop() does not reconnect.

    The refresh is one of three reconnect paths; a `Forbidden` setter's
    recovery is another, and it takes the same lock. A setter refused while
    stop() drains the socket queues for that lock and, once stop() lets go,
    runs its own disconnect/connect pair. It must find the client stopped and
    report the refusal it already had, not open a socket nothing will close.
    """
    await client.wait_for_devices()
    (device,) = client.get_devices()

    sensi_backend.ack_for = lambda name, _data: (
        ({"error": {"description": "Forbidden"}},)
        if name == "set_fan_mode"
        else (None,)
    )

    emitted = asyncio.Event()
    release_ack = asyncio.Event()
    draining = asyncio.Event()
    drained = asyncio.Event()
    invoke_ack = FakeSensiSocket.invoke_ack

    async def held_ack(socket: FakeSensiSocket, callback, args: tuple) -> None:
        emitted.set()
        await release_ack.wait()
        await invoke_ack(socket, callback, args)

    async def slow_wait(_socket: FakeSensiSocket) -> None:
        draining.set()
        await drained.wait()

    with (
        patch.object(FakeSensiSocket, "invoke_ack", held_ack),
        patch.object(FakeSensiSocket, "wait", slow_wait),
        patch.object(client, "try_refresh_access_token", AsyncMock()),
    ):
        setter = asyncio.create_task(client.async_set_fan_mode(device, "on"))
        await emitted.wait()

        stop = asyncio.create_task(client.stop())
        await draining.wait()

        # The refusal arrives while stop() holds the lock; let the setter run
        # until its recovery blocks on it.
        release_ack.set()
        for _ in range(5):
            await asyncio.sleep(0)
        assert not setter.done()

        drained.set()
        await stop
        response = await setter

    assert response.error == "Forbidden"
    assert len(sensi_backend.sockets) == 1
    assert [socket for socket in sensi_backend.sockets if socket.connected] == []
    assert client._sio is None
    assert client._emit_loop_task is None

    await sensi_backend.shutdown()


async def test_a_setter_recovery_waits_for_another_setters_ack_in_flight(
    client: SensiClient, sensi_backend: FakeSensiBackend
) -> None:
    """A refused setter's reconnect does not drop another setter's ack.

    python-socketio drops pending ack callbacks on disconnect. The refresh
    waits for every setter on the wire before it tears the socket down; the
    `Forbidden` recovery did not, so a second setter whose ack was still on
    its way timed out with "Future not done" although the thermostat had
    accepted it.
    """
    await client.wait_for_devices()
    (device,) = client.get_devices()

    fan_refused = asyncio.Event()

    def ack_for(name, _data):
        if name == "set_fan_mode" and not fan_refused.is_set():
            fan_refused.set()
            return ({"error": {"description": "Forbidden"}},)
        return (None,)

    sensi_backend.ack_for = ack_for

    offset_emitted = asyncio.Event()
    release_offset_ack = asyncio.Event()
    invoke_ack = FakeSensiSocket.invoke_ack

    async def slow_first_ack(socket: FakeSensiSocket, callback, args: tuple) -> None:
        if not offset_emitted.is_set():
            # The first ack is the offset's: the backend's round trip.
            offset_emitted.set()
            await release_offset_ack.wait()
            if not socket.connected:
                return
        await invoke_ack(socket, callback, args)

    connections_before = len(sensi_backend.connections)

    with (
        patch.object(FakeSensiSocket, "invoke_ack", slow_first_ack),
        patch.object(client, "try_refresh_access_token", AsyncMock()),
    ):
        offset = asyncio.create_task(client.async_set_temperature_offset(device, 2))
        await offset_emitted.wait()

        fan = asyncio.create_task(client.async_set_fan_mode(device, "on"))
        # The fan setter is refused while the offset's ack is still on its
        # way. Its recovery must wait for that ack, not reconnect under it.
        await asyncio.wait_for(fan_refused.wait(), 3)
        await asyncio.sleep(0.2)
        assert not fan.done()
        assert len(sensi_backend.connections) == connections_before

        release_offset_ack.set()
        fan_response = await fan
        offset_response = await offset

    assert offset_response.error is None
    assert fan_response.error is None
    assert sensi_backend.emitted_names().count("set_temp_offset") == 1
    assert len(sensi_backend.connections) == connections_before + 1

    await client.stop()
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
