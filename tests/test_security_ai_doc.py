"""The agent security policy, `docs/SECURITY-AI.md`, as a subject.

Six modules opened the page before this one, and each took one thing from it:
`tests/test_agent_format_hook.py` reads the "Disable or edit the security
boundary itself" bullet, `tests/test_instruction_docs.py` resolves its markdown
links, and the rest use its path as a string - a file to point a refused
command at, or a CODEOWNERS row. Nothing read what the page says about the
ruleset, the allow list, the Bash gate, the test wrapper or the workflows, and
its backticked paths were not resolved either, because the committed-path check
runs over the instruction files and this page is only a prose file.

It had drifted (#287). f4fe547 corrected the **Push to `master`** bullet to say
the `protect master` ruleset refuses a direct push, and missed the second copy
of the old claim 160 lines further down, which still says that rule is "a
discipline rather than a mechanism here". The page is `Edit`-denied to agents,
so the replacement text is in the issue and the guard below is marked as an
expected failure until a human applies it.

This module joins the page to what decides each claim:

- **The ruleset**: its name, the three things the bullet says it refuses, and
  the number word in front of "checks", against `.github/rulesets/master.json`.
  While the ruleset is active, no sentence may call the rule unenforced.
- **The gates**: the workflows the Hive section names are the workflows whose
  jobs produce a required check, both directions.
- **The allow list**: the non-git `:*` rows the redirection rule names, and the
  rows with no `:*`, against `.claude/settings.json`, both directions.
- **The Bash gate**: every command the page spells out as refused or allowed
  is run through `.claude/hooks/gate-git-file-arguments.py`, and so are the
  refusals it states as rules over the allow list (`xargs` in front of any row,
  a `<` from any `Read` deny path).
- **The test wrapper**: every option the page says it refuses is refused by
  `scripts/run_tests.py`, and the code-loading list is its short-option set.
- **The workflows**: the fork-safety sentence about `labeler.yml`, and the
  removal of `ai-fix.yml` and everything that armed it.
- **Every backticked path** resolves to a tracked file, except the one the page
  says was removed, which must stay absent.

Every scan asserts how much it found before asserting anything about it, so a
reworded sentence fails here instead of making a search come back empty.
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "SECURITY-AI.md"
_SETTINGS = _ROOT / ".claude" / "settings.json"
_HOOK = _ROOT / ".claude" / "hooks" / "gate-git-file-arguments.py"
_DEFINITION = _ROOT / ".github" / "rulesets" / "master.json"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_AUTH = _ROOT / "custom_components" / "sensi" / "auth.py"

_spec = importlib.util.spec_from_file_location(
    "run_tests_wrapper", _ROOT / "scripts" / "run_tests.py"
)
run_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_tests)

_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}

# The workflow the page says was removed. It is the one backticked path that
# must NOT resolve, and it is asserted absent rather than skipped.
_REMOVED = ".github/workflows/ai-fix.yml"


# --- reading the page ---------------------------------------------------------


@functools.cache
def _flat() -> str:
    """Return the page with its whitespace squashed; Markdown wraps anywhere."""
    return " ".join(_DOC.read_text(encoding="utf-8").split())


def _section(heading: str) -> str:
    """Return one `## ` section, flattened."""
    text = _DOC.read_text(encoding="utf-8")
    marker = f"\n## {heading}\n"
    assert marker in text, f"no section {heading!r}"
    body = text.split(marker, 1)[1].split("\n## ", 1)[0]
    return " ".join(body.split())


def _bullet(lead: str) -> str:
    """Return the top-level bullet whose bold lead-in is `lead`, flattened."""
    text = _DOC.read_text(encoding="utf-8")
    start = text.index(f"- **{lead}**")
    end = text.find("\n- ", start + 1)
    return " ".join(text[start : end if end != -1 else None].split())


def _between(text: str, before: str, after: str) -> str:
    assert before in text, f"the page no longer says {before!r}"
    return text.split(before, 1)[1].split(after, 1)[0]


def _ticked(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


def _listed(text: str) -> list[str]:
    """Split "`a`, `b` and `c`" style prose into its items."""
    parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", text)
    return [item.strip() for item in parts if item.strip()]


# --- reading what decides each claim ------------------------------------------


@functools.cache
def _ruleset() -> dict:
    return json.loads(_DEFINITION.read_text(encoding="utf-8"))


def _required() -> list[str]:
    for rule in _ruleset()["rules"]:
        if rule["type"] == "required_status_checks":
            checks = rule["parameters"]["required_status_checks"]
            return [check["context"] for check in checks]
    raise AssertionError("master.json has no required_status_checks rule")


@functools.cache
def _allow() -> tuple[str, ...]:
    """Return the Bash allow rows, without the `Bash(...)` wrapper."""
    rows = json.loads(_SETTINGS.read_text(encoding="utf-8"))["permissions"]["allow"]
    bash = tuple(re.fullmatch(r"Bash\((.*)\)", row)[1] for row in rows)
    assert len(bash) >= 10, bash
    return bash


def _read_denied() -> list[str]:
    rows = json.loads(_SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"]
    paths = [m[1] for row in rows if (m := re.fullmatch(r"Read\(\./(.*)\)", row))]
    assert paths, rows
    return paths


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _check_names(job_id: str, job: dict) -> set[str]:
    """Return the status check names one job reports, with its matrix expanded."""
    name = job.get("name", job_id)
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    names = {name}
    for key, values in matrix.items():
        if not isinstance(values, list):
            continue
        token = "${{ matrix." + key + " }}"
        names = {
            n.replace(token, str(v)) if token in n else n for n in names for v in values
        }
    return names


@functools.cache
def _tracked() -> frozenset[str]:
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return frozenset(path for path in out.split("\0") if path)


def _refused(command: str) -> bool:
    """Run the Bash gate on `command`: exit 2 refuses it, exit 0 lets it run."""
    payload = json.dumps(
        {
            "session_id": "0000",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )
    done = subprocess.run(
        [str(_HOOK)],
        input=payload,
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode in (0, 2), done.stderr
    return done.returncode == 2


# --- the ruleset --------------------------------------------------------------

# The three things the bullet says the ruleset refuses, as the rule types that
# refuse them.
_REFUSES = {
    "a direct push": "pull_request",
    "a force push": "non_fast_forward",
    "deletion": "deletion",
}


def test_the_push_bullet_names_the_ruleset_that_is_committed() -> None:
    """Claim: "The `protect master` ruleset refuses ..."."""
    bullet = _bullet("Push to `master`.")
    name = re.search(r"The `([^`]+)` ruleset refuses", bullet)
    assert name, bullet
    assert name[1] == _ruleset()["name"]
    assert _ruleset()["target"] == "branch"
    assert _ruleset()["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]


def test_the_push_bullet_names_every_rule_that_refuses_a_write() -> None:
    """Claim: "refuses a direct push, a force push and deletion"."""
    refused = _between(
        _bullet("Push to `master`."), "ruleset refuses ", ", and requires"
    )
    items = _listed(refused)
    assert items == list(_REFUSES), items
    types = {rule["type"] for rule in _ruleset()["rules"]}
    missing = {item for item in items if _REFUSES[item] not in types}
    assert not missing, f"no rule refuses {sorted(missing)}"


def test_the_push_bullet_counts_the_required_checks() -> None:
    """Claim: "requires the six checks"."""
    word = re.search(r"requires the (\w+) checks", _bullet("Push to `master`."))
    assert word, "the bullet no longer counts the required checks"
    assert _NUMBER_WORDS[word[1]] == len(_required())


def test_the_push_bullet_names_the_live_check() -> None:
    """Claim: "`python3 scripts/check_ruleset.py` verifies GitHub is still enforcing it"."""
    bullet = _bullet("Push to `master`.")
    assert "`python3 scripts/check_ruleset.py` verifies" in bullet
    assert "scripts/check_ruleset.py" in _tracked()
    assert "`docs/branch-protection.md` explains it" in bullet


# A sentence that calls the rule against pushing to `master` voluntary. Matched
# over the whole page, because the drift in #287 was a second copy of the claim
# far from the bullet that had been corrected.
_UNENFORCED = (
    r"discipline rather than a mechanism",
    r"\bis\b[^.]{0,40}\bunprotected\b",
    r"\bnot (?:yet )?(?:enforced|protected)\b",
)


def _unenforced_sentences() -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", _flat())
    return [
        sentence
        for sentence in sentences
        if any(re.search(pattern, sentence) for pattern in _UNENFORCED)
    ]


def test_the_guard_can_see_the_sentence_it_guards_against() -> None:
    """The patterns match the wording #263 removed from the bullet."""
    old = (
        "The branch is unprotected today, which makes this a discipline "
        "rather than a mechanism"
    )
    assert all(re.search(pattern, old) for pattern in _UNENFORCED[:2]), (
        "a guard pattern no longer matches the claim it was written for"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#287: docs/SECURITY-AI.md:201-205 still calls the master rule 'a "
        "discipline rather than a mechanism'. The page is Edit-denied to "
        "agents; the replacement text is in the issue. Remove this marker in "
        "the diff that applies it."
    ),
)
def test_no_sentence_calls_the_master_rule_unenforced() -> None:
    """While the ruleset is active, the page must not say nothing enforces it."""
    assert _ruleset()["enforcement"] == "active"
    stale = _unenforced_sentences()
    assert not stale, stale


# --- the gates ----------------------------------------------------------------


def test_the_workflows_named_as_gates_are_the_ones_the_ruleset_requires() -> None:
    """Claim: "the gates in `ci.yml`, `coverage-gate.yml` and `validate.yml` apply"."""
    named = _between(
        _section("One autonomous path, and it is Hive"), "the gates in ", " apply"
    )
    stated = {item.strip("`") for item in _listed(named)}
    assert len(stated) >= 2, stated

    required = set(_required())
    gating = set()
    produced = set()
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        for job_id, job in _workflow(path)["jobs"].items():
            checks = _check_names(job_id, job) & required
            if checks:
                gating.add(path.name)
                produced |= checks
    assert produced == required, f"no job produces {sorted(required - produced)}"
    assert stated == gating, (
        f"named but gate nothing: {sorted(stated - gating)}; "
        f"gate but not named: {sorted(gating - stated)}"
    )


# --- the allow list -----------------------------------------------------------

# How the redirection rule names each non-git `:*` row, as the row's prefix. A
# name the page adds that is not here fails, so a new row is classified here.
_PROSE_ROWS = {
    "the test wrapper": ("python3 scripts/run_tests.py",),
    "the PR metrics script": ("python3 scripts/pr_metrics.py",),
    "the `gh pr`": ("gh pr ",),
    "`gh issue`": ("gh issue ",),
    "`gh run` read verbs": ("gh run ",),
}

_READ_VERBS = frozenset({"view", "diff", "list"})


def _open_rows() -> list[str]:
    """Return the allow rows ending `:*`, as their prefixes."""
    return [row.removesuffix(":*") for row in _allow() if row.endswith(":*")]


def test_the_redirection_rule_names_every_non_git_open_row() -> None:
    """Claim: "every `:*` row that is not git's (the test wrapper, ...)"."""
    names = _listed(_between(_flat(), "every `:*` row that is not git's (", ")"))
    unknown = [name for name in names if name not in _PROSE_ROWS]
    assert not unknown, f"unclassified row names: {unknown}"

    rows = [row for row in _open_rows() if not row.startswith("git ")]
    assert len(rows) >= 5, rows
    covered = {
        row for name in names for row in rows if row.startswith(_PROSE_ROWS[name])
    }
    assert covered == set(rows), f"not named: {sorted(set(rows) - covered)}"
    for name in names:
        assert any(row.startswith(_PROSE_ROWS[name]) for row in rows), (
            f"{name!r} names no allow row"
        )


def test_the_gh_rows_are_read_verbs() -> None:
    """Claim: "the `gh pr`, `gh issue` and `gh run` read verbs"."""
    verbs = {row.split()[2] for row in _open_rows() if row.startswith("gh ")}
    assert verbs, _open_rows()
    assert verbs <= _READ_VERBS, f"not a read verb: {sorted(verbs - _READ_VERBS)}"


def test_the_rows_with_no_open_suffix_are_counted_and_named() -> None:
    """Claim: "The three rows with no `:*` (`ruff check .`, ...)"."""
    match = re.search(r"The (\w+) rows with no `:\*` \(([^)]*)\)", _flat())
    assert match, "the page no longer lists the rows with no `:*`"
    named = set(_ticked(match[2]))
    closed = {row for row in _allow() if not row.endswith(":*")}
    assert _NUMBER_WORDS[match[1].lower()] == len(closed)
    assert named == closed, (
        f"named but not rows: {sorted(named - closed)}; "
        f"rows but not named: {sorted(closed - named)}"
    )


def test_the_two_git_rows_that_touch_files_are_allow_listed() -> None:
    """Claim: "Two of git's allow rows write or read files through git itself"."""
    match = re.search(r"(\w+) of git's allow rows write or read files", _flat())
    assert match, "the page no longer counts the git rows that touch files"
    named = ("git checkout -b", "git add")
    assert _NUMBER_WORDS[match[1].lower()] == len(named)
    for prefix in named:
        assert f"{prefix}:*" in _allow(), prefix
    assert "`git checkout -b NAME`" in _flat()
    assert "`git add --pathspec-from-file`" in _flat()


# --- the Bash gate ------------------------------------------------------------

# Each command the page spells out, and what it says the gate does with it. The
# first column must appear in the page as written, so an example that is
# reworded there is reworded here too.
_EXAMPLES = (
    ("git diff HEAD >out", "git diff HEAD >out", True),
    ("git diff --no-index", "git diff --no-index README.md AGENTS.md", True),
    (
        "GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD",
        "GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD",
        True,
    ),
    (
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external GIT_CONFIG_VALUE_0=prog",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external GIT_CONFIG_VALUE_0=prog git diff HEAD~1 HEAD",
        True,
    ),
    ("NAME+=value", "GIT_EXTERNAL_DIFF+=prog git diff HEAD~1 HEAD", True),
    ("export NAME=value", "export GIT_EXTERNAL_DIFF=prog; git diff HEAD~1 HEAD", True),
    ("declare -x", "declare -x GIT_EXTERNAL_DIFF=prog; git diff HEAD~1 HEAD", True),
    ("typeset -x", "typeset -x GIT_EXTERNAL_DIFF=prog; git diff HEAD~1 HEAD", True),
    ("readonly", "readonly GIT_EXTERNAL_DIFF=prog; git diff HEAD~1 HEAD", True),
    ("env -S", "env -S 'GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD'", True),
    ("git checkout -b NAME", "git checkout -b NAME HEAD~1", True),
    ("git checkout -b NAME", "git checkout -b NAME", False),
    ("git add --pathspec-from-file", "git add --pathspec-from-file=README.md", True),
    ("git add --force", "git add --force README.md", True),
    ("git add .", "git add .", False),
    ("git log --stdin <.env", "git log --stdin <.env", True),
    ("git log --stdin </dev/null", "git log --stdin </dev/null", False),
    ("git log --stdin <revs.txt", "git log --stdin <revs.txt", False),
    ("2>&1", "git diff HEAD 2>&1", False),
    ("--output", "git diff --output=out HEAD", True),
    ("-O", "git diff -Oorder.txt HEAD", True),
    ("~", "git diff ~/notes.txt", True),
)


@pytest.mark.parametrize(
    ("quoted", "command", "refused"), _EXAMPLES, ids=[row[1] for row in _EXAMPLES]
)
def test_each_example_is_decided_as_the_page_says(
    quoted: str, command: str, refused: bool
) -> None:
    """Every command the page spells out is refused or allowed as it says."""
    assert f"`{quoted}`" in _flat(), f"the page no longer quotes {quoted!r}"
    assert _refused(command) is refused, command


def test_a_redirection_before_the_command_name_is_refused_too() -> None:
    """Claim: "or the same redirection written before the command name"."""
    assert "or the same redirection written before the command name" in _flat()
    assert _refused(">out git diff HEAD")


def test_xargs_is_refused_in_front_of_every_allow_row() -> None:
    """Claim: "`xargs` in front of any allow-listed command is refused outright"."""
    assert "`xargs` in front of any allow-listed command is refused outright" in _flat()
    allowed = _open_rows() + [row for row in _allow() if not row.endswith(":*")]
    passed = [row for row in allowed if not _refused(f"xargs {row}")]
    assert not passed, passed


def test_ruff_is_refused_its_own_output_file() -> None:
    """Claim: "ruff its own `--output-file`/`-o`"."""
    assert "ruff its own `--output-file`/`-o`" in _flat()
    for option in ("--output-file", "-o"):
        assert _refused(f"ruff check . {option} report.txt"), option


def test_a_read_denied_file_on_git_stdin_is_refused() -> None:
    """Claim: "A `<` on an allow-listed git command must name a file ... that none of the `Read` deny rows ... match"."""
    assert "none of the `Read` deny rows" in _flat()
    for path in _read_denied():
        target = path.replace("**", "secrets.yaml")
        assert _refused(f"git log --stdin <{target}"), target


def test_redirections_on_the_closed_rows_are_refused() -> None:
    """Claim: the rows with no `:*` "are refused a redirection the same way"."""
    assert "are refused a redirection the same way" in _flat()
    closed = [row for row in _allow() if not row.endswith(":*")]
    passed = [row for row in closed if not _refused(f"{row} >out.txt")]
    assert not passed, passed


# --- the test wrapper ---------------------------------------------------------

# Long spellings of the short code-loading options. The page names the short
# form; the wrapper refuses both.
_LONG_ALIASES = {"--config-file": "-c", "--override-ini": "-o"}


def test_the_code_loading_options_are_the_wrappers_list() -> None:
    """Claim: "the options that load code (`-p`, `-c`, `-o`, `--pyargs`, `--confcutdir`) are refused"."""
    stated = set(_ticked(_between(_flat(), "the options that load code (", ")")))
    assert stated, "the page no longer lists the code-loading options"
    long_ = run_tests.REFUSED_LONG - set(_LONG_ALIASES)
    assert set(_LONG_ALIASES) <= run_tests.REFUSED_LONG
    assert set(_LONG_ALIASES.values()) <= run_tests.REFUSED_SHORT
    assert stated == set(run_tests.REFUSED_SHORT) | long_, (
        f"named but not refused: {sorted(stated - run_tests.REFUSED_SHORT - long_)}; "
        f"refused but not named: {sorted((run_tests.REFUSED_SHORT | long_) - stated)}"
    )


def test_each_option_the_page_names_is_refused_by_the_wrapper() -> None:
    """Claim: the options named in the Always bullet are refused, with a value."""
    body = _between(_flat(), "The wrapper is pytest with three rules", "The full list")
    options = [token for token in _ticked(body) if token.startswith("-")]
    assert len(options) >= 9, options
    for option in options:
        argument = option if option in run_tests.REFUSED_SHORT else f"{option}=x"
        if option == "--cov-report":
            argument = "--cov-report=xml:DEST"
        assert run_tests.refusals([argument]), argument
    assert "`xml:DEST`" in body
    assert run_tests.refusals(["--cov-report=xml:DEST"])
    assert not run_tests.refusals(["--cov-report=term-missing"])


def test_every_target_must_be_inside_tests() -> None:
    """Claim: "every target must be inside `tests/`"."""
    assert "every target must be inside `tests/`" in _flat()
    assert run_tests.refusals(["custom_components"])
    assert not run_tests.refusals(["tests"])


# --- the workflows ------------------------------------------------------------


def _triggers(workflow: dict) -> dict:
    # PyYAML reads a bare `on:` key as the boolean True.
    on = workflow.get("on", workflow.get(True))
    return on if isinstance(on, dict) else {name: None for name in [on] if name}


def test_labeler_is_the_only_pull_request_target_and_checks_out_the_base() -> None:
    """Claim: "`labeler.yml` uses `pull_request_target` but checks out the base commit"."""
    assert (
        "`labeler.yml` uses `pull_request_target` but checks out the base commit"
        in _flat()
    )
    targets = [
        path.name
        for path in sorted(_WORKFLOWS.glob("*.yml"))
        if "pull_request_target" in _triggers(_workflow(path))
    ]
    assert targets == ["labeler.yml"], targets
    checkouts = [
        step
        for job in _workflow(_WORKFLOWS / "labeler.yml")["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkouts, "labeler.yml checks nothing out"
    for step in checkouts:
        assert (step.get("with") or {}).get("ref") == (
            "${{ github.event.pull_request.base.sha }}"
        ), step


def test_the_removed_workflow_and_what_armed_it_are_gone() -> None:
    """Claim: "`.github/workflows/ai-fix.yml` ran Claude ... It was removed"."""
    assert f"`{_REMOVED}` ran Claude" in _flat()
    assert _REMOVED not in _tracked()
    armed = ("ANTHROPIC_API_KEY", "AI_FIX_ENABLED", "ai-fix-requested", "@claude")
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        found = [needle for needle in armed if needle in text]
        assert not found, f"{path.name} still carries {found}"


# --- names and paths ----------------------------------------------------------


def test_auth_exports_redact_token() -> None:
    """Claim: "`auth.py` exports `redact_token`".

    It is defined in `utils.py` and bound in `auth.py` by import, which is what
    makes `from .auth import redact_token` work. Either binding satisfies
    "exports"; neither one is allowed to disappear.
    """
    assert "`auth.py` exports `redact_token`" in _flat()
    tree = ast.parse(_AUTH.read_text(encoding="utf-8"))
    bound = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    bound |= {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "redact_token" in bound


_PATH_SHAPE = re.compile(r"^[\w./-]+$")
_SUFFIXES = (".py", ".md", ".yml", ".json", ".txt")


def _path_tokens() -> list[str]:
    tokens = {
        token
        for token in _ticked(_flat())
        if _PATH_SHAPE.match(token) and ("/" in token or token.endswith(_SUFFIXES))
    }
    return sorted(tokens)


def _resolves(token: str) -> bool:
    tracked = _tracked()
    if token.endswith("/"):
        return any(path.startswith(token) for path in tracked)
    if token in tracked:
        return True
    # A bare workflow name is the workflow: `labeler.yml` is also the name of
    # the labeler's config, but the page names it as the thing that "uses
    # `pull_request_target`".
    if (_WORKFLOWS / token).relative_to(_ROOT).as_posix() in tracked:
        return True
    # Any other bare name - `auth.py`, `manifest.json` - must name exactly one
    # tracked file, or the page is ambiguous about which it means.
    matches = [path for path in tracked if path.rsplit("/", 1)[-1] == token]
    return len(matches) == 1


def test_the_path_scan_finds_the_paths() -> None:
    """The scan sees the page's paths, including the removed workflow."""
    tokens = _path_tokens()
    assert len(tokens) >= 15, tokens
    assert _REMOVED in tokens


@pytest.mark.parametrize("token", _path_tokens())
def test_every_backticked_path_is_committed(token: str) -> None:
    """Every path the page names is tracked, except the one it says was removed."""
    if token == _REMOVED:
        assert not _resolves(token), f"{token} is back"
        return
    assert _resolves(token), f"{token!r} names no tracked file"
