"""The shell inside `.github/workflows/labeler.yml`'s `classify` job.

`scripts/classify_pr.py` is thoroughly tested by `test_classify_pr.py`, but
the workflow around it is not. Two steps carry shell: `Classify` redirects the
script's stdout into `classification.json`, and `Apply the labels` is a small
program - it reads that file, creates or updates each label with `--force`,
drops whichever `tier/*` and `size/*` labels the pull request is still
carrying from an earlier push, and then adds the current pair.

Nothing in this repository executes either of them. The one test that names
`labeler.yml` (`test_each_committed_rule_path_lands_in_its_own_tier`) passes
the path as a *classification input*; it never runs the workflow's shell.
`.coveragerc` measures `custom_components/sensi`, and a `run:` body is a
string inside YAML whatever coverage is pointed at, so the pieces that can rot
quietly - the redirect that makes the script's stdout the next step's input,
the quoting that keeps a label description in one piece, and the loop that
decides whether a stale label is removed - are held by nothing.

These tests close that the way `test_nightly_workflow.py`,
`test_coverage_gate_workflow.py`, `test_release_workflow.py` and
`test_validate_workflow.py` do: extract the step's `run:` body, substitute the
`${{ }}` expressions from an explicit context, and run the result under `bash`
with `gh` stubbed on `PATH`. The stub records every invocation, so the
assertions are about what the step would actually have asked GitHub to do
rather than about the text of the script. The `Classify` step is run against
the committed `scripts/classify_pr.py` and `.github/risk-tiers.yml` instead of
a stub, because the thing worth checking there is that what the real script
prints is what the real next step can parse.
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
_WORKFLOW = _ROOT / ".github" / "workflows" / "labeler.yml"
_RULES = _ROOT / ".github" / "risk-tiers.yml"

_JOB = "classify"

_INSTALL_STEP = ("classify", "Install PyYAML")
_CLASSIFY_STEP = ("classify", "Classify")
_APPLY_STEP = ("classify", "Apply the labels")

# Every step in labeler.yml that carries shell. Kept here so that adding a
# `run:` body to the workflow without a test for it fails
# `test_every_run_body_in_the_workflow_is_executed_here` rather than passing
# unnoticed.
_SHELL_STEPS = (_INSTALL_STEP, _CLASSIFY_STEP, _APPLY_STEP)

_PR = "44"

# What `gh pr view --json files` reports for the change the `Classify` step is
# run against: one runtime-tier file, small enough to land in `size/S`.
_CHANGE = [
    {"path": "custom_components/sensi/client.py", "additions": 24, "deletions": 6}
]

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")

_CONTEXT = {
    "github.token": "gh-token",
    "github.event.pull_request.number": _PR,
}


def _workflow() -> dict:
    """Return the parsed workflow."""

    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _step(job: str, name: str) -> dict:
    """Return the named step, failing if it has been renamed away."""

    document = _workflow()
    assert job in document["jobs"], (
        f"labeler.yml has no {job!r} job; these tests cover its shell and "
        "must be updated with it"
    )
    for step in document["jobs"][job]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"the {job!r} job in labeler.yml has no step named {name!r}; these "
        "tests cover its shell and must be updated with it"
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
                f"labeler.yml uses ${{{{ {expression} }}}}, which no test "
                "supplies a value for"
            )
        return context[expression]

    return _EXPRESSION.sub(replace, text)


class StepResult:
    """What running a step produced."""

    def __init__(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        summary: str,
        calls: list[list[str]],
        workspace: Path,
    ) -> None:
        """Store the exit status, streams, job summary, calls and workspace."""

        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.summary = summary
        self.calls = calls
        self.workspace = workspace

    def calls_starting(self, *prefix: str) -> list[list[str]]:
        """Return every recorded invocation starting with `prefix`."""

        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]

    def call(self, *prefix: str) -> list[str]:
        """Return the single recorded invocation starting with `prefix`."""

        matches = self.calls_starting(*prefix)
        assert len(matches) == 1, (
            f"expected exactly one {' '.join(prefix)!r} call, got "
            f"{len(matches)}: {self.calls}"
        )
        return matches[0]

    def option(self, call: list[str], flag: str) -> str:
        """Return the value `call` passed to `flag`."""

        assert flag in call, f"{flag} missing from {call}"
        return call[call.index(flag) + 1]


@pytest.fixture(name="stubs")
def stubs_fixture(tmp_path: Path) -> Path:
    """Build a PATH directory holding a recording `gh` and `python`.

    The `gh` stub answers `gh pr view --json files` with `$GH_FILES` and
    `gh pr view --json labels` with `$GH_LABELS`, which is how a test says
    "this is the change" and "the pull request is already carrying these
    labels". It fails whichever subcommands `$GH_FAIL` names, and appends
    every invocation to `$STUB_LOG` as a JSON array so the assertions can look
    at arguments rather than at text the step printed.

    `python` is recorded rather than run, because the only step that calls it
    by that name installs dependencies. `python3` is the interpreter running
    these tests: the steps that use it run real programs.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    recorder = (
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "arguments = sys.argv[1:]\n"
        'with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:\n'
        '    log.write(json.dumps([{tool!r}, *arguments]) + "\\n")\n'
        'failing = [f for f in os.environ.get("GH_FAIL", "").split(",") if f]\n'
        'if " ".join(arguments[:2]) in failing:\n'
        '    sys.stderr.write("{tool}: stubbed failure\\n")\n'
        "    sys.exit(1)\n"
    )

    stub = bin_dir / "gh"
    stub.write_text(
        recorder.format(tool="gh") + 'if arguments[:2] == ["pr", "view"]:\n'
        '    asked = arguments[arguments.index("--json") + 1]\n'
        '    print(os.environ["GH_FILES" if asked == "files" else "GH_LABELS"])\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    stub = bin_dir / "python"
    stub.write_text(recorder.format(tool="python") + "sys.exit(0)\n", encoding="utf-8")
    stub.chmod(0o755)

    # The interpreter these tests run under, so that `python3` in a step is a
    # real Python with this repository's dependencies importable.
    stub = bin_dir / "python3"
    stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    stub.chmod(0o755)

    return bin_dir


def _run(
    job: str,
    name: str,
    *,
    tmp_path: Path,
    stubs: Path,
    workspace: Path,
    labels: str = "",
    files: list[dict] | None = None,
    gh_fail: str = "",
) -> StepResult:
    """Run a step's `run:` body under bash in `workspace`.

    `bash -e` is what Actions uses for a `run:` body with no explicit shell,
    so a step that would have stopped on a failing command stops here too.
    """

    log_file = tmp_path / "stub.log"
    log_file.write_text("", encoding="utf-8")
    summary_file = tmp_path / "summary.md"
    summary_file.write_text("", encoding="utf-8")

    environment = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(workspace),
        "GITHUB_STEP_SUMMARY": str(summary_file),
        "STUB_LOG": str(log_file),
        "GH_LABELS": labels,
        "GH_FILES": json.dumps({"files": _CHANGE if files is None else files}),
        "GH_FAIL": gh_fail,
    }
    # Layered as Actions layers them, so the step reads PR and GH_TOKEN from
    # the workflow's own `env:` block rather than from values invented here.
    document = _workflow()
    for block in (
        document.get("env") or {},
        document["jobs"][job].get("env") or {},
        _step(job, name).get("env") or {},
    ):
        environment.update(
            {key: _render(str(value), _CONTEXT) for key, value in block.items()}
        )

    completed = subprocess.run(  # noqa: S603
        ["bash", "-e", "-c", _render(_step(job, name)["run"], _CONTEXT)],
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
        summary_file.read_text(encoding="utf-8"),
        calls,
        workspace,
    )


def _checkout(tmp_path: Path) -> Path:
    """Return a working directory shaped like the job's checkout.

    `scripts/` and `.github/` are the committed ones: the `Classify` step runs
    the real classifier against the real rules file, which is the only way the
    assertion about what it prints means anything.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "scripts").symlink_to(_ROOT / "scripts")
    (workspace / ".github").symlink_to(_ROOT / ".github")
    return workspace


def _classified(workspace: Path, tier: dict | None, size: dict | None) -> Path:
    """Write the `classification.json` the `Classify` step would have left."""

    path = workspace / "classification.json"
    path.write_text(
        json.dumps(
            {"changed_files": 1, "changed_lines": 30, "tier": tier, "size": size},
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _rules() -> dict:
    """Return the committed tier and size rules."""

    return yaml.safe_load(_RULES.read_text(encoding="utf-8"))


def _tier(name: str) -> dict:
    """Return the classifier's label shape for a committed tier."""

    entry = next(t for t in _rules()["tiers"] if t["name"] == name)
    return {
        "name": entry["name"],
        "colour": entry["label_colour"],
        "description": entry["label_description"],
    }


def _size(name: str, lines: int) -> dict:
    """Return the classifier's label shape for a size bucket."""

    return {"name": name, "colour": "c5def5", "description": f"{lines} lines changed"}


def test_every_run_body_in_the_workflow_is_executed_here() -> None:
    """A new `run:` body in labeler.yml has to be covered by this file."""

    document = _workflow()
    carrying_shell = {
        (job_name, step["name"])
        for job_name, job in document["jobs"].items()
        for step in job["steps"]
        if "run" in step
    }

    assert carrying_shell == set(_SHELL_STEPS)


def test_the_job_reads_the_base_repository_and_can_only_write_labels() -> None:
    """`pull_request_target` runs with a write token; nothing runs fork code.

    The checkout is pinned to the base commit rather than defaulted, and the
    only write the workflow's token permits is the one the job exists for.
    """

    document = _workflow()

    assert document["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
    }
    checkout = next(
        step
        for step in document["jobs"][_JOB]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"


def test_the_install_step_installs_what_the_classifier_imports(
    tmp_path: Path, stubs: Path
) -> None:
    """`scripts/classify_pr.py` imports `yaml`; the job installs it.

    The runner's image has no PyYAML, so dropping it from this step turns the
    next step into an `ImportError` rather than a classification.
    """

    result = _run(
        *_INSTALL_STEP, tmp_path=tmp_path, stubs=stubs, workspace=_checkout(tmp_path)
    )

    assert result.returncode == 0, result.stderr
    assert result.call("python") == [
        "python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
        "pyyaml",
    ]


def test_the_classify_step_leaves_a_file_the_next_step_can_parse(
    tmp_path: Path, stubs: Path
) -> None:
    """The redirect is the seam between the two steps.

    `Apply the labels` reads `classification.json` with `json.load`, so
    anything the classifier prints to stdout alongside the JSON - a warning, a
    progress line - breaks a step that never mentions the classifier. This
    runs the real script against the real rules file and parses what lands.
    """

    workspace = _checkout(tmp_path)

    result = _run(
        *_CLASSIFY_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        labels="",
    )

    assert result.returncode == 0, result.stderr
    assert result.call("gh", "pr", "view") == [
        "gh",
        "pr",
        "view",
        _PR,
        "--json",
        "files",
    ]
    written = json.loads((workspace / "classification.json").read_text("utf-8"))
    assert written["tier"] == _tier("tier/runtime")
    assert written["size"] == _size("size/S", written["changed_lines"])
    # `cat classification.json` is the step's own record in the run log.
    assert json.loads(result.stdout) == written


def test_a_failed_lookup_stops_the_job_rather_than_labelling_from_an_empty_file(
    tmp_path: Path, stubs: Path
) -> None:
    """A `gh` failure must not reach `Apply the labels`.

    The redirect creates `classification.json` before the classifier runs, so
    a failure leaves an empty file behind. What keeps that out of the next
    step is the step's own non-zero exit, not anything in the file.
    """

    workspace = _checkout(tmp_path)

    result = _run(
        *_CLASSIFY_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        gh_fail="pr view",
    )

    assert result.returncode != 0
    assert (workspace / "classification.json").read_text("utf-8") == ""


def test_the_apply_step_creates_both_labels_and_adds_them_together(
    tmp_path: Path, stubs: Path
) -> None:
    """Each label is forced to the committed colour and description.

    A tier description is a sentence with spaces in it, and it has to reach
    `gh` as one argument. `--force` updates an existing label rather than
    failing, which is what stops a label created by hand months ago from
    keeping a random colour forever.
    """

    workspace = _checkout(tmp_path)
    tier = _tier("tier/runtime")
    size = _size("size/M", 250)
    _classified(workspace, tier, size)

    result = _run(
        *_APPLY_STEP, tmp_path=tmp_path, stubs=stubs, workspace=workspace, labels=""
    )

    assert result.returncode == 0, result.stderr
    for label in (tier, size):
        call = result.call("gh", "label", "create", label["name"])
        assert "--force" in call
        assert result.option(call, "--color") == label["colour"]
        # One argument, not one word per space.
        assert result.option(call, "--description") == label["description"]

    assert result.call("gh", "pr", "edit") == [
        "gh",
        "pr",
        "edit",
        _PR,
        "--add-label",
        f"{tier['name']},{size['name']}",
    ]
    assert result.summary.strip() == f"Applied: {tier['name']},{size['name']}"


def test_the_apply_step_drops_the_stale_pair_and_leaves_everything_else(
    tmp_path: Path, stubs: Path
) -> None:
    """Re-pushing a branch must not leave two tiers or two sizes on it.

    A push that moves a change from `tier/breaking` to `tier/support`, or from
    `size/XL` to `size/L`, has to take the old label off: a pull request
    carrying two tiers tells a reviewer nothing. Labels outside the two
    namespaces - a `hold`, a `documentation` - are none of this step's
    business and have to survive it.
    """

    workspace = _checkout(tmp_path)
    tier = _tier("tier/support")
    size = _size("size/L", 900)
    _classified(workspace, tier, size)

    result = _run(
        *_APPLY_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        labels="tier/breaking\nsize/XL\nhold\ndocumentation",
    )

    assert result.returncode == 0, result.stderr
    removed = {
        result.option(call, "--remove-label")
        for call in result.calls_starting("gh", "pr", "edit")
        if "--remove-label" in call
    }
    assert removed == {"tier/breaking", "size/XL"}
    assert result.call("gh", "pr", "edit", _PR, "--add-label")[-1] == (
        f"{tier['name']},{size['name']}"
    )


def test_the_apply_step_removes_nothing_when_the_labels_are_already_right(
    tmp_path: Path, stubs: Path
) -> None:
    """Every push re-runs this; an unchanged classification is a no-op.

    Removing and re-adding the same label would notify every subscriber of the
    pull request twice per push.
    """

    workspace = _checkout(tmp_path)
    tier = _tier("tier/behaviour")
    size = _size("size/XS", 12)
    _classified(workspace, tier, size)

    result = _run(
        *_APPLY_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        labels=f"{tier['name']}\n{size['name']}\nhold",
    )

    assert result.returncode == 0, result.stderr
    assert [c for c in result.calls if "--remove-label" in c] == []


def test_a_rejected_label_fails_the_step_before_anything_is_labelled(
    tmp_path: Path, stubs: Path
) -> None:
    """The `set -euo pipefail` at the top is the point of this test.

    When this step tolerated a failing `gh label create`, an HTTP 422 on the
    description turned into an unrelated-looking "not found" from the
    add-label call further down. It must stop where the error is.
    """

    workspace = _checkout(tmp_path)
    _classified(workspace, _tier("tier/runtime"), _size("size/M", 250))

    result = _run(
        *_APPLY_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        gh_fail="label create",
    )

    assert result.returncode != 0
    assert result.calls_starting("gh", "pr", "edit") == []
    assert result.summary == ""


def test_the_apply_step_labels_what_it_has_when_a_bucket_is_missing(
    tmp_path: Path, stubs: Path
) -> None:
    """A classification carrying only a tier applies only that tier.

    The committed rules always produce both, so this is the guard rather than
    the normal path: with a rules file that lost its `sizes`, the step applies
    the tier it does have instead of asking `gh` for an empty label name.
    """

    workspace = _checkout(tmp_path)
    tier = _tier("tier/breaking")
    _classified(workspace, tier, None)

    result = _run(
        *_APPLY_STEP,
        tmp_path=tmp_path,
        stubs=stubs,
        workspace=workspace,
        labels="size/XS",
    )

    assert result.returncode == 0, result.stderr
    assert len(result.calls_starting("gh", "label", "create")) == 1
    assert result.call("gh", "label", "create", tier["name"])
    assert result.call("gh", "pr", "edit", _PR, "--add-label")[-1] == tier["name"]
    # The size label the pull request was carrying is still stale and still
    # goes, even though this classification names no replacement.
    assert (
        result.option(
            result.call("gh", "pr", "edit", _PR, "--remove-label"), "--remove-label"
        )
        == "size/XS"
    )
