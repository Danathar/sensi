"""The decision logic inside `.github/workflows/release.yml`.

That workflow decides *whether* a release happens, *what number* it carries,
and *what gets written* when it does. Between them its steps read repository
variables, git tags, workflow inputs and the manifest, and a mistake in any of
them is only observable after a tag exists - which, as the workflow's own
comment says, deleting does not un-download from HACS.

None of it is Python, so nothing in this repository's coverage measurement can
see it: `.coveragerc` measures `custom_components/sensi`, and the `run:` bodies
are strings inside YAML either way. These tests close that by extracting each
step's script, substituting the `${{ }}` expressions from an explicit context,
and running the result under `bash` against a throwaway git repository. A step
renamed or an expression added that the context does not know about fails here
rather than silently going untested.

Two properties are asserted about the file as a whole rather than by running
anything, because they are what stop the workflow from being a way around the
rules the repository is governed by:

* no `${{ }}` expression appears inside a `run:` body, so a workflow input can
  never become part of the shell program that a job holding `contents: write`
  executes; and
* no job pushes a branch to `master` - `release` writes a tag and nothing else,
  and `prepare` writes only its own `release/*` branch and a pull request.

The `date` used when a version is derived is stubbed on `PATH` so the derived
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


def _workflow() -> dict:
    """Return the parsed workflow."""

    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _job_steps(job: str = "release") -> list[dict]:
    """Return the steps of one of release.yml's jobs."""

    return _workflow()["jobs"][job]["steps"]


def _step(name: str, job: str = "release") -> dict:
    """Return the step called `name`, failing if it has been renamed away."""

    for step in _job_steps(job):
        if step.get("name") == name:
            return step
    raise AssertionError(
        f"the {job!r} job in release.yml has no step named {name!r}; these "
        "tests cover its decision logic and must be updated with it"
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
    job: str = "release",
) -> StepResult:
    """Run one step's `run:` body in `repo` and collect what it wrote."""

    document = _workflow()
    step = _step(name, job)
    script = _render(step["run"], context)

    output_file = repo / ".github_output"
    summary_file = repo / ".github_step_summary"
    output_file.write_text("", encoding="utf-8")
    summary_file.write_text("", encoding="utf-8")

    path = os.environ["PATH"]
    if extra_path is not None:
        path = f"{extra_path}{os.pathsep}{path}"

    # The `env:` blocks are part of what is under test: they are where the
    # workflow's inputs reach the script at all, and - since the shell
    # injection fix - the *only* way they do. Layered exactly as Actions
    # layers them, so a step reading a workflow-wide value like
    # CALVER_PATTERN sees the one the file actually defines.
    environment = {
        "PATH": path,
        "HOME": str(repo),
        "GITHUB_OUTPUT": str(output_file),
        "GITHUB_STEP_SUMMARY": str(summary_file),
        "GITHUB_REF_NAME": "master",
    }
    for block in (
        document.get("env") or {},
        document["jobs"][job].get("env") or {},
        step.get("env") or {},
    ):
        environment.update(
            {key: _render(str(value), context) for key, value in block.items()}
        )

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
    """Write the component manifest the release steps read."""

    manifest = repo / "custom_components" / "sensi" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"domain": "sensi", "name": "Sensi", "version": version}),
        encoding="utf-8",
    )


def _commit_manifest(repo: Path, version: str) -> None:
    """Set the manifest version and commit it, as a bump pull request would."""

    _write_manifest(repo, version)
    _git(repo, "add", "-A")
    # The fixture already writes a version, so asking for that same one is a
    # no-op rather than an error - the state a test wants is what matters.
    if _git(repo, "status", "--porcelain").strip():
        _git(repo, "commit", "-q", "-m", f"chore(release): {version}")


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
# What the file may not contain at all.
# --------------------------------------------------------------------------


def test_no_run_body_interpolates_a_workflow_expression() -> None:
    """`${{ }}` inside a `run:` is substituted before bash parses the script.

    A free-form input reaching a shell that way is not a value the script
    reads, it is source the script is built from - and both jobs here hold
    `contents: write`. Every expression must arrive through `env:` instead,
    which is a variable assignment bash never re-parses.
    """

    document = _workflow()
    offenders = [
        (job, step.get("name"))
        for job, body in document["jobs"].items()
        for step in body["steps"]
        if "run" in step and _EXPRESSION.search(step["run"])
    ]

    assert offenders == [], (
        "these steps interpolate a workflow expression into shell source; "
        f"pass the value through `env:` and read it as a variable: {offenders}"
    )


def test_every_input_a_step_reads_arrives_through_env() -> None:
    """The version input is the one that matters, so name it explicitly."""

    for job, step_name in (
        ("release", "Decide the version"),
        ("prepare", "Decide the next version"),
    ):
        step = _step(step_name, job)
        assert step["env"]["GIVEN_VERSION"] == "${{ inputs.version }}"
        assert "$GIVEN_VERSION" in step["run"]


def test_the_release_job_writes_a_tag_and_no_branch() -> None:
    """Tagging an approved commit is the whole point; pushing master is not.

    A release that can push a branch needs the ruleset relaxed for it, which
    makes the release pipeline a second route around the review every other
    change goes through.
    """

    pushes = [
        line.strip()
        for step in _job_steps("release")
        for line in step.get("run", "").splitlines()
        if "git push" in line
    ]

    assert pushes == ['git push origin "refs/tags/$VERSION"'], (
        f"the release job must push exactly one ref, a tag; found {pushes}"
    )
    assert not any(
        "git commit" in step.get("run", "") for step in _job_steps("release")
    ), "the release job must not create commits; the manifest is bumped by PR"


def test_the_prepare_job_pushes_only_its_own_release_branch() -> None:
    """`prepare` proposes a change; the ruleset still decides whether it lands."""

    pushes = [
        line.strip()
        for step in _job_steps("prepare")
        for line in step.get("run", "").splitlines()
        if "git push" in line
    ]

    assert pushes == ['git push origin "HEAD:refs/heads/$branch"']
    assert (
        'branch="release/$VERSION"' in _step("Open the pull request", "prepare")["run"]
    )


def test_no_permission_is_granted_workflow_wide() -> None:
    """Each job asks for what it needs, so a later job inherits nothing."""

    document = _workflow()

    assert document["permissions"] == {}
    assert document["jobs"]["release"]["permissions"] == {
        "contents": "write",
        "checks": "read",
    }
    assert document["jobs"]["prepare"]["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }


def test_exactly_one_job_runs_for_any_event() -> None:
    """`prepare` and `release` are alternatives, never both and never neither."""

    document = _workflow()

    assert document["jobs"]["prepare"]["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.prepare"
    )
    assert document["jobs"]["release"]["if"] == (
        "github.event_name != 'workflow_dispatch' || !inputs.prepare"
    )


# --------------------------------------------------------------------------
# How the steps are chained.
# --------------------------------------------------------------------------


def test_every_step_after_the_gate_is_conditioned_on_it() -> None:
    """Nothing downstream of the two gates may run when either says no."""

    steps = {step.get("name"): step for step in _job_steps("release")}

    assert steps["Decide whether to release"]["if"] == (
        "steps.enabled.outputs.go == 'true'"
    )
    for name in (
        "Require green checks on this commit",
        "Decide the version",
        "Validate the version",
    ):
        assert steps[name]["if"] == "steps.decide.outputs.go == 'true'", (
            f"{name!r} must not run when the release was declined"
        )


def test_nothing_is_written_or_published_on_a_dry_run() -> None:
    """The three steps with side effects also require `dry_run` to be off."""

    steps = {step.get("name"): step for step in _job_steps("release")}

    for name in (
        "Tag the approved commit",
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
# "Decide the version" - the release job reads the number, it does not choose
# it.
# --------------------------------------------------------------------------

_VERSION = "Decide the version"


def _decide_version(repo: Path, given: str = "") -> StepResult:
    """Run the release job's version step with `given` as the input."""

    return _run_step(_VERSION, repo, {"inputs.version": given})


def test_the_released_version_is_the_one_the_merged_manifest_carries(
    repo: Path,
) -> None:
    """The number was approved when the bump pull request was merged."""

    _commit_manifest(repo, "2026.9.4")

    result = _decide_version(repo)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.4"


def test_a_version_given_by_hand_confirms_the_manifest(repo: Path) -> None:
    """Typing the number you expect is allowed; it must be the one on master."""

    _commit_manifest(repo, "2026.9.4")

    result = _decide_version(repo, given="2026.9.4")

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.4"


def test_a_version_the_manifest_does_not_carry_is_refused(repo: Path) -> None:
    """Releasing a number master does not have would publish the wrong tree."""

    _commit_manifest(repo, "2026.9.4")

    result = _decide_version(repo, given="2027.1.0")

    assert result.returncode == 1
    assert "the manifest says 2026.9.4" in result.stderr
    assert "prepare" in result.stderr
    assert result.outputs == {}


def test_a_malformed_version_input_is_refused_before_it_is_used(
    repo: Path,
) -> None:
    """The input is validated after the transfer through the environment."""

    result = _decide_version(repo, given="v2026.9.0")

    assert result.returncode == 1
    assert "is not CalVer" in result.stderr


@pytest.mark.parametrize(
    "hostile",
    [
        '2026.9.0"; touch pwned; :"',
        "2026.9.0$(touch pwned)",
        "2026.9.0`touch pwned`",
        "2026.9.0'; touch pwned; '",
        "$(touch pwned)",
    ],
)
def test_shell_syntax_in_the_version_input_is_data_not_program(
    repo: Path, hostile: str
) -> None:
    """The whole point of the `env:` transfer.

    Interpolated into the script this would have run `touch`; read from a
    variable it is only a string that fails the CalVer check. `contents:
    write` on this job is what makes the difference matter.
    """

    result = _decide_version(repo, given=hostile)

    assert result.returncode == 1
    assert "is not CalVer" in result.stderr
    assert not (repo / "pwned").exists(), (
        "the version input reached the shell as program text, not as data"
    )


def test_a_newline_in_the_version_input_cannot_forge_a_step_output(
    repo: Path,
) -> None:
    """$GITHUB_OUTPUT is key=value lines, so an unchecked value writes keys.

    Validating before anything is written is what keeps a crafted input from
    setting, say, `version=` to something the later steps never checked.
    """

    result = _decide_version(repo, given="2026.9.0\nversion=6.6.6")

    assert result.returncode == 1
    assert result.outputs == {}


def test_a_manifest_version_outside_calver_never_reaches_the_output(
    repo: Path,
) -> None:
    """Upstream's 2.1.6 is still in the manifest until the first bump lands."""

    _commit_manifest(repo, "2.1.6")

    result = _decide_version(repo)

    assert result.returncode == 1
    assert "is not CalVer" in result.stderr
    assert result.outputs == {}


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
    """The happy path: newer than the last tag, no tag yet, stable number."""

    _git(repo, "tag", "2026.8.0")
    _commit_manifest(repo, "2026.9.1")

    result = _validate(repo, "2026.9.1")

    assert result.returncode == 0, result.stderr


def test_a_pre_release_validates_when_the_checkbox_agrees(repo: Path) -> None:
    """b1/rc1 is accepted, but only alongside prerelease=true."""

    _git(repo, "tag", "2026.8.0")
    _commit_manifest(repo, "2026.9.0rc1")

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


def test_a_scheduled_run_has_no_inputs_and_still_publishes_a_stable_version(
    repo: Path,
) -> None:
    """Cron triggers carry no `inputs`, so the checkbox arrives as "".

    Compared literally against "false" that mismatch refuses every stable
    version the monthly run was scheduled to publish.
    """

    _commit_manifest(repo, "2026.9.1")

    result = _validate(repo, "2026.9.1", prerelease="")

    assert result.returncode == 0, result.stderr


def test_a_scheduled_run_still_refuses_a_pre_release_number(repo: Path) -> None:
    """Treating the absent checkbox as unticked must not make it permissive."""

    _commit_manifest(repo, "2026.9.1rc1")

    result = _validate(repo, "2026.9.1rc1", prerelease="")

    assert result.returncode == 1
    assert "disagree" in result.stderr


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


def test_an_existing_tag_means_the_manifest_was_never_bumped(repo: Path) -> None:
    """Re-tagging a published version would change what users already have.

    Under the pull-request flow this is also the ordinary "nothing to release"
    case, so the message says which pull request is missing.
    """

    _git(repo, "tag", "2026.9.0")

    result = _validate(repo, "2026.9.0")

    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert "prepare" in result.stderr


def test_a_manifest_that_disagrees_with_the_tag_being_written_is_refused(
    repo: Path,
) -> None:
    """The tag has to name the version the commit under it actually declares."""

    _commit_manifest(repo, "2026.9.1")

    result = _validate(repo, "2026.9.2")

    assert result.returncode == 1
    assert "the manifest says 2026.9.1" in result.stderr


def test_a_version_older_than_the_newest_tag_is_refused(repo: Path) -> None:
    """A backwards number strands users on a release HACS thinks is newer."""

    _git(repo, "tag", "2026.9.5")
    _commit_manifest(repo, "2026.9.2")

    result = _validate(repo, "2026.9.2")

    assert result.returncode == 1
    assert "not newer" in result.stderr


def test_the_ordering_check_is_numeric_not_lexical(repo: Path) -> None:
    """2026.10.0 is newer than 2026.9.0 even though it sorts before it."""

    _git(repo, "tag", "2026.9.0")
    _commit_manifest(repo, "2026.10.0")

    result = _validate(repo, "2026.10.0")

    assert result.returncode == 0, result.stderr


def test_a_pre_release_tag_is_not_the_bar_a_stable_release_must_clear(
    repo: Path,
) -> None:
    """A beta from a later month must not block this month's stable release."""

    _git(repo, "tag", "2026.9.0")
    _git(repo, "tag", "2026.10.0rc1")
    _commit_manifest(repo, "2026.9.1")

    result = _validate(repo, "2026.9.1")

    assert result.returncode == 0, result.stderr


def test_validation_reports_the_numbers_it_compared(repo: Path) -> None:
    """The step echoes both so a refusal is explicable from the log alone."""

    _commit_manifest(repo, "2026.9.0")

    result = _validate(repo, "2026.9.0")

    assert result.returncode == 0, result.stderr
    assert "manifest: 2026.9.0" in result.stdout
    assert "newest stable tag: none" in result.stdout


# --------------------------------------------------------------------------
# "Decide the next version" - the prepare job, which proposes the number.
# --------------------------------------------------------------------------

_NEXT = "Decide the next version"


def _next_version(repo: Path, fixed_date: Path, given: str = "") -> StepResult:
    """Run the prepare job's version step with `given` as the input."""

    return _run_step(
        _NEXT,
        repo,
        {"inputs.version": given},
        extra_path=fixed_date,
        job="prepare",
    )


def test_a_version_given_by_hand_is_proposed_verbatim(
    repo: Path, fixed_date: Path
) -> None:
    """An off-cycle release uses the number the operator typed."""

    result = _next_version(repo, fixed_date, given="2027.1.4")

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2027.1.4"


def test_the_first_release_of_a_month_is_patch_zero(
    repo: Path, fixed_date: Path
) -> None:
    """With nothing tagged this month the version is YYYY.M.0."""

    result = _next_version(repo, fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.0"


def test_a_later_release_in_the_same_month_increments_the_patch(
    repo: Path, fixed_date: Path
) -> None:
    """A second release this month is the next patch, not a new month."""

    _git(repo, "tag", "2026.9.0")

    result = _next_version(repo, fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.1"


def test_the_patch_number_is_compared_numerically_not_as_text(
    repo: Path, fixed_date: Path
) -> None:
    """After 2026.9.9 comes 2026.9.10; a lexical sort would say 2026.9.10."""

    _git(repo, "tag", "2026.9.9")
    _git(repo, "tag", "2026.9.10")

    result = _next_version(repo, fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.11"


def test_a_pre_release_tag_does_not_consume_a_patch_number(
    repo: Path, fixed_date: Path
) -> None:
    """2026.9.1rc1 is filtered out, so the next stable is still 2026.9.1."""

    _git(repo, "tag", "2026.9.0")
    _git(repo, "tag", "2026.9.1rc1")

    result = _next_version(repo, fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.1"


def test_tags_from_other_months_do_not_affect_this_months_number(
    repo: Path, fixed_date: Path
) -> None:
    """Last month's 2026.8.5 must not make this month start at .6."""

    _git(repo, "tag", "2026.8.5")

    result = _next_version(repo, fixed_date)

    assert result.returncode == 0, result.stderr
    assert result.outputs["version"] == "2026.9.0"


def test_preparing_a_version_that_is_already_tagged_is_refused(
    repo: Path, fixed_date: Path
) -> None:
    """A bump pull request for a published number could only re-release it."""

    _git(repo, "tag", "2027.1.4")

    result = _next_version(repo, fixed_date, given="2027.1.4")

    assert result.returncode == 1
    assert "already exists" in result.stderr


@pytest.mark.parametrize(
    "hostile",
    [
        '2026.9.0"; touch pwned; :"',
        "2026.9.0$(touch pwned)",
        "2026.9.0`touch pwned`",
    ],
)
def test_shell_syntax_in_the_prepared_version_is_data_too(
    repo: Path, fixed_date: Path, hostile: str
) -> None:
    """`prepare` holds contents: write and pull-requests: write as well."""

    result = _next_version(repo, fixed_date, given=hostile)

    assert result.returncode == 1
    assert "is not CalVer" in result.stderr
    assert not (repo / "pwned").exists()


# --------------------------------------------------------------------------
# "Bump the manifest" - the prepare job's write, which lands only via a PR.
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("jq") is None, reason="the step under test shells out to jq"
)
def test_the_manifest_is_rewritten_to_the_proposed_version(repo: Path) -> None:
    """Rewrite only the version and leave the rest of the file intact."""

    _commit_manifest(repo, "2.1.6")

    result = _run_step(
        "Bump the manifest",
        repo,
        {"steps.version.outputs.version": "2026.9.0"},
        job="prepare",
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
