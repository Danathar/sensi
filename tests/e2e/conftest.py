"""Fixtures for the Sensi end-to-end tests.

The unit tests under ``tests/`` construct ``SensiDevice`` and entity objects
directly. These tests go the other way round: they let Home Assistant set the
integration up the way it does on a real system - config flow, config entry,
platform forwarding, coordinator refresh - and replace only the one thing that
cannot run in CI, the socket.io connection to ``rt.sensiapi.io``.

``FakeSensiBackend`` is that replacement. It is a scripted stand-in for the
Sensi server: it answers ``get_info`` / ``get_capabilities`` with the sample
payloads, replies to setter events through the socket.io ack callback, and
records everything the integration emitted so a test can assert on the wire
traffic rather than on internal state.
"""

import asyncio
from collections.abc import Callable, Iterator
import copy
import json
import os
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensi.auth import (
    KEY_ACCESS_TOKEN,
    KEY_EXPIRES_AT,
    KEY_REFRESH_TOKEN,
    KEY_USER_ID,
)
from custom_components.sensi.const import CONFIG_REFRESH_TOKEN, SENSI_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

# Far enough in the future that AuthenticationConfig never considers the stored
# access token expired, so no test accidentally depends on a token refresh.
NOT_EXPIRED = 99_999_999_999


def load_sample(filename: str) -> dict:
    """Load one of the captured payloads from ``tests/``."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), filename)
    with open(path, encoding="utf-8") as fptr:
        return json.load(fptr)


class FakeSensiSocket:
    """Stand-in for ``socketio.AsyncClient``.

    Only the surface ``SensiClient`` actually uses is implemented: the
    ``@sio.event`` / ``@sio.on("*")`` handler registration, ``connect``,
    ``disconnect``, ``wait`` and ``emit``.
    """

    def __init__(self, backend: FakeSensiBackend) -> None:
        """Bind this socket to the backend that scripts its responses."""
        self._backend = backend
        self._handlers: dict[str, Callable] = {}
        self._catch_all: Callable | None = None
        self.connected = False

    # -- handler registration -------------------------------------------

    def event(self, handler: Callable) -> Callable:
        """Register ``connect`` / ``connect_error`` / ``disconnect``."""
        self._handlers[handler.__name__] = handler
        return handler

    def on(self, name: str) -> Callable:
        """Register a named handler, or the ``"*"`` catch-all."""

        def decorator(handler: Callable) -> Callable:
            if name == "*":
                self._catch_all = handler
            else:
                self._handlers[name] = handler
            return handler

        return decorator

    # -- connection ------------------------------------------------------

    async def connect(self, url: str, **kwargs: Any) -> None:
        """Accept the connection and queue the initial ``state`` event.

        The initial state is delivered as a task rather than inline because
        ``SensiClient`` only creates the future it waits on *after*
        ``connect()`` returns.
        """
        failure = self._backend.next_connect_failure()
        if failure is not None:
            error, data = failure
            connect_error = self._handlers.get("connect_error")
            if connect_error:
                await connect_error(data)
            raise error

        self._backend.connections.append({"url": url, **kwargs})
        self.connected = True

        connect_handler = self._handlers.get("connect")
        if connect_handler:
            await connect_handler()

        self._backend.schedule(self.deliver("state", self._backend.state_payload()))

    async def fire_disconnect(self, reason: str) -> None:
        """Invoke the client's ``disconnect`` handler, as socket.io would.

        The server dropping the connection is not the same as the client
        calling ``disconnect()``; only the former runs that handler.
        """
        self.connected = False
        handler = self._handlers.get("disconnect")
        if handler:
            await handler(reason)

    async def disconnect(self) -> None:
        """Mark the socket disconnected."""
        self.connected = False
        self._backend.disconnects += 1

    async def shutdown(self) -> None:
        """Disconnect and abort any in-flight reconnect.

        ``SensiClient._async_disconnect`` uses ``shutdown()`` rather than
        ``disconnect()``, and swallows every exception while doing so - a fake
        without this method would silently look like a successful teardown.
        """
        await self.disconnect()

    async def wait(self) -> None:
        """Return immediately - there is no background connection to drain."""
        return

    async def emit(
        self,
        name: str,
        data: Any = None,
        namespace: Any = None,
        callback: Callable | None = None,
    ) -> None:
        """Record an emitted event and schedule the scripted response."""
        self._backend.emitted.append((name, data))

        for event_name, payload in self._backend.responses_for(name, data):
            self._backend.schedule(self.deliver(event_name, payload))

        if callback is not None:
            ack = self._backend.ack_for(name, data)
            if ack is not None:
                self._backend.schedule(self.invoke_ack(callback, ack))

    # -- server -> client -------------------------------------------------

    async def deliver(self, event: str, payload: Any) -> None:
        """Push a server event at the integration's catch-all handler."""
        if self._catch_all:
            await self._catch_all(event, payload)

    async def invoke_ack(self, callback: Callable, args: tuple) -> None:
        """Invoke a socket.io ack callback with the scripted arguments."""
        callback(*args)


class FakeSensiBackend:
    """Scripted Sensi server used by the end-to-end tests."""

    def __init__(self, devices: list[dict]) -> None:
        """Script the backend to serve the given device payloads."""
        self.devices = {device["icd_id"]: copy.deepcopy(device) for device in devices}

        self.emitted: list[tuple[str, Any]] = []
        self.connections: list[dict] = []
        self.disconnects = 0
        self.sockets: list[FakeSensiSocket] = []

        # Set to an exception instance to make *every* connect attempt fail.
        self.connect_error: BaseException | None = None
        self.connect_error_data: Any = None

        # Or queue per-attempt failures as (exception, connect_error_data).
        # Each connect consumes one entry; once the queue is empty, connects
        # succeed. This is how a "fails once, then works after a token
        # refresh" sequence is scripted.
        self.connect_failures: list[tuple[BaseException, Any]] = []

        # Replaces the whole `state` event body when set, so a test can serve
        # a malformed or empty payload.
        self.state_override: list[dict] | None = None

        # Overrides keyed by emitted event name. Each value is the tuple of
        # positional arguments the socket.io ack callback receives.
        self.acks: dict[str, tuple] = {}

        self._tasks: set[asyncio.Task] = set()

    # -- connection scripting ----------------------------------------------

    def next_connect_failure(self) -> tuple[BaseException, Any] | None:
        """Return the failure the next connect should raise, if any."""
        if self.connect_failures:
            return self.connect_failures.pop(0)
        if self.connect_error is not None:
            return (self.connect_error, self.connect_error_data)
        return None

    # -- payloads ---------------------------------------------------------

    def state_payload(self) -> list[dict]:
        """Return the ``state`` event body - a list of device documents."""
        if self.state_override is not None:
            return copy.deepcopy(self.state_override)
        return copy.deepcopy(list(self.devices.values()))

    def info_payload(self, icd_id: str) -> dict:
        """Return the ``info`` event body for one device."""
        info = copy.deepcopy(self.devices[icd_id].get("thermostat_info", {}))
        info["icd_id"] = icd_id
        return info

    def capabilities_payload(self, icd_id: str) -> dict:
        """Return the ``capabilities`` event body for one device."""
        capabilities = copy.deepcopy(self.devices[icd_id].get("capabilities", {}))
        capabilities["icd_id"] = icd_id
        return capabilities

    def responses_for(self, name: str, data: Any) -> list[tuple[str, Any]]:
        """Return the server events triggered by an emitted event."""
        icd_id = (data or {}).get("icd_id")
        if icd_id not in self.devices:
            return []

        if name == "get_info":
            return [("info", self.info_payload(icd_id))]
        if name == "get_capabilities":
            return [("capabilities", self.capabilities_payload(icd_id))]
        return []

    def ack_for(self, name: str, data: Any) -> tuple | None:
        """Return the ack arguments for a setter event, or None to stay silent."""
        if name in self.acks:
            return self.acks[name]

        payload = data or {}
        if name == "set_temperature":
            return (
                None,
                {
                    "current_temp": payload.get("target_temp"),
                    "mode": payload.get("mode"),
                    "target_temp": payload.get("target_temp"),
                },
            )
        if name == "set_operating_mode":
            return ("accepted",)

        # Every other setter is treated as an empty success ack.
        return (None,)

    # -- task bookkeeping --------------------------------------------------

    def schedule(self, coro) -> None:
        """Run a coroutine on the loop, keeping a reference so it is not GC'd."""
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        """Cancel anything still in flight at the end of a test."""
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with_suppressed = asyncio.gather(task, return_exceptions=True)
            await with_suppressed

    # -- convenience for assertions ---------------------------------------

    def emitted_names(self) -> list[str]:
        """Return just the names of the events the integration emitted."""
        return [name for name, _ in self.emitted]

    def last_emitted(self, name: str) -> Any:
        """Return the payload of the most recent emit of ``name``."""
        for emitted_name, payload in reversed(self.emitted):
            if emitted_name == name:
                return payload
        raise AssertionError(f"{name} was never emitted, saw {self.emitted_names()}")


@pytest.fixture
def sensi_backend() -> Iterator[FakeSensiBackend]:
    """Patch ``socketio.AsyncClient`` with the scripted fake backend."""
    backend = FakeSensiBackend([load_sample("sample.json")])

    def factory(*args: Any, **kwargs: Any) -> FakeSensiSocket:
        socket = FakeSensiSocket(backend)
        backend.sockets.append(socket)
        return socket

    with patch("custom_components.sensi.client.socketio.AsyncClient", factory):
        yield backend


@pytest.fixture
def stored_credentials() -> Iterator[None]:
    """Serve a valid, unexpired stored token from Home Assistant's store."""
    config = {
        KEY_REFRESH_TOKEN: "e2e_refresh_token",
        KEY_ACCESS_TOKEN: "e2e_access_token",
        KEY_EXPIRES_AT: NOT_EXPIRED,
        KEY_USER_ID: "e2e_user",
    }
    with patch("homeassistant.helpers.storage.Store.async_load", return_value=config):
        yield


@pytest.fixture
async def sensi_entry(
    hass: HomeAssistant,
    sensi_backend: FakeSensiBackend,
    stored_credentials: None,
    enable_custom_integrations: None,
) -> MockConfigEntry:
    """Set up the integration end to end and return the loaded config entry."""
    # The captured payloads report `display_scale: "f"`, so run these tests on
    # the unit system a Sensi owner actually has. Otherwise every temperature
    # assertion is really an assertion about Home Assistant's F-to-C rounding.
    hass.config.units = US_CUSTOMARY_SYSTEM

    entry = MockConfigEntry(
        domain=SENSI_DOMAIN,
        data={CONFIG_REFRESH_TOKEN: "e2e_refresh_token"},
        unique_id="e2e_user",
        title="Sensi Thermostat",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    yield entry

    await sensi_backend.shutdown()
