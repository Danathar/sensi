"""Repository metadata that decides where this integration can be installed."""

from importlib.metadata import PackageNotFoundError, requires, version
import json
from pathlib import Path
import re

from awesomeversion import AwesomeVersion
import pytest

_ROOT = Path(__file__).resolve().parents[1]

# The first Home Assistant release whose wheel declares Requires-Python >=3.14.2
# (2026.2.x still allowed 3.13). The tree uses PEP 758 syntax such as
# `except ValueError, TypeError:`, which is a SyntaxError on 3.13, so a core
# older than this cannot import the integration at all. HACS reads the floor
# from hacs.json and refuses the download instead of leaving a broken install.
FIRST_HOME_ASSISTANT_ON_PYTHON_314 = "2026.3.0"


def test_hacs_declares_a_home_assistant_floor_that_guarantees_python_314() -> None:
    """hacs.json must keep a minimum Home Assistant version at or above 2026.3.0."""

    hacs = json.loads((_ROOT / "hacs.json").read_text(encoding="utf-8"))

    assert "homeassistant" in hacs, (
        "hacs.json must declare a minimum Home Assistant version; without it "
        "HACS installs onto Python 3.13 cores where the integration cannot import"
    )
    assert AwesomeVersion(hacs["homeassistant"]) >= AwesomeVersion(
        FIRST_HOME_ASSISTANT_ON_PYTHON_314
    )


def _pinned_test_dependency_home_assistant() -> str:
    """Return the Home Assistant version the pinned test dependency brings.

    `pytest-homeassistant-custom-component` pins an exact `homeassistant`, so
    the pin in `requirements_test.txt` decides which core the suite runs on.
    The version is read from the installed distribution's metadata, after
    checking the installed copy is the one `requirements_test.txt` names, so a
    stale environment cannot make the comparison pass or fail by accident.
    """

    requirements = (_ROOT / "requirements_test.txt").read_text(encoding="utf-8")
    pin = re.search(
        r"^pytest-homeassistant-custom-component==(\S+)$", requirements, re.MULTILINE
    )
    assert pin, "requirements_test.txt must pin pytest-homeassistant-custom-component"

    try:
        installed = version("pytest-homeassistant-custom-component")
    except PackageNotFoundError:  # pragma: no cover - the suite cannot run without it
        raise AssertionError(
            "pytest-homeassistant-custom-component is not installed"
        ) from None
    if installed != pin.group(1):
        # nightly.yml's latest-Home-Assistant leg upgrades the harness on purpose;
        # the floor is only compared against the pinned harness's core.
        pytest.skip(
            f"installed pytest-homeassistant-custom-component {installed} is not "
            f"the {pin.group(1)} pinned in requirements_test.txt"
        )

    for requirement in requires("pytest-homeassistant-custom-component") or []:
        match = re.match(r"homeassistant==([^\s;]+)", requirement)
        if match:
            return match.group(1)
    raise AssertionError(
        "pytest-homeassistant-custom-component no longer pins homeassistant exactly"
    )


def test_hacs_floor_matches_the_home_assistant_the_tests_run_on() -> None:
    """The version HACS promises must be a version the suite actually runs.

    A floor below the tested core lets a call that only exists in the newer core
    reach older installs untested (#449); a floor above it refuses installs the
    suite shows working. So the two must agree whenever the pin is bumped.
    """

    hacs = json.loads((_ROOT / "hacs.json").read_text(encoding="utf-8"))
    tested = _pinned_test_dependency_home_assistant()

    assert AwesomeVersion(hacs["homeassistant"]) == AwesomeVersion(tested), (
        f'hacs.json declares "homeassistant": "{hacs["homeassistant"]}" but the '
        f"pinned pytest-homeassistant-custom-component runs the suite on Home "
        f"Assistant {tested}; set the hacs.json floor, README.md and AGENTS.md "
        "to the tested version (or bump the pin)"
    )


def _readme_home_assistant_requirement() -> str:
    """Return the Home Assistant bullet under README.md's `## Requirements`."""

    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    section = re.search(
        r"^## Requirements\n(.*?)^## ", readme, re.MULTILINE | re.DOTALL
    )
    assert section, "README.md no longer has a `## Requirements` section"
    bullets = [
        line
        for line in section.group(1).splitlines()
        if line.startswith("- Home Assistant ")
    ]
    assert len(bullets) == 1, (
        "README.md's Requirements section should have exactly one Home Assistant "
        f"bullet, found {len(bullets)}"
    )
    return bullets[0]


def test_readme_requirements_quote_the_hacs_floor() -> None:
    """README.md tells a manual installer the floor HACS enforces for everyone else.

    A manual install skips hacs.json, so the README bullet is the only place
    that reader learns the minimum. It names the floor twice: as the minimum
    and as the release the suite runs on. Both must be the hacs.json value.
    """

    hacs = json.loads((_ROOT / "hacs.json").read_text(encoding="utf-8"))
    floor = hacs["homeassistant"]
    bullet = _readme_home_assistant_requirement()

    minimum = re.match(r"- Home Assistant \*\*(\S+) or newer\*\*", bullet)
    assert minimum, (
        "README.md's Home Assistant requirement no longer reads "
        "'- Home Assistant **<version> or newer**'"
    )
    assert minimum.group(1) == floor, (
        f"README.md asks for Home Assistant {minimum.group(1)} or newer, but "
        f'hacs.json declares "homeassistant": "{floor}"'
    )

    tested = re.search(r"(\S+) is the release the test suite runs against", bullet)
    assert tested, (
        "README.md no longer says which Home Assistant release the test suite "
        "runs against"
    )
    assert tested.group(1) == floor, (
        f"README.md says the suite runs against Home Assistant {tested.group(1)}, "
        f"but hacs.json's floor is {floor} and "
        "test_hacs_floor_matches_the_home_assistant_the_tests_run_on keeps the "
        "two equal"
    )

    others = set(re.findall(r"\b20\d\d\.\d+\.\d+\b", bullet)) - {floor}
    assert not others, (
        f"README.md's Home Assistant requirement also names {sorted(others)}; "
        f"the only release it should name is the hacs.json floor {floor}"
    )

    assert "HACS enforces the floor, a manual install does not" in bullet, (
        "README.md no longer warns that only HACS enforces the floor; a manual "
        "install reads nothing but this bullet"
    )
