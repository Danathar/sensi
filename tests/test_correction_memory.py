"""`.claude/memory/` tells an agent what the tree does, and nothing checked it.

`AGENTS.md` and `CLAUDE.md` both send a reader here before a non-trivial
change: six correction files recording the things a capable agent gets wrong
on this codebase without being told. Each one is a claim about machinery that
lives somewhere else - `client.py` calls `shutdown()` and not `disconnect()`,
`hacs.json` pins the Home Assistant floor that keeps a Python 3.13 core from
importing the tree, `scripts/classify_pr.py` rejects a label description
GitHub would reject, the `hass` fixture must be requested before the one that
patches `Store.async_load`.

None of it was read by a test, and the directory had already drifted.
`tooling-changes-are-not-feat.md` described a release pipeline this repository
does not have: a `.releaserc` that is not committed, driving an action no
workflow uses, computing the version from commit prefixes - the exact claim
`release.yml` contradicts in its own header and that #167 / #168 corrected in
six other files. It survived because it is not markdown anyone was scanning
for the corrected wording, and because the wording it used ("produces a minor
bump") is not the wording those files used.

A correction that is wrong is worse than no correction: it is read first, by
a reader with no way to check it, and it is believed. This module is the join.
Every assertion resolves what a correction file says against the artefact that
decides it:

- the format of each file against the contract `.claude/memory/README.md`
  states, extracted from that file rather than restated here
- every repository path a correction names in backticks against `git ls-files`
- every `path::test_name` it names against the functions that file defines
- every markdown link into `.claude/memory/` from anywhere in the tree
- and one test per correction, joining its specific claim to the code,
  fixture, workflow or config that would make it false

Three conventions carried from `tests/test_instruction_docs.py` and
`tests/test_file_conventions.py`:

- A claim table is a list of claims, not a cache. Dropping a claim from a
  correction means dropping its row here, in the same diff.
- Every scan asserts how much it found before asserting anything about it, so
  an assertion that quantifies over an empty list cannot pass.
- A path named outside this repository (`homeassistant/...`) is allowed only
  by name, and the allowance is itself checked: the path must be absent from
  `git ls-files` and present in the installed package.

What is deliberately NOT asserted: that a correction is still *useful* (that
is a judgement the README already gives a rule for), that the prose matches
any particular wording, or that every mistake worth recording has a file.
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

_MEMORY = _ROOT / ".claude" / "memory"
_MEMORY_README = _MEMORY / "README.md"

_AGENTS = _ROOT / "AGENTS.md"
_CLAUDE = _ROOT / "CLAUDE.md"

_COMPONENT = _ROOT / "custom_components" / "sensi"
_CLIENT = _COMPONENT / "client.py"
_CLIMATE = _COMPONENT / "climate.py"

_E2E_CONFTEST = _ROOT / "tests" / "e2e" / "conftest.py"
_METADATA_TEST = _ROOT / "tests" / "test_metadata.py"

_RISK_TIERS = _ROOT / ".github" / "policies" / "risk-tiers.yml"
_LABELER = _ROOT / ".github" / "workflows" / "labeler.yml"
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_CLASSIFY_PR = _ROOT / "scripts" / "classify_pr.py"
_RUFF_TOML = _ROOT / "ruff.toml"
_HACS = _ROOT / "hacs.json"

# The sentence release.yml states about itself, quoted in
# tests/test_instruction_docs.py for the same reason: a workflow that starts
# deriving versions from commit messages must fail a test rather than quietly
# make the prose correct again.
_RELEASE_PREMISE = "The version is never derived from commit messages."


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    """Return a repository-relative path, for readable test ids."""
    return path.relative_to(_ROOT).as_posix()


def _flatten(text: str) -> str:
    """Collapse whitespace, so a hard-wrapped phrase still matches."""
    return " ".join(text.split())


def _tracked_files() -> tuple[str, ...]:
    """Return every path git tracks, as repository-relative POSIX strings."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return tuple(sorted(part for part in out.split("\0") if part))


_TRACKED = _tracked_files()
_TRACKED_DIRS = frozenset(
    parent.as_posix() for path in _TRACKED for parent in Path(path).parents
)
_TRACKED_BASENAMES = frozenset(Path(path).name for path in _TRACKED)


def _corrections() -> list[Path]:
    """Return every correction file git tracks, README excluded.

    Taken from `git ls-files` rather than a directory listing: an untracked
    scratch file left in `.claude/memory/` is not something a reader gets.
    """
    prefix = f"{_rel(_MEMORY)}/"
    return [
        _ROOT / path
        for path in _TRACKED
        if path.startswith(prefix)
        and path.endswith(".md")
        and Path(path).name != "README.md"
    ]


_CORRECTIONS = _corrections()


def _module(path: Path) -> ast.Module:
    """Parse a committed Python file."""
    return ast.parse(_read(path), filename=str(path))


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Return the function or coroutine defined as `name`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
            node.name == name
        ):
            return node
    raise AssertionError(f"no function named {name!r}")


# --------------------------------------------------------------------------
# Guards: every scan below quantifies over these
# --------------------------------------------------------------------------


def test_the_correction_files_are_found() -> None:
    """An empty correction set would agree with every assertion here."""
    assert len(_CORRECTIONS) >= 5, (
        f"found {len(_CORRECTIONS)} correction files under {_rel(_MEMORY)}, so "
        "the format and claim scans below are not looking at the directory"
    )
    assert _MEMORY_README in [_ROOT / path for path in _TRACKED], (
        f"{_rel(_MEMORY_README)} is not tracked; it is the format contract the "
        "tests below extract"
    )


def test_the_tracked_file_index_is_not_empty() -> None:
    """Guard the index every path assertion below resolves against."""
    assert len(_TRACKED) > 50, (
        "git ls-files returned almost nothing, so every path assertion in this "
        "module would pass without checking anything"
    )
    assert "AGENTS.md" in _TRACKED


# --------------------------------------------------------------------------
# The format, taken from the README rather than restated
# --------------------------------------------------------------------------

_BOLD_LABEL = re.compile(r"^\*\*(.+?)\*\*", re.MULTILINE)
_FENCED = re.compile(r"```markdown\n(.*?)```", re.DOTALL)


def _declared_sections() -> tuple[str, ...]:
    """Return the section labels `README.md` declares, in declared order.

    Extracted from the fenced template rather than hard-coded, so changing the
    format in the README is what changes this test - and a format nobody can
    satisfy fails here rather than being quietly ignored.
    """
    block = _FENCED.search(_read(_MEMORY_README))
    assert block, (
        f"{_rel(_MEMORY_README)} no longer carries a ```markdown template, so "
        "the format the correction files are checked against is undefined"
    )
    return tuple(_BOLD_LABEL.findall(block.group(1)))


_SECTIONS = _declared_sections()


def test_the_readme_declares_a_usable_format() -> None:
    """The extracted contract must be a contract, not an empty tuple."""
    assert len(_SECTIONS) >= 3, (
        f"{_rel(_MEMORY_README)}'s template declares {_SECTIONS}, which is too "
        "little to check a correction file against"
    )
    assert "```markdown\n# " in _read(_MEMORY_README), (
        f"{_rel(_MEMORY_README)}'s template no longer opens with a level-one "
        "heading, so the line-one assertion below checks a rule it stopped "
        "stating"
    )


@pytest.mark.parametrize("doc", _CORRECTIONS, ids=_rel)
def test_every_correction_opens_with_its_title_on_line_one(doc: Path) -> None:
    """A correction is read for its title first; the README puts it on line 1."""
    first = _read(doc).splitlines()[0]
    assert first.startswith("# "), (
        f"{_rel(doc)} does not open with a level-one heading: {first!r}"
    )
    assert len(first) > 10, (
        f"{_rel(doc)}'s heading is {first!r}, which does not state what went wrong"
    )


@pytest.mark.parametrize("doc", _CORRECTIONS, ids=_rel)
def test_every_correction_states_every_declared_section(doc: Path) -> None:
    """Each section the README declares must be present, in that order."""
    text = _read(doc)
    positions = []
    for section in _SECTIONS:
        label = f"**{section}**"
        assert label in text, (
            f"{_rel(doc)} has no {label} section, which "
            f"{_rel(_MEMORY_README)} declares. A correction without it is a "
            "note, and the missing half is usually why the wrong answer looked "
            "right"
        )
        positions.append(text.index(label))

    assert positions == sorted(positions), (
        f"{_rel(doc)} states {list(_SECTIONS)} out of the declared order; the "
        "symptom is what a reader matches against before anything else"
    )

    # Both directions, so the contract cannot be satisfied by weakening it:
    # dropping a label from the README's template would otherwise stop it
    # being required here while every correction still carried it.
    carried = tuple(_BOLD_LABEL.findall(text))
    assert carried == _SECTIONS, (
        f"{_rel(doc)} carries the sections {list(carried)} and "
        f"{_rel(_MEMORY_README)} declares {list(_SECTIONS)}. One of the two "
        "changed without the other"
    )


@pytest.mark.parametrize("doc", _CORRECTIONS, ids=_rel)
def test_every_correction_is_named_for_the_mistake(doc: Path) -> None:
    """Lower-case kebab-case, so the file name is the thing being corrected."""
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*\.md", doc.name), (
        f"{_rel(doc)} is not named in lower-case kebab-case; the README names "
        "files for the mistake so the directory listing is the index"
    )


# --------------------------------------------------------------------------
# Every path a correction names
# --------------------------------------------------------------------------

# Paths that are deliberately outside this repository. Each is checked below:
# it must be absent from git ls-files and present in the installed package, so
# this tuple cannot be used to excuse a repository path that stopped existing.
_EXTERNAL_PATHS = ("homeassistant/helpers/category_registry.py",)

_PATH_SUFFIXES = (
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
)

_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_NODE_ID = re.compile(r"^([\w./-]+\.py)::(\w+)$")


def _looks_like_a_path(token: str) -> bool:
    """Decide whether a code span names a file in (or beside) this repository.

    Deliberately narrow. A span is a path candidate when it is a bare dotfile
    (`.releaserc`), carries a known file suffix (`client.py`), or is a slash
    separated token with no spaces (`jossef/action-semantic-release-info`).
    Everything else a correction quotes - `shutdown()`, `Store.async_load`,
    `python-socketio`, `feat:` - is prose or code, not a path, and must not be
    resolved as one.
    """
    if not re.fullmatch(r"[.\w][\w./-]*", token):
        return False
    if token.endswith(_PATH_SUFFIXES) or "/" in token:
        return True
    return token.startswith(".") and "." not in token[1:]


def _path_spans(doc: Path) -> list[str]:
    """Return the code spans in `doc` that name a path."""
    return [
        token for token in _CODE_SPAN.findall(_read(doc)) if _looks_like_a_path(token)
    ]


def test_the_path_scan_finds_paths_to_resolve() -> None:
    """Guard the resolution test: no candidates would make it vacuous."""
    found = {token for doc in _CORRECTIONS for token in _path_spans(doc)}
    assert len(found) >= 8, (
        f"the correction files name {len(found)} path-like code spans "
        f"({sorted(found)}), so the resolution test below is not checking much"
    )
    assert "client.py" in found


@pytest.mark.parametrize("doc", _CORRECTIONS, ids=_rel)
def test_every_path_a_correction_names_exists(doc: Path) -> None:
    """A correction that points at a file which is not there misleads twice.

    This is the assertion that would have caught `.releaserc`: the file was
    never committed and the action it was said to drive is in no workflow, but
    the claim read as authoritative because nothing resolved it.
    """
    for token in _path_spans(doc):
        if token in _EXTERNAL_PATHS:
            continue
        resolved = (
            token in _TRACKED
            or token in _TRACKED_DIRS
            or token.rstrip("/") in _TRACKED_DIRS
            or ("/" not in token and token in _TRACKED_BASENAMES)
        )
        assert resolved, (
            f"{_rel(doc)} names `{token}`, which git does not track and which "
            "is not in _EXTERNAL_PATHS. A correction naming a file that is not "
            "there is read as fact by the one reader who cannot check it"
        )


@pytest.mark.parametrize("external", _EXTERNAL_PATHS)
def test_each_external_path_is_external_and_real(external: str) -> None:
    """The allowance is falsifiable: absent here, present in the package."""
    assert external not in _TRACKED, (
        f"{external} is tracked by this repository, so it must be resolved "
        "against the tree rather than excused as external"
    )
    package, _, relative = external.partition("/")
    module = pytest.importorskip(package)
    root = Path(next(iter(module.__path__)))
    assert (root / relative).exists(), (
        f"{external} does not exist in the installed {package} package, so the "
        "correction that names it is pointing at nothing"
    )


@pytest.mark.parametrize("doc", _CORRECTIONS, ids=_rel)
def test_every_test_a_correction_names_still_exists(doc: Path) -> None:
    """`path::name` is a promise that the test is still there under that name."""
    for token in _CODE_SPAN.findall(_read(doc)):
        node_id = _NODE_ID.match(token)
        if not node_id:
            continue
        path, name = node_id.group(1), node_id.group(2)
        assert path in _TRACKED, f"{_rel(doc)} names `{token}`, but {path} is gone"
        tree = _module(_ROOT / path)
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert name in defined, (
            f"{_rel(doc)} cites `{token}` as the source of the correction, but "
            f"{path} defines no {name}. The evidence for the rule is gone"
        )


def test_every_link_into_the_memory_directory_resolves() -> None:
    """A renamed correction breaks the pointers that send a reader to it.

    `AGENTS.md` links one correction by full path and both instruction files
    link the directory, so a rename is silent everywhere except here.
    """
    pattern = re.compile(r"\]\((?:\.\./)*\.?/?(\.claude/memory/[\w./-]*)\)")
    links: list[tuple[str, str]] = []
    for path in _TRACKED:
        if not path.endswith((".md", ".mdc")):
            continue
        for target in pattern.findall(_read(_ROOT / path)):
            links.append((path, target))

    assert len(links) >= 2, (
        f"found {len(links)} links into {_rel(_MEMORY)}; the instruction files "
        "are supposed to point at it, so this scan has stopped working"
    )
    for source, target in links:
        cleaned = target.rstrip("/")
        assert cleaned in _TRACKED or cleaned in _TRACKED_DIRS, (
            f"{source} links to {target}, which git does not track"
        )


def test_the_instruction_files_still_send_a_reader_here() -> None:
    """The directory is only read while something points at it."""
    for doc in (_AGENTS, _CLAUDE):
        # Mentions of the directory itself, not of a file that happens to live
        # in it. A file naming only `.claude/memory/README.md` or one
        # correction by path has stopped telling anyone the directory is there,
        # and a substring check would not notice.
        directory = len(re.findall(r"\.claude/memory/(?![\w.-])", _read(doc)))
        assert directory >= 1, (
            f"{_rel(doc)} no longer names .claude/memory/ as a directory to "
            "read, so the corrections are no longer part of what an agent is "
            "told before it starts"
        )
    assert ".claude/memory/README.md" in _read(_CLAUDE), (
        "CLAUDE.md no longer names the README as the format for a new "
        "correction, which is how the format stays the format"
    )


# --------------------------------------------------------------------------
# except-tuple-without-parentheses-is-valid.md
# --------------------------------------------------------------------------

_EXCEPT_TUPLE = _MEMORY / "except-tuple-without-parentheses-is-valid.md"


def test_the_except_tuple_correction_still_describes_the_tree() -> None:
    """The form it defends is still in the file it names, and still parses."""
    text = _flatten(_read(_EXCEPT_TUPLE))
    assert "`client.py`" in text

    source = _read(_CLIENT)
    assert "except ValueError, TypeError:" in source, (
        "client.py no longer contains the unparenthesised except tuple this "
        "correction exists to stop people 'fixing'. If the form was removed on "
        "purpose, remove the correction in the same change"
    )
    # The claim is that the form is valid here, not that it is unusual. Parsing
    # it with the interpreter running the suite is what makes that falsifiable.
    ast.parse(source, filename=str(_CLIENT))


def test_the_except_tuple_correction_quotes_the_settings_that_make_it_true() -> None:
    """Both literals it quotes are the committed ones."""
    text = _flatten(_read(_EXCEPT_TUPLE))

    ruff = tomllib.loads(_read(_RUFF_TOML))
    target = ruff["target-version"]
    assert f'target-version = "{target}"' in text, (
        f"the correction quotes a ruff target-version that ruff.toml does not "
        f"set (it sets {target!r}); the argument that the form is valid rests "
        "on that value"
    )

    floor = json.loads(_read(_HACS))["homeassistant"]
    assert f'"homeassistant": "{floor}"' in text, (
        f"the correction quotes a Home Assistant floor that hacs.json does not "
        f"declare (it declares {floor!r})"
    )


def test_the_home_assistant_floor_is_still_enforced_by_the_test_it_names() -> None:
    """The correction's fallback for an older core is `test_metadata.py`."""
    floor = json.loads(_read(_HACS))["homeassistant"]
    constants = {
        target.id: ast.literal_eval(node.value)
        for node in ast.walk(_module(_METADATA_TEST))
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant)
    }
    assert floor in constants.values(), (
        f"tests/test_metadata.py no longer pins {floor!r}, so nothing keeps "
        "hacs.json's floor from being dropped and the correction's "
        "'HACS refuses the download' fallback is not enforced"
    )


# --------------------------------------------------------------------------
# fake-socket-must-implement-shutdown.md
# --------------------------------------------------------------------------

_FAKE_SOCKET = _MEMORY / "fake-socket-must-implement-shutdown.md"


def _disconnect_function() -> ast.AsyncFunctionDef:
    """Return `SensiClient._async_disconnect` as parsed from client.py."""
    node = _function(_module(_CLIENT), "_async_disconnect")
    assert isinstance(node, ast.AsyncFunctionDef)
    return node


def test_the_client_still_tears_down_with_shutdown_not_disconnect() -> None:
    """The whole correction rests on which method the client calls."""
    node = _disconnect_function()
    called = {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }
    assert "shutdown" in called, (
        "SensiClient._async_disconnect no longer calls shutdown(); the "
        "correction tells every future fake to implement a method the client "
        "has stopped using"
    )
    assert "disconnect" not in called, (
        "SensiClient._async_disconnect now calls disconnect(), which is the "
        "opposite of what the correction says. Rewrite the correction in the "
        "same change"
    )


def test_the_teardown_is_still_silent_and_still_logs_first() -> None:
    """Why a missing method is invisible: suppressed, and logged beforehand."""
    node = _disconnect_function()

    suppressed = [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.With | ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "suppress"
            for item in child.items
        )
    ]
    assert suppressed, (
        "the shutdown call is no longer inside contextlib.suppress, so a fake "
        "missing shutdown() would now raise instead of looking like a clean "
        "teardown - which is the mechanism the correction describes"
    )
    shutdown_lines = [
        child.lineno
        for block in suppressed
        for child in ast.walk(block)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "shutdown"
    ]
    assert shutdown_lines, "the shutdown call is not inside the suppressing block"

    logged = [
        child.lineno
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "info"
        and any(
            isinstance(arg, ast.Constant) and arg.value == "Disconnecting"
            for arg in child.args
        )
    ]
    assert logged, (
        'client.py no longer logs "Disconnecting"; the correction\'s symptom - '
        "a log line that appears while nothing is torn down - depends on it"
    )
    assert min(logged) < min(shutdown_lines), (
        "the Disconnecting log no longer precedes the teardown, so the "
        "misleading symptom the correction explains cannot happen"
    )


def test_the_socket_fake_implements_everything_the_client_calls_on_it() -> None:
    """The rule, computed rather than remembered.

    The correction generalises to "any stand-in for `socketio.AsyncClient`
    must implement what the client calls". That set is derived from client.py
    here, so a new call the fake does not answer fails now rather than being
    swallowed by the `suppress` above.
    """
    client = _module(_CLIENT)
    used = {
        node.attr
        for node in ast.walk(client)
        if isinstance(node, ast.Attribute)
        and (
            (isinstance(node.value, ast.Name) and node.value.id == "sio")
            or (isinstance(node.value, ast.Attribute) and node.value.attr == "_sio")
        )
    }
    assert len(used) >= 5, (
        f"client.py appears to use only {sorted(used)} on its socket handle, "
        "so this test is not checking the fake against much"
    )

    conftest = _module(_E2E_CONFTEST)
    fakes = [
        node
        for node in ast.walk(conftest)
        if isinstance(node, ast.ClassDef)
        and "socketio.AsyncClient" in (ast.get_docstring(node) or "")
    ]
    assert fakes, (
        "no class in tests/e2e/conftest.py documents itself as a stand-in for "
        "socketio.AsyncClient, so the correction's rule has nothing to apply to"
    )

    for fake in fakes:
        provided = {
            node.name
            for node in fake.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        provided |= {
            node.attr
            for node in ast.walk(fake)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        }
        missing = sorted(used - provided)
        assert not missing, (
            f"{fake.name} does not provide {missing}, which client.py uses on "
            "its socket handle. Inside contextlib.suppress that is silent: the "
            "test sees a clean teardown that never happened"
        )


# --------------------------------------------------------------------------
# github-label-descriptions-are-capped-at-100-chars.md
# --------------------------------------------------------------------------

_LABEL_CAP = _MEMORY / "github-label-descriptions-are-capped-at-100-chars.md"


def _classify_pr_constants() -> dict[str, object]:
    """Return the module-level constants scripts/classify_pr.py assigns."""
    return {
        target.id: ast.literal_eval(node.value)
        for node in _module(_CLASSIFY_PR).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant)
    }


def test_the_label_cap_the_correction_states_is_the_one_enforced() -> None:
    """100 is a number in prose here and a check in the script there."""
    heading = _read(_LABEL_CAP).splitlines()[0]
    stated = re.search(r"(\d+) characters?", heading)
    assert stated, f"the correction's title no longer states a cap: {heading!r}"

    constants = _classify_pr_constants()
    assert "MAX_LABEL_DESCRIPTION" in constants, (
        "scripts/classify_pr.py no longer defines MAX_LABEL_DESCRIPTION, so "
        "the cap the correction says is enforced locally is not"
    )
    assert constants["MAX_LABEL_DESCRIPTION"] == int(stated.group(1)), (
        f"the correction states a cap of {stated.group(1)} and "
        f"classify_pr.py enforces {constants['MAX_LABEL_DESCRIPTION']}"
    )


def test_every_tier_still_carries_both_descriptions_within_the_cap() -> None:
    """The fix the correction records: a short one for GitHub, a long one here."""
    cap = _classify_pr_constants()["MAX_LABEL_DESCRIPTION"]
    tiers = yaml.safe_load(_read(_RISK_TIERS))["tiers"]
    assert len(tiers) >= 3, f"{_rel(_RISK_TIERS)} declares only {len(tiers)} tiers"

    for tier in tiers:
        name = tier["name"]
        short = tier.get("label_description")
        long = tier.get("description")
        assert short, f"{name} has no label_description; GitHub gets nothing"
        assert long, (
            f"{name} has no description, so the prose the correction says is "
            "kept out of the label has nowhere to live"
        )
        assert len(short) <= cap, (
            f"{name}'s label_description is {len(short)} characters; GitHub "
            f"rejects anything over {cap} with an HTTP 422"
        )


def test_the_label_step_still_reports_its_own_failure() -> None:
    """The generalisation: `|| true` is what turned a 422 into a wrong error."""
    body = _read(_LABELER)
    create = [line for line in body.splitlines() if "gh label create" in line]
    assert create, (
        "labeler.yml no longer creates labels, so the correction describes a "
        "step that is gone"
    )
    for line in create:
        assert "|| true" not in line, (
            "the label-create step tolerates failure again; the correction "
            "records that this is what produced \"'tier/runtime' not found\" "
            f"from an unrelated call: {line.strip()!r}"
        )

    triggers = yaml.safe_load(body)[True]
    assert "pull_request_target" in triggers, (
        "labeler.yml is no longer a pull_request_target workflow, so the "
        "correction's closing note about base-branch execution is wrong"
    )


# --------------------------------------------------------------------------
# hass-fixture-must-be-created-before-store-patches.md
# --------------------------------------------------------------------------

_STORE_ORDER = _MEMORY / "hass-fixture-must-be-created-before-store-patches.md"
_PATCHING_FIXTURE = "stored_credentials"


def test_the_fixture_the_correction_names_still_patches_the_store() -> None:
    """The rule only applies while the fixture patches `Store.async_load`."""
    node = _function(_module(_E2E_CONFTEST), _PATCHING_FIXTURE)
    patched = {
        arg.value
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == "patch"
        for arg in child.args
        if isinstance(arg, ast.Constant)
    }
    assert "homeassistant.helpers.storage.Store.async_load" in patched, (
        f"{_PATCHING_FIXTURE} no longer patches Store.async_load globally, so "
        "the ordering rule the correction states is no longer load-bearing"
    )


def test_every_user_of_the_patching_fixture_requests_hass_first() -> None:
    """The rule, enforced over the tier rather than over a remembered list."""
    users: list[str] = []
    for path in _TRACKED:
        if not path.startswith("tests/e2e/") or not path.endswith(".py"):
            continue
        for node in ast.walk(_module(_ROOT / path)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names = [arg.arg for arg in node.args.args]
            if _PATCHING_FIXTURE not in names:
                continue
            users.append(f"{path}::{node.name}")
            assert "hass" in names, (
                f"{path}::{node.name} requests {_PATCHING_FIXTURE} without "
                "requesting hass, so nothing forces the registries to load "
                "before async_load starts returning the credential dict"
            )
            assert names.index("hass") < names.index(_PATCHING_FIXTURE), (
                f"{path}::{node.name} requests {_PATCHING_FIXTURE} before "
                "hass. The correction records what that produces: "
                "KeyError: 'categories' out of the category registry, during "
                "setup, pointing nowhere near the fixture"
            )

    assert len(users) >= 3, (
        f"only {users} request {_PATCHING_FIXTURE}; the ordering rule is not "
        "being checked against the end-to-end tier"
    )


def test_the_patching_fixture_is_never_reached_through_usefixtures() -> None:
    """`usefixtures` is set up first, which is the trap the correction opens on."""
    for path in _TRACKED:
        if not path.startswith("tests/e2e/") or not path.endswith(".py"):
            continue
        for node in ast.walk(_module(_ROOT / path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "usefixtures":
                continue
            named = {arg.value for arg in node.args if isinstance(arg, ast.Constant)}
            assert _PATCHING_FIXTURE not in named, (
                f"{path} names {_PATCHING_FIXTURE} in usefixtures, which pytest "
                "sets up before the parameter list - the exact ordering the "
                "correction exists to prevent"
            )


# --------------------------------------------------------------------------
# temperature-tests-need-us-customary-units.md
# --------------------------------------------------------------------------

_UNITS = _MEMORY / "temperature-tests-need-us-customary-units.md"


def test_the_end_to_end_fixture_still_sets_the_unit_system() -> None:
    """The correction's fix is a line in a file; both are checked."""
    conftest = _read(_E2E_CONFTEST)
    assert "hass.config.units = US_CUSTOMARY_SYSTEM" in conftest, (
        "tests/e2e/conftest.py no longer sets US_CUSTOMARY_SYSTEM, so every "
        "Fahrenheit assertion in the tier is really asserting on Home "
        "Assistant's F-to-C rounding"
    )
    imports = {
        f"{node.module}.{alias.name}"
        for node in ast.walk(_module(_E2E_CONFTEST))
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    assert "homeassistant.util.unit_system.US_CUSTOMARY_SYSTEM" in imports, (
        "US_CUSTOMARY_SYSTEM is not imported from the module the correction "
        "names, so its code sample no longer copies into a working test"
    )


def test_the_payload_fixtures_still_report_fahrenheit() -> None:
    """Why the unit system matters here: the captured payloads are in F."""
    samples = [
        path
        for path in _TRACKED
        if path.startswith("tests/sample") and path.endswith(".json")
    ]
    assert samples, "no tests/sample*.json fixtures found"

    scales: set[str] = set()
    for path in samples:
        scales.update(re.findall(r'"display_scale":\s*"(\w+)"', _read(_ROOT / path)))
    assert scales == {"f"}, (
        f"the payload fixtures report display_scale {sorted(scales)}; the "
        "correction's reasoning assumes Fahrenheit throughout"
    )


def test_the_climate_entity_still_rounds_to_whole_degrees() -> None:
    """The second half of the correction: a pushed 61.5 is reported as 62."""
    # Used, not merely imported: leaving the import in place while assigning a
    # half-degree step is exactly the change that makes the warning wrong.
    used = {
        node.id
        for node in ast.walk(_module(_CLIMATE))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    assert "PRECISION_WHOLE" in used, (
        "climate.py no longer uses PRECISION_WHOLE, so the correction's "
        "rounding warning is stale and a fixture written to it is wrong"
    )


# --------------------------------------------------------------------------
# tooling-changes-are-not-feat.md
# --------------------------------------------------------------------------

_PREFIX = _MEMORY / "tooling-changes-are-not-feat.md"


def test_the_prefix_correction_agrees_with_the_release_workflow() -> None:
    """It described a semantic-release pipeline this repository never had.

    `.releaserc` is caught by the path scan above. This is the other half: no
    workflow derives a version from commit messages, and `release.yml` says so
    itself, so the correction may not claim otherwise.
    """
    release = _read(_RELEASE)
    assert release.count(_RELEASE_PREMISE) == 1, (
        f"release.yml no longer states {_RELEASE_PREMISE!r}; the correction's "
        "rule is derived from it"
    )

    # Action references only. release.yml's header explains at length why
    # semantic-release is not used here, and a text search would match the
    # explanation - which is the wrong direction to be sensitive in.
    workflows = sorted((_ROOT / ".github" / "workflows").glob("*.yml"))
    assert len(workflows) >= 4
    uses = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)", re.MULTILINE)
    for workflow in workflows:
        for action in uses.findall(_read(workflow)):
            assert "semantic-release" not in action, (
                f"{_rel(workflow)} runs {action}; the correction was rewritten "
                "on the premise that no workflow derives a version from the "
                "commit log"
            )

    text = _flatten(_read(_PREFIX))
    assert "generate_release_notes: true" in _read(_RELEASE), (
        "release.yml no longer generates release notes, which is the only "
        "consequence the prefix still has"
    )
    assert "generate_release_notes" in text, (
        "the correction no longer names what the prefix does affect, which is "
        "the half a reader gets wrong once the version half is removed"
    )


def test_the_prefix_correction_describes_how_the_release_is_triggered() -> None:
    """It used to say `workflow_dispatch` only; release.yml is also scheduled."""
    # PyYAML resolves a workflow's bare `on:` key to the boolean True.
    triggers = yaml.safe_load(_read(_RELEASE))[True]
    assert "workflow_dispatch" in triggers, (
        f"release.yml triggers are {sorted(triggers)}; the correction describes "
        "a manual dispatch"
    )
    crons = [entry.get("cron") for entry in triggers.get("schedule") or []]
    assert crons and all(crons), (
        f"release.yml declares no cron schedule (schedule: {triggers.get('schedule')!r}), "
        "so the correction's 'runs monthly on a schedule' is wrong"
    )
    text = _flatten(_read(_PREFIX))
    assert not re.search(r"`?workflow_dispatch`? only", text), (
        "the correction says the release workflow is workflow_dispatch only, "
        f"but its triggers are {sorted(triggers)}"
    )


def test_the_prefix_correction_lists_the_prefixes_agents_md_lists() -> None:
    """Two copies of one table; this is the join that keeps them one table."""
    correction = _flatten(_read(_PREFIX))
    agents = _flatten(_read(_AGENTS))
    for prefix in ("ci:", "docs:", "test:", "refactor:", "chore:"):
        assert f"`{prefix}`" in agents, (
            f"AGENTS.md no longer lists `{prefix}` as a non-release prefix"
        )
        assert f"`{prefix}`" in correction, (
            f"the correction no longer offers `{prefix}` as an alternative to "
            "`feat:`, so it states the ban without stating the replacement"
        )
    assert "AGENTS.md" in correction, (
        "the correction restates a rule AGENTS.md states without linking to "
        f"it; {_rel(_MEMORY_README)} asks for the link rather than the copy"
    )
