"""README.md's "Sensors and controls" section is an entity inventory nothing reads.

The section sorts every non-climate entity into three lists - enabled by
default, disabled by default, and configuration entities - and a user reads
it to learn what the integration adds and what they have to enable by hand.
Each name in it is a hand copy of an entity's `name=` or its `strings.json`
translation, and each list is a hand copy of `entity_category` and
`entity_registry_enabled_default`. `.claude/commands/add-entity.md` is the
procedure for adding an entity, and before this module no test opened the
section, so an entity could be added, renamed, moved to a different category
or enabled by default without the README changing.

This module builds the inventory from the platforms themselves - it runs each
platform's `async_setup_entry` against both committed payloads, so the
entities that only appear on some models (humidification, circulating fan)
are included - and compares each list to the matching README list in both
directions.

Names are compared case-insensitively (the README writes "WiFi", the entity
"Wifi"), and the README's "Min/Max setpoints" shorthand is expanded to the
two names it stands for before comparing.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from unittest.mock import MagicMock

import pytest

from custom_components.sensi import binary_sensor, number, sensor, switch
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import UNDEFINED

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"
_STRINGS = _ROOT / "custom_components" / "sensi" / "strings.json"

_SECTION = "### Sensors and controls"

# The README's label for each list, and how an entity is sorted into it.
_ENABLED = "Enabled by default:"
_DISABLED = "Disabled by default"
_CONFIGURATION = "Configuration entities"
_LABELS = (_ENABLED, _DISABLED, _CONFIGURATION)

# Every platform a non-climate entity comes from. The climate entity has its
# own README section and is not in these lists.
_PLATFORMS = {
    "binary_sensor": binary_sensor,
    "number": number,
    "sensor": sensor,
    "switch": switch,
}

# "Min/Max setpoints" -> "Min setpoint", "Max setpoint".
_SHORTHAND = re.compile(r"^(\w+)/(\w+) (\w+?)s$")


def _section() -> str:
    """Return the README text from the section heading to the next heading."""
    text = _README.read_text(encoding="utf-8")
    start = text.index(_SECTION) + len(_SECTION)
    end = text.find("\n#", start)
    return text[start : end if end != -1 else len(text)]


def _expand(item: str) -> list[str]:
    """Return the entity names one README list item stands for."""
    item = re.sub(r"^and\s+", "", item.strip(" *"))
    if match := _SHORTHAND.match(item):
        first, second, noun = match.groups()
        return [f"{first} {noun}", f"{second} {noun}"]
    return [item]


def _readme_lists() -> dict[str, set[str]]:
    """Return each README list as a set of casefolded entity names."""
    lists: dict[str, set[str]] = {}
    for paragraph in re.split(r"\n\s*\n", _section()):
        paragraph = " ".join(paragraph.split())
        label = next((lbl for lbl in _LABELS if f"**{lbl}" in paragraph), None)
        if label is None:
            continue
        # The list is whatever follows the last colon: the disabled list has a
        # parenthetical before it, the configuration list a qualifying clause.
        items = paragraph.rsplit(":", 1)[1].strip().rstrip(".")
        lists[label] = {
            name.casefold() for item in items.split(",") for name in _expand(item)
        }
    return lists


def _entity_name(platform: str, entity) -> str:
    """Return the name a user sees: `name=`, else the strings.json translation."""
    description = getattr(entity, "entity_description", None)
    name = getattr(description, "name", None)
    if isinstance(name, str):
        return name
    key = entity.translation_key
    assert key is not None, (
        f"{platform} entity {entity!r} has neither a name nor a translation_key"
    )
    strings = json.loads(_STRINGS.read_text(encoding="utf-8"))
    return strings["entity"][platform][key]["name"]


async def _built_lists(
    hass: HomeAssistant, coordinator, devices
) -> dict[str, set[str]]:
    """Set up every platform against `devices` and sort what it adds."""
    coordinator.get_devices = MagicMock(return_value=devices)
    lists: dict[str, set[str]] = {label: set() for label in _LABELS}
    for platform, module in _PLATFORMS.items():
        add_entities = MagicMock()
        await module.async_setup_entry(hass, coordinator.config_entry, add_entities)
        for (entities,), _ in add_entities.call_args_list:
            for entity in entities:
                if entity.entity_category is EntityCategory.CONFIG:
                    label = _CONFIGURATION
                elif entity.entity_registry_enabled_default:
                    label = _ENABLED
                else:
                    label = _DISABLED
                name = _entity_name(platform, entity)
                assert name is not UNDEFINED
                lists[label].add(name.casefold())
    return lists


@pytest.fixture
async def built(
    hass: HomeAssistant, mock_coordinator, mock_device, mock_device_with_humidification
) -> dict[str, set[str]]:
    """Return the inventory the platforms actually build, across both payloads."""
    return await _built_lists(
        hass, mock_coordinator, [mock_device, mock_device_with_humidification]
    )


def test_the_readme_section_has_all_three_lists() -> None:
    """Every assertion below compares against these; none may be empty."""
    lists = _readme_lists()
    assert set(lists) == set(_LABELS), (
        f"README.md's {_SECTION!r} section should have the lists {_LABELS}, "
        f"found {sorted(lists)}"
    )
    for label, names in lists.items():
        assert len(names) >= 3, f"README.md list {label!r} parsed as {names}"


def test_the_shorthand_expands_to_both_names() -> None:
    """The one shorthand form the README uses must not be compared literally."""
    assert _expand("Min/Max setpoints") == ["Min setpoint", "Max setpoint"]
    assert _expand("and Temperature/Humidity offsets") == [
        "Temperature offset",
        "Humidity offset",
    ]
    assert _expand("Fan speed") == ["Fan speed"]


async def test_the_platform_scan_finds_every_list(built) -> None:
    """Guard the reader itself: two empty sets would agree."""
    for label, names in built.items():
        assert len(names) >= 3, f"platforms built only {names} for {label!r}"
    assert sum(len(names) for names in built.values()) >= 20


@pytest.mark.parametrize("label", _LABELS)
async def test_every_entity_is_in_the_readme_list_for_its_category(
    built, label: str
) -> None:
    """A new, renamed or re-categorised entity must reach the README."""
    missing = sorted(built[label] - _readme_lists()[label])
    assert not missing, (
        f"these entities belong under README.md's {label!r} list but are not "
        f"in it: {missing}. Add them there, or move them if their category "
        "or enabled-by-default changed."
    )


@pytest.mark.parametrize("label", _LABELS)
async def test_every_readme_name_is_an_entity_in_that_category(
    built, label: str
) -> None:
    """A removed, renamed or re-categorised entity must leave the README list."""
    extra = sorted(_readme_lists()[label] - built[label])
    assert not extra, (
        f"README.md's {label!r} list names {extra}, which no platform builds "
        "in that category"
    )
