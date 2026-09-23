"""The prose behind the `master` ruleset, `docs/branch-protection.md`.

The page explains `.github/rulesets/master.json`: which rules it applies, which
checks it requires and which it deliberately does not, why review is set where
it is, what had to change in `release.yml` before it could be applied, and how
to check it and apply it elsewhere. Until this module existed nothing read it as
a subject. `tests/test_instruction_docs.py` looks for the six required check
names in it, `tests/test_codeowners.py` looks for two paths and the
`require_code_owner_review` sentence, and `tests/test_check_ruleset.py` looks
for the page's name in the script's output. Nothing else on the page was joined
to anything.

The page was written on 2026-09-07 as a plan, and the plan was carried out the
next day - #116 merged at 00:01 UTC on 2026-09-08 and the ruleset was created
eighteen minutes later - but the page still read as the plan. It opened by
calling `master` **unprotected** "at the time of writing", told the reader
"Do not apply this ruleset while `release.yml` still pushes to `master`",
quoted a `git push origin HEAD:"${GITHUB_REF_NAME}"` that `release.yml` no
longer contains, said "Merge that first" of a pull request that had merged, and
said the release workflow's "only write is a tag push" when its `prepare` job
pushes a `release/*` branch. Its step 2 (`gh api --method POST ...`) would
now add a second ruleset beside the live one. The same change rewrites those
passages in the past tense and moves the `POST` to a placeholder repository.

Nothing offline can read what GitHub enforces - that is `check_ruleset.py`'s
job - so the present-tense sentences are guarded by what they must not say
(`test_the_page_does_not_describe_the_ruleset_as_unapplied`) and every other
claim is joined to the file it describes: the rule table and the review numbers
to `master.json`, the not-required checks to the workflow jobs a pull request
reaches, the release paragraph to every `git push` in `release.yml`, and the
commands to `scripts/check_ruleset.py`'s `main()`.
"""

import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
from unittest.mock import patch

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "branch-protection.md"
_DEFINITION = _ROOT / ".github" / "rulesets" / "master.json"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_RELEASE = _WORKFLOWS / "release.yml"
_CODEOWNERS = _ROOT / ".github" / "CODEOWNERS"

_SCRIPT = _ROOT / "scripts" / "check_ruleset.py"
_spec = importlib.util.spec_from_file_location("check_ruleset", _SCRIPT)
check_ruleset = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_ruleset)

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}

# The triggers a pull request fires. `pull_request_target` is here because the
# page's "appear on a pull request" is about what a reviewer sees in the checks
# list, and the labelling jobs appear there even though they run against the
# base repository.
_PULL_REQUEST_EVENTS = ("pull_request", "pull_request_target")


def _text() -> str:
    return _DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse whitespace; Markdown wraps a phrase across lines anywhere."""
    return " ".join(text.split())


def _section(heading: str) -> str:
    """Return the `## `/`### ` section titled `heading`, up to the next one."""
    lines = _text().splitlines()
    starts = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(r"#{2,3} " + re.escape(heading), line)
    ]
    assert len(starts) == 1, (
        f"docs/branch-protection.md has {len(starts)} {heading!r} sections"
    )
    body = []
    for line in lines[starts[0] + 1 :]:
        if re.match(r"#{2,3} ", line):
            break
        body.append(line)
    return "\n".join(body)


def _fences(language: str) -> list[str]:
    return re.findall(r"^```" + language + r"\n(.*?)^```$", _text(), re.M | re.S)


def _definition() -> dict:
    return json.loads(_DEFINITION.read_text(encoding="utf-8"))


def _rule(rule_type: str) -> dict:
    rules = [rule for rule in _definition()["rules"] if rule["type"] == rule_type]
    assert len(rules) == 1, f"master.json has {len(rules)} {rule_type!r} rules"
    return rules[0]


def _required() -> set[str]:
    return {
        check["context"]
        for check in _rule("required_status_checks")["parameters"][
            "required_status_checks"
        ]
    }


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    """Return the `on:` block; PyYAML reads a bare `on` key as True."""
    triggers = workflow.get("on", workflow.get(True)) or {}
    if isinstance(triggers, str):
        return {triggers: None}
    if isinstance(triggers, list):
        return dict.fromkeys(triggers)
    return triggers


def _check_names(job_id: str, job: dict) -> list[str]:
    """Return the status-check names a job reports, one per matrix value."""
    names = [job.get("name", job_id)]
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    for key, values in matrix.items():
        token = "${{ matrix." + key + " }}"
        if isinstance(values, list) and any(token in name for name in names):
            names = [
                name.replace(token, str(value)) for name in names for value in values
            ]
    return names


def _pull_request_jobs() -> dict[str, tuple[str, dict]]:
    """Every check name a pull request shows, mapped to (workflow file, job)."""
    jobs: dict[str, tuple[str, dict]] = {}
    for path in sorted(_WORKFLOWS.glob("*.y*ml")):
        workflow = _workflow(path)
        if not any(event in _triggers(workflow) for event in _PULL_REQUEST_EVENTS):
            continue
        for job_id, job in (workflow.get("jobs") or {}).items():
            for name in _check_names(job_id, job):
                jobs[name] = (path.name, job)
    return jobs


def _tracked() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()


# --- the rule table ----------------------------------------------------------


def _rule_table() -> dict[str, str]:
    rows = re.findall(
        r"^\| `([a-z_]+)` \| (.+?) \|$", _section("What the ruleset does"), re.M
    )
    assert rows, "the rule table under 'What the ruleset does' is gone"
    return dict(rows)


def test_the_rule_table_names_every_rule_the_definition_applies() -> None:
    """Claim: the four table rows are the ruleset, in both directions."""
    documented = set(_rule_table())
    applied = {rule["type"] for rule in _definition()["rules"]}
    assert documented == applied, (
        f"table only: {sorted(documented - applied)}, "
        f"master.json only: {sorted(applied - documented)}"
    )


def test_the_status_check_row_counts_the_required_checks() -> None:
    """Claim: "`required_status_checks` | Six checks must pass before merge"."""
    effect = _rule_table()["required_status_checks"]
    word = re.match(r"(\w+) checks must pass", effect)
    assert word, f"the required_status_checks row no longer states a count: {effect!r}"
    assert _NUMBER_WORDS[word[1].lower()] == len(_required())


def test_the_heading_and_the_prose_list_count_the_same_checks() -> None:
    """Claim: "### The six required checks" and "All six run unconditionally"."""
    heading = re.search(r"^### The (\w+) required checks$", _text(), re.M)
    assert heading, "the required-checks heading is gone"
    body = _flat(_section(f"The {heading[1]} required checks"))
    assert _NUMBER_WORDS[heading[1]] == len(_required())
    all_n = re.search(r"All (\w+) run unconditionally", body)
    assert all_n and all_n[1] == heading[1], all_n
    listed = set(re.findall(r"`([^`]+)`", body.split("All ")[0]))
    assert listed == _required(), (
        f"listed only: {sorted(listed - _required())}, "
        f"required only: {sorted(_required() - listed)}"
    )


def test_the_condition_named_is_the_one_the_definition_uses() -> None:
    """Claim: "Conditions target `~DEFAULT_BRANCH` rather than the literal name"."""
    stated = re.search(r"Conditions target `([^`]+)` rather than", _flat(_text()))
    assert stated, "the ref condition sentence is gone"
    ref_name = _definition()["conditions"]["ref_name"]
    assert ref_name["include"] == [stated[1]]
    assert ref_name["exclude"] == []
    assert "master" not in json.dumps(_definition()["conditions"])


def test_the_ruleset_targets_branches_as_the_page_says() -> None:
    """Claim: "This ruleset targets branches, and only the default one"."""
    assert "This ruleset targets branches, and only the default one" in _flat(_text())
    assert _definition()["target"] == "branch"
    assert _definition()["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]


# --- bypass actors -----------------------------------------------------------


def test_bypass_actors_is_empty_as_stated() -> None:
    """Claim: "**`bypass_actors` is empty.**"."""
    assert "**`bypass_actors` is empty.**" in _text()
    assert _definition()["bypass_actors"] == []


def test_an_added_bypass_actor_fails_the_check_as_stated() -> None:
    """Claim: "`check_ruleset.py` treats an added bypass actor as drift and fails on it"."""
    assert (
        "`check_ruleset.py` treats an added bypass actor as drift and fails on it"
        in _flat(_text())
    )
    live = _definition()
    live["bypass_actors"] = [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
    ]
    with patch.object(check_ruleset, "fetch_live", return_value=live):
        assert check_ruleset.main([]) == 1
    assert any(
        "bypass_actors" in problem
        for problem in check_ruleset.compare(_definition(), live)
    )


# --- the checks that are deliberately not required ---------------------------


def _not_required_bullets() -> tuple[int, list[str]]:
    body = _flat(_section("The six required checks"))
    count = re.search(
        r"(\w+) that also appear on a pull request are deliberately \*\*not\*\* required",
        body,
    )
    assert count, "the not-required sentence is gone"
    section = _section("The six required checks")
    names = []
    for bullet in re.findall(r"^- (.+(?:\n  .+)*)", section, re.M):
        names += re.findall(r"\*\*`([^`]+)`\*\*", bullet)
    return _NUMBER_WORDS[count[1].lower()], names


def test_the_not_required_list_is_every_pull_request_job_the_ruleset_skips() -> None:
    """Claim: "Three that also appear on a pull request are deliberately **not** required".

    Computed from the workflows: every check a pull request shows, minus the
    ones `master.json` requires. A job added to a pull-request workflow and not
    required has to be argued for on this page, or required.
    """
    count, named = _not_required_bullets()
    shown = set(_pull_request_jobs())
    skipped = shown - _required()
    assert set(named) == skipped, (
        f"page only: {sorted(set(named) - skipped)}, "
        f"workflows only: {sorted(skipped - set(named))}"
    )
    assert len(named) == len(set(named)) == count


def test_the_badge_job_is_guarded_to_a_push_to_master() -> None:
    """Claim: "`publish badge and trend` runs only on a push to `master` (`coverage-gate.yml` guards it with an `if:`)"."""
    body = _flat(_section("The six required checks"))
    stated = re.search(
        r"\*\*`([^`]+)`\*\* runs only on a push to `master` \(`([^`]+)` guards it with an `if:`\)",
        body,
    )
    assert stated, "the badge job sentence is gone"
    filename, job = _pull_request_jobs()[stated[1]]
    assert filename == stated[2]
    guard = job.get("if", "")
    assert "github.event_name == 'push'" in guard, guard
    assert "github.ref == 'refs/heads/master'" in guard, guard


def test_the_labelling_jobs_are_the_labelling_workflow() -> None:
    """Claim: "**`path labels`** and **`risk tier and size`** classify a change"."""
    stated = re.search(
        r"\*\*`([^`]+)`\*\* and \*\*`([^`]+)`\*\* classify a change",
        _flat(_section("The six required checks")),
    )
    assert stated, "the labelling sentence is gone"
    jobs = _pull_request_jobs()
    for name in stated.groups():
        filename, _ = jobs[name]
        assert filename == "labeler.yml", f"{name!r} runs from {filename}"


# --- review requirements ------------------------------------------------------


def test_the_review_count_is_the_one_the_definition_sets() -> None:
    """Claim: "`required_approving_review_count` is **0**"."""
    stated = re.findall(
        r"`required_approving_review_count` is \*\*(\d+)\*\*", _flat(_text())
    )
    assert len(stated) == 1, stated
    params = _rule("pull_request")["parameters"]
    assert params["required_approving_review_count"] == int(stated[0])


def test_the_second_reviewer_edit_tightens_both_fields_and_nothing_else() -> None:
    """Claim: "two fields make it real, and they are the only edit needed".

    The fence must name parameters the `pull_request` rule actually carries -
    a misspelt key would be applied by GitHub as a no-op or rejected - and each
    value must be stricter than the committed one, or the edit changes nothing.
    """
    fences = _fences("json")
    assert len(fences) == 1, f"{len(fences)} json fences"
    edit = json.loads("{" + fences[0].strip() + "}")
    params = _rule("pull_request")["parameters"]
    assert len(edit) == 2
    for key, value in edit.items():
        assert key in params, f"{key!r} is not a pull_request parameter"
        assert type(value) is type(params[key]), key
        assert value > params[key], f"{key}: {value!r} does not tighten {params[key]!r}"


def test_the_control_plane_paths_named_have_their_own_owner() -> None:
    """Claim: "`.github/CODEOWNERS` ... already lists the control-plane paths".

    The sentence lists them as "workflows, the ruleset, the AI policy,
    `AGENTS.md`, `custom_components/sensi/auth.py`"; the three prose names are
    the workflows directory, the directory `master.json` is in, and
    `docs/SECURITY-AI.md`.
    """
    sentence = _flat(_text())
    assert (
        "already lists the control-plane paths — workflows, the ruleset, the AI "
        "policy, `AGENTS.md`, `custom_components/sensi/auth.py`" in sentence
    )
    patterns = {
        line.split()[0]
        for line in _CODEOWNERS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    named = {
        "/" + _WORKFLOWS.relative_to(_ROOT).as_posix() + "/",
        "/" + _DEFINITION.parent.relative_to(_ROOT).as_posix() + "/",
        "/docs/SECURITY-AI.md",
        "/AGENTS.md",
        "/custom_components/sensi/auth.py",
    }
    assert named <= patterns, (
        f"not owned by a rule of their own: {sorted(named - patterns)}"
    )


# --- the release workflow -----------------------------------------------------


def _pushes() -> dict[str, list[str]]:
    """Every `git push` in `release.yml`, keyed by the job that runs it."""
    workflow = _workflow(_RELEASE)
    pushes: dict[str, list[str]] = {}
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            for line in (step.get("run") or "").splitlines():
                if line.strip().startswith("git push"):
                    pushes.setdefault(job_id, []).append(line.strip())
    return pushes


def _branch_assignment() -> str:
    body = "\n".join(
        step.get("run") or ""
        for job in _workflow(_RELEASE)["jobs"].values()
        for step in job.get("steps", [])
    )
    assigned = re.findall(r'^\s*branch="([^"]+)"$', body, re.M)
    assert len(assigned) == 1, assigned
    return assigned[0]


def test_release_yml_pushes_exactly_what_the_page_says() -> None:
    """Claim: "the `prepare` job pushes a `release/<version>` branch ... and the `release` job pushes a tag".

    Also the negative the whole ruleset depends on: nothing in `release.yml`
    pushes to the ref the run started from, which on a schedule is `master`.
    """
    body = _flat(_section("The release workflow, and why it had to change first"))
    assert (
        "`release.yml` now pushes two things, and neither is `master`: the "
        "`prepare` job pushes a `release/<version>` branch and opens the "
        "version-bump pull request from it, and the `release` job pushes a tag."
    ) in body
    pushes = _pushes()
    assert set(pushes) == {"prepare", "release"}, pushes
    assert pushes["prepare"] == ['git push origin "HEAD:refs/heads/$branch"'], pushes
    assert _branch_assignment().startswith("release/")
    assert len(pushes["release"]) == 1 and "refs/tags/" in pushes["release"][0], pushes
    for line in sum(pushes.values(), []):
        assert "GITHUB_REF_NAME" not in line and "master" not in line, line


def test_the_prepare_job_opens_its_pull_request_from_that_branch() -> None:
    """Claim: "opens the version-bump pull request from it"."""
    steps = _workflow(_RELEASE)["jobs"]["prepare"]["steps"]
    body = "\n".join(step.get("run") or "" for step in steps)
    create = re.search(r"gh pr create(.*?)(?:\n\s*\n|\Z)", body, re.S)
    assert create, "the prepare job no longer runs gh pr create"
    assert '--head "$branch"' in create[1]
    assert '--base "$GITHUB_REF_NAME"' in create[1]


def test_the_scheduled_run_is_monthly_on_the_first_and_gated_by_the_named_variable() -> (
    None
):
    """Claim: "the monthly scheduled run armed by the `AUTO_RELEASE_ENABLED` repository variable ... on the 1st"."""
    body = _flat(_section("The release workflow, and why it had to change first"))
    variable = re.search(
        r"monthly scheduled run armed by the `(\w+)` repository variable", body
    )
    assert variable, "the scheduled-run sentence is gone"
    assert "a failed release on the 1st" in body
    workflow = _workflow(_RELEASE)
    crons = [entry["cron"] for entry in _triggers(workflow)["schedule"]]
    assert len(crons) == 1, crons
    _, _, day, month, weekday = crons[0].split()
    assert (day, month, weekday) == ("1", "*", "*"), crons[0]
    assert f"vars.{variable[1]} == 'true'" in _RELEASE.read_text(encoding="utf-8")


def test_the_page_does_not_describe_the_ruleset_as_unapplied() -> None:
    """The present-tense plan the page was written as, before the ruleset landed.

    GitHub has enforced the ruleset since 2026-09-08. Nothing offline can
    re-check that - `scripts/check_ruleset.py` does, online - so the sentences
    that read as though it had not been applied are refused by wording.
    """
    text = _flat(_text())
    for pattern in (
        r"at the time of writing",
        r"\bis\b[^.]{0,40}\bunprotected\b",
        r"\*\*unprotected\*\*",
        r"Do not apply",
        r"Merge that first",
        r"Applying the ruleset today",
        r"only write is a tag push",
    ):
        assert not re.search(pattern, text), (
            f"docs/branch-protection.md still says {pattern!r}"
        )
    assert (
        "Until the ruleset below was applied on 2026-09-08 it was unprotected" in text
    )


def test_the_quoted_release_push_is_not_quoted_as_current() -> None:
    """A `git push` quoted on the page must be one `release.yml` still runs.

    The page used to quote `git push origin HEAD:"${GITHUB_REF_NAME}"` as what
    the release job does, a line #116 removed. Any push the page quotes in a
    fence is checked against the workflow.
    """
    runs = sum(_pushes().values(), [])
    for fence in _fences(""):
        for line in fence.splitlines():
            if line.strip().startswith("git push"):
                assert line.strip() in runs, (
                    f"release.yml no longer runs {line.strip()!r}"
                )


# --- the commands -------------------------------------------------------------


def _commands() -> list[list[str]]:
    commands = []
    for fence in _fences("bash"):
        joined = re.sub(r"\\\n\s*", " ", fence)
        for line in joined.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                commands.append(shlex.split(line))
    return commands


def test_the_page_runs_the_checker_both_ways() -> None:
    """Claim: the two `check_ruleset.py` commands, offline and online."""
    runs = [
        argv[2:]
        for argv in _commands()
        if argv[:2] == ["python3", "scripts/check_ruleset.py"]
    ]
    assert runs == [["--offline"], []], runs
    assert (_ROOT / "scripts" / "check_ruleset.py").is_file()


def test_the_offline_command_needs_no_github(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Claim: "Validate the committed definition without contacting GitHub"."""
    with patch.object(
        check_ruleset, "fetch_live", side_effect=AssertionError("network")
    ):
        assert check_ruleset.main(["--offline"]) == 0
    assert "Definition is valid" in capsys.readouterr().out


def test_the_online_command_passes_when_github_matches_the_file() -> None:
    """Claim: "Verify GitHub agrees with the file"."""
    with patch.object(check_ruleset, "fetch_live", return_value=_definition()):
        assert check_ruleset.main([]) == 0


def test_the_apply_command_posts_the_committed_definition_elsewhere() -> None:
    """Claim: "A repository that does not have it yet ... applies it once with" a POST.

    It must post the file the checker compares against, and it must not name
    this repository: the ruleset exists here, and a second POST would add a
    second ruleset rather than update the first.
    """
    posts = [argv for argv in _commands() if argv[:2] == ["gh", "api"]]
    assert len(posts) == 1, posts
    argv = posts[0]
    assert argv[argv.index("--method") + 1] == "POST"
    assert (
        _ROOT / argv[argv.index("--input") + 1]
    ).resolve() == check_ruleset.DEFAULT_DEFINITION
    endpoint = next(arg for arg in argv[2:] if arg.startswith("repos/"))
    assert endpoint.endswith("/rulesets"), endpoint
    assert endpoint != f"repos/{check_ruleset.DEFAULT_REPO}/rulesets", (
        "the page tells the reader to POST the ruleset to the repository that has it"
    )
    assert "a `POST` here would add a second one beside it" in _flat(_text())


# --- paths --------------------------------------------------------------------


def test_every_path_the_page_names_is_committed() -> None:
    """Each backticked file or directory name resolves, by full path or basename."""
    tracked = _tracked()
    basenames = {Path(path).name for path in tracked}
    tokens = set(re.findall(r"`([^`\s]+)`", _text()))
    paths = {
        token
        for token in tokens
        if re.search(r"\.(py|ya?ml|json|md)$", token) or token.startswith(".github/")
    }
    assert paths, "no paths found - the scan is broken"
    for token in sorted(paths):
        # `.github/workflows/**` is a glob over a directory; resolve the directory.
        path = re.sub(r"/\*\*$", "/", token)
        if "/" in path.rstrip("/"):
            assert path in tracked or any(
                t.startswith(path.rstrip("/") + "/") for t in tracked
            ), path
        else:
            assert path in basenames, path
