"""The operations runbook, `docs/ai-ops-runbook.md`, joined to what it describes.

The runbook is the page the person holding the merge button reads when
something goes red. Every entry names a signal - a workflow, a job, a step, an
issue title, a schedule - and a response. Until this module existed only
`tests/test_instruction_docs.py` read it, and only to resolve its links, so
nothing noticed when a signal it names stopped being the one the repository
emits, or when the repository started emitting one it does not name.

The second case had already happened. On 2026-10-01 the monthly Release run
went red at `Validate the version` with "Tag 2026.9.0 already exists": the
component had changed since the last release, so the run went ahead, but the
version-bump pull request had not been merged first, so the manifest still
carried the published number. The runbook's release row called a healthy run
one that "publishes, or skips", and its "A release is refused" entry named only
`Require green checks on this commit`, so the page had no entry for the run the
repository actually produced. `test_every_release_refusal_has_a_runbook_entry`
is the guard for that: every step of the `release` job that stops a run with
an `::error::` has to be named in that entry.

Everything else here joins a name on the page to the file that defines it:
workflow names to `name:`, schedules to `cron:`, job and step names to the jobs
and steps, the nightly issue title to the script that files it, "six required
checks" to `.github/rulesets/master.json`, and the security-boundary path list
to `docs/SECURITY-AI.md` and `.github/CODEOWNERS`.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest
import yaml

from tests._workflows import workflow_paths
from tests.test_required_checks_match_workflows import _job_names

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "ai-ops-runbook.md"
_SECURITY = _ROOT / "docs" / "SECURITY-AI.md"
_CODEOWNERS = _ROOT / ".github" / "CODEOWNERS"
_DEFINITION = _ROOT / ".github" / "rulesets" / "master.json"
_CHECK_RULESET = _ROOT / "scripts" / "check_ruleset.py"

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}


def _text() -> str:
    return _DOC.read_text(encoding="utf-8")


def _section(heading: str) -> str:
    """Return the body of the `## heading` section, up to the next `## `."""
    match = re.search(
        rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)",
        _text(),
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"docs/ai-ops-runbook.md has no '## {heading}' section"
    return match.group(1)


def _squashed(text: str) -> str:
    """Collapse the wrapping so a phrase split across lines still matches."""
    return re.sub(r"\s+", " ", text)


def _ticked(text: str) -> list[str]:
    """Every backticked span, with wrapping collapsed."""
    return [_squashed(span) for span in re.findall(r"`([^`]+)`", text)]


def _workflows_by_name() -> dict[str, dict]:
    documents = {}
    for path in workflow_paths():
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        documents[document["name"]] = document
    assert documents, "no workflows found"
    return documents


def _workflow(name: str) -> dict:
    workflows = _workflows_by_name()
    assert name in workflows, (
        f"docs/ai-ops-runbook.md names the {name!r} workflow, but no workflow "
        f"is called that; the workflows are {sorted(workflows)}"
    )
    return workflows[name]


def _job(workflow: dict, name: str) -> dict:
    names = _job_names(workflow)
    assert name in names, (
        f"docs/ai-ops-runbook.md names the job {name!r} in the "
        f"{workflow['name']!r} workflow, which has {sorted(names)}"
    )
    return names[name]


def _step_names(job: dict) -> list[str]:
    return [step["name"] for step in job.get("steps", []) if "name" in step]


def _watch_rows() -> dict[str, tuple[str, str]]:
    """Read the 'What to watch' table: signal -> (where it shows up, healthy)."""
    rows = {}
    for line in _section("What to watch").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("Signal", "") or set(cells[0]) <= {"-"}:
            continue
        rows[cells[0]] = (cells[1], cells[2])
    assert rows, "the 'What to watch' table has no rows"
    return rows


def _named_workflows(cell: str) -> list[str]:
    """Split `the CI, Coverage gate and Validate workflows` into the names."""
    match = re.fullmatch(r"the (.+?) workflows?", cell)
    assert match, f"cannot read workflow names out of {cell!r}"
    return [
        name.strip()
        for name in re.split(r",\s*(?:and\s+)?|\s+and\s+", match.group(1))
        if name.strip()
    ]


# --- What to watch ----------------------------------------------------------


def test_every_workflow_the_watch_table_names_exists() -> None:
    """Each workflow in the "Where it shows up" column is a workflow `name:`."""
    named = [
        name
        for where, _ in _watch_rows().values()
        if where.endswith(("workflow", "workflows"))
        for name in _named_workflows(where)
    ]
    assert {"CI", "Nightly compliance", "Release"} <= set(named), named
    for name in named:
        _workflow(name)


def test_the_required_check_count_and_workflows_match_the_ruleset() -> None:
    """`all six required checks`, shown on the workflows the row names."""
    where, healthy = _watch_rows()["Checks on `master`"]
    count = re.search(r"all (\w+) required checks", healthy)
    assert count, healthy

    definition = json.loads(_DEFINITION.read_text(encoding="utf-8"))
    required = {
        check["context"]
        for rule in definition["rules"]
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    }
    assert _NUMBER_WORDS[count.group(1)] == len(required)

    # Each required check comes from one of the workflows the row points at,
    # and each of those workflows produces at least one of them.
    producers: dict[str, set[str]] = {}
    for name, workflow in _workflows_by_name().items():
        for check in _job_names(workflow):
            if check in required:
                producers.setdefault(name, set()).add(check)
    assert set().union(*producers.values()) == required
    assert set(_named_workflows(where)) == set(producers), producers


@pytest.mark.parametrize(
    ("signal", "workflow", "cron"),
    [
        ("Nightly compliance, 06:17 UTC", "Nightly compliance", "17 6 * * *"),
        ("Monthly release, 09:00 UTC on the 1st", "Release", "0 9 1 * *"),
    ],
)
def test_the_schedules_match_the_cron(signal: str, workflow: str, cron: str) -> None:
    """The times in the table are the `cron:` the workflow runs on."""
    assert signal in _watch_rows()
    document = _workflow(workflow)
    triggers = document.get("on", document.get(True))
    assert [entry["cron"] for entry in triggers["schedule"]] == [cron]


def test_the_nightly_job_names_are_the_nightly_jobs() -> None:
    """Both nightly legs the runbook names are jobs of the nightly workflow."""
    nightly = _workflow("Nightly compliance")
    ticked = _ticked(_section("What to watch")) + _ticked(
        _section("The nightly run fails")
    )
    for name in ("pinned Home Assistant", "latest Home Assistant (advance warning)"):
        assert name in ticked, f"the runbook no longer names {name!r}"
        _job(nightly, name)


def test_the_nightly_issue_title_is_the_one_the_report_job_files() -> None:
    """The issue title to watch for is the one the report job opens."""
    report = _job(_workflow("Nightly compliance"), "report")
    script = "\n".join(step.get("run", "") for step in report["steps"])
    title = re.search(r'^\s*TITLE="([^"]+)"', script, flags=re.MULTILINE)
    assert title, "the report job no longer sets TITLE"
    assert f"`{title.group(1)}`" in _section("What to watch")
    assert f"`{title.group(1)}`" in _section("The nightly run fails")


def test_only_the_pinned_leg_decides_whether_the_nightly_issue_opens() -> None:
    """Claim: a failure in `latest ...` alone opens no issue and gates nothing."""
    nightly = _workflow("Nightly compliance")
    latest = _job(nightly, "latest Home Assistant (advance warning)")
    assert latest.get("continue-on-error") is True

    report = _job(nightly, "report")
    script = "\n".join(step.get("run", "") for step in report["steps"])
    conditions = [
        line.strip() for line in script.splitlines() if re.match(r"\s*(el)?if \[", line)
    ]
    assert any('"$PINNED" = "success"' in line for line in conditions), conditions
    assert not any("LATEST" in line for line in conditions), conditions


def test_the_resolved_versions_step_is_in_the_pinned_job() -> None:
    """The step summary the runbook says to compare exists on the pinned leg."""
    assert "Record resolved versions" in _ticked(_section("The nightly run fails"))
    pinned = _job(_workflow("Nightly compliance"), "pinned Home Assistant")
    assert "Record resolved versions" in _step_names(pinned)


# --- Releases ---------------------------------------------------------------


def _refusing_release_steps() -> list[str]:
    """Return the `release` job steps that stop a run with an `::error::`."""
    release = _job(_workflow("Release"), "release")
    return [
        step["name"]
        for step in release["steps"]
        if "::error::" in step.get("run", "") and "exit 1" in step.get("run", "")
    ]


def test_every_release_refusal_has_a_runbook_entry() -> None:
    """A red Release run has to land on an entry that says what to do.

    The scheduled run on 2026-10-01 stopped at `Validate the version` because
    the version-bump pull request had not been merged, and the runbook named
    only `Require green checks on this commit`.
    """
    refusing = _refusing_release_steps()
    assert "Require green checks on this commit" in refusing
    assert "Validate the version" in refusing

    ticked = _ticked(_section("A release is refused"))
    missing = [name for name in refusing if name not in ticked]
    assert not missing, (
        "the release job can stop at these steps, but docs/ai-ops-runbook.md's "
        f"'A release is refused' entry does not name them: {missing}"
    )


def test_every_step_the_release_entry_names_is_a_release_step() -> None:
    """No step name in the release entry is stale or misspelled."""
    release = _job(_workflow("Release"), "release")
    steps = _step_names(release)
    ticked = _ticked(_section("A release is refused"))
    named = [span for span in ticked if span in steps]
    assert named, "the release entry names no step of the release job"
    for span in ticked:
        if span[:1].isupper() and " " in span:
            assert span in steps, f"{span!r} is not a step of the release job"


def test_the_release_entry_names_the_bump_pull_request_remedy() -> None:
    """The tag-exists refusal is fixed by running `prepare`, not by a rerun."""
    release = _job(_workflow("Release"), "release")
    validate = next(
        s for s in release["steps"] if s.get("name") == "Validate the version"
    )
    assert "already exists" in validate["run"]
    assert "'prepare'" in validate["run"]

    bullets = re.split(r"^- ", _section("A release is refused"), flags=re.MULTILINE)
    entry = next(
        (_squashed(b) for b in bullets if b.startswith("**`Validate the version`**")),
        None,
    )
    assert entry, "the release entry has no `Validate the version` bullet"
    assert "already exists" in entry
    assert re.search(r"Run the Release workflow with \*\*prepare\*\*", entry), entry
    assert "merge it" in entry


def test_the_release_row_points_at_the_refusal_entry() -> None:
    """A red monthly run is not "healthy", so the row links where to go."""
    _, healthy = _watch_rows()["Monthly release, 09:00 UTC on the 1st"]
    assert "(#a-release-is-refused)" in healthy


# --- The security boundary --------------------------------------------------


def _boundary_paths_in_security_doc() -> set[str]:
    text = _SECURITY.read_text(encoding="utf-8")
    bullet = re.search(
        r"^- \*\*Disable or edit the security boundary itself\.\*\*(.*?)(?=^- |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert bullet, "docs/SECURITY-AI.md lost its boundary bullet"
    body = _squashed(bullet.group(1))
    paths = set(re.findall(r"`([^`]+)`", body.split(" are outside ")[0]))
    if "this file" in body.split(" are outside ")[0]:
        paths.add("docs/SECURITY-AI.md")
    return paths


def test_the_boundary_paths_are_the_security_docs_list() -> None:
    """The paths that mean "close it" are docs/SECURITY-AI.md's, both ways."""
    signal = _section("An agent pull request touches the security boundary")
    signal = _squashed(signal.split("\n\n")[0])
    named = set(re.findall(r"`([^`]+)`", signal))
    assert named == _boundary_paths_in_security_doc()


def test_the_boundary_paths_are_in_the_codeowners_control_plane() -> None:
    """The runbook says CODEOWNERS lists these paths in its control plane."""
    text = _CODEOWNERS.read_text(encoding="utf-8")
    plane = text.split("# --- the control plane")[1].split("# --- ")[0]
    owned = {
        line.split()[0].lstrip("/")
        for line in plane.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    signal = _squashed(
        _section("An agent pull request touches the security boundary").split("\n\n")[0]
    )
    for path in re.findall(r"`([^`]+)`", signal):
        assert path in owned, f"{path} is not in CODEOWNERS' control-plane section"


def test_the_ruleset_command_and_its_offline_flag_exist() -> None:
    """The ruleset check the runbook gives, and its `--offline` flag, exist."""
    section = _section("`master` may no longer be protected")
    assert "`python3 scripts/check_ruleset.py`" in section
    assert "`--offline`" in section
    assert _CHECK_RULESET.is_file()
    assert '"--offline"' in _CHECK_RULESET.read_text(encoding="utf-8")
