"""End-to-end coverage of a set_temperature the thermostat drops (#634).

The backend acks a write, the integration shows the new setpoint, and a few
minutes later a ``state`` event reports the value from before the write. The
client now treats that as a dropped write, re-sends it once and says so in
the log. These tests script the drop by putting the old setpoint back into
what ``FakeSensiBackend`` serves, then pushing a ``state`` event.
"""

import asyncio
import logging
import time
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensi.client import SETPOINT_CONFIRM_WINDOW, SensiClient
from custom_components.sensi.data import OperatingMode
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

from .conftest import FakeSensiBackend

CLIMATE = "climate.sensi_living_room"
ICD_ID = "aa-bb-cc-dd-ee-ff-00-01"

# The sample thermostat is in HEAT with heat setpoint 68 and cool setpoint 76.
OLD_HEAT = 68


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


async def _wait_for_resend(client: SensiClient) -> None:
    """Wait for the re-send the last ``state`` event started to be answered."""
    tasks = list(client._setpoint_resend_tasks.values())  # noqa: SLF001
    assert tasks, "no re-send was started"
    await asyncio.wait_for(asyncio.gather(*tasks), 5)


def _no_resend(client: SensiClient) -> bool:
    return not client._setpoint_resend_tasks  # noqa: SLF001


def _pending(client: SensiClient) -> set:
    return {mode for _, mode in client._pending_setpoints}  # noqa: SLF001


def _warnings(caplog, text: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and text in record.getMessage()
    ]


async def test_a_reverted_setpoint_is_resent_once_with_the_same_payload(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
    caplog,
) -> None:
    """The reproduction from #634: ack 72, then the thermostat reports 68."""
    caplog.set_level(logging.DEBUG, logger="custom_components.sensi")
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    assert len(_set_temperatures(sensi_backend)) == 1

    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
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
    caplog,
) -> None:
    """A second revert is logged and left alone: no third emit."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
    await _wait_for_resend(client)

    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
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
) -> None:
    """A state echoing 72 keeps watching; a later revert is still caught."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, current_heat_temp=72)
    assert _no_resend(client)
    assert _pending(client) == {OperatingMode.HEAT}

    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
    await _wait_for_resend(client)
    assert len(_set_temperatures(sensi_backend)) == 2


async def test_a_revert_after_the_window_is_not_resent(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Past SETPOINT_CONFIRM_WINDOW the old value is a change like any other."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})

    later = time.monotonic() + SETPOINT_CONFIRM_WINDOW + 1
    with patch("custom_components.sensi.client._monotonic", return_value=later):
        await _report(sensi_backend, current_heat_temp=OLD_HEAT)

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
) -> None:
    """An offline report is no evidence; the online one that follows is."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, status="offline", current_heat_temp=OLD_HEAT)
    assert _no_resend(client)
    assert _pending(client) == {OperatingMode.HEAT}

    await _report(sensi_backend, status="online")
    await _wait_for_resend(client)
    assert len(_set_temperatures(sensi_backend)) == 2


async def test_offline_past_the_window_is_not_resent(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Back online after the window: the record is gone, nothing is sent."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    await _report(sensi_backend, status="offline", current_heat_temp=OLD_HEAT)

    later = time.monotonic() + SETPOINT_CONFIRM_WINDOW + 1
    with patch("custom_components.sensi.client._monotonic", return_value=later):
        await _report(sensi_backend, status="online")

    assert _no_resend(client)
    assert _pending(client) == set()
    assert len(_set_temperatures(sensi_backend)) == 1


async def test_auto_tracks_heat_and_cool_on_their_own(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
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

    await _report(sensi_backend, current_heat_temp=OLD_HEAT, current_cool_temp=78)
    await _wait_for_resend(client)

    assert _set_temperatures(sensi_backend) == [heat, cool, heat]
    assert _pending(client) == {OperatingMode.HEAT, OperatingMode.COOL}


async def test_a_revert_delivered_under_the_refresh_lock_resends(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """The coordinator's reconnect delivers state while it holds the lock.

    _update_state runs inside that, so a re-send that awaited the setter or
    took _reconnect_lock there would wedge the refresh. It is started as a
    background task instead, and both the refresh and the re-send finish.
    """
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    sensi_backend.devices[ICD_ID]["state"]["current_heat_temp"] = OLD_HEAT
    sensi_backend.state_before_connect_returns = True

    lock_held: list[bool] = []
    check = client._check_pending_setpoints  # noqa: SLF001

    def check_and_record(device) -> None:
        lock_held.append(client._reconnect_lock.locked())  # noqa: SLF001
        check(device)

    with patch.object(client, "_check_pending_setpoints", new=check_and_record):
        await asyncio.wait_for(client.async_update_devices(), 3)

    assert lock_held == [True]
    await _wait_for_resend(client)
    assert len(_set_temperatures(sensi_backend)) == 2


async def test_stop_cancels_a_resend_in_flight(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Unloading the entry does not leave a re-send behind to go out later."""
    client = _client(sensi_entry)

    await _set_temperature(hass, **{ATTR_TEMPERATURE: 72})
    # Down, so the re-send is still waiting for the wire when stop() runs.
    sensi_backend.sockets[-1].connected = False
    await _report(sensi_backend, current_heat_temp=OLD_HEAT)
    [resend] = client._setpoint_resend_tasks.values()  # noqa: SLF001

    await client.stop()

    assert resend.cancelled()
    assert _no_resend(client)
    assert _pending(client) == set()
    assert len(_set_temperatures(sensi_backend)) == 1
