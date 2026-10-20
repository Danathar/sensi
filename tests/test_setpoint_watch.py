"""Tests for the dropped-setpoint watch, driven without a socket.

tests/e2e/test_dropped_setpoint.py covers the watch through SensiClient and the
fake backend. These hold SetpointWatch to the collaborators it is given: the
setter it re-sends through, the callable that applies a value, and the
callback that runs once a re-send has been applied.
"""

import asyncio
from collections.abc import Callable, Iterator
import copy
import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from custom_components.sensi.client import SETTER_NOT_WANTED, ActionResponse
from custom_components.sensi.data import OperatingMode, SensiDevice
from custom_components.sensi.setpoint_watch import (
    SETPOINT_CONFIRM_WINDOW,
    SETPOINT_REVERT_CONFIRM,
    SetpointWatch,
    reported_setpoint,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

# tests/sample.json: HEAT, heat setpoint 68, cool setpoint 76, online.
OLD_HEAT = 68
REQUEST = {"icd_id": "redacted", "mode": "heat", "target_temp": 72}


class _Clock:
    """The watch's clock, moved only when a test moves it."""

    def __init__(self) -> None:
        """Start at the real monotonic time, in whole seconds.

        Whole seconds keep `advance` exact. From a fractional start, adding
        SETPOINT_REVERT_CONFIRM can cross a power of two and round down, so
        a gap of exactly SETPOINT_REVERT_CONFIRM reads as just under it.
        """
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


def _watch(
    hass: HomeAssistant,
    device: SensiDevice,
    response: ActionResponse | Exception,
    devices: dict | None = None,
) -> tuple[SetpointWatch, list, MagicMock, MagicMock]:
    """Return a watch whose setter answers with `response`, and its fakes.

    `response` may also be a callable, called with the watch's `still_wanted`
    predicate, for a setter whose answer depends on it.
    """
    sent: list = []

    async def resend(
        event: str, data: dict, still_wanted: Callable[[], bool]
    ) -> ActionResponse:
        sent.append((event, data))
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(still_wanted)
        return response

    apply = MagicMock()
    on_applied = MagicMock()
    watch = SetpointWatch(
        hass,
        {device.identifier: device} if devices is None else devices,
        resend=resend,
        apply=apply,
        on_applied=on_applied,
    )
    return watch, sent, apply, on_applied


def _track_heat(
    watch: SetpointWatch, device: SensiDevice, previous: int | None = OLD_HEAT
) -> None:
    """Track an accepted HEAT write of 72 over `previous`."""
    mode = OperatingMode.HEAT
    watch.track(device.identifier, mode, 72, previous, mode, REQUEST)


def _warnings(caplog, text: str) -> list[str]:
    """Return every WARNING message captured so far that contains `text`."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and text in record.getMessage()
    ]


def _confirm_revert(watch: SetpointWatch, device: SensiDevice, clock: _Clock) -> None:
    """Have `check` see the old value on two connections, far enough apart."""
    watch.on_connect()
    watch.check(device)
    assert not watch._resend_tasks, "one report of the old value re-sent"
    clock.advance(SETPOINT_REVERT_CONFIRM)
    watch.on_connect()
    watch.check(device)


async def _resend_once(
    watch: SetpointWatch, device: SensiDevice, clock: _Clock
) -> None:
    """Have `check` confirm the old value and wait for the re-send it starts."""
    _confirm_revert(watch, device, clock)
    tasks = list(watch._resend_tasks.values())
    assert tasks, "no re-send was started"
    await asyncio.gather(*tasks)


def test_reported_setpoint_reads_the_setpoint_for_the_mode(
    mock_device: SensiDevice,
) -> None:
    """Heat and cool have a setpoint; any other mode has none."""
    state = mock_device.state
    assert reported_setpoint(state, OperatingMode.HEAT) == OLD_HEAT
    assert reported_setpoint(state, OperatingMode.COOL) == 76
    assert reported_setpoint(state, OperatingMode.OFF) is None


async def test_a_write_with_nothing_to_tell_a_drop_by_is_not_watched(
    hass: HomeAssistant, mock_device: SensiDevice
) -> None:
    """No earlier value, or the value already shown, cannot be told from a drop."""
    watch, *_ = _watch(hass, mock_device, ActionResponse(None, {}))

    _track_heat(watch, mock_device, previous=None)
    _track_heat(watch, mock_device, previous=72)

    assert not watch.watching


async def test_repeating_a_watched_write_keeps_its_earlier_value(
    hass: HomeAssistant, mock_device: SensiDevice
) -> None:
    """72 again over a shown 72 is still watched against the 68 before it."""
    watch, *_ = _watch(hass, mock_device, ActionResponse(None, {}))

    _track_heat(watch, mock_device)
    _track_heat(watch, mock_device, previous=72)

    [record] = watch._pending.values()
    assert record.previous == OLD_HEAT


async def test_an_accepted_resend_is_applied_and_reported(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock
) -> None:
    """The same payload goes out again, then apply and on_applied run."""
    watch, sent, apply, on_applied = _watch(hass, mock_device, ActionResponse(None, {}))
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device, clock)

    assert sent == [("set_temperature", REQUEST)]
    apply.assert_called_once_with(mock_device, OperatingMode.HEAT, 72)
    on_applied.assert_called_once_with()
    [record] = watch._pending.values()
    assert record.attempts == 2


async def test_an_accepted_resend_for_a_device_now_gone_still_reports(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock
) -> None:
    """Nothing to apply the value to, but the listeners are still told."""
    watch, _sent, apply, on_applied = _watch(
        hass, mock_device, ActionResponse(None, {}), devices={}
    )
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device, clock)

    apply.assert_not_called()
    on_applied.assert_called_once_with()


async def test_a_refused_resend_is_dropped(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock, caplog
) -> None:
    """A refusal is logged once and the setpoint is no longer watched."""
    watch, _sent, apply, on_applied = _watch(
        hass, mock_device, ActionResponse("OutOfRange", None)
    )
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device, clock)

    assert not watch.watching
    apply.assert_not_called()
    on_applied.assert_not_called()
    assert _warnings(caplog, "was refused with 'OutOfRange'")


async def test_a_resend_that_raises_is_read_as_a_refusal(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock, caplog
) -> None:
    """A HomeAssistantError from the setter is the refusal's message."""
    watch, _sent, apply, on_applied = _watch(
        hass, mock_device, HomeAssistantError("Thermostat offline")
    )
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device, clock)

    assert not watch.watching
    apply.assert_not_called()
    on_applied.assert_not_called()
    assert _warnings(caplog, "was refused with 'Thermostat offline'")


async def test_a_stopped_watch_forgets_and_ignores_state(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock
) -> None:
    """After stop() nothing is watched and a revert starts no re-send."""
    watch, sent, _apply, _on_applied = _watch(
        hass, mock_device, ActionResponse(None, {})
    )
    _track_heat(watch, mock_device)

    await watch.stop()
    assert not watch.watching

    # A revert confirmed on two connections would start a re-send on a
    # running watch.
    _track_heat(watch, mock_device)
    _confirm_revert(watch, mock_device, clock)

    assert not watch._resend_tasks
    assert sent == []


async def test_a_revert_needs_a_second_report_on_another_connection(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock
) -> None:
    """One read, however many packets carry it, is not a dropped write (#638)."""
    watch, sent, *_ = _watch(hass, mock_device, ActionResponse(None, {}))
    _track_heat(watch, mock_device)

    watch.on_connect()
    watch.check(mock_device)
    clock.advance(SETPOINT_REVERT_CONFIRM * 2)
    # The same connection again, long after: still the one read.
    watch.check(mock_device)
    assert not watch._resend_tasks

    watch.on_connect()
    watch.check(mock_device)
    await asyncio.gather(*watch._resend_tasks.values())
    assert sent == [("set_temperature", REQUEST)]


async def test_a_second_connection_too_soon_is_not_confirmation(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock
) -> None:
    """Two connections seconds apart can carry the same stale read."""
    watch, *_ = _watch(hass, mock_device, ActionResponse(None, {}))
    _track_heat(watch, mock_device)

    watch.on_connect()
    watch.check(mock_device)
    clock.advance(SETPOINT_REVERT_CONFIRM - 1)
    watch.on_connect()
    watch.check(mock_device)
    assert not watch._resend_tasks

    clock.advance(1)
    watch.on_connect()
    watch.check(mock_device)
    assert watch._resend_tasks
    await asyncio.gather(*watch._resend_tasks.values())


async def test_the_requested_value_clears_a_first_report(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock
) -> None:
    """Old, then requested: the old report was stale and is forgotten."""
    watch, *_ = _watch(hass, mock_device, ActionResponse(None, {}))
    _track_heat(watch, mock_device)

    watch.on_connect()
    watch.check(mock_device)
    [record] = watch._pending.values()
    assert record.revert_seen_at is not None

    mock_device.state.current_heat_temp = 72
    watch.check(mock_device)
    assert record.revert_seen_at is None
    assert record.revert_seen_on is None

    mock_device.state.current_heat_temp = OLD_HEAT
    clock.advance(SETPOINT_REVERT_CONFIRM)
    watch.on_connect()
    watch.check(mock_device)
    assert not watch._resend_tasks


async def test_the_resend_is_offered_a_predicate_that_follows_the_record(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock
) -> None:
    """still_wanted is true while the record is watched, false once replaced."""
    predicates: list[Callable[[], bool]] = []

    def answer(still_wanted: Callable[[], bool]) -> ActionResponse:
        predicates.append(still_wanted)
        return ActionResponse(None, {})

    watch, *_ = _watch(hass, mock_device, answer)
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device, clock)

    [still_wanted] = predicates
    assert still_wanted()
    watch.track(
        mock_device.identifier, OperatingMode.HEAT, 71, 72, OperatingMode.HEAT, {}
    )
    assert not still_wanted()


async def test_a_withdrawn_resend_applies_nothing_and_says_why(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock, caplog
) -> None:
    """A re-send the setter did not send because its record went (#638)."""
    caplog.set_level(logging.DEBUG, logger="custom_components.sensi")
    watch: SetpointWatch

    def withdrawn(still_wanted: Callable[[], bool]) -> ActionResponse:
        # Someone set 65 by hand while the re-send waited for the wire.
        mock_device.state.current_heat_temp = 65
        watch.check(mock_device)
        assert not still_wanted()
        return ActionResponse(SETTER_NOT_WANTED, None, sent=False)

    watch, _sent, apply, on_applied = _watch(hass, mock_device, withdrawn)
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device, clock)

    assert not watch.watching
    apply.assert_not_called()
    on_applied.assert_not_called()
    assert not _warnings(caplog, "was refused")
    [message] = [
        record.getMessage()
        for record in caplog.records
        if "Discarding queued resend" in record.getMessage()
    ]
    assert "heat setpoint 72" in message
    assert "no longer wanted (other value)" in message
    assert mock_device.identifier not in message


async def test_a_write_in_a_mode_without_a_setpoint_is_not_watched(
    hass: HomeAssistant, mock_device: SensiDevice
) -> None:
    """A write sent in a mode that holds no setpoint has nothing to compare."""
    watch, *_ = _watch(hass, mock_device, ActionResponse(None, {}))

    mode = OperatingMode.UNKNOWN
    watch.track(mock_device.identifier, mode, 72, OLD_HEAT, mode, REQUEST)

    assert not watch.watching


async def test_another_thermostats_state_leaves_the_record_alone(
    hass: HomeAssistant, mock_json: dict, mock_device: SensiDevice, clock: _Clock
) -> None:
    """Only the thermostat a write went to can confirm or contradict it."""
    other_json = copy.deepcopy(mock_json)
    other_json["icd_id"] = "aa-bb-cc-dd-ee-ff-00-02"
    _have_state, other = SensiDevice.create(other_json)
    watch, sent, *_ = _watch(hass, mock_device, ActionResponse(None, {}))
    _track_heat(watch, mock_device)
    [record] = watch._pending.values()

    # The other thermostat shows the old value on two refreshes...
    _confirm_revert(watch, other, clock)
    # ...and then a value of its own.
    other.state.current_heat_temp = 65
    watch.check(other)

    assert not watch._resend_tasks
    assert sent == []
    assert watch._pending == {(mock_device.identifier, OperatingMode.HEAT): record}
    assert record.revert_seen_at is None
    assert record.ended is None


async def test_a_state_without_the_setpoint_keeps_the_record(
    hass: HomeAssistant, mock_device: SensiDevice
) -> None:
    """A state event with no heat setpoint in it says nothing about the write."""
    watch, *_ = _watch(hass, mock_device, ActionResponse(None, {}))
    _track_heat(watch, mock_device)
    [record] = watch._pending.values()

    mock_device.state.current_heat_temp = None
    watch.on_connect()
    watch.check(mock_device)

    assert watch._pending == {(mock_device.identifier, OperatingMode.HEAT): record}
    assert record.ended is None
    assert record.revert_seen_at is None


async def test_a_revert_while_the_resend_is_in_flight_starts_no_second(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock
) -> None:
    """More reports of the old value during a re-send do not send it again."""
    release = asyncio.Event()
    sent: list = []

    async def resend(
        event: str, data: dict, still_wanted: Callable[[], bool]
    ) -> ActionResponse:
        sent.append((event, data))
        await release.wait()
        return ActionResponse(None, {})

    watch = SetpointWatch(
        hass,
        {mock_device.identifier: mock_device},
        resend=resend,
        apply=MagicMock(),
        on_applied=MagicMock(),
    )
    _track_heat(watch, mock_device)

    _confirm_revert(watch, mock_device, clock)
    [first] = watch._resend_tasks.values()
    await asyncio.sleep(0)
    clock.advance(SETPOINT_REVERT_CONFIRM)
    watch.on_connect()
    watch.check(mock_device)
    await asyncio.sleep(0)

    assert list(watch._resend_tasks.values()) == [first]
    release.set()
    await first
    assert sent == [("set_temperature", REQUEST)]
    [record] = watch._pending.values()
    assert record.attempts == 2


async def test_a_revert_confirmed_at_the_end_of_the_window_is_resent(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock
) -> None:
    """The window is inclusive: a confirmation exactly at its end still counts."""
    watch, sent, *_ = _watch(hass, mock_device, ActionResponse(None, {}))
    _track_heat(watch, mock_device)

    clock.advance(SETPOINT_CONFIRM_WINDOW - SETPOINT_REVERT_CONFIRM)
    await _resend_once(watch, mock_device, clock)

    assert sent == [("set_temperature", REQUEST)]


async def test_an_accepted_resend_starts_a_new_window(
    hass: HomeAssistant, mock_device: SensiDevice, clock: _Clock, caplog
) -> None:
    """A re-send is watched for a full window from when it was accepted.

    Reported dropped again after the first write's window has passed, but
    inside the re-send's, it is logged and given up on - not let go of as if
    the window had run out.
    """
    watch, sent, *_ = _watch(hass, mock_device, ActionResponse(None, {}))
    _track_heat(watch, mock_device)

    clock.advance(SETPOINT_CONFIRM_WINDOW - SETPOINT_REVERT_CONFIRM - 10)
    await _resend_once(watch, mock_device, clock)
    assert sent == [("set_temperature", REQUEST)]
    # Let the finished re-send's done callback forget it.
    await asyncio.sleep(0)

    # Past the first write's window, inside the re-send's.
    clock.advance(SETPOINT_REVERT_CONFIRM)
    _confirm_revert(watch, mock_device, clock)

    assert not watch.watching
    assert sent == [("set_temperature", REQUEST)]
    [message] = _warnings(caplog, "after resend; not retrying")
    assert f"({SETPOINT_REVERT_CONFIRM * 2}s after accepting 72" in message
