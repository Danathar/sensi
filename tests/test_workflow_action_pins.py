"""Every action a workflow runs must be pinned to a commit, not a tag.

Not part of the component, so not measured by the coverage gate - but these
workflows hold `contents: write`, the signing key and, in one case, an API key,
and a `uses:` line is the one place where code nobody in this repository wrote
gets to run with all of that. A tag is a pointer the upstream owner can move;
a commit is not.

#114 pinned them. This is what stops the next `uses:` from arriving unpinned,
because a mutable tag is invisible in review - `actions/checkout@v4` looks
exactly as deliberate as the forty hex characters that replaced it.

The version comment is checked too. Without it the pins are unreadable and,
more to the point, Dependabot has nothing to rewrite: it reads the comment to
know which release a SHA stands for.
"""

from pathlib import Path
import re

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_DEPENDABOT = _ROOT / ".github" / "dependabot.yml"

# `uses: owner/repo@ref  # comment`, tolerating `- uses:` and any indentation.
_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)(?:\s+#\s*(?P<comment>.*?))?\s*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _action_references() -> list[tuple[str, int, str, str | None]]:
    """(workflow, line number, ref, comment) for every external action used."""
    found: list[tuple[str, int, str, str | None]] = []
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _USES.match(line)
            if match is None:
                continue
            ref = match.group("ref")
            # A local composite action and a container image are not tags that
            # somebody upstream can re-point, so neither is in scope here.
            if ref.startswith(("./", "docker://")):
                continue
            found.append((path.name, number, ref, match.group("comment")))
    return found


def test_the_parser_finds_the_action_references_that_are_there() -> None:
    """Otherwise every assertion below passes by finding nothing.

    A `uses:` written in a form this regex does not match would be invisible to
    the checks in this module and would pass by never being seen - which is the
    exact failure they exist to prevent.
    """
    references = _action_references()
    assert len(references) >= 15, "the parser stopped seeing most of the workflows"

    # Cross-check against a dumber count: every line mentioning `uses:` that is
    # not a comment should have been parsed.
    raw = 0
    for path in _WORKFLOWS.glob("*.yml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.match(r"^(?:-\s*)?uses:", stripped):
                raw += 1
    assert len(references) == raw, (
        "a `uses:` line was written in a form the parser missed"
    )


@pytest.mark.parametrize("workflow,line,ref,comment", _action_references())
def test_every_action_is_pinned_to_a_commit(
    workflow: str, line: int, ref: str, comment: str | None
) -> None:
    """A tag is a pointer its owner can move; a commit is not."""
    assert "@" in ref, f"{workflow}:{line}: {ref} names no ref at all"
    _, _, version = ref.partition("@")

    assert _SHA.match(version), (
        f"{workflow}:{line}: {ref} is pinned to {version!r}, which is a mutable "
        "tag - use the full 40-character commit SHA"
    )


@pytest.mark.parametrize("workflow,line,ref,comment", _action_references())
def test_every_pin_says_which_release_it_is(
    workflow: str, line: int, ref: str, comment: str | None
) -> None:
    """Forty hex characters say nothing on their own.

    The comment is also what Dependabot rewrites, so a pin without one stops
    being updated rather than merely being hard to read.
    """
    assert comment, (
        f"{workflow}:{line}: {ref} has no trailing version comment; add "
        "`# vX.Y.Z` so the pin is readable and Dependabot can move it"
    )


# --------------------------------------------------------------------------
# The half that keeps the pins from going stale.
# --------------------------------------------------------------------------


def test_dependabot_is_configured_to_update_the_actions() -> None:
    """Pinning without update automation trades one problem for another.

    "Pinned" becomes "stuck on whatever was current the day it was pinned",
    including through a security fix, and nothing in the repository would say
    so.
    """
    assert _DEPENDABOT.exists(), ".github/dependabot.yml is missing"
    config = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))

    ecosystems = {entry.get("package-ecosystem") for entry in config.get("updates", [])}
    assert "github-actions" in ecosystems, (
        "Dependabot does not watch github-actions, so the SHAs above will not move"
    )


def test_dependabot_watches_the_directory_the_workflows_are_in() -> None:
    """`/` is the repository root; Dependabot finds .github/workflows from there.

    Pointing it at `/.github/workflows` instead is the common mistake, and it
    silently matches nothing.
    """
    config = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))
    actions = [
        entry
        for entry in config["updates"]
        if entry.get("package-ecosystem") == "github-actions"
    ]

    assert actions
    for entry in actions:
        assert entry.get("directory") == "/", (
            f"directory is {entry.get('directory')!r}; github-actions updates are "
            "configured from the repository root"
        )


def test_every_workflow_still_parses_as_yaml() -> None:
    """Cheap, and the pins were applied by rewriting lines."""
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        assert yaml.safe_load(path.read_text(encoding="utf-8")), f"{path.name} is empty"
