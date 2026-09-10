"""The shell inside `.github/workflows/nightly.yml`'s two test legs.

`test_nightly_workflow.py` covers the `report` job - the program that decides
whether a nightly issue is opened, commented on or closed - and asserts two
static facts about the legs that feed it (their permissions, and that the
pinned leg's direct test tools are pinned). Neither leg's shell has ever been
executed by a test. Nine `run:` bodies live in the `pinned` and `latest` jobs,
and a `run:` body is a string inside YAML, so `.coveragerc`'s
`source = custom_components/sensi` cannot see it and `tests/e2e`, which drives
Home Assistant against a scripted Sensi backend, never runs a workflow step.

What those nine bodies decide:

* `pinned` is the leg whose result opens or closes the nightly issue. It
  installs `requirements_test.txt`, records which releases pip resolved,
  lints, checks the manifest/requirements pins against each other, runs the
  suite under coverage, and then asks `scripts/auto_qa_tuner.py` whether the
  gate could be raised. The coverage XML the test step writes is the file the
  tuner step's `[ -f coverage.xml ]` guard looks for, so a dropped
  `--cov-report=xml` would not fail anything - it would silently turn the
  tuner into a no-op.
* `latest` is the advance-warning leg. It deliberately installs
  `requirements_component.txt` and then upgrades the test harness, so that it
  runs against whatever Home Assistant shipped rather than against the pin. It
  is `continue-on-error`, and its `id: run` step outcome is the job output the
  report job reads.

These tests follow `test_ci_workflow.py` and `test_validate_workflow.py`:
extract each step's `run:` body from the parsed YAML and run it under `bash`
with the tools it calls stubbed on `PATH`, recording their argument vectors.
`grep` stays real, because the pattern it is handed is the thing under test,
and the two steps that call committed scripts run those scripts for real.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "nightly.yml"
_TEST_REQUIREMENTS = _ROOT / "requirements_test.txt"
_COMPONENT_REQUIREMENTS = _ROOT / "requirements_component.txt"

_PINNED_INSTALL = ("pinned", "Install dependencies")
_PINNED_RECORD = ("pinned", "Record resolved versions")
_PINNED_LINT = ("pinned", "Lint and format")
_PINNED_SYNC = ("pinned", "Requirements sync")
_PINNED_TESTS = ("pinned", "Tests with coverage")
_PINNED_TUNER = ("pinned", "Propose a gate adjustment")
_LATEST_INSTALL = ("latest", "Install the newest test harness")
_LATEST_RECORD = ("latest", "Record resolved versions")
_LATEST_TESTS = ("latest", "Tests")

# The report job's shell is covered by test_nightly_workflow.py; this file
# owns the two test legs. Listing them here is what makes
# `test_every_run_body_in_the_two_test_legs_is_executed_here` fail when a new
# shell step lands without a test.
_SHELL_STEPS = (
    _PINNED_INSTALL,
    _PINNED_RECORD,
    _PINNED_LINT,
    _PINNED_SYNC,
    _PINNED_TESTS,
    _PINNED_TUNER,
    _LATEST_INSTALL,
    _LATEST_RECORD,
    _LATEST_TESTS,
)

# A plausible `pip list --format=freeze` listing for the pinned leg. The
# entries the step is meant to record are mixed in with decoys, each rejected
# by a different piece of the pattern:
#
#   aiohttp                 not named at all
#   homeassistant-frontend  the `=` after the name in the pattern
#   pytest-asyncio          the `=` after the name in the pattern
#   types-homeassistant     the `^` anchor
#   ruff-lsp                the `=` after the name in the pattern
_FREEZE_LISTING = """aiohttp==3.13.2
homeassistant==2026.9.1
homeassistant-frontend==20260903.0
pytest==9.1.2
pytest-asyncio==1.3.0
pytest-cov==7.1.0
pytest-homeassistant-custom-component==0.13.363
python-socketio==5.16.4
ruff==0.14.4
ruff-lsp==0.0.62
types-homeassistant==2026.9.0
voluptuous==0.15.2
"""

_PINNED_EXPECTED = [
    "homeassistant==2026.9.1",
    "pytest-cov==7.1.0",
    "pytest-homeassistant-custom-component==0.13.363",
    "python-socketio==5.16.4",
    "ruff==0.14.4",
]

# The advance-warning leg records only the two releases it exists to warn
# about: the Home Assistant it resolved and the harness that pulled it in.
_LATEST_EXPECTED = [
    "homeassistant==2026.9.1",
    "pytest-homeassistant-custom-component==0.13.363",
]

# A Cobertura report is a `line-rate` attribute as far as the tuner is
# concerned; `measured_coverage` reads that and nothing else.
_COVERAGE_XML = '<?xml version="1.0" ?>\n<coverage line-rate="{rate}"></coverage>\n'


def _workflow() -> dict:
    """Return the parsed workflow."""

    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _step(job: str, name: str) -> dict:
    """Return the named step, failing if it has been renamed away."""

    document = _workflow()
    assert job in document["jobs"], (
        f"nightly.yml has no {job!r} job; these tests cover its shell and "
        "must be updated with it"
    )
    for step in document["jobs"][job]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"the {job!r} job in nightly.yml has no step named {name!r}; these "
        "tests cover its shell and must be updated with it"
    )


def _body(job: str, name: str) -> str:
    """Return a step's shell, refusing one that needs a context this file has not.

    Neither leg interpolates a `${{ }}` expression today. If one appears, the
    body cannot be run faithfully without a value for it, and a test that
    silently ran it with the literal text would prove nothing.
    """

    body = _step(job, name)["run"]
    assert "${{" not in body, (
        f"nightly.yml's {name!r} step now interpolates an expression; these "
        f"tests run its body verbatim and must supply a value:\n{body}"
    )
    return body


def _pinned_names(requirements: Path) -> set[str]:
    """Return the `==`-pinned distribution names in a requirements file."""

    names = set()
    for line in requirements.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        names.add(stripped.split("==")[0].strip())
    return names


def _alternation(job: str, name: str) -> list[str]:
    """Return the package names a record step's `grep` alternation names."""

    body = _body(job, name)
    match = re.search(r"\^\((?P<names>[^)]+)\)=", body)
    assert match is not None, (
        f"the {job!r} record step no longer greps for an anchored `^(a|b)=` "
        f"alternation; these tests cover that pattern:\n{body}"
    )
    return match.group("names").split("|")


class StepResult:
    """What running a step produced."""

    def __init__(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        calls: list,
        summary: str,
    ) -> None:
        """Store the exit status, streams, stubbed calls and job summary."""

        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = calls
        self.summary = summary

    def calls_to(self, tool: str) -> list[list[str]]:
        """Return every invocation of `tool`, argv[0] first."""

        return [call for call in self.calls if call[0] == tool]

    def only_call_to(self, tool: str) -> list[str]:
        """Return the single invocation of `tool`, argv[0] first."""

        matches = self.calls_to(tool)
        assert len(matches) == 1, (
            f"expected exactly one {tool} invocation, got {len(matches)}: {self.calls}"
        )
        return matches[0]


_RECORDING_STUB = """#!{interpreter}
import json, os, sys

arguments = [{tool!r}, *sys.argv[1:]]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(arguments) + "\\n")
sys.stdout.write(os.environ.get("STUB_STDOUT_{upper}", ""))
failing = [f for f in os.environ.get("STUB_FAIL", "").split(",") if f]
if {tool!r} in failing:
    sys.stderr.write("{tool}: stubbed failure\\n")
    sys.exit(1)
sys.exit(0)
"""

# Records the call and then becomes the real interpreter, so a step that runs
# a committed script runs that script rather than a stand-in for it.
_PASSTHROUGH_STUB = """#!{interpreter}
import json, os, sys

arguments = [{tool!r}, *sys.argv[1:]]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(arguments) + "\\n")
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
"""


def _stub_dir(tmp_path: Path, *, passthrough: tuple[str, ...] = ()) -> Path:
    """Build a PATH directory of recording stubs for the tools the steps call.

    Every stub appends its own name and arguments to `$STUB_LOG` as a JSON
    array, so an assertion can look at the argument vector rather than at
    text. `$STUB_STDOUT_<TOOL>` is what that stub prints, which is how a test
    hands a record step a `pip list` listing to filter, and `$STUB_FAIL` names
    the tools that exit non-zero.

    Tools named in `passthrough` record their arguments and then exec the real
    interpreter, which is how the two steps that call committed scripts are
    run against the committed tree.

    `grep`, `tee` and `[` are deliberately not stubbed: what they are asked to
    do is the thing under test.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("python", "python3", "pip", "pytest", "ruff"):
        template = _PASSTHROUGH_STUB if tool in passthrough else _RECORDING_STUB
        stub = bin_dir / tool
        stub.write_text(
            template.format(interpreter=sys.executable, tool=tool, upper=tool.upper()),
            encoding="utf-8",
        )
        stub.chmod(0o755)
    return bin_dir


@pytest.fixture(name="stubs")
def stubs_fixture(tmp_path: Path) -> Path:
    """Return a PATH directory in which every tool is a recording stub."""

    return _stub_dir(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(tmp_path: Path) -> Path:
    """Return a checkout-shaped directory holding the committed pin files."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for requirements in (_TEST_REQUIREMENTS, _COMPONENT_REQUIREMENTS):
        (workspace / requirements.name).write_text(
            requirements.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return workspace


def _run(
    job: str,
    name: str,
    *,
    tmp_path: Path,
    stubs: Path,
    workspace: Path,
    fail: str = "",
    stdout: dict[str, str] | None = None,
) -> StepResult:
    """Run a step's `run:` body under bash in `workspace`.

    `bash -e` is what Actions uses for a `run:` body with no explicit shell -
    note that this is not `-o pipefail`, which only an explicit `shell: bash`
    adds - so a step that would have stopped on a failing command stops here
    too, and a pipeline reports its last command's status here too.
    """

    log_file = tmp_path / "stub.log"
    log_file.write_text("", encoding="utf-8")
    summary_file = tmp_path / "step-summary.md"
    if not summary_file.exists():
        summary_file.write_text("", encoding="utf-8")

    environment = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(workspace),
        "STUB_LOG": str(log_file),
        "STUB_FAIL": fail,
        "GITHUB_STEP_SUMMARY": str(summary_file),
    }
    for tool, text in (stdout or {}).items():
        environment[f"STUB_STDOUT_{tool.upper()}"] = text

    completed = subprocess.run(  # noqa: S603
        ["bash", "-e", "-c", _body(job, name)],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    calls = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line
    ]
    return StepResult(
        completed.returncode,
        completed.stdout,
        completed.stderr,
        calls,
        summary_file.read_text(encoding="utf-8"),
    )


def test_the_pinned_leg_installs_the_pin_the_nightly_result_is_about(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """This leg's result is what opens and closes the nightly issue.

    It has to be the pinned environment - `requirements_test.txt` is the file
    that pins Home Assistant through
    `pytest-homeassistant-custom-component` - or the issue would be reporting
    on something other than the environment contributors and `ci.yml` see.
    pip itself is upgraded through the interpreter, not through whichever
    `pip` happens to be first on PATH.
    """

    result = _run(*_PINNED_INSTALL, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("python") == [
        "python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
    ]
    assert result.only_call_to("pip") == [
        "pip",
        "install",
        "-r",
        "requirements_test.txt",
    ]


def test_the_pinned_install_stops_when_the_pip_upgrade_fails(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """`bash -e` is what keeps a half-built environment from being tested.

    Joining the two commands with `||` instead of a newline would install the
    requirements with a pip that just failed and then report the result as a
    nightly pass or failure of this repository. (A `;` would not: `bash -e`
    stops at the first failing command either way, so writing them on one line
    separated by `;` is the same script.)
    """

    result = _run(
        *_PINNED_INSTALL,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        fail="python",
    )

    assert result.returncode != 0
    assert result.calls_to("pip") == []


def test_the_pinned_record_step_writes_the_resolved_versions_to_the_summary(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """Exactly the recorded packages, and none of the look-alikes.

    The listing contains names that merely start with a recorded one
    (`homeassistant-frontend`, `pytest-asyncio`, `ruff-lsp`) and one that
    merely contains a recorded one (`types-homeassistant`); the `^` anchor and
    the trailing `=` are what separate them. `tee` is what puts the result in
    both the run log and the job summary, which is where a reader of the
    nightly issue looks to see what the failing run actually resolved.
    """

    result = _run(
        *_PINNED_RECORD,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        stdout={"pip": _FREEZE_LISTING},
    )

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("pip") == ["pip", "list", "--format=freeze"]
    assert result.stdout.splitlines() == _PINNED_EXPECTED
    assert result.summary.splitlines() == _PINNED_EXPECTED


def test_the_pinned_record_step_appends_to_the_summary(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """`tee -a`, not `tee`.

    The job summary is one file for the whole job, and the tuner step writes
    to it after this one. A truncating `tee` here would be invisible while
    this step ran last and would start silently discarding whatever earlier
    steps had written the moment one was added above it.
    """

    summary_file = tmp_path / "step-summary.md"
    summary_file.write_text("### An earlier step wrote this\n", encoding="utf-8")

    result = _run(
        *_PINNED_RECORD,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        stdout={"pip": _FREEZE_LISTING},
    )

    assert result.returncode == 0, result.stderr
    assert result.summary.splitlines() == [
        "### An earlier step wrote this",
        *_PINNED_EXPECTED,
    ]


def test_the_pinned_record_step_does_not_fail_the_leg_when_nothing_matches(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """A no-match must not be the thing that opens a nightly issue.

    `grep` exits 1 when it matches nothing, but it is not the last command in
    this pipeline and the default `run:` shell is `bash -e` without
    `-o pipefail`, so the step's status is `tee`'s. That is what stands in for
    the `|| true` that `ci.yml`'s equivalent step needs; adding an explicit
    `shell: bash` to this step would turn a bookkeeping line into a gate that
    fails the pinned leg and opens an issue about a `pip list` that merely
    resolved different names.
    """

    result = _run(
        *_PINNED_RECORD,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        stdout={"pip": "aiohttp==3.13.2\nvoluptuous==0.15.2\n"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.summary == ""


def test_the_pinned_record_step_names_every_pin_that_decides_the_result() -> None:
    """A new pin has to be added to the pattern too.

    The requirements files and the `grep` alternation are a contract between
    two files that nothing else checks. Unlike `ci.yml`'s equivalent, this leg
    lints as well as tests, so `ruff` belongs here: a nightly failure caused
    by a new lint rule is unreadable without the version that produced it.
    """

    recorded = set(_alternation(*_PINNED_RECORD))
    pinned = _pinned_names(_TEST_REQUIREMENTS) | _pinned_names(_COMPONENT_REQUIREMENTS)

    assert pinned <= recorded, (
        "nightly.yml's pinned 'Record resolved versions' grep does not name "
        f"every pinned requirement; missing {sorted(pinned - recorded)}"
    )


def test_the_lint_step_checks_formatting_instead_of_rewriting_it(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """Both ruff modes run, and the formatter is a gate.

    `ruff format` without `--check` rewrites the tree and exits 0, which on a
    runner means a badly formatted commit passes the nightly silently. The
    two invocations are also separate commands, so `ruff check` failing is
    reported as a lint failure rather than being followed by a formatter run
    over code that does not lint.
    """

    result = _run(*_PINNED_LINT, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.calls_to("ruff") == [
        ["ruff", "check", "."],
        ["ruff", "format", "--check", "."],
    ]


def test_a_lint_failure_stops_the_leg_before_the_formatter_runs(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """`bash -e` again: the first failing ruff invocation ends the step."""

    result = _run(
        *_PINNED_LINT,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        fail="ruff",
    )

    assert result.returncode != 0
    assert result.calls_to("ruff") == [["ruff", "check", "."]]


def test_the_requirements_sync_step_runs_the_committed_script(
    tmp_path: Path,
) -> None:
    """The path in the workflow has to name a script that exists and passes.

    `tests/test_check_requirements_sync.py` imports the module with
    `spec_from_file_location`, so it can never see the path this step names -
    a moved or renamed script would leave every test in this repository green
    while the nightly failed. Running the step verbatim against the committed
    tree covers both the path and the state of the pins it compares.
    """

    stubs = _stub_dir(tmp_path, passthrough=("python3",))
    result = _run(*_PINNED_SYNC, tmp_path=tmp_path, stubs=stubs, workspace=_ROOT)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.only_call_to("python3") == [
        "python3",
        "scripts/check_requirements_sync.py",
    ]


def test_the_coverage_step_writes_the_report_the_next_step_reads(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """`--cov-report=xml` is what makes the tuner step do anything.

    The tuner step is guarded by `[ -f coverage.xml ]`, so dropping the XML
    report here fails nothing: the nightly stays green and simply stops
    proposing gate adjustments. `--cov=custom_components.sensi` is the same
    measurement `coverage-gate.yml` enforces, which is what makes the proposal
    about the number that gate reads.
    """

    result = _run(*_PINNED_TESTS, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("pytest") == [
        "pytest",
        "--cov=custom_components.sensi",
        "--cov-report=term-missing",
        "--cov-report=xml",
    ]


def test_the_nightly_reports_coverage_instead_of_gating_on_it() -> None:
    """`--cov-fail-under` belongs to coverage-gate.yml, not here.

    A threshold in this leg would make the nightly issue say "compliance
    failing" for a coverage dip that the gate on every pull request already
    reports, and it would do so against a threshold nobody had to keep in step
    with `MIN_COVERAGE`.
    """

    assert "--cov-fail-under" not in _body(*_PINNED_TESTS)


def test_the_tuner_step_proposes_from_the_report_the_test_step_wrote(
    tmp_path: Path,
) -> None:
    """The real tuner, the real policy file, and its output in the summary.

    `scripts/auto_qa_tuner.py` resolves its policy from `__file__`, so
    symlinking `scripts/` into the workspace runs it against the committed
    `.github/auto-qa-tuning.json` rather than a copy that can drift. The step
    appends to the job summary, which is where the proposal is read: sending
    it to stdout instead would leave it buried in the run log.
    """

    stubs = _stub_dir(tmp_path, passthrough=("python3",))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "scripts").symlink_to(_ROOT / "scripts")
    (workspace / "coverage.xml").write_text(
        _COVERAGE_XML.format(rate="0.9912"), encoding="utf-8"
    )

    result = _run(*_PINNED_TUNER, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.only_call_to("python3") == [
        "python3",
        "scripts/auto_qa_tuner.py",
        "--markdown",
    ]
    assert result.stdout == ""
    assert result.summary.startswith("### Coverage gate")
    assert "99.12" in result.summary


def test_the_tuner_step_is_skipped_when_the_suite_wrote_no_report(
    tmp_path: Path,
) -> None:
    """`if: always()` means this step runs after a failed test step too.

    A crashed or errored `pytest` leaves no `coverage.xml`, and the tuner
    exits non-zero on a missing report. The `[ -f coverage.xml ]` guard is
    what stops that from adding a second, misleading failure to a leg that has
    already failed for a reason worth reading.
    """

    stubs = _stub_dir(tmp_path, passthrough=("python3",))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "scripts").symlink_to(_ROOT / "scripts")

    result = _run(*_PINNED_TUNER, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.calls_to("python3") == []
    assert result.summary == ""


def test_the_tuner_step_runs_even_when_the_tests_failed() -> None:
    """Without `if: always()` a failing leg would also lose its proposal.

    A red nightly is exactly the run whose summary gets read, and the tuner's
    output is part of what makes that summary worth reading.
    """

    assert _step(*_PINNED_TUNER)["if"] == "always()"


def test_the_latest_leg_installs_the_newest_harness_rather_than_the_pin(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """Installing `requirements_test.txt` here would delete the whole point.

    This leg exists to run against the Home Assistant that shipped, not the
    one this repository pins, so it installs the component requirements and
    then upgrades the harness that brings Home Assistant with it. Neither
    upgrade may name a version.
    """

    result = _run(*_LATEST_INSTALL, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.calls_to("pip") == [
        ["pip", "install", "-r", "requirements_component.txt"],
        [
            "pip",
            "install",
            "--upgrade",
            "pytest-homeassistant-custom-component",
            "pytest-cov",
        ],
    ]
    assert "requirements_test.txt" not in _body(*_LATEST_INSTALL)


def test_the_latest_record_step_writes_only_to_the_job_summary(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """The braces are a group whose whole output is redirected.

    The heading and the versions have to arrive in the summary together;
    redirecting only the `pip list` pipeline would leave the heading on stdout
    and the versions under whatever heading the previous job step wrote. The
    two recorded names are the point of the leg: which Home Assistant, and
    which harness pulled it in.
    """

    result = _run(
        *_LATEST_RECORD,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        stdout={"pip": _FREEZE_LISTING},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.summary.splitlines() == [
        "### Latest Home Assistant leg",
        "",
        *_LATEST_EXPECTED,
    ]


def test_a_latest_record_step_that_matches_nothing_fails_its_step(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """Stated rather than assumed: this leg's record step is not `|| true`.

    `grep` is the last command in the redirected group, so its exit status is
    the group's and a listing with neither name fails the step. The following
    `Tests` step then never runs, `steps.run.outcome` is empty, and the report
    job renders the leg as "not run" through its `${LATEST:-not run}` default.
    `continue-on-error: true` on the job is what keeps that off the nightly
    issue's verdict.
    """

    result = _run(
        *_LATEST_RECORD,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        stdout={"pip": "aiohttp==3.13.2\n"},
    )

    assert result.returncode != 0
    assert _workflow()["jobs"]["latest"]["continue-on-error"] is True


def test_the_latest_leg_runs_the_suite_without_measuring_coverage(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """`-ra` is the payload; coverage on an unpinned environment is noise.

    The warnings summary `-ra` prints is how a Home Assistant deprecation
    surfaces here before it surfaces as a broken installation. A `--cov` flag
    would measure an environment nobody enforces a threshold on, and a
    `--cov-fail-under` would turn an advisory leg into a gate.
    """

    result = _run(*_LATEST_TESTS, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("pytest") == ["pytest", "-q", "-ra"]


def test_the_latest_result_the_report_job_reads_is_this_steps_outcome() -> None:
    """The job output names the step id that runs the suite.

    `test_nightly_workflow.py` asserts the report side of this contract - that
    `LATEST` is `needs.latest.outputs.result` rather than the job result,
    which `continue-on-error` pins to success. This is the producing side: the
    output has to name the id of the step whose failure the notice is about.
    """

    latest = _workflow()["jobs"]["latest"]
    step = _step(*_LATEST_TESTS)

    assert step["id"] == "run"
    assert latest["outputs"] == {"result": "${{ steps.run.outcome }}"}


def test_every_run_body_in_the_two_test_legs_is_executed_here() -> None:
    """No step in either leg may carry shell that this file never runs."""

    found = {
        (job_name, step["name"])
        for job_name in ("pinned", "latest")
        for step in _workflow()["jobs"][job_name]["steps"]
        if "run" in step
    }

    assert found == set(_SHELL_STEPS), (
        "nightly.yml's test legs have different shell steps than this file "
        "runs; add the new step to _SHELL_STEPS and a test that executes it"
    )
