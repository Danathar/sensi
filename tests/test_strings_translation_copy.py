"""strings.json and translations/en.json say the same thing.

Home Assistant shows a custom integration's English text from
translations/en.json; hassfest validates strings.json. Nothing generates one
from the other here, so they are two hand copies, and an edit to only one of
them leaves either the shipped text or the validated text stale without any
check noticing. The per-entity tests read single keys from both files; this
compares the whole of them.

The "already configured" abort is the integration's own copy of Home
Assistant's text since #450 (async_abort accepts translation_domain only from
2026.9.0), so it is also held to the wording Home Assistant uses for the same
abort.
"""

import json
from pathlib import Path

import homeassistant

_COMPONENT = Path(__file__).parents[1] / "custom_components" / "sensi"
STRINGS = _COMPONENT / "strings.json"
ENGLISH = _COMPONENT / "translations" / "en.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _leaves(tree: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a strings tree to {"config.abort.key": "text"}."""
    leaves: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            leaves.update(_leaves(value, f"{path}."))
        else:
            leaves[path] = value
    return leaves


def test_the_shipped_english_text_is_the_validated_text() -> None:
    """Every key and every string in strings.json is in en.json, and no more."""
    strings = _leaves(_load(STRINGS))
    english = _leaves(_load(ENGLISH))

    assert sorted(strings.keys() - english.keys()) == [], "missing from en.json"
    assert sorted(english.keys() - strings.keys()) == [], "missing from strings.json"
    assert {
        key: (strings[key], english[key])
        for key in strings
        if strings[key] != english[key]
    } == {}


def test_already_configured_reads_as_home_assistant_says_it() -> None:
    """The single-instance abort keeps Home Assistant's own English wording."""
    core = _load(Path(homeassistant.__file__).parent / "strings.json")
    expected = core["common"]["config_flow"]["abort"]["single_instance_allowed"]

    for path in (STRINGS, ENGLISH):
        abort = _load(path)["config"]["abort"]
        assert abort["single_instance_allowed"] == expected, path.name
