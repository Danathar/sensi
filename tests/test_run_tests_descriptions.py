"""Every prose copy of the test wrapper's refusal lists, joined to the wrapper.

`scripts/run_tests.py` runs without a permission prompt, so what it refuses is
a boundary, and three places describe that boundary in prose: the wrapper's own
module docstring, AGENTS.md's test section (the list CLAUDE.md sends readers
to), and docs/SECURITY-AI.md's "Always" bullet. `test_security_ai_doc.py`
checked the last one against `REFUSED_SHORT`/`REFUSED_LONG`; nothing read the
other two. #306 added `--tx` and then `--px` by hand to all three, and its first
commit left the docstring listing five options until the second commit noticed.

Each copy is held to the same claims here, read out of that copy's own
three-rules passage:

- the code-loading list is exactly `REFUSED_SHORT` plus the long options that
  are not aliases of a short one, both directions;
- every option in the write list is one the wrapper refuses as a path-writer,
  and each path-writing category (`REFUSED_WRITE`, `REFUSED_CONFIG`,
  `VALUED_WRITE`) is represented;
- every copy names the same write options, so dropping one from one copy is
  seen;
- the `@FILE` refusal (#295) is stated, since the wrapper applies it before any
  of the three rules.

A fourth copy is caught by `test_every_copy_of_the_list_is_classified`.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "run_tests.py"
_spec = importlib.util.spec_from_file_location("run_tests_descriptions", _SCRIPT)
run_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_tests)

# Long spellings of the short code-loading options. Every copy names the short
# form; the wrapper refuses both.
_LONG_ALIASES = {"--config-file": "-c", "--override-ini": "-o"}

# Where each copy's three-rules passage starts and stops.
_COPIES = {
    "AGENTS.md": ("pytest` with three rules", "Bare `pytest` still takes"),
    "docs/SECURITY-AI.md": ("The wrapper is pytest with three rules", "The full list"),
    "scripts/run_tests.py": ("pytest with three rules", "Running agent-written code"),
}

# Ticked tokens in a write list that are not options: the name of the set the
# docstring defers to, and the example destination.
_NOT_OPTIONS = {"REFUSED_WRITE", "xml:DEST"}


def _text(name: str) -> str:
    path = _ROOT / name
    if path.suffix == ".py":
        text = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
        assert text, f"{name} has no module docstring"
    else:
        text = path.read_text(encoding="utf-8")
    return " ".join(text.split())


def _passage(name: str) -> str:
    start, stop = _COPIES[name]
    text = _text(name)
    assert start in text, f"{name} no longer says {start!r}"
    passage = text.split(start, 1)[1]
    assert stop in passage, f"{name} no longer says {stop!r} after {start!r}"
    return passage.split(stop, 1)[0]


def _parenthetical(passage: str, lead: str) -> list[str]:
    """Return the ticked tokens in the first `( ... )` after `lead`."""
    match = re.search(re.escape(lead) + r"[^(]*\(([^)]*)\)", passage)
    assert match, f"no parenthesised list after {lead!r}"
    return re.findall(r"`([^`]+)`", match[1])


def _refuses(option: str) -> bool:
    if option in run_tests.REFUSED_SHORT:
        return bool(run_tests.refusals([option, "x"]))
    if option == "--cov-report":
        return bool(run_tests.refusals(["--cov-report=xml:DEST"]))
    return bool(run_tests.refusals([f"{option}=x"]))


@pytest.mark.parametrize("name", sorted(_COPIES))
def test_the_code_loading_list_is_the_wrappers(name: str) -> None:
    """Claim: "the options that load code (...) are refused" - the whole set."""
    stated = set(_parenthetical(_passage(name), "load code"))
    assert set(_LONG_ALIASES) <= run_tests.REFUSED_LONG
    assert set(_LONG_ALIASES.values()) <= run_tests.REFUSED_SHORT
    refused = set(run_tests.REFUSED_SHORT) | (
        run_tests.REFUSED_LONG - set(_LONG_ALIASES)
    )
    assert stated == refused, (
        f"{name}: named but not refused: {sorted(stated - refused)}; "
        f"refused but not named: {sorted(refused - stated)}"
    )
    for option in stated:
        assert _refuses(option), option


@pytest.mark.parametrize("name", sorted(_COPIES))
def test_the_write_list_names_only_path_writers(name: str) -> None:
    """Claim: "the options that write or delete a path of their own (...)"."""
    tokens = _parenthetical(_passage(name), "a path of their own")
    options = {token for token in tokens if token not in _NOT_OPTIONS}
    writers = (
        run_tests.REFUSED_WRITE | run_tests.REFUSED_CONFIG | run_tests.VALUED_WRITE
    )
    assert options <= writers, f"{name}: not a path-writer: {options - writers}"
    for option in options:
        assert _refuses(option), option
    for category in ("REFUSED_WRITE", "REFUSED_CONFIG", "VALUED_WRITE"):
        assert options & getattr(run_tests, category), (
            f"{name}'s write list names nothing from {category}"
        )
    assert not run_tests.refusals(["--cov-report=term-missing"])


def test_every_copy_names_the_same_write_options() -> None:
    """The write lists are examples, but #306 kept them in sync; so must a change."""
    named = {
        name: {
            token
            for token in _parenthetical(_passage(name), "a path of their own")
            if token not in _NOT_OPTIONS
        }
        for name in _COPIES
    }
    first = named[min(named)]
    differing = {
        name: options ^ first for name, options in named.items() if options != first
    }
    assert not differing, f"write lists differ from {min(named)}'s: {differing}"


@pytest.mark.parametrize("name", sorted(_COPIES))
def test_the_argument_file_refusal_is_stated(name: str) -> None:
    """Claim: an argument starting with `@` is refused before the three rules."""
    sentences = re.split(r"(?<=[.:;])\s+", _passage(name))
    stating = [s for s in sentences if "`@`" in s and "refused" in s]
    assert stating, f"{name} does not say an `@` argument is refused"
    assert run_tests.FROMFILE_PREFIX == "@"
    assert run_tests.refusals(["@args.txt"])
    assert run_tests.refusals(["tests", "-k", "@args.txt"])


def test_claude_md_sends_readers_to_a_copy_that_has_the_list() -> None:
    """Claim (CLAUDE.md): "(AGENTS.md has the list)"."""
    assert "(AGENTS.md has the list)" in _text("CLAUDE.md")
    assert "AGENTS.md" in _COPIES


def test_every_copy_of_the_list_is_classified() -> None:
    """A tracked file outside tests/ that spells out the list must be in _COPIES."""
    listed = subprocess.run(
        ["git", "grep", "-l", "-F", "`--confcutdir`", "--", ":!tests"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode in (0, 1), listed.stderr
    found = set(listed.stdout.split())
    assert found == set(_COPIES), (
        f"unclassified copies: {sorted(found - set(_COPIES))}; "
        f"copies that no longer list it: {sorted(set(_COPIES) - found)}"
    )
