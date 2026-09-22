"""The prose behind the quality signals, `docs/quality.md`.

This is the page the README's **Unit coverage** badge links to. It states what
each CI gate proves, what the coverage number does and does not mean, what is
deliberately not covered at all, what the nightly run adds, and where the
published number lives. Until this module existed nothing opened it.

Thirteen references to the path exist under `tests/` and none of them reads the
page as a subject: `tests/test_instruction_docs.py` takes the six-row gate table
and the two copies of the 93% number and resolves the relative markdown links,
`tests/test_file_conventions.py` and `tests/test_codeowners.py` use the path as
a glob-matching input string, and `tests/test_auto_qa_policy.py` names it in a
docstring. The backticked-path scan that would resolve the paths it names runs
over `_INSTRUCTION_FILES`, not `_PROSE_FILES`, so this page was outside it too.

Ten falsehoods written into the committed page at once - both coverage
before/after rows, the poll interval, the socket host, the number of files on
the `coverage-data` branch, the CSV column order, the publish trigger, the badge
colour, the `ruff` argv in the local-reproduction fence, and the nightly cadence
- left the whole suite green.

Two claims on the page were wrong when this module was written, and the same
change corrects them:

* The before/after table was headed `now` and gave `client.py` 100% and the
  repository 98%. Running the page's own command against the committed
  `.coveragerc` (`branch = true`) reports 99% for both. The numbers are right as
  the *historical* measurement - `README.md` and `coverage-gate.yml` carry the
  same pair and both frame it as the past - so the column is dated instead of
  refreshed, and `test_the_after_column_does_not_claim_to_be_today` keeps it
  that way.
* "runs the whole gate once a day" was an overstatement: the nightly `pinned`
  job runs lint, format, the requirements-sync check and the suite, and neither
  `hassfest` nor `HACS` is in it.

Everything below is derived rather than restated: the gate commands come from
the workflow `run:` bodies, the poll interval from `const.py`, the socket host
from `client.py`, the trend row from `scripts/coverage_badge.py`, and the
repository slug from the badge URLs in `README.md`.
"""

import ast
import configparser
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "quality.md"
_README = _ROOT / "README.md"
_COVERAGERC = _ROOT / ".coveragerc"
_COMPONENT = _ROOT / "custom_components" / "sensi"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_COVERAGE_GATE = _WORKFLOWS / "coverage-gate.yml"
_NIGHTLY = _WORKFLOWS / "nightly.yml"
_VALIDATE = _WORKFLOWS / "validate.yml"
_RULESET = _ROOT / ".github" / "rulesets" / "master.json"
_PR_TEMPLATE = _ROOT / ".github" / "pull_request_template.md"
_E2E = _ROOT / "tests" / "e2e"
_BADGE_SCRIPT = _ROOT / "scripts" / "coverage_badge.py"
_CONFTEST = _ROOT / "tests" / "conftest.py"

_spec = importlib.util.spec_from_file_location("coverage_badge_doc", _BADGE_SCRIPT)
coverage_badge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coverage_badge)


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """Return a file's text with every run of whitespace collapsed to a space.

    The page is hard-wrapped prose, so any sentence quoted in this module is
    split across lines at whatever column the wrap fell on. Matching the raw
    text would make each assertion sensitive to re-wrapping rather than to the
    claim it is about.
    """
    return re.sub(r"\s+", " ", _read(path))


def _tracked() -> set[str]:
    """Return every tracked path, as git reports it."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {name for name in out.split("\0") if name}


_TRACKED = _tracked()
_TRACKED_DIRS = {
    str(Path(name).parent) for name in _TRACKED for _ in Path(name).parents
} | {
    str(parent)
    for name in _TRACKED
    for parent in Path(name).parents
    if str(parent) != "."
}


def _workflow(path: Path) -> dict:
    """Return a parsed workflow.

    PyYAML resolves an unquoted `on:` key to the boolean `True` under YAML 1.1,
    so the trigger block has to be looked up both ways.
    """
    loaded = yaml.safe_load(_read(path))
    loaded["on"] = loaded.get("on", loaded.get(True))
    return loaded


def _run_steps(path: Path, job: str) -> dict[str, str]:
    """Return {step name: shell body} for one job's `run:` steps."""
    return {
        step["name"]: step["run"]
        for step in _workflow(path)["jobs"][job]["steps"]
        if "run" in step
    }


def _min_coverage() -> int:
    """Return MIN_COVERAGE as coverage-gate.yml sets it."""
    return int(_workflow(_COVERAGE_GATE)["env"]["MIN_COVERAGE"])


# --------------------------------------------------------------------------
# The gate identities the page's local-reproduction fence names
# --------------------------------------------------------------------------
#
# A "gate identity" is what a command checks, independent of how it is spelled:
# `ruff check .` and `ruff check --output-format=github .` are the same gate,
# and a bare `pytest` and one carrying `--cov-fail-under` are the same suite.
# Every assertion about "which gates run where" is phrased in these terms, so a
# presentation flag cannot read as a different check and a dropped path cannot
# read as the same one.

_RUFF_FORMAT = "ruff format"
_RUFF_CHECK = "ruff check"
_REQUIREMENTS_SYNC = "requirements sync"
_SUITE = "the suite"


def _gate_identity(argv: list[str]) -> str | None:
    """Return the gate a command line checks, or None if it checks nothing."""
    if not argv:
        return None
    if argv[0] == "ruff" and len(argv) > 1:
        if argv[1] == "format":
            return _RUFF_FORMAT
        if argv[1] == "check":
            return _RUFF_CHECK
        return None
    if argv[0] == "pytest":
        return _SUITE
    if (
        argv[0] in {"python", "python3"}
        and "scripts/check_requirements_sync.py" in argv
    ):
        return _REQUIREMENTS_SYNC
    return None


def _commands(body: str) -> list[list[str]]:
    """Return one argv per command line in a shell body.

    Continuations are folded first: the workflows wrap `pytest` over five lines
    with trailing backslashes, and a per-line reader would see five commands.
    """
    folded = re.sub(r"\\\n\s*", " ", body)
    argvs = []
    for line in folded.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            argv = shlex.split(line)
        except ValueError:  # an unbalanced quote in a heredoc body
            continue
        if argv:
            argvs.append(argv)
    return argvs


def _gates_in(body: str) -> set[str]:
    """Return the gate identities a shell body runs."""
    return {
        identity
        for argv in _commands(body)
        if (identity := _gate_identity(argv)) is not None
    }


# --------------------------------------------------------------------------
# The page's own fenced blocks
# --------------------------------------------------------------------------

_BASH_FENCE = re.compile(r"```bash\n(.*?)```", re.DOTALL)


def _fences() -> list[list[str]]:
    """Return the page's bash fences, each as a list of command lines."""
    return [
        [line for line in block.splitlines() if line.strip()]
        for block in _BASH_FENCE.findall(_read(_DOC))
    ]


def _local_fence() -> list[list[str]]:
    """Return the *Reproducing all of it locally* fence, as argv lists."""
    fences = _fences()
    assert len(fences) == 2, (
        f"docs/quality.md has {len(fences)} bash fences; this module reads the "
        "coverage one and the local-reproduction one, so a third needs a home"
    )
    return [shlex.split(line) for line in fences[-1]]


# --------------------------------------------------------------------------
# What the local fence deliberately drops from the CI spelling
# --------------------------------------------------------------------------
#
# Each entry has to be present in the CI argv, so the allowance cannot outlive
# the flag it excuses.

_PRESENTATION_FLAGS = {
    "--output-format=github": (
        "annotates the changed line in review instead of printing to the log; "
        "the check is the same"
    ),
}

_CI_ONLY_FLAGS = {
    "--cov-report=xml": "writes the artifact the summary and the tuner read",
    "--cov-fail-under": (
        ".coveragerc's own comment says a local run reports honestly instead of failing"
    ),
}


# --------------------------------------------------------------------------
# Claims this module deliberately leaves to the module that owns them
# --------------------------------------------------------------------------

_DEFERRED = {
    "the six-row gate table": (
        "tests/test_instruction_docs.py",
        "test_quality_doc_gate_table_covers_every_required_check",
    ),
    "the two copies of the 93% gate": (
        "tests/test_instruction_docs.py",
        "test_quoted_coverage_gate_matches_the_workflow",
    ),
    "the tuner only ever proposes": (
        "tests/test_auto_qa_policy.py",
        "review",
    ),
    "the nightly issue is reused and closes itself": (
        "tests/test_nightly_workflow.py",
        "close",
    ),
    "badge colour thresholds": (
        "tests/test_coverage_badge.py",
        "badge_color",
    ),
}


@pytest.mark.parametrize(("claim", "owner"), sorted(_DEFERRED.items()))
def test_a_deferred_claim_is_still_owned_somewhere(claim: str, owner: tuple) -> None:
    """A deferral must not become a hole when the owning module is rewritten."""
    module, needle = owner
    path = _ROOT / module
    assert path.exists(), f"{claim!r} is deferred to {module}, which is gone"
    assert needle in _read(path), (
        f"{claim!r} is deferred to {module}, which no longer mentions {needle!r}; "
        "either follow the claim to its new home or assert it here"
    )


# --------------------------------------------------------------------------
# Section structure
# --------------------------------------------------------------------------

_SECTIONS = (
    "The gates",
    "Coverage",
    "What is deliberately not covered",
    "Nightly",
    "Trend",
    "Reproducing all of it locally",
)


def test_the_page_has_exactly_the_sections_this_module_reads() -> None:
    """A new section must arrive with a test, not slip in beside them."""
    headings = re.findall(r"^## (.+)$", _read(_DOC), re.MULTILINE)
    assert tuple(headings) == _SECTIONS, (
        f"docs/quality.md's sections are {headings}; this module reads "
        f"{list(_SECTIONS)}. A section added or renamed here is unasserted "
        "until it is given a home."
    )


# --------------------------------------------------------------------------
# Paths the page names
# --------------------------------------------------------------------------

_PATH_IN_BACKTICKS = re.compile(
    r"`((?:\.[\w][\w.-]*|[\w][\w.-]*/[\w./-]*|[\w][\w.-]*\.(?:py|yml|json|csv|md|txt|cfg))[\w./-]*)`"
)


def _named_paths() -> set[str]:
    """Return every backticked path the page names."""
    return {match.group(1) for match in _PATH_IN_BACKTICKS.finditer(_read(_DOC))}


def test_the_page_names_enough_paths_for_the_scan_to_mean_anything() -> None:
    """Guard the reader: an empty path set would make the next test vacuous."""
    assert len(_named_paths()) >= 8, (
        f"only {len(_named_paths())} backticked paths found in docs/quality.md, "
        "so the resolution test below is not reading what it thinks it is"
    )


# Two paths the page names deliberately do not exist on this branch: they are
# the whole content of the data-only `coverage-data` branch. Asserted absent
# rather than skipped, so a stray copy committed to master fails here.
_LIVES_ON_THE_DATA_BRANCH = {
    "coverage-unit.json": "the shields.io payload behind the Unit coverage badge",
    "coverage-trend.csv": "the push-by-push history the per-run artifact cannot be",
}


def _path_exists(name: str) -> bool:
    """Return whether a documented path resolves against the committed tree.

    Three spellings, because the page names files three ways: a repository
    path, a bare file name (`client.py`, `validate.yml`) which the prose uses
    for modules and workflows, and a directory.
    """
    cleaned = name.rstrip("/")
    if cleaned in _TRACKED or cleaned in _TRACKED_DIRS:
        return True
    return "/" not in cleaned and cleaned in {
        Path(tracked).name for tracked in _TRACKED
    }


@pytest.mark.parametrize(
    "name", sorted(_named_paths() - set(_LIVES_ON_THE_DATA_BRANCH))
)
def test_every_path_the_page_names_is_committed(name: str) -> None:
    """A file renamed out from under this page must fail here.

    `test_instruction_docs.py` runs this scan over `_INSTRUCTION_FILES` only,
    so until this module existed the paths on this page were unresolved.
    """
    assert _path_exists(name), f"docs/quality.md names `{name}`, which is not committed"


@pytest.mark.parametrize(("name", "reason"), sorted(_LIVES_ON_THE_DATA_BRANCH.items()))
def test_the_data_branch_files_are_not_committed_here(name: str, reason: str) -> None:
    """Claim: "two files and no code" is a claim about a branch that holds only them."""
    assert name in _named_paths(), (
        f"docs/quality.md no longer names `{name}` ({reason}); drop it from the "
        "list in the same change"
    )
    assert not _path_exists(name), (
        f"`{name}` is committed on this branch. docs/quality.md describes it as "
        f"living on the {_BRANCH} branch only ({reason})."
    )


# --------------------------------------------------------------------------
# Coverage: what is measured, and by what
# --------------------------------------------------------------------------


def _coveragerc() -> configparser.ConfigParser:
    """Return the committed coverage configuration."""
    parser = configparser.ConfigParser()
    parser.read_string(_read(_COVERAGERC))
    return parser


def test_the_coverage_fence_measures_what_coveragerc_defines() -> None:
    """Claim: "What is measured is defined by `.coveragerc`" has to stay true.

    The page's command names a package with dots; `.coveragerc` names a path
    with slashes. They are the same measurement only if they agree.
    """
    fence = _fences()[0]
    assert len(fence) == 1, f"the Coverage fence holds {len(fence)} commands"
    argv = shlex.split(fence[0])
    assert argv[0] == "pytest"
    cov = [arg for arg in argv if arg.startswith("--cov=")]
    assert len(cov) == 1, f"the documented command passes {cov} for --cov"
    measured = cov[0].split("=", 1)[1].replace(".", "/")
    source = _coveragerc()["run"]["source"].strip()
    assert measured == source, (
        f"docs/quality.md measures `{measured}` while .coveragerc's source is "
        f"`{source}`, so a local run and a CI run are not the same measurement"
    )


def test_the_coveragerc_the_page_defers_to_is_committed() -> None:
    """Claim: "which is committed" is the whole reason the page defers to it."""
    assert ".coveragerc" in _TRACKED
    assert "which is committed" in _flat(_DOC)


def test_min_coverage_lives_where_the_page_says_it_does() -> None:
    """The page names both the key and the file that sets it."""
    flat = _flat(_DOC)
    assert "set in `MIN_COVERAGE` in `.github/workflows/coverage-gate.yml`" in flat
    assert "MIN_COVERAGE" in _workflow(_COVERAGE_GATE)["env"]


def test_the_gate_is_enforced_by_the_workflow_the_page_names() -> None:
    """Claim: "the committed floor in `coverage-gate.yml` is still what blocks a merge"."""
    bodies = "\n".join(_run_steps(_COVERAGE_GATE, "coverage").values())
    assert '--cov-fail-under="${MIN_COVERAGE}"' in bodies, (
        "coverage-gate.yml no longer enforces MIN_COVERAGE with --cov-fail-under, "
        "so docs/quality.md's 'the committed floor is what actually blocks a "
        "merge' is no longer true"
    )


# --------------------------------------------------------------------------
# Coverage: the two tiers
# --------------------------------------------------------------------------

# `const.py` is the one shipped module with no test module of its own. It holds
# constants the other modules' tests exercise; there is nothing in it to call.
# Declared rather than silently skipped, and asserted to stay the only one.
_MODULES_WITH_NO_TEST_MODULE = {
    "const.py": "constants only - exercised through the modules that import them",
}


def _shipped_modules() -> set[str]:
    """Return the component's Python modules, by file name."""
    return {path.name for path in _COMPONENT.glob("*.py")}


def _test_module_for(name: str) -> str:
    """Return the unit-tier module name that covers a shipped module."""
    stem = "init" if name == "__init__.py" else name[: -len(".py")]
    return f"test_{stem}.py"


def test_the_unit_tier_is_one_module_per_source_module() -> None:
    """Claim: "one module per source module" is a promise about the whole tier."""
    missing = {
        name
        for name in _shipped_modules()
        if name not in _MODULES_WITH_NO_TEST_MODULE
        and not (_ROOT / "tests" / _test_module_for(name)).exists()
    }
    assert not missing, (
        f"shipped modules with no tests/{{test_<stem>}}.py: {sorted(missing)}. "
        "docs/quality.md describes tests/ as one module per source module; "
        "either add the module or declare the exception with its reason."
    )


@pytest.mark.parametrize(
    ("name", "reason"), sorted(_MODULES_WITH_NO_TEST_MODULE.items())
)
def test_a_declared_exception_is_still_shipped_and_still_untested(
    name: str, reason: str
) -> None:
    """The exception list must not excuse a module that now has a test."""
    assert name in _shipped_modules(), f"{name} is no longer shipped ({reason})"
    assert not (_ROOT / "tests" / _test_module_for(name)).exists(), (
        f"tests/{_test_module_for(name)} now exists, so {name} is no longer an "
        "exception to 'one module per source module' - drop it from the list"
    )


def test_the_e2e_tier_runs_against_the_fake_the_page_names() -> None:
    """`FakeSensiBackend` is the stand-in the whole tier is built on."""
    conftest = ast.parse(_read(_E2E / "conftest.py"))
    classes = {
        node.name for node in ast.walk(conftest) if isinstance(node, ast.ClassDef)
    }
    assert "FakeSensiBackend" in classes, (
        "tests/e2e/conftest.py no longer defines FakeSensiBackend, which "
        "docs/quality.md names as the scripted stand-in the tier runs against"
    )


# The page claims the e2e tier is "the only tier that reaches" four paths.
# Exclusivity is deliberately NOT asserted: `tests/test_client.py` awaits
# `client._connect()` against a mocked socket, so a literal reading is red on
# arrival. What is asserted is the half that is checkable and load-bearing -
# each named path is exercised in tests/e2e/ - so a path that stops being
# covered there fails here.
_E2E_PATHS = {
    "the connect handshake": r"await\s+\w+\._connect\(",
    "the emit loop": r"_emit_loop",
    "the reconnect path": r"reconnect",
    "the coordinator refresh": r"async_refresh\(\)",
}


@pytest.mark.parametrize(("path", "pattern"), sorted(_E2E_PATHS.items()))
def test_each_path_the_page_credits_to_e2e_is_exercised_there(
    path: str, pattern: str
) -> None:
    """A path the page says only e2e reaches must at least be reached there."""
    blob = "\n".join(_read(f) for f in sorted(_E2E.glob("test_*.py")))
    assert re.search(pattern, blob), (
        f"docs/quality.md credits tests/e2e/ with reaching {path}, and nothing "
        f"under tests/e2e/ matches {pattern!r} any more"
    )


def test_the_page_names_exactly_the_paths_this_module_checks() -> None:
    """Guard the table above against the prose growing a fifth path."""
    sentence = re.search(r"This is the only tier that reaches (.+?)\.", _flat(_DOC))
    assert sentence, "the 'only tier that reaches' sentence has been reworded"
    named = {
        part.strip()
        for part in re.split(r",\s*(?:and\s+)?|\s+and\s+", sentence.group(1))
    }
    assert named == set(_E2E_PATHS), (
        f"the page now credits e2e with {sorted(named)}; this module checks "
        f"{sorted(_E2E_PATHS)}"
    )


# --------------------------------------------------------------------------
# Coverage: the before/after table
# --------------------------------------------------------------------------

_TABLE_ROW = re.compile(
    r"^\| (.+?) \| (\d+)% \| \*{0,2}(\d+)%\*{0,2} \|$", re.MULTILINE
)


def _before_after() -> dict[str, tuple[int, int]]:
    """Return {subject: (before, after)} from the e2e before/after table."""
    return {
        row[0].strip().strip("`"): (int(row[1]), int(row[2]))
        for row in _TABLE_ROW.findall(_read(_DOC))
    }


def test_the_before_after_table_has_the_two_rows_this_module_reads() -> None:
    """Guard the parser: an empty table would make the joins below vacuous."""
    assert set(_before_after()) == {"client.py", "repository total"}, (
        f"the before/after table now reads {_before_after()}"
    )


def test_the_table_agrees_with_the_readme_copy_of_the_same_numbers() -> None:
    """Two hand-kept copies of one measurement, joined to nothing until now."""
    sentence = re.search(
        r"from (\d+)% line coverage to (\d+)%, and the repository from (\d+)% "
        r"to (\d+)%",
        _flat(_README),
    )
    assert sentence, (
        "README.md no longer states the e2e before/after numbers in the "
        "recognised form; the copy is either checkable or it is not"
    )
    readme = {
        "client.py": (int(sentence.group(1)), int(sentence.group(2))),
        "repository total": (int(sentence.group(3)), int(sentence.group(4))),
    }
    assert _before_after() == readme, (
        f"docs/quality.md's table says {_before_after()} while README.md says "
        f"{readme}; they are the same measurement"
    )


def test_the_prose_above_the_table_repeats_the_before_column() -> None:
    """Claim: "sat at 52% ... while the repository read 85%" is a third copy."""
    sentence = re.search(
        r"sat at (\d+)% line coverage while the repository read (\d+)%", _flat(_DOC)
    )
    assert sentence, "the sentence introducing the table has been reworded"
    before = {name: values[0] for name, values in _before_after().items()}
    assert before == {
        "client.py": int(sentence.group(1)),
        "repository total": int(sentence.group(2)),
    }


def test_the_before_column_is_below_the_gate_and_the_after_column_is_above() -> None:
    """The table only argues for the e2e tier if it crosses the threshold."""
    gate = _min_coverage()
    for name, (before, after) in _before_after().items():
        assert before < gate <= after, (
            f"{name} reads {before}% -> {after}% against a {gate}% gate, which "
            "is not the story the surrounding prose tells"
        )


_PRESENT_TENSE = re.compile(r"\b(now|today|current|currently|latest)\b", re.IGNORECASE)


def test_the_after_column_does_not_claim_to_be_today() -> None:
    """The column is a dated measurement, and has to read as one.

    It was headed `now` and said 98% while the page's own command reports 99%
    under the committed `.coveragerc`. Nothing in the suite can cheaply measure
    coverage, so the fix is to stop the page claiming the present rather than to
    chase the number - and this is what keeps that fix from being undone.
    """
    header = re.search(r"^\|\s*\| before `tests/e2e/` \| (.+?) \|$", _read(_DOC), re.M)
    assert header, "the before/after table header has been rewritten"
    claimed = header.group(1)
    assert not _PRESENT_TENSE.search(claimed), (
        f"the table's second column is headed {claimed!r}, which claims the "
        "present. These are the numbers the e2e tier moved, measured when it "
        "landed; the live figure is the Unit coverage badge."
    )


def test_the_page_points_at_the_badge_for_the_live_number() -> None:
    """Dating the column only works if the live number has somewhere to be."""
    assert "The live figure is the **Unit coverage** badge" in _flat(_DOC)
    assert "Unit coverage" in _read(_README)


# --------------------------------------------------------------------------
# What is deliberately not covered
# --------------------------------------------------------------------------


def test_the_hardware_gap_is_the_one_the_pull_request_template_asks_about() -> None:
    """Claim: "the pull request template asks for that explicitly"."""
    assert "the pull request template asks for that explicitly" in _flat(_DOC)
    assert re.search(r"- \[ \] Ran against a real thermostat", _read(_PR_TEMPLATE)), (
        "the pull request template no longer asks whether a change was run "
        "against real hardware, which docs/quality.md says it does"
    )


def test_the_undocumented_host_is_the_one_the_client_connects_to() -> None:
    """`rt.sensiapi.io` is the protocol the page calls reverse engineered."""
    host = re.search(r"`(rt\.[\w.]+)` is reverse engineered", _flat(_DOC))
    assert host, "the reverse-engineered-protocol sentence has been reworded"
    url = re.search(r'^SOCKET_URL = "(.+)"$', _read(_COMPONENT / "client.py"), re.M)
    assert url, "client.py no longer defines SOCKET_URL"
    assert urlparse(url.group(1)).hostname == host.group(1), (
        f"docs/quality.md names {host.group(1)} while client.py connects to "
        f"{url.group(1)}"
    )


def test_the_captured_payloads_are_committed_under_tests() -> None:
    """Claim: "The fixtures under `tests/` are captured payloads".

    Both directions, because each catches a different way the claim stops
    being true: a payload committed and never loaded proves nothing about what
    Sensi sent, and a payload loaded and not committed is a fixture that has
    gone missing. The committed-to-loaded direction is also asserted by
    `tests/test_slash_commands.py`; the loaded-to-committed one is not.
    """
    committed = {
        Path(name).name
        for name in _TRACKED
        if re.fullmatch(r"tests/sample[\w]*\.json", name)
    }
    assert committed, "no captured payload fixtures are committed under tests/"
    for name in committed:
        json.loads(_read(_ROOT / "tests" / name))

    loaded = set(re.findall(r'load_json\("(sample[\w]*\.json)"\)', _read(_CONFTEST)))
    assert loaded == committed, (
        f"tests/conftest.py loads {sorted(loaded)} while {sorted(committed)} are "
        "committed; docs/quality.md's 'the fixtures under tests/ are captured "
        "payloads' is a claim about the set the suite actually replays"
    )


def test_there_is_no_config_entry_migration_to_cover() -> None:
    """Claim: "Config entry migration ... has no automated coverage".

    The honest reason is that there is no migration entry point at all. Adding
    one has to come back through this page, so the claim is joined to its
    absence rather than to the absence of a test.
    """
    shipped = "\n".join(_read(path) for path in sorted(_COMPONENT.glob("*.py")))
    assert "async_migrate_entry" not in shipped, (
        "the component now defines async_migrate_entry, so docs/quality.md's "
        "'upgrade paths have no automated coverage' is a gap with code behind "
        "it rather than a path that does not exist"
    )


def test_there_are_no_benchmarks() -> None:
    """Claim: "No benchmarks" - so nothing in the harness should provide them."""
    requirements = _read(_ROOT / "requirements_test.txt").lower()
    assert "benchmark" not in requirements, (
        "a benchmarking plugin is now installed, which docs/quality.md says "
        "does not exist"
    )


def test_the_poll_interval_is_the_one_the_coordinator_uses() -> None:
    """Claim: "the integration polls every 30 seconds"."""
    stated = re.search(r"polls every (\d+) seconds", _flat(_DOC))
    assert stated, "the polling sentence has been reworded"
    const = ast.parse(_read(_COMPONENT / "const.py"))
    values = {
        node.target.id: node.value
        for node in ast.walk(const)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    interval = values.get("COORDINATOR_UPDATE_INTERVAL")
    assert isinstance(interval, ast.Constant), (
        "const.py no longer assigns COORDINATOR_UPDATE_INTERVAL a literal"
    )
    assert interval.value == int(stated.group(1)), (
        f"docs/quality.md says the integration polls every {stated.group(1)}s "
        f"while COORDINATOR_UPDATE_INTERVAL is {interval.value}"
    )
    assert "timedelta(seconds=COORDINATOR_UPDATE_INTERVAL)" in _read(
        _COMPONENT / "coordinator.py"
    ), "the constant is no longer used as the coordinator's interval in seconds"


# --------------------------------------------------------------------------
# Nightly
# --------------------------------------------------------------------------

# Steps in the workflows this module reads that run shell but gate nothing.
# Declared by name so a new gate step cannot hide among them.
_NOT_A_GATE_STEP = {
    "Install dependencies": "installs the harness",
    "Install ruff": "installs the harness",
    "Install the newest test harness": "installs the harness",
    "Record resolved versions": "prints versions into the job summary",
    "Propose a gate adjustment": "runs the tuner, which only ever proposes",
    "Summarise": "writes the measured number to the job summary",
    "Write and push the badge data": "publishes, after the gate has passed",
    "Open, update or close the nightly issue": "reports, and runs no check",
}

# The steps of the nightly `pinned` job that do run a check. Named rather than
# discovered, because "which gates run" is a set union: a second step running a
# gate another step already runs changes no union and would otherwise land
# unnoticed. What each one checks is still derived from its shell body.
_NIGHTLY_GATE_STEPS = ("Lint and format", "Requirements sync", "Tests with coverage")


def test_the_nightly_pinned_leg_runs_the_local_fence_and_nothing_more() -> None:
    """Claim: "runs the four commands *Reproducing all of it locally* lists below".

    Both directions. A gate dropped from the nightly run and a gate added to it
    without reaching the page both fail here.
    """
    documented = {
        identity
        for argv in _local_fence()
        if (identity := _gate_identity(argv)) is not None
    }
    assert documented == {_RUFF_FORMAT, _RUFF_CHECK, _REQUIREMENTS_SYNC, _SUITE}, (
        f"the local-reproduction fence now runs {sorted(documented)}"
    )

    steps = _run_steps(_NIGHTLY, "pinned")
    gating = {name: gates for name, body in steps.items() if (gates := _gates_in(body))}
    assert set(steps) == set(gating) | (set(steps) & set(_NOT_A_GATE_STEP)), (
        f"nightly.yml's pinned job runs steps {sorted(set(steps) - set(gating) - set(_NOT_A_GATE_STEP))} "
        "that this module recognises as neither a gate nor a declared non-gate"
    )
    assert set(gating) == set(_NIGHTLY_GATE_STEPS), (
        f"nightly's pinned job checks something in {sorted(gating)}; this module "
        f"reads {sorted(_NIGHTLY_GATE_STEPS)}. A second step running a gate that "
        "another step already runs would otherwise be invisible."
    )
    assert set().union(*gating.values()) == documented, (
        f"nightly's pinned job runs {sorted(set().union(*gating.values()))} while "
        f"docs/quality.md says it runs {sorted(documented)}"
    )


@pytest.mark.parametrize("action", ["hassfest", "HACS"])
def test_the_home_assistant_actions_are_not_in_the_nightly_run(action: str) -> None:
    """Claim: "`hassfest` and `HACS` need a Home Assistant action and are not in it"."""
    assert action in _read(_DOC)
    jobs = _workflow(_NIGHTLY)["jobs"]
    names = {job.get("name", key).lower() for key, job in jobs.items()}
    assert action.lower() not in names, (
        f"nightly.yml now carries a {action} job, so the page's 'not in it' is "
        "false - and the nightly run is closer to the whole gate than it says"
    )


@pytest.mark.parametrize("action", ["hassfest", "HACS"])
def test_the_home_assistant_actions_are_action_only(action: str) -> None:
    """Claim: "cannot be run locally; they need the workflow"."""
    validate = _workflow(_VALIDATE)["jobs"]
    job = next(
        job
        for job in validate.values()
        if job.get("name", "").lower() == action.lower()
    )
    steps = job["steps"]
    assert any("uses" in step for step in steps), f"{action} runs no action"
    checking = [step for step in steps if "run" in step and _gates_in(step["run"])]
    assert not checking, (
        f"validate.yml's {action} job now runs a shell check, so the page's "
        "'cannot be run locally' needs revisiting"
    )


def test_the_nightly_coverage_run_measures_without_enforcing() -> None:
    """Claim: "the nightly coverage run measures without enforcing the threshold"."""
    body = _run_steps(_NIGHTLY, "pinned")["Tests with coverage"]
    assert "--cov-report=xml" in body, (
        "the nightly coverage run no longer writes coverage.xml, which is the "
        "file the tuner step's own guard reads"
    )
    assert "--cov-fail-under" not in body, (
        "the nightly run now enforces the threshold, so it is closer to the "
        "whole gate than docs/quality.md says"
    )


def test_the_nightly_run_happens_once_a_day() -> None:
    """Claim: "once a day" is a claim about the cron, not about the workflow name."""
    schedules = _workflow(_NIGHTLY)["on"]["schedule"]
    assert len(schedules) == 1, f"nightly.yml has {len(schedules)} schedules"
    minute, hour, dom, month, dow = schedules[0]["cron"].split()
    assert (dom, month, dow) == ("*", "*", "*"), (
        f"nightly.yml's cron is {schedules[0]['cron']!r}, which does not run every day"
    )
    assert "*" not in (minute, hour) and "/" not in f"{minute}{hour}", (
        f"nightly.yml's cron is {schedules[0]['cron']!r}, which runs more than "
        "once a day"
    )


def test_the_latest_leg_resolves_a_newer_home_assistant_than_the_pin() -> None:
    """Claim: "runs the suite against the **latest** ... rather than the pinned one"."""
    body = "\n".join(_run_steps(_NIGHTLY, "latest").values())
    assert "requirements_test.txt" not in body, (
        "the latest leg now installs the pinned harness, which makes it a "
        "duplicate of the pinned leg rather than advance warning"
    )
    assert "--upgrade pytest-homeassistant-custom-component" in body
    assert _SUITE in _gates_in(body), "the latest leg no longer runs the suite"


def test_the_latest_leg_never_gates_a_pull_request() -> None:
    """Claim: "It is informational and never gates a pull request"."""
    triggers = _workflow(_NIGHTLY)["on"]
    assert "pull_request" not in triggers and "pull_request_target" not in triggers, (
        "nightly.yml now runs on pull requests"
    )
    assert _workflow(_NIGHTLY)["jobs"]["latest"].get("continue-on-error") is True

    required = {
        check["context"]
        for rule in json.loads(_read(_RULESET))["rules"]
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    }
    nightly_names = {
        job.get("name", key) for key, job in _workflow(_NIGHTLY)["jobs"].items()
    }
    assert not (required & nightly_names), (
        f"nightly jobs are required checks: {sorted(required & nightly_names)}"
    )


def test_the_tuner_reads_the_policy_the_page_names() -> None:
    """Claim: "reads the measured coverage against the policy in auto-qa-tuning.json"."""
    steps = _run_steps(_NIGHTLY, "pinned")
    assert "scripts/auto_qa_tuner.py" in steps["Propose a gate adjustment"]
    assert ".github/auto-qa-tuning.json" in _TRACKED
    assert "auto-qa-tuning.json" in _read(_ROOT / "scripts" / "auto_qa_tuner.py"), (
        "the tuner no longer reads the policy file docs/quality.md names"
    )


# --------------------------------------------------------------------------
# Trend
# --------------------------------------------------------------------------

_BRANCH = "coverage-data"


def _publish_step() -> str:
    """Return the shell body of the publish job's only step."""
    steps = _run_steps(_COVERAGE_GATE, "publish")
    assert len(steps) == 1, f"the publish job now has {len(steps)} shell steps"
    return next(iter(steps.values()))


def _published_files() -> set[str]:
    """Return the file names the publish step writes to the data branch."""
    body = _publish_step()
    return {
        Path(match).name
        for match in re.findall(r'--(?:badge|trend)-out "\$tree/([\w.-]+)"', body)
    }


def test_the_data_branch_holds_exactly_the_files_the_table_lists() -> None:
    """Claim: "which holds two files and no code" - both directions."""
    stated = re.search(r"holds (\w+) files and no code", _flat(_DOC))
    assert stated, "the coverage-data sentence has been reworded"
    assert stated.group(1) == "two", f"the page now says {stated.group(1)} files"

    tabled = set(re.findall(r"\[`([\w.-]+)`\]\(https://[^)]+\)", _read(_DOC)))
    assert tabled == _published_files(), (
        f"docs/quality.md's trend table lists {sorted(tabled)} while the "
        f"publish step writes {sorted(_published_files())}"
    )
    assert len(tabled) == 2


@pytest.mark.parametrize(
    "url",
    sorted(re.findall(r"\[`[\w.-]+`\]\((https://[^)]+)\)", _read(_DOC))),
)
def test_each_trend_url_points_at_this_repository_s_data_branch(url: str) -> None:
    """A URL that drifted to another fork or branch shows nothing useful."""
    slug = _repo_slug()
    assert f"/{slug}/" in url, f"{url} does not point at {slug}"
    assert f"/{_BRANCH}/" in url, f"{url} does not read the {_BRANCH} branch"


def _repo_slug() -> str:
    """Return owner/repo, taken from README's workflow badges."""
    slugs = set(
        re.findall(
            r"https://github\.com/([\w.-]+/[\w.-]+)/actions/workflows/", _read(_README)
        )
    )
    assert len(slugs) == 1, f"README's workflow badges name {sorted(slugs)}"
    return slugs.pop()


def test_the_badge_file_is_the_one_the_readme_badge_reads() -> None:
    """The table's badge row and the README badge must name one file."""
    badge = re.search(
        r"\[!\[Unit coverage\]\(https://img\.shields\.io/endpoint\?([^)]+)\)\]",
        _read(_README),
    )
    assert badge, "README.md no longer carries the Unit coverage endpoint badge"
    endpoint = parse_qs(badge.group(1))["url"][0]
    assert endpoint.endswith("coverage-unit.json"), endpoint
    assert f"/{_BRANCH}/" in endpoint, endpoint
    assert Path(endpoint).name in _published_files()


def test_the_readme_carries_both_badges_the_last_paragraph_argues_about() -> None:
    """Claim: "a red **Coverage gate** badge next to a healthy **Unit coverage**"."""
    readme = _read(_README)
    assert "[![Coverage gate](" in readme and "[![Unit coverage](" in readme, (
        "docs/quality.md's closing paragraph explains why both badges are "
        "there; one of them is gone"
    )


def test_the_trend_row_is_the_three_columns_in_the_documented_order() -> None:
    """Claim: "one row per push — `date,sha,percent`" - checked behaviourally."""
    columns = re.search(r"one row per push — `([\w,]+)`", _flat(_DOC))
    assert columns, "the trend-row sentence has been reworded"
    row = coverage_badge.trend_row(date="2026-01-02", sha="abc123", percent=97.5)
    values = dict(zip(columns.group(1).split(","), row.split(","), strict=True))
    assert values["date"] == "2026-01-02"
    assert values["sha"] == "abc123"
    assert float(values["percent"]) == pytest.approx(97.5)


def test_the_badge_script_writes_both_files() -> None:
    """Claim: "`scripts/coverage_badge.py` writes both"."""
    tree = ast.parse(_read(_BADGE_SCRIPT))
    main = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    calls = {
        node.func.id
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "write_trend" in calls and "open" in calls, (
        "scripts/coverage_badge.py's main no longer writes both files"
    )


def test_the_publish_job_pushes_them_and_only_after_the_gate_passed() -> None:
    """Claim: "the `publish` job ... pushes them", "depends on the gate having passed"."""
    jobs = _workflow(_COVERAGE_GATE)["jobs"]
    assert "publish" in jobs, "coverage-gate.yml no longer has a publish job"
    assert jobs["publish"]["needs"] == "coverage", (
        "the publish job no longer depends on the gate job, so a failed run "
        "could overwrite a good number with a bad one"
    )
    assert f"push origin HEAD:{_BRANCH}" in _publish_step()


def test_publishing_happens_on_a_push_to_master_only() -> None:
    """Claim: "It runs on push to `master` only"."""
    condition = _workflow(_COVERAGE_GATE)["jobs"]["publish"]["if"]
    assert "github.event_name == 'push'" in condition, condition
    assert "github.ref == 'refs/heads/master'" in condition, condition


def test_the_badge_colour_uses_the_gate_rather_than_a_second_copy() -> None:
    """Claim: "Its colour uses the same `MIN_COVERAGE`"."""
    assert '--high "${MIN_COVERAGE}"' in _publish_step(), (
        "the publish step no longer passes MIN_COVERAGE to the badge script, "
        "so the badge's colour and the gate can now disagree"
    )


def test_the_badge_turns_yellow_exactly_where_the_gate_would_fail() -> None:
    """Claim: "it goes yellow at exactly the point the gate would fail"."""
    gate = float(_min_coverage())
    assert coverage_badge.badge_color(gate, high=gate) == "brightgreen"
    assert coverage_badge.badge_color(gate - 0.01, high=gate) == "yellow"


# --------------------------------------------------------------------------
# Reproducing all of it locally
# --------------------------------------------------------------------------


def _ci_argv() -> dict[str, list[str]]:
    """Return {gate identity: the argv CI runs} across the gating workflows."""
    found: dict[str, list[str]] = {}
    for path, job in ((_VALIDATE, "lint"), (_VALIDATE, "requirements-sync")):
        for body in _run_steps(path, job).values():
            for argv in _commands(body):
                if (identity := _gate_identity(argv)) is not None:
                    found[identity] = argv
    for argv in _commands(
        _run_steps(_COVERAGE_GATE, "coverage")["Run the suite under coverage"]
    ):
        if (identity := _gate_identity(argv)) is not None:
            found[identity] = argv
    return found


def test_the_local_fence_runs_the_gates_ci_runs() -> None:
    """Both directions: a gate the page drops and one it invents both fail."""
    documented = {
        identity: argv
        for argv in _local_fence()
        if (identity := _gate_identity(argv)) is not None
    }
    assert set(documented) == set(_ci_argv()), (
        f"the fence runs {sorted(documented)} while CI's gating workflows run "
        f"{sorted(_ci_argv())}"
    )


@pytest.mark.parametrize(
    "identity", sorted([_RUFF_FORMAT, _RUFF_CHECK, _REQUIREMENTS_SYNC, _SUITE])
)
def test_each_documented_command_matches_the_argv_ci_runs(identity: str) -> None:
    """A superset match would let `ruff format .` read as `ruff format --check .`.

    Positional words are compared for equality in both directions, and so are
    flags, with two declared allowances: presentation flags CI adds for the
    review UI, and flags CI needs that a local run deliberately drops.
    """
    documented = next(
        argv for argv in _local_fence() if _gate_identity(argv) == identity
    )
    ci = _ci_argv()[identity]

    def split(argv: list[str]) -> tuple[list[str], set[str]]:
        positional = [word for word in argv if not word.startswith("-")]
        flags = {word for word in argv if word.startswith("-")}
        return positional, flags

    doc_positional, doc_flags = split(documented)
    ci_positional, ci_flags = split(ci)
    assert doc_positional == ci_positional, (
        f"docs/quality.md runs {documented} while CI runs {ci}"
    )

    excused = (
        set(_PRESENTATION_FLAGS)
        | {flag for flag in ci_flags if flag.split("=")[0] in _CI_ONLY_FLAGS}
        | {flag for flag in ci_flags if flag in _CI_ONLY_FLAGS}
    )
    assert doc_flags == ci_flags - excused, (
        f"docs/quality.md passes {sorted(doc_flags)} while CI passes "
        f"{sorted(ci_flags)}; an unexplained difference is a different check"
    )


@pytest.mark.parametrize(
    ("flag", "reason"),
    sorted({**_PRESENTATION_FLAGS, **_CI_ONLY_FLAGS}.items()),
)
def test_an_excused_flag_is_still_a_flag_ci_passes(flag: str, reason: str) -> None:
    """The allowance lists must not outlive the flags they excuse."""
    passed = {
        word.split("=")[0]
        for argv in _ci_argv().values()
        for word in argv
        if word.startswith("-")
    }
    assert flag.split("=")[0] in passed, (
        f"{flag} is excused from the comparison ({reason}) but CI no longer passes it"
    )


def test_the_fence_does_not_promise_the_two_checks_that_need_the_workflow() -> None:
    """Claim: "`hassfest` and HACS validation cannot be run locally"."""
    fence = " ".join(" ".join(argv) for argv in _local_fence()).lower()
    assert "hassfest" not in fence and "hacs" not in fence, (
        "the local-reproduction fence now claims to run a check the page says "
        "needs the workflow"
    )
    assert "cannot be run locally" in _flat(_DOC)
