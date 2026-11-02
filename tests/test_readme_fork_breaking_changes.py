"""README.md's "This fork" breaking change is a hand copy nothing read.

Under "Breaking changes", the README tells an upgrading user what changed
about the WiFi diagnostic sensor: which key and which thermostat field it
reports, that it is now a percentage with no device class, its new name, that
its entity ID and unique ID did not change, the unit in the recorder warning
they will see, and that it is disabled by default. Each of those is a
statement about the sensor's description in `SENSOR_TYPES`.

`tests/test_sensor.py` pins the description itself, but nothing compared the
README with it: a later change to the unit, the name or the default would
leave this section telling users the opposite. These tests read each claim
and compare it with the sensor.
"""

from __future__ import annotations

from pathlib import Path
import re

from custom_components.sensi.data import SensiDevice
from custom_components.sensi.sensor import SENSOR_TYPES, SensiSensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"

_SECTION = "### This fork"


def _section() -> str:
    """Return the "This fork" section, unwrapped onto single lines.

    It ends where the collapsed upstream release history starts.
    """
    text = _README.read_text(encoding="utf-8")
    start = text.index(f"\n{_SECTION}\n") + len(_SECTION) + 2
    ends = [
        i for i in (text.find("\n#", start), text.find("\n<details>", start)) if i != -1
    ]
    body = text[start : min(ends) if ends else len(text)]
    return re.sub(r"[ \t]*\n(?![ \t]*\n)[ \t]*", " ", body)


def _sensor():
    """Return the description of the sensor the section is about."""
    match = re.search(r"The `([^`]+)` diagnostic sensor reports", _section())
    assert match, "the section no longer names the sensor the way this test reads"
    found = [s for s in SENSOR_TYPES if s.key == match.group(1)]
    assert len(found) == 1, f"no sensor has the key `{match.group(1)}`"
    return found[0]


def test_the_sensor_reports_the_named_field(mock_device: SensiDevice) -> None:
    """'reports the thermostat's `wifi_connection_quality`, a 0-100 value'."""
    match = re.search(r"reports the thermostat's `([^`]+)`, a 0-100 value", _section())
    assert match, "the section no longer names the field the sensor reports"

    value = _sensor().value_fn(mock_device)

    assert value == getattr(mock_device.state, match.group(1))
    assert 0 <= value <= 100


def test_it_is_a_percentage_with_no_device_class() -> None:
    """'It now reports a percentage with no device class'."""
    assert "It now reports a percentage with no device class" in _section()

    sensor = _sensor()

    assert sensor.native_unit_of_measurement == "%"
    assert sensor.device_class is None


def test_the_recorder_warning_names_the_current_unit_and_key() -> None:
    """The quoted warning is for the sensor's entity ID, in its current unit."""
    match = re.search(
        r"`The unit of (sensor\.\S+) \(([^)]+)\) cannot be converted", _section()
    )
    assert match, "the section no longer quotes the recorder warning"
    entity_id, unit = match.groups()
    sensor = _sensor()

    assert entity_id.endswith(f"_{sensor.key}")
    assert unit == sensor.native_unit_of_measurement


def test_it_is_named_as_the_section_says() -> None:
    """'and is named *Wifi quality*'."""
    match = re.search(r"is named \*([^*]+)\*", _section())
    assert match, "the section no longer gives the sensor's name"

    assert _sensor().name == match.group(1)


def test_its_unique_id_is_still_the_key(
    hass: HomeAssistant, mock_device: SensiDevice, mock_coordinator
) -> None:
    """'Its entity ID and unique ID are unchanged': both still end in the key."""
    assert "Its entity ID and unique ID are unchanged" in _section()
    sensor = _sensor()

    entity = SensiSensorEntity(hass, mock_device, sensor, mock_coordinator.config_entry)

    assert entity.unique_id == f"{mock_device.identifier}_{sensor.key}"
    assert entity.entity_id.endswith(f"_{sensor.key}")


def test_it_is_a_diagnostic_sensor_disabled_by_default() -> None:
    """'The ... diagnostic sensor' and 'The sensor is disabled by default'."""
    assert "The sensor is disabled by default" in _section()
    sensor = _sensor()

    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is False
