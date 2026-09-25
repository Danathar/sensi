"""The prose summaries of what each workflow checks, against the workflows.

Four files say, in a sentence or a table row, which gates run where:
`CONTRIBUTING.md`'s "CI runs the same things" table, `README.md`'s two lists of
the checks every pull request gets, and the nightly line in
`.claude/checkpoint.md`. `docs/quality.md` says it too, and
`tests/test_quality_doc.py` reads that page; nothing read these four.

They had drifted (#285). `docs/quality.md` was corrected in #258 to say the
nightly run "is not the whole gate", but `CONTRIBUTING.md` still said "the whole
gate nightly", `README.md` said the nightly run "repeats the gate against the
*latest* Home Assistant release" (that leg runs the suite and nothing else), and
the checkpoint said "`nightly.yml` runs the whole gate daily". The nightly
`pinned` job runs neither hassfest nor HACS, and it measures coverage without
enforcing the floor.

Everything here is derived from the workflow files. A step's gate is read from
its `run:` body or its `uses:` action, a workflow's gates are the union over its
jobs, and "every pull request" means the workflows triggered by
`pull_request`. Each prose summary is then compared with that set in both
directions: a gate the prose leaves out fails, and so does one it adds.
"""

from pathlib import Path
import re
import shlex
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_CONTRIBUTING = _ROOT / "CONTRIBUTING.md"
_README = _ROOT / "README.md"
_CHECKPOINT = _ROOT / ".claude" / "checkpoint.md"
_NIGHTLY = "nightly.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """Return a file with its whitespace squashed; Markdown wraps anywhere."""
    return " ".join(_read(path).split())


def _tracked(pattern: str) -> list[str]:
    return subprocess.run(
        ["git", "ls-files", "--", pattern],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()


def _workflow(name: str) -> dict:
    """Return a parsed workflow; PyYAML reads a bare `on:` key as `True`."""
    loaded = yaml.safe_load(_read(_WORKFLOWS / name))
    loaded["on"] = loaded.get("on", loaded.get(True))
    return loaded


_COMMITTED_WORKFLOWS = sorted(
    Path(path).name for path in _tracked(".github/workflows/*.yml")
)

# --------------------------------------------------------------------------
# Gate identities
# --------------------------------------------------------------------------
#
# What a step checks, independent of how it is spelled. A `pytest` carrying
# `--cov-fail-under` is the coverage floor rather than the suite: `ci.yml` is
# the suite's gate, and the prose credits `coverage-gate.yml` with the floor.

_SUITE = "the pytest suite"
_FLOOR = "the coverage floor"
_RUFF_CHECK = "ruff check"
_RUFF_FORMAT = "ruff format"
_REQUIREMENTS_SYNC = "requirements sync"
_HASSFEST = "hassfest"
_HACS = "HACS"

# The two gates that are Home Assistant actions rather than shell commands,
# keyed by the action path before the `@` pin.
_ACTIONS = {
    "home-assistant/actions/hassfest": _HASSFEST,
    "hacs/action": _HACS,
}


def _command_gate(argv: list[str]) -> str | None:
    if argv[0] == "pytest":
        if any(arg.startswith("--cov-fail-under") for arg in argv):
            return _FLOOR
        return _SUITE
    if argv[0] == "ruff" and len(argv) > 1:
        return {"check": _RUFF_CHECK, "format": _RUFF_FORMAT}.get(argv[1])
    if (
        argv[0] in {"python", "python3"}
        and "scripts/check_requirements_sync.py" in argv
    ):
        return _REQUIREMENTS_SYNC
    return None


def _step_gates(step: dict) -> set[str]:
    if "uses" in step:
        gate = _ACTIONS.get(step["uses"].split("@", 1)[0])
        return {gate} if gate else set()
    gates = set()
    # Fold continuations first: the workflows wrap `pytest` over several lines.
    for line in re.sub(r"\\\n\s*", " ", step.get("run", "")).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            argv = shlex.split(line)
        except ValueError:  # an unbalanced quote inside a heredoc body
            continue
        if argv and (gate := _command_gate(argv)):
            gates.add(gate)
    return gates


def _job_gates(workflow: str, job: str) -> set[str]:
    steps = _workflow(workflow)["jobs"][job]["steps"]
    return set().union(*(_step_gates(step) for step in steps))


def _workflow_gates(workflow: str) -> set[str]:
    jobs = _workflow(workflow)["jobs"]
    return set().union(*(_job_gates(workflow, job) for job in jobs))


def _pull_request_workflows() -> list[str]:
    """Return the workflows every pull request runs.

    `pull_request_target` is left out on purpose: `labeler.yml` uses it, runs
    against the base commit, and checks nothing.
    """
    return [
        name
        for name in _COMMITTED_WORKFLOWS
        if "pull_request" in (_workflow(name)["on"] or {})
    ]


def _pull_request_gates() -> set[str]:
    return set().union(*(_workflow_gates(name) for name in _pull_request_workflows()))


# --------------------------------------------------------------------------
# The prose vocabulary
# --------------------------------------------------------------------------
#
# How the four files spell each gate. `ruff` in backticks is both ruff gates,
# because every summary names the tool once for lint and format together.

_TERMS = (
    (re.compile(r"pytest suite|\bthe suite\b"), {_SUITE}),
    (re.compile(r"`ruff`"), {_RUFF_CHECK, _RUFF_FORMAT}),
    (re.compile(r"requirements sync"), {_REQUIREMENTS_SYNC}),
    (re.compile(r"\bhassfest\b"), {_HASSFEST}),
    (re.compile(r"\bHACS\b"), {_HACS}),
    (
        re.compile(
            r"coverage floor|line-coverage floor|line coverage stays at or above"
        ),
        {_FLOOR},
    ),
)

_NEGATION = re.compile(r"\(not ([^)]*)\)")


def _named(text: str) -> set[str]:
    return set().union(*(gates for term, gates in _TERMS if term.search(text)))


def _positive(text: str) -> str:
    """Return the text with its `(not ...)` parentheticals removed."""
    return _NEGATION.sub("", text)


def _negated(text: str) -> str:
    return " ".join(_NEGATION.findall(text))


def test_the_vocabulary_names_exactly_the_gates_the_workflows_run() -> None:
    """Both ways, so a new gate needs a term and a retired gate loses its own."""
    in_workflows = set().union(
        *(_workflow_gates(name) for name in _COMMITTED_WORKFLOWS)
    )
    in_vocabulary = set().union(*(gates for _, gates in _TERMS))
    assert in_workflows == in_vocabulary, (
        f"the workflows run {sorted(in_workflows)}; this module can recognise "
        f"{sorted(in_vocabulary)} in prose"
    )


def test_the_pull_request_workflows_are_the_ones_the_readme_attributes() -> None:
    """The derivation of "every pull request" finds something to compare."""
    assert _pull_request_workflows(), "no workflow runs on pull_request"
    assert _NIGHTLY not in _pull_request_workflows()
    assert _pull_request_gates() - _workflow_gates(_NIGHTLY), (
        "nightly now runs every pull-request gate, so the '(not ...)' lists "
        "below have nothing left to name"
    )


# --------------------------------------------------------------------------
# CONTRIBUTING.md: "CI runs the same things"
# --------------------------------------------------------------------------

_TABLE_ROW = re.compile(r"^\| `\.github/workflows/([\w-]+\.yml)` \| (.+?) \|$", re.M)

# Committed workflows the table leaves out, and why. Checked below, so an entry
# cannot outlive the reason it gives.
_NOT_IN_TABLE = {"release.yml": "publishes a release and enforces nothing"}


def _table() -> dict[str, str]:
    rows = _TABLE_ROW.findall(_read(_CONTRIBUTING))
    assert len(rows) == len(dict(rows)), "CONTRIBUTING.md's table repeats a row"
    return dict(rows)


def test_the_table_rows_are_the_committed_workflows() -> None:
    """Every row is a committed workflow and every workflow has a row."""
    rows = set(_table())
    assert rows - set(_COMMITTED_WORKFLOWS) == set(), (
        f"CONTRIBUTING.md describes {sorted(rows - set(_COMMITTED_WORKFLOWS))}, "
        "which is not committed"
    )
    missing = set(_COMMITTED_WORKFLOWS) - rows - set(_NOT_IN_TABLE)
    assert not missing, (
        f"{sorted(missing)} is committed and CONTRIBUTING.md's table does not "
        "say what it enforces"
    )


@pytest.mark.parametrize("workflow", sorted(_NOT_IN_TABLE))
def test_a_workflow_left_out_of_the_table_still_enforces_nothing(workflow: str) -> None:
    """The exemption holds only while its reason does."""
    assert workflow in _COMMITTED_WORKFLOWS, f"{workflow} is gone; drop the exemption"
    assert workflow not in _table(), f"{workflow} has a row now; drop the exemption"
    assert _workflow_gates(workflow) == set(), (
        f"{workflow} now runs {sorted(_workflow_gates(workflow))}, so the table "
        "has to say so"
    )


@pytest.mark.parametrize(
    "workflow", sorted(set(_table()) - {_NIGHTLY}), ids=lambda name: name
)
def test_each_table_row_names_the_gates_its_workflow_runs(workflow: str) -> None:
    """Claim: "CI runs the same things", row by row."""
    text = _table()[workflow]
    assert "whole gate" not in text
    assert _named(text) == _workflow_gates(workflow), (
        f"CONTRIBUTING.md says {workflow} enforces {sorted(_named(text))}; it "
        f"runs {sorted(_workflow_gates(workflow))}"
    )


def test_the_labeler_row_says_what_the_labeler_does_instead() -> None:
    """A row that names no gate still has to say something true."""
    text = _table()["labeler.yml"]
    assert _workflow_gates("labeler.yml") == set()
    labels = " ".join(_read(_WORKFLOWS / "labeler.yml").split())
    for kind in ("path", "risk-tier", "size"):
        assert kind in text, f"CONTRIBUTING.md's labeler row dropped {kind!r}"
    assert "actions/labeler@" in labels and "classify_pr.py" in labels


# --------------------------------------------------------------------------
# README.md: what every pull request gets
# --------------------------------------------------------------------------

_README_ATTRIBUTED = re.compile(
    r"CI enforces the same checks on every pull request: (.+?)\. A nightly run"
)
_README_BULLET = re.compile(
    r"\*\*CI that runs on every pull request\*\* — (.+?), and a nightly run"
)
_ATTRIBUTION = re.compile(r"([^()]+?)\(`([\w-]+\.yml)`\)")


def _attributions() -> list[tuple[str, str]]:
    found = _README_ATTRIBUTED.search(_flat(_README))
    assert found, "README.md no longer has its 'CI enforces the same checks' sentence"
    pairs = _ATTRIBUTION.findall(found.group(1))
    assert pairs, "README.md's CI sentence attributes no check to a workflow"
    return [(text, workflow) for text, workflow in pairs]


def test_each_readme_attribution_names_the_gates_of_that_workflow() -> None:
    """Claim: "the pytest suite (`ci.yml`), a line-coverage floor ... (`coverage-gate.yml`), ..."."""
    for text, workflow in _attributions():
        assert workflow in _pull_request_workflows(), (
            f"README.md says {workflow} runs on every pull request; it does not"
        )
        assert _named(text) == _workflow_gates(workflow), (
            f"README.md credits {workflow} with {sorted(_named(text))}; it runs "
            f"{sorted(_workflow_gates(workflow))}"
        )


def test_the_readme_attributes_every_pull_request_workflow() -> None:
    """No pull-request workflow is missing from the sentence, and none is extra."""
    attributed = [workflow for _, workflow in _attributions()]
    assert sorted(attributed) == sorted(_pull_request_workflows()), (
        f"README.md attributes checks to {sorted(attributed)}; pull requests run "
        f"{sorted(_pull_request_workflows())}"
    )


def test_the_readme_fork_bullet_names_every_pull_request_gate() -> None:
    """Claim: "**CI that runs on every pull request** — ..." lists every gate."""
    found = _README_BULLET.search(_flat(_README))
    assert found, "README.md's 'CI that runs on every pull request' bullet moved"
    assert _named(found.group(1)) == _pull_request_gates(), (
        f"README.md's fork bullet names {sorted(_named(found.group(1)))}; pull "
        f"requests run {sorted(_pull_request_gates())}"
    )


# --------------------------------------------------------------------------
# The nightly clause, in each file that states it
# --------------------------------------------------------------------------
#
# Each clause names what the `pinned` job runs, lists in `(not ...)` exactly the
# pull-request gates it leaves out, and then names what the `latest` leg runs.

_NIGHTLY_CLAUSES = {
    "CONTRIBUTING.md": lambda: _table()[_NIGHTLY],
    "README.md": lambda: _match(_README, r"A nightly run (.+?advance warning\.)"),
    ".claude/checkpoint.md": lambda: _match(
        _CHECKPOINT, r"`nightly\.yml` runs (.+?advance warning\.)"
    ),
}


def _match(path: Path, pattern: str) -> str:
    found = re.search(pattern, _flat(path))
    assert found, f"{path.relative_to(_ROOT)} no longer states what nightly runs"
    return found.group(1)


def _legs(doc: str) -> tuple[str, str]:
    parts = re.split(r",\s+(?:plus|and runs)\b", _NIGHTLY_CLAUSES[doc](), maxsplit=1)
    assert len(parts) == 2, f"{doc}'s nightly clause no longer names the latest leg"
    assert "*latest*" in parts[1], f"{doc}'s second nightly leg is not the latest one"
    return parts[0], parts[1]


@pytest.mark.parametrize("doc", sorted(_NIGHTLY_CLAUSES))
def test_the_nightly_clause_names_what_the_pinned_job_runs(doc: str) -> None:
    """The gates the clause names outside `(not ...)` are the pinned job's."""
    pinned, _ = _legs(doc)
    runs = _job_gates(_NIGHTLY, "pinned")
    assert _named(_positive(pinned)) == runs, (
        f"{doc} says nightly runs {sorted(_named(_positive(pinned)))}; the pinned "
        f"job runs {sorted(runs)}"
    )


@pytest.mark.parametrize("doc", sorted(_NIGHTLY_CLAUSES))
def test_the_nightly_clause_names_what_the_pinned_job_leaves_out(doc: str) -> None:
    """The `(not ...)` list is exactly the pull-request gates nightly skips."""
    pinned, _ = _legs(doc)
    left_out = _pull_request_gates() - _job_gates(_NIGHTLY, "pinned")
    assert _named(_negated(pinned)) == left_out, (
        f"{doc} says nightly leaves out {sorted(_named(_negated(pinned)))}; it "
        f"leaves out {sorted(left_out)}"
    )


@pytest.mark.parametrize("doc", sorted(_NIGHTLY_CLAUSES))
def test_the_nightly_clause_names_what_the_latest_leg_runs(doc: str) -> None:
    """Claim: "the suite alone against the *latest* Home Assistant"."""
    _, latest = _legs(doc)
    runs = _job_gates(_NIGHTLY, "latest")
    assert _named(latest) == runs, (
        f"{doc} says the latest leg runs {sorted(_named(latest))}; it runs "
        f"{sorted(runs)}"
    )


# --------------------------------------------------------------------------
# "The whole gate", anywhere that states current state
# --------------------------------------------------------------------------

# Dated records say what was true when they were written, and are not edited.
_HISTORICAL = (".claude/session-summary.md", "docs/reflections/")

_WHOLE_GATE = re.compile(
    r"\b(?:runs|repeats) the (?:whole |full |entire |same )?gate\b"
    r"|(?<!not )\bthe (?:whole|full|entire) gate\b",
    re.IGNORECASE,
)


def _current_state_docs() -> list[str]:
    return [path for path in _tracked("*.md") if not path.startswith(_HISTORICAL)]


def test_the_historical_exemption_names_committed_records() -> None:
    """The exemption cannot go dead, and cannot swallow a current doc."""
    tracked = _tracked("*.md")
    for prefix in _HISTORICAL:
        assert any(path.startswith(prefix) for path in tracked), (
            f"{prefix} is exempt below and no longer committed"
        )
    assert str(_CONTRIBUTING.relative_to(_ROOT)) in _current_state_docs()


@pytest.mark.parametrize("path", _current_state_docs())
def test_no_current_doc_says_nightly_runs_the_whole_gate(path: str) -> None:
    """True only while nightly runs every pull-request gate, which it does not."""
    if _pull_request_gates() <= _workflow_gates(_NIGHTLY):
        return
    for sentence in re.split(r"(?<=[.!?])\s+|\s*\|\s*", _flat(_ROOT / path)):
        if "nightly" in sentence.lower():
            assert not _WHOLE_GATE.search(sentence), (
                f"{path} says {sentence!r}, but nightly does not run "
                f"{sorted(_pull_request_gates() - _workflow_gates(_NIGHTLY))}"
            )
