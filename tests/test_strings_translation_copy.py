"""strings.json and translations/en.json say the same thing.

Home Assistant shows a custom integration's English text from
translations/en.json; hassfest validates strings.json. Nothing generates one
from the other here, so they are two hand copies, and an edit to only one of
them leaves either the shipped text or the validated text stale without any
check noticing. The per-entity tests read single keys from both files; this
compares the whole of them.
"""

import json
from pathlib import Path

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

