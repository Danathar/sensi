"""`.github/workflows/auto-issues.yml`: a failed scheduled Release opens an issue.

The monthly `Release` run refuses to tag a version that is already released,
so it fails every month until somebody bumps the manifest by hand (#669). A red
scheduled run tells nobody but whoever last edited its cron line, and no pull
request carries its check. `auto-issues.yml` is what turns that failure into an
issue someone sees; the maintainer decided on #669 that the run keeps failing
and this reports it, rather than the run being made to pass.

Two things are asserted, and both fail quietly when they drift:

* The trigger. `workflow_run` matches the watched workflow by its `name:`, not
  its file name, so renaming `Release` silently stops the reporting. The
  watched list is checked against the name in `release.yml` and against every
  workflow that declares a schedule, so a new scheduled workflow is either
  watched or named here as reporting for itself.
* The shell. Its `run:` body is extracted from the workflow and executed under
  `bash` with `gh` stubbed on `PATH`, the way `test_nightly_workflow.py` runs
  the nightly `report` step. Only a failed run on the default branch of this
  repository that a schedule started may open or update an issue; the tracking
  issue is found by exact title and by the Actions bot as author, since anyone
  can open an issue with that title; a listing that fails must fail the step,
  or it would read as "no issue open" and file a duplicate.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

from tests._workflows import workflow_paths

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_WORKFLOW = _WORKFLOWS / "auto-issues.yml"

_JOB = "track"
_STEP = "Report the failed scheduled run"

_REPO = "Danathar/sensi"
_RUN_URL = f"https://github.com/{_REPO}/actions/runs/1234567890"
_SHA = "a" * 40

# Written out here rather than read from the workflow, so a change to the title
# has to be made in both places deliberately.
_TITLE = "Scheduled run failing: Release"

# Scheduled workflows `auto-issues.yml` deliberately does not watch, each with
# the reason.
_SELF_REPORTING = {
    "Nightly compliance": "its own `report` job keeps one issue and closes it",
}

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(document: dict) -> dict:
    """Return the `on:` block; PyYAML reads a bare `on` key as `True`."""

    return document.get("on", document.get(True)) or {}


def _step() -> dict:
    for step in _load(_WORKFLOW)["jobs"][_JOB]["steps"]:
        if step.get("name") == _STEP:
            return step
    raise AssertionError(
        f"the {_JOB!r} job in auto-issues.yml has no step named {_STEP!r}; "
        "these tests cover its logic and must be updated with it"
    )


def test_the_workflow_exists_and_is_named() -> None:
    """The workflow file is committed and named `Auto issues`."""
    assert _WORKFLOW.is_file(), "no auto-issues workflow, so a failed release is silent"
    assert _load(_WORKFLOW)["name"] == "Auto issues"


def test_it_runs_when_the_release_workflow_completes() -> None:
    """It triggers only on a completed run of the workflow `release.yml` names."""
    release_name = _load(_WORKFLOWS / "release.yml")["name"]

    trigger = _triggers(_load(_WORKFLOW))

    assert set(trigger) == {"workflow_run"}, "it must run on nothing else"
    assert trigger["workflow_run"]["workflows"] == [release_name]
    assert trigger["workflow_run"]["types"] == ["completed"]
    assert "branches" not in trigger["workflow_run"], (
        "the shell compares the branch to the default branch; a filter here "
        "would have to be renamed with it"
    )


def test_it_watches_every_scheduled_workflow_not_reporting_for_itself() -> None:
    """Every scheduled workflow is watched here or reports its own failures."""
    scheduled = {}
    for path in workflow_paths():
        document = _load(path)
        if "schedule" in _triggers(document):
            scheduled[document["name"]] = path.name
    assert "Release" in scheduled, "release.yml no longer has a schedule"

    watched = set(_triggers(_load(_WORKFLOW))["workflow_run"]["workflows"])

    assert watched == set(scheduled) - set(_SELF_REPORTING)
    assert set(_SELF_REPORTING) <= set(scheduled), (
        "a workflow listed as reporting for itself no longer runs on a schedule"
    )


def test_only_the_job_that_files_issues_can_write_them() -> None:
    """The token can write issues and nothing else, and only in the one job."""
    document = _load(_WORKFLOW)

    assert document["permissions"] == {}
    assert document["jobs"][_JOB]["permissions"] == {"issues": "write"}


def test_it_runs_no_code_from_the_watched_run() -> None:
    """No checkout, no action, and no `${{ }}` inside a script."""

    document = _load(_WORKFLOW)

    assert list(document["jobs"]) == [_JOB]
    for step in document["jobs"][_JOB]["steps"]:
        assert "uses" not in step, step
        assert not _EXPRESSION.search(step.get("run", "")), (
            f"step {step.get('name')!r} puts an expression in `run:`; the "
            "workflow run's fields must reach the shell through `env:`"
        )


@pytest.fixture(name="stubs")
def stubs_fixture(tmp_path: Path) -> Path:
    """Build a PATH directory holding a recording `gh`.

    Like the real one it fails without `--repo` (the job checks nothing out, so
    it has no git remote to read), answers `issue list` with `$GH_ISSUES`
    unless `$GH_LIST_FAIL` is set, and records each call's arguments and stdin.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "arguments = sys.argv[1:]\n"
        'body = "" if sys.stdin.isatty() or arguments[:2] == ["issue", "list"]'
        " else sys.stdin.read()\n"
        'with open(os.environ["GH_LOG"], "a", encoding="utf-8") as log:\n'
        '    log.write(json.dumps({"args": arguments, "stdin": body}) + "\\n")\n'
        'if "--repo" not in arguments:\n'
        '    sys.stderr.write("gh: no --repo; not a git repository\\n")\n'
        "    sys.exit(1)\n"
        'if arguments[:2] == ["issue", "list"]:\n'
        '    if os.environ.get("GH_LIST_FAIL"):\n'
        '        sys.stderr.write("gh: listing failed\\n")\n'
        "        sys.exit(1)\n"
        '    print(os.environ.get("GH_ISSUES", "[]"))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir


def _issue(
    number: int, title: str = _TITLE, author: str = "app/github-actions"
) -> dict:
    return {"number": number, "title": title, "author": {"login": author}}


class Result:
    """One run of the step: its exit status, stderr and recorded `gh` calls."""

    def __init__(
        self, completed: subprocess.CompletedProcess, calls: list[dict]
    ) -> None:
        """Keep what the assertions read from the finished process."""
        self.returncode = completed.returncode
        self.stderr = completed.stderr
        self.calls = calls

    def starting(self, *prefix: str) -> list[dict]:
        """Return the recorded `gh` calls whose arguments begin with `prefix`."""
        return [c for c in self.calls if c["args"][: len(prefix)] == list(prefix)]


def _run(
    tmp_path: Path,
    stubs: Path,
    *,
    issues: list[dict] | None = None,
    conclusion: str = "failure",
    event: str = "schedule",
    branch: str = "master",
    head_repo: str = _REPO,
    workflow: str = "Release",
    list_fails: bool = False,
) -> Result:
    """Run the step with the given workflow-run context and issue tracker."""

    step = _step()
    log = tmp_path / "gh.log"
    log.write_text("", encoding="utf-8")

    context = {
        "github.token": "gh-token",
        "github.repository": _REPO,
        "github.event.repository.default_branch": "master",
        "github.event.workflow_run.name": workflow,
        "github.event.workflow_run.event": event,
        "github.event.workflow_run.head_branch": branch,
        "github.event.workflow_run.head_repository.full_name": head_repo,
        "github.event.workflow_run.head_sha": _SHA,
        "github.event.workflow_run.conclusion": conclusion,
        "github.event.workflow_run.html_url": _RUN_URL,
    }

    def render(value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            expression = match.group(1).strip()
            assert expression in context, f"no test supplies {expression}"
            return context[expression]

        return _EXPRESSION.sub(replace, str(value))

    environment = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "GH_LOG": str(log),
        "GH_ISSUES": json.dumps(issues or []),
        "GH_LIST_FAIL": "1" if list_fails else "",
    }
    environment.update({k: render(v) for k, v in (step.get("env") or {}).items()})

    completed = subprocess.run(  # noqa: S603
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line
    ]
    return Result(completed, calls)


def test_a_failed_scheduled_release_opens_an_issue(tmp_path: Path, stubs: Path) -> None:
    """The first failure opens an issue linking the run and the commit."""
    result = _run(tmp_path, stubs)

    assert result.returncode == 0, result.stderr
    (create,) = result.starting("issue", "create")
    assert create["args"][create["args"].index("--title") + 1] == _TITLE
    assert create["args"][create["args"].index("--repo") + 1] == _REPO
    assert _RUN_URL in create["stdin"]
    assert _SHA in create["stdin"]
    assert not result.starting("issue", "comment")
    assert not result.starting("issue", "close")


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure"])
def test_every_kind_of_failure_is_reported(
    tmp_path: Path, stubs: Path, conclusion: str
) -> None:
    """A timeout or a startup failure is reported like a failure."""
    result = _run(tmp_path, stubs, conclusion=conclusion)

    assert result.returncode == 0, result.stderr
    assert len(result.starting("issue", "create")) == 1


def test_a_further_failure_comments_on_the_open_issue(
    tmp_path: Path, stubs: Path
) -> None:
    """A second failure comments on the oldest open tracking issue."""
    result = _run(tmp_path, stubs, issues=[_issue(9), _issue(7)])

    assert result.returncode == 0, result.stderr
    assert not result.starting("issue", "create")
    (comment,) = result.starting("issue", "comment")
    assert comment["args"][2] == "7", "comment on the oldest tracking issue"
    assert _RUN_URL in comment["stdin"]


def test_an_issue_with_the_title_from_someone_else_is_not_the_tracking_issue(
    tmp_path: Path, stubs: Path
) -> None:
    """Only an issue the Actions bot opened counts as the tracking issue."""
    squatted = [_issue(3, author="someone"), _issue(4, title="Something else")]

    result = _run(tmp_path, stubs, issues=squatted)

    assert result.returncode == 0, result.stderr
    assert len(result.starting("issue", "create")) == 1
    assert not result.starting("issue", "comment")


def test_a_failed_listing_fails_the_step_instead_of_filing_a_duplicate(
    tmp_path: Path, stubs: Path
) -> None:
    """A failed `gh issue list` fails the step rather than reading as none open."""
    result = _run(tmp_path, stubs, list_fails=True)

    assert result.returncode != 0
    assert not result.starting("issue", "create")
    assert not result.starting("issue", "comment")


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": "workflow_dispatch"},
        {"event": "push"},
        {"branch": "release/2026.12.0"},
        {"head_repo": "someone/sensi"},
        {"conclusion": "success"},
        {"conclusion": "cancelled"},
        {"conclusion": "skipped"},
    ],
    ids=lambda overrides: ",".join(f"{k}={v}" for k, v in overrides.items()),
)
def test_a_run_that_is_not_an_unattended_failure_touches_nothing(
    tmp_path: Path, stubs: Path, overrides: dict
) -> None:
    """A pass, a cancel, a dispatch or another branch makes no `gh` call."""
    result = _run(tmp_path, stubs, issues=[_issue(7)], **overrides)

    assert result.returncode == 0, result.stderr
    assert result.calls == []
