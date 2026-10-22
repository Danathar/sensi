"""End-to-end coverage of a set_temperature the thermostat drops (#634).

The backend acks a write, the integration shows the new setpoint, and a few
minutes later a ``state`` event reports the value from before the write. The
client treats that as a dropped write once a second refresh confirms it,
re-sends it once and says so in the log. These tests script the drop by
putting the old setpoint back into what ``FakeSensiBackend`` serves, then
pushing a ``state`` event or running a refresh.

One report of the old value is not enough (#638): right after a write the
backend can serve the previous value for one refresh. A revert counts once
it is reported again on another connection, SETPOINT_REVERT_CONFIRM later.
"""

import asyncio
from collections.abc import Iterator
import logging
import time
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensi.client import SensiClient
from custom_components.sensi.data import OperatingMode
from custom_components.sensi.setpoint_watch import (
    SETPOINT_CONFIRM_WINDOW,
    SETPOINT_REVERT_CONFIRM,
)
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant

from .conftest import FakeSensiBackend, FakeSensiSocket

CLIMATE = "climate.sensi_living_room"
ICD_ID = "aa-bb-cc-dd-ee-ff-00-01"

# The sample thermostat is in HEAT with heat setpoint 68 and cool setpoint 76.
OLD_HEAT = 68

# A value set at the wall or in the app: neither the old nor the requested one.
BY_HAND = 65

FORBIDDEN = ({"error": {"description": "Forbidden"}},)


class _Clock:
    """The watch's clock, moved only when a test moves it.

    It starts on a whole second. The watch compares differences against
    SETPOINT_REVERT_CONFIRM with >=, and from a fractional start such as
    497.4454882892299 the sum t + 20 rounds so that (t + 20) - t is
    19.999999999999943: the revert is never confirmed and the test fails
    depending on the runner's uptime. Whole seconds plus the whole and half
    seconds the tests advance by stay exact.
    """

    def __init__(self) -> None:
        """Start at the real monotonic time, rounded down to a whole second."""
        self.now = float(int(time.monotonic()))

    def __call__(self) -> float:
        """Return the current time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += seconds


@pytest.fixture(autouse=True)
def clock() -> Iterator[_Clock]:
    """Patch setpoint_watch's clock for every test in this module."""
    moving = _Clock()
    with patch("custom_components.sensi.setpoint_watch._monotonic", new=moving):
        yield moving


def _client(entry: MockConfigEntry) -> SensiClient:
    return entry.runtime_data.client


def _set_temperatures(backend: FakeSensiBackend) -> list[dict]:
    """Return every set_temperature payload that reached the wire, in order."""
    return [data for name, data in backend.emitted if name == "set_temperature"]


async def _set_temperature(hass: HomeAssistant, **arguments) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, **arguments},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _report(backend: FakeSensiBackend, **state) -> None:
    """Have the thermostat report these state fields in a ``state`` event."""
    backend.devices[ICD_ID]["state"].update(state)
    await backend.sockets[-1].deliver("state", backend.state_payload())


async def _refresh(client: SensiClient) -> None:
    """Run the coordinator's refresh: a new connection and the state it reports."""
    await asyncio.wait_for(client.async_update_devices(), 3)


async def _confirm_revert(
    backend: FakeSensiBackend,
    client: SensiClient,
    clock: _Clock,
    *,
    then_disconnect: bool = False,
) -> None:
    """Report the old heat setpoint now, then again on the next refresh.

    The second report is SETPOINT_REVERT_CONFIRM after the first and on a
    new connection, so it confirms the revert and starts the re-send. With
    `then_disconnect` the new socket goes down as soon as that state has been
    checked, so the re-send stays queued, not yet on the wire.
    """
    await _report(backend, current_heat_temp=OLD_HEAT)
    assert _no_resend(client), "one report of the old value re-sent"
    clock.advance(SETPOINT_REVERT_CONFIRM)

    if not then_disconnect:
        await _refresh(client)
        return

    watch = client._setpoint_watch  # noqa: SLF001
    check = watch.check

    def check_then_disconnect(device) -> None:
        check(device)
        backend.sockets[-1].connected = False

    with patch.object(watch, "check", new=check_then_disconnect):
        await _refresh(client)


async def _wait_for_resend(client: SensiClient) -> None:
    """Wait for the re-send the last ``state`` event started to be answered."""
    tasks = list(client._setpoint_watch._resend_tasks.values())  # noqa: SLF001
    assert tasks, "no re-send was started"
    await asyncio.wait_for(asyncio.gather(*tasks), 5)


def _no_resend(client: SensiClient) -> bool:
    return not client._setpoint_watch._resend_tasks  # noqa: SLF001


def _pending(client: SensiClient) -> set:
    return {mode for _, mode in client._setpoint_watch._pending}  # noqa: SLF001


def _warnings(caplog, text: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and text in record.getMessage()
    ]


def _debugs(caplog, text: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.DEBUG and text in record.getMessage()
    ]


def _heat_setpoint(client: SensiClient) -> int:
    """Return the heat setpoint the integration holds for the thermostat."""
    return client._devices[ICD_ID].state.current_heat_temp  # noqa: SLF001


async def test_a_reverted_setpoint_is_resent_once_with_the_same_payload(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
    caplog,
) -> None:
    """The reproduction from #634: ack 72, then the thermostat reports 68."""
    caplog.set_level(logging.DEBUG, logger="custom_components.sensi")
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    assert len(_set_temperatures(sensi_backend)) == 1

    await _confirm_revert(sensi_backend, client, clock)
    await _wait_for_resend(client)
    await hass.async_block_till_done()

    first, resent = _set_temperatures(sensi_backend)
    assert resent == first
    assert sensi_backend.last_emitted("set_temperature")["target_temp"] == 72
    assert hass.states.get(CLIMATE).attributes["temperature"] == 72

    [warning] = _warnings(caplog, "resending")
    assert f"reported heat setpoint {OLD_HEAT}" in warning
    assert "after accepting 72" in warning
    assert "attempt 2 of 2" in warning
    assert "status online" in warning

    [first_report] = _debugs(caplog, "First report of the pre-write")
    assert f"heat setpoint {OLD_HEAT}" in first_report
    assert "waiting for a second before resending" in first_report

    # The new lines name the thermostat only in redacted form.
    messages = [record.getMessage() for record in caplog.records]
    assert any("Accepted set_temperature" in message for message in messages)
    for message in messages:
        if "setpoint" in message:
            assert ICD_ID not in message


async def test_a_revert_after_the_resend_gives_up(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
    caplog,
) -> None:
    """A second confirmed revert is logged and left alone: no third emit."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _confirm_revert(sensi_backend, client, clock)
    await _wait_for_resend(client)

    # One report after the re-send is not yet a second drop.
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
    assert _pending(client) == {OperatingMode.HEAT}
    assert not _warnings(caplog, "not retrying")

    clock.advance(SETPOINT_REVERT_CONFIRM)
    await _refresh(client)
    assert _no_resend(client)
    await hass.async_block_till_done()

    assert len(_set_temperatures(sensi_backend)) == 2
    assert _pending(client) == set()
    [warning] = _warnings(caplog, "not retrying")
    assert f"still reports heat setpoint {OLD_HEAT} after resend" in warning


async def test_a_third_value_is_not_overwritten(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Someone set 65 at the wall: that is a change, not a dropped write."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, current_heat_temp=65)

    assert _no_resend(client)
    assert _pending(client) == set()
    assert len(_set_temperatures(sensi_backend)) == 1


async def test_the_requested_value_is_not_taken_as_confirmation(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """A state echoing 72 keeps watching; a later revert is still caught."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, current_heat_temp=72)
    assert _no_resend(client)
    assert _pending(client) == {OperatingMode.HEAT}

    await _confirm_revert(sensi_backend, client, clock)
    await _wait_for_resend(client)
    assert len(_set_temperatures(sensi_backend)) == 2


async def test_a_revert_after_the_window_is_not_resent(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """Past SETPOINT_CONFIRM_WINDOW the old value is a change like any other."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})

    clock.advance(SETPOINT_CONFIRM_WINDOW + 1)
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)

    assert _no_resend(client)
    assert _pending(client) == set()
    assert len(_set_temperatures(sensi_backend)) == 1


async def test_a_first_report_too_late_to_confirm_goes_with_the_window(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """A first report near the end of the window is not confirmed after it."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})

    clock.advance(SETPOINT_CONFIRM_WINDOW - SETPOINT_REVERT_CONFIRM / 2)
    await _confirm_revert(sensi_backend, client, clock)

    assert _no_resend(client)
    assert _pending(client) == set()
    assert len(_set_temperatures(sensi_backend)) == 1


async def test_a_newer_write_replaces_the_record(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """72 then 71: a revert to 68 does not send 72 again."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _set_temperature(hass, **{ATTR_TEMPERATURE: 71})
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)

    assert _no_resend(client)
    assert [p["target_temp"] for p in _set_temperatures(sensi_backend)] == [72, 71]


async def test_a_mode_change_drops_the_record(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """The heat setpoint was sent for HEAT; in COOL it is not re-sent."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, operating_mode="cool", current_heat_temp=OLD_HEAT)

    assert _no_resend(client)
    assert _pending(client) == set()
    assert len(_set_temperatures(sensi_backend)) == 1


async def test_off_drops_the_record(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """A thermostat turned off is not sent a setpoint."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, operating_mode="off", current_heat_temp=OLD_HEAT)

    assert _no_resend(client)
    assert _pending(client) == set()
    assert len(_set_temperatures(sensi_backend)) == 1


async def test_offline_waits_and_resends_once_online_inside_the_window(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """An offline report is no evidence; the online ones that follow are."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, status="offline", current_heat_temp=OLD_HEAT)
    assert _no_resend(client)
    assert _pending(client) == {OperatingMode.HEAT}

    # Online on the next refresh: the first report that counts.
    clock.advance(SETPOINT_REVERT_CONFIRM)
    sensi_backend.devices[ICD_ID]["state"]["status"] = "online"
    await _refresh(client)
    assert _no_resend(client), "the offline report was counted"

    clock.advance(SETPOINT_REVERT_CONFIRM)
    await _refresh(client)
    await _wait_for_resend(client)
    assert len(_set_temperatures(sensi_backend)) == 2


async def test_offline_past_the_window_is_not_resent(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """Back online after the window: the record is gone, nothing is sent."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, status="offline", current_heat_temp=OLD_HEAT)

    clock.advance(SETPOINT_CONFIRM_WINDOW + 1)
    await _report(sensi_backend, status="online")

    assert _no_resend(client)
    assert _pending(client) == set()
    assert len(_set_temperatures(sensi_backend)) == 1


async def test_auto_tracks_heat_and_cool_on_their_own(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """Only the heat setpoint reverts, so only the heat setpoint is re-sent."""
    client = _client(sensi_entry)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.AUTO},
        blocking=True,
    )
    await hass.async_block_till_done()
    sensi_backend.devices[ICD_ID]["state"]["operating_mode"] = "auto"

    await _set_temperature(
        hass, **{ATTR_TARGET_TEMP_LOW: 66, ATTR_TARGET_TEMP_HIGH: 78}
    )
    heat, cool = _set_temperatures(sensi_backend)
    assert (heat["mode"], cool["mode"]) == ("heat", "cool")
    assert _pending(client) == {OperatingMode.HEAT, OperatingMode.COOL}

    # The cool setpoint stays at the 78 the backend applied.
    await _confirm_revert(sensi_backend, client, clock)
    await _wait_for_resend(client)

    assert _set_temperatures(sensi_backend) == [heat, cool, heat]
    assert _pending(client) == {OperatingMode.HEAT, OperatingMode.COOL}


async def test_a_revert_delivered_under_the_refresh_lock_resends(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """The coordinator's reconnect delivers state while it holds the lock.

    _update_state runs inside that, so a re-send that awaited the setter or
    took _reconnect_lock there would wedge the refresh. It is started as a
    background task instead, and both the refresh and the re-send finish.
    """
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
    clock.advance(SETPOINT_REVERT_CONFIRM)
    sensi_backend.state_before_connect_returns = True

    lock_held: list[bool] = []
    check = client._setpoint_watch.check  # noqa: SLF001

    def check_and_record(device) -> None:
        lock_held.append(client._reconnect_lock.locked())  # noqa: SLF001
        check(device)

    with patch.object(client._setpoint_watch, "check", new=check_and_record):
        await asyncio.wait_for(client.async_update_devices(), 3)

    assert lock_held == [True]
    await _wait_for_resend(client)
    assert len(_set_temperatures(sensi_backend)) == 2


async def test_stop_cancels_a_resend_in_flight(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """Unloading the entry does not leave a re-send behind to go out later."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    # Down, so the re-send is still waiting for the wire when stop() runs.
    await _confirm_revert(sensi_backend, client, clock, then_disconnect=True)
    [resend] = client._setpoint_watch._resend_tasks.values()  # noqa: SLF001

    await client.stop()

    assert resend.cancelled()
    assert _no_resend(client)
    assert _pending(client) == set()
    assert len(_set_temperatures(sensi_backend)) == 1


async def test_a_stale_read_does_not_spend_the_resend(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """Old, then requested, then a real revert: still exactly one re-send (#638).

    The first report of 68 is a stale read; the next refresh reports 72. A
    revert confirmed after that is a real drop and is recovered.
    """
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    [first] = _set_temperatures(sensi_backend)

    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
    clock.advance(30)
    sensi_backend.devices[ICD_ID]["state"]["current_heat_temp"] = 72
    await _refresh(client)
    assert _no_resend(client)

    clock.advance(30)
    await _confirm_revert(sensi_backend, client, clock)
    await _wait_for_resend(client)

    assert _set_temperatures(sensi_backend) == [first, first]
    assert _pending(client) == {OperatingMode.HEAT}


async def test_one_old_report_then_the_requested_value_is_not_resent(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """A stale read followed by 72 for the rest of the window sends nothing."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    accepted_at = clock.now
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)

    sensi_backend.devices[ICD_ID]["state"]["current_heat_temp"] = 72
    while clock.now - accepted_at < SETPOINT_CONFIRM_WINDOW:
        clock.advance(30)
        await _refresh(client)
        assert _no_resend(client)

    assert len(_set_temperatures(sensi_backend)) == 1


async def test_two_old_reports_on_one_connection_are_one_read(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """Repeated `state` packets from one connection do not confirm a revert."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
    clock.advance(SETPOINT_REVERT_CONFIRM + 5)
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
    assert _no_resend(client)

    await _report(sensi_backend, current_heat_temp=72)
    clock.advance(30)
    await _refresh(client)

    assert _no_resend(client)
    assert _pending(client) == {OperatingMode.HEAT}
    assert len(_set_temperatures(sensi_backend)) == 1


async def test_two_refreshes_close_together_are_not_confirmation(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """A reconnect seconds after the first report is too soon to count.

    A refused setter's recovery can reconnect just before the coordinator's
    refresh, and both can serve the same stale read.
    """
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
    clock.advance(5)
    await _refresh(client)
    assert _no_resend(client)

    clock.advance(SETPOINT_REVERT_CONFIRM)
    await _refresh(client)
    await _wait_for_resend(client)
    assert len(_set_temperatures(sensi_backend)) == 2


async def test_a_queued_resend_is_withdrawn_by_a_third_value(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
    caplog,
) -> None:
    """A hand change while the re-send waits for the wire: nothing is sent."""
    caplog.set_level(logging.DEBUG, logger="custom_components.sensi")
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _confirm_revert(sensi_backend, client, clock, then_disconnect=True)
    [resend] = client._setpoint_watch._resend_tasks.values()  # noqa: SLF001

    # The next connection reports 65 before the emit loop can drain the queue.
    sensi_backend.devices[ICD_ID]["state"]["current_heat_temp"] = BY_HAND
    sensi_backend.state_before_connect_returns = True
    await _refresh(client)
    await asyncio.wait_for(resend, 5)

    assert len(_set_temperatures(sensi_backend)) == 1
    assert _pending(client) == set()
    assert _heat_setpoint(client) == BY_HAND
    [discarded] = _debugs(caplog, "Discarding queued resend")
    assert "heat setpoint 72" in discarded
    assert "no longer wanted (other value)" in discarded
    assert ICD_ID not in discarded


async def test_a_third_value_during_the_forbidden_recovery_stops_the_retry(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
    caplog,
) -> None:
    """The token refresh completes; the refused re-send is not emitted again."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    sensi_backend.acks["set_temperature"] = FORBIDDEN

    async def refresh_while_set_by_hand() -> None:
        sensi_backend.devices[ICD_ID]["state"]["current_heat_temp"] = BY_HAND
        await sensi_backend.sockets[-1].deliver("state", sensi_backend.state_payload())

    refresh = AsyncMock(side_effect=refresh_while_set_by_hand)
    with patch.object(client, "try_refresh_access_token", refresh):
        await _confirm_revert(sensi_backend, client, clock)
        await _wait_for_resend(client)

    refresh.assert_awaited_once()
    assert len(_set_temperatures(sensi_backend)) == 2
    assert _pending(client) == set()
    assert _heat_setpoint(client) == BY_HAND
    assert not _warnings(caplog, "retried once")
    assert not _warnings(caplog, "was refused")


async def test_a_third_value_after_the_resend_is_emitted_is_not_overwritten(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    clock: _Clock,
) -> None:
    """A hand change between the re-send's emit and its ack.

    The emit cannot be taken back, so the thermostat does get 72 again.
    What this pins is the local side: the ack is not applied over the 65
    the hand change reported.
    """
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    emit = FakeSensiSocket.emit

    async def emit_then_set_by_hand(
        socket: FakeSensiSocket, name, data=None, namespace=None, callback=None
    ) -> None:
        await emit(socket, name, data, namespace, callback)
        if name == "set_temperature":
            sensi_backend.devices[ICD_ID]["state"]["current_heat_temp"] = BY_HAND
            await socket.deliver("state", sensi_backend.state_payload())

    with patch.object(FakeSensiSocket, "emit", emit_then_set_by_hand):
        await _confirm_revert(sensi_backend, client, clock)
        await _wait_for_resend(client)
        await hass.async_block_till_done()

    first, resent = _set_temperatures(sensi_backend)
    assert resent == first
    assert _pending(client) == set()
    assert _heat_setpoint(client) == BY_HAND
