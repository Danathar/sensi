""".claude/session-summary.md is half a dated record and half live guidance.

`tests/test_instruction_docs.py` reaches this file three times and reads no
claim it makes. `_PROSE_FILES` puts it through
`test_relative_markdown_links_resolve`, which checks its three relative links.
`_HISTORICAL_RECORDS` exempts it from the repository-wide commit-prefix ban and
asserts only that it stays dated and still carries the exempted wording.
`test_the_markdown_scan_sees_the_files_it_claims_to` requires it to be in the
scanned set. `_STATE_FILES` - the tuple whose members actually get read - is
`(_CHECKPOINT,)` alone.

The comment above `_STATE_FILES` explains why, and it is right as far as it
goes: this file names `tests.yml` and `pyproject.toml`, both correct for the day
the entry describes, and holding a dated record to the current tree is the
retro-edit that removing `ai-fix.yml` argued against.

But `AGENTS.md` routes the next agent here for "decisions already made, so they
are not re-litigated", and three of the sections are guidance rather than
history: *Decisions worth not re-litigating*, *Deliberately left inert*, and
*Notes for whoever is next*. Each of those is a hand copy of a machine-readable
value, and each can go stale in the direction that matters - the record keeps
promising something the tree stopped doing.

So the split is the design here, not a caveat:

- A **dated** claim is checked for being dated, and for still saying what the
  exemption in `test_instruction_docs.py` was granted for. It is never compared
  to the current tree.
- A **live** claim is joined to the artefact that decides it: the write scopes
  `labeler.yml` and `nightly.yml` grant, the coverage flags `ci.yml` does and
  does not pass, the link asymmetry between `CLAUDE.md` and
  `.github/copilot-instructions.md`, the corrections in `.claude/memory/`, and
  the reason `.coveragerc` gives for `pyproject.toml` being gitignored.
- A path the record names in order to describe a state that has since changed
  gets a row in `_NAMED_TO_DESCRIBE_THE_PAST` with its reason, and is asserted
  to *stay* absent - so that list cannot become a way to excuse a lost file.

Conventions carried from `tests/test_prompt_procedures.py` and
`tests/test_slash_commands.py`: every scan asserts how much it found before
asserting anything about it, because every assertion here quantifies over a
list some reader built and a reader that silently returned nothing would turn
the assertion green; and claims are a table, not a cache, so dropping a
sentence from the record means dropping its row in the same diff.

Deliberately NOT asserted, and why:

- **"do not reach into private client state."** `tests/e2e/test_connection.py`
  calls `client._connect()` in five places and reads `client._send_event`, and
  did so already at PR #46, the change this entry describes. `ruff.toml` also
  disables `SLF001` for all of `tests/**` with the opposite reasoning in its
  comment. The rule has never been enforced; making it an assertion would be
  red on arrival and is a maintainer's call, not this module's.
- **`gh label create ... || true` in `nightly.yml`.** The record generalises
  from the labeller incident that `|| true` on a command the next step depends
  on hides the real error, and `nightly.yml` still uses that shape. It is
  argued for in the workflow and covered by
  `tests/test_nightly_workflow.py::test_an_unlabelled_repository_still_gets_its_issue`;
  the labeller half is owned by
  `tests/test_correction_memory.py::test_the_label_step_still_reports_its_own_failure`.
- The `ruff==` pin (`tests/test_validate_workflow.py`), the `FakeSensiBackend`
  class and the coverage-gate percentage
  (`tests/test_instruction_docs.py`), the 100-character label cap and the
  `pull_request_target` note (`tests/test_correction_memory.py`), and the
  permission dicts of the jobs that exist today
  (`tests/test_labeler_workflow.py`, `tests/test_nightly_workflow.py`). Each is
  already asserted where it belongs; a second copy here would be the weaker of
  the two.
"""

from __future__ import annotations

import functools
from pathlib import Path
import re
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent

_SUMMARY = _ROOT / ".claude" / "session-summary.md"
_CHECKPOINT = _ROOT / ".claude" / "checkpoint.md"
_MEMORY = _ROOT / ".claude" / "memory"
_AGENTS = _ROOT / "AGENTS.md"
_CLAUDE = _ROOT / "CLAUDE.md"
_COPILOT = _ROOT / ".github" / "copilot-instructions.md"
_CURSOR = _ROOT / ".cursor" / "rules" / "sensi.mdc"
_COVERAGERC = _ROOT / ".coveragerc"
_GITIGNORE = _ROOT / ".gitignore"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_CI = _WORKFLOWS / "ci.yml"
_COVERAGE_GATE = _WORKFLOWS / "coverage-gate.yml"
_LABELER = _WORKFLOWS / "labeler.yml"
_NIGHTLY = _WORKFLOWS / "nightly.yml"


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """Return a file's text with every run of whitespace collapsed to a space.

    The record is hard-wrapped prose, so a sentence quoted here is split across
    lines at whatever column the wrap fell on. Searching the raw text would
    make every assertion sensitive to re-wrapping rather than to the claim.
    """
    return " ".join(_read(path).split())


def _rel(path: Path) -> str:
    """Return a path relative to the repository root, for failure messages."""
    return path.relative_to(_ROOT).as_posix()


@functools.cache
def _tracked_files() -> frozenset[str]:
    """Return every path git tracks, read from the index rather than a walk.

    A walk would see `coverage.xml`, `__pycache__` and any local virtualenv, so
    a path the record names could "exist" without being committed.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return frozenset(part for part in out.stdout.split("\0") if part)


@functools.cache
def _present_paths() -> frozenset[str]:
    """Return every path in the working tree that git does not ignore.

    `--cached` alone answers "is it committed"; a path that is back on disk
    and not yet staged is still back. `--exclude-standard` drops the ignored
    ones, which is what keeps a devcontainer's own gitignored `pyproject.toml`
    from reading as a re-introduction.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = {part for part in out.stdout.split("\0") if part}
    return frozenset(paths | {Path(path).name for path in paths})


_TRACKED = _tracked_files()
_TRACKED_DIRS = frozenset(
    parent.as_posix() for path in _TRACKED for parent in Path(path).parents
)
_TRACKED_BASENAMES = frozenset(Path(path).name for path in _TRACKED)


# A backticked name that looks like a repository path. Two shapes the prose
# uses that `tests/test_prompt_procedures.py` does not need: a leading dot
# (`.coveragerc`, `.editorconfig`), and an extensionless dotfile.
_PATH_IN_BACKTICKS = re.compile(
    r"`(\.?[A-Za-z0-9_][A-Za-z0-9_./-]*"
    r"\.(?:py|json|toml|ini|yml|yaml|md|mdc|txt|sh|cfg)|"
    r"\.[a-z][a-z0-9]+rc|\.editorconfig|"
    r"\.?[A-Za-z0-9_][A-Za-z0-9_./-]*/)`"
)


def _path_exists(name: str) -> bool:
    """Return whether a path named in prose resolves to something committed.

    Three spellings, because the record uses all three: a repository path, a
    bare file name (it writes `ci.yml`, not `.github/workflows/ci.yml`), and a
    directory.
    """
    cleaned = name.rstrip("/")
    return (
        cleaned in _TRACKED or cleaned in _TRACKED_DIRS or cleaned in _TRACKED_BASENAMES
    )


# Paths the record names in order to describe a state that has since changed.
# Each is asserted to stay absent, with the reason recorded, so this list
# cannot quietly become the reason a lost file goes unnoticed.
_NAMED_TO_DESCRIBE_THE_PAST = {
    "tests.yml": "renamed to ci.yml by PR #44, which this entry records",
    "ai-fix.yml": "removed by PR #118; the entry records why it was inert",
    "pyproject.toml": "gitignored on purpose; the devcontainer image supplies it",
}


# --------------------------------------------------------------------------
# The record is a record: shape, and the basis of its exemption
# --------------------------------------------------------------------------

_ENTRY_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — ", re.MULTILINE)


def _entry_dates() -> list[str]:
    """Return the date of every entry heading, in the order they appear."""
    return _ENTRY_HEADING.findall(_read(_SUMMARY))


def test_the_entry_scan_sees_at_least_one_entry() -> None:
    """Guard the two tests below: no entries would agree with anything."""
    found = _entry_dates()
    assert found, (
        f"{_rel(_SUMMARY)} has no `## YYYY-MM-DD — ` entry heading, so every "
        "claim below about its entries is quantifying over nothing. The date "
        "in that heading is also what qualifies the file for "
        "_HISTORICAL_RECORDS in tests/test_instruction_docs.py"
    )


def test_every_entry_is_dated_in_iso_form() -> None:
    """A record with an undated entry cannot be read as a record.

    `tests/test_instruction_docs.py` requires *a* date heading, because one is
    enough to establish that the file is dated. This requires that no entry
    arrives without one, which is the property a reader relies on.
    """
    headings = [
        line
        for line in _read(_SUMMARY).splitlines()
        if line.startswith("## ") and not line.startswith("### ")
    ]
    undated = [line for line in headings if not _ENTRY_HEADING.match(line)]
    assert not undated, (
        f"{_rel(_SUMMARY)} has entry headings with no ISO date: {undated}. "
        "The whole file is exempt from the repository-wide commit-prefix ban "
        "because it is a dated record; an undated entry is not one"
    )


def test_the_entries_are_newest_first_as_the_header_says() -> None:
    """The header promises an order; nothing else keeps it."""
    assert "Newest first." in _flat(_SUMMARY), (
        f"{_rel(_SUMMARY)} no longer states its own ordering, so drop this "
        "test in the same change that drops the sentence"
    )
    dates = _entry_dates()
    assert dates == sorted(dates, reverse=True), (
        f"{_rel(_SUMMARY)} says entries are newest first, but its entry dates "
        f"are {dates}. A reader who trusts the order reads the wrong entry"
    )


def test_the_record_and_the_checkpoint_point_at_each_other() -> None:
    """Two files, one split: current state there, history here.

    A one-way link survives either file being renamed, and the surviving side
    then reads as the whole story.
    """
    assert "[`.claude/checkpoint.md`](checkpoint.md)" in _flat(_SUMMARY), (
        f"{_rel(_SUMMARY)} no longer links to the live-state file, so a "
        "reader who starts in the history has no way to reach the present"
    )
    assert "[`session-summary.md`](session-summary.md)" in _flat(_CHECKPOINT), (
        f"{_rel(_CHECKPOINT)} no longer links back to {_rel(_SUMMARY)}, so a "
        "reader who starts in the present has no way to reach the reasoning"
    )


def test_agents_md_still_sends_the_next_agent_here() -> None:
    """This module only matters because the record is routed to.

    `AGENTS.md` is what makes the file guidance rather than an archive. If it
    stops naming the record, the live half of this module is checking prose
    nobody is told to read, and the rows below should be reconsidered.
    """
    assert "[`.claude/session-summary.md`](.claude/session-summary.md)" in _flat(
        _AGENTS
    ), (
        f"{_rel(_AGENTS)} no longer points at {_rel(_SUMMARY)}. The live "
        "claims below are only load-bearing while something routes a reader "
        "to them"
    )


# --------------------------------------------------------------------------
# Paths the record names
# --------------------------------------------------------------------------


def test_the_path_scan_finds_the_paths_the_record_names() -> None:
    """Guard the two tests below: an empty list agrees with anything."""
    named = _PATH_IN_BACKTICKS.findall(_read(_SUMMARY))
    assert len(named) >= 15, (
        f"the backticked-path reader found {len(named)} paths in "
        f"{_rel(_SUMMARY)}, which is fewer than the record has always "
        "carried. Either the reader stopped matching a spelling the prose "
        "uses, or the record was gutted"
    )


def test_every_path_the_record_names_is_committed_or_declared_past() -> None:
    """A dead pointer in the file an agent is sent to misdirects it.

    The exception is not "this file is old, so anything goes": a path only
    escapes by being listed in `_NAMED_TO_DESCRIBE_THE_PAST` with the reason it
    describes a state that changed, and the test below holds that list to it.
    """
    named = sorted(set(_PATH_IN_BACKTICKS.findall(_read(_SUMMARY))))
    missing = sorted(
        name
        for name in named
        if name.rstrip("/") not in _NAMED_TO_DESCRIBE_THE_PAST
        and not _path_exists(name)
    )
    assert not missing, (
        f"{_rel(_SUMMARY)} names paths that are not committed: {missing}. "
        "Either the file was renamed and the record's live half now points at "
        "the old name, or the path describes a state that has since changed "
        "and belongs in _NAMED_TO_DESCRIBE_THE_PAST with the reason"
    )


@pytest.mark.parametrize(
    ("name", "reason"),
    sorted(_NAMED_TO_DESCRIBE_THE_PAST.items()),
    ids=lambda value: value[:28],
)
def test_a_path_declared_past_really_is_absent(name: str, reason: str) -> None:
    """The exception list must not become a way to excuse a lost file.

    Read with `--others` as well as `--cached`, the way
    `tests/test_contributor_templates.py` checks `ai-fix.yml`'s waiver: a file
    that is back in the working tree but not yet staged is back, and a row
    that only consults the index would call it absent until the commit lands.
    `--exclude-standard` is what keeps `pyproject.toml` - gitignored on
    purpose - from being reported by a devcontainer that has one.
    """
    cleaned = name.rstrip("/")
    assert cleaned not in _present_paths(), (
        f"`{name}` is present, but {_rel(_SUMMARY)} names it to describe "
        f"something that changed ({reason}). Either it came back, in which "
        "case the row is wrong, or the row was always wrong"
    )


def test_the_workflow_the_renamed_one_became_is_committed() -> None:
    """`tests.yml` is absent *because* it became `ci.yml`, not because it went.

    Asserting only the absence would stay green if both files disappeared,
    which is the failure the row is there to describe.
    """
    assert "tests.yml` → `ci.yml" in _flat(_SUMMARY), (
        f"{_rel(_SUMMARY)} no longer records the tests.yml → ci.yml rename, "
        "so drop its row from _NAMED_TO_DESCRIBE_THE_PAST in this change"
    )
    assert ".github/workflows/ci.yml" in _TRACKED, (
        "the record says tests.yml became ci.yml, and ci.yml is not committed "
        "either. The rename this entry describes did not survive"
    )


# --------------------------------------------------------------------------
# Decisions worth not re-litigating
# --------------------------------------------------------------------------


def test_the_canonical_file_is_linked_and_the_copilot_one_duplicates() -> None:
    """The record's reason is the asymmetry, so check the asymmetry.

    "`CLAUDE.md` and `.cursor/rules/` point at it. `.github/copilot-instructions.md`
    deliberately duplicates the hard rules instead, because Copilot reads that
    file directly and will not follow a link."

    `tests/test_instruction_docs.py` checks that the restated rules agree. What
    is unchecked is the shape the record gives as the reason: two files defer
    by link, one carries the rules itself.
    """
    flat = _flat(_SUMMARY)
    assert "`AGENTS.md` is canonical." in flat, (
        f"{_rel(_SUMMARY)} no longer states that AGENTS.md is canonical; drop "
        "this test in the same change"
    )

    assert "[AGENTS.md](AGENTS.md)" in _read(_CLAUDE), (
        "the record says CLAUDE.md points at AGENTS.md; it no longer carries "
        "a markdown link to it, so the deferral is prose a reader may not "
        "follow"
    )
    assert "@AGENTS.md" in _read(_CURSOR), (
        "the record says .cursor/rules/ points at AGENTS.md; sensi.mdc no "
        "longer carries the @AGENTS.md reference Cursor resolves"
    )

    copilot_links = re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", _read(_COPILOT))
    assert not [
        target for target in copilot_links if target.rstrip("/") == "AGENTS.md"
    ], (
        f"{_rel(_COPILOT)} now links to AGENTS.md. The record's reason for it "
        "duplicating the hard rules is that Copilot reads that file directly "
        "and will not follow a link - if that stopped being true, the entry's "
        "reasoning and the duplication both need revisiting"
    )
    assert "AGENTS.md" in _read(_COPILOT), (
        f"{_rel(_COPILOT)} no longer mentions AGENTS.md at all, so a reader "
        "of the duplicate has no way to find the canonical file"
    )


def _pytest_run_flags(workflow: Path) -> list[str]:
    """Return every argument of every `pytest` invocation in a workflow.

    Read from the `run:` bodies rather than from a parsed command line: the
    point is what the workflow would execute, and a coverage flag can arrive
    on a continuation line.
    """
    document = yaml.safe_load(_read(workflow))
    bodies: list[str] = []
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            body = step.get("run")
            if body and "pytest" in body:
                bodies.append(body)
    return [word for body in bodies for word in body.split()]


def test_coverage_is_measured_by_the_gate_and_not_by_ci() -> None:
    """Coverage lives in the gate workflow, not in the fast signal.

    The split is what makes `ci.yml` the fast "do the tests pass" signal. It
    erodes silently: adding `--cov` to the fast job costs nothing visible and
    leaves two places that measure, one of which nothing enforces.
    """
    assert "Coverage lives in `coverage-gate.yml`, not `ci.yml`." in _flat(_SUMMARY), (
        f"{_rel(_SUMMARY)} no longer records the coverage split; drop this "
        "test in the same change that drops the decision"
    )

    gate_flags = _pytest_run_flags(_COVERAGE_GATE)
    assert gate_flags, (
        f"{_rel(_COVERAGE_GATE)} has no step that runs pytest, so the file "
        "the record names as the owner of measurement measures nothing"
    )
    assert any(flag.startswith("--cov-fail-under") for flag in gate_flags), (
        f"{_rel(_COVERAGE_GATE)} runs pytest without --cov-fail-under, so it "
        "reports coverage rather than enforcing it. The record says this file "
        "is where enforcement lives"
    )

    ci_flags = _pytest_run_flags(_CI)
    assert ci_flags, (
        f"{_rel(_CI)} no longer runs pytest at all, so the record's "
        "description of it as the fast signal is wrong"
    )
    measuring = sorted(flag for flag in ci_flags if flag.startswith("--cov"))
    assert not measuring, (
        f"{_rel(_CI)} now measures coverage ({measuring}). The record says "
        f"the gate owns measurement and {_rel(_CI)} is the fast signal; two "
        "measurements, only one of them enforced, is what that decision "
        "exists to prevent"
    )


# --------------------------------------------------------------------------
# Deliberately left inert
# --------------------------------------------------------------------------

# GitHub's `permissions:` scopes that let a workflow change something. Read as
# a set so a scope added to the schema does not silently become allowed here.
_WRITE_VALUES = frozenset({"write"})


def _write_scopes(workflow: Path) -> set[str]:
    """Return every scope the workflow grants at write level, any job included.

    `tests/test_labeler_workflow.py` and `tests/test_nightly_workflow.py`
    assert the permission dicts of the jobs that exist today. This reads the
    whole file, so a job added later is covered by the record's promise rather
    than escaping it.
    """
    document = yaml.safe_load(_read(workflow))
    blocks = [document.get("permissions")]
    blocks += [job.get("permissions") for job in document["jobs"].values()]
    granted: set[str] = set()
    for block in blocks:
        if isinstance(block, str):
            if block in _WRITE_VALUES:
                granted.add("*")
            continue
        for scope, level in (block or {}).items():
            if level in _WRITE_VALUES:
                granted.add(scope)
    return granted


def test_the_two_live_automations_are_the_two_the_record_names() -> None:
    """Guard the scopes below: the reader must be looking at real workflows."""
    assert "The labeller and the nightly run are live" in _flat(_SUMMARY), (
        f"{_rel(_SUMMARY)} no longer names which automation is live; drop the "
        "two tests below in the same change"
    )
    for workflow in (_LABELER, _NIGHTLY):
        assert workflow.exists(), (
            f"{_rel(_SUMMARY)} describes {_rel(workflow)} as live automation, "
            "and the file is gone"
        )
        assert yaml.safe_load(_read(workflow))["jobs"], (
            f"{_rel(workflow)} has no jobs, so the scope reader below would "
            "find nothing and agree with anything"
        )


def test_the_labeller_can_only_label() -> None:
    """Only adding labels is a write scope, and the scope is checkable.

    A token that can also write contents or approve a review is a different
    automation than the one the record says was left live, and nothing about
    the workflow's behaviour would look different in a diff.
    """
    granted = _write_scopes(_LABELER)
    assert granted == {"pull-requests"}, (
        f"{_rel(_LABELER)} grants write on {sorted(granted)}. The record says "
        "the labeller only adds labels, which is pull-requests: write and "
        "nothing else"
    )


def test_the_nightly_run_can_only_open_its_issue() -> None:
    """One self-closing issue means one issue, and it closes itself.

    Both halves are checkable: the scope it gets, and the fact that the step
    which opens the issue also closes it when the run goes green.
    """
    granted = _write_scopes(_NIGHTLY)
    assert granted == {"issues"}, (
        f"{_rel(_NIGHTLY)} grants write on {sorted(granted)}. The record says "
        "the nightly run only opens an issue, which is issues: write and "
        "nothing else"
    )

    bodies = [
        step["run"]
        for job in yaml.safe_load(_read(_NIGHTLY))["jobs"].values()
        for step in job.get("steps", [])
        if "gh issue create" in step.get("run", "")
    ]
    assert len(bodies) == 1, (
        f"{_rel(_NIGHTLY)} has {len(bodies)} steps that open an issue; the "
        "record promises one"
    )
    body = bodies[0]
    assert body.count("gh issue create") == 1, (
        f"{_rel(_NIGHTLY)} opens more than one issue in a single step, so "
        '"one self-closing issue" understates what a failing night does'
    )
    assert "gh issue close" in body, (
        f"{_rel(_NIGHTLY)} opens an issue and never closes one, so the issue "
        "the record calls self-closing has to be closed by hand"
    )


# --------------------------------------------------------------------------
# Learned the hard way
# --------------------------------------------------------------------------

# What the record says it learned, and the correction that holds the rule.
# One direction only, and deliberately: a correction added after this entry
# was written must not make a dated record fail. The reverse check - that
# every committed correction is well-formed - is
# `tests/test_correction_memory.py`.
_CORRECTIONS_NAMED = {
    "a fake socket.io\nclient must implement `shutdown()`": (
        "fake-socket-must-implement-shutdown.md",
        "shutdown()",
    ),
    "`except ValueError, TypeError:` is valid on 3.14": (
        "except-tuple-without-parentheses-is-valid.md",
        "PEP 758",
    ),
    "the `hass` fixture must be created before anything that patches\n"
    "`Store.async_load`": (
        "hass-fixture-must-be-created-before-store-patches.md",
        "Store.async_load",
    ),
    "temperature assertions need `US_CUSTOMARY_SYSTEM`": (
        "temperature-tests-need-us-customary-units.md",
        "US_CUSTOMARY_SYSTEM",
    ),
    "the tier descriptions exceed GitHub's 100-character\nlabel limit": (
        "github-label-descriptions-are-capped-at-100-chars.md",
        "100 characters",
    ),
}


@pytest.mark.parametrize(
    ("quoted", "correction"),
    sorted(_CORRECTIONS_NAMED.items()),
    ids=lambda value: value[0][:28] if isinstance(value, tuple) else value[:28],
)
def test_a_correction_the_record_names_is_still_recorded(
    quoted: str, correction: tuple[str, str]
) -> None:
    """The record points at `.claude/memory/`; the file has to be there.

    The record summarises each lesson in half a sentence and defers the rule
    to the correction. A correction that is deleted or reworded past the rule
    leaves that pointer aimed at nothing, and the summary is too short to
    stand on its own.
    """
    name, rule = correction
    assert " ".join(quoted.split()) in _flat(_SUMMARY), (
        f"{_rel(_SUMMARY)} no longer names the {name} lesson. The claim table "
        "in this module is a list of claims, not a cache: drop the row in the "
        "same diff that drops the sentence"
    )
    path = _MEMORY / name
    assert path.exists(), (
        f"{_rel(_SUMMARY)} says this lesson is recorded in .claude/memory/, "
        f"and {_rel(path)} is not committed"
    )
    assert rule in _read(path), (
        f"{_rel(path)} no longer says {rule!r}, which is the part of it "
        f"{_rel(_SUMMARY)} summarises. Either the rule changed, in which case "
        "the summary is now wrong, or the correction lost its point"
    )


def test_the_memory_directory_the_record_links_to_has_corrections_in_it() -> None:
    """Guard the rows above: the pointer is to a directory, not to a file."""
    corrections = sorted(
        path.name for path in _MEMORY.glob("*.md") if path.name != "README.md"
    )
    assert len(corrections) >= len(_CORRECTIONS_NAMED), (
        f".claude/memory/ holds {len(corrections)} corrections, fewer than "
        f"the {len(_CORRECTIONS_NAMED)} {_rel(_SUMMARY)} points at: "
        f"{corrections}"
    )


# --------------------------------------------------------------------------
# Notes for whoever is next
# --------------------------------------------------------------------------


def test_the_e2e_fake_the_record_sends_the_next_session_to_exists() -> None:
    """The record names the e2e fake by path, so the path has to hold it.

    The class itself is asserted by `tests/test_instruction_docs.py`. What is
    unchecked is the spelling the record uses: it names a *path*, and a reader
    given `tests/e2e/FakeSensiBackend` looks in `tests/e2e/`.
    """
    assert "`tests/e2e/FakeSensiBackend`" in _flat(_SUMMARY), (
        f"{_rel(_SUMMARY)} no longer names the e2e fake; drop this test in "
        "the same change"
    )
    defining = sorted(
        path
        for path in _TRACKED
        if path.startswith("tests/e2e/")
        and path.endswith(".py")
        and "class FakeSensiBackend" in _read(_ROOT / path)
    )
    assert defining, (
        f"{_rel(_SUMMARY)} sends the next session to tests/e2e/ for "
        "FakeSensiBackend, and no module under tests/e2e/ defines it"
    )


def test_the_reason_pyproject_is_absent_is_stated_the_same_way_twice() -> None:
    """Two hand copies of one reason: the record's, and `.coveragerc`'s.

    "Do not create `pyproject.toml`. It is gitignored because the devcontainer
    image supplies one." `.coveragerc` gives the same reason for existing. If
    the image stops supplying one, both are wrong, and the record is the one
    an agent is told to trust.
    """
    assert "It is gitignored because the devcontainer image supplies one." in _flat(
        _SUMMARY
    ), (
        f"{_rel(_SUMMARY)} no longer gives the reason pyproject.toml is "
        "absent; drop this test in the same change"
    )
    assert "pyproject.toml" in [
        line.strip() for line in _read(_GITIGNORE).splitlines()
    ], (
        f"{_rel(_SUMMARY)} tells the next session pyproject.toml is "
        "gitignored, and .gitignore no longer lists it, so creating one would "
        "now be committable"
    )
    assert "devcontainer image's gitignored pyproject.toml" in _flat(_COVERAGERC), (
        f"{_rel(_COVERAGERC)} no longer gives the devcontainer image as the "
        f"reason it exists. That reason and {_rel(_SUMMARY)}'s are two hand "
        "copies of one fact; they have to move together"
    )


def test_no_workflow_reaches_a_real_thermostat() -> None:
    """No thermostat in CI is a claim about what a workflow can reach.

    A workflow that grew a Sensi credential would make the whole suite's
    "everything the tests talk to is a fake" reading wrong, and the record is
    where that reading is written down.

    No workflow reads a repository secret today - every one of them uses
    `github.token`, which GitHub mints per run. So the assertion is that the
    set stays empty, and the guard is the `github.token` count: a reader that
    found no `${{ }}` expression at all would agree with an empty set too.
    """
    assert "There is no thermostat in CI and there never will be." in _flat(_SUMMARY), (
        f"{_rel(_SUMMARY)} no longer carries the no-thermostat rule; drop "
        "this test in the same change"
    )
    workflows = sorted(_WORKFLOWS.glob("*.yml"))
    assert len(workflows) >= 5, (
        f"the workflow scan found {len(workflows)} files, so this assertion "
        "is not looking at the repository's CI"
    )

    bodies = {workflow: _read(workflow) for workflow in workflows}
    minted = sum(body.count("github.token") for body in bodies.values())
    assert minted >= 2, (
        f"the expression reader found {minted} uses of github.token across "
        "the workflows. It is meant to be reading the same text the secret "
        "scan below reads; finding none means the scan below is vacuous"
    )

    reached = sorted(
        {
            f"{workflow.name}: {name}"
            for workflow, body in bodies.items()
            for name in re.findall(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)", body)
        }
    )
    assert not reached, (
        f"workflows now read repository secrets: {reached}. "
        f"{_rel(_SUMMARY)} tells the next session there is no thermostat in "
        "CI and there never will be; a credential handed to a workflow is how "
        "that stops being true. GITHUB_TOKEN is not an exception here - the "
        "workflows use `github.token`, which is minted per run"
    )
