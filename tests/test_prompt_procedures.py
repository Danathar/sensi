"""The `.github/prompts/` bodies are procedures, and nothing reads them.

`tests/test_instruction_docs.py` joins the prompt *catalog* to the tree:
`test_prompt_catalog_lists_every_committed_prompt` compares the set of file
names in the `README.md` table against the set of committed prompts. It never
opens a prompt. The bodies are the part that gets followed.

`tests/test_slash_commands.py` did exactly this for `.claude/commands/`. These
files are the same class of artefact one directory over - a procedure an
agent or a maintainer follows without checking - and each one is dense with
hand copies of machine-readable values:

- `upgrade-home-assistant.md` names the pin that decides the Home Assistant
  release, the `ci.yml` step that reports it, every workflow whose
  `python-version` has to move with the Python floor, and the `ruff.toml`
  key that has to move with them.
- `protocol-change.md` names the modules that own payload shape, the setter
  helper a response-shape change lands in, the one method that already
  accepts two response shapes, and the tier each kind of assertion belongs
  to.
- `triage-issue.md` is a runbook keyed on log lines. Four quoted fragments
  route a report to a place in `client.py`, and five lifecycle entries name
  a module and a method each.
- `review.md` restates `docs/review-rubric.md`: seven priority levels in
  order, four verdicts, and the list of things CI already gates and a review
  must therefore not spend itself on.

None of that is checkable by the tools the repository already runs. Markdown
is not measured by `.coveragerc` (`source = custom_components/sensi`), ruff
does not read it, and the prompts are executed by a reader rather than by CI.
So a prompt can name a renamed method, quote a reworded log line, restate a
reordered rubric, or omit a workflow that has to move, and the only reader
affected is the one with no way to notice.

This module is the join. Each test takes a claim a prompt makes and checks it
against the artefact that actually decides it: `.github/workflows/` for the
gates and the Python floor, `ruff.toml` and `requirements_test.txt` for the
pins, `custom_components/sensi/` for every module, method and log line named,
`tests/e2e/` for the fake, `docs/review-rubric.md` for the rubric review.md
restates, and `git ls-files` for every path.

Conventions carried from `tests/test_slash_commands.py`:

- A claim table is a list of claims, not a cache. Dropping a rule from a
  prompt means dropping its row here, in the same diff, where it is
  reviewable - and the table is asserted to cover every committed prompt, so
  a new prompt cannot arrive unread.
- Every scan asserts how much it found before asserting anything about it.
  Every assertion here quantifies over a list some reader built, so a reader
  that silently returns nothing would turn the assertion green.

What is deliberately NOT asserted: that the prompt set and the
`.claude/commands/` set describe the same workflows (they do not overlap
today, and which procedures are worth a slash command is a maintainer's
call), that a prompt names every module or method that exists (a procedure
names the ones it needs), or that the wording of a prompt matches the catalog
row that advertises it.
"""

from __future__ import annotations

import ast
import functools
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent

_PROMPTS_DIR = _ROOT / ".github" / "prompts"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_COMPONENT = _ROOT / "custom_components" / "sensi"
_RUFF_TOML = _ROOT / "ruff.toml"
_REQUIREMENTS_TEST = _ROOT / "requirements_test.txt"
_REVIEW_RUBRIC = _ROOT / "docs" / "review-rubric.md"

_CATALOG = _PROMPTS_DIR / "README.md"
_PROTOCOL_CHANGE = _PROMPTS_DIR / "protocol-change.md"
_REVIEW = _PROMPTS_DIR / "review.md"
_TRIAGE = _PROMPTS_DIR / "triage-issue.md"
_UPGRADE = _PROMPTS_DIR / "upgrade-home-assistant.md"

_CLIENT = "custom_components/sensi/client.py"

# Every committed prompt, and what this module checks about it. A prompt added
# to `.github/prompts/` without a row here fails the coverage guard below
# rather than going unread.
_PROMPTS = {
    "README": "the catalog table: link targets and the directories it points at",
    "protocol-change": "the parsing layer, the setter helper, the two-shape setter",
    "release-readiness": "owned by tests/test_instruction_docs.py and test_release_workflow.py",
    "review": "the rubric order, the verdicts, and the list of gated checks",
    "triage-issue": "the lifecycle map, the log fragments, the e2e fake",
    "upgrade-home-assistant": "the pin, the reporting step, the Python floor",
}


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """Return a file's text with every run of whitespace collapsed to a space.

    The prompts are hard-wrapped prose, so a sentence this module quotes is
    split across lines at whatever column the wrap fell on. Searching the raw
    text for it would make every assertion sensitive to re-wrapping rather
    than to the claim.
    """
    return " ".join(_read(path).split())


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

_PROMPT_FILES = tuple(
    sorted(
        _ROOT / path
        for path in _TRACKED
        if path.startswith(".github/prompts/") and path.endswith(".md")
    )
)


def _rel(path: Path) -> str:
    """Return a path relative to the repository root, for failure messages."""
    return path.relative_to(_ROOT).as_posix()


# A backticked name that looks like a repository path, read the way
# `tests/test_slash_commands.py` reads one. Bare file names are resolved
# against every tracked basename because the prose names component modules by
# name alone (`data.py`, not `custom_components/sensi/data.py`).
_PATH_IN_BACKTICKS = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*"
    r"\.(?:py|json|toml|ini|yml|yaml|md|mdc|txt|sh|cfg)|"
    r"[A-Za-z0-9_][A-Za-z0-9_./-]*/)`"
)


def _named_paths(path: Path) -> list[str]:
    """Return every repository path a prompt names in backticks."""
    return [match.group(1) for match in _PATH_IN_BACKTICKS.finditer(_read(path))]


def _path_exists(name: str) -> bool:
    """Return whether a path named in prose resolves to something committed.

    Three spellings, because the prompts use all three: a repository path, a
    bare file name (the prose writes `data.py`, not
    `custom_components/sensi/data.py`), and a path relative to the component
    package.
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

    An import counts as a definition, for the same reason
    `tests/test_slash_commands.py` counts one: the prose names a symbol by
    where a reader would look for it, not by where it happens to be defined.
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


@functools.cache
def _function(relpath: str, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    """Return the function or method named `name`, which must be defined once."""
    found = [
        node
        for node in ast.walk(_module_tree(relpath))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
    ]
    assert len(found) == 1, f"{relpath} defines {name} {len(found)} times"
    return found[0]


def _calls(node: ast.AST) -> frozenset[str]:
    """Return the attribute and bare names called anywhere inside `node`."""
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Attribute):
            names.add(child.func.attr)
        elif isinstance(child.func, ast.Name):
            names.add(child.func.id)
    return frozenset(names)


@functools.cache
def _workflow(name: str) -> dict:
    """Return a parsed workflow.

    PyYAML resolves an unquoted `on:` key to the boolean True under YAML 1.1,
    which is why nothing here reads triggers by that key.
    """
    loaded = yaml.safe_load(_read(_WORKFLOWS / name))
    assert isinstance(loaded, dict), f"{name} is not a mapping"
    return loaded


def _steps(name: str) -> list[dict]:
    """Return every step of every job in a workflow, flattened."""
    steps = [
        step
        for job in _workflow(name).get("jobs", {}).values()
        for step in job.get("steps", [])
    ]
    assert steps, f"{name} has no steps"
    return steps


# --------------------------------------------------------------------------
# The catalog, and the guard that keeps this module honest
# --------------------------------------------------------------------------


def test_every_committed_prompt_has_a_row_here() -> None:
    """A prompt this module does not name is a prompt nothing reads.

    `tests/test_instruction_docs.py` already asserts the catalog and the
    committed set agree. This is the same guard one layer down: the claim
    table above has to be extended in the diff that adds a prompt.
    """
    committed = {path.stem for path in _PROMPT_FILES}
    assert committed, "no committed prompts found"
    assert committed == set(_PROMPTS), (
        "the claim table and .github/prompts/ disagree. "
        f"Committed but unread here: {sorted(committed - set(_PROMPTS))}. "
        f"Claimed but not committed: {sorted(set(_PROMPTS) - committed)}."
    )


def test_the_catalog_table_link_targets_resolve() -> None:
    """The catalog links by relative path, not only by name.

    `test_prompt_catalog_lists_every_committed_prompt` compares the backticked
    *names* in the table. A row whose link target is misspelled keeps the
    right name and still lands nowhere.
    """
    rows = re.findall(r"\|\s*\[`([a-z-]+\.md)`\]\(([^)]+)\)\s*\|", _read(_CATALOG))
    assert len(rows) == len(_PROMPT_FILES) - 1, (
        f"read {len(rows)} catalog rows for {len(_PROMPT_FILES) - 1} prompts"
    )
    for name, target in rows:
        assert target == name, f"catalog row for {name} links to {target}"
        assert f".github/prompts/{target}" in _TRACKED, (
            f"catalog links to .github/prompts/{target}, which is not committed"
        )


def test_the_catalog_points_at_directories_that_exist() -> None:
    """The catalog routes readers to `.claude/commands/` and `AGENTS.md`."""
    text = _flat(_CATALOG)
    for named in ("`.claude/commands/`", "`AGENTS.md`"):
        assert named in text, f"{_rel(_CATALOG)} no longer names {named}"
    assert ".claude/commands" in _TRACKED_DIRS
    assert "AGENTS.md" in _TRACKED


@pytest.mark.parametrize("prompt", _PROMPT_FILES, ids=lambda path: path.stem)
def test_every_path_a_prompt_names_is_committed(prompt: Path) -> None:
    """A prompt that names a path a reader cannot open has already drifted."""
    named = _named_paths(prompt)
    assert named, f"{_rel(prompt)} names no paths; the reader found nothing"
    missing = sorted({name for name in named if not _path_exists(name)})
    assert not missing, f"{_rel(prompt)} names uncommitted paths: {missing}"


# --------------------------------------------------------------------------
# upgrade-home-assistant.md - the pin, the report, the Python floor
# --------------------------------------------------------------------------


def test_the_harness_pin_the_prompt_describes_is_pinned() -> None:
    """The prompt's whole premise is that one `==` pin decides the HA release."""
    text = _flat(_UPGRADE)
    assert "`pytest-homeassistant-custom-component` in `requirements_test.txt`" in text
    pins = re.findall(
        r"^pytest-homeassistant-custom-component==\S+$",
        _read(_REQUIREMENTS_TEST),
        re.MULTILINE,
    )
    assert len(pins) == 1, f"requirements_test.txt has {len(pins)} harness pins"


def test_the_named_ci_step_that_reports_the_resolved_version_exists() -> None:
    """Step 2 tells the reader to report the resolved `homeassistant` version.

    It routes them to a step by name. A renamed step leaves the instruction
    pointing at nothing, and the reported number is the only evidence the
    bump produced.
    """
    named = re.findall(r"a `([^`]+)` step that prints it", _flat(_UPGRADE))
    assert named == ["Record resolved versions"], f"prompt names {named}"
    matching = [step for step in _steps("ci.yml") if step.get("name") == named[0]]
    assert len(matching) == 1, f"ci.yml has {len(matching)} `{named[0]}` steps"
    # As its own alternative in the grep, not as a substring: every other
    # candidate there (`pytest-homeassistant-custom-component`) contains the
    # word, so a filter that only printed the harness version would satisfy a
    # substring check while reporting nothing about Home Assistant itself.
    assert re.search(r"(?<=[(|])homeassistant(?=[|)])", matching[0]["run"]), (
        f"ci.yml's `{named[0]}` step does not print the homeassistant version"
    )


def _floor_workflows_named_by_the_prompt() -> list[str]:
    """Return the workflow files step 3 says have to move with the floor."""
    sentence = re.search(
        r"If the new Home Assistant raises its minimum Python,(.*?)all have to move",
        _flat(_UPGRADE),
    )
    assert sentence is not None, "the Python-floor sentence is no longer recognisable"
    return re.findall(r"`([a-z-]+\.yml)`", sentence.group(1))


def _workflows_that_build_the_harness() -> set[str]:
    """Return every workflow that sets up Python *and* reads the test pins.

    These are the jobs whose interpreter is decided by what Home Assistant
    requires. `labeler.yml` also pins a `python-version`, but it installs only
    PyYAML to run `scripts/classify_pr.py`, so a Home Assistant floor does not
    reach it - which is why the rule is "pins Python and reads
    requirements_test.txt", not "pins Python".
    """
    found = set()
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        text = _read(path)
        if "python-version" in text and "requirements_test.txt" in text:
            found.add(path.name)
    assert found, "no workflow reads requirements_test.txt; the reader found nothing"
    return found


def test_every_workflow_whose_interpreter_follows_home_assistant_is_named() -> None:
    """Step 3 is a checklist, and an omission from it is a red nightly.

    A workflow left behind at the old `python-version` installs the pinned
    harness on an interpreter Home Assistant no longer supports. That is the
    exact failure the matrix comment in `ci.yml` describes, except nobody was
    told to look.
    """
    named = set(_floor_workflows_named_by_the_prompt())
    assert named == _workflows_that_build_the_harness(), (
        "upgrade-home-assistant.md's Python-floor list and the workflows that "
        "install the pinned harness disagree. "
        f"Installs the harness but unnamed: {sorted(_workflows_that_build_the_harness() - named)}. "
        f"Named but does not install it: {sorted(named - _workflows_that_build_the_harness())}."
    )


def test_the_python_floor_is_one_version_everywhere_the_prompt_names() -> None:
    """They can only move together if they agree now."""
    versions = set()
    for name in _floor_workflows_named_by_the_prompt():
        found = re.findall(
            r'python-version:\s*(?:\[)?"([\d.]+)"', _read(_WORKFLOWS / name)
        )
        assert found, f"{name} pins no python-version"
        versions.update(found)
    assert len(versions) == 1, f"the named workflows pin {sorted(versions)}"


def test_the_ruff_target_version_the_prompt_names_tracks_that_floor() -> None:
    """Step 3 puts `target-version` in the same move as `python-version`."""
    assert "`target-version` in `ruff.toml`" in _flat(_UPGRADE)
    target = re.search(r'^target-version\s*=\s*"py(\d)(\d+)"', _read(_RUFF_TOML), re.M)
    assert target is not None, "ruff.toml sets no target-version"
    floor = re.findall(
        r'python-version:\s*\["([\d.]+)"\]', _read(_WORKFLOWS / "ci.yml")
    )
    assert len(floor) == 1, f"ci.yml's matrix reads {floor}"
    assert f"{target.group(1)}.{target.group(2)}" == floor[0], (
        f"ruff targets py{target.group(1)}{target.group(2)} "
        f"while the matrix runs {floor[0]}"
    )


def test_the_matrix_carries_one_interpreter_and_the_comment_the_prompt_cites() -> None:
    """Step 3 tells the reader not to add a second interpreter, and why.

    It defers the reasoning to "the matrix comment in `ci.yml`". If the
    comment goes, the instruction becomes an unexplained prohibition.
    """
    assert "The matrix comment in `ci.yml` explains why" in _flat(_UPGRADE)
    matrix = _workflow("ci.yml")["jobs"]["pytest"]["strategy"]["matrix"]
    assert len(matrix["python-version"]) == 1, (
        f"ci.yml's matrix runs {matrix['python-version']}; the prompt says one"
    )
    # The comment block directly above the matrix key, not every comment in
    # the file: the prompt sends a reader to *the matrix comment*, and the
    # surrounding steps carry comments of their own that would satisfy a
    # file-wide search after the matrix comment was deleted.
    lines = _read(_WORKFLOWS / "ci.yml").splitlines()
    index = next(
        position
        for position, line in enumerate(lines)
        if line.lstrip().startswith("python-version: [")
    )
    block = []
    while index and lines[index - 1].lstrip().startswith("#"):
        index -= 1
        block.insert(0, lines[index].lstrip("# ").strip())
    assert len(block) >= 4, f"the matrix comment is {len(block)} lines"
    joined = " ".join(block)
    for needle in ("older", "pip", "Home Assistant"):
        assert needle in joined, f"the matrix comment no longer mentions {needle!r}"


def test_hassfest_is_a_committed_job_the_prompt_can_tell_you_to_re_run() -> None:
    """Step 5 says to re-run hassfest; something has to run it."""
    assert "Re-run hassfest" in _flat(_UPGRADE)
    uses = [
        step.get("uses", "")
        for step in _steps("validate.yml")
        if "hassfest" in step.get("uses", "")
    ]
    assert len(uses) == 1, f"validate.yml has {len(uses)} hassfest steps"


def test_the_coverage_gate_the_prompt_warns_about_exists() -> None:
    """Step 6 says the gate can fail on a harness bump, so a gate must exist."""
    assert "If the gate fails" in _flat(_UPGRADE)
    gate = _read(_WORKFLOWS / "coverage-gate.yml")
    assert "MIN_COVERAGE:" in gate
    assert "--cov-fail-under" in gate


# --------------------------------------------------------------------------
# protocol-change.md - the parsing layer and the setter path
# --------------------------------------------------------------------------


def test_the_parsing_layer_the_prompt_names_is_the_committed_one() -> None:
    """Step 3 sends every payload-shape fix to three modules by name.

    A fix landing anywhere else is the leak the step describes, so the three
    names have to be real modules.
    """
    named = re.findall(
        r"`(\w+\.py)`, `(\w+\.py)` and `(\w+\.py)` own payload shape",
        _flat(_PROTOCOL_CHANGE),
    )
    assert len(named) == 1, "the parsing-layer sentence is no longer recognisable"
    assert set(named[0]) == {"data.py", "capabilities.py", "event.py"}
    for module in named[0]:
        assert f"custom_components/sensi/{module}" in _TRACKED, (
            f"protocol-change.md routes payload fixes to {module}, which is gone"
        )


def test_the_scrubbing_command_the_prompt_defers_to_exists() -> None:
    """Step 1 owns no scrub rules; it points at the command that does."""
    assert "`.claude/commands/capture-payload.md` for the scrubbing rules" in _flat(
        _PROTOCOL_CHANGE
    )
    assert ".claude/commands/capture-payload.md" in _TRACKED


def test_the_baseline_fixture_the_prompt_diffs_against_is_a_payload() -> None:
    """Steps 1 and 5 both key on `tests/sample.json` being the baseline."""
    assert "`tests/sample.json`" in _flat(_PROTOCOL_CHANGE)
    assert "tests/sample.json" in _TRACKED
    loaded = json.loads(_read(_ROOT / "tests" / "sample.json"))
    assert isinstance(loaded, dict) and loaded, "tests/sample.json is not a payload"


def test_the_two_shape_setter_the_prompt_cites_still_accepts_both() -> None:
    """Step 2 holds up one method as the pattern to copy.

    "the way `async_set_operating_mode` already accepts either a string or a
    dict" is a claim about code. If that method stops branching on the
    response type, the prompt is teaching a pattern the tree no longer shows.
    """
    assert (
        "`async_set_operating_mode` already accepts either a string or a dict"
        in _flat(_PROTOCOL_CHANGE)
    )
    method = _function(_CLIENT, "async_set_operating_mode")
    checks = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        and any(isinstance(arg, ast.Name) and arg.id == "str" for arg in node.args[1:])
    ]
    assert checks, (
        "async_set_operating_mode no longer branches on a string response; "
        "protocol-change.md cites it as the both-shapes pattern"
    )


def test_every_setter_goes_through_the_helper_the_prompt_names() -> None:
    """Step 6 says a response-shape change "usually hits `_async_invoke_setter`".

    That is only a useful pointer while the setters actually funnel through
    it. A setter that talks to the socket directly is one the step would send
    a reader straight past.
    """
    assert "`_async_invoke_setter`" in _flat(_PROTOCOL_CHANGE)
    setters = [
        node.name
        for node in ast.walk(_module_tree(_CLIENT))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("async_set_")
    ]
    assert len(setters) >= 5, f"only {len(setters)} setters found in client.py"
    direct = [
        name
        for name in setters
        if "_async_invoke_setter" not in _calls(_function(_CLIENT, name))
    ]
    assert not direct, f"setters bypassing _async_invoke_setter: {sorted(direct)}"


def test_the_error_code_degradation_the_prompt_promises_is_implemented() -> None:
    """Step 2 fixes the failure mode: a new error code becomes an error, not a crash."""
    assert "becomes a `HomeAssistantError` with the code in the message" in _read(
        _PROTOCOL_CHANGE
    )
    raises = [
        node
        for node in ast.walk(_module_tree(_CLIENT))
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "HomeAssistantError"
    ]
    assert raises, "client.py raises no HomeAssistantError"


def test_the_control_tier_the_prompt_routes_setter_work_to_asserts_emissions() -> None:
    """Step 6 names one file as where setter behaviour "gets asserted".

    It goes further and says it is asserted "on the emitted payload", which
    is a claim about how that file tests, not only that it exists.
    """
    assert "`tests/e2e/test_control.py` is where that gets asserted" in _read(
        _PROTOCOL_CHANGE
    )
    assert "tests/e2e/test_control.py" in _TRACKED
    assert "emit" in _read(_ROOT / "tests" / "e2e" / "test_control.py")


# --------------------------------------------------------------------------
# triage-issue.md - the lifecycle map and the log fragments
# --------------------------------------------------------------------------

# Step 2's lifecycle table, as the prompt writes it: a stage, and the modules
# and methods it sends the reader to. Dropping a row means dropping it here.
_LIFECYCLE = {
    "config flow / authentication": ("config_flow.py", "auth.py"),
    "initial setup": ("__init__.py",),
    "connection dropped mid-session": ("client.py",),
    "coordinator update failing": ("coordinator.py",),
    "one entity wrong": ("data.py",),
}

# Every `client.<name>` the lifecycle table routes to, and the bare method
# name step 2 spells without the receiver.
_CLIENT_MEMBERS = (
    "wait_for_devices",
    "_connect",
    "_async_disconnect",
    "async_update_devices",
)


@pytest.mark.parametrize(
    "module", sorted({m for row in _LIFECYCLE.values() for m in row})
)
def test_the_lifecycle_map_names_committed_modules(module: str) -> None:
    """Step 2 is the routing table for every report; each stop must exist."""
    assert f"`{module}`" in _flat(_TRIAGE), f"triage-issue.md no longer names {module}"
    assert f"custom_components/sensi/{module}" in _TRACKED


@pytest.mark.parametrize("member", _CLIENT_MEMBERS)
def test_every_client_member_the_runbook_routes_to_is_defined(member: str) -> None:
    """A renamed method turns a routing table row into a dead end."""
    assert member in _flat(_TRIAGE), f"triage-issue.md no longer names {member}"
    assert member in _defined_names(_CLIENT), f"client.py no longer defines {member}"


def test_the_runbook_names_no_client_member_that_is_gone() -> None:
    """The reverse direction: every `client.x` the prose spells resolves.

    The parametrised test above quantifies over a hand-kept tuple. This one
    quantifies over what the file actually says, so a member added to the
    prose cannot slip in unchecked.
    """
    spelled = set(re.findall(r"`client\.(?!py`)(\w+)`", _flat(_TRIAGE)))
    assert spelled, "the runbook spells no client members; the reader found nothing"
    missing = sorted(spelled - _defined_names(_CLIENT))
    assert not missing, f"triage-issue.md routes to missing client members: {missing}"


# The four log fragments step 2 says "each pin it to a different place", and
# whether the fragment is this component's own text. `Engine.IO connection
# dropped` is python-socketio's, so it is only ever quoted in the annotated
# samples; the other three are strings this repository owns and can reword.
_LOG_FRAGMENTS = {
    "Engine.IO connection dropped": False,
    "jwt expired": True,
    "Timed out waiting for event": True,
    "Updating devices - reconnecting": True,
}


@pytest.mark.parametrize("fragment", sorted(_LOG_FRAGMENTS))
def test_every_quoted_log_fragment_appears_in_the_client(fragment: str) -> None:
    """The runbook is keyed on log text; text nobody emits routes nobody."""
    assert f"`{fragment}`" in _flat(_TRIAGE), (
        f"triage-issue.md no longer quotes {fragment!r}"
    )
    assert fragment in _read(_ROOT / _CLIENT), (
        f"client.py no longer contains {fragment!r}, which triage-issue.md "
        "uses to place a failure"
    )


@pytest.mark.parametrize(
    "fragment", sorted(name for name, owned in _LOG_FRAGMENTS.items() if owned)
)
def test_the_fragments_this_repository_owns_are_live_strings(fragment: str) -> None:
    """Three of the four are this component's own text, not sample transcript.

    Those are the ones a refactor can reword. Finding them only inside the
    commented log samples would mean the component stopped emitting them
    while the samples - and the runbook - kept saying it did.
    """
    live = "\n".join(
        line
        for line in _read(_ROOT / _CLIENT).splitlines()
        if not line.lstrip().startswith("#")
    )
    assert fragment in live, (
        f"{fragment!r} survives only in a comment; client.py no longer emits it"
    )


def test_the_annotated_log_samples_the_runbook_defers_to_exist() -> None:
    """Step 2 ends by sending the reader to samples inside `client.py`."""
    assert "`client.py` carries annotated log samples" in _flat(_TRIAGE)
    samples = [
        line
        for line in _read(_ROOT / _CLIENT).splitlines()
        if line.lstrip().startswith("#") and "[custom_components.sensi]" in line
    ]
    assert len(samples) >= 5, f"client.py carries {len(samples)} annotated log lines"


def test_the_logger_namespace_the_runbook_asks_for_is_the_real_one() -> None:
    """The prompt's Input section asks for `custom_components.sensi` log lines.

    That is the logger name only because `const.py` builds it from
    `__package__`. A logger renamed there makes the requested lines
    unobtainable.
    """
    assert "`custom_components.sensi` log lines" in _flat(_TRIAGE)
    const = _read(_COMPONENT / "const.py")
    assert re.search(r"^LOGGER = logging\.getLogger\(__package__\)", const, re.M), (
        "const.py no longer derives LOGGER from __package__"
    )
    assert (_COMPONENT / "__init__.py").exists()


def test_the_e2e_fake_the_runbook_says_to_script_exists() -> None:
    """Step 4 claims almost every case reproduces by scripting one class."""
    assert "scripting `FakeSensiBackend` in `tests/e2e/`" in _flat(_TRIAGE)
    classes = {
        node.name
        for node in ast.walk(_module_tree("tests/e2e/conftest.py"))
        if isinstance(node, ast.ClassDef)
    }
    assert "FakeSensiBackend" in classes, (
        f"tests/e2e/conftest.py defines {sorted(classes)}"
    )


def test_the_runbook_hands_protocol_changes_to_the_other_prompt() -> None:
    """Step 3 is a handoff, and a handoff to a missing file is a dead end."""
    assert "switch to `protocol-change.md`" in _flat(_TRIAGE)
    assert ".github/prompts/protocol-change.md" in _TRACKED


# --------------------------------------------------------------------------
# review.md - the rubric, restated
# --------------------------------------------------------------------------


def _rubric_levels() -> list[str]:
    """Return the rubric's priority headings, lowercased, in file order."""
    found = [
        (int(number), text.strip())
        for number, text in re.findall(
            r"^### (\d+)\.\s+(.+)$", _read(_REVIEW_RUBRIC), re.MULTILINE
        )
    ]
    assert found, "docs/review-rubric.md has no numbered priority headings"
    assert [number for number, _ in found] == list(range(1, len(found) + 1)), (
        f"the rubric's headings are numbered {[n for n, _ in found]}"
    )
    return [re.split(r"\s+[—-]{1,2}\s+", text)[0].lower() for _, text in found]


def _review_levels() -> list[str]:
    """Return review.md's restated priority list, lowercased, in file order."""
    block = re.search(
        r"stop escalating once you have a\s+blocking finding:\n(.*?)\n\n3\.",
        _read(_REVIEW),
        re.DOTALL,
    )
    assert block is not None, "review.md's priority list is no longer recognisable"
    found = [
        (int(number), text.strip())
        for number, text in re.findall(r"^\s+(\d+)\.\s+(.+)$", block.group(1), re.M)
    ]
    assert found, "review.md's priority list read as empty"
    assert [number for number, _ in found] == list(range(1, len(found) + 1)), (
        f"review.md's list is numbered {[n for n, _ in found]}"
    )
    return [re.split(r"\s+[—-]{1,2}\s+", text)[0].lower() for _, text in found]


def test_the_review_prompt_restates_every_rubric_level() -> None:
    """The prompt is a second copy of the rubric's priority order.

    A level added to `docs/review-rubric.md` and not here is a level the
    assistant reviewing against that rubric never checks.
    """
    assert len(_review_levels()) == len(_rubric_levels()), (
        f"review.md lists {len(_review_levels())} levels, "
        f"the rubric has {len(_rubric_levels())}"
    )


def test_the_restated_levels_are_in_the_rubrics_order() -> None:
    """Order is the point: "a finding at a higher level outranks anything below"."""
    for position, (restated, heading) in enumerate(
        zip(_review_levels(), _rubric_levels(), strict=True), start=1
    ):
        assert heading.startswith(restated), (
            f"review.md level {position} is {restated!r}; "
            f"the rubric's level {position} is {heading!r}"
        )


def test_the_review_prompt_and_the_rubric_link_to_each_other() -> None:
    """Each file sends the reader to the other by relative path."""
    assert "[`docs/review-rubric.md`](../../docs/review-rubric.md)" in _flat(_REVIEW)
    assert "docs/review-rubric.md" in _TRACKED
    assert "[`.github/prompts/review.md`](../.github/prompts/review.md)" in _flat(
        _REVIEW_RUBRIC
    )
    assert ".github/prompts/review.md" in _TRACKED


def _rubric_verdicts() -> list[str]:
    """Return the verdicts from the rubric's table, in row order."""
    found = re.findall(r"^\|\s*\*\*([A-Za-z ]+)\*\*\s*\|", _read(_REVIEW_RUBRIC), re.M)
    assert found, "docs/review-rubric.md has no verdict table"
    return found


def test_the_review_prompt_offers_exactly_the_rubrics_verdicts() -> None:
    """The prompt closes by demanding "one verdict", from a hand-copied list."""
    sentence = re.search(
        r"Then one verdict — (.*?) — and the single reason", _flat(_REVIEW)
    )
    assert sentence is not None, (
        "review.md's verdict sentence is no longer recognisable"
    )
    offered = re.findall(r"\*\*([A-Za-z ]+)\*\*", sentence.group(1))
    assert offered == _rubric_verdicts(), (
        f"review.md offers {offered}; the rubric defines {_rubric_verdicts()}"
    )


# What review.md's step 1 says is "gated", and the committed job that gates
# it. A term here with no gate behind it tells a reviewer to skip a check
# nothing performs.
_GATED = {
    "formatting": ("validate.yml", "ruff format --check"),
    "lint": ("validate.yml", "ruff check"),
    "coverage threshold": ("coverage-gate.yml", "--cov-fail-under"),
    "manifest validity": ("validate.yml", "hassfest"),
    "HACS metadata": ("validate.yml", "hacs/action"),
    "requirements sync": ("validate.yml", "scripts/check_requirements_sync.py"),
}


def test_step_one_lists_exactly_the_gates_this_module_checks() -> None:
    """The claim table is read out of the prompt, not remembered."""
    sentence = re.search(r"(Formatting,.*?) are gated\.", _flat(_REVIEW))
    assert sentence is not None, "review.md's gated-checks sentence is gone"
    listed = re.split(r", | and ", sentence.group(1))
    assert [item.lower() for item in listed] == [term.lower() for term in _GATED], (
        f"review.md lists {listed}; this module checks {list(_GATED)}"
    )


@pytest.mark.parametrize(("term", "gate"), sorted(_GATED.items()))
def test_everything_review_is_told_not_to_re_check_is_gated(
    term: str, gate: tuple[str, str]
) -> None:
    """Step 1 is only sound while each gate it names actually runs."""
    workflow, needle = gate
    assert needle in _read(_WORKFLOWS / workflow), (
        f"review.md calls {term} gated, but {workflow} no longer runs {needle!r}"
    )


def test_the_lifecycle_exceptions_the_rubric_level_names_are_used() -> None:
    """Level 4 turns on choosing between two Home Assistant exceptions.

    Both have to be reachable in this component for the distinction to be a
    review finding rather than a hypothetical.
    """
    text = _flat(_REVIEW)
    for name in ("ConfigEntryAuthFailed", "ConfigEntryNotReady"):
        assert f"`{name}`" in text, f"review.md no longer names {name}"
        used = any(
            name in _defined_names(f"custom_components/sensi/{module.name}")
            for module in _COMPONENT.glob("*.py")
        )
        assert used, f"nothing in the component imports {name}"


def test_the_breaking_change_surfaces_level_two_names_exist() -> None:
    """Level 2 names three concrete things a change can break."""
    text = _flat(_REVIEW)
    assert "entity `unique_id`" in text
    assert "hand-edited `manifest.json` version" in text
    assert "unique_id" in _read(_COMPONENT / "entity.py"), (
        "entity.py no longer sets a unique_id"
    )
    manifest = json.loads(_read(_COMPONENT / "manifest.json"))
    assert "version" in manifest, "manifest.json carries no version to hand-edit"
