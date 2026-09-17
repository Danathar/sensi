"""The four `.claude/commands/` files are procedures, and nothing reads them.

`tests/test_instruction_docs.py` joins the prose in `AGENTS.md` and its
restatements to the artefacts that decide those claims. It stops at the
slash-command table: `test_claude_md_command_table_matches_the_committed_commands`
compares the set of file *names* in `.claude/commands/` against the table in
`CLAUDE.md` and never opens a file. The bodies are the part that gets executed.

Each of them is a procedure an agent follows without checking:

- `check.md` is the local gate. It names four commands and claims they are
  "every check CI runs", quotes the coverage threshold, and says which two
  checks cannot be reproduced locally.
- `cover.md` routes new tests to a tier, names the fixtures and the fake to
  use, and quotes the threshold again along with the workflow that holds it.
- `add-entity.md` maps a kind of value to a platform module, points at the
  capability gate to copy, and names the helpers, the state class, the test
  modules and the fixtures involved.
- `capture-payload.md` is the scrub list for a payload taken from a real
  account. Every field name in its table is a hand copy of a field the
  component parses, and a field that drops out of the table is a field nobody
  is told to scrub.

None of that is checkable by the tools the repository already runs. Markdown
is not measured by `.coveragerc` (`source = custom_components/sensi`), ruff
does not read it, and the commands are executed by an agent rather than by
CI. So these files can name a module that was renamed, a fixture that was
removed, a helper that moved, or a coverage number that changed, and the only
reader affected is the one with no way to notice.

This module is the join. Each test takes a claim one of the commands makes
and checks it against the artefact that actually decides it: the workflows
for the gate list and the threshold, `.claude/settings.json` for whether a
command it tells an agent to run is auto-approved, `custom_components/sensi/`
for every module, platform, helper and field named, `tests/conftest.py` and
`tests/e2e/conftest.py` for every fixture and fake, and `git ls-files` for
every path.

Conventions carried from `tests/test_codeowners.py`,
`tests/test_instruction_docs.py` and `tests/test_file_conventions.py`:

- A claim table is a list of claims, not a cache. Dropping a rule from a
  command means dropping its row here, in the same diff, where it is
  reviewable - and the table is asserted to cover every committed command, so
  a new command cannot arrive unread.
- The permission matcher is hand-rolled and carries its own case table.
  Every assertion about what `.claude/settings.json` approves quantifies over
  lists that matcher builds, so a matcher stuck at True or False would turn
  those assertions green either way.
- Every scan asserts how much it found before asserting anything about it.

What is deliberately NOT asserted: that the wording of a command matches the
`CLAUDE.md` table row that advertises it (the descriptions are written for
two different readers), that every platform module drives its entities from
a table (`binary_sensor.py` has a single description and does not need one),
or that a command names every fixture or helper that exists - a procedure is
allowed to name the ones it needs.
"""

from __future__ import annotations

import ast
import configparser
import functools
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent

_COMMANDS_DIR = _ROOT / ".claude" / "commands"
_SETTINGS = _ROOT / ".claude" / "settings.json"
_COVERAGERC = _ROOT / ".coveragerc"
_COMPONENT = _ROOT / "custom_components" / "sensi"
_TESTS = _ROOT / "tests"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_COVERAGE_GATE = _WORKFLOWS / "coverage-gate.yml"
_VALIDATE = _WORKFLOWS / "validate.yml"
_NIGHTLY = _WORKFLOWS / "nightly.yml"
_CI = _WORKFLOWS / "ci.yml"
_REVIEW_RUBRIC = _ROOT / "docs" / "review-rubric.md"

_CHECK = _COMMANDS_DIR / "check.md"
_COVER = _COMMANDS_DIR / "cover.md"
_ADD_ENTITY = _COMMANDS_DIR / "add-entity.md"
_CAPTURE_PAYLOAD = _COMMANDS_DIR / "capture-payload.md"

# Every committed command, and what this module checks about it. A command
# added to `.claude/commands/` without a row here fails the coverage guard
# below rather than going unread.
_COMMANDS = {
    "check": "the local gate: the command list, the threshold, the CI-only checks",
    "cover": "the tier split, the fixtures and fake, the threshold and its file",
    "add-entity": "the platform map, the capability gate, the helpers and fixtures",
    "capture-payload": "the scrub table and the fixture conventions",
}


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


@functools.cache
def _tracked_files() -> frozenset[str]:
    """Return every path git tracks, read from the index rather than a walk."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return frozenset(part for part in out.stdout.split("\0") if part)


_TRACKED = _tracked_files()
_TRACKED_DIRS = frozenset(
    parent.as_posix() for path in _TRACKED for parent in Path(path).parents
)
_TRACKED_BASENAMES = frozenset(Path(path).name for path in _TRACKED)

_COMMAND_FILES = tuple(
    sorted(
        _ROOT / path
        for path in _TRACKED
        if path.startswith(".claude/commands/") and path.endswith(".md")
    )
)


def _rel(path: Path) -> str:
    """Return a path relative to the repository root, for failure messages."""
    return path.relative_to(_ROOT).as_posix()


# --------------------------------------------------------------------------
# Reading a command file
# --------------------------------------------------------------------------

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


def _frontmatter(path: Path) -> tuple[dict, str]:
    """Return a command's frontmatter mapping and its body.

    Claude Code reads the frontmatter to build the command's entry in the
    picker; the body is the prompt. A file with no frontmatter block raises
    here rather than resolving to an empty mapping that every assertion about
    the frontmatter would then agree with.
    """
    match = _FRONTMATTER.match(_read(path))
    if match is None:
        raise AssertionError(f"{_rel(path)} has no `---` frontmatter block")
    loaded = yaml.safe_load(match.group(1))
    if not isinstance(loaded, dict):
        raise AssertionError(f"{_rel(path)} frontmatter is not a mapping")
    return loaded, match.group(2)


_FENCED_BLOCK = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)


def _shell_lines(path: Path) -> list[str]:
    """Return every command line inside a command file's shell code blocks.

    Only fenced blocks tagged as shell are read. A command file quotes a
    command to be *run*, and that is the only kind of block these files
    carry; an untagged block would be prose, and reading it as a command
    would invent claims the file does not make.
    """
    lines = []
    for tag, body in (
        (match.group(1), match.group(2))
        for match in _FENCED_BLOCK.finditer(_read(path))
    ):
        if tag not in {"bash", "sh", "shell", "console"}:
            continue
        lines.extend(
            stripped
            for stripped in (line.strip() for line in body.splitlines())
            if stripped and not stripped.startswith("#")
        )
    return lines


# A backticked name that looks like a repository path, as
# `tests/test_instruction_docs.py` resolves one. Bare file names are resolved
# against every tracked basename because the prose names component modules by
# name alone (`sensor.py`, not `custom_components/sensi/sensor.py`).
_PATH_IN_BACKTICKS = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*"
    r"\.(?:py|json|toml|ini|yml|yaml|md|mdc|txt|sh|cfg)|"
    r"[A-Za-z0-9_][A-Za-z0-9_./-]*/)`"
)


def _named_paths(path: Path) -> list[str]:
    """Return every repository path a command names in backticks."""
    return [match.group(1) for match in _PATH_IN_BACKTICKS.finditer(_read(path))]


def _path_exists(name: str) -> bool:
    """Return whether a path named in prose resolves to something committed.

    Three spellings, because the procedures use all three: a repository path,
    a bare file name (the prose writes `sensor.py`, not
    `custom_components/sensi/sensor.py`), and a path relative to the
    component package (`translations/en.json`).
    """
    cleaned = name.rstrip("/")
    candidates = [cleaned, f"custom_components/sensi/{cleaned}"]
    if any(
        candidate in _TRACKED or candidate in _TRACKED_DIRS for candidate in candidates
    ):
        return True
    return "/" not in cleaned and cleaned in _TRACKED_BASENAMES


@functools.cache
def _module_tree(relpath: str) -> ast.Module:
    """Return the parsed module at `relpath`, which must be committed."""
    assert relpath in _TRACKED, f"{relpath} is not committed"
    return ast.parse(_read(_ROOT / relpath))


def _defined_names(relpath: str) -> frozenset[str]:
    """Return every name a module defines or imports at any level.

    An import counts as a definition: four instruction files already name
    `redact_token` as belonging to `auth.py`, where it is re-exported rather
    than defined, and the same is true of anything a command points at.
    """
    names: set[str] = set()
    for node in ast.walk(_module_tree(relpath)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
    return frozenset(names)


def _string_constants(relpath: str) -> frozenset[str]:
    """Return every string literal in a module."""
    return frozenset(
        node.value
        for node in ast.walk(_module_tree(relpath))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


@functools.cache
def _component_string_constants() -> frozenset[str]:
    """Return every string literal in the component package."""
    constants: set[str] = set()
    for path in sorted(_COMPONENT.glob("*.py")):
        relpath = f"custom_components/sensi/{path.name}"
        if relpath in _TRACKED:
            constants |= _string_constants(relpath)
    return frozenset(constants)


# --------------------------------------------------------------------------
# The permission rules, which decide whether a command can be run at all
#
# Hand-rolled on purpose, and only the two rule shapes `.claude/settings.json`
# uses: `Bash(<prefix>:*)` approves any command starting with the prefix, and
# `Bash(<command>)` approves exactly that command. Anything else raises
# rather than resolving to something plausible.
# --------------------------------------------------------------------------


def _bash_rule(rule: str) -> tuple[str, bool] | None:
    """Return (text, is_prefix) for a Bash permission rule, or None.

    A rule for another tool - `Read(./secrets.yaml)`, `Edit(.claude/hooks/**)`
    - is not about running a command and is skipped.
    """
    match = re.fullmatch(r"Bash\((.+)\)", rule)
    if match is None:
        if re.fullmatch(r"(Read|Edit|Write|WebFetch)\(.+\)", rule):
            return None
        raise AssertionError(
            f"permission rule in a shape this test cannot read: {rule}"
        )
    body = match.group(1)
    if body.endswith(":*"):
        return body[:-2], True
    if "*" in body:
        raise AssertionError(f"Bash rule with a wildcard this test cannot read: {rule}")
    return body, False


def _rule_matches(rule: str, command: str) -> bool:
    """Return whether one permission rule covers `command`."""
    parsed = _bash_rule(rule)
    if parsed is None:
        return False
    text, is_prefix = parsed
    if is_prefix:
        return command == text or command.startswith(f"{text} ")
    return command == text


@functools.cache
def _permissions() -> dict[str, tuple[str, ...]]:
    """Return the three permission lists from `.claude/settings.json`."""
    settings = json.loads(_read(_SETTINGS))
    permissions = settings["permissions"]
    return {kind: tuple(permissions.get(kind, ())) for kind in ("allow", "ask", "deny")}


def _decision(command: str) -> str:
    """Return how `.claude/settings.json` treats `command`.

    `deny` wins over `allow`, `allow` over `ask`, and a command no rule names
    is `unlisted` - which means Claude Code asks, the same outcome as `ask`
    but arrived at by nobody's decision.
    """
    rules = _permissions()
    for kind in ("deny", "allow", "ask"):
        if any(_rule_matches(rule, command) for rule in rules[kind]):
            return kind
    return "unlisted"


# The case table the matcher is proved against. Every other assertion about
# the permission rules rests on `_rule_matches`.
_MATCHER_CASES = (
    ("Bash(ruff check:*)", "ruff check .", True),
    ("Bash(ruff check:*)", "ruff check", True),
    ("Bash(ruff check:*)", "ruff check --fix .", True),
    ("Bash(ruff check:*)", "ruff format --check .", False),
    ("Bash(ruff check:*)", "ruff checkout", False),
    ("Bash(python3 scripts/run_tests.py:*)", "python3 scripts/run_tests.py", True),
    (
        "Bash(python3 scripts/run_tests.py:*)",
        "python3 scripts/run_tests.py tests/e2e",
        True,
    ),
    ("Bash(python3 scripts/run_tests.py:*)", "pytest", False),
    (
        "Bash(python3 scripts/check_requirements_sync.py)",
        "python3 scripts/check_requirements_sync.py",
        True,
    ),
    (
        "Bash(python3 scripts/check_requirements_sync.py)",
        "python3 scripts/check_requirements_sync.py --fix",
        False,
    ),
    ("Bash(pytest:*)", "pytest --cov=custom_components.sensi", True),
    ("Read(./secrets.yaml)", "cat secrets.yaml", False),
)


@pytest.mark.parametrize(("rule", "command", "expected"), _MATCHER_CASES)
def test_the_permission_matcher_answers_its_case_table(
    rule: str, command: str, expected: bool
) -> None:
    """Prove the matcher every permission assertion rests on is not stuck."""
    assert _rule_matches(rule, command) is expected


def test_the_permission_matcher_refuses_a_rule_shape_it_cannot_read() -> None:
    """Raise rather than resolving an unknown shape to something plausible."""
    with pytest.raises(AssertionError):
        _rule_matches("Bash(git * --force)", "git push --force")
    with pytest.raises(AssertionError):
        _rule_matches("MysteryTool(anything)", "anything")


# --------------------------------------------------------------------------
# The commands themselves
# --------------------------------------------------------------------------


def test_the_command_scan_finds_the_committed_commands() -> None:
    """Every assertion below quantifies over this list."""
    assert len(_COMMAND_FILES) >= 4, (
        f"found {len(_COMMAND_FILES)} committed command files in "
        f"{_rel(_COMMANDS_DIR)}; the scan is not looking where it thinks"
    )
    assert _CHECK in _COMMAND_FILES


def test_every_committed_command_is_read_by_this_module() -> None:
    """A command added without a row here would go unchecked."""
    committed = {path.stem for path in _COMMAND_FILES}
    assert committed == set(_COMMANDS), (
        f"{_rel(_COMMANDS_DIR)} and the claim table in this module disagree. "
        f"Committed but unread here: {sorted(committed - set(_COMMANDS))}. "
        f"Claimed here but not committed: {sorted(set(_COMMANDS) - committed)}."
    )


@pytest.mark.parametrize("command", _COMMAND_FILES, ids=_rel)
def test_every_command_declares_a_description(command: Path) -> None:
    """The description is the command's only label in the picker."""
    meta, _body = _frontmatter(command)
    description = meta.get("description")
    assert isinstance(description, str) and description.strip(), (
        f"{_rel(command)} declares no `description`, so it appears in the "
        "slash-command picker with nothing to say what it does"
    )


@pytest.mark.parametrize("command", _COMMAND_FILES, ids=_rel)
def test_a_command_that_takes_arguments_declares_an_argument_hint(
    command: Path,
) -> None:
    """`$ARGUMENTS` with no hint asks the caller to guess what to pass."""
    meta, body = _frontmatter(command)
    uses_arguments = "$ARGUMENTS" in body
    has_hint = bool(str(meta.get("argument-hint", "")).strip())
    assert uses_arguments == has_hint, f"{_rel(command)} " + (
        "substitutes $ARGUMENTS but declares no `argument-hint`"
        if uses_arguments
        else "declares an `argument-hint` but never substitutes $ARGUMENTS"
    )


# How many backticked paths each command names. The count is per file rather
# than a single floor because `cover.md` names two and `add-entity.md` names a
# dozen; one floor would either be vacuous for the long file or wrong for the
# short one. A rewrite that drops the paths out of a file lands here.
_EXPECTED_PATH_COUNTS = {
    "check": 3,
    "cover": 2,
    "add-entity": 8,
    "capture-payload": 4,
}


@pytest.mark.parametrize("command", _COMMAND_FILES, ids=_rel)
def test_every_path_a_command_names_is_committed(command: Path) -> None:
    """A path renamed out from under a procedure must fail here."""
    named = _named_paths(command)
    floor = _EXPECTED_PATH_COUNTS[command.stem]
    assert len(named) >= floor, (
        f"{_rel(command)}: found only {len(named)} backticked paths, expected "
        f"at least {floor}, so the scan below is not looking at the file it "
        "thinks it is"
    )
    missing = sorted({name for name in named if not _path_exists(name)})
    assert not missing, (
        f"{_rel(command)} names paths that are not committed: {missing}. "
        "Either the file was renamed and the procedure still points at the "
        "old name, or the step no longer applies."
    )


_SLASH_REFERENCE = re.compile(r"`/([a-z][a-z-]*)`")


@pytest.mark.parametrize("command", _COMMAND_FILES, ids=_rel)
def test_every_command_a_command_hands_off_to_is_committed(command: Path) -> None:
    """`Finish by running /check` must name a command that exists."""
    referenced = set(_SLASH_REFERENCE.findall(_read(command)))
    committed = {path.stem for path in _COMMAND_FILES}
    missing = sorted(referenced - committed)
    assert not missing, (
        f"{_rel(command)} hands off to slash commands that are not committed: {missing}"
    )


@pytest.mark.parametrize("command", _COMMAND_FILES, ids=_rel)
def test_every_command_a_procedure_runs_is_auto_approved(command: Path) -> None:
    """A procedure an agent follows must not stop on a permission prompt.

    `.claude/settings.json` is where the repository decided which commands
    run unattended. `scripts/run_tests.py` exists because the decision is a
    security boundary rather than an ergonomic one: bare `pytest` is on the
    `ask` list precisely because an allow rule for it would also approve
    `pytest <path outside the repository>`. A command file that tells an
    agent to run the asked-for spelling routes around that boundary and
    stalls, which is how the wrapper gets skipped.
    """
    lines = _shell_lines(command)
    if not lines:
        pytest.skip(f"{_rel(command)} quotes no shell commands")

    wrong = {line: _decision(line) for line in lines if _decision(line) != "allow"}
    assert not wrong, (
        f"{_rel(command)} tells an agent to run commands that "
        f"{_rel(_SETTINGS)} does not auto-approve: {wrong}. Either use the "
        "spelling the settings allow - `python3 scripts/run_tests.py` rather "
        "than bare `pytest` - or move the rule, deliberately, in this diff."
    )


def test_the_shell_scan_finds_the_commands_it_checks() -> None:
    """Guard the scan above: a file that parses to no lines is skipped."""
    found = {_rel(path): len(_shell_lines(path)) for path in _COMMAND_FILES}
    assert sum(found.values()) >= 5, f"found almost no shell commands: {found}"
    assert found[_rel(_CHECK)] >= 4, (
        f"{_rel(_CHECK)} is the local gate and quotes "
        f"{found[_rel(_CHECK)]} command(s); the block it is read from changed shape"
    )


# Frontmatter that is capability rather than description. A file in
# `.claude/commands/` is the older format for a skill and takes the same
# frontmatter, so one of these keys pre-approves tools for the turn that
# invokes the command, registers a hook that keeps running for the rest of
# the session, or chooses the model the turn runs on. None of that is what
# these four files are for, and none of it appears in the slash-command
# picker, so the reviewer of a diff that adds one sees a procedure change.
_CAPABILITY_KEYS = ("allowed-tools", "disallowed-tools", "hooks", "model", "shell")

# `!` at the start of a line or after whitespace, followed by a backticked
# command, is dynamic context injection: Claude Code runs the command and
# substitutes its output before the prompt is assembled. A fenced ```! block
# does the same for several commands. `KEY=!`cmd`` is literal text and is
# deliberately not matched, which is also Claude Code's own rule.
_SHELL_INJECTION = re.compile(r"(?:\A|\s)!`|^```!\s*$", re.MULTILINE)


@pytest.mark.parametrize("command", _COMMAND_FILES, ids=_rel)
def test_a_command_grants_no_capability_in_its_frontmatter(command: Path) -> None:
    """A procedure says what to do. These keys say what the session may do.

    Tool permissions belong in `.claude/settings.json`, where the deny list,
    `tests/test_agent_format_hook.py` and `.github/CODEOWNERS` all reach them.
    A grant written here is subject to none of those.
    """

    meta, _body = _frontmatter(command)
    granted = sorted(key for key in _CAPABILITY_KEYS if key in meta)
    assert not granted, (
        f"{_rel(command)} declares {granted} in its frontmatter, which changes "
        "what the session may do rather than what it is told to do"
    )


@pytest.mark.parametrize("command", _COMMAND_FILES, ids=_rel)
def test_a_command_runs_no_shell_of_its_own(command: Path) -> None:
    """An injected command runs while the file renders, with nothing to approve.

    The commands these files name - `ruff`, `python3 scripts/run_tests.py` -
    are for an agent to run as tool calls, which the permission rules see.
    """

    _meta, body = _frontmatter(command)
    assert not _SHELL_INJECTION.search(body), (
        f"{_rel(command)} injects a shell command with `!`; Claude Code runs "
        "it before the prompt is assembled and no permission prompt appears"
    )


# --------------------------------------------------------------------------
# check.md: "every check CI runs"
# --------------------------------------------------------------------------

# A gate is identified by what it checks, not by how it is spelled. The
# wrapper and bare `pytest` are the same gate - the wrapper forwards to pytest
# - and `--output-format=github` is a CI presentation flag, not a check.
_GATE_IDENTITIES = (
    ("ruff format", "ruff format --check"),
    ("ruff check", "ruff check"),
    ("pytest", "the test suite"),
    ("python3 scripts/run_tests.py", "the test suite"),
    ("python scripts/run_tests.py", "the test suite"),
    ("python3 scripts/check_requirements_sync.py", "the requirements sync check"),
)

# Lines in a workflow that begin like a gate but are not one. Each entry is
# asserted below to still be what it says, so this is not a way to excuse a
# gate that quietly stopped being named in check.md.
_NOT_A_GATE = {
    "python3 scripts/auto_qa_tuner.py": (
        "writes a proposed threshold into the run summary; it changes nothing "
        "and fails nothing"
    ),
    "python3 scripts/coverage_badge.py": (
        "renders the badge and trend data after the gate has already passed "
        "or failed; it publishes a result rather than deciding one"
    ),
}

_GATE_WORKFLOWS = (_CI, _COVERAGE_GATE, _VALIDATE, _NIGHTLY)


def _run_lines(workflow: Path) -> list[str]:
    """Return every line of every `run:` body in a workflow."""
    loaded = yaml.safe_load(_read(workflow))
    lines = []
    for job in loaded["jobs"].values():
        for step in job.get("steps", ()):
            if "run" not in step:
                continue
            # A continuation joins its next line, so a multi-line invocation
            # is read as the one command it is.
            body = step["run"].replace("\\\n", " ")
            lines.extend(
                stripped
                for stripped in (line.strip() for line in body.splitlines())
                if stripped and not stripped.startswith("#")
            )
    return lines


def _gate_identity(line: str) -> str | None:
    """Return the gate a command line is, or None if it is not one."""
    command = line.split("|")[0].strip()
    for prefix, identity in _GATE_IDENTITIES:
        if command == prefix or command.startswith(f"{prefix} "):
            return identity
    for prefix in _NOT_A_GATE:
        if command == prefix or command.startswith(f"{prefix} "):
            return None
    # Something that looks like a gate and is in neither table.
    if re.match(r"(ruff|pytest)\b", command) or re.match(
        r"python3? scripts/\S+\.py\b", command
    ):
        raise AssertionError(
            f"CI runs `{command}`, which looks like a check but is in neither "
            "_GATE_IDENTITIES nor _NOT_A_GATE. If it is a gate, check.md must "
            "name it; if it is not, say why in _NOT_A_GATE."
        )
    return None


@functools.cache
def _ci_gates() -> frozenset[str]:
    """Return every gate the gating workflows run."""
    gates: set[str] = set()
    for workflow in _GATE_WORKFLOWS:
        for line in _run_lines(workflow):
            identity = _gate_identity(line)
            if identity is not None:
                gates.add(identity)
    return frozenset(gates)


@functools.cache
def _check_md_gates() -> frozenset[str]:
    """Return every gate `check.md` tells an agent to run."""
    return frozenset(
        identity
        for identity in (_gate_identity(line) for line in _shell_lines(_CHECK))
        if identity is not None
    )


def test_the_gate_scan_finds_the_workflows_it_reads() -> None:
    """Two empty sets would agree with each other."""
    for workflow in _GATE_WORKFLOWS:
        relpath = _rel(workflow)
        assert relpath in _TRACKED, f"{relpath} is not committed"
        assert _run_lines(workflow), f"{relpath} has no run: bodies to read"
    assert len(_ci_gates()) >= 4, f"found almost no CI gates: {sorted(_ci_gates())}"


def test_check_md_names_every_check_ci_runs() -> None:
    """`check.md` claims to be every check CI runs; both directions.

    A gate added to CI and not to `check.md` is a gate an agent finds out
    about from a red pull request. A gate in `check.md` that CI stopped
    running is worse: the local run is then stricter than the thing it exists
    to predict, and the difference is invisible.
    """
    assert _check_md_gates() == _ci_gates(), (
        f"{_rel(_CHECK)} and the gating workflows disagree. "
        f"Run by CI, missing from the command: {sorted(_ci_gates() - _check_md_gates())}. "
        f"In the command, not run by CI: {sorted(_check_md_gates() - _ci_gates())}."
    )


@pytest.mark.parametrize(("command", "reason"), sorted(_NOT_A_GATE.items()))
def test_the_lines_excluded_from_the_gate_list_are_still_excluded(
    command: str, reason: str
) -> None:
    """The exclusion list must not become a way to lose a real gate."""
    script = command.split()[-1]
    assert script in _TRACKED, (
        f"`{command}` is excluded from the gate list ({reason}) but {script} "
        "is no longer committed; drop the exclusion"
    )
    assert any(
        command in line for workflow in _GATE_WORKFLOWS for line in _run_lines(workflow)
    ), f"no gating workflow runs `{command}` any more; drop the exclusion"


def _coverage_source() -> str:
    """Return the package `.coveragerc` measures, as an import path."""
    parser = configparser.ConfigParser()
    parser.read_string(_read(_COVERAGERC))
    return parser.get("run", "source").strip().replace("/", ".")


_COV_TARGET = re.compile(r"--cov=(\S+)")


@pytest.mark.parametrize("command", (_CHECK, _COVER), ids=_rel)
def test_the_measured_package_a_command_names_is_the_measured_package(
    command: Path,
) -> None:
    """A stale `--cov=` target measures the wrong tree and reads as coverage."""
    targets = {
        match.group(1).rstrip("\\").strip()
        for line in _shell_lines(command)
        for match in _COV_TARGET.finditer(line)
    }
    assert targets, f"{_rel(command)} quotes no --cov= target to check"
    assert targets == {_coverage_source()}, (
        f"{_rel(command)} measures {sorted(targets)} but {_rel(_COVERAGERC)} "
        f"sets source = {_coverage_source()}"
    )


def _min_coverage() -> str:
    """Return MIN_COVERAGE as `coverage-gate.yml` sets it."""
    return str(yaml.safe_load(_read(_COVERAGE_GATE))["env"]["MIN_COVERAGE"])


# How each command states the gate. `tests/test_instruction_docs.py` keeps the
# same table for the prose files; these two copies are outside it because
# neither phrasing matches the forms that module recognises.
_THRESHOLD_CLAIMS = {
    _CHECK: re.compile(r"coverage is below (\d+)%"),
    _COVER: re.compile(r"Do not lower the (\d+)% threshold"),
}


@pytest.mark.parametrize(
    ("command", "pattern"),
    sorted(_THRESHOLD_CLAIMS.items(), key=lambda kv: _rel(kv[0])),
    ids=lambda value: _rel(value) if isinstance(value, Path) else "",
)
def test_the_threshold_a_command_quotes_matches_the_workflow(
    command: Path, pattern: re.Pattern
) -> None:
    """Both commands quote the gate; `coverage-gate.yml` decides it."""
    quoted = pattern.findall(_read(command))
    assert len(quoted) == 1, (
        f"{_rel(command)} states the coverage threshold {len(quoted)} time(s), "
        "expected 1. A copy reworded out of the recognised form is a copy "
        "nothing checks - update the pattern or the prose."
    )
    assert quoted[0] == _min_coverage(), (
        f"{_rel(command)} quotes {quoted[0]}% but {_rel(_COVERAGE_GATE)} sets "
        f"MIN_COVERAGE: {_min_coverage()}"
    )


def test_the_threshold_is_quoted_somewhere() -> None:
    """Guard the table above: an empty scan would agree with anything."""
    assert len(_THRESHOLD_CLAIMS) == 2
    assert _min_coverage().isdigit()


def test_the_workflow_the_commands_name_is_the_one_that_holds_the_threshold() -> None:
    """Both commands name `coverage-gate` as the job that fails; it is."""
    assert '--cov-fail-under="${MIN_COVERAGE}"' in _read(_COVERAGE_GATE), (
        f"{_rel(_COVERAGE_GATE)} no longer fails the run on MIN_COVERAGE, so "
        "both commands' 'the coverage-gate workflow will fail' is wrong"
    )
    assert "coverage-gate" in _read(_CHECK)
    assert ".github/workflows/coverage-gate.yml" in _read(_COVER), (
        f"{_rel(_COVER)} no longer names the file that holds the threshold"
    )


# The two checks `check.md` says cannot be reproduced locally, and the action
# that runs each. Both are third-party actions rather than commands, which is
# exactly why a local run cannot include them.
_CI_ONLY_CHECKS = {
    "hassfest": "home-assistant/actions/hassfest",
    "HACS": "hacs/action",
}


@pytest.mark.parametrize(("label", "action"), sorted(_CI_ONLY_CHECKS.items()))
def test_the_ci_only_checks_run_in_ci(label: str, action: str) -> None:
    """`check.md` says these two run in CI. They must actually run."""
    loaded = yaml.safe_load(_read(_VALIDATE))
    used = {
        step["uses"].split("@")[0]
        for job in loaded["jobs"].values()
        for step in job.get("steps", ())
        if "uses" in step
    }
    assert used, f"{_rel(_VALIDATE)} uses no actions; the scan found nothing"
    assert action in used, (
        f"{_rel(_CHECK)} says {label} validation runs in CI, but "
        f"{_rel(_VALIDATE)} no longer uses {action}"
    )
    assert label in _read(_CHECK), (
        f"{_rel(_CHECK)} no longer mentions {label}; drop it from this table "
        "in the same change"
    )


@pytest.mark.parametrize(("label", "action"), sorted(_CI_ONLY_CHECKS.items()))
def test_the_ci_only_checks_are_not_in_the_local_block(label: str, action: str) -> None:
    """A check the command calls unreproducible must not be in its block."""
    lines = _shell_lines(_CHECK)
    assert lines, "the local gate block is empty"
    assert not [line for line in lines if label.lower() in line.lower()], (
        f"{_rel(_CHECK)} says {label} cannot be reproduced locally but its "
        f"command block runs something named {label}"
    )


# The artefacts `check.md` says to flag when a change touches them, because
# only the CI-only checks validate them.
_HASSFEST_ARTEFACTS = (
    "custom_components/sensi/manifest.json",
    "custom_components/sensi/strings.json",
    "custom_components/sensi/translations/en.json",
)


@pytest.mark.parametrize("relpath", _HASSFEST_ARTEFACTS)
def test_the_artefacts_the_ci_only_checks_validate_are_committed(relpath: str) -> None:
    """A renamed artefact leaves the warning pointing at nothing."""
    assert relpath in _TRACKED, (
        f"{_rel(_CHECK)} tells an agent to flag changes to {relpath}, which "
        "is no longer committed"
    )


# --------------------------------------------------------------------------
# add-entity.md: the platform map
# --------------------------------------------------------------------------

# The platform each kind of value goes to, exactly as `add-entity.md` states
# it. The module name is the Home Assistant platform name.
_OFFERED_PLATFORMS = {
    "sensor": "read-only values",
    "binary_sensor": "on/off readings",
    "switch": "a toggleable thermostat setting",
    "number": "a bounded numeric setting",
}

# A supported platform a new entity is not routed to, and why. Asserted below
# to still be supported, so this is not a way to excuse a platform the
# procedure stopped covering.
_PLATFORMS_NOT_OFFERED = {
    "climate": (
        "the thermostat itself rather than an added reading or setting; it is "
        "one entity per device, not a table to extend"
    ),
}


@functools.cache
def _supported_platforms() -> frozenset[str]:
    """Return the platforms `__init__.py` forwards a config entry to."""
    tree = _module_tree("custom_components/sensi/__init__.py")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if "SUPPORTED_PLATFORMS" not in targets:
            continue
        assert isinstance(node.value, ast.List), "SUPPORTED_PLATFORMS is not a list"
        return frozenset(
            element.attr.lower()
            for element in node.value.elts
            if isinstance(element, ast.Attribute)
        )
    raise AssertionError(
        "custom_components/sensi/__init__.py sets no SUPPORTED_PLATFORMS"
    )


def test_the_platform_scan_finds_the_supported_platforms() -> None:
    """Every assertion about the platform map quantifies over this set."""
    assert len(_supported_platforms()) >= 4, (
        f"found {sorted(_supported_platforms())}; the scan is not reading "
        "SUPPORTED_PLATFORMS"
    )


def test_the_platforms_add_entity_offers_are_the_supported_platforms() -> None:
    """A new platform must not arrive without a route into the procedure.

    An agent following `add-entity.md` picks from the four it lists. A fifth
    platform that is set up but never offered is a platform new entities are
    silently never routed to.
    """
    covered = set(_OFFERED_PLATFORMS) | set(_PLATFORMS_NOT_OFFERED)
    assert covered == _supported_platforms(), (
        f"{_rel(_ADD_ENTITY)}'s platform list and SUPPORTED_PLATFORMS "
        f"disagree. Supported but not routed to: "
        f"{sorted(_supported_platforms() - covered)}. Routed to but not "
        f"supported: {sorted(covered - _supported_platforms())}."
    )


@pytest.mark.parametrize("platform", sorted(_PLATFORMS_NOT_OFFERED))
def test_a_platform_listed_as_not_offered_is_still_supported(platform: str) -> None:
    """The exception list must not excuse a platform that left the tree."""
    assert platform in _supported_platforms(), (
        f"{platform} is listed here as deliberately outside "
        f"{_rel(_ADD_ENTITY)}'s platform map but is no longer a supported "
        "platform; drop the exception"
    )


@pytest.mark.parametrize(
    ("platform", "role"), sorted(_OFFERED_PLATFORMS.items()), ids=lambda v: v
)
def test_every_offered_platform_is_named_by_the_command(
    platform: str, role: str
) -> None:
    """The module and the role it is given must both still be in the prose."""
    body = _read(_ADD_ENTITY)
    assert f"`{platform}.py`" in body, (
        f"{_rel(_ADD_ENTITY)} no longer names {platform}.py; drop its row "
        "here in the same change"
    )
    assert role in body, (
        f"{_rel(_ADD_ENTITY)} no longer describes {platform}.py as {role!r}, "
        "so the mapping this table checks is not the one the command states"
    )


@pytest.mark.parametrize("platform", sorted(_OFFERED_PLATFORMS), ids=lambda v: v)
def test_every_offered_platform_builds_entity_descriptions(platform: str) -> None:
    """The command says to add to the module's `*EntityDescription` objects."""
    relpath = f"custom_components/sensi/{platform}.py"
    built = [
        node
        for node in ast.walk(_module_tree(relpath))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.endswith("EntityDescription")
    ]
    assert built, (
        f"{_rel(_ADD_ENTITY)} says to add an entity to {platform}.py's "
        "*EntityDescription objects, but that module constructs none"
    )


@pytest.mark.parametrize("platform", sorted(_OFFERED_PLATFORMS), ids=lambda v: v)
def test_every_offered_platform_has_the_test_module_the_command_names(
    platform: str,
) -> None:
    """`tests/test_<platform>.py` is where step 6 sends the unit test."""
    assert f"tests/test_{platform}.py" in _TRACKED, (
        f"{_rel(_ADD_ENTITY)} sends a new {platform} entity's unit test to "
        f"tests/test_{platform}.py, which is not committed"
    )


def test_the_end_to_end_module_add_entity_names_is_committed() -> None:
    """Step 6's second tier is a named file, not a directory."""
    assert "tests/e2e/test_control.py" in _TRACKED, (
        f"{_rel(_ADD_ENTITY)} sends a write-back entity's end-to-end test to "
        "tests/e2e/test_control.py, which is not committed"
    )


def test_the_capability_gate_add_entity_points_at_still_gates() -> None:
    """The aux heat switch must still be a capability gate to copy.

    The procedure does not describe the pattern; it points at one entity and
    says to do what that one does. If the aux heat switch stops consulting
    `capabilities`, step 3 points at an example of nothing.
    """
    tree = _module_tree("custom_components/sensi/switch.py")
    aux_classes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and "Aux" in node.name
    ]
    assert aux_classes, (
        f"{_rel(_ADD_ENTITY)} points at the aux heat switch as the capability "
        "gate to copy; switch.py defines no aux heat entity"
    )

    availability = [
        node
        for cls in aux_classes
        for node in ast.walk(cls)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == "available"
    ]
    assert availability, (
        "the aux heat switch no longer overrides `available`, so it is not an "
        "example of reporting unavailable on an unsupported model"
    )
    assert any(
        isinstance(node, ast.Attribute) and node.attr == "capabilities"
        for override in availability
        for node in ast.walk(override)
    ), (
        "the aux heat switch's `available` no longer consults `capabilities`, "
        f"so {_rel(_ADD_ENTITY)}'s step 3 points at an example that does not "
        "gate on anything"
    )


# Each helper a command names, and the module it is named as belonging to.
_NAMED_HELPERS = {
    "to_bool": ("custom_components/sensi/utils.py", _ADD_ENTITY),
    "to_int": ("custom_components/sensi/utils.py", _ADD_ENTITY),
    "to_float": ("custom_components/sensi/utils.py", _ADD_ENTITY),
    "State": ("custom_components/sensi/data.py", _ADD_ENTITY),
    "FakeSensiBackend": ("tests/e2e/conftest.py", _COVER),
    "mock_json": ("tests/conftest.py", _COVER),
    "mock_device": ("tests/conftest.py", _COVER),
}


@pytest.mark.parametrize(
    ("name", "relpath", "command"),
    sorted((name, *value) for name, value in _NAMED_HELPERS.items()),
    ids=lambda value: value if isinstance(value, str) else _rel(value),
)
def test_every_symbol_a_command_names_is_defined_where_it_says(
    name: str, relpath: str, command: Path
) -> None:
    """A helper that moved leaves the procedure pointing at the wrong module."""
    assert name in _defined_names(relpath), (
        f"{_rel(command)} tells an agent to use `{name}` from {relpath}, "
        "which defines no such name"
    )
    assert name in _read(command), (
        f"{_rel(command)} no longer names `{name}`; drop its row from "
        "_NAMED_HELPERS in the same change"
    )


def test_the_unique_id_rule_points_at_an_attribute_that_exists() -> None:
    """The one thing `add-entity.md` forbids must still be a real attribute."""
    assert "_attr_unique_id" in _defined_names("custom_components/sensi/entity.py"), (
        f"{_rel(_ADD_ENTITY)} ends with a rule about an entity's `unique_id`, "
        "but entity.py no longer sets one"
    )


# --------------------------------------------------------------------------
# The fixtures both add-entity.md and capture-payload.md are built around
# --------------------------------------------------------------------------

_SAMPLE_FIXTURE_FILES = tuple(
    sorted(
        path
        for path in _TRACKED
        if path.startswith("tests/sample") and path.endswith(".json")
    )
)


def test_the_fixture_scan_finds_the_committed_payloads() -> None:
    """Every assertion about the fixtures quantifies over this list."""
    assert len(_SAMPLE_FIXTURE_FILES) >= 2, (
        f"found {list(_SAMPLE_FIXTURE_FILES)}; the scan is not finding the "
        "committed payload fixtures"
    )
    assert "tests/sample.json" in _SAMPLE_FIXTURE_FILES


@pytest.mark.parametrize(
    "relpath", ("tests/sample.json", "tests/sample_with_humidification.json")
)
def test_the_payloads_add_entity_says_to_search_are_committed(relpath: str) -> None:
    """Step 1 sends an agent to these two files to confirm a value exists."""
    assert relpath in _TRACKED, (
        f"{_rel(_ADD_ENTITY)} says to confirm a new value exists in "
        f"{relpath}, which is not committed"
    )
    assert Path(relpath).name in _read(_ADD_ENTITY), (
        f"{_rel(_ADD_ENTITY)} no longer names {relpath}"
    )


@functools.cache
def _conftest_loaded_files() -> frozenset[str]:
    """Return every sample file `tests/conftest.py` loads."""
    return frozenset(
        node.args[0].value
        for node in ast.walk(_module_tree("tests/conftest.py"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_json"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    )


def test_every_committed_payload_fixture_is_loaded_by_conftest() -> None:
    """`capture-payload.md` step 2 requires a fixture for each new payload.

    A payload committed with no fixture is a file that costs maintenance and
    is read by nothing - which is the outcome step 2 exists to prevent.
    """
    loaded = _conftest_loaded_files()
    assert loaded, "tests/conftest.py loads no sample files; the scan found nothing"
    missing = sorted(
        relpath for relpath in _SAMPLE_FIXTURE_FILES if Path(relpath).name not in loaded
    )
    assert not missing, (
        f"{_rel(_CAPTURE_PAYLOAD)} says a new payload gets a fixture in "
        f"tests/conftest.py alongside `mock_json`; these have none: {missing}"
    )


def test_the_fixture_the_scrub_table_points_at_loads_the_payload_it_names() -> None:
    """The placeholder the table points at is reached through `mock_json`."""
    assert "sample.json" in _conftest_loaded_files(), (
        "tests/conftest.py no longer loads sample.json, which both commands "
        "treat as the payload every other fixture is compared against"
    )


# --------------------------------------------------------------------------
# capture-payload.md: the scrub table
# --------------------------------------------------------------------------

_TABLE_ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$", re.MULTILINE)
_BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")


@functools.cache
def _scrub_table() -> tuple[tuple[str, str], ...]:
    """Return (field, instruction) for every row of the scrub table."""
    rows = []
    for left, right in _TABLE_ROW.findall(_read(_CAPTURE_PAYLOAD)):
        if left in {"Field", "---"} or set(left) <= set("- "):
            continue
        for field in _BACKTICKED.findall(left):
            rows.append((field, right))
    return tuple(rows)


def test_the_scrub_table_parses() -> None:
    """Every assertion about the scrub list quantifies over these rows."""
    fields = {field for field, _ in _scrub_table()}
    assert len(fields) >= 6, (
        f"parsed {sorted(fields)} from {_rel(_CAPTURE_PAYLOAD)}'s table; the "
        "table changed shape and the scan is no longer reading it"
    )
    assert "icd_id" in fields


@functools.cache
def _sample_keys() -> frozenset[str]:
    """Return every key name at any depth in `tests/sample.json`."""

    def walk(value: object) -> set[str]:
        keys: set[str] = set()
        if isinstance(value, dict):
            for key, nested in value.items():
                keys.add(key)
                keys |= walk(nested)
        elif isinstance(value, list):
            for nested in value:
                keys |= walk(nested)
        return keys

    return frozenset(walk(json.loads(_read(_TESTS / "sample.json"))))


@pytest.mark.parametrize(
    ("field", "instruction"), _scrub_table(), ids=lambda v: v.split()[0][:32]
)
def test_every_field_the_scrub_table_names_is_a_field_that_exists(
    field: str, instruction: str
) -> None:
    """A renamed field silently drops off the list of things to scrub.

    The table is the only statement of what carries identity in a captured
    payload. A field the component stopped parsing, or that the backend
    renamed, leaves a row that scrubs nothing - and, worse, leaves the real
    field unlisted.
    """
    leaf = field.rsplit(".", 1)[-1]
    assert leaf in _sample_keys() or leaf in _component_string_constants(), (
        f"{_rel(_CAPTURE_PAYLOAD)} says to scrub `{field}` ({instruction}), "
        "but no committed payload carries that key and no module in "
        "custom_components/sensi/ names it"
    )


# The identifiers the scrub table and `docs/review-rubric.md` both list. Two
# hand-kept copies of the same list drift apart silently; the rubric is what
# a reviewer checks a pull request against.
_SHARED_IDENTIFIERS = (
    "icd_id",
    "serial_number",
    "unique_hardware_id",
    "wifi_mac_address",
)


@pytest.mark.parametrize("field", _SHARED_IDENTIFIERS)
def test_the_scrub_table_and_the_review_rubric_name_the_same_identifiers(
    field: str,
) -> None:
    """What an agent is told to scrub is what a reviewer is told to look for."""
    assert field in {name for name, _ in _scrub_table()}, (
        f"{_rel(_REVIEW_RUBRIC)} tells a reviewer to look for `{field}` but "
        f"{_rel(_CAPTURE_PAYLOAD)} no longer tells anyone to scrub it"
    )
    assert f"`{field}`" in _read(_REVIEW_RUBRIC), (
        f"{_rel(_CAPTURE_PAYLOAD)} tells an agent to scrub `{field}` but "
        f"{_rel(_REVIEW_RUBRIC)} no longer names it"
    )


# The rows whose instruction is "the placeholder already in tests/sample.json",
# as (the name the table writes, where the payload carries it). The table
# writes the registration fields with the prefix on the first one only -
# `registration.address1` / `address2` / ... - so the two spellings are
# separate columns rather than one string split on the dot.
_PLACEHOLDER_FIELDS = (
    ("icd_id", "icd_id"),
    ("registration.address1", "registration.address1"),
    ("address2", "registration.address2"),
    ("postal_code", "registration.postal_code"),
    ("city", "registration.city"),
    ("state", "registration.state"),
)


@pytest.mark.parametrize(("field", "location"), _PLACEHOLDER_FIELDS)
def test_every_placeholder_the_table_points_at_is_in_the_fixture(
    field: str, location: str
) -> None:
    """Each placeholder the table points at must be in the fixture."""
    node: object = json.loads(_read(_TESTS / "sample.json"))
    for segment in location.split("."):
        assert isinstance(node, dict) and segment in node, (
            f"{_rel(_CAPTURE_PAYLOAD)} says to replace `{field}` with the "
            f"placeholder already in tests/sample.json, which carries no "
            f"{location}"
        )
        node = node[segment]
    assert field in {name for name, _ in _scrub_table()}, (
        f"{_rel(_CAPTURE_PAYLOAD)} no longer names `{field}`; drop it from "
        "_PLACEHOLDER_FIELDS in the same change"
    )


_CREDENTIAL_KEYS = ("access_token", "refresh_token", "Authorization")


@pytest.mark.parametrize("relpath", _SAMPLE_FIXTURE_FILES)
def test_no_committed_payload_carries_a_credential_key(relpath: str) -> None:
    """The one row of the table that says to delete rather than replace.

    `tests/test_no_real_identifiers.py` checks that no committed file carries
    a credential *value*. This is the other half: the instruction is to
    remove the key entirely, so a fixture that carries `access_token: null`
    has followed it only halfway and invites the next capture to fill it in.
    """
    payload = json.loads(_read(_ROOT / relpath))

    def keys(value: object) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for key, nested in value.items():
                found.add(key)
                found |= keys(nested)
        elif isinstance(value, list):
            for nested in value:
                found |= keys(nested)
        return found

    present = sorted(set(_CREDENTIAL_KEYS) & keys(payload))
    assert not present, (
        f"{relpath} carries {present}; {_rel(_CAPTURE_PAYLOAD)} says to "
        "remove those keys entirely rather than replace them"
    )


@pytest.mark.parametrize("key", _CREDENTIAL_KEYS)
def test_the_credential_keys_the_table_names_are_still_the_credentials(
    key: str,
) -> None:
    """The delete list must name keys the integration really authenticates with."""
    assert key in _component_string_constants(), (
        f"{_rel(_CAPTURE_PAYLOAD)} says to delete `{key}` from a captured "
        "payload, but no module in custom_components/sensi/ names it, so "
        "either the key was renamed or the row is dead weight"
    )
    assert key in _read(_CAPTURE_PAYLOAD), (
        f"{_rel(_CAPTURE_PAYLOAD)} no longer names `{key}`; drop it from "
        "_CREDENTIAL_KEYS in the same change"
    )


# --------------------------------------------------------------------------
# cover.md: the tiers
# --------------------------------------------------------------------------


def test_the_tiers_cover_md_routes_to_exist() -> None:
    """Step 3 splits new tests between two tiers; both must be committed."""
    assert "tests/conftest.py" in _TRACKED
    assert "tests/e2e/conftest.py" in _TRACKED, (
        f"{_rel(_COVER)} routes connection, emit-loop, coordinator and entity "
        "lifecycle tests to tests/e2e/, which is not committed"
    )
    e2e_modules = sorted(
        path
        for path in _TRACKED
        if path.startswith("tests/e2e/test_") and path.endswith(".py")
    )
    assert e2e_modules, (
        "tests/e2e/ holds no test modules, so the end-to-end tier "
        f"{_rel(_COVER)} routes to is a directory with nothing in it"
    )


def test_the_module_cover_md_uses_as_its_risk_example_still_reconnects() -> None:
    """The reconnect path `cover.md` weighs highest must be a real path."""
    assert "custom_components/sensi/client.py" in _TRACKED
    source = _read(_COMPONENT / "client.py")
    assert "reconnect" in source, (
        f"{_rel(_COVER)} tells an agent to weigh client.py's reconnect path "
        "highest, but client.py no longer has one"
    )
    assert "reconnect" in _read(_COVER), (
        f"{_rel(_COVER)} no longer names the reconnect path; this assertion "
        "is checking a claim the command stopped making"
    )
