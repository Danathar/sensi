"""Tests for the parts of SensiClient that need no socket.io connection.

`tests/e2e/` covers the client the way Home Assistant drives it - connect,
handshake, setters, refresh. What it cannot reach are the branches that only
happen when something goes wrong at an awkward moment: an ack that arrives after
its future was cancelled, an emit loop that finds the socket gone mid-drain, a
token that expires between two connection attempts.

Those are pure in-process logic, so they are covered here directly rather than
by contorting the fake backend into producing them.

The event-registry, dispatcher and state-handler cases came from PR #37, which
covered them more thoroughly than the first version of this file did - it
asserts the payload each waiter receives rather than only that it completed.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sensi.auth import SensiConnectionError
from custom_components.sensi.client import (
    EventInfo,
    SensiClient,
    SensiDevice,
    extract_icd_id,
    get_error_description_from_event_callback,
    is_token_expired,
)
from custom_components.sensi.data import AuthenticationConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

ICD_ID = "36-6f-92-ff-fe-0c-0b-07"


@pytest.fixture
def client(hass: HomeAssistant) -> SensiClient:
    """Return a client with credentials that are valid and not near expiry.

    Unexpired on purpose: the token-refresh tests below drive `_connect`, and a
    client whose stored token is already expired would try to refresh it for
    real before reaching the branch under test.
    """
    config = AuthenticationConfig(
        refresh_token="refresh",
        access_token="access",
        expires_at=99_999_999_999,
        user_id="user",
    )
    return SensiClient(hass, config)


class TestContextManager:
    """`async with SensiClient(...)` disconnects on the way out."""

    async def test_aenter_returns_the_client(self, client: SensiClient) -> None:
        """Entering yields the same client instance."""
        async with client as entered:
            assert entered is client

    async def test_aexit_disconnects_and_does_not_swallow(
        self, client: SensiClient
    ) -> None:
        """Leaving disconnects, and returns False so exceptions propagate."""
        with patch.object(client, "_async_disconnect", AsyncMock()) as mock_disconnect:
            assert await client.__aexit__(None, None, None) is False
            mock_disconnect.assert_awaited_once()

    async def test_aexit_lets_an_exception_through(self, client: SensiClient) -> None:
        """A failure inside the block is not suppressed by the context manager."""
        with (
            patch.object(client, "_async_disconnect", AsyncMock()),
            pytest.raises(ValueError, match="boom"),
        ):
            async with client:
                raise ValueError("boom")


class TestEventFutures:
    """Test cases for the (event, icd_id) future registry."""

    async def test_create_event_future_registers_future(self, client) -> None:
        """A created future is registered under its (event, icd_id) key."""
        future = await client._create_event_future("info", "icd-1")

        assert client._futures[("info", "icd-1")] == [future]
        assert not future.done()

        future.cancel()

    async def test_create_event_future_appends_to_existing_key(self, client) -> None:
        """Two waiters on the same key share one future list."""
        first = await client._create_event_future("info", "icd-1")
        second = await client._create_event_future("info", "icd-1")

        assert client._futures[("info", "icd-1")] == [first, second]
        assert first is not second

        first.cancel()
        second.cancel()

    async def test_create_event_future_separates_keys(self, client) -> None:
        """The same event for two devices uses two separate keys."""
        first = await client._create_event_future("info", "icd-1")
        second = await client._create_event_future("info", "icd-2")

        assert client._futures[("info", "icd-1")] == [first]
        assert client._futures[("info", "icd-2")] == [second]

        first.cancel()
        second.cancel()

    async def test_resolve_futures_resolves_and_clears(self, client) -> None:
        """Resolving hands the data to every waiter and drops the key."""
        first = await client._create_event_future("info", "icd-1")
        second = await client._create_event_future("info", "icd-1")

        client._resolve_futures("info", "icd-1", {"payload": 1})

        assert await first == {"payload": 1}
        assert await second == {"payload": 1}
        assert ("info", "icd-1") not in client._futures

    async def test_resolve_futures_without_waiters(self, client) -> None:
        """Resolving an unknown key is a no-op rather than a KeyError."""
        client._resolve_futures("info", "icd-unknown", {"payload": 1})

        assert client._futures == {}

    async def test_resolve_futures_ignores_already_resolved(self, client) -> None:
        """An already-resolved future does not raise InvalidStateError."""
        future = await client._create_event_future("info", "icd-1")
        future.set_result("first")

        client._resolve_futures("info", "icd-1", "second")

        assert await future == "first"
        assert ("info", "icd-1") not in client._futures

    async def test_resolve_futures_resolves_the_waiters_behind_a_cancelled_one(
        self, client
    ) -> None:
        """One dead future must not strand the waiters queued behind it.

        `set_result` on a cancelled future raises `InvalidStateError`. With the
        suppress wrapped around the loop rather than around the call, that
        exception left the loop and every later waiter went unresolved until
        its own timeout - one wasted coordinator cycle per timeout.
        """
        stale = await client._create_event_future("info", "icd-1")
        first = await client._create_event_future("info", "icd-1")
        second = await client._create_event_future("info", "icd-1")
        # Cancelled after the others were created, so the pruning in
        # _create_event_future cannot remove it: this is the ordering that
        # actually happens, with the corpse first in the list.
        stale.cancel()
        assert stale.cancelled()

        client._resolve_futures("info", "icd-1", {"payload": 1})

        # done() before await: on the unfixed client these are never resolved,
        # and awaiting one would hang the suite instead of failing it.
        assert first.done()
        assert second.done()
        assert await first == {"payload": 1}
        assert await second == {"payload": 1}

    async def test_resolve_futures_resolves_the_waiters_behind_a_resolved_one(
        self, client
    ) -> None:
        """The same holds for a future that already has a result."""
        stale = await client._create_event_future("info", "icd-1")
        live = await client._create_event_future("info", "icd-1")
        stale.set_result("first")

        client._resolve_futures("info", "icd-1", "second")

        assert live.done()
        assert await stale == "first"
        assert await live == "second"

    async def test_a_timed_out_waiter_does_not_strand_the_next_one(
        self, client
    ) -> None:
        """The whole shape, as it happens in production.

        `asyncio.wait_for` cancels the future it was handed, and only
        `_resolve_futures` clears the key, so the cancelled future is still
        sitting in the list when the next event for that key arrives. The
        fresh waiter behind it used to time out too.
        """
        assert await client._wait_for_event("info", "icd-1", timeout=0) is None

        async def resolve_soon() -> None:
            # Let the second _wait_for_event register its future first.
            await asyncio.sleep(0)
            client._resolve_futures("info", "icd-1", {"payload": 3})

        # An explicit short timeout: the unfixed client leaves this waiter
        # unresolved, and the default 5s would just make the failure slow.
        results = await asyncio.gather(
            client._wait_for_event("info", "icd-1", timeout=1), resolve_soon()
        )

        assert results[0] == {"payload": 3}

    async def test_create_event_future_drops_futures_nobody_awaits(
        self, client
    ) -> None:
        """A new waiter clears the corpses left behind by earlier ones.

        Nothing else prunes this list between events, so without it a key
        collects one dead future per timeout and the "Resolving N futures"
        debug line counts them as real waiters.
        """
        cancelled = await client._create_event_future("info", "icd-1")
        resolved = await client._create_event_future("info", "icd-1")
        cancelled.cancel()
        resolved.set_result("done")

        fresh = await client._create_event_future("info", "icd-1")

        assert client._futures[("info", "icd-1")] == [fresh]

        fresh.cancel()

    async def test_create_event_future_keeps_the_waiters_still_waiting(
        self, client
    ) -> None:
        """Pruning must not evict a waiter that is still pending."""
        waiting = await client._create_event_future("info", "icd-1")

        fresh = await client._create_event_future("info", "icd-1")

        assert client._futures[("info", "icd-1")] == [waiting, fresh]

        client._resolve_futures("info", "icd-1", "data")

        assert waiting.done()
        assert fresh.done()
        assert await waiting == "data"
        assert await fresh == "data"

    async def test_resolve_futures_distinguishes_icd_id(self, client) -> None:
        """Resolving one device's future leaves another device's future pending."""
        mine = await client._create_event_future("info", "icd-1")
        theirs = await client._create_event_future("info", "icd-2")

        client._resolve_futures("info", "icd-1", "data")

        assert await mine == "data"
        assert not theirs.done()

        theirs.cancel()

    async def test_wait_for_event_returns_resolved_data(self, client) -> None:
        """A resolved future is handed back to the waiter."""

        async def resolve_soon() -> None:
            # Let _wait_for_event register its future first.
            await asyncio.sleep(0)
            client._resolve_futures("info", "icd-1", {"payload": 2})

        results = await asyncio.gather(
            client._wait_for_event("info", "icd-1"), resolve_soon()
        )

        assert results[0] == {"payload": 2}

    async def test_wait_for_event_times_out(self, client) -> None:
        """A timed out wait logs and returns None instead of raising."""
        assert await client._wait_for_event("info", "icd-1", timeout=0) is None


class TestOnEvent:
    """Test cases for the _on_event dispatcher."""

    @pytest.mark.parametrize(
        ("event", "handler"),
        [
            ("state", "_update_state"),
            ("capabilities", "_update_capabilities"),
            ("info", "_update_info"),
        ],
    )
    def test_dispatches_to_handler(self, client, event: str, handler: str) -> None:
        """Each known event is routed to its own handler."""
        data = {"icd_id": "icd-1"}

        with patch.object(client, handler) as mock_handler:
            client._on_event(event, data)

        mock_handler.assert_called_once_with(data)

    async def test_unknown_event_resolves_futures(self, client) -> None:
        """An unrecognized event resolves its (event, None) waiters."""
        future = await client._create_event_future("set_temperature", None)

        client._on_event("set_temperature", {"icd_id": "icd-1"})

        # The dispatcher deliberately resolves with None, not with the payload.
        assert await future is None

    def test_unknown_event_does_not_call_update_handlers(self, client) -> None:
        """An unrecognized event touches none of the state handlers."""
        with (
            patch.object(client, "_update_state") as mock_state,
            patch.object(client, "_update_capabilities") as mock_capabilities,
            patch.object(client, "_update_info") as mock_info,
        ):
            client._on_event("set_temperature", {"icd_id": "icd-1"})

        mock_state.assert_not_called()
        mock_capabilities.assert_not_called()
        mock_info.assert_not_called()


class TestUpdateState:
    """Test cases for the _update_state handler."""

    @pytest.mark.parametrize("data", [None, []])
    async def test_empty_payload_is_ignored(self, client, data) -> None:
        """An empty state event neither creates devices nor resolves futures."""
        future = await client._create_event_future("state", None)

        client._update_state(data)

        assert client.get_devices() == []
        assert not future.done()

        future.cancel()

    async def test_creates_device_and_resolves_futures(self, client, mock_json) -> None:
        """A first state event creates the device and resolves both futures."""
        icd_id = mock_json["icd_id"]
        initial_future = await client._create_event_future("state", None)
        device_future = await client._create_event_future("state", icd_id)

        client._update_state([mock_json])

        assert [device.identifier for device in client.get_devices()] == [icd_id]
        assert await initial_future is None
        assert await device_future == mock_json

    async def test_updates_existing_device(self, client, mock_json) -> None:
        """A second state event updates the device already in the registry."""
        client._update_state([mock_json])
        device = client.get_devices()[0]

        update = {
            "icd_id": mock_json["icd_id"],
            "state": {**mock_json["state"], "display_temp": 61},
        }
        device_future = await client._create_event_future("state", update["icd_id"])

        client._update_state([update])

        assert client.get_devices() == [device]
        assert device.state.display_temp == 61
        assert await device_future == update

    async def test_registration_only_payload_skips_device_future(
        self, client, mock_json
    ) -> None:
        """A payload carrying no state resolves the initial future only.

        The first state event on connect usually carries registration and
        capabilities but no state, so per-device waiters must stay pending.
        """
        client._update_state([mock_json])

        stateless = {
            "icd_id": mock_json["icd_id"],
            "registration": mock_json["registration"],
        }
        initial_future = await client._create_event_future("state", None)
        device_future = await client._create_event_future("state", stateless["icd_id"])

        client._update_state([stateless])

        assert await initial_future is None
        assert not device_future.done()

        device_future.cancel()

    async def test_item_without_icd_id_is_skipped(self, client) -> None:
        """An entry with no icd_id creates no device."""
        initial_future = await client._create_event_future("state", None)

        client._update_state([{"state": {"display_temp": 70}}])

        assert client.get_devices() == []
        # The initial future is resolved regardless of whether any item was usable.
        assert await initial_future is None

    async def test_resolves_each_device_in_a_batch(self, client, mock_json) -> None:
        """A multi-device event resolves every device's future."""
        second = {**mock_json, "icd_id": "36-6f-92-ff-fe-0c-0b-08"}
        first_future = await client._create_event_future("state", mock_json["icd_id"])
        second_future = await client._create_event_future("state", second["icd_id"])

        client._update_state([mock_json, second])

        assert len(client.get_devices()) == 2
        assert await first_future == mock_json
        assert await second_future == second

    async def test_one_unparseable_device_does_not_block_the_others(
        self, client, mock_json
    ) -> None:
        """A device this parser chokes on must not cost every other device.

        _update_state parses every device in one loop and then resolves the
        state futures for all of them. An exception escaping the parse skipped
        that entirely: on connect the initial state wait timed out and setup
        raised ConfigEntryNotReady, and at runtime every entity went
        unavailable after two failed refreshes - over one thermostat.
        """
        broken = {**mock_json, "icd_id": "36-6f-92-ff-fe-0c-0b-99"}
        initial_future = await client._create_event_future("state", None)
        broken_future = await client._create_event_future("state", broken["icd_id"])
        healthy_future = await client._create_event_future("state", mock_json["icd_id"])

        real_create = SensiDevice.create

        def create(item):
            if item["icd_id"] == broken["icd_id"]:
                raise AttributeError("'NoneType' object has no attribute 'get'")
            return real_create(item)

        with patch(
            "custom_components.sensi.client.SensiDevice.create", side_effect=create
        ):
            # Broken first, so a failure that ended the loop would take the
            # healthy device with it.
            client._update_state([broken, mock_json])

        assert [device.identifier for device in client.get_devices()] == [
            mock_json["icd_id"]
        ]
        assert await initial_future is None
        assert await healthy_future == mock_json
        assert not broken_future.done()

        broken_future.cancel()

    async def test_a_null_container_no_longer_discards_the_event(
        self, client, mock_json_with_nulls
    ) -> None:
        """The payload from the issue, straight through the handler.

        With the containers guarded this parses rather than raising, so it
        needs no per-device rescue - the guard above is for shapes nobody has
        thought of yet.
        """
        icd_id = mock_json_with_nulls["icd_id"]
        initial_future = await client._create_event_future("state", None)
        device_future = await client._create_event_future("state", icd_id)

        client._update_state([mock_json_with_nulls])

        assert [device.identifier for device in client.get_devices()] == [icd_id]
        assert await initial_future is None
        assert await device_future == mock_json_with_nulls

    async def test_an_unparseable_update_leaves_the_previous_state(
        self, client, mock_json
    ) -> None:
        """A device already known keeps the state it had, rather than losing it."""
        client._update_state([mock_json])
        device = client.get_devices()[0]
        previous_temp = device.state.display_temp

        with patch.object(device, "update_state", side_effect=AttributeError("boom")):
            client._update_state([{**mock_json, "state": {"display_temp": 61}}])

        assert device.state.display_temp == previous_temp


class TestUpdateInfo:
    """Test cases for the _update_info handler."""

    async def test_updates_known_device_and_resolves(self, client, mock_json) -> None:
        """Info for a known device updates it and resolves its future."""
        client._update_state([mock_json])
        device = client.get_devices()[0]

        data = {
            "icd_id": mock_json["icd_id"],
            "serial_number": "42WFRP46B00220",
            "model_number": "1F87U-42WFC",
        }
        future = await client._create_event_future("info", data["icd_id"])

        client._update_info(data)

        assert device.info.serial_number == "42WFRP46B00220"
        assert device.info.model_number == "1F87U-42WFC"
        assert await future == data

    async def test_unknown_device_still_resolves(self, client) -> None:
        """Info for an unknown device resolves the waiter without a device update."""
        data = {"icd_id": "icd-unknown", "serial_number": "S1"}
        future = await client._create_event_future("info", "icd-unknown")

        client._update_info(data)

        assert client.get_devices() == []
        assert await future == data

    @pytest.mark.parametrize("data", [None, {}, {"serial_number": "S1"}])
    async def test_unusable_payload_is_ignored(self, client, data) -> None:
        """A falsy payload, or one with no icd_id, resolves nothing."""
        future = await client._create_event_future("info", "icd-1")

        client._update_info(data)

        assert not future.done()

        future.cancel()


class TestUpdateCapabilities:
    """Test cases for the _update_capabilities handler."""

    async def test_updates_known_device_and_resolves(self, client, mock_json) -> None:
        """Capabilities for a known device update it and resolve its future."""
        client._update_state([mock_json])
        device = client.get_devices()[0]

        data = {"icd_id": mock_json["icd_id"], **mock_json["capabilities"]}
        data["keypad_lockout"] = "yes"
        future = await client._create_event_future("capabilities", data["icd_id"])

        client._update_capabilities(data)

        assert device.capabilities.keypad_lockout is True
        assert await future == data

    async def test_unknown_device_still_resolves(self, client) -> None:
        """Capabilities for an unknown device resolve the waiter only."""
        data = {"icd_id": "icd-unknown", "keypad_lockout": "yes"}
        future = await client._create_event_future("capabilities", "icd-unknown")

        client._update_capabilities(data)

        assert client.get_devices() == []
        assert await future == data

    @pytest.mark.parametrize("data", [None, {}, {"keypad_lockout": "yes"}])
    async def test_unusable_payload_is_ignored(self, client, data) -> None:
        """A falsy payload, or one with no icd_id, resolves nothing."""
        future = await client._create_event_future("capabilities", "icd-1")

        client._update_capabilities(data)

        assert not future.done()

        future.cancel()


class TestSetterAcks:
    """`_async_invoke_setter` and the ack callback it installs."""

    async def _invoke(self, client: SensiClient, ack: tuple | None):
        """Run a setter, replying with `ack` (or not replying at all)."""

        async def fake_send(name, data, callback=None, future=None):
            if ack is not None and callback is not None:
                callback(*ack)

        with patch.object(client, "_send_event", fake_send):
            return await client._async_invoke_setter("set_thing", {"icd_id": ICD_ID})

    async def test_empty_ack_is_a_success(self, client: SensiClient) -> None:
        """A callback invoked with no arguments means "accepted"."""
        response = await self._invoke(client, ())
        assert response.error is None
        assert response.data == {}

    async def test_none_payload_ack_is_a_success(self, client: SensiClient) -> None:
        """An explicit null payload is also a success, not a failure."""
        response = await self._invoke(client, (None,))
        assert response.error is None
        assert response.data == {}

    async def test_error_payload_becomes_the_error_description(
        self, client: SensiClient
    ) -> None:
        """The thermostat's error code is what the caller sees."""
        response = await self._invoke(
            client, ({"error": {"description": "ThermostatOffline"}},)
        )
        assert response.error == "ThermostatOffline"
        assert response.data is None

    async def test_two_argument_ack_is_split_into_error_and_data(
        self, client: SensiClient
    ) -> None:
        """socket.io acks arrive as (error, data) when both are present."""
        response = await self._invoke(client, (None, {"mode": "heat"}))
        assert response.error is None
        assert response.data == {"mode": "heat"}

    async def test_no_ack_at_all_is_reported_as_a_failure(
        self, client: SensiClient
    ) -> None:
        """A thermostat that never answers must not leave the caller hanging."""
        with patch("custom_components.sensi.client.SET_EVENT_TIMEOUT", 0.01):
            response = await self._invoke(client, None)

        assert response.error == "Future not done"
        assert response.data is None

    async def test_ack_arriving_after_cancellation_is_dropped(
        self, client: SensiClient
    ) -> None:
        """A late ack must not touch a future that was already cancelled."""
        captured: list = []

        async def capture(name, data, callback=None, future=None):
            captured.append(callback)

        with (
            patch.object(client, "_send_event", capture),
            patch("custom_components.sensi.client.SET_EVENT_TIMEOUT", 0.01),
        ):
            response = await client._async_invoke_setter("set_thing", {})

        assert response.error == "Future not done"

        # The late reply lands after the setter has given up. It must be a
        # no-op rather than an InvalidStateError in a socket.io callback.
        captured[0]({"late": True})


class TestEmitLoop:
    """The background queue drain."""

    async def _run_once(self, client: SensiClient) -> None:
        """Run the emit loop just long enough for one pass."""
        task = asyncio.get_running_loop().create_task(client._emit_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_queued_events_are_emitted_while_connected(
        self, client: SensiClient
    ) -> None:
        """The happy path: drain the queue onto the socket."""
        sio = MagicMock()
        sio.connected = True
        sio.emit = AsyncMock()
        client._sio = sio

        await client._send_event("get_info", {"icd_id": ICD_ID})
        await self._run_once(client)

        sio.emit.assert_awaited_once_with("get_info", {"icd_id": ICD_ID}, None, None)

    async def test_a_disconnect_mid_drain_requeues_the_event(
        self, client: SensiClient
    ) -> None:
        """Work is put back rather than dropped when the socket goes away.

        The loop checks `connected` once for itself and again for each item it
        pulls off the queue. This socket answers True to the first check and
        False to the second, which is exactly the race the requeue branch
        exists for - the connection drops between deciding to drain and
        emitting.
        """
        checks = 0

        class FlakySocket:
            emit = AsyncMock()

            @property
            def connected(self) -> bool:
                nonlocal checks
                checks += 1
                return checks == 1

        client._sio = FlakySocket()
        client._event_queue.put_nowait(EventInfo("get_info", {}, None))

        await self._run_once(client)

        assert checks >= 2, "the loop never reached the per-item check"
        FlakySocket.emit.assert_not_awaited()
        assert client._event_queue.qsize() == 1

    async def test_an_emit_type_error_is_logged_not_fatal(
        self, client: SensiClient
    ) -> None:
        """A malformed payload must not kill the background task."""
        sio = MagicMock()
        sio.connected = True
        sio.emit = AsyncMock(side_effect=TypeError("not serialisable"))
        client._sio = sio

        await client._send_event("set_thing", object())
        await self._run_once(client)

        sio.emit.assert_awaited()

    async def test_a_setter_nobody_is_waiting_for_is_discarded(
        self, client: SensiClient
    ) -> None:
        """A timed out setter must not reach the thermostat on the next connect.

        _async_invoke_setter waits SET_EVENT_TIMEOUT and then tells the caller
        "Future not done", but the event stayed on the queue. The socket is
        torn down and rebuilt on every 30-second refresh, so the command went
        out on the next connect - the thermostat acting on something the user
        was told had failed, possibly an hour later.
        """
        sio = MagicMock()
        sio.connected = True
        sio.emit = AsyncMock()
        client._sio = sio

        timed_out = asyncio.get_running_loop().create_future()
        timed_out.cancel()
        client._event_queue.put_nowait(
            EventInfo("set_temperature", {"value": 72}, None, timed_out)
        )

        await self._run_once(client)

        sio.emit.assert_not_awaited()
        assert client._event_queue.qsize() == 0

    async def test_a_setter_still_being_waited_on_is_emitted(
        self, client: SensiClient
    ) -> None:
        """The discard must not swallow a setter with a live caller."""
        sio = MagicMock()
        sio.connected = True
        sio.emit = AsyncMock()
        client._sio = sio

        waiting = asyncio.get_running_loop().create_future()
        client._event_queue.put_nowait(
            EventInfo("set_temperature", {"value": 72}, None, waiting)
        )

        await self._run_once(client)

        sio.emit.assert_awaited_once_with("set_temperature", {"value": 72}, None, None)

        waiting.cancel()

    async def test_a_getter_survives_because_it_queues_no_future(
        self, client: SensiClient
    ) -> None:
        """Getters legitimately want to outlive a reconnect.

        get_info and get_capabilities are re-requested rather than retried by
        the caller, so they carry no future and the discard never applies to
        them.
        """
        sio = MagicMock()
        sio.connected = True
        sio.emit = AsyncMock()
        client._sio = sio

        await client._send_event("get_info", {"icd_id": ICD_ID})

        queued = client._event_queue.get_nowait()
        assert queued.future is None
        client._event_queue.put_nowait(queued)

        await self._run_once(client)

        sio.emit.assert_awaited_once_with("get_info", {"icd_id": ICD_ID}, None, None)

    async def test_a_stale_setter_does_not_block_the_ones_behind_it(
        self, client: SensiClient
    ) -> None:
        """Discarding one item continues the drain rather than ending it."""
        sio = MagicMock()
        sio.connected = True
        sio.emit = AsyncMock()
        client._sio = sio

        timed_out = asyncio.get_running_loop().create_future()
        timed_out.cancel()
        client._event_queue.put_nowait(
            EventInfo("set_temperature", {}, None, timed_out)
        )
        client._event_queue.put_nowait(EventInfo("get_info", {"icd_id": ICD_ID}, None))

        await self._run_once(client)

        sio.emit.assert_awaited_once_with("get_info", {"icd_id": ICD_ID}, None, None)
        assert client._event_queue.qsize() == 0

    async def test_the_loop_idles_while_disconnected(self, client: SensiClient) -> None:
        """With no socket the loop waits instead of spinning or crashing."""
        client._sio = None

        await client._send_event("get_info", {"icd_id": ICD_ID})
        await self._run_once(client)

        # Nothing was consumed - the event is still waiting for a connection.
        assert client._event_queue.qsize() == 1


class TestCirculatingFan:
    """Capability gating on the circulating fan setter."""

    async def test_setting_it_on_an_incapable_device_raises(
        self, client: SensiClient, mock_device
    ) -> None:
        """The thermostat is never asked for something it cannot do."""
        mock_device.capabilities.circulating_fan.capable = False

        with pytest.raises(HomeAssistantError, match="does not support it"):
            await client.async_set_circulating_fan_mode(mock_device, True, 20)


class TestTokenRefresh:
    """`try_refresh_access_token` and the refresh branches of `_connect`."""

    async def test_a_refresh_failure_becomes_a_connection_error(
        self, client: SensiClient
    ) -> None:
        """Whatever the auth layer raises is normalised for the caller."""
        with (
            patch(
                "custom_components.sensi.client.refresh_access_token",
                side_effect=RuntimeError("no network"),
            ),
            pytest.raises(SensiConnectionError, match="Error refreshing tokens"),
        ):
            await client.try_refresh_access_token()

    async def test_an_expired_token_is_refreshed_before_connecting(
        self, hass: HomeAssistant
    ) -> None:
        """A token known to be expired is renewed rather than tried and failed."""
        expired = AuthenticationConfig(
            refresh_token="refresh",
            access_token="access",
            expires_at=0,
            user_id="user",
        )
        client = SensiClient(hass, expired)
        fresh = AuthenticationConfig(
            refresh_token="refresh2",
            access_token="access2",
            expires_at=99_999_999_999,
            user_id="user",
        )

        with (
            patch(
                "custom_components.sensi.client.refresh_access_token",
                AsyncMock(return_value=fresh),
            ) as mock_refresh,
            patch.object(client, "_connect_client", AsyncMock()),
            patch("custom_components.sensi.client.socketio.AsyncClient", MagicMock()),
        ):
            await client._connect()

        mock_refresh.assert_awaited_once()

    async def test_a_connection_timeout_is_wrapped(self, client: SensiClient) -> None:
        """A timeout reaching Sensi is reported as a connection error."""
        with (
            patch.object(
                client, "_connect_client", AsyncMock(side_effect=TimeoutError)
            ),
            patch("custom_components.sensi.client.socketio.AsyncClient", MagicMock()),
            pytest.raises(
                SensiConnectionError, match="Timed out making the connection"
            ),
        ):
            await client._connect()

    async def test_an_unexpected_failure_is_wrapped(self, client: SensiClient) -> None:
        """Anything else is still a connection error, never a bare exception."""
        with (
            patch.object(
                client, "_connect_client", AsyncMock(side_effect=RuntimeError("odd"))
            ),
            patch("custom_components.sensi.client.socketio.AsyncClient", MagicMock()),
            pytest.raises(SensiConnectionError, match="Failed to connect"),
        ):
            await client._connect()


class TestGetErrorDescriptionFromEventCallback:
    """Test cases for get_error_description_from_event_callback."""

    def test_none(self):
        """No error payload yields an empty description."""
        assert get_error_description_from_event_callback(None) == ""

    def test_empty_dict(self):
        """An empty payload yields an empty description."""
        assert get_error_description_from_event_callback({}) == ""

    def test_description_with_icd_id(self):
        """The description is read out of the nested error object."""
        error = {
            "error": {"description": "InvalidScale"},
            "icd_id": "36-6f-92-ff-fe-02-24-b7",
        }
        assert get_error_description_from_event_callback(error) == "InvalidScale"

    def test_description_without_icd_id(self):
        """A payload with no icd_id still yields its description."""
        assert (
            get_error_description_from_event_callback(
                {"error": {"description": "Forbidden"}}
            )
            == "Forbidden"
        )

    def test_error_object_without_description(self):
        """An error object with no description yields an empty string."""
        assert get_error_description_from_event_callback({"error": {}}) == ""

    def test_payload_without_error_key(self):
        """A payload with no error key yields an empty string."""
        assert get_error_description_from_event_callback({"icd_id": "abc"}) == ""


class TestIsTokenExpired:
    """Test cases for is_token_expired."""

    def test_jwt_expired(self):
        """The documented expiry message is recognized."""
        assert is_token_expired({"message": "jwt expired"}) is True

    def test_other_message(self):
        """An unrelated message is not an expiry."""
        assert is_token_expired({"message": "Unauthorized"}) is False

    def test_empty_dict(self):
        """An empty dict is not an expiry."""
        assert not is_token_expired({})

    def test_none(self):
        """A missing payload is not an expiry."""
        assert is_token_expired(None) is False

    def test_non_dict(self):
        """A non-dict payload (socketio can hand back a string) is not an expiry."""
        assert is_token_expired("jwt expired") is False


class TestExtractIcdId:
    """Test cases for extract_icd_id."""

    def test_present(self):
        """The icd_id is returned when present."""
        assert extract_icd_id({"icd_id": "36-6f-92-ff-fe-0c-0b-07"}) == (
            "36-6f-92-ff-fe-0c-0b-07"
        )

    def test_absent(self):
        """A payload without an icd_id yields an empty string."""
        assert extract_icd_id({"state": {}}) == ""

    def test_none(self):
        """A missing payload yields an empty string."""
        assert extract_icd_id(None) == ""

    def test_empty_dict(self):
        """An empty payload yields an empty string."""
        assert extract_icd_id({}) == ""
