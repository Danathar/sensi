"""The shell inside `.github/workflows/validate.yml`.

`validate.yml` is the only workflow in this repository that no test file
names. Three of its four jobs are a single pinned action, but the `lint` job
carries a small program: it reads the ruff version out of
`requirements_test.txt` with an anchored `grep`, hands the result to `pip` as
one quoted word, and then runs ruff twice with flags that decide whether a
lint failure is annotated on the pull request or merely printed. The
`requirements-sync` job runs a checked-in script by path.

Nothing executes any of that. `.coveragerc` measures
`custom_components/sensi`, and a `run:` body is a string inside YAML whatever
coverage is pointed at; `tests/e2e` drives Home Assistant against a scripted
Sensi backend and never runs a workflow step. So the pieces that can rot
quietly - the `^ruff==` anchor, the quoting around the command substitution,
`--output-format=github`, the script path - are held by nothing.

These tests close that the way `test_nightly_workflow.py`,
`test_coverage_gate_workflow.py` and `test_release_workflow.py` do: extract
the step's `run:` body and run it under `bash` with the tools it calls stubbed
on `PATH`. The stubs record their arguments, so the assertions are about what
the step would actually have asked `pip` and `ruff` to do rather than about
the text of the script. The `requirements-sync` step is run for real against
the committed tree instead, because the thing worth checking there is that the
path in the workflow still names a script that passes.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "validate.yml"
_TEST_REQUIREMENTS = _ROOT / "requirements_test.txt"

_INSTALL_STEP = ("lint", "Install ruff")
_CHECK_STEP = ("lint", "ruff check")
_FORMAT_STEP = ("lint", "ruff format")
_SYNC_STEP = (
    "requirements-sync",
    "Compare manifest requirements against requirements_component.txt",
)

# Every step in validate.yml that carries shell. Kept here so that adding a
# `run:` body to the workflow without a test for it fails
# `test_every_run_body_in_the_workflow_is_executed_here` rather than passing
# unnoticed.
_SHELL_STEPS = (_INSTALL_STEP, _CHECK_STEP, _FORMAT_STEP, _SYNC_STEP)


def _workflow() -> dict:
    """Return the parsed workflow."""

    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _step(job: str, name: str) -> dict:
    """Return the named step, failing if it has been renamed away."""

    document = _workflow()
    assert job in document["jobs"], (
        f"validate.yml has no {job!r} job; these tests cover its shell and "
        "must be updated with it"
    )
    for step in document["jobs"][job]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"the {job!r} job in validate.yml has no step named {name!r}; these "
        "tests cover its shell and must be updated with it"
    )


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
    text. `$STUB_FAIL` names the tools that should exit non-zero, which is how
    a test says "ruff found a problem".
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    for tool in ("python", "pip", "ruff"):
        stub = bin_dir / tool
        stub.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"arguments = [{tool!r}, *sys.argv[1:]]\n"
            'with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:\n'
            '    log.write(json.dumps(arguments) + "\\n")\n'
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


def _workspace(tmp_path: Path, requirements: str) -> Path:
    """Return a checkout-shaped directory holding `requirements_test.txt`."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "requirements_test.txt").write_text(requirements, encoding="utf-8")
    return workspace


def _pinned_ruff() -> str:
    """Return the single `ruff==` requirement the repository actually pins."""

    matches = [
        line
        for line in _TEST_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.startswith("ruff==")
    ]
    assert len(matches) == 1, (
        f"requirements_test.txt must pin exactly one ruff version for the "
        f"lint job's grep to resolve; found {matches}"
    )
    return matches[0]


def test_the_install_step_installs_the_version_the_repository_pins(
    tmp_path: Path, stubs: Path
) -> None:
    """Against the committed requirements file, pip gets the pinned ruff.

    This is the assertion the whole file exists for: the workflow's `grep`
    pattern and `requirements_test.txt` are a contract between two files that
    nothing else checks, and a `grep` that matches nothing still exits the
    step successfully with an empty install target.
    """

    workspace = _workspace(tmp_path, _TEST_REQUIREMENTS.read_text(encoding="utf-8"))

    result = _run(*_INSTALL_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("pip") == ["pip", "install", _pinned_ruff()]
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


def test_the_install_step_ignores_comments_and_other_packages(
    tmp_path: Path, stubs: Path
) -> None:
    """The `^` anchor is load-bearing.

    `requirements_test.txt` explains the ruff pin in comments directly above
    it, and other pinned tools have names ending in `ruff` is not far off. An
    unanchored pattern would pick a version out of prose or out of a different
    package.
    """

    workspace = _workspace(
        tmp_path,
        "# ruff==9.9.9 was too old for the rules selected in ruff.toml\n"
        "pytest-ruff==1.2.3\n"
        "ruff==0.16.6\n"
        "  ruff==8.8.8\n",
    )

    result = _run(*_INSTALL_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.only_call_to("pip") == ["pip", "install", "ruff==0.16.6"]


def test_the_install_step_passes_the_requirement_as_one_word(
    tmp_path: Path, stubs: Path
) -> None:
    """The quotes around the command substitution are load-bearing.

    Two `ruff==` lines make the substitution multi-line. Quoted, that reaches
    pip as one unsatisfiable requirement and the job fails loudly; unquoted,
    the shell would split it and pip would install whichever of the two it
    resolved last. Asserting the single argument keeps the noisy failure.
    """

    workspace = _workspace(tmp_path, "ruff==0.16.6\nruff==0.17.0\n")

    result = _run(*_INSTALL_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.only_call_to("pip") == [
        "pip",
        "install",
        "ruff==0.16.6\nruff==0.17.0",
    ]


def test_the_install_step_does_not_fail_when_the_pin_disappears(
    tmp_path: Path, stubs: Path
) -> None:
    """A missing pin is silent, which is why the first test uses the real file.

    `grep` finding nothing exits 1, but its status is discarded inside `$( )`
    and `set -e` never sees it, so the step asks pip to install the empty
    string. Recording that here states the failure mode instead of leaving a
    reader to assume the workflow catches it.
    """

    workspace = _workspace(tmp_path, "pytest==9.0.3\n")

    result = _run(*_INSTALL_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.only_call_to("pip") == ["pip", "install", ""]


def test_the_check_step_annotates_the_pull_request(tmp_path: Path, stubs: Path) -> None:
    """Lint findings have to reach the diff, not just the log.

    `--output-format=github` is what turns a finding into a review annotation
    on the changed line; without it the finding is only in the job log.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _run(*_CHECK_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("ruff") == [
        "ruff",
        "check",
        "--output-format=github",
        ".",
    ]


def test_the_check_step_fails_the_job_when_ruff_does(
    tmp_path: Path, stubs: Path
) -> None:
    """A lint finding has to stop the job, not just print."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _run(
        *_CHECK_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace, fail="ruff"
    )

    assert result.returncode != 0


def test_the_format_step_checks_without_rewriting(tmp_path: Path, stubs: Path) -> None:
    """`--check` is what makes this a gate.

    `ruff format .` on its own reformats the checkout and exits 0, so the job
    would pass while reporting nothing about unformatted code.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _run(*_FORMAT_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace)

    assert result.returncode == 0, result.stderr
    assert result.only_call_to("ruff") == ["ruff", "format", "--check", "."]


def test_the_format_step_fails_the_job_when_ruff_does(
    tmp_path: Path, stubs: Path
) -> None:
    """Unformatted code has to stop the job."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _run(
        *_FORMAT_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace, fail="ruff"
    )

    assert result.returncode != 0


def test_the_requirements_sync_step_runs_a_script_that_passes(
    tmp_path: Path,
) -> None:
    """Run the step for real against the committed tree.

    `tests/test_check_requirements_sync.py` covers the script's logic by
    importing it; what is untested is the workflow's side of the seam - the
    interpreter name and the path it hands to it. Running the body verbatim
    from the repository root catches the script being moved or renamed, which
    the unit tests cannot see.
    """

    script = _step(*_SYNC_STEP)["run"]

    completed = subprocess.run(  # noqa: S603
        ["bash", "-e", "-c", script],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        f"validate.yml's requirements-sync step failed:\n{completed.stdout}"
        f"{completed.stderr}"
    )


def test_the_lint_job_installs_ruff_before_it_runs_it() -> None:
    """Order matters: the install step has to precede both ruff steps."""

    names = [step.get("name") for step in _workflow()["jobs"]["lint"]["steps"]]

    assert names.index(_INSTALL_STEP[1]) < names.index(_CHECK_STEP[1])
    assert names.index(_INSTALL_STEP[1]) < names.index(_FORMAT_STEP[1])


def test_every_run_body_in_the_workflow_is_executed_here() -> None:
    """No step in validate.yml may carry shell that this file never runs."""

    found = {
        (job_name, step["name"])
        for job_name, job in _workflow()["jobs"].items()
        for step in job["steps"]
        if "run" in step
    }

    assert found == set(_SHELL_STEPS), (
        "validate.yml's shell steps have changed; add the new step to "
        "_SHELL_STEPS and a test that executes it"
    )
