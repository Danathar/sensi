#!/usr/bin/env python3
"""Check that manifest.json requirements and requirements_component.txt agree.

The two files list the same third-party dependencies for two different
consumers - Home Assistant installs from the manifest, the test and dev
environments install from the requirements file - so they drift silently.
"""

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "custom_components" / "sensi" / "manifest.json"
REQUIREMENTS = ROOT / "requirements_component.txt"


# `name[extras] specifiers ; marker`. Only the stdlib is used: validate.yml
# runs this with the runner's bare python3, which may not have `packaging`.
_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*"
    r"(?:\[(?P<extras>[^\]]*)\])?\s*"
    r"(?P<specifier>[^;]*?)\s*"
    r"(?:;\s*(?P<marker>.*))?$"
)

# pip treats `#` as a comment at the start of a line or after whitespace.
_COMMENT = re.compile(r"(?:^|\s)#.*$")


def read_requirements(path: Path) -> set[str]:
    """Return the pinned requirements in a requirements file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return {
        stripped
        for line in lines
        if (stripped := _COMMENT.sub("", line).strip()) and not stripped.startswith("-")
    }


def canonical(requirement: str) -> str:
    """Return a form of a requirement that equal requirements share.

    The name is normalised as PEP 503 does (case, and runs of `-`, `_`, `.`),
    and whitespace, extras order and specifier order no longer matter, so
    `Python_SocketIO == 5.16.4` and `python-socketio==5.16.4` compare equal.
    Anything the pattern does not recognise is compared as written.
    """
    match = _REQUIREMENT.match(requirement.strip())
    if match is None:
        return requirement.strip()

    name = re.sub(r"[-_.]+", "-", match["name"]).lower()
    extras = sorted(
        re.sub(r"[-_.]+", "-", extra.strip()).lower()
        for extra in (match["extras"] or "").split(",")
        if extra.strip()
    )
    specifier = ",".join(
        sorted(
            part for part in re.sub(r"\s+", "", match["specifier"]).split(",") if part
        )
    )
    marker = " ".join((match["marker"] or "").split())

    result = name
    if extras:
        result += f"[{','.join(extras)}]"
    result += specifier
    if marker:
        result += f"; {marker}"
    return result


def main() -> int:
    """Compare the two dependency lists and report any difference."""
    manifest = {
        canonical(requirement): requirement
        for requirement in json.loads(MANIFEST.read_text(encoding="utf-8"))[
            "requirements"
        ]
    }
    requirements = {
        canonical(requirement): requirement
        for requirement in read_requirements(REQUIREMENTS)
    }

    if manifest.keys() == requirements.keys():
        print(f"OK: {len(manifest)} requirement(s) in sync")
        return 0

    for key in sorted(manifest.keys() - requirements.keys()):
        print(
            f"::error file={REQUIREMENTS.name}::in manifest.json only: {manifest[key]}"
        )
    for key in sorted(requirements.keys() - manifest.keys()):
        print(
            f"::error file={MANIFEST.name}::in {REQUIREMENTS.name} only: "
            f"{requirements[key]}"
        )

    print(
        f"\n{MANIFEST.relative_to(ROOT)} and {REQUIREMENTS.relative_to(ROOT)} "
        "list different requirements; update both."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
