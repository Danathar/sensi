#!/usr/bin/env python3
"""Check that manifest.json requirements and requirements_component.txt agree.

The two files list the same third-party dependencies for two different
consumers - Home Assistant installs from the manifest, the test and dev
environments install from the requirements file - so they drift silently.
"""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "custom_components" / "sensi" / "manifest.json"
REQUIREMENTS = ROOT / "requirements_component.txt"


def read_requirements(path: Path) -> set[str]:
    """Return the pinned requirements in a requirements file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return {
        stripped
        for line in lines
        if (stripped := line.strip()) and not stripped.startswith(("#", "-"))
    }


def main() -> int:
    """Compare the two dependency lists and report any difference."""
    manifest = set(json.loads(MANIFEST.read_text(encoding="utf-8"))["requirements"])
    requirements = read_requirements(REQUIREMENTS)

    if manifest == requirements:
        print(f"OK: {len(manifest)} requirement(s) in sync")
        return 0

    for missing in sorted(manifest - requirements):
        print(f"::error file={REQUIREMENTS.name}::in manifest.json only: {missing}")
    for extra in sorted(requirements - manifest):
        print(f"::error file={MANIFEST.name}::in {REQUIREMENTS.name} only: {extra}")

    print(
        f"\n{MANIFEST.relative_to(ROOT)} and {REQUIREMENTS.relative_to(ROOT)} "
        "list different requirements; update both."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
