"""README.md's climate "Attributes" sample is a hand copy nothing reads.

The ``### Attributes`` section shows a YAML block of the attributes a user
sees on the climate entity, some from this integration and some from Home
Assistant itself. The block is typed by hand: #391 had to add
``power_status``, an attribute ``extra_state_attributes`` has always set on
every climate entity, because no test compared the block with the entity.

These tests set the integration up end to end and read the attributes Home
Assistant actually writes to the state machine. Two thermostats are served
at once: the plain ``sample.json`` device and the humidification one, so the
target-humidity attributes the README lists are present too. The README's
keys and the union of the real keys must be the same set, in both
directions.

The section's prose also says ``power_status`` is an empty string when the
thermostat has not reported it; the last test serves a payload without the
field and checks that.
"""

from __future__ import annotations

from collections.abc import Iterator
import copy
from pathlib import Path
import re
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensi.const import ATTR_POWER_STATUS
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.core import HomeAssistant

from .conftest import FakeSensiBackend, FakeSensiSocket, load_sample

_README = Path(__file__).resolve().parents[2] / "README.md"
_SECTION = "### Attributes"
_YAML_BLOCK = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)

# The humidification payload uses the same icd_id and name as sample.json.
# Serve it as a second thermostat so both devices load side by side.
_SECOND_ICD_ID = "aa-bb-cc-dd-ee-ff-00-02"
_SECOND_NAME = "Basement"


def _readme_attribute_keys() -> list[str]:
    """Return the keys of the YAML sample under README's Attributes heading."""
    text = _README.read_text(encoding="utf-8")
    start = text.index(_SECTION) + len(_SECTION)
    end = text.find("\n## ", start)
    section = text[start:end]
    match = _YAML_BLOCK.search(section)
    assert match, f"no ```yaml block under {_SECTION!r} in README.md"
    keys = [line.split(":", 1)[0].strip() for line in match.group(1).splitlines()]
    assert all(keys), "blank or malformed line in the README attributes block"
    return keys


def _serve(devices: list[dict]) -> Iterator[FakeSensiBackend]:
    """Patch socket.io with a fake backend serving ``devices``."""
    backend = FakeSensiBackend(devices)

    def factory(*args: Any, **kwargs: Any) -> FakeSensiSocket:
        socket = FakeSensiSocket(backend)
        backend.sockets.append(socket)
        return socket

    with patch("custom_components.sensi.client.socketio.AsyncClient", factory):
        yield backend


@pytest.fixture
def sensi_backend(request: pytest.FixtureRequest) -> Iterator[FakeSensiBackend]:
    """Serve both committed payloads, or the one a test asks for indirectly."""
    devices = getattr(request, "param", None)
    if devices is None:
        humidification = load_sample("sample_with_humidification.json")
        humidification["icd_id"] = _SECOND_ICD_ID
        humidification["registration"]["name"] = _SECOND_NAME
        devices = [load_sample("sample.json"), humidification]
    yield from _serve(devices)


def _climate_states(hass: HomeAssistant) -> list:
    return hass.states.async_all(CLIMATE_DOMAIN)


def test_readme_attributes_block_has_no_duplicate_keys() -> None:
    """A repeated key would hide a missing one from the set comparison."""
    keys = _readme_attribute_keys()
    assert len(keys) == len(set(keys)), keys


async def test_both_thermostats_load(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
) -> None:
    """The comparison below is only meaningful if both payloads are served."""
    names = sorted(state.attributes["friendly_name"] for state in _climate_states(hass))
    assert names == sorted(["Living Room", _SECOND_NAME])


async def test_every_climate_attribute_is_in_the_readme_sample(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
) -> None:
    """An attribute the entity carries must appear in the README sample."""
    actual = {str(key) for state in _climate_states(hass) for key in state.attributes}
    missing = actual - set(_readme_attribute_keys())
    assert not missing, (
        f"the climate entity carries {sorted(missing)}, which README.md's "
        f"{_SECTION!r} sample does not list"
    )


async def test_every_readme_sample_attribute_is_one_the_climate_entity_carries(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
) -> None:
    """A README key no climate entity carries is stale or misspelt."""
    actual = {str(key) for state in _climate_states(hass) for key in state.attributes}
    extra = set(_readme_attribute_keys()) - actual
    assert not extra, (
        f"README.md's {_SECTION!r} sample lists {sorted(extra)}, which no "
        "climate entity carries with either committed payload"
    )


def _without_power_status() -> list[dict]:
    device = copy.deepcopy(load_sample("sample.json"))
    del device["state"]["power_status"]
    return [device]


@pytest.mark.parametrize("sensi_backend", [_without_power_status()], indirect=True)
async def test_power_status_is_empty_when_the_thermostat_does_not_report_it(
    hass: HomeAssistant,
    sensi_entry: MockConfigEntry,
) -> None:
    """README: "an empty string when the thermostat has not reported it"."""
    (state,) = _climate_states(hass)
    assert state.attributes[ATTR_POWER_STATUS] == ""
