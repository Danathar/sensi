"""Tests for the dropped-setpoint watch, driven without a socket.

tests/e2e/test_dropped_setpoint.py covers the watch through SensiClient and the
fake backend. These hold SetpointWatch to the collaborators it is given: the
setter it re-sends through, the callable that applies a value, and the
callback that runs once a re-send has been applied.
"""

import asyncio
import logging
from unittest.mock import MagicMock

from custom_components.sensi.client import ActionResponse
from custom_components.sensi.data import OperatingMode, SensiDevice
from custom_components.sensi.setpoint_watch import SetpointWatch, reported_setpoint
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

# tests/sample.json: HEAT, heat setpoint 68, cool setpoint 76, online.
OLD_HEAT = 68
REQUEST = {"icd_id": "redacted", "mode": "heat", "target_temp": 72}


def _watch(
    hass: HomeAssistant,
    device: SensiDevice,
    response: ActionResponse | Exception,
    devices: dict | None = None,
) -> tuple[SetpointWatch, list, MagicMock, MagicMock]:
    """Return a watch whose setter answers with `response`, and its fakes."""
    sent: list = []

    async def resend(event: str, data: dict) -> ActionResponse:
        sent.append((event, data))
        if isinstance(response, Exception):
            raise response
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


async def _resend_once(watch: SetpointWatch, device: SensiDevice) -> None:
    """Have `check` see the old value and wait for the re-send it starts."""
    watch.check(device)
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
    hass: HomeAssistant, mock_device: SensiDevice
) -> None:
    """The same payload goes out again, then apply and on_applied run."""
    watch, sent, apply, on_applied = _watch(hass, mock_device, ActionResponse(None, {}))
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device)

    assert sent == [("set_temperature", REQUEST)]
    apply.assert_called_once_with(mock_device, OperatingMode.HEAT, 72)
    on_applied.assert_called_once_with()
    [record] = watch._pending.values()
    assert record.attempts == 2


async def test_an_accepted_resend_for_a_device_now_gone_still_reports(
    hass: HomeAssistant, mock_device: SensiDevice
) -> None:
    """Nothing to apply the value to, but the listeners are still told."""
    watch, _sent, apply, on_applied = _watch(
        hass, mock_device, ActionResponse(None, {}), devices={}
    )
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device)

    apply.assert_not_called()
    on_applied.assert_called_once_with()


async def test_a_refused_resend_is_dropped(
    hass: HomeAssistant, mock_device: SensiDevice, caplog
) -> None:
    """A refusal is logged once and the setpoint is no longer watched."""
    watch, _sent, apply, on_applied = _watch(
        hass, mock_device, ActionResponse("OutOfRange", None)
    )
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device)

    assert not watch.watching
    apply.assert_not_called()
    on_applied.assert_not_called()
    assert _warnings(caplog, "was refused with 'OutOfRange'")


async def test_a_resend_that_raises_is_read_as_a_refusal(
    hass: HomeAssistant, mock_device: SensiDevice, caplog
) -> None:
    """A HomeAssistantError from the setter is the refusal's message."""
    watch, _sent, apply, on_applied = _watch(
        hass, mock_device, HomeAssistantError("Thermostat offline")
    )
    _track_heat(watch, mock_device)

    await _resend_once(watch, mock_device)

    assert not watch.watching
    apply.assert_not_called()
    on_applied.assert_not_called()
    assert _warnings(caplog, "was refused with 'Thermostat offline'")


async def test_a_stopped_watch_forgets_and_ignores_state(
    hass: HomeAssistant, mock_device: SensiDevice
) -> None:
    """After stop() nothing is watched and a revert starts no re-send."""
    watch, sent, _apply, _on_applied = _watch(
        hass, mock_device, ActionResponse(None, {})
    )
    _track_heat(watch, mock_device)

    await watch.stop()
    assert not watch.watching

    _track_heat(watch, mock_device)
    watch.check(mock_device)

    assert not watch._resend_tasks
    assert sent == []
