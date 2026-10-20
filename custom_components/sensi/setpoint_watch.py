"""Notice a set_temperature the thermostat accepted and then dropped, and resend it.

An ack is the backend's answer, not the thermostat's. A write can be accepted,
applied locally, and then reported back at its old value by a later `state`
event with nothing having touched the thermostat (#634). SetpointWatch keeps
each accepted write for SETPOINT_CONFIRM_WINDOW and re-sends it once when a
`state` event reports the value from before it.

SensiClient owns one SetpointWatch. It calls `track` after an accepted
set_temperature, `check` from its `state` handler, and `stop` when it stops.
Everything else the watch needs - the setter, the device it applies to and
what to refresh afterwards - is handed to it by the client.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import LOGGER, SENSI_DOMAIN
from .data import OperatingMode, SensiDevice, State, get_setpoint_mode
from .utils import redact_identifier

if TYPE_CHECKING:
    from .client import ActionResponse

# How long after an accepted set_temperature a `state` event reporting the
# setpoint from before the write is read as the thermostat having dropped it.
# An ack is the backend's answer, not the thermostat's: a write was accepted,
# applied locally, and about four minutes later (249 s, the only data point)
# a `state` event reported the old setpoint again with nothing having touched
# the thermostat (#634). The cost of a longer window is that someone setting
# the old value back by hand inside it is overridden once.
SETPOINT_CONFIRM_WINDOW = 600

# Re-sends of a dropped set_temperature before giving up on it. One: a write
# that is dropped twice is not a transient, and a loop would fight whatever
# is putting the old value back.
MAX_SETPOINT_RESENDS = 1


def _monotonic() -> float:
    """Return the clock pending setpoints are timed against.

    A function of its own so tests can move it without moving the event
    loop's clock, which also runs on time.monotonic.
    """
    return time.monotonic()


@dataclass
class PendingSetpoint:
    """An accepted set_temperature that no `state` event has contradicted yet.

    Kept in memory only, so a restart drops it. See SetpointWatch.check.
    """

    requested: float
    """The value that was asked for."""
    previous: int
    """The setpoint device.state held before the write went out."""
    operating_mode: OperatingMode
    """The thermostat's operating mode when the write went out."""
    request: dict
    """The set_temperature payload, re-sent as-is. Carries the icd_id."""
    sent_at: float
    """When the write (or its latest re-send) was accepted, by _monotonic."""
    attempts: int = 1
    """How many times this value has been sent and accepted."""


class SetpointWatch:
    """Watch accepted set_temperature writes and re-send one the thermostat drops.

    Runs on the socket.io read loop through `check`, so nothing here awaits
    or takes SensiClient's _reconnect_lock. A re-send is started as a
    background task and goes through the client's setter, which takes the
    lock itself when it has to recover from a refusal.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        devices: Mapping[str, SensiDevice],
        resend: Callable[[str, dict], Awaitable[ActionResponse]],
        apply: Callable[[SensiDevice, OperatingMode, float], None],
        on_applied: Callable[[], None],
    ) -> None:
        """Initialize the watch.

        `devices` is the client's own icd_id-to-device map, read when a
        re-send is accepted. `resend` emits a setter event and returns its
        ActionResponse. `apply` records an accepted setpoint on a device, and
        `on_applied` runs after a re-send has been applied.
        """

        self._hass = hass
        self._devices = devices
        self._resend = resend
        self._apply = apply
        self._on_applied = on_applied

        # Set by stop(), and never cleared, like SensiClient._stopped.
        self._stopped = False

        # Accepted set_temperature writes still waiting to be contradicted,
        # keyed by (icd_id, setpoint mode), and the re-send in flight for a
        # key. See check.
        self._pending: dict[tuple[str, OperatingMode], PendingSetpoint] = {}
        self._resend_tasks: dict[tuple[str, OperatingMode], asyncio.Task] = {}

    @property
    def watching(self) -> bool:
        """Return whether any accepted setpoint is still being watched."""
        return bool(self._pending)

    async def stop(self) -> None:
        """Forget every pending setpoint and cancel any re-send in flight.

        Does not take _reconnect_lock: a re-send whose setter is recovering
        from a refusal holds it, and nothing is left to apply a re-send to.
        """

        self._stopped = True

        resend_tasks = list(self._resend_tasks.values())
        self._resend_tasks.clear()
        self._pending.clear()
        for task in resend_tasks:
            task.cancel()
        if resend_tasks:
            await asyncio.gather(*resend_tasks, return_exceptions=True)

    def track(
        self,
        icd_id: str,
        mode: OperatingMode,
        requested: float,
        previous: int | None,
        sent_in_mode: OperatingMode,
        request: dict,
    ) -> None:
        """Record an accepted set_temperature so a dropped one can be noticed.

        A newer accepted write for the same key replaces the record. One that
        asks for the value already shown has no earlier value to tell a drop
        by, so it keeps the earlier write's pre-write value when that write
        asked for the same thing, and is not tracked otherwise.

        A re-send still in flight for a replaced record is left to finish
        rather than cancelled mid-recovery. It finds its record gone and
        applies nothing.
        """

        key = (icd_id, get_setpoint_mode(mode))
        existing = self._pending.pop(key, None)
        if existing is not None:
            if previous == requested and existing.requested == requested:
                previous = existing.previous
            else:
                self._log_dropped(key, "superseded")

        if key[1] not in (OperatingMode.HEAT, OperatingMode.COOL):
            return
        if previous is None or previous == requested:
            return

        self._pending[key] = PendingSetpoint(
            requested=requested,
            previous=previous,
            operating_mode=sent_in_mode,
            request=request,
            sent_at=_monotonic(),
        )

    def check(self, device: SensiDevice) -> None:
        """Compare a device's new state with the setpoints it accepted.

        Runs from SensiClient._update_state, inside the socket.io read loop,
        and the coordinator's refresh delivers `state` events while it holds
        _reconnect_lock - so this neither awaits nor takes that lock. A
        re-send is started as a background task instead.

        A setpoint equal to the requested value is not taken as confirmation:
        whether the backend echoes a requested value before the thermostat
        has applied it is not known, so the record stays for the window.
        """

        if self._stopped:
            return

        state = device.state
        for key, record in list(self._pending.items()):
            if key[0] != device.identifier:
                continue

            elapsed = _monotonic() - record.sent_at
            if elapsed > SETPOINT_CONFIRM_WINDOW:
                self._drop(key, "window passed")
                continue

            # An offline thermostat's state says nothing about what it did
            # with the write. Wait for it to come back, inside the window.
            if not state.is_online:
                continue

            if state.operating_mode == OperatingMode.OFF:
                self._drop(key, "off")
                continue
            if state.operating_mode != record.operating_mode:
                self._drop(key, "mode change")
                continue

            reported = reported_setpoint(state, key[1])
            if reported is None or reported == record.requested:
                continue
            if reported != record.previous:
                # The wall or the app moved it. Never write over that.
                self._drop(key, "other value")
                continue

            if key in self._resend_tasks:
                continue

            if record.attempts >= MAX_SETPOINT_RESENDS + 1:
                LOGGER.warning(
                    "Thermostat %s still reports %s setpoint %s after resend; not "
                    "retrying (%ds after accepting %s, status %s, wifi quality %s)",
                    redact_identifier(key[0]),
                    key[1].value,
                    reported,
                    elapsed,
                    record.requested,
                    state.status,
                    state.wifi_connection_quality,
                )
                del self._pending[key]
                continue

            LOGGER.warning(
                "Thermostat %s reported %s setpoint %s %ds after accepting %s; "
                "resending (attempt %d of %d, status %s, wifi quality %s)",
                redact_identifier(key[0]),
                key[1].value,
                reported,
                elapsed,
                record.requested,
                record.attempts + 1,
                MAX_SETPOINT_RESENDS + 1,
                state.status,
                state.wifi_connection_quality,
            )
            task = self._hass.async_create_background_task(
                self._async_resend(key, record),
                name=f"{SENSI_DOMAIN}._async_resend_setpoint",
            )
            self._resend_tasks[key] = task

            def _done(done: asyncio.Task, key=key) -> None:
                """Forget the re-send once it has finished, unless replaced."""
                if self._resend_tasks.get(key) is done:
                    del self._resend_tasks[key]

            task.add_done_callback(_done)

    async def _async_resend(
        self, key: tuple[str, OperatingMode], record: PendingSetpoint
    ) -> None:
        """Send a dropped set_temperature again, once, and apply it if accepted.

        Through the client's _async_invoke_setter, so the Forbidden retry
        covers it as it covers the first send.
        """

        try:
            response = await self._resend("set_temperature", record.request)
        except HomeAssistantError as err:
            error = str(err)
        else:
            error = response.error

        # Replaced by a newer write, or dropped, while this was on the wire.
        if self._pending.get(key) is not record:
            return

        if error:
            LOGGER.warning(
                "Resending %s setpoint %s to thermostat %s was refused with '%s'; "
                "not retrying",
                key[1].value,
                record.requested,
                redact_identifier(key[0]),
                error,
            )
            del self._pending[key]
            return

        record.attempts += 1
        record.sent_at = _monotonic()

        device = self._devices.get(key[0])
        if device is not None:
            self._apply(device, key[1], record.requested)

        self._on_applied()

    def _drop(self, key: tuple[str, OperatingMode], reason: str) -> None:
        """Forget a pending setpoint without re-sending it."""
        del self._pending[key]
        self._log_dropped(key, reason)

    @staticmethod
    def _log_dropped(key: tuple[str, OperatingMode], reason: str) -> None:
        """Log at DEBUG that a pending setpoint is no longer watched, and why."""
        LOGGER.debug(
            "No longer watching the %s setpoint of %s: %s",
            key[1].value,
            redact_identifier(key[0]),
            reason,
        )


def reported_setpoint(state: State, mode: OperatingMode) -> int | None:
    """Return the setpoint a State holds for a setpoint mode, if it has one."""
    if mode == OperatingMode.HEAT:
        return state.current_heat_temp
    if mode == OperatingMode.COOL:
        return state.current_cool_temp
    return None
