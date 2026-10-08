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
