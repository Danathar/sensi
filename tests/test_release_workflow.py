"""The decision logic inside `.github/workflows/release.yml`.

Four of that workflow's steps decide *whether* a release happens and *what
number* it carries: "Check the schedule is enabled", "Decide whether to
release", "Decide the version" and "Validate the version". Between them they
read repository variables, git tags, workflow inputs and the manifest, and a
mistake in any of them is only observable after a tag exists - which, as the
workflow's own comment says, deleting does not un-download from HACS.

None of it is Python, so nothing in this repository's coverage measurement can
see it: `.coveragerc` measures `custom_components/sensi`, and the `run:` bodies
are strings inside YAML either way. These tests close that by extracting each
step's script, substituting the `${{ }}` expressions from an explicit context,
and running the result under `bash` against a throwaway git repository. A step
renamed or an expression added that the context does not know about fails here
rather than silently going untested.

The `date` used by "Decide the version" is stubbed on `PATH` so the derived
month is a fixed 2026.9 instead of whatever day the suite runs on.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "release.yml"

# The month the stubbed `date` reports, so a derived version is the same in
# September as it is in any other month.
_STUB_YEAR = "2026"
_STUB_MONTH = "9"

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")


def _job_steps() -> list[dict]:
    """Return the steps of release.yml's only job."""

    document = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]["release"]["steps"]


def _step(name: str) -> dict:
    """Return the step called `name`, failing if it has been renamed away."""

    for step in _job_steps():
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"release.yml has no step named {name!r}; these tests cover its "
        "decision logic and must be updated with it"
    )


def _render(text: str, context: dict[str, str]) -> str:
    """Substitute `${{ ... }}` expressions in `text` from `context`.

    An expression the test did not supply is an error rather than an empty
    string: a new input silently rendering as "" is exactly the kind of
    untested branch this file exists to prevent.
    """

    def replace(match: re.Match[str]) -> str:
        expression = match.group(1).strip()
        if expression not in context:
            raise AssertionError(
                f"release.yml uses ${{{{ {expression} }}}}, which no test "
                "supplies a value for"
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
    repo: Path,
    context: dict[str, str],
    extra_path: Path | None = None,
) -> StepResult:
    """Run one step's `run:` body in `repo` and collect what it wrote."""

    step = _step(name)
    script = _render(step["run"], context)

    output_file = repo / ".github_output"
    summary_file = repo / ".github_step_summary"
    output_file.write_text("", encoding="utf-8")
    summary_file.write_text("", encoding="utf-8")

    path = os.environ["PATH"]
    if extra_path is not None:
        path = f"{extra_path}{os.pathsep}{path}"

    environment = {
        "PATH": path,
        "HOME": str(repo),
        "GITHUB_OUTPUT": str(output_file),
        "GITHUB_STEP_SUMMARY": str(summary_file),
        "GITHUB_REF_NAME": "master",
        # The workflow's own `env:` block is part of what is under test: it is
        # where `vars.AUTO_RELEASE_ENABLED` and the version output reach the
        # script at all.
        **{
            key: _render(str(value), context)
            for key, value in (step.get("env") or {}).items()
        },
    }

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


def _write_manifest(repo: Path, version: str) -> None:
    """Write the component manifest the release steps read and rewrite."""

    manifest = repo / "custom_components" / "sensi" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"domain": "sensi", "name": "Sensi", "version": version}),
        encoding="utf-8",
    )


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    """Build a throwaway git repository shaped like this one, with no tags."""

    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _write_manifest(repo, "2026.9.0")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


@pytest.fixture(name="fixed_date")
def fixed_date_fixture(tmp_path: Path) -> Path:
    """Provide a `date` on PATH that always reports the same year and month."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Resolved now, while the stub is not yet on PATH, so the fallback cannot
    # re-enter the stub.
    real_date = shutil.which("date") or "/bin/date"
    stub = bin_dir / "date"
    stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  "-u +%Y") echo {_STUB_YEAR} ;;\n'
        f'  "-u +%-m") echo {_STUB_MONTH} ;;\n'
        f'  *) exec {real_date} "$@" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir


def _commit(repo: Path, path: str, body: str, message: str) -> None:
    """Commit `body` at `path`, creating parent directories as needed."""

    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


# --------------------------------------------------------------------------
# How the four steps are chained.
# --------------------------------------------------------------------------


def test_every_step_after_the_gate_is_conditioned_on_it() -> None:
    """Nothing downstream of the two gates may run when either says no."""

    steps = {step.get("name"): step for step in _job_steps()}

    assert steps["Decide whether to release"]["if"] == (
        "steps.enabled.outputs.go == 'true'"
    )
    for name in (
        "Require green checks on this commit",
        "Decide the version",
        "Validate the version",
        "Set the manifest version",
    ):
        assert steps[name]["if"] == "steps.decide.outputs.go == 'true'", (
            f"{name!r} must not run when the release was declined"
        )


def test_nothing_is_written_or_published_on_a_dry_run() -> None:
    """The three steps with side effects also require `dry_run` to be off."""

    steps = {step.get("name"): step for step in _job_steps()}

    for name in (
        "Commit and tag",
        "Build the manual-install ZIP",
        "Publish the release",
    ):
        assert steps[name]["if"] == (
            "steps.decide.outputs.go == 'true' && !inputs.dry_run"
        ), f"{name!r} would run during a dry run"


# --------------------------------------------------------------------------
# "Check the schedule is enabled"
# --------------------------------------------------------------------------

_ENABLED = "Check the schedule is enabled"


def test_a_manual_run_proceeds_even_though_monthly_releases_are_off(
    repo: Path,
) -> None:
    """workflow_dispatch never consults the opt-in - a person is in the loop."""

    result = _run_step(
        _ENABLED,
        repo,
        {
            "github.event_name": "workflow_dispatch",
            "vars.AUTO_RELEASE_ENABLED == 'true'": "false",
        },
    )

    assert result.returncode == 0
    assert result.outputs["go"] == "true"


def test_a_scheduled_run_proceeds_once_the_variable_is_set(repo: Path) -> None:
    """The monthly path runs only after someone opts the repository in."""

    result = _run_step(
        _ENABLED,
        repo,
        {
            "github.event_name": "schedule",
            "vars.AUTO_RELEASE_ENABLED == 'true'": "true",
        },
    )

    assert result.returncode == 0
    assert result.outputs["go"] == "true"
    assert result.summary == ""


def test_a_scheduled_run_stops_and_says_how_to_enable_it(repo: Path) -> None:
    """Without the opt-in the schedule declines and names the variable."""

    result = _run_step(
        _ENABLED,
        repo,
        {
            "github.event_name": "schedule",
            "vars.AUTO_RELEASE_ENABLED == 'true'": "false",
        },
    )

    assert result.returncode == 0
    assert result.outputs["go"] == "false"
    assert "AUTO_RELEASE_ENABLED" in result.summary


# --------------------------------------------------------------------------
# "Decide whether to release"
# --------------------------------------------------------------------------

_DECIDE = "Decide whether to release"


def test_the_first_release_is_allowed_on_a_repository_with_no_tags(
    repo: Path,
) -> None:
    """The `|| true` path: no tags at all must not fail under pipefail."""

    result = _run_step(_DECIDE, repo, {"inputs.force": ""})

    assert result.returncode == 0, result.stderr
    assert result.outputs["go"] == "true"
    assert "first" in result.stdout


def test_only_pre_release_tags_still_counts_as_the_first_release(
    repo: Path,
) -> None:
    """A b1/rc1 tag is filtered out, so the first stable release still goes."""

    _git(repo, "tag", "2026.9.0b1")

    result = _run_step(_DECIDE, repo, {"inputs.force": ""})

    assert result.returncode == 0, result.stderr
    assert result.outputs["go"] == "true"


def test_a_component_change_since_the_last_tag_earns_a_release(
    repo: Path,
) -> None:
    """Something under custom_components/sensi changed, so users get a build."""

    _git(repo, "tag", "2026.8.0")
    _commit(repo, "custom_components/sensi/switch.py", "x = 1\n", "feat: switch")

    result = _run_step(_DECIDE, repo, {"inputs.force": ""})

    assert result.returncode == 0, result.stderr
    assert result.outputs["go"] == "true"


def test_a_month_of_only_ci_and_docs_does_not_get_a_release(repo: Path) -> None:
    """The common case: nothing installable changed, so nothing is published."""

    _git(repo, "tag", "2026.8.0")
    _commit(repo, "docs/notes.md", "notes\n", "docs: notes")
    _commit(repo, ".github/workflows/ci.yml", "name: CI\n", "ci: tweak")

    result = _run_step(_DECIDE, repo, {"inputs.force": ""})

    assert result.returncode == 0, result.stderr
    assert result.outputs["go"] == "false"
    assert "No release" in result.summary
    assert "2026.8.0" in result.summary


def test_force_releases_a_month_that_changed_nothing_installable(
    repo: Path,
) -> None:
    """The manual override still publishes when a person asks for it."""

    _git(repo, "tag", "2026.8.0")
    _commit(repo, "docs/notes.md", "notes\n", "docs: notes")

    result = _run_step(_DECIDE, repo, {"inputs.force": "true"})

    assert result.returncode == 0, result.stderr
    assert result.outputs["go"] == "true"


def test_the_comparison_uses_the_newest_tag_not_the_first_one(
    repo: Path,
) -> None:
    """`sort -V | tail -1` picks 2026.9.10 over 2026.9.9, not the reverse."""

    _commit(repo, "custom_components/sensi/switch.py", "x = 1\n", "feat: switch")
    _git(repo, "tag", "2026.9.9")
    _git(repo, "tag", "2026.9.10")
    _commit(repo, "docs/notes.md", "notes\n", "docs: notes")

    result = _run_step(_DECIDE, repo, {"inputs.force": ""})

    assert result.returncode == 0, result.stderr
    assert result.outputs["go"] == "false"
    assert "2026.9.10" in result.summary


# --------------------------------------------------------------------------
# "Decide the version"
# --------------------------------------------------------------------------

_VERSION = "Decide the version"


def test_a_version_given_by_hand_is_used_verbatim(repo: Path, fixed_date: Path) -> None:
    """An off-cycle release uses the number the operator typed."""

    result = _run_step(
        _VERSION, repo, {"inputs.version": "2027.1.4"}, extra_path=fixed_date
    )

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2027.1.4"


def test_the_first_release_of_a_month_is_patch_zero(
    repo: Path, fixed_date: Path
) -> None:
    """With nothing tagged this month the version is YYYY.M.0."""

    result = _run_step(_VERSION, repo, {"inputs.version": ""}, extra_path=fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.0"


def test_a_later_release_in_the_same_month_increments_the_patch(
    repo: Path, fixed_date: Path
) -> None:
    """A second release this month is the next patch, not a new month."""

    _git(repo, "tag", "2026.9.0")

    result = _run_step(_VERSION, repo, {"inputs.version": ""}, extra_path=fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.1"


def test_the_patch_number_is_compared_numerically_not_as_text(
    repo: Path, fixed_date: Path
) -> None:
    """After 2026.9.9 comes 2026.9.10; a lexical sort would say 2026.9.10."""

    _git(repo, "tag", "2026.9.9")
    _git(repo, "tag", "2026.9.10")

    result = _run_step(_VERSION, repo, {"inputs.version": ""}, extra_path=fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.11"


def test_a_pre_release_tag_does_not_consume_a_patch_number(
    repo: Path, fixed_date: Path
) -> None:
    """2026.9.1rc1 is filtered out, so the next stable is still 2026.9.1."""

    _git(repo, "tag", "2026.9.0")
    _git(repo, "tag", "2026.9.1rc1")

    result = _run_step(_VERSION, repo, {"inputs.version": ""}, extra_path=fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.1"


def test_tags_from_other_months_do_not_affect_this_months_number(
    repo: Path, fixed_date: Path
) -> None:
    """Last month's 2026.8.5 must not make this month start at .6."""

    _git(repo, "tag", "2026.8.5")

    result = _run_step(_VERSION, repo, {"inputs.version": ""}, extra_path=fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.0"


# --------------------------------------------------------------------------
# "Validate the version"
# --------------------------------------------------------------------------

_VALIDATE = "Validate the version"


def _validate(repo: Path, version: str, prerelease: str = "false") -> StepResult:
    """Run the validation step for `version` at the given prerelease setting."""

    return _run_step(
        _VALIDATE,
        repo,
        {
            "steps.version.outputs.version": version,
            "inputs.prerelease": prerelease,
        },
    )


def test_a_well_formed_calver_version_validates(repo: Path) -> None:
    """The happy path: newer than the manifest, no tag yet, stable number."""

    _git(repo, "tag", "2026.8.0")

    result = _validate(repo, "2026.9.1")

    assert result.returncode == 0, result.stderr


def test_a_pre_release_validates_when_the_checkbox_agrees(repo: Path) -> None:
    """b1/rc1 is accepted, but only alongside prerelease=true."""

    _git(repo, "tag", "2026.8.0")

    result = _validate(repo, "2026.9.0rc1", prerelease="true")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "version",
    [
        "2026.09.0",  # zero-padded month
        "v2026.9.0",  # the upstream tag style this fork does not use
        "2026.13.0",  # month out of range
        "2026.0.0",  # month zero
        "2026.9",  # no patch
        "2026.9.0-rc1",  # not the b1/rc1 spelling
        "2.1.7",  # upstream's semver line
        "2026.9.01",  # zero-padded patch
    ],
)
def test_a_version_that_is_not_calver_is_refused(repo: Path, version: str) -> None:
    """Anything outside YYYY.M.PATCH stops before a tag can be written."""

    result = _validate(repo, version)

    assert result.returncode == 1
    assert "is not CalVer" in result.stderr


def test_a_stable_number_with_the_prerelease_box_ticked_is_refused(
    repo: Path,
) -> None:
    """A beta that HACS would offer to everyone is caught here."""

    result = _validate(repo, "2026.9.0", prerelease="true")

    assert result.returncode == 1
    assert "disagree" in result.stderr


def test_a_pre_release_number_without_the_box_is_refused(repo: Path) -> None:
    """A stable release tagged 2026.9.0b1 is caught here."""

    result = _validate(repo, "2026.9.0b1")

    assert result.returncode == 1
    assert "disagree" in result.stderr


def test_an_existing_tag_is_never_reused(repo: Path) -> None:
    """Re-tagging a published version would change what users already have."""

    _git(repo, "tag", "2026.9.1")

    result = _validate(repo, "2026.9.1")

    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_a_version_older_than_the_newest_tag_is_refused(repo: Path) -> None:
    """A backwards number strands users on a release HACS thinks is newer."""

    _git(repo, "tag", "2026.9.5")

    result = _validate(repo, "2026.9.2")

    assert result.returncode == 1
    assert "not newer" in result.stderr


def test_the_ordering_check_is_numeric_not_lexical(repo: Path) -> None:
    """2026.10.0 is newer than 2026.9.0 even though it sorts before it."""

    _git(repo, "tag", "2026.9.0")

    result = _validate(repo, "2026.10.0")

    assert result.returncode == 0, result.stderr


def test_a_pre_release_tag_is_not_the_bar_a_stable_release_must_clear(
    repo: Path,
) -> None:
    """A beta from a later month must not block this month's stable release."""

    _git(repo, "tag", "2026.9.0")
    _git(repo, "tag", "2026.10.0rc1")

    result = _validate(repo, "2026.9.1")

    assert result.returncode == 0, result.stderr


def test_validation_reports_the_manifest_version_it_read(repo: Path) -> None:
    """The step echoes the manifest so a stale one is visible in the log."""

    _write_manifest(repo, "2.1.6")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore: manifest")

    result = _validate(repo, "2026.9.0")

    assert result.returncode == 0, result.stderr
    assert "manifest: 2.1.6" in result.stdout
    assert "newest stable tag: none" in result.stdout


# --------------------------------------------------------------------------
# "Set the manifest version"
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("jq") is None, reason="the step under test shells out to jq"
)
def test_the_manifest_is_rewritten_to_the_released_version(repo: Path) -> None:
    """Rewrite only the version and leave the rest of the file intact."""

    _write_manifest(repo, "2.1.6")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore: manifest")

    result = _run_step(
        "Set the manifest version",
        repo,
        {"steps.version.outputs.version": "2026.9.0"},
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(
        (repo / "custom_components" / "sensi" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["version"] == "2026.9.0"
    assert manifest["domain"] == "sensi"
    assert not (repo / "custom_components" / "sensi" / "manifest.json.tmp").exists()
