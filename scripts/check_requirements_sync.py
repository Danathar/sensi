#!/usr/bin/env python3
"""Check that manifest.json requirements and requirements_component.txt agree.

The two files list the same third-party dependencies for two different
consumers - Home Assistant installs from the manifest, the test and dev
environments install from the requirements file - so they drift silently.

Requirements are compared the way pip reads them, not as strings: an inline
comment, spaces around an operator, or another spelling of the same project
name is the same pin. This is done with the standard library only, because the
CI job that runs it installs nothing - `packaging` is not guaranteed to be there.
"""

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "custom_components" / "sensi" / "manifest.json"
REQUIREMENTS = ROOT / "requirements_component.txt"

# pip ends a line at a `#` that starts it or follows whitespace.
_INLINE_COMMENT = re.compile(r"(^|\s+)#.*$")
# PEP 508: a name, optional extras, then whatever specifier or URL follows.
_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[([^\]]*)\])?\s*(.*)$"
)


def _canonical_name(name: str) -> str:
    """Return a project name as PEP 503 normalises it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def normalise(requirement: str) -> str:
    """Return one spelling for every way of writing the same requirement.

    The name follows PEP 503, extras are sorted, and whitespace is dropped from
    the specifier and collapsed in the marker. What is left to differ is what
    pip would actually install differently.
    """
    requirement, _, marker = requirement.partition(";")
    match = _REQUIREMENT.match(requirement.strip())
    if match is None:
        return " ".join(requirement.split())
    name, extras, specifier = match.groups()
    specifier = specifier.strip()
    if specifier.startswith("(") and specifier.endswith(")"):
        specifier = specifier[1:-1]

    canonical = _canonical_name(name)
    extra_names = sorted(
        {_canonical_name(extra.strip()) for extra in (extras or "").split(",")} - {""}
    )
    if extra_names:
        canonical += f"[{','.join(extra_names)}]"
    canonical += ",".join(
        sorted("".join(part.split()) for part in specifier.split(",") if part.strip())
    )
    if marker := " ".join(marker.split()):
        canonical += f"; {marker}"
    return canonical


def read_requirements(path: Path) -> set[str]:
    """Return the pinned requirements in a requirements file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return {
        stripped
        for line in lines
        if (stripped := _INLINE_COMMENT.sub("", line).strip())
        and not stripped.startswith("-")
    }


def main() -> int:
    """Compare the two dependency lists and report any difference."""
    manifest = {
        normalise(requirement): requirement
        for requirement in json.loads(MANIFEST.read_text(encoding="utf-8"))[
            "requirements"
        ]
    }
    requirements = {
        normalise(requirement): requirement
        for requirement in read_requirements(REQUIREMENTS)
    }

    if manifest.keys() == requirements.keys():
        print(f"OK: {len(manifest)} requirement(s) in sync")
        return 0

    for missing in sorted(manifest.keys() - requirements.keys()):
        print(
            f"::error file={REQUIREMENTS.name}::in manifest.json only: "
            f"{manifest[missing]}"
        )
    for extra in sorted(requirements.keys() - manifest.keys()):
        print(
            f"::error file={MANIFEST.name}::in {REQUIREMENTS.name} only: "
            f"{requirements[extra]}"
        )

    print(
        f"\n{MANIFEST.relative_to(ROOT)} and {REQUIREMENTS.relative_to(ROOT)} "
        "list different requirements; update both."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
