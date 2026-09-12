"""The instruction files restate the repository's rules, and nothing checks them.

`AGENTS.md` is the canonical instruction file. `CLAUDE.md`,
`.github/copilot-instructions.md` and `.cursor/rules/sensi.mdc` each say so in
their own words and then repeat the rules anyway, because the tools that read
them do not follow a pointer. `CONTRIBUTING.md` says the same things for
humans, and `docs/` restates the gate list a third time. That is deliberate -
and it means every rule in this repository exists as five or six prose copies
of a value that lives somewhere else in machine-readable form.

Every one of those copies drifts silently. Nothing errors when
`MIN_COVERAGE` moves and four markdown files keep quoting the old number, when
a module is added and the layout block stops describing the package, when a
prompt is committed and the catalog does not list it, or when a file the
instructions point at is renamed. The reader most affected is an agent, which
has no way to notice that what it was told is no longer true.

This module is the join. Each test takes a claim the prose makes and checks it
against the artefact that actually decides it: `coverage-gate.yml` for the
coverage gate, `ruff.toml` for the line length, `ci.yml` for the interpreter,
`.github/rulesets/master.json` for the required checks, the committed tree for
every path and symbol named, and `release.yml` for how a version is chosen.

Two conventions carried from `tests/test_codeowners.py`:

- A claim table is a list of claims, not a cache. Dropping a rule from the
  prose means dropping its row here, in the same diff, where it is reviewable.
- Every assertion that quantifies over a list built by this module is vacuous
  if the list comes back empty, so each scan asserts how much it found before
  asserting anything about it.

What is deliberately NOT asserted: that every source module has prose about
it, that the wording matches between copies, or that `const.py` has a test
module (it has none, and whether constants need one is a judgement, not an
invariant).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import tomllib

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent

_AGENTS = _ROOT / "AGENTS.md"
_CLAUDE = _ROOT / "CLAUDE.md"
_CONTRIBUTING = _ROOT / "CONTRIBUTING.md"
_COPILOT = _ROOT / ".github" / "copilot-instructions.md"
_CURSOR = _ROOT / ".cursor" / "rules" / "sensi.mdc"
_PROMPT_CATALOG = _ROOT / ".github" / "prompts" / "README.md"
_QUALITY_DOC = _ROOT / "docs" / "quality.md"
_METRICS_DOC = _ROOT / "docs" / "metrics.md"
_BRANCH_PROTECTION_DOC = _ROOT / "docs" / "branch-protection.md"
_REVIEW_RUBRIC = _ROOT / "docs" / "review-rubric.md"
_SECURITY_AI_DOC = _ROOT / "docs" / "SECURITY-AI.md"
_README = _ROOT / "README.md"

_COMPONENT = _ROOT / "custom_components" / "sensi"
_COVERAGE_GATE = _ROOT / ".github" / "workflows" / "coverage-gate.yml"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_RULESET = _ROOT / ".github" / "rulesets" / "master.json"
_RUFF_TOML = _ROOT / "ruff.toml"
_HACS = _ROOT / "hacs.json"
_METADATA_TEST = _ROOT / "tests" / "test_metadata.py"

# The files that restate AGENTS.md rather than linking to it. CONTRIBUTING.md
# is the human-facing half of the same content and drifts the same way, so it
# is scanned with them.
_INSTRUCTION_FILES = (_AGENTS, _CLAUDE, _CONTRIBUTING, _COPILOT, _CURSOR)

# Every markdown (and Cursor `.mdc`) file whose prose is checked here.
_PROSE_FILES = (
    *_INSTRUCTION_FILES,
    _PROMPT_CATALOG,
    _QUALITY_DOC,
    _METRICS_DOC,
    _BRANCH_PROTECTION_DOC,
    _REVIEW_RUBRIC,
    _SECURITY_AI_DOC,
    _README,
)


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    """Return a repository-relative path, for readable test ids."""
    return path.relative_to(_ROOT).as_posix()


def _tracked_files() -> frozenset[str]:
    """Return every path git tracks, read from git rather than a walk.

    A walk would see `coverage.xml`, `__pycache__` and any local virtualenv,
    so a path the instructions name could "exist" without being committed.
    """
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return frozenset(part for part in out.split("\0") if part)


_TRACKED = _tracked_files()
_TRACKED_DIRS = frozenset(
    parent.as_posix() for path in _TRACKED for parent in Path(path).parents
)
_TRACKED_BASENAMES = frozenset(Path(path).name for path in _TRACKED)


def test_the_tracked_file_index_is_not_empty() -> None:
    """Guard the index every path assertion below quantifies over."""
    assert len(_TRACKED) > 50, (
        "git ls-files returned almost nothing, so every path assertion in "
        "this module would pass without checking anything"
    )
    assert "AGENTS.md" in _TRACKED


# --------------------------------------------------------------------------
# Paths the prose names
# --------------------------------------------------------------------------

# A backticked name that looks like a repository path. Bare file names
# (`auth.py`, `manifest.json`) are resolved against every tracked basename,
# because the prose refers to component modules by name alone.
_PATH_IN_BACKTICKS = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*"
    r"\.(?:py|json|toml|ini|yml|yaml|md|mdc|txt|sh|cfg)|"
    r"[A-Za-z0-9_][A-Za-z0-9_./-]*/)`"
)

# Paths the instructions name *in order to say they are absent or forbidden*.
# Each is asserted below to really be absent, so this is not a way to excuse a
# path that quietly stopped existing.
_DOCUMENTED_AS_ABSENT = {
    "pyproject.toml": "gitignored on purpose - the devcontainer image supplies it",
    "secrets.yaml": "gitignored; reading it is denied in .claude/settings.json",
    "config/": "gitignored devcontainer state, not a committed directory",
    ".env": "gitignored; reading it is denied in .claude/settings.json",
}


def _named_paths(path: Path) -> list[str]:
    """Return every repository path the file names in backticks."""
    return [match.group(1) for match in _PATH_IN_BACKTICKS.finditer(_read(path))]


def _path_exists(name: str) -> bool:
    """Return whether a path named in prose resolves to something committed."""
    cleaned = name.rstrip("/")
    if cleaned in _TRACKED or cleaned in _TRACKED_DIRS:
        return True
    # A bare file name with no separator refers to a module by name; the prose
    # writes `auth.py`, not `custom_components/sensi/auth.py`.
    return "/" not in cleaned and cleaned in _TRACKED_BASENAMES


@pytest.mark.parametrize("doc", _INSTRUCTION_FILES, ids=_rel)
def test_every_path_the_instructions_name_is_committed(doc: Path) -> None:
    """A path renamed out from under the instructions must fail here."""
    named = _named_paths(doc)
    assert len(named) >= 5, (
        f"{_rel(doc)}: found only {len(named)} backticked paths, so the scan "
        "below is not looking at the file it thinks it is"
    )

    missing = sorted(
        {
            name
            for name in named
            if name not in _DOCUMENTED_AS_ABSENT and not _path_exists(name)
        }
    )
    assert not missing, (
        f"{_rel(doc)} names paths that are not committed: {missing}. Either "
        "the file was renamed and the instructions still point at the old "
        "name, or the path is deliberately absent and belongs in "
        "_DOCUMENTED_AS_ABSENT with the reason."
    )


@pytest.mark.parametrize(
    ("name", "reason"), sorted(_DOCUMENTED_AS_ABSENT.items()), ids=lambda v: v[:24]
)
def test_paths_documented_as_absent_really_are_absent(name: str, reason: str) -> None:
    """The absent-path list must not become a way to excuse a lost file."""
    cleaned = name.rstrip("/")
    assert cleaned not in _TRACKED and cleaned not in _TRACKED_DIRS, (
        f"`{name}` is committed, but the instructions describe it as absent "
        f"({reason}). Either the rule changed or the file should not be here."
    )


def test_pyproject_is_gitignored_as_four_instruction_files_claim() -> None:
    """Keep the "do not add `pyproject.toml`, it is gitignored" rule true."""
    ignored = _read(_ROOT / ".gitignore").splitlines()
    assert "pyproject.toml" in [line.strip() for line in ignored], (
        "AGENTS.md, CLAUDE.md, copilot-instructions.md and .cursor/rules all "
        "say pyproject.toml is gitignored on purpose; .gitignore no longer "
        "lists it, so creating one would now be committable"
    )
    for doc in (_AGENTS, _CLAUDE, _COPILOT, _CURSOR):
        assert "pyproject.toml" in _read(doc), (
            f"{_rel(doc)} no longer carries the pyproject.toml rule; drop it "
            "from this test in the same change that drops it from the prose"
        )


_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


@pytest.mark.parametrize("doc", _PROSE_FILES, ids=_rel)
def test_relative_markdown_links_resolve(doc: Path) -> None:
    """A moved document must not leave a dead link in the instructions."""
    targets = [
        match.group(1)
        for match in _MARKDOWN_LINK.finditer(_read(doc))
        if not match.group(1).startswith(("http://", "https://", "mailto:", "#"))
    ]
    broken = sorted(
        target for target in targets if not (doc.parent / target.split("#")[0]).exists()
    )
    assert not broken, f"{_rel(doc)} links to missing paths: {broken}"


# --------------------------------------------------------------------------
# Numbers the prose quotes
# --------------------------------------------------------------------------


def _coverage_threshold() -> str:
    """Return MIN_COVERAGE as coverage-gate.yml sets it."""
    workflow = yaml.safe_load(_read(_COVERAGE_GATE))
    return str(workflow["env"]["MIN_COVERAGE"])


# Each pattern captures a percentage that is *the coverage gate*, not one of
# the historical numbers docs/quality.md and README.md quote in their
# before/after tables. Keeping the patterns narrow is what makes the counts
# below meaningful.
_GATE_PERCENTAGE = re.compile(
    r"gated at (\d+)%|the gate is (\d+)%|≥(\d+)% of|"
    r"line-coverage floor of (\d+)%|floor, currently (\d+)%|the (\d+)% coverage gate",
    re.IGNORECASE,
)

# How many times each file states the gate. A rewording that drops the number
# out of the recognised forms lands here, which is the point: the copy is
# either checkable or it is not.
_GATE_COPIES = {
    _AGENTS: 1,
    _CLAUDE: 1,
    _COPILOT: 1,
    _CURSOR: 1,
    _QUALITY_DOC: 2,
    _METRICS_DOC: 1,
    _README: 2,
}


@pytest.mark.parametrize(
    ("doc", "expected_copies"), sorted(_GATE_COPIES.items(), key=lambda kv: _rel(kv[0]))
)
def test_quoted_coverage_gate_matches_the_workflow(
    doc: Path, expected_copies: int
) -> None:
    """Every prose copy of the gate must equal MIN_COVERAGE."""
    quoted = [
        next(group for group in match.groups() if group)
        for match in _GATE_PERCENTAGE.finditer(_read(doc))
    ]
    assert len(quoted) == expected_copies, (
        f"{_rel(doc)} states the coverage gate {len(quoted)} time(s), expected "
        f"{expected_copies}. A copy that was reworded out of a recognised form "
        "is a copy nothing checks - update the pattern or the count."
    )
    threshold = _coverage_threshold()
    assert set(quoted) == {threshold}, (
        f"{_rel(doc)} quotes {sorted(set(quoted))} but "
        f"{_rel(_COVERAGE_GATE)} sets MIN_COVERAGE: {threshold}"
    )


def test_the_coverage_gate_is_quoted_somewhere() -> None:
    """Guard the table above: an empty scan would agree with anything."""
    assert sum(_GATE_COPIES.values()) >= 6
    assert _coverage_threshold().isdigit()


_LINE_LENGTH_CLAIMS = (
    (_COPILOT, re.compile(r"[Ll]ine length (\d+)")),
    (_CURSOR, re.compile(r"(\d+)-column lines")),
)


@pytest.mark.parametrize(
    ("doc", "pattern"),
    _LINE_LENGTH_CLAIMS,
    ids=lambda v: _rel(v) if isinstance(v, Path) else "pattern",
)
def test_quoted_line_length_matches_ruff_toml(doc: Path, pattern: re.Pattern) -> None:
    """The two files that quote the line length must match `ruff.toml`."""
    ruff = tomllib.loads(_read(_RUFF_TOML))
    quoted = pattern.findall(_read(doc))
    assert len(quoted) == 1, (
        f"{_rel(doc)} states the line length {len(quoted)} time(s); the join "
        "to ruff.toml only works while the statement is findable"
    )
    assert int(quoted[0]) == ruff["line-length"], (
        f"{_rel(doc)} says {quoted[0]} columns, ruff.toml sets {ruff['line-length']}"
    )


def _ci_python_versions() -> list[str]:
    """Return the interpreter versions ci.yml runs the suite on."""
    workflow = yaml.safe_load(_read(_CI))
    matrix = workflow["jobs"]["pytest"]["strategy"]["matrix"]["python-version"]
    return [str(version) for version in matrix]


_PYTHON_FLOOR_CLAIMS = (
    (_AGENTS, "Python >= 3.14.2"),
    (_CLAUDE, "Python 3.14+"),
    (_CONTRIBUTING, "Python >= 3.14.2"),
    (_COPILOT, "Python 3.14+"),
    (_CURSOR, "Python 3.14+"),
)


@pytest.mark.parametrize(
    ("doc", "claim"),
    _PYTHON_FLOOR_CLAIMS,
    ids=lambda v: _rel(v) if isinstance(v, Path) else v,
)
def test_quoted_interpreter_floor_matches_ci(doc: Path, claim: str) -> None:
    """Each file's interpreter claim must be present and match the CI matrix."""
    assert claim in _read(doc), (
        f"{_rel(doc)} no longer states the interpreter floor as {claim!r}; if "
        "the floor moved, this table moves with it"
    )
    versions = _ci_python_versions()
    assert versions == ["3.14"], (
        f"ci.yml runs on {versions}; the instruction files all say 3.14, and "
        "the required check name is 'pytest (Python 3.14)'"
    )
    quoted = re.search(r"3\.\d+", claim).group(0)
    assert quoted in versions


def test_ruff_target_version_matches_the_interpreter_agents_md_quotes() -> None:
    """AGENTS.md quotes `target-version = "py314"` as the PEP 758 argument."""
    ruff = tomllib.loads(_read(_RUFF_TOML))
    target = ruff["target-version"]
    assert f'target-version = "{target}"' in _read(_AGENTS), (
        "AGENTS.md rests its 'except A, B: is valid here' rule on ruff's "
        f"target-version; ruff.toml now sets {target!r}"
    )
    (major_minor,) = _ci_python_versions()
    assert target == "py" + major_minor.replace(".", "")


def test_home_assistant_floor_agents_md_quotes_matches_hacs_json() -> None:
    """AGENTS.md, hacs.json and tests/test_metadata.py must name one floor."""
    pinned = json.loads(_read(_HACS))["homeassistant"]
    assert f"Home Assistant `{pinned}`" in _read(_AGENTS), (
        f"hacs.json pins Home Assistant {pinned}; AGENTS.md quotes a "
        "different version as the reason the tree cannot reach an interpreter "
        "that rejects PEP 758 syntax"
    )
    assert f'FIRST_HOME_ASSISTANT_ON_PYTHON_314 = "{pinned}"' in _read(
        _METADATA_TEST
    ), (
        "tests/test_metadata.py enforces the floor AGENTS.md cites; the two "
        "no longer name the same release"
    )


# --------------------------------------------------------------------------
# Symbols the prose names
# --------------------------------------------------------------------------

# (module, symbol, the files that tell a reader to use it). A row is a claim
# that `from custom_components.sensi.<module> import <symbol>` is the advice
# the prose gives - note `redact_token` is *defined* in utils.py and re-exported
# through auth.py's import, which is what four of these files point at.
_SYMBOL_CLAIMS = (
    ("utils.py", "redact_token", (_AGENTS, _CLAUDE, _COPILOT, _CURSOR)),
    ("auth.py", "redact_token", (_AGENTS, _CLAUDE, _COPILOT, _CURSOR)),
    ("utils.py", "to_bool", (_AGENTS, _CONTRIBUTING, _COPILOT, _CURSOR)),
    ("utils.py", "to_int", (_AGENTS, _CONTRIBUTING, _COPILOT, _CURSOR)),
    ("utils.py", "to_float", (_AGENTS, _CONTRIBUTING, _COPILOT, _CURSOR)),
    ("utils.py", "bool_to_onoff", (_AGENTS,)),
    ("const.py", "SENSI_LOGIN_URL", (_AGENTS,)),
    ("const.py", "LOGGER", (_AGENTS,)),
    ("__init__.py", "async_setup_entry", (_AGENTS,)),
    ("__init__.py", "async_unload_entry", (_AGENTS,)),
)


def _module_names(module: str) -> set[str]:
    """Return every name a component module defines or imports.

    An imported name is a real attribute of the module, which is what the
    prose is pointing a reader at when it says `auth.py` exports
    `redact_token`.
    """
    tree = ast.parse(_read(_COMPONENT / module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


@pytest.mark.parametrize(
    ("module", "symbol", "docs"),
    _SYMBOL_CLAIMS,
    ids=[f"{module}:{symbol}" for module, symbol, _ in _SYMBOL_CLAIMS],
)
def test_named_helpers_exist_where_the_prose_says(
    module: str, symbol: str, docs: tuple[Path, ...]
) -> None:
    """A helper renamed or moved must fail the instructions that name it."""
    assert symbol in _module_names(module), (
        f"{', '.join(_rel(doc) for doc in docs)} point at `{symbol}` in "
        f"`{module}`, which no longer defines or imports it"
    )
    for doc in docs:
        assert symbol in _read(doc), (
            f"{_rel(doc)} no longer mentions `{symbol}`; drop the row from "
            "_SYMBOL_CLAIMS in the same change"
        )


def test_e2e_fake_backend_still_has_the_methods_agents_md_warns_about() -> None:
    """AGENTS.md's two "easy to get wrong" facts about the fake backend.

    It says the fake implements `shutdown()` as well as `disconnect()`,
    because `SensiClient._async_disconnect` calls `shutdown()` inside
    `contextlib.suppress` - a fake missing it looks like a clean teardown
    while doing nothing at all. Nothing else fails when that stops being true.
    """
    conftest = ast.parse(_read(_ROOT / "tests" / "e2e" / "conftest.py"))
    classes = {
        node.name: {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(conftest)
        if isinstance(node, ast.ClassDef)
    }
    # FakeSensiSocket is what patches `socketio.AsyncClient`, so it is the
    # object `SensiClient` calls; FakeSensiBackend is the scripted server
    # behind it, which the socket shuts down in turn.
    assert "FakeSensiSocket" in classes and "FakeSensiBackend" in classes, (
        f"tests/e2e/conftest.py now defines {sorted(classes)}; AGENTS.md "
        "describes a fake socket.io client backed by a scripted backend"
    )
    assert {"disconnect", "shutdown"} <= classes["FakeSensiSocket"], (
        "AGENTS.md says the fake implements shutdown() as well as "
        f"disconnect(); FakeSensiSocket now defines "
        f"{sorted(classes['FakeSensiSocket'])}"
    )
    assert "shutdown" in classes["FakeSensiBackend"]

    client = _read(_COMPONENT / "client.py")
    disconnect = client.split("async def _async_disconnect", 1)
    assert len(disconnect) == 2, "client.py no longer has _async_disconnect"
    assert "shutdown()" in disconnect[1][:800], (
        "AGENTS.md tells the reader the fake needs shutdown() because "
        "_async_disconnect calls it; it no longer does"
    )

    assert "hass.config.units = US_CUSTOMARY_SYSTEM" in _read(
        _ROOT / "tests" / "e2e" / "conftest.py"
    ), (
        "AGENTS.md says the e2e fixture sets US_CUSTOMARY_SYSTEM, without "
        "which temperature assertions are really assertions about Home "
        "Assistant's F-to-C rounding"
    )


# --------------------------------------------------------------------------
# Catalogues the prose keeps by hand
# --------------------------------------------------------------------------

_LAYOUT_BLOCK = re.compile(r"```\ncustom_components/sensi/\n(.*?)```", re.DOTALL)


def test_agents_layout_block_describes_every_component_module() -> None:
    """The layout block is a hand-kept index of the package.

    A module added without a line here leaves the canonical description of the
    package silently incomplete - and the layout block is the first thing an
    agent reads to find out where something belongs.
    """
    block = _LAYOUT_BLOCK.search(_read(_AGENTS))
    assert block is not None, "AGENTS.md no longer has a layout block to check"

    listed = set(re.findall(r"\b([a-z_]+\.py)\b", block.group(1)))
    committed = {
        path.name
        for path in _COMPONENT.glob("*.py")
        if f"custom_components/sensi/{path.name}" in _TRACKED
    }
    assert len(committed) >= 10, "found almost no component modules to compare against"

    assert listed == committed, (
        "AGENTS.md's layout block and custom_components/sensi/ disagree. "
        f"Missing from the block: {sorted(committed - listed)}. "
        f"Listed but not committed: {sorted(listed - committed)}."
    )


# `__init__.py` is covered by tests/test_init.py, not tests/test___init__.py.
_TEST_MODULE_ALIASES = {"__init__": "test_init"}

# `const.py` is constants only and has no test module of its own. AGENTS.md's
# "every source module has a matching tests/test_<module>.py" does not hold
# for it; whether it should is a maintainer's call, so it is named here rather
# than asserted either way.
_MODULES_WITHOUT_TEST_MODULE = {"const"}


def test_every_source_module_has_the_test_module_agents_md_promises() -> None:
    """A new platform must not arrive without its test module."""
    committed = {
        path.stem
        for path in _COMPONENT.glob("*.py")
        if f"custom_components/sensi/{path.name}" in _TRACKED
    }
    assert len(committed) >= 10

    missing = sorted(
        stem
        for stem in committed - _MODULES_WITHOUT_TEST_MODULE
        if f"tests/{_TEST_MODULE_ALIASES.get(stem, f'test_{stem}')}.py" not in _TRACKED
    )
    assert not missing, (
        "AGENTS.md promises one test module per source module; these have "
        f"none: {missing}"
    )

    for stem in sorted(_MODULES_WITHOUT_TEST_MODULE):
        assert f"custom_components/sensi/{stem}.py" in _TRACKED, (
            f"{stem}.py is listed as having no test module but is no longer "
            "committed; drop the exception"
        )


def test_claude_md_command_table_matches_the_committed_commands() -> None:
    """`.claude/commands/` and the table in CLAUDE.md must agree."""
    table = set(re.findall(r"\| `/([a-z-]+)` \|", _read(_CLAUDE)))
    committed = {
        Path(path).stem for path in _TRACKED if path.startswith(".claude/commands/")
    }
    assert committed, "no committed slash commands found"
    assert table == committed, (
        "CLAUDE.md's slash-command table and .claude/commands/ disagree. "
        f"Committed but undocumented: {sorted(committed - table)}. "
        f"Documented but missing: {sorted(table - committed)}."
    )


def test_prompt_catalog_lists_every_committed_prompt() -> None:
    """`.github/prompts/README.md` is the catalog; it is kept by hand.

    A prompt that is committed but not in the table is invisible to everyone
    who arrives through the catalog, which is the only route the instructions
    describe.
    """
    catalog = set(re.findall(r"\[`([a-z-]+\.md)`\]", _read(_PROMPT_CATALOG)))
    committed = {
        Path(path).name
        for path in _TRACKED
        if path.startswith(".github/prompts/") and not path.endswith("README.md")
    }
    assert committed, "no committed prompts found"
    assert catalog == committed, (
        ".github/prompts/README.md and the committed prompts disagree. "
        f"Committed but not in the catalog: {sorted(committed - catalog)}. "
        f"In the catalog but not committed: {sorted(catalog - committed)}."
    )


# --------------------------------------------------------------------------
# The required checks, restated in prose
# --------------------------------------------------------------------------


def _required_contexts() -> list[str]:
    """Return the check names the master ruleset requires."""
    ruleset = json.loads(_read(_RULESET))
    for rule in ruleset["rules"]:
        if rule["type"] == "required_status_checks":
            return [
                check["context"]
                for check in rule["parameters"]["required_status_checks"]
            ]
    raise AssertionError("master.json has no required_status_checks rule")


def test_branch_protection_doc_names_the_required_checks_exactly() -> None:
    """docs/branch-protection.md spells out all six names in prose."""
    required = _required_contexts()
    assert len(required) == 6, (
        "the ruleset requires "
        f"{len(required)} checks; both docs say 'six required checks'"
    )
    text = " ".join(_read(_BRANCH_PROTECTION_DOC).split())
    for context in required:
        assert f"`{context}`" in text, (
            f"docs/branch-protection.md does not name the required check "
            f"{context!r}; the prose list and the ruleset have drifted"
        )
    assert "The six required checks" in text


def test_quality_doc_gate_table_covers_every_required_check() -> None:
    """docs/quality.md's gate table is a third copy of the same list.

    Its last row abbreviates `manifest requirements match
    requirements_component.txt`, so a row matches by prefix - but every
    required check must still have exactly one row, and the table must not
    claim a check the ruleset does not require.
    """
    rows = re.findall(
        r"^\| `([^`]+)` \| `([a-z-]+\.yml)` \|", _read(_QUALITY_DOC), re.M
    )
    assert len(rows) == 6, f"docs/quality.md's gate table has {len(rows)} rows"

    required = _required_contexts()
    for name, workflow in rows:
        matches = [context for context in required if context.startswith(name)]
        assert len(matches) == 1, (
            f"docs/quality.md's table row {name!r} matches {matches} in the "
            "ruleset; the gate table and master.json have drifted"
        )
        assert f".github/workflows/{workflow}" in _TRACKED, (
            f"docs/quality.md attributes {name!r} to {workflow}, which is not committed"
        )

    unclaimed = [
        context
        for context in required
        if not any(context.startswith(name) for name, _ in rows)
    ]
    assert not unclaimed, f"required checks with no row in docs/quality.md: {unclaimed}"


# --------------------------------------------------------------------------
# How a version is chosen
# --------------------------------------------------------------------------

# The sentence release.yml states about itself. It is quoted here so that a
# workflow that starts deriving versions from commit messages fails this test
# rather than silently making the prose rule below correct again.
_RELEASE_PREMISE = "The version is never derived from commit messages."

_COMMIT_PREFIX_RULE_FILES = (
    _AGENTS,
    _CLAUDE,
    _CONTRIBUTING,
    _COPILOT,
    _CURSOR,
    _REVIEW_RUBRIC,
)

# The claim that is wrong: that a prefix moves the number users install. The
# patterns are written against the wording the files used before this was
# checked, so re-introducing any of them fails.
_PREFIX_DECIDES_THE_VERSION = (
    re.compile(
        r"`(?:feat|fix):`[^.]{0,80}bumps? (?:it\b|the (?:released )?version)", re.I
    ),
    re.compile(r"`(?:feat|fix):`[^.]{0,80}bump the version users see", re.I),
    # "the prefix no longer decides the version" is the correct statement, so
    # the negation has to be excluded rather than matched.
    re.compile(
        r"prefix(?:es)?(?![^.]{0,60}(?:no longer|not|never))"
        r"[^.]{0,60}(?:decide|select)s? the (?:released )?version",
        re.I,
    ),
)

# What the files say instead, taken from AGENTS.md: "the prefix no longer
# decides the version - CalVer is chosen when the release is cut - but it
# decides how the change reads in the generated release notes".
_VERSION_IS_CALVER = re.compile(
    r"no longer (decides|selects)|CalVer|release notes", re.I
)


def test_release_workflow_still_states_the_premise_the_docs_rest_on() -> None:
    """Pin the artefact the commit-prefix rule below is derived from."""
    assert _read(_RELEASE).count(_RELEASE_PREMISE) == 1, (
        f"release.yml no longer states {_RELEASE_PREMISE!r}; the rule the "
        "instruction files state about commit prefixes is derived from it"
    )


@pytest.mark.parametrize("doc", _COMMIT_PREFIX_RULE_FILES, ids=_rel)
def test_commit_prefix_rule_agrees_with_the_release_workflow(doc: Path) -> None:
    """No instruction file may say a commit prefix chooses the version.

    `release.yml` tags the version already committed in `manifest.json` and
    derives CalVer from the date when it is left blank. A file that tells an
    agent `feat:` bumps the released version is telling it the prefix has a
    consequence it does not have - and the agent then avoids `feat:` for the
    wrong reason, or reaches for it expecting a release.
    """
    text = " ".join(_read(doc).split())
    assert "`feat:`" in text, (
        f"{_rel(doc)} no longer states a commit-prefix rule at all; drop it "
        "from _COMMIT_PREFIX_RULE_FILES in the same change"
    )

    for pattern in _PREFIX_DECIDES_THE_VERSION:
        found = pattern.search(text)
        assert found is None, (
            f"{_rel(doc)} says a commit prefix decides the released version: "
            f"{text[max(0, found.start() - 80) : found.end() + 80]!r}\n"
            f"release.yml says: {_RELEASE_PREMISE}"
        )

    assert _VERSION_IS_CALVER.search(text), (
        f"{_rel(doc)} states a commit-prefix rule without saying what the "
        "prefix does decide (release notes) or what chooses the version "
        "(CalVer at release time), which is the half readers get wrong"
    )


# --------------------------------------------------------------------------
# The pointer structure itself
# --------------------------------------------------------------------------


def test_every_restating_file_points_back_at_agents_md() -> None:
    """AGENTS.md is canonical only while the restatements say so.

    A new instruction file that restates the rules without naming AGENTS.md
    becomes a fifth independent copy, which is exactly how the copies drift.
    """
    restatements = [_CLAUDE, _COPILOT, *sorted((_ROOT / ".cursor" / "rules").iterdir())]
    assert len(restatements) >= 3

    for doc in restatements:
        assert "AGENTS.md" in _read(doc), (
            f"{_rel(doc)} restates the repository rules without pointing at AGENTS.md"
        )

    agents = _read(_AGENTS)
    for pointer in (
        "`CLAUDE.md`",
        "`.github/copilot-instructions.md`",
        "`.cursor/rules/`",
    ):
        assert pointer in agents, (
            f"AGENTS.md no longer names {pointer} as a file that restates it, "
            "so a rule change there will not prompt re-checking this one"
        )
