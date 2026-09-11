"""The required status checks and the jobs that produce them must agree.

`.github/rulesets/master.json` names the checks a pull request has to pass by
*string*. Nothing joins those strings to the jobs in `.github/workflows/` that
produce them, and both ends move: a job renamed, a matrix value bumped, a new
gating job added. Neither end of that drift fails anything.

The two directions fail differently, and both are silent.

A required check no name produces is never reported, so it sits pending and
every pull request blocks forever - a repository-wide outage caused by a
rename in a file that looks unrelated to protection.

A gating job that is *not* required is worse, because nothing looks wrong: the
job runs, goes red, and the pull request merges anyway. That is the state the
ruleset exists to prevent, arrived at by adding a job rather than by removing
a rule.

`tests/test_check_ruleset.py` covers the definition and what GitHub enforces.
Both of those can agree perfectly while naming checks the workflows no longer
produce, so this is the join neither of them makes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_DEFINITION = _ROOT / ".github" / "rulesets" / "master.json"

_SCRIPT = _ROOT / "scripts" / "check_ruleset.py"
_spec = importlib.util.spec_from_file_location("check_ruleset", _SCRIPT)
check_ruleset = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_ruleset)

# Jobs that run on a pull request and are deliberately advisory rather than
# gating. Empty on purpose: today every unconditional pull request job is
# required. A job added here is a decision that it may go red without stopping
# a merge, which is exactly the decision that should be visible in a diff.
_ADVISORY: frozenset[str] = frozenset()

# GitHub Actions runs a file in `.github/workflows/` that ends in either
# extension, so both are read here. A spelling left out would not narrow the
# comparisons below - it would exempt that file from all of them, and a job it
# runs on every pull request could then gate nothing while the suite is green.
_WORKFLOW_GLOBS = ("*.yml", "*.yaml")


def _workflow_paths(directory: Path = _WORKFLOWS) -> list[Path]:
    """Every workflow file in `directory`, whichever extension it uses."""
    return sorted(path for glob in _WORKFLOW_GLOBS for path in directory.glob(glob))


def _workflows() -> dict[str, dict]:
    """Every workflow document, keyed by file name."""
    documents = {}
    for path in _workflow_paths():
        documents[path.name] = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert documents, f"no workflows found under {_WORKFLOWS}"
    return documents


def _triggers(workflow: dict) -> dict:
    """Return the `on:` block.

    PyYAML resolves an unquoted `on` key to the boolean True (the YAML 1.1
    y/n/on/off rule), so both spellings have to be accepted or every workflow
    reads as having no triggers at all.
    """
    return workflow.get("on", workflow.get(True)) or {}


def _job_names(workflow: dict) -> dict[str, dict]:
    """Check names this workflow reports, mapped to their job definition.

    The name a status check appears under is the job's `name:`, falling back to
    its id, with a matrix job producing one check per combination.
    """
    names: dict[str, dict] = {}
    for job_id, job in (workflow.get("jobs") or {}).items():
        template = job.get("name", job_id)
        matrix = ((job.get("strategy") or {}).get("matrix")) or {}
        for name in _expand(template, matrix):
            names[name] = job
    return names


def _expand(template: str, matrix: dict) -> list[str]:
    """Render `${{ matrix.x }}` in a job name for every value of x.

    Only matrix expressions are understood. Anything else in a job name -
    `github.event_name`, an input - would make the reported check name depend
    on the run rather than on the file, and no required check can be written
    against that; raising says so rather than silently comparing a name
    containing `${{`.
    """
    rendered = [template]
    for key, values in matrix.items():
        if key in {"include", "exclude"} or not isinstance(values, list):
            continue
        token = "${{ matrix." + key + " }}"
        if not any(token in candidate for candidate in rendered):
            continue
        rendered = [
            candidate.replace(token, str(value))
            for candidate in rendered
            for value in values
        ]
    for name in rendered:
        assert "${{" not in name, (
            f"job name {template!r} interpolates something other than a matrix "
            "value, so the check name it reports cannot be known from the file"
        )
    return rendered


def _pull_request_checks() -> dict[str, tuple[str, dict]]:
    """Every check name a pull request produces, mapped to (workflow, job).

    `pull_request_target` is excluded deliberately: it is the labelling
    workflow, it runs with a write token against the base repository, and it
    is not a gate on the change.
    """
    checks: dict[str, tuple[str, dict]] = {}
    for filename, workflow in _workflows().items():
        if "pull_request" not in _triggers(workflow):
            continue
        for name, job in _job_names(workflow).items():
            checks[name] = (filename, job)
    return checks


@pytest.fixture(name="required")
def required_fixture() -> set[str]:
    """Return the contexts the committed ruleset requires."""
    definition = json.loads(_DEFINITION.read_text(encoding="utf-8"))
    return check_ruleset._required_checks(definition)


def test_every_required_check_is_produced_by_a_pull_request_job(
    required: set[str],
) -> None:
    """Otherwise it never reports, and every pull request blocks forever.

    This is the failure a job rename causes, and the rename is in a workflow
    file that has nothing obviously to do with branch protection.
    """
    produced = set(_pull_request_checks())

    assert required <= produced, (
        f"required checks nothing produces: {sorted(required - produced)}; "
        f"pull requests would block forever. Produced: {sorted(produced)}"
    )


def test_every_gating_pull_request_job_is_required(required: set[str]) -> None:
    """A job that runs on a pull request and is not required is not a gate.

    It goes red and the merge proceeds. Nothing about that looks wrong, which
    is why it needs a test rather than a review.
    """
    gating = {
        name
        for name, (_, job) in _pull_request_checks().items()
        if "if" not in job and name not in _ADVISORY
    }

    assert gating <= required, (
        f"jobs that run on every pull request but cannot block one: "
        f"{sorted(gating - required)}. Add them to the ruleset, or to "
        f"_ADVISORY if going red is genuinely allowed to merge."
    )


def test_no_required_check_comes_from_a_conditional_job(required: set[str]) -> None:
    """A job with an `if:` may not report at all, and pending never resolves.

    `publish badge and trend` is the live example: it runs only on a push to
    master, so requiring it would deadlock the merge queue rather than gate it.
    """
    conditional = {
        name for name, (_, job) in _pull_request_checks().items() if "if" in job
    }

    assert not (required & conditional), (
        f"required checks that only run conditionally: {sorted(required & conditional)}"
    )


def test_no_required_check_comes_from_a_path_filtered_workflow(
    required: set[str],
) -> None:
    """A path filter means the workflow does not run for some pull requests.

    The check then stays pending rather than being skipped, so the filter
    silently converts the gate into a block on every change that misses it.
    """
    filtered = set()
    for name, (filename, _) in _pull_request_checks().items():
        trigger = _triggers(_workflows()[filename])["pull_request"] or {}
        if "paths" in trigger or "paths-ignore" in trigger:
            filtered.add(name)

    assert not (required & filtered), (
        f"required checks from a path-filtered workflow: {sorted(required & filtered)}"
    )


def test_the_matrix_the_required_pytest_check_names_still_exists() -> None:
    """The Python version is inside the check name.

    Bumping `ci.yml`'s matrix renames the check, and the ruleset keeps
    requiring the version that no longer runs. Both halves of that are
    reasonable-looking edits on their own.
    """
    produced = set(_pull_request_checks())
    pytest_checks = {name for name in produced if name.startswith("pytest (")}

    assert pytest_checks, f"no pytest check produced at all; got {sorted(produced)}"
    assert "pytest (Python 3.14)" in pytest_checks, (
        "the ruleset requires 'pytest (Python 3.14)' but ci.yml now produces "
        f"{sorted(pytest_checks)}"
    )


def test_the_check_names_are_read_from_the_files_not_assumed() -> None:
    """Guard the reader itself.

    Every assertion above compares two sets, and two empty sets agree. If the
    `on:`-as-True quirk or a workflow layout change made the reader return
    nothing, the drift tests would all pass while measuring nothing.
    """
    checks = _pull_request_checks()

    assert len(checks) >= 6, f"only {sorted(checks)} read from {_WORKFLOWS}"
    assert {"ci.yml", "coverage-gate.yml", "validate.yml"} <= {
        filename for filename, _ in checks.values()
    }
    assert "risk tier and size" not in checks, (
        "labeler.yml is pull_request_target, not pull_request; counting it "
        "would make the labelling job look like a gate"
    )


def test_the_reader_opens_both_workflow_extensions(tmp_path: Path) -> None:
    """A `.yaml` workflow runs, reports checks, and holds a token like any other.

    The guard above is a regression check on the files already present: it
    counts what was read and fails if that shrinks. A file the glob never
    matched contributes nothing to the count either way, so it passes through
    every comparison in this module as though it did not exist.
    """
    document = "on:\n  pull_request:\njobs:\n  build:\n    name: build\n"
    (tmp_path / "one.yml").write_text(document, encoding="utf-8")
    (tmp_path / "two.yaml").write_text(document, encoding="utf-8")
    (tmp_path / "README.md").write_text("not a workflow\n", encoding="utf-8")

    assert [path.name for path in _workflow_paths(tmp_path)] == ["one.yml", "two.yaml"]
