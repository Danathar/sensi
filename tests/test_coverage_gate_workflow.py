"""The shell and Python inside `.github/workflows/coverage-gate.yml`.

That workflow is what turns a coverage number into a decision and then into a
durable record: the `coverage` job's `Summarise` step reads `coverage.xml` and
reports the verdict, and the `publish` job's `Write and push the badge data`
step builds the `coverage-data` branch the README badge is read from and
pushes it with `contents: write`.

Neither is measured by anything in this repository. `.coveragerc` measures
`custom_components/sensi`, and both bodies are strings inside YAML regardless
of what Python is asked to measure - the embedded `python3 - <<'PY'` heredoc
in `Summarise` is not a file coverage can name. `tests/e2e` drives a running
Home Assistant against the Sensi service and never executes a workflow step
either.

These tests close that the same way `test_release_workflow.py` does: extract
each step's `run:` body, substitute the `${{ }}` expressions from an explicit
context, and run the result under `bash` against a throwaway git repository
with a real bare remote to push to. `date` and `python3` are stubbed on `PATH`
so the recorded date is fixed and the interpreter is this suite's, not
whatever `python3` the machine happens to have.

The branch-building step is worth executing rather than reading because its
two halves are the ones nothing else can check: the orphan path that runs
exactly once in a repository's life, and the "nothing changed" path that
decides whether a push happens at all.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "coverage-gate.yml"

# What the stubbed `date -u +%Y-%m-%d` reports, so a trend row is the same row
# whichever day the suite runs on.
_STUB_DATE = "2026-09-08"

# A plausible commit SHA. The publish step truncates it to twelve characters
# for the commit subject.
_SHA = "0123456789abcdef0123456789abcdef01234567"

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")


def _workflow() -> dict:
    """Return the parsed workflow."""

    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _step(name: str, job: str) -> dict:
    """Return the step called `name`, failing if it has been renamed away."""

    for step in _workflow()["jobs"][job]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"the {job!r} job in coverage-gate.yml has no step named {name!r}; "
        "these tests cover its logic and must be updated with it"
    )


def _render(text: str, context: dict[str, str]) -> str:
    """Substitute `${{ ... }}` expressions in `text` from `context`.

    An expression no test supplies is an error rather than an empty string: a
    new value silently rendering as "" is the kind of untested branch this
    file exists to prevent.
    """

    def replace(match: re.Match[str]) -> str:
        expression = match.group(1).strip()
        if expression not in context:
            raise AssertionError(
                f"coverage-gate.yml uses ${{{{ {expression} }}}}, which no "
                "test supplies a value for"
            )
        return context[expression]

    return _EXPRESSION.sub(replace, text)


class StepResult:
    """What running one workflow step produced."""

    def __init__(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        outputs: dict[str, str],
        summary: str,
    ) -> None:
        """Store the exit status, streams, step outputs and job summary."""

        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.outputs = outputs
        self.summary = summary


def _run_step(
    name: str,
    job: str,
    repo: Path,
    stubs: Path,
    context: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
) -> StepResult:
    """Run one step's `run:` body in `repo` and collect what it wrote."""

    document = _workflow()
    step = _step(name, job)
    context = context or {}
    script = _render(step["run"], context)

    output_file = repo / ".github_output"
    summary_file = repo / ".github_step_summary"
    output_file.write_text("", encoding="utf-8")
    summary_file.write_text("", encoding="utf-8")

    environment = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(repo),
        "GITHUB_OUTPUT": str(output_file),
        "GITHUB_STEP_SUMMARY": str(summary_file),
        "GITHUB_SHA": _SHA,
    }
    # Layered exactly as Actions layers them: the workflow-wide `env:` is
    # where MIN_COVERAGE lives, and both steps under test read it.
    for block in (
        document.get("env") or {},
        document["jobs"][job].get("env") or {},
        step.get("env") or {},
    ):
        environment.update(
            {key: _render(str(value), context) for key, value in block.items()}
        )
    environment.update(env or {})

    completed = subprocess.run(  # noqa: S603
        ["bash", "-c", script],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    outputs: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value

    return StepResult(
        completed.returncode,
        completed.stdout,
        completed.stderr,
        outputs,
        summary_file.read_text(encoding="utf-8"),
    )


def _git(repo: Path, *arguments: str) -> str:
    """Run git in `repo` and return its stdout."""

    return subprocess.run(  # noqa: S603
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _write_coverage_xml(repo: Path, line_rate: str | None) -> None:
    """Write a coverage.xml carrying `line_rate`, or none at all."""

    attribute = "" if line_rate is None else f' line-rate="{line_rate}"'
    (repo / "coverage.xml").write_text(
        f'<?xml version="1.0" ?>\n<coverage{attribute} version="7.0">\n'
        "  <packages/>\n</coverage>\n",
        encoding="utf-8",
    )


@pytest.fixture(name="stubs")
def stubs_fixture(tmp_path: Path) -> Path:
    """Build a PATH directory with a fixed `date` and this suite's interpreter.

    `python3` is stubbed so the heredoc in `Summarise` and
    `scripts/coverage_badge.py` run under the interpreter the tests are
    running under rather than whatever `python3` resolves to on the machine.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Resolved now, while the stub is not yet on PATH, so the fallback cannot
    # re-enter the stub.
    real_date = shutil.which("date") or "/bin/date"
    date_stub = bin_dir / "date"
    date_stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  "-u +%Y-%m-%d") echo {_STUB_DATE} ;;\n'
        f'  *) exec {real_date} "$@" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    date_stub.chmod(0o755)

    python_stub = bin_dir / "python3"
    python_stub.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8"
    )
    python_stub.chmod(0o755)
    return bin_dir


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    """Build a checkout shaped like this one, with a bare remote to publish to.

    `scripts/coverage_badge.py` is the real file: the step invokes it by path
    from the checkout, and a stub in its place would leave the badge and trend
    contents unasserted.
    """

    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))

    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", str(origin))

    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copy(_ROOT / "scripts" / "coverage_badge.py", scripts / "coverage_badge.py")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    _git(repo, "push", "-q", "origin", "master")
    return repo


def _origin(repo: Path) -> Path:
    """Return the bare remote backing `repo`."""

    return repo.parent / "origin.git"


def _published(repo: Path, path: str) -> str:
    """Return a file's contents on the published `coverage-data` branch."""

    return _git(_origin(repo), "show", f"coverage-data:{path}")


def _publish(repo: Path, stubs: Path, percent: str, **env: str) -> StepResult:
    """Run the publish step with `percent` as the measured coverage."""

    return _run_step(
        "Write and push the badge data",
        "publish",
        repo,
        stubs,
        context={"needs.coverage.outputs.percent": percent},
        env=env,
    )


def _summarise(repo: Path, stubs: Path, **env: str) -> StepResult:
    """Run the summarise step against whatever coverage.xml `repo` holds."""

    return _run_step("Summarise", "coverage", repo, stubs, env=env)


# --------------------------------------------------------------------------
# `Summarise`: coverage.xml to a verdict, a job summary and a step output.
# --------------------------------------------------------------------------


def test_summarise_reports_a_pass_above_the_threshold(repo: Path, stubs: Path) -> None:
    """The percentage, the threshold and the verdict all reach the summary."""

    _write_coverage_xml(repo, "0.9812")

    result = _summarise(repo, stubs)

    assert result.returncode == 0, result.stderr
    assert "### Coverage PASS" in result.summary
    assert "- Line coverage: **98.12%**" in result.summary
    assert "- Threshold: **93%**" in result.summary


def test_summarise_hands_the_percentage_to_the_publish_job(
    repo: Path, stubs: Path
) -> None:
    """The badge number is this output, not a second parse of coverage.xml.

    Two decimal places, so the value the publish job commits is the value the
    summary displayed.
    """

    _write_coverage_xml(repo, "0.9812")

    result = _summarise(repo, stubs)

    assert result.outputs == {"percent": "98.12"}


def test_summarise_reports_a_failure_below_the_threshold(
    repo: Path, stubs: Path
) -> None:
    """A number under MIN_COVERAGE is called FAIL, and still published."""

    _write_coverage_xml(repo, "0.9")

    result = _summarise(repo, stubs)

    assert "### Coverage FAIL" in result.summary
    assert "- Line coverage: **90.00%**" in result.summary
    assert result.outputs == {"percent": "90.00"}


def test_summarise_calls_exactly_the_threshold_a_pass(repo: Path, stubs: Path) -> None:
    """The gate is `>=`: 93.00% against a threshold of 93 passes.

    The same boundary `--cov-fail-under` applies, so the summary must not
    contradict the step that actually failed or did not fail the job.
    """

    _write_coverage_xml(repo, "0.93")

    result = _summarise(repo, stubs)

    assert "### Coverage PASS" in result.summary
    assert result.outputs == {"percent": "93.00"}


def test_summarise_reads_the_threshold_from_the_workflow_env(
    repo: Path, stubs: Path
) -> None:
    """MIN_COVERAGE is the threshold, not a number repeated in the heredoc."""

    _write_coverage_xml(repo, "0.9812")

    result = _summarise(repo, stubs, MIN_COVERAGE="99")

    assert "### Coverage FAIL" in result.summary
    assert "- Threshold: **99%**" in result.summary


def test_summarise_says_so_when_no_coverage_xml_was_produced(
    repo: Path, stubs: Path
) -> None:
    """The step runs `if: always()`, so a crashed suite reaches it too.

    It has to report the absence rather than fail parsing a file that is not
    there, and it must not hand the publish job a percentage.
    """

    result = _summarise(repo, stubs)

    assert result.returncode == 0, result.stderr
    assert result.summary == ("No coverage.xml was produced - the suite did not run.\n")
    assert result.outputs == {}


def test_summarise_treats_a_missing_line_rate_as_zero(repo: Path, stubs: Path) -> None:
    """A coverage.xml without the attribute is a failure, not a traceback."""

    _write_coverage_xml(repo, None)

    result = _summarise(repo, stubs)

    assert result.returncode == 0, result.stderr
    assert "### Coverage FAIL" in result.summary
    assert result.outputs == {"percent": "0.00"}


# --------------------------------------------------------------------------
# `Write and push the badge data`: the coverage-data branch.
# --------------------------------------------------------------------------


def test_publish_refuses_to_run_without_a_percentage(repo: Path, stubs: Path) -> None:
    """An empty output means the coverage job produced no number.

    Publishing anything then would overwrite a good badge with a blank one,
    so the step fails instead - and leaves the branch untouched.
    """

    result = _publish(repo, stubs, "")

    assert result.returncode == 1
    assert "produced no percentage" in result.stderr
    assert "coverage-data" not in _git(_origin(repo), "branch", "--list")


def test_publish_creates_the_branch_on_the_first_ever_run(
    repo: Path, stubs: Path
) -> None:
    """With no `coverage-data` to fetch, the step starts an orphan branch."""

    result = _publish(repo, stubs, "97.80")

    assert result.returncode == 0, result.stderr
    badge = json.loads(_published(repo, "coverage-unit.json"))
    assert badge == {
        "schemaVersion": 1,
        "label": "unit coverage",
        "message": "97.8%",
        "color": "brightgreen",
    }


def test_publish_starts_the_branch_with_no_history_behind_it(
    repo: Path, stubs: Path
) -> None:
    """Orphan, not a branch off master: the data must not carry the code."""

    _publish(repo, stubs, "97.80")

    assert _git(_origin(repo), "rev-list", "--count", "coverage-data").strip() == "1"


def test_publish_leaves_only_the_two_data_files_on_the_branch(
    repo: Path, stubs: Path
) -> None:
    """A branch documented as holding two files and no code holds two files.

    `git switch --orphan` empties the worktree and the commit stages two
    named paths, so the checkout the fixture committed - README.md, scripts/ -
    does not follow the data onto the branch the badge is served from.
    """

    _publish(repo, stubs, "97.80")

    tracked = _git(
        _origin(repo), "ls-tree", "-r", "--name-only", "coverage-data"
    ).split()
    assert sorted(tracked) == ["coverage-trend.csv", "coverage-unit.json"]


def test_publish_records_the_date_and_the_commit_in_the_trend(
    repo: Path, stubs: Path
) -> None:
    """The CSV is the history the expiring per-run artifact cannot be."""

    _publish(repo, stubs, "97.80")

    rows = _published(repo, "coverage-trend.csv").splitlines()
    assert rows[-1] == f"{_STUB_DATE},{_SHA},97.80"


def test_publish_names_the_number_and_the_commit_in_its_subject(
    repo: Path, stubs: Path
) -> None:
    """`${GITHUB_SHA::12}` - the short commit, not the whole forty."""

    _publish(repo, stubs, "97.80")

    subject = _git(_origin(repo), "log", "-1", "--format=%s", "coverage-data").strip()
    assert subject == f"chore: record unit coverage 97.80% for {_SHA[:12]}"


def test_publish_appends_to_the_branch_it_already_has(repo: Path, stubs: Path) -> None:
    """The second run fetches the branch instead of orphaning a new one.

    A new orphan each time would leave the trend one row long forever, which
    is the whole difference between a history and a snapshot.
    """

    later = "fedcba9876543210fedcba9876543210fedcba98"

    _publish(repo, stubs, "97.80")
    result = _publish(repo, stubs, "98.10", GITHUB_SHA=later)

    assert result.returncode == 0, result.stderr
    rows = _published(repo, "coverage-trend.csv").splitlines()
    assert rows[-2:] == [
        f"{_STUB_DATE},{_SHA},97.80",
        f"{_STUB_DATE},{later},98.10",
    ]
    assert _git(_origin(repo), "rev-list", "--count", "coverage-data").strip() == "2"


def test_publish_replaces_the_row_when_the_same_commit_is_run_again(
    repo: Path, stubs: Path
) -> None:
    """Re-running a push records the commit once, at its newest number.

    A second row for one commit would also make the trend file grow on every
    re-run, so "nothing changed, do not commit" could never be true again.
    """

    _publish(repo, stubs, "97.80")
    result = _publish(repo, stubs, "98.10")

    assert result.returncode == 0, result.stderr
    assert _published(repo, "coverage-trend.csv").splitlines() == [
        f"{_STUB_DATE},{_SHA},98.10"
    ]
    assert _git(_origin(repo), "rev-list", "--count", "coverage-data").strip() == "2"


def test_publish_makes_no_commit_when_nothing_changed(repo: Path, stubs: Path) -> None:
    """Same number, same day, same commit: there is nothing to record.

    Pushing anyway would put an empty commit on the branch for every push to
    master that did not move coverage.
    """

    _publish(repo, stubs, "97.80")
    result = _publish(repo, stubs, "97.80")

    assert result.returncode == 0, result.stderr
    assert "nothing to commit" in result.stdout
    assert _git(_origin(repo), "rev-list", "--count", "coverage-data").strip() == "1"
    assert result.summary == ""


def test_publish_colours_the_badge_by_the_gate_not_by_a_default(
    repo: Path, stubs: Path
) -> None:
    """`--high "${MIN_COVERAGE}"` is what keeps the badge and the gate agreed.

    95% is green against the committed threshold and yellow against a raised
    one; the badge must turn yellow at exactly the point the gate would start
    failing rather than at the default baked into coverage_badge.py.
    """

    result = _publish(repo, stubs, "95.00", MIN_COVERAGE="99")

    assert result.returncode == 0, result.stderr
    assert json.loads(_published(repo, "coverage-unit.json"))["color"] == "yellow"


def test_publish_reports_what_it_published_to_the_job_summary(
    repo: Path, stubs: Path
) -> None:
    """The run that moved the badge says which number it moved it to."""

    result = _publish(repo, stubs, "97.80")

    assert "Published **97.80%** to the coverage-data branch." in result.summary


# --------------------------------------------------------------------------
# Properties of the file itself, which running a step cannot show.
# --------------------------------------------------------------------------


def test_no_run_body_interpolates_a_workflow_expression() -> None:
    """`${{ }}` inside a `run:` becomes shell source, not a shell value.

    The publish job holds `contents: write` and pushes a branch, so every
    value must arrive through `env:` - a variable assignment bash never
    re-parses - exactly as release.yml is held to.
    """

    document = _workflow()
    offenders = [
        (job, step.get("name"))
        for job, body in document["jobs"].items()
        for step in body["steps"]
        if "run" in step and _EXPRESSION.search(step["run"])
    ]

    assert offenders == []


def test_only_a_push_to_master_can_publish() -> None:
    """A pull request must never be able to move the published number."""

    condition = _workflow()["jobs"]["publish"]["if"]

    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/master'" in condition


def test_a_failed_gate_cannot_overwrite_a_good_number() -> None:
    """`needs: coverage` is what makes the badge mean the gate passed."""

    assert _workflow()["jobs"]["publish"]["needs"] == "coverage"


def test_concurrent_pushes_are_serialised_rather_than_cancelled() -> None:
    """Two pushes must append two trend rows, not race and drop one."""

    concurrency = _workflow()["jobs"]["publish"]["concurrency"]

    assert concurrency["group"] == "coverage-data"
    assert concurrency["cancel-in-progress"] is False


def test_only_the_publishing_job_may_write_to_the_repository() -> None:
    """The job that runs the suite has no reason to hold `contents: write`."""

    jobs = _workflow()["jobs"]

    assert jobs["publish"]["permissions"] == {"contents": "write"}
    assert "permissions" not in jobs["coverage"]
    assert _workflow()["permissions"] == {"contents": "read"}


def test_the_gate_fails_the_suite_at_the_declared_threshold() -> None:
    """`--cov-fail-under` reads MIN_COVERAGE rather than repeating it."""

    body = _step("Run the suite under coverage", "coverage")["run"]

    assert '--cov-fail-under="${MIN_COVERAGE}"' in body


def test_the_percentage_the_publish_job_reads_is_the_one_summarise_wrote() -> None:
    """The wiring between the two jobs, which no single step can show."""

    document = _workflow()

    assert (
        document["jobs"]["coverage"]["outputs"]["percent"]
        == "${{ steps.summarise.outputs.percent }}"
    )
    assert (
        _step("Write and push the badge data", "publish")["env"]["COVERAGE_PERCENT"]
        == "${{ needs.coverage.outputs.percent }}"
    )
