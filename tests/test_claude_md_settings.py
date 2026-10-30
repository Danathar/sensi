"""CLAUDE.md's account of `.claude/settings.json`, joined to the file.

`CLAUDE.md` "Project settings and the format hook" is the page that tells an
agent what it may run without a prompt, what asks first, and what is denied
outright, including the list of boundary files it may not edit. Every item in
that bullet is a hand copy of a row in `.claude/settings.json`.

Nothing read it. The settings file is checked from the other side -
`tests/test_agent_format_hook.py` classifies every deny rule, and
`tests/test_security_ai_doc.py` reads the allow list against
`docs/SECURITY-AI.md` - but no test opened this section, so dropping
`docs/SECURITY-AI.md` from its "denied outright" list, or `gh release` from its
"asks first" list, left the whole suite green.

The boundary list is held both ways against the `Edit` deny rows, because a
row added to the deny list is a boundary file this page should name. The
"asks first" list is a set of examples, so each one it names must be an ask
row, and nothing more.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
import re

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_CLAUDE = _ROOT / "CLAUDE.md"
_SETTINGS = _ROOT / ".claude" / "settings.json"
_HEADING = "## Project settings and the format hook"


@functools.cache
def _section() -> str:
    """Return the section, flattened; Markdown wraps anywhere."""
    text = _CLAUDE.read_text(encoding="utf-8")
    assert f"\n{_HEADING}\n" in text, f"CLAUDE.md has no {_HEADING!r} section"
    body = text.split(f"\n{_HEADING}\n", 1)[1].split("\n## ", 1)[0]
    return " ".join(body.split())


def _between(before: str, after: str) -> str:
    """Return the section text between two phrases it must contain."""
    text = _section()
    assert before in text, f"CLAUDE.md no longer says {before!r}"
    rest = text.split(before, 1)[1]
    assert after in rest, f"CLAUDE.md no longer says {after!r} after {before!r}"
    return rest.split(after, 1)[0]


def _ticked(text: str) -> list[str]:
    """Return the backticked items in `text`, in order."""
    return re.findall(r"`([^`]+)`", text)


@functools.cache
def _settings() -> dict:
    return json.loads(_SETTINGS.read_text(encoding="utf-8"))


def _rows(kind: str, tool: str) -> list[str]:
    """Return the `tool(...)` arguments of one permission list."""
    rows = _settings()["permissions"][kind]
    found = [m[1] for row in rows if (m := re.fullmatch(rf"{tool}\((.*)\)", row))]
    assert found, f"no {tool} rows in the {kind} list"
    return found


def _matches(rule: str, command: str) -> bool:
    """Whether a Bash permission rule covers `command`.

    A rule is the exact command, or a prefix ending `:*` that covers the
    prefix on its own and the prefix followed by arguments.
    """
    if rule.endswith(":*"):
        prefix = rule[:-2]
        return command == prefix or command.startswith(prefix + " ")
    return command == rule


def _path(rule: str) -> str:
    """Normalise a path rule to the form the page writes it in."""
    path = rule.removeprefix("./")
    return path[: -len("**")] if path.endswith("/**") else path


def test_matches_reads_both_rule_shapes() -> None:
    """The rule reader distinguishes exact rows from prefix rows."""
    assert _matches("ruff check .", "ruff check .")
    assert not _matches("ruff check .", "ruff check . --fix")
    assert _matches("pytest:*", "pytest")
    assert _matches("pytest:*", "pytest tests/e2e")
    assert not _matches("pytest:*", "pytestx")


@pytest.mark.parametrize(
    "command",
    ["ruff check .", "ruff format --check .", "python3 scripts/run_tests.py"],
)
def test_what_runs_without_asking_is_allowed(command: str) -> None:
    """Claim: these run without asking."""
    named = _ticked(_between("Read-only inspection,", "run without asking"))
    assert command in named, f"CLAUDE.md no longer names {command!r}"
    assert any(_matches(rule, command) for rule in _rows("allow", "Bash"))


def test_ruff_format_asks_because_no_row_allows_it() -> None:
    """Claim: `ruff format .` asks; Claude Code asks for any command no row allows."""
    assert "`ruff format .` asks" in _section()
    for kind in ("allow", "deny"):
        rows = _rows(kind, "Bash")
        assert not any(_matches(rule, "ruff format .") for rule in rows), kind


def test_bare_pytest_asks() -> None:
    """Claim: bare `pytest` asks."""
    assert "Bare `pytest` asks" in _section()
    assert any(_matches(rule, "pytest") for rule in _rows("ask", "Bash"))
    assert not any(_matches(rule, "pytest") for rule in _rows("allow", "Bash"))


def test_what_changes_shared_state_asks_first() -> None:
    """Claim: each command and edit the page lists as shared state asks first."""
    named = _ticked(_between("changes shared state —", "— asks first"))
    commands = [item for item in named if item != "manifest.json"]
    assert commands == ["git push", "gh pr create", "gh pr merge", "gh release"]
    ask = _rows("ask", "Bash")
    missing = [c for c in commands if not any(_matches(r, c) for r in ask)]
    assert not missing, (
        f"CLAUDE.md says these ask first, settings.json does not: {missing}"
    )
    assert "manifest.json" in named
    assert "custom_components/sensi/manifest.json" in _rows("ask", "Edit")


def test_pyproject_and_secrets_are_denied() -> None:
    """Claim: writing pyproject.toml and reading the secret files are denied."""
    named = _ticked(_between("Writing", "are denied outright"))
    assert named[0] == "pyproject.toml"
    assert "pyproject.toml" in {_path(rule) for rule in _rows("deny", "Edit")}
    assert set(named[1:]) == {_path(rule) for rule in _rows("deny", "Read")}


def test_the_boundary_list_is_the_edit_deny_list() -> None:
    """Claim: editing exactly these boundary files is denied, both directions."""
    named = _ticked(
        _between("editing the boundary itself —", "- **A PostToolUse hook**")
    )
    assert len(named) >= 6, named
    denied = {_path(rule) for rule in _rows("deny", "Edit")} - {"pyproject.toml"}
    assert set(named) == denied, (
        "CLAUDE.md's boundary list and the Edit deny rows disagree: "
        f"page only {sorted(set(named) - denied)}, "
        f"settings only {sorted(denied - set(named))}"
    )


def test_the_format_hook_is_the_post_tool_use_hook() -> None:
    """Claim: the PostToolUse hook is this script, run after edits."""
    named = _ticked(_between("**A PostToolUse hook**", "that sorts"))
    assert named == [".claude/hooks/format-edited-python.sh"]
    entries = _settings()["hooks"]["PostToolUse"]
    commands = {
        hook["command"].removeprefix("$CLAUDE_PROJECT_DIR/"): entry["matcher"]
        for entry in entries
        for hook in entry["hooks"]
    }
    assert set(commands) == set(named)
    assert set(commands[named[0]].split("|")) >= {"Edit", "Write"}
