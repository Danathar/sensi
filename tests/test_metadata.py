"""Repository metadata that decides where this integration can be installed."""

import json
from pathlib import Path

from awesomeversion import AwesomeVersion

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
