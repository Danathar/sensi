"""SECURITY-AI's "Only with an explicit human decision" list, joined to the tree.

`docs/SECURITY-AI.md` tells an agent to stop and explain when a fix needs
something on this list, and its "Always" section points back here by name. Each
bullet states a fact the repository decides somewhere else: which files can
break an install on upgrade, what asks before it edits the manifest, who owns
the manifest's version, and which `.github` paths stay with the owner.

`tests/test_security_ai_doc.py` reads the page's other sections and never this
one, so dropping a bullet, or the rows that back it, left the suite green. The
page is `Edit`-denied to agents, so a failure here is a call for a human to
correct the page or the row, not for an agent to rewrite either.

Each test finds its bullet first, so a reworded bullet fails here instead of
making a search come back empty.
"""

from __future__ import annotations

import functools
import importlib.util
import json
from pathlib import Path
import re

import pytest
import yaml

from tests._workflows import WORKFLOWS, workflow_paths

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "SECURITY-AI.md"
_SETTINGS = _ROOT / ".claude" / "settings.json"
_TIERS = _ROOT / ".github" / "policies" / "risk-tiers.yml"
_CODEOWNERS = _ROOT / ".github" / "CODEOWNERS"
_COMPONENT = _ROOT / "custom_components" / "sensi"
_MANIFEST = "custom_components/sensi/manifest.json"
_HEADING = "Only with an explicit human decision"

_spec = importlib.util.spec_from_file_location(
    "check_requirements_sync", _ROOT / "scripts" / "check_requirements_sync.py"
)
requirements_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(requirements_sync)


@functools.cache
def _bullets() -> tuple[str, ...]:
    """Return the section's bullets, each flattened; Markdown wraps anywhere."""
    text = _DOC.read_text(encoding="utf-8")
    marker = f"\n### {_HEADING}\n"
    assert marker in text, f"docs/SECURITY-AI.md has no {_HEADING!r} section"
    body = re.split(r"\n#{2,3} ", text.split(marker, 1)[1], maxsplit=1)[0]
    return tuple(" ".join(item.split()) for item in body.split("\n- ")[1:])


def _bullet(opening: str) -> str:
    """Return the one bullet that opens with `opening`."""
    found = [item for item in _bullets() if item.startswith(opening)]
    assert len(found) == 1, f"expected one bullet opening {opening!r}: {_bullets()}"
    return found[0]


@functools.cache
def _permissions() -> dict[str, list[str]]:
    return json.loads(_SETTINGS.read_text(encoding="utf-8"))["permissions"]


def _tier(name: str) -> dict:
    tiers = yaml.safe_load(_TIERS.read_text(encoding="utf-8"))["tiers"]
    found = [tier for tier in tiers if tier["name"] == name]
    assert len(found) == 1, f"{_TIERS.name} has no single {name!r} tier"
    return found[0]


def test_the_list_has_the_four_bullets_the_tests_below_read() -> None:
    """A new bullet is a new claim, and it needs a test here."""
    assert [item.split()[0] for item in _bullets()] == [
        "Changing",
        "Editing",
        "Adding",
        "Rotating,",
    ]


def test_the_always_section_points_at_this_list_by_its_name() -> None:
    """The "Always" section sends an agent here, so the name has to match."""
    text = " ".join(_DOC.read_text(encoding="utf-8").split())
    assert (
        "Stop and explain when the correct fix requires something on the "
        f"*{_HEADING[0].lower()}{_HEADING[1:]}* list"
    ) in text


@pytest.mark.parametrize(
    ("words", "source", "evidence"),
    [
        ("an entity `unique_id`", "entity.py", "_attr_unique_id"),
        ("the config flow", "config_flow.py", "ConfigFlow"),
        ("the stored credential shape", "auth.py", "storage.Store"),
    ],
)
def test_each_upgrade_breaking_change_lives_in_a_breaking_tier_file(
    words: str, source: str, evidence: str
) -> None:
    """The bullet's three changes are the ones the labeler marks as breaking."""
    bullet = _bullet("Changing ")
    assert words in bullet
    assert evidence in (_COMPONENT / source).read_text(encoding="utf-8")
    assert f"custom_components/sensi/{source}" in _tier("tier/breaking")["paths"]


def test_the_breaking_bullet_gives_the_reason_the_tier_gives() -> None:
    """Both say the harm is to an existing install, on upgrade."""
    assert "breaks existing installations on upgrade" in _bullet("Changing ")
    description = " ".join(_tier("tier/breaking")["description"].split())
    assert "break an existing install on upgrade" in description


def test_editing_the_manifest_asks_first() -> None:
    """The bullet is a rule; the ask row is what makes it stop for a person."""
    assert "Editing `manifest.json`" in _bullet("Editing ")
    assert f"Edit({_MANIFEST})" in _permissions()["ask"]
    assert _MANIFEST in _tier("tier/breaking")["paths"]


def test_only_the_release_workflow_writes_the_manifest_version() -> None:
    """`version` is owned by the release workflow, and by no other one."""
    assert "`version` is owned by the release workflow" in _bullet("Editing ")
    writers = [
        path.name
        for path in workflow_paths()
        if ".version = $v" in path.read_text(encoding="utf-8")
    ]
    assert writers == ["release.yml"]
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert f'manifest="{_MANIFEST}"' in release


def test_the_two_requirement_lists_are_the_pair_the_sync_check_compares() -> None:
    """The script reading these two files is what makes them move together."""
    assert (
        "`requirements` must move together with `requirements_component.txt`"
        in _bullet("Editing ")
    )
    assert requirements_sync.MANIFEST == _ROOT / _MANIFEST
    assert requirements_sync.REQUIREMENTS == _ROOT / "requirements_component.txt"
    assert "requirements" in json.loads((_ROOT / _MANIFEST).read_text(encoding="utf-8"))


def test_a_runtime_dependency_goes_through_the_manifest_row() -> None:
    """Home Assistant installs what the manifest lists, so that row covers it."""
    assert _bullet("Adding ").startswith("Adding a runtime dependency.")
    assert "every dependency is one they did not choose" in _bullet("Adding ")
    assert f"Edit({_MANIFEST})" in _permissions()["ask"]
    assert "Bash(pip install:*)" in _permissions()["ask"]


def test_the_github_paths_that_carry_permissions_stay_with_the_owner() -> None:
    """Workflows are denied outright; they and the rulesets have a code owner."""
    assert "anything under `.github` that affects permissions or secrets" in _bullet(
        "Rotating, "
    )
    assert "Edit(.github/workflows/**)" in _permissions()["deny"]
    owned = {
        line.split()[0]
        for line in _CODEOWNERS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {"/.github/workflows/", "/.github/rulesets/"} <= owned
