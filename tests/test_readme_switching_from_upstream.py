"""README.md's "How to switch" section is a set of hand copies nothing read.

The section tells someone running upstream's integration that nothing needs
to be set up again: both use the domain `sensi`, so the config entry and its
entities carry over, including entries upstream created before it started
keying them by the Sensi user_id. It then has them replace
`custom_components/sensi/` and check the version shown afterwards. Each of
those is a statement about this tree: the manifest's domain and version, the
component directory, and the entry generations `is_user_id` tells apart.

If the domain or the directory moved, a switching user would end up with a
second, empty integration next to the old one; if the version cut-over in the
README stopped matching the code, the promise about older entries would be
one nothing keeps. These tests read each claim and compare it with the tree.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import re

from custom_components.sensi.auth import is_user_id
from custom_components.sensi.const import SENSI_DOMAIN

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"
_MANIFEST = _ROOT / "custom_components" / "sensi" / "manifest.json"

_SECTION = "### How to switch"

_BACKTICKED = re.compile(r"`([^`]+)`")


def _section() -> str:
    """Return the "How to switch" section, unwrapped onto single lines."""
    text = _README.read_text(encoding="utf-8")
    start = text.index(f"\n{_SECTION}\n") + len(_SECTION) + 2
    end = text.find("\n#", start)
    body = text[start : end if end != -1 else len(text)]
    return re.sub(r"[ \t]*\n(?![ \t]*\n|\d+\. )[ \t]*", " ", body)


def _manifest() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def test_the_named_domain_is_the_manifests() -> None:
    """'Both integrations use the domain `sensi`' names this component's domain."""
    match = re.search(r"use the domain `([^`]+)`", _section())
    assert match, "the section no longer names the domain the way this test reads"

    assert match.group(1) == _manifest()["domain"] == SENSI_DOMAIN


def test_the_component_directory_is_the_one_the_tree_ships() -> None:
    """Every `custom_components/<name>/` the steps name is this component's directory.

    Home Assistant loads a custom integration from the directory named after
    its domain, so the path the user replaces has to be that one.
    """
    paths = [
        path
        for path in _BACKTICKED.findall(_section())
        if re.fullmatch(r"(?:.*/)?custom_components/[^/]+/", path)
    ]
    assert len(paths) >= 2, f"expected the HACS and by-hand paths, found {paths}"

    for path in paths:
        name = path.rstrip("/").rsplit("/", 1)[-1]
        assert name == _manifest()["domain"], path
        assert (_ROOT / "custom_components" / name / "manifest.json").is_file()


def test_the_repository_to_add_is_the_manifests() -> None:
    """The custom repository the user adds is the one the manifest documents."""
    match = re.search(
        r"Add `(https://github\.com/[^`]+)` as a custom repository", _section()
    )
    assert match, "the section no longer names the repository to add"

    assert match.group(1) == _manifest()["documentation"]


def test_the_version_shown_after_switching_is_the_manifests() -> None:
    """'shows version `2026.x`' is the year the manifest's version starts with."""
    match = re.search(r"shows version `(\d+)\.x`", _section())
    assert match, "the section no longer says which version to look for"

    assert _manifest()["version"].split(".")[0] == match.group(1)


def test_the_older_entry_cut_over_is_the_one_the_code_tells_apart() -> None:
    """'created by upstream releases before v1.4.2' is where user_id keying starts.

    `is_user_id` is what tells the generations apart. Its docstring dates the
    user_id keying from the same release the README names, and the entries
    from before it - keyed by the login, or not keyed at all - are the ones it
    does not take for a user_id, so the setup path re-keys them instead of
    refusing them.
    """
    match = re.search(r"upstream releases before (v\d+\.\d+\.\d+)", _section())
    assert match, "the section no longer names the release that changed the keying"

    doc = inspect.getdoc(is_user_id)
    assert f"upstream {match.group(1)} and later, and this fork, key it by" in doc

    assert not is_user_id("someone@example.com")
    assert not is_user_id(None)
    assert is_user_id("0123456789abcdef")
