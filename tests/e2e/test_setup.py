"""End-to-end setup of the Sensi integration.

Drives the same path Home Assistant takes on a real system - config flow,
config entry setup, platform forwarding - against the scripted fake socket.io
backend in ``conftest.py``.
"""

import asyncio
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
from socketio.exceptions import ConnectionError as SocketIOConnectionError

from custom_components.sensi.auth import AuthenticationConfig
from custom_components.sensi.const import CONFIG_REFRESH_TOKEN, SENSI_DOMAIN
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM

from .conftest import NOT_EXPIRED, FakeSensiBackend

ICD_ID = "36-6f-92-ff-fe-0c-0b-07"


async def test_config_flow_creates_a_loadable_entry(
    hass: HomeAssistant,
    sensi_backend: FakeSensiBackend,
    enable_custom_integrations: None,
) -> None:
    """A user walking the config flow ends up with a loaded entry and entities."""
    result = await hass.config_entries.flow.async_init(
        SENSI_DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    logged_in = AuthenticationConfig(
        refresh_token="from_the_form",
        access_token="access",
        expires_at=NOT_EXPIRED,
        user_id="e2e_user",
    )

    with (
        patch(
            "custom_components.sensi.config_flow.validate_refresh_token",
            return_value=logged_in,
        ),
        patch(
            "homeassistant.helpers.storage.Store.async_load",
            return_value={
                "refresh_token": "from_the_form",
                "access_token": "access",
                "expires_at": NOT_EXPIRED,
                "user_id": "e2e_user",
            },
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONFIG_REFRESH_TOKEN: "from_the_form"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONFIG_REFRESH_TOKEN: "from_the_form"}

    entry = hass.config_entries.async_entries(SENSI_DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == "e2e_user"

    assert hass.states.get("climate.sensi_living_room") is not None

    await sensi_backend.shutdown()


async def test_entry_setup_performs_the_full_handshake(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Setup connects once and asks each device for info and capabilities."""
    assert sensi_entry.state is ConfigEntryState.LOADED

    assert len(sensi_backend.connections) == 1
    connection = sensi_backend.connections[0]
    assert connection["socketio_path"] == "/thermostat"
    assert connection["transports"] == ["websocket"]
    assert connection["headers"]["Authorization"] == "bearer e2e_access_token"

    assert sensi_backend.emitted_names() == ["get_info", "get_capabilities"]
    assert sensi_backend.last_emitted("get_info") == {"icd_id": ICD_ID}


async def test_entities_are_created_from_the_sample_payload(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
) -> None:
    """Every platform contributes entities, and they carry real state."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, sensi_entry.entry_id)
    domains = {entry.domain for entry in entries}

    assert domains == {"binary_sensor", "climate", "number", "sensor", "switch"}

    # Every enabled entity must reach the state machine.
    enabled = [entry for entry in entries if entry.disabled_by is None]
    assert enabled
    for entry in enabled:
        assert hass.states.get(entry.entity_id) is not None, (
            f"{entry.entity_id} has no state"
        )

    # The entities this thermostat actually supports must be usable. The aux
    # heat switch is deliberately excluded: the sample payload reports
    # `operating_mode_settings.aux == "no"`, so that one is expected to be
    # unavailable and `test_unsupported_capability_is_unavailable` covers it.
    for entity_id in (
        "climate.sensi_living_room",
        "sensor.sensi_living_room_temperature",
        "sensor.sensi_living_room_humidity",
        "switch.sensi_living_room_display_humidity",
        "number.sensi_living_room_temperature_offset",
    ):
        state = hass.states.get(entity_id)
        assert state is not None, f"{entity_id} was never created"
        assert state.state != STATE_UNAVAILABLE, f"{entity_id} is unavailable"

    climate = hass.states.get("climate.sensi_living_room")
    assert climate is not None
    assert climate.state != STATE_UNKNOWN
    assert climate.attributes["current_temperature"] is not None
    assert climate.attributes["friendly_name"] == "Living Room"


async def test_device_registry_entry_is_populated(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
) -> None:
    """The thermostat shows up as one device with identity from the payload."""
    device_registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_registry, sensi_entry.entry_id)

    assert len(devices) == 1
    device = devices[0]
    assert (SENSI_DOMAIN, ICD_ID) in device.identifiers
    assert device.name == "Living Room"
    assert device.manufacturer is not None


async def test_unload_disconnects_the_socket(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
    sensi_backend: FakeSensiBackend,
) -> None:
    """Unloading the entry tears the connection down and removes the entities."""
    assert await hass.config_entries.async_unload(sensi_entry.entry_id)
    await hass.async_block_till_done()

    assert sensi_entry.state is ConfigEntryState.NOT_LOADED
    assert sensi_backend.disconnects >= 1

    # Unloading takes the entities out of service rather than deleting them.
    climate = hass.states.get("climate.sensi_living_room")
    assert climate is None or climate.state == STATE_UNAVAILABLE


async def test_setup_retries_when_the_backend_is_unreachable(
    hass: HomeAssistant,
    sensi_backend: FakeSensiBackend,
    stored_credentials: None,
    enable_custom_integrations: None,
) -> None:
    """A connection failure leaves the entry in a retrying state, not loaded.

    ``hass`` is requested before ``stored_credentials`` on purpose: the latter
    patches ``Store.async_load`` globally, and Home Assistant loads its own
    registries through that same store while the ``hass`` fixture starts up.
    """
    sensi_backend.connect_error = SocketIOConnectionError("Connection error")
    sensi_backend.connect_error_data = "Connection error"

    entry = MockConfigEntry(
        domain=SENSI_DOMAIN,
        data={CONFIG_REFRESH_TOKEN: "e2e_refresh_token"},
        unique_id="e2e_user",
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state in (
        ConfigEntryState.SETUP_RETRY,
        ConfigEntryState.SETUP_ERROR,
    )
    assert hass.states.get("climate.sensi_living_room") is None

    await sensi_backend.shutdown()


async def test_temperature_offset_is_not_converted_on_a_metric_instance(
    hass: HomeAssistant,
    sensi_backend: FakeSensiBackend,
    stored_credentials: None,
    enable_custom_integrations: None,
) -> None:
    """A delta must survive an instance whose unit system is not the device's.

    This sets up its own entry rather than using `sensi_entry`, because that
    fixture pins US customary to match the Fahrenheit payload - which is
    exactly the combination where this bug is invisible.

    The offset says "shift what the thermostat displays by this much", so it is
    a delta. NumberDeviceClass.TEMPERATURE made Home Assistant run it through
    the absolute converter, turning an offset of 0 into -17.8 °C and putting
    the whole -5..+5 range outside anything the backend would accept.
    """
    hass.config.units = METRIC_SYSTEM

    entry = MockConfigEntry(
        domain=SENSI_DOMAIN,
        data={CONFIG_REFRESH_TOKEN: "e2e_refresh_token"},
        unique_id="e2e_user",
        title="Sensi Thermostat",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("number.sensi_living_room_temperature_offset")
    assert state is not None

    # The payload reports temp_offset 0 in Fahrenheit. Zero shift is zero
    # shift in any scale.
    assert state.state == "0"
    # The label still names the thermostat's own scale, so the number is not
    # ambiguous just because it was left alone.
    assert state.attributes["unit_of_measurement"] == UnitOfTemperature.FAHRENHEIT
    # The range the backend accepts, unconverted.
    assert state.attributes["min"] == -5
    assert state.attributes["max"] == 5

    await sensi_backend.shutdown()


def _live_emit_loops() -> list[asyncio.Task]:
    """Return the emit-loop background tasks that are still running."""
    return [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("sensi._emit_loop") and not task.done()
    ]


async def test_a_failed_setup_leaves_no_connected_client_behind(
    hass: HomeAssistant,
    sensi_backend: FakeSensiBackend,
    stored_credentials: None,
    enable_custom_integrations: None,
) -> None:
    """Setup that fails after the socket is up must tear the client down.

    `wait_for_devices` raises ConfigEntryNotReady from a state where
    `_connect()` has already succeeded, and Home Assistant retries with a
    brand-new SensiClient every time. Nothing stopped the previous one, so it
    stayed connected to the backend with its own emit-loop task, parsing every
    state push into a device dict nobody reads, until Home Assistant restarted.

    The thermostat here connects and then never answers get_info or
    get_capabilities - the exact case the "continuing without them" partial
    success code exists for, taken to its limit.
    """
    sensi_backend.silent_getters = True

    entry = MockConfigEntry(
        domain=SENSI_DOMAIN,
        data={CONFIG_REFRESH_TOKEN: "e2e_refresh_token"},
        unique_id="e2e_user",
        title="Sensi Thermostat",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.sensi.client.PREPARE_DEVICES_TIMEOUT", 0.05):
        # The first attempt, and the retry Home Assistant would make.
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY

    # Both attempts got as far as connecting, which is what makes the leak
    # possible - if this ever stops being true the test is no longer covering
    # the thing it was written for.
    assert len(sensi_backend.sockets) >= 2

    still_connected = [socket for socket in sensi_backend.sockets if socket.connected]
    assert still_connected == [], (
        f"{len(still_connected)} socket(s) left connected after a failed setup"
    )
    assert _live_emit_loops() == []
