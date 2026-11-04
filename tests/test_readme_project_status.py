"""README.md's "Project status" section is what a new user reads first.

It says what beta means here: what CI runs on every pull request, that CI never
reaches the real backend, and what has to hold before the integration is called
stable. Each of those is a statement about this tree, and nothing compared them
with it.

It also said how many releases there had been, naming the only one. That went
stale the day the next release was published, and no test could have kept it
right: `release.yml`'s `prepare` job opens a pull request that changes the
manifest alone, and the tag lands after the merge, so a test pinning the count
would go red on every release. The section therefore states no count and no
version, and that is the claim read here.
"""

from __future__ import annotations

import configparser
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest
import yaml

from custom_components.sensi.client import SOCKET_URL

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"
_STRATEGY = _ROOT / "docs" / "strategy.md"
_GUARD_TEST = _ROOT / "tests" / "test_ci_network_guard.py"
_WORKFLOWS = _ROOT / ".github" / "workflows"

_SECTION = "## Project status"

_LINK = re.compile(r"\]\(#([^)]+)\)")
_HEADING = re.compile(r"^#{1,6} (.+)$", re.MULTILINE)
_CHECKBOX = re.compile(r"^- \[[ x]\] (.+)$", re.MULTILINE)


def _section() -> str:
    """Return the "Project status" section."""
    text = _README.read_text(encoding="utf-8")
    start = text.index(f"\n{_SECTION}\n") + len(_SECTION) + 2
    return text[start : text.index("\n## ", start)]


def _anchor(heading: str) -> str:
    """Return the anchor GitHub gives a heading."""
    kept = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return kept.replace(" ", "-")


def _workflow(name: str) -> dict:
    """Return a parsed workflow file."""
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    """Return a workflow's triggers; YAML reads the bare key `on` as True."""
    return workflow[True]


def _runs(job: dict) -> list[str]:
    """Return the shell of every step of a job."""
    return [step["run"] for step in job["steps"] if "run" in step]


def test_the_section_states_no_release_count_or_version() -> None:
    """A count or a version here goes stale with the next release."""
    section = _section()
    assert not re.search(r"\b\d{4}\.\d+\.\d+", section)
    assert not re.search(
        r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten) releases? so far",
        section,
        re.IGNORECASE,
    )


def test_the_section_says_beta_as_the_strategy_page_does() -> None:
    """The two pages agree on the status, and the strategy page links here."""
    strategy = _STRATEGY.read_text(encoding="utf-8")
    assert _section().lstrip().startswith("**Beta.**")
    assert "The integration is in beta." in strategy
    assert f"(../README.md#{_anchor(_SECTION[3:])})" in strategy


def test_every_link_in_the_section_reaches_a_heading() -> None:
    """The links to the feature list, the limits and the upgrade steps resolve."""
    headings = {
        _anchor(heading)
        for heading in _HEADING.findall(_README.read_text(encoding="utf-8"))
    }
    links = _LINK.findall(_section())
    assert {"what-you-get", "limitations", "coming-from-ipraksensi"} <= set(links)
    assert set(links) <= headings


def test_every_pull_request_runs_the_unit_and_end_to_end_tiers() -> None:
    """Bare `pytest` on a pull request collects the unit suite and tests/e2e."""
    assert "Every pull request runs the unit suite" in _section()
    workflow = _workflow("ci.yml")
    assert "pull_request" in _triggers(workflow)
    assert "pytest" in _runs(workflow["jobs"]["pytest"])

    config = configparser.ConfigParser()
    config.read(_ROOT / "pytest.ini", encoding="utf-8")
    assert config["pytest"]["testpaths"].split() == ["tests"]
    assert "addopts" not in config["pytest"]
    assert list((_ROOT / "tests" / "e2e").glob("test_*.py"))


def test_every_pull_request_runs_the_coverage_floor() -> None:
    """The floor the section names is enforced on pull requests."""
    assert "a coverage floor" in _section()
    workflow = _workflow("coverage-gate.yml")
    assert "pull_request" in _triggers(workflow)
    assert any(
        "--cov-fail-under=" in run
        for job in workflow["jobs"].values()
        for run in _runs(job)
    )


def test_the_host_ci_never_reaches_is_the_one_the_client_connects_to() -> None:
    """The section names the host the client really opens its socket to."""
    host = urlparse(SOCKET_URL).hostname
    assert f"Nothing in CI reaches `{host}` or a physical thermostat." in _section()


def test_the_network_guard_quotes_the_section() -> None:
    """The guard's tests say which sentence they back; that sentence is here."""
    guard = " ".join(_GUARD_TEST.read_text(encoding="utf-8").split())
    sentence = "Nothing in CI reaches `rt.sensiapi.io` or a physical thermostat."
    assert sentence in guard
    assert sentence in _section()


def test_the_nightly_run_the_last_condition_names_tracks_latest() -> None:
    """Stable needs the nightly latest leg green, so that leg has to exist."""
    conditions = _CHECKBOX.findall(_section())
    assert conditions
    assert conditions[-1] == (
        "The CI suite, coverage gate, and nightly run against the latest "
        "Home Assistant release are green on `master`."
    )

    workflow = _workflow("nightly.yml")
    assert "schedule" in _triggers(workflow)
    assert any(
        "--upgrade pytest-homeassistant-custom-component" in run
        for run in _runs(workflow["jobs"]["latest"])
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "Three consecutive releases, spanning at least three months",
        "checked against a real thermostat before it was tagged",
        "Upgrading in place from an `iprak/sensi` install",
        "No open bug report",
    ],
)
def test_the_other_stable_conditions_are_still_listed(phrase: str) -> None:
    """The strategy page defers to this list, so it keeps every condition."""
    assert any(phrase in condition for condition in _CHECKBOX.findall(_section()))
