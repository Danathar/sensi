"""The shell inside `.github/workflows/nightly.yml`'s `report` job.

The nightly run's two test legs are ordinary `pytest` invocations, but the
`report` job is a program: it looks up whether a nightly issue is already
open, then either closes it, comments on it, or opens it with a freshly built
body. That decision is the whole point of the nightly - a compliance failure
nobody is told about is the same as no nightly at all - and nothing in this
repository executes it. `.coveragerc` measures `custom_components/sensi`, and
a `run:` body is a string inside YAML whatever coverage is pointed at;
`tests/e2e` drives Home Assistant against the Sensi service and never runs a
workflow step.

These tests close that the way `test_coverage_gate_workflow.py` and
`test_release_workflow.py` do: extract the step's `run:` body, substitute the
`${{ }}` expressions from an explicit context, and run the result under `bash`
with `gh` stubbed on `PATH`. The stub records every invocation, so the
assertions are about what the step would actually have done to the issue
tracker rather than about the text of the script.
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

_STEP = "Open, update or close the nightly issue"
_JOB = "report"

# The title the step searches for and, when it finds nothing, opens. Written
# out here rather than read from the workflow so that a change to it has to be
# made in both places deliberately.
_TITLE = "Nightly compliance failing"

_REPO = "Danathar/sensi"
_RUN_URL = f"https://github.com/{_REPO}/actions/runs/1234567890"

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")

_CONTEXT = {
    "github.token": "gh-token",
    "github.repository": _REPO,
    "github.server_url": "https://github.com",
    "github.run_id": "1234567890",
}


def _workflow() -> dict:
    """Return the parsed workflow."""

    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _step() -> dict:
    """Return the reporting step, failing if it has been renamed away."""

    for step in _workflow()["jobs"][_JOB]["steps"]:
        if step.get("name") == _STEP:
            return step
    raise AssertionError(
        f"the {_JOB!r} job in nightly.yml has no step named {_STEP!r}; these "
        "tests cover its logic and must be updated with it"
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
                f"nightly.yml uses ${{{{ {expression} }}}}, which no test "
                "supplies a value for"
            )
        return context[expression]

    return _EXPRESSION.sub(replace, text)


class StepResult:
    """What running the reporting step produced."""

    def __init__(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        summary: str,
        calls: list[list[str]],
    ) -> None:
        """Store the exit status, streams, job summary and `gh` invocations."""

        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.summary = summary
        self.calls = calls

    def call(self, *prefix: str) -> list[str]:
        """Return the single `gh` invocation starting with `prefix`."""

        matches = [c for c in self.calls if c[: len(prefix)] == list(prefix)]
        assert len(matches) == 1, (
            f"expected exactly one `gh {' '.join(prefix)}` call, got "
            f"{len(matches)}: {self.calls}"
        )
        return matches[0]

    def calls_starting(self, *prefix: str) -> list[list[str]]:
        """Return every `gh` invocation starting with `prefix`."""

        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]

    def option(self, call: list[str], flag: str) -> str:
        """Return the value `call` passed to `flag`."""

        assert flag in call, f"{flag} missing from {call}"
        return call[call.index(flag) + 1]


@pytest.fixture(name="stubs")
def stubs_fixture(tmp_path: Path) -> Path:
    """Build a PATH directory holding a recording `gh`.

    The stub answers `gh issue list` with `$GH_EXISTING`, which is how a test
    says "an issue is already open" or "none is", fails whichever
    subcommands `$GH_FAIL` names, and appends every invocation to `$GH_LOG`
    as a JSON array so the assertions can look at arguments rather than at
    text the step printed.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    stub = bin_dir / "gh"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "arguments = sys.argv[1:]\n"
        'with open(os.environ["GH_LOG"], "a", encoding="utf-8") as log:\n'
        '    log.write(json.dumps(arguments) + "\\n")\n'
        'failing = [f for f in os.environ.get("GH_FAIL", "").split(",") if f]\n'
        'if " ".join(arguments[:2]) in failing:\n'
        '    sys.stderr.write("gh: stubbed failure\\n")\n'
        "    sys.exit(1)\n"
        'if arguments[:2] == ["issue", "list"]:\n'
        '    print(os.environ.get("GH_EXISTING", ""))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir


def _run(
    tmp_path: Path,
    stubs: Path,
    *,
    pinned: str,
    latest: str = "success",
    existing: str = "",
    gh_fail: str = "",
) -> StepResult:
    """Run the reporting step with the leg results and issue state given."""

    document = _workflow()
    script = _render(_step()["run"], _CONTEXT)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    summary_file = tmp_path / "summary.md"
    summary_file.write_text("", encoding="utf-8")
    log_file = tmp_path / "gh.log"
    log_file.write_text("", encoding="utf-8")

    environment = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(workspace),
        "GITHUB_STEP_SUMMARY": str(summary_file),
        "GH_LOG": str(log_file),
        "GH_EXISTING": existing,
        "GH_FAIL": gh_fail,
    }
    # Layered as Actions layers them, so the step reads REPO, RUN_URL, PINNED
    # and LATEST from the workflow's own `env:` block rather than from values
    # invented here.
    for block in (
        document.get("env") or {},
        document["jobs"][_JOB].get("env") or {},
        _step().get("env") or {},
    ):
        environment.update(
            {
                key: _render(str(value), {**_CONTEXT, **_leg_context(pinned, latest)})
                for key, value in block.items()
            }
        )

    completed = subprocess.run(  # noqa: S603
        ["bash", "-c", script],
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
    )


def _leg_context(pinned: str, latest: str) -> dict[str, str]:
    """Return the `needs` expressions carrying the two legs' results."""

    return {
        "needs.pinned.result": pinned,
        "needs.latest.outputs.result": latest,
    }


def _body(result: StepResult, call: list[str]) -> str:
    """Return the `--body` `call` passed."""

    return result.option(call, "--body")


def test_a_green_nightly_with_no_open_issue_only_looks(
    tmp_path: Path, stubs: Path
) -> None:
    """The quiet path: nothing is opened, commented on or closed."""

    result = _run(tmp_path, stubs, pinned="success")

    assert result.returncode == 0, result.stderr
    assert [c[:2] for c in result.calls] == [["issue", "list"]]
    assert "Nightly green; nothing to report." in result.summary


def test_a_green_nightly_closes_the_issue_it_finds(tmp_path: Path, stubs: Path) -> None:
    """Recovery comments on the open issue and then closes it.

    The comment has to come first: closing is what stops later runs from
    finding the issue, so a comment posted afterwards explains the closure on
    an issue that is already shut.
    """

    result = _run(tmp_path, stubs, pinned="success", existing="42")

    assert result.returncode == 0, result.stderr
    comment = result.call("issue", "comment")
    close = result.call("issue", "close")
    assert comment[2] == "42"
    assert close[2] == "42"
    assert result.calls.index(comment) < result.calls.index(close)
    assert result.option(comment, "--repo") == _REPO
    assert _RUN_URL in _body(result, comment)
    assert not result.calls_starting("issue", "create")


def test_a_failed_nightly_with_no_open_issue_opens_one(
    tmp_path: Path, stubs: Path
) -> None:
    """The first failure opens the issue, labelled, with both legs recorded."""

    result = _run(tmp_path, stubs, pinned="failure", latest="success")

    assert result.returncode == 0, result.stderr
    created = result.call("issue", "create")
    assert result.option(created, "--title") == _TITLE
    assert result.option(created, "--label") == "ci"
    assert result.option(created, "--repo") == _REPO

    body = _body(result, created)
    assert "- Pinned leg: `failure`" in body
    assert "- Latest-Home-Assistant leg: `success`" in body
    assert f"- Run: {_RUN_URL}" in body
    assert not result.calls_starting("issue", "comment")
    assert not result.calls_starting("issue", "close")


def test_a_failed_nightly_reuses_the_issue_it_finds(
    tmp_path: Path, stubs: Path
) -> None:
    """A second failure comments rather than opening a duplicate."""

    result = _run(tmp_path, stubs, pinned="failure", existing="7")

    assert result.returncode == 0, result.stderr
    comment = result.call("issue", "comment")
    assert comment[2] == "7"
    assert "- Pinned leg: `failure`" in _body(result, comment)
    assert not result.calls_starting("issue", "create")
    assert not result.calls_starting("label", "create")
    assert not result.calls_starting("issue", "close")


def test_the_issue_is_searched_for_under_the_title_it_is_opened_with(
    tmp_path: Path, stubs: Path
) -> None:
    """The lookup and the creation have to agree on the title.

    If they drift apart the step stops finding its own issue and opens a new
    one every night, which is the failure mode this reuse logic exists to
    avoid - and it is invisible in a single run.
    """

    lookup = _run(tmp_path, stubs, pinned="failure")
    search = lookup.option(lookup.call("issue", "list"), "--search")
    created = lookup.option(lookup.call("issue", "create"), "--title")

    assert search == f'"{created}" in:title'
    assert created == _TITLE
    assert lookup.option(lookup.call("issue", "list"), "--state") == "open"


def test_a_latest_leg_that_never_ran_is_reported_as_such(
    tmp_path: Path, stubs: Path
) -> None:
    """The advance-warning leg is optional, so its absence is spelled out.

    `needs.latest.outputs.result` is empty when that job did not reach its
    test step, and the body has to say so rather than render an empty pair of
    backticks that reads as "no result was recorded" only to someone who
    knows the workflow.
    """

    result = _run(tmp_path, stubs, pinned="failure", latest="")

    body = _body(result, result.call("issue", "create"))
    assert "- Latest-Home-Assistant leg: `not run`" in body
    assert "leg: ``" not in body


def test_the_body_is_not_indented_by_the_yaml_block(
    tmp_path: Path, stubs: Path
) -> None:
    """The heredoc is unindented, so the issue renders as Markdown.

    `<<BODY` does not strip leading whitespace. Indenting the heredoc to line
    up with the `if` around it would push every line four spaces in, turning
    the paragraphs into code blocks and the leg lines into indented text
    rather than a list, in an issue no test reads.
    """

    result = _run(tmp_path, stubs, pinned="failure")

    body = _body(result, result.call("issue", "create"))
    assert body.startswith("The nightly compliance run failed")
    indented = [line for line in body.splitlines() if line[:1].isspace()]
    assert not indented, f"indented body lines: {indented}"


def test_an_unlabelled_repository_still_gets_its_issue(
    tmp_path: Path, stubs: Path
) -> None:
    """A `ci` label that cannot be created must not swallow the report.

    `gh label create` fails whenever the label already exists, which is every
    night after the first, so its failure is deliberately ignored. Under
    `set -e` that only holds because of the `|| true`.
    """

    result = _run(tmp_path, stubs, pinned="failure", gh_fail="label create")

    assert result.returncode == 0, result.stderr
    assert result.calls_starting("label", "create")
    assert result.option(result.call("issue", "create"), "--title") == _TITLE


def test_a_failed_lookup_is_not_reported_as_no_open_issue(
    tmp_path: Path, stubs: Path
) -> None:
    """If the search itself fails the step stops instead of opening a duplicate.

    `set -o pipefail` and `set -e` are what make this true: the empty output
    of a failed `gh issue list` is otherwise indistinguishable from "no issue
    is open".
    """

    result = _run(tmp_path, stubs, pinned="failure", gh_fail="issue list")

    assert result.returncode != 0
    assert not result.calls_starting("issue", "create")


def test_the_advance_warning_leg_is_read_from_its_step_not_its_job(
    tmp_path: Path, stubs: Path
) -> None:
    """`continue-on-error` makes the job's own result useless.

    The `latest` job is allowed to fail, which means `needs.latest.result` is
    reported as success even when its tests failed. The real outcome survives
    only through the step outcome the job publishes as an output, so the
    report has to read `needs.latest.outputs.result`.
    """

    document = _workflow()
    latest = document["jobs"]["latest"]

    assert latest["continue-on-error"] is True
    assert latest["outputs"]["result"] == "${{ steps.run.outcome }}"
    assert _step()["env"]["LATEST"] == "${{ needs.latest.outputs.result }}"
    assert "steps.run.outcome" not in _step()["env"]["PINNED"]
