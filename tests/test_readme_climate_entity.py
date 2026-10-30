"""README.md's "What you get" and "Climate entity" sections are hand copies nothing read.

"What you get" opens with how often the data refreshes, and the "Climate
entity" table tells a user which operating modes, setpoints, fan modes and
humidity control the thermostat entity offers. Each of those is a statement
about the code: the coordinator's update interval, `SensiThermostat.hvac_modes`,
`supported_features`, `fan_modes` and the Circulate duty-cycle fallback.

Before this module no test opened either section, and the fan-mode row had
already gone stale: since the thermostat's own `smart` fan mode was added to
`fan_modes` (for a thermostat that advertises it), the README still listed
only Auto, On and Circulate. These tests read each row and compare it with
the entity, in both directions where the row is a list.

Names are compared case-insensitively: the README writes the labels a user
sees (`Heat`, `Smart`), the entity offers the values (`heat`, `smart`).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import re

import pytest

from custom_components.sensi.climate import SensiThermostat
from custom_components.sensi.const import (
    COORDINATOR_UPDATE_INTERVAL,
    FAN_CIRCULATE_DUTY_CYCLE_DEFAULT,
)
from custom_components.sensi.coordinator import SensiUpdateCoordinator
from custom_components.sensi.data import OperatingMode, SensiDevice
from homeassistant.components.climate import ClimateEntityFeature, HVACMode

_README = Path(__file__).resolve().parent.parent / "README.md"

_WHAT_YOU_GET = "## What you get"
_CLIMATE_ENTITY = "### Climate entity"

_BACKTICKED = re.compile(r"`([^`]+)`")


def _section(heading: str) -> str:
    """Return the README text from the heading to the next heading of any level."""
    text = _README.read_text(encoding="utf-8")
    start = text.index(f"\n{heading}\n") + len(heading) + 2
    end = text.find("\n#", start)
    return text[start : end if end != -1 else len(text)]


def _row(label: str) -> str:
    """Return the second cell of the climate table row labelled `label`."""
    for line in _section(_CLIMATE_ENTITY).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] == f"**{label}**":
            assert len(cells) == 2, line
            return cells[1]
    raise AssertionError(f"README climate table has no {label!r} row")


def _names(text: str) -> list[str]:
    """Return the backticked names in `text`, lowercased."""
    return [name.lower() for name in _BACKTICKED.findall(text)]


def _conditional(cell: str) -> set[str]:
    """Return the names the cell says depend on the thermostat, lowercased."""
    match = re.search(r"\.\s*([^.]+?)\s+depends? on the thermostat", cell)
    assert match, cell
    return {
        name.strip().lower()
        for name in re.split(r",|\band\b", match[1])
        if name.strip()
    }


def _offer_everything(device: SensiDevice) -> None:
    """Make the device advertise every operating mode and fan mode it can."""
    settings = device.capabilities.operating_mode_settings
    settings.off = settings.heat = settings.cool = settings.auto = True
    device.capabilities.circulating_fan.capable = True
    device.capabilities.fan_mode_settings.smart = True


def test_refresh_interval_is_the_coordinators(mock_coordinator) -> None:
    """'Data refreshes every N seconds' is the coordinator's update interval."""
    match = re.search(r"refreshes every (\d+) seconds", _section(_WHAT_YOU_GET))
    assert match, "README 'What you get' no longer states the refresh interval"

    seconds = int(match[1])
    assert seconds == COORDINATOR_UPDATE_INTERVAL
    assert isinstance(mock_coordinator, SensiUpdateCoordinator)
    assert mock_coordinator.update_interval == timedelta(seconds=seconds)


def test_operating_modes_are_every_mode_the_entity_can_offer(
    mock_device, mock_thermostat: SensiThermostat
) -> None:
    """The listed operating modes are exactly what a fully capable thermostat offers."""
    listed = _names(_row("Operating modes").split("—")[0])
    assert len(listed) == len(set(listed)), listed

    _offer_everything(mock_device)

    assert set(listed) == {mode.value for mode in mock_thermostat.hvac_modes}


@pytest.mark.parametrize(
    "mode", [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]
)
def test_each_operating_mode_depends_on_the_thermostat(
    mock_device, mock_thermostat: SensiThermostat, mode: HVACMode
) -> None:
    """'Which of these appear depends on the thermostat's own configuration'."""
    assert "depends on the thermostat's own configuration" in _row("Operating modes")

    _offer_everything(mock_device)
    setattr(mock_device.capabilities.operating_mode_settings, mode.value, False)

    assert mode not in mock_thermostat.hvac_modes


def test_setpoints_follow_the_operating_mode(
    mock_device, mock_thermostat: SensiThermostat
) -> None:
    """Single-target modes and the two-setpoint mode are the ones the entity uses."""
    cell = _row("Setpoints")
    single_clause, range_clause = cell.split(";")
    assert "single target temperature" in single_clause
    assert "separate heat and cool setpoints" in range_clause
    single = set(_names(single_clause))
    ranged = set(_names(range_clause))

    # Every mode that has a setpoint is described, and only those.
    listed = set(_names(_row("Operating modes").split("—")[0]))
    assert single | ranged == listed - {HVACMode.OFF.value}
    assert not single & ranged

    for name, want, other in [
        *(
            (
                n,
                ClimateEntityFeature.TARGET_TEMPERATURE,
                ClimateEntityFeature.TARGET_TEMPERATURE_RANGE,
            )
            for n in single
        ),
        *(
            (
                n,
                ClimateEntityFeature.TARGET_TEMPERATURE_RANGE,
                ClimateEntityFeature.TARGET_TEMPERATURE,
            )
            for n in ranged
        ),
    ]:
        mock_device.state.operating_mode = OperatingMode(name)
        features = mock_thermostat.supported_features
        assert want in features, name
        assert other not in features, name


def test_fan_modes_are_every_mode_the_entity_can_offer(
    mock_device, mock_thermostat: SensiThermostat
) -> None:
    """The listed fan modes are exactly what a fully capable thermostat offers."""
    listed = _names(_row("Fan modes"))
    assert len(listed) == len(set(listed)), listed

    _offer_everything(mock_device)

    assert set(listed) == {mode.lower() for mode in mock_thermostat.fan_modes}


def test_the_fan_modes_said_to_depend_on_the_thermostat_are_the_optional_ones(
    mock_device, mock_thermostat: SensiThermostat
) -> None:
    """The row names the fan modes a thermostat without the capability lacks."""
    cell = _row("Fan modes")

    _offer_everything(mock_device)
    every = {mode.lower() for mode in mock_thermostat.fan_modes}
    mock_device.capabilities.circulating_fan.capable = False
    mock_device.capabilities.fan_mode_settings.smart = False
    always = {mode.lower() for mode in mock_thermostat.fan_modes}

    assert _conditional(cell) == every - always


def test_circulate_falls_back_to_the_stated_duty_cycle() -> None:
    """'10% if none is set' is the duty cycle Circulate sends when the state has 0."""
    match = re.search(r"(\d+)% if none is set", _row("Fan modes"))
    assert match, _row("Fan modes")
    assert int(match[1]) == FAN_CIRCULATE_DUTY_CYCLE_DEFAULT


@pytest.mark.parametrize("enabled", [False, True])
def test_target_humidity_needs_humidification_enabled(
    mock_device_with_humidification,
    mock_thermostat_with_humidification: SensiThermostat,
    enabled: bool,
) -> None:
    """'Target humidity, when the thermostat has humidification enabled'."""
    assert "when the thermostat has humidification enabled" in _row("Humidity")

    mock_device_with_humidification.state.humidity_control.humidification.enabled = (
        enabled
    )
    features = mock_thermostat_with_humidification.supported_features

    assert (ClimateEntityFeature.TARGET_HUMIDITY in features) is enabled
