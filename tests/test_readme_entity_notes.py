"""README.md's "Notes on specific entities" section is a set of hand copies nothing read.

The section tells a user how the less obvious entities behave: what the
battery percentage means, what aux heat looks like, the circulating fan duty
cycle's range and when it is unavailable, the humidity step and which climate
attributes humidification adds, where the offset bounds come from, and the
values of the Active Savings Event sensor. Each of those is a statement about
the code: `BATTERY_DISCHARGE_CURVE`, `SensiThermostat.hvac_action`, the
number descriptions, the capability defaults, `ActiveSavingsEventState` and
its `strings.json` translations.

Before this module no test opened the section, and the humidification
paragraph had already gone stale: it listed `current_humidity` among the
attributes humidification adds, but the climate entity shows the current
humidity on every thermostat that reports one. These tests read each claim
and compare it with the entity.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from custom_components.sensi.capabilities import CirculatingFanCapabilities
from custom_components.sensi.climate import SensiThermostat
from custom_components.sensi.const import (
    ATTR_BATTERY_VOLTAGE,
    DEFAULT_HUMIDITY_STEP,
    FAN_CIRCULATE_DUTY_CYCLE_MAXIMUM,
    FAN_CIRCULATE_DUTY_CYCLE_MINIMUM,
    FAN_CIRCULATE_DUTY_CYCLE_STEP,
)
from custom_components.sensi.data import (
    ActiveSavingsEventState,
    OperatingMode,
    SensiDevice,
)
from custom_components.sensi.number import CIRCULATING_FAN_DUTY_CYCLE, NUMBER_TYPES
from custom_components.sensi.sensor import (
    BATTERY_DISCHARGE_CURVE,
    SENSOR_TYPES,
    ActiveSavingsEventEntity,
    calculate_battery_level,
)
from homeassistant.components.climate import HVACAction
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"
_STRINGS = _ROOT / "custom_components" / "sensi" / "strings.json"

_SECTION = "### Notes on specific entities"

_BACKTICKED = re.compile(r"`([^`]+)`")

# "3.0 V or more reads 100%", "2.6 V about 70%", "2.0 V or less 0%".
_BATTERY_POINT = re.compile(
    r"(\d+\.\d+) V (?:(or more|or less) )?(?:reads |about )?(\d+)%"
)


def _paragraph(entity: str) -> str:
    """Return the note that opens with `**<entity>**`, unwrapped.

    A note runs on through the paragraphs after it until the next one that
    opens with a bold entity name: Active Savings Event's takes two.
    """
    text = _README.read_text(encoding="utf-8")
    start = text.index(f"\n{_SECTION}\n") + len(_SECTION) + 2
    end = text.find("\n#", start)
    section = text[start : end if end != -1 else len(text)]
    note: list[str] = []
    for paragraph in re.split(r"\n\s*\n", section.strip()):
        if paragraph.startswith("**"):
            if note:
                break
            if paragraph.startswith(f"**{entity}**"):
                note.append(paragraph)
        elif note:
            note.append(paragraph)
    assert note, f"README {_SECTION!r} has no **{entity}** note"
    return " ".join(" ".join(note).split())


def _sentence(paragraph: str, words: str) -> str:
    """Return the sentence of `paragraph` that contains `words`."""
    for sentence in re.split(r"(?<=\.)\s+", paragraph):
        if words in sentence:
            return sentence
    raise AssertionError(f"no sentence containing {words!r} in: {paragraph}")


def _description(descriptions, key: str):
    """Return the entity description with `key`."""
    return next(d for d in descriptions if d.key == key)


def _battery_points() -> list[tuple[float, str | None, int]]:
    """Return each (voltage, 'or more'/'or less'/None, percent) the README gives."""
    points = [
        (float(volts), qualifier or None, int(percent))
        for volts, qualifier, percent in _BATTERY_POINT.findall(_paragraph("Battery"))
    ]
    assert len(points) >= 3, f"README battery paragraph parsed as {points}"
    return points


def test_battery_attribute_is_the_one_the_sensor_sets(mock_device) -> None:
    """The voltage is 'kept as the `battery_voltage` attribute' of the Battery sensor."""
    paragraph = _paragraph("Battery")
    assert "kept as the" in paragraph
    (attribute,) = _BACKTICKED.findall(_sentence(paragraph, "kept as the"))
    assert attribute == ATTR_BATTERY_VOLTAGE

    battery = _description(SENSOR_TYPES, "battery")
    assert battery.extra_state_attributes_fn(mock_device) == {
        attribute: mock_device.state.battery_voltage
    }


def test_every_battery_reading_is_the_curves() -> None:
    """Each voltage the README quotes reads the percentage it says."""
    for volts, qualifier, percent in _battery_points():
        assert calculate_battery_level(volts) == percent, (volts, percent)
        if qualifier == "or more":
            assert calculate_battery_level(volts + 0.5) == percent, volts
        elif qualifier == "or less":
            assert calculate_battery_level(volts - 0.5) == percent, volts


def test_the_readme_battery_ends_are_the_curves_ends() -> None:
    """'or more' is the curve's top point and 'or less' its bottom one."""
    points = _battery_points()
    (top_volts, top_percent), (bottom_volts, bottom_percent) = (
        BATTERY_DISCHARGE_CURVE[0],
        BATTERY_DISCHARGE_CURVE[-1],
    )
    assert points[0] == (top_volts, "or more", top_percent), points
    assert points[-1] == (bottom_volts, "or less", bottom_percent), points
    assert all(qualifier is None for _, qualifier, _ in points[1:-1]), points


def test_aux_heating_reads_as_the_heating_action(
    mock_device: SensiDevice, mock_thermostat: SensiThermostat
) -> None:
    """'Home Assistant labels the resulting action as `Heating`'."""
    sentence = _sentence(_paragraph("Auxiliary heating"), "labels the resulting action")
    (label,) = _BACKTICKED.findall(sentence)

    mock_device.state.operating_mode = OperatingMode.AUX
    mock_device.state.demand_status.aux = 100

    assert mock_thermostat.hvac_action == HVACAction.HEATING
    assert label.lower() == HVACAction.HEATING.value


def test_duty_cycle_typical_range_is_the_app_limits() -> None:
    """'typically 10-100% in steps of 5' is what a thermostat that reports none gets."""
    match = re.search(
        r"typically (\d+)[–-](\d+)% in steps of (\d+)", _paragraph("Circulating fan")
    )
    assert match, "README circulating fan paragraph no longer gives the typical range"
    minimum, maximum, step = map(int, match.groups())

    assert (minimum, maximum, step) == (
        FAN_CIRCULATE_DUTY_CYCLE_MINIMUM,
        FAN_CIRCULATE_DUTY_CYCLE_MAXIMUM,
        FAN_CIRCULATE_DUTY_CYCLE_STEP,
    )
    defaults = CirculatingFanCapabilities({})
    assert (defaults.min_duty_cycle, defaults.max_duty_cycle, defaults.step) == (
        minimum,
        maximum,
        step,
    )


def test_duty_cycle_range_and_step_are_the_thermostats(mock_device) -> None:
    """'The number's range and step are the thermostat's own'."""
    assert "range and step are the thermostat's own" in _paragraph("Circulating fan")

    fan = mock_device.capabilities.circulating_fan
    fan.min_duty_cycle, fan.max_duty_cycle, fan.step = 15, 85, 7

    assert CIRCULATING_FAN_DUTY_CYCLE.min_fn(mock_device) == 15
    assert CIRCULATING_FAN_DUTY_CYCLE.max_fn(mock_device) == 85
    assert CIRCULATING_FAN_DUTY_CYCLE.step_fn(mock_device) == 7


def test_duty_cycle_is_unavailable_while_the_switch_is_off(mock_device) -> None:
    """'it is unavailable while the switch is off'."""
    assert "unavailable while the switch is off" in _paragraph("Circulating fan")

    mock_device.state.circulating_fan.enabled = False
    assert CIRCULATING_FAN_DUTY_CYCLE.available_fn(mock_device) is False

    mock_device.state.circulating_fan.enabled = True
    assert CIRCULATING_FAN_DUTY_CYCLE.available_fn(mock_device) is True


def test_humidity_step_is_the_thermostats(mock_device_with_humidification) -> None:
    """'Sensi works in 5% increments' is the humidification step."""
    match = re.search(r"works in (\d+)% increments", _paragraph("Humidification"))
    assert match, "README humidification paragraph no longer gives the step"
    step = int(match[1])

    assert step == DEFAULT_HUMIDITY_STEP
    humidification = mock_device_with_humidification.capabilities.humidity_control
    assert humidification.humidification.step == step


def _attribute_keys(hass: HomeAssistant, thermostat: SensiThermostat) -> set[str]:
    """Return every attribute key Home Assistant writes for the climate entity."""
    thermostat.hass = hass
    return {
        str(key)
        for key in (
            *thermostat.capability_attributes,
            *thermostat.state_attributes,
        )
    }


def test_humidification_adds_exactly_the_listed_attributes(
    hass: HomeAssistant,
    mock_thermostat: SensiThermostat,
    mock_thermostat_with_humidification: SensiThermostat,
) -> None:
    """'When it is active the climate entity gains ...' names what it gains."""
    sentence = _sentence(_paragraph("Humidification"), "climate entity gains")
    # What follows "next to" is there either way, so it is not a gain.
    gains = sentence.split("gains", 1)[1].split(" next to ", 1)[0]
    listed = set(_BACKTICKED.findall(gains))
    assert listed, sentence

    plain = _attribute_keys(hass, mock_thermostat)
    humidified = _attribute_keys(hass, mock_thermostat_with_humidification)

    assert listed == humidified - plain, (
        f"README says humidification adds {sorted(listed)}; the entity adds "
        f"{sorted(humidified - plain)}"
    )


def test_offset_bounds_are_the_thermostats(mock_device) -> None:
    """'Their bounds come from the thermostat's own reported capabilities'."""
    assert "bounds come from the thermostat's own reported capabilities" in (
        _paragraph("Offsets")
    )

    capabilities = mock_device.capabilities
    capabilities.temp_offset_lower_bound, capabilities.temp_offset_upper_bound = -3, 4
    capabilities.humidity_offset_lower_bound = -6
    capabilities.humidity_offset_upper_bound = 7

    temperature = _description(NUMBER_TYPES, "temperature_offset")
    humidity = _description(NUMBER_TYPES, "humidity_offset")
    assert (temperature.min_fn(mock_device), temperature.max_fn(mock_device)) == (-3, 4)
    assert (humidity.min_fn(mock_device), humidity.max_fn(mock_device)) == (-6, 7)


def test_active_savings_values_are_the_sensors_states(
    hass: HomeAssistant, mock_device, mock_coordinator
) -> None:
    """'Its values are ...' lists the translated state of every option, in order."""
    sentence = _sentence(_paragraph("Active Savings Event"), "Its values are")
    listed = _BACKTICKED.findall(sentence.split(", with the")[0])

    entity = ActiveSavingsEventEntity(hass, mock_device, mock_coordinator.config_entry)
    strings = json.loads(_STRINGS.read_text(encoding="utf-8"))
    translated = strings["entity"]["sensor"][entity.translation_key]["state"]

    assert entity.options == [state.value for state in ActiveSavingsEventState]
    assert listed == [translated[option] for option in entity.options]


def test_active_savings_is_a_diagnostic_sensor_with_the_event_window(
    hass: HomeAssistant, mock_device, mock_coordinator
) -> None:
    """'a diagnostic sensor' with 'the event start and end times as extra attributes'."""
    paragraph = _paragraph("Active Savings Event")
    assert "is a diagnostic sensor" in paragraph
    assert "event start and end times as extra attributes" in paragraph

    entity = ActiveSavingsEventEntity(hass, mock_device, mock_coordinator.config_entry)

    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    assert set(entity.extra_state_attributes) == {"start_time", "end_time"}
