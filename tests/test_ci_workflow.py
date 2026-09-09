"""The shell inside `.github/workflows/ci.yml`.

`ci.yml` is the fast "do the tests pass" signal for every push and pull
request, and it is the last workflow in this repository whose `run:` bodies no
test executes. `.coveragerc` measures `custom_components/sensi`, so a `run:`
body - a string inside YAML - is outside every coverage source by
construction; `tests/e2e` drives Home Assistant against a scripted Sensi
backend and never runs a workflow step. The only mention of `ci.yml` anywhere
under `tests/` is `test_release_workflow.py`, which commits a file by that
name into a scratch git repository as a fixture for "only CI changed" - it
does not run a line of this workflow.

Three steps carry shell:

* `Install dependencies` upgrades pip through the interpreter and then
  installs `requirements_test.txt`, which is what pins Home Assistant and
  therefore what the whole suite runs against.
* `Record resolved versions` is the only place the job writes down which
  releases pip actually resolved. It is a `pip list` piped into an anchored
  case-insensitive `grep -E` alternation, ending in `|| true`. Every piece of
  that pattern decides whether a version reaches the log: the `^` anchor, the
  trailing `=`, the package names in the alternation, and the `|| true` that
  keeps a no-match from failing the job.
* `Run tests` is a bare `pytest`. Its argument list is a contract with
  `coverage-gate.yml`, which owns coverage measurement and enforcement; this
  job is deliberately the one that only reports pass or fail.

These tests close that the way `test_validate_workflow.py`,
`test_nightly_workflow.py`, `test_coverage_gate_workflow.py` and
`test_labeler_workflow.py` do: extract the step's `run:` body from the parsed
YAML and run it under `bash` with the tools it calls stubbed on `PATH`. The
stubs record their argument vectors and can be told what to print and which
should fail, so the assertions are about what the step would have asked pip
and pytest to do - and about which lines the real `grep` lets through - rather
than about the text of the script.
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
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"
_TEST_REQUIREMENTS = _ROOT / "requirements_test.txt"
_COMPONENT_REQUIREMENTS = _ROOT / "requirements_component.txt"

_INSTALL_STEP = ("pytest", "Install dependencies")
_RECORD_STEP = ("pytest", "Record resolved versions")
_RUN_STEP = ("pytest", "Run tests")

# Every step in ci.yml that carries shell. Kept here so that adding a `run:`
# body to the workflow without a test for it fails
# `test_every_run_body_in_the_workflow_is_executed_here` rather than passing
# unnoticed.
_SHELL_STEPS = (_INSTALL_STEP, _RECORD_STEP, _RUN_STEP)

# Pins that only decide how the code is linted, not what it is executed
# against. `validate.yml` installs the ruff pin and
# `test_validate_workflow.py` asserts it, so ci.yml has no reason to record
# it.
_LINT_ONLY_PINS = frozenset({"ruff"})

# A plausible `pip list --format=freeze` listing. The five entries the step is
# meant to record are mixed in with four decoys, each of which is rejected by
# a different piece of the pattern:
#
#   aiohttp                 not named at all
#   homeassistant-frontend  the `=` after the name in the pattern
#   pytest-asyncio          the `=` after the name in the pattern
#   types-homeassistant     the `^` anchor
_FREEZE_LISTING = """aiohttp==3.13.2
homeassistant==2026.9.1
homeassistant-frontend==20260903.0
pytest==9.1.2
pytest-asyncio==1.3.0
pytest-cov==7.1.0
pytest-homeassistant-custom-component==0.13.363
python-socketio==5.16.4
types-homeassistant==2026.9.0
voluptuous==0.15.2
"""

_EXPECTED_RECORDED = [
    "homeassistant==2026.9.1",
    "pytest==9.1.2",
    "pytest-cov==7.1.0",
    "pytest-homeassistant-custom-component==0.13.363",
    "python-socketio==5.16.4",
]


def _workflow() -> dict:
    """Return the parsed workflow."""

    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _step(job: str, name: str) -> dict:
    """Return the named step, failing if it has been renamed away."""

    document = _workflow()
    assert job in document["jobs"], (
        f"ci.yml has no {job!r} job; these tests cover its shell and must be "
        "updated with it"
    )
    for step in document["jobs"][job]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"the {job!r} job in ci.yml has no step named {name!r}; these tests "
        "cover its shell and must be updated with it"
    )


def _pinned_names(requirements: Path) -> set[str]:
    """Return the `==`-pinned distribution names in a requirements file."""

    names = set()
    for line in requirements.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        names.add(stripped.split("==")[0].strip())
    return names


def _recorded_names() -> list[str]:
    """Return the package names the record step's `grep` alternation names."""

    body = _step(*_RECORD_STEP)["run"]
    match = re.search(r"\^\((?P<alternation>[^)]+)\)=", body)
    assert match is not None, (
        "the record step no longer greps for an anchored `^(a|b)=` "
        f"alternation; these tests cover that pattern:\n{body}"
    )
    return match.group("alternation").split("|")


class StepResult:
    """What running a step produced."""

    def __init__(self, returncode: int, stdout: str, stderr: str, calls: list) -> None:
        """Store the exit status, streams and stubbed-tool invocations."""

        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = calls

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


@pytest.fixture(name="stubs")
def stubs_fixture(tmp_path: Path) -> Path:
    """Build a PATH directory of recording stubs for the tools the steps call.

    Every stub appends its own name and arguments to `$STUB_LOG` as a JSON
    array, so an assertion can look at the argument vector rather than at
    text. `$STUB_STDOUT_<TOOL>` is what that stub prints, which is how a test
    hands the record step a `pip list` listing to filter. `$STUB_FAIL` names
    the tools that should exit non-zero, which is how a test says "the test
    run failed".

    `grep` is deliberately not stubbed: the pattern it is given is the thing
    under test, so it has to be the real program.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    for tool in ("python", "pip", "pytest"):
        stub = bin_dir / tool
        stub.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"arguments = [{tool!r}, *sys.argv[1:]]\n"
            'with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:\n'
            '    log.write(json.dumps(arguments) + "\\n")\n'
            f'sys.stdout.write(os.environ.get("STUB_STDOUT_{tool.upper()}", ""))\n'
            'failing = [f for f in os.environ.get("STUB_FAIL", "").split(",") if f]\n'
            f"if {tool!r} in failing:\n"
            f'    sys.stderr.write("{tool}: stubbed failure\\n")\n'
            "    sys.exit(1)\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
    return bin_dir


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

    `bash -e` is what Actions uses for a `run:` body with no explicit shell,
    so a step that would have stopped on a failing command stops here too.
    """

    log_file = tmp_path / "stub.log"
    log_file.write_text("", encoding="utf-8")

    environment = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(workspace),
        "STUB_LOG": str(log_file),
        "STUB_FAIL": fail,
    }
    for tool, text in (stdout or {}).items():
        environment[f"STUB_STDOUT_{tool.upper()}"] = text

    completed = subprocess.run(  # noqa: S603
        ["bash", "-e", "-c", _step(job, name)["run"]],
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
    return StepResult(completed.returncode, completed.stdout, completed.stderr, calls)


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


def test_the_install_step_installs_the_pinned_test_requirements(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """The suite runs against whatever this one file resolves to.

    `requirements_test.txt` pins pytest-homeassistant-custom-component, which
    ships the Home Assistant release everything else is tested against, and it
    pulls `requirements_component.txt` in with `-r`. Installing anything else
    here - or installing it without `-r` - silently changes what the green
    check means.
    """

    result = _run(*_INSTALL_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("pip") == [
        "pip",
        "install",
        "-r",
        "requirements_test.txt",
    ]
    # pip itself is upgraded through the interpreter, not through whichever
    # `pip` happens to be first on PATH.
    assert result.only_call_to("python") == [
        "python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
    ]


def test_the_install_step_names_a_requirements_file_that_exists() -> None:
    """The path in the workflow has to still name a committed file.

    Nothing else checks this seam: the file could be renamed and every test in
    this repository would keep passing while CI failed on the next push.
    """

    body = _step(*_INSTALL_STEP)["run"]
    named = re.findall(r"-r\s+(\S+)", body)

    assert named, f"the install step no longer installs from a file:\n{body}"
    for path in named:
        assert (_ROOT / path).is_file(), f"ci.yml installs from a missing {path}"


def test_the_install_step_stops_when_the_pip_upgrade_fails(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """`set -e` is what keeps a half-installed environment from running tests.

    If the interpreter's pip upgrade fails the step has to abort, not carry on
    and install the requirements with a pip that just failed. Joining the two
    commands with `;` or `||` instead of a newline would lose that.
    """

    result = _run(
        *_INSTALL_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        fail="python",
    )

    assert result.returncode != 0
    assert result.calls_to("pip") == []


def test_the_record_step_asks_pip_for_a_freeze_listing(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """The pattern is written against `name==version`, not `name (version)`.

    `pip list` defaults to a column layout whose lines start with the name
    followed by spaces, which the anchored `=` would never match. The whole
    step is a no-op without `--format=freeze`.
    """

    result = _run(
        *_RECORD_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        stdout={"pip": _FREEZE_LISTING},
    )

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("pip") == ["pip", "list", "--format=freeze"]


def test_the_record_step_records_the_versions_that_decide_the_result(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """Exactly the five packages, and none of the four look-alikes.

    This is the assertion the record step exists for. The listing it is given
    contains a package whose name merely starts with a recorded one
    (`homeassistant-frontend`, `pytest-asyncio`), one that merely contains a
    recorded name (`types-homeassistant`), and one that is unrelated. The `^`
    anchor and the trailing `=` are what separate them; without either, the
    log fills with versions the job was not asked to record and a reader
    cannot tell which Home Assistant the suite ran against.
    """

    result = _run(
        *_RECORD_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        stdout={"pip": _FREEZE_LISTING},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == _EXPECTED_RECORDED


def test_the_record_step_does_not_fail_the_job_when_nothing_matches(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """`|| true` is load-bearing.

    `grep` exits 1 when it matches nothing, and under `set -e` that would fail
    the whole job on a listing that simply resolved different names - turning
    a bookkeeping step into a gate it was never meant to be.
    """

    result = _run(
        *_RECORD_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        stdout={"pip": "aiohttp==3.13.2\nvoluptuous==0.15.2\n"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_the_record_step_records_every_pin_that_decides_what_ran(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """A new runtime pin has to be added to the pattern too.

    The requirements files and the `grep` alternation are a contract between
    two files that nothing else checks. Adding a pin to
    `requirements_component.txt` or `requirements_test.txt` without extending
    the alternation leaves the job reporting on a release nobody can see in
    the log. Lint-only pins are excluded: `validate.yml` installs those and
    `test_validate_workflow.py` asserts them.
    """

    pinned = (
        _pinned_names(_TEST_REQUIREMENTS) | _pinned_names(_COMPONENT_REQUIREMENTS)
    ) - _LINT_ONLY_PINS

    assert pinned <= set(_recorded_names()), (
        "ci.yml's 'Record resolved versions' grep does not name every pinned "
        f"runtime requirement; missing {sorted(pinned - set(_recorded_names()))}"
    )


def test_the_record_step_hides_a_broken_pip(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """State the failure mode rather than leaving a reader to assume a gate.

    The pipeline's status is `grep`'s, and `|| true` discards even that, so a
    `pip list` that fails outright is indistinguishable from one that resolved
    nothing interesting. That is acceptable for a bookkeeping step - the next
    step runs the tests and would fail on a broken environment - but it is
    worth pinning down, because it means this step can never be the thing that
    catches one.
    """

    result = _run(
        *_RECORD_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        fail="pip",
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_the_run_step_runs_pytest_with_no_arguments(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """This job is the pass/fail signal; coverage-gate.yml owns coverage.

    A `--cov` flag added here would measure coverage twice on every push, and
    a `-x` or `--maxfail` would report the first failure instead of the whole
    picture that `fail-fast: false` in the matrix is asking for.
    """

    result = _run(*_RUN_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("pytest") == ["pytest"]


def test_a_failing_test_run_fails_the_job(
    tmp_path: Path, stubs: Path, workspace: Path
) -> None:
    """The point of the whole workflow: red tests have to be a red check."""

    result = _run(
        *_RUN_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace, fail="pytest"
    )

    assert result.returncode != 0


def test_the_job_installs_and_records_before_it_runs_the_tests() -> None:
    """Order matters: nothing can be installed or recorded after the run.

    `Run tests` is the last step, so a `Record resolved versions` moved below
    it would only ever describe a job that already finished - and would not
    run at all once the tests failed.
    """

    names = [step.get("name") for step in _workflow()["jobs"]["pytest"]["steps"]]

    assert names.index(_INSTALL_STEP[1]) < names.index(_RECORD_STEP[1])
    assert names.index(_RECORD_STEP[1]) < names.index(_RUN_STEP[1])


def test_every_run_body_in_the_workflow_is_executed_here() -> None:
    """No step in ci.yml may carry shell that this file never runs."""

    found = {
        (job_name, step["name"])
        for job_name, job in _workflow()["jobs"].items()
        for step in job["steps"]
        if "run" in step
    }

    assert found == set(_SHELL_STEPS), (
        "ci.yml's shell steps have changed; add the new step to _SHELL_STEPS "
        "and a test that executes it"
    )
