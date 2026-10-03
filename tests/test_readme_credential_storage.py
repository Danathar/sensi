"""README.md's paragraph on where the credentials live is a hand copy nothing reads.

Under "Only one Sensi account can be configured at a time", the README tells a
user where their Sensi credentials are kept on disk (`.storage/sensi`), why a
second account is refused (`single_config_entry`), and - since #411 - that the
config entry holds no credentials at all, and that the refresh-token copy and
the login and password older versions left there are removed the first time
this version starts.

Every one of those is a statement about code: the store's key, the manifest,
and `async_migrate_entry`. The code side is tested (`tests/test_init.py` runs
the migration, `tests/e2e/test_setup.py` checks a new entry is created empty),
but before this module no test opened the paragraph, so changing the store
path, dropping a credential from the migration's list or rewording the version
range in the README went unnoticed. These tests read each claim and compare it
with the code it describes.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

from custom_components.sensi import _LEGACY_ENTRY_CREDENTIALS
from custom_components.sensi.config_flow import SensiFlowHandler
from custom_components.sensi.const import (
    CONFIG_REFRESH_TOKEN,
    SENSI_DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"
_COMPONENT = _ROOT / "custom_components" / "sensi"
_MANIFEST = _COMPONENT / "manifest.json"

_ANCHOR = "Only one Sensi account can be configured at a time."

# The words the README uses for each credential, and the entry.data key each
# one names. A key added to _LEGACY_ENTRY_CREDENTIALS needs a row here, and
# the README has to name it, or test_the_readme_names_every_removed_credential
# fails.
_CREDENTIAL_WORDS = {
    "refresh token": CONFIG_REFRESH_TOKEN,
    "login": CONF_USERNAME,
    "password": CONF_PASSWORD,
}

_UPSTREAM_RANGE = re.compile(
    r"upstream v(\d+)\.(\d+)(?:\.\S+)? to v(\d+)\.(\d+)", re.IGNORECASE
)


def _paragraph() -> str:
    """Return the README paragraph that starts with the anchor, unwrapped."""
    text = _README.read_text(encoding="utf-8")
    start = text.index(_ANCHOR)
    end = text.find("\n\n", start)
    return " ".join(text[start : end if end != -1 else None].split())


def _sentences() -> list[str]:
    """Return the paragraph split into sentences."""
    return [s for s in re.split(r"(?<=\.)\s+", _paragraph()) if s]


def _removal_sentence() -> str:
    """Return the one sentence that says which credentials are removed."""
    found = [s for s in _sentences() if "removed" in s]
    assert len(found) == 1, f"expected one sentence saying what is removed: {found}"
    return found[0]


def test_the_paragraph_is_where_the_tests_look() -> None:
    """Each test below reads the one paragraph that starts with the anchor."""
    assert _README.read_text(encoding="utf-8").count(_ANCHOR) == 1


async def test_the_store_path_is_the_one_home_assistant_writes(
    hass: HomeAssistant,
) -> None:
    """Claim: "Credentials live in a single domain-keyed store (`.storage/sensi`)".

    The path is computed by Home Assistant's own Store for the integration's
    key, so it follows whatever STORAGE_KEY is, and the key must be the
    domain for "domain-keyed" to hold.
    """
    claimed = re.findall(r"`(\.storage/[^`]+)`", _paragraph())
    assert len(claimed) == 1, claimed

    store = storage.Store(hass, STORAGE_VERSION, STORAGE_KEY)
    actual = Path(store.path).relative_to(hass.config.config_dir).as_posix()

    assert claimed[0] == actual
    assert STORAGE_KEY == SENSI_DOMAIN
    assert json.loads(_MANIFEST.read_text(encoding="utf-8"))["domain"] == STORAGE_KEY


def test_the_declared_manifest_key_is_set() -> None:
    """Claim: "the integration declares `single_config_entry`"."""
    sentence = next(s for s in _sentences() if "declares" in s)
    named = re.findall(r"declares `(\w+)`", sentence)
    assert named == ["single_config_entry"], sentence

    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert manifest.get(named[0]) is True


def test_the_entry_is_said_to_hold_no_credentials() -> None:
    """Claim: "The config entry itself holds no credentials".

    MINOR_VERSION 2 is the version whose entries hold none; an entry created
    at it gets data={} (tests/e2e/test_setup.py), and older ones are migrated
    to it on load.
    """
    assert "The config entry itself holds no credentials." in _sentences()
    assert SensiFlowHandler.MINOR_VERSION == 2


def test_the_readme_names_every_removed_credential() -> None:
    """Claim: the refresh token, the login and the password are removed.

    The set the README names and the set async_migrate_entry strips must be
    the same, both ways: a credential the migration stops removing would leave
    the README promising it is gone, and one it starts removing should be
    named so a user knows.
    """
    sentence = _removal_sentence()
    named = {key for words, key in _CREDENTIAL_WORDS.items() if words in sentence}

    unmapped = set(_LEGACY_ENTRY_CREDENTIALS) - set(_CREDENTIAL_WORDS.values())
    assert not unmapped, f"add the README's words for {unmapped} to this module"
    assert named == set(_LEGACY_ENTRY_CREDENTIALS), sentence


def test_the_removal_happens_on_start() -> None:
    """Claim: "both are removed the first time this version starts".

    Home Assistant runs async_migrate_entry when an entry's minor version is
    older than the flow's, before setup; that only happens on start if the
    integration defines the hook and the flow version is past 1.
    """
    assert "removed the first time this version starts" in _removal_sentence()

    tree = ast.parse((_COMPONENT / "__init__.py").read_text(encoding="utf-8"))
    hooks = [
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_migrate_entry"
    ]
    assert hooks == ["async_migrate_entry"]
    assert SensiFlowHandler.MINOR_VERSION > 1


def test_the_upstream_range_matches_the_migration() -> None:
    """Claim: "upstream v1.0 to v1.2 kept your Sensi login and password".

    async_migrate_entry's docstring states the same range with patch levels
    ("v1.0.0 to v1.2.x"); the major.minor ends must agree.
    """
    readme = _UPSTREAM_RANGE.findall(_removal_sentence())
    assert len(readme) == 1, _removal_sentence()

    tree = ast.parse((_COMPONENT / "__init__.py").read_text(encoding="utf-8"))
    hook = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_migrate_entry"
    )
    docstring = " ".join((ast.get_docstring(hook) or "").split())
    code = _UPSTREAM_RANGE.findall(docstring)
    assert code, docstring

    assert readme[0] == code[0]
