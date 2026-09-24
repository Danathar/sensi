"""The page every review is briefed from, `docs/review-rubric.md`.

Three tests opened the rubric before this module, and each checked one narrow
thing: `tests/test_prompt_procedures.py` compares its heading numbers and
verdict names with `.github/prompts/review.md`, `tests/test_slash_commands.py`
compares its scrub-field names with the capture runbook, and
`tests/test_instruction_docs.py` resolves its links. None of them read what the
page says about CI or about the code.

That is where it had drifted (#274). The page opens with a rule: the automated
gates already cover some checks, so review should not spend itself on them. Its
list of those gates named four of the five required checks a review can skip.
It left out HACS metadata and the requirements-sync check, and §2 then asked the
reviewer to do the requirements-sync check by hand ("Do `manifest.json`
`requirements` and `requirements_component.txt` still agree?"). That check is a
required status check in `.github/rulesets/master.json`, so it cannot fail on a
pull request that is able to merge. `review.md` step 1 restated the list with
HACS but without requirements sync, and the test that reads it only compared the
list to a copy of itself.

This module joins the page to what decides each claim:

- **The opening gate list, against the ruleset.** Every required check except
  the test run maps to a term in the list, and every term maps back to a
  required check whose job runs the tool the term names. The one exempt check
  is asserted to still be required, so the exemption cannot go dead.
- **`review.md` step 1 against the page.** The two lists are equal.
- **The priority levels against the gates.** No §1–§7 level, and nothing under
  "What not to do", names a file or tool a gate already compares.
- **Every backticked token on the page is classified**: a tracked path, a
  manifest key, a payload field the component reads, a name the component
  defines or uses (found by AST), or a named exemption. The classifier raises on
  anything else, so a token in a new shape fails loudly instead of being skipped.
- **The code and template claims the levels make**: the four value helpers in
  `utils.py`, the emit loop that runs as a background task and that `stop()`
  cancels, `_futures`, the two config-entry exceptions being raised, the aux heat
  switch's capability gate, the release workflow owning the manifest version,
  the pull request template's *Risk* and *How it was verified* sections, and
  generated release notes.

Conventions carried from `tests/test_contributor_templates.py` and
`tests/test_prompt_procedures.py`: claims are parsed out of the documents rather
than restated here, the classifier carries its own case table, and every scan
asserts how much it found before asserting anything about it.

Deliberately not asserted: that GitHub's generated release notes list pull
request titles (that is GitHub's behaviour, and cannot be read offline), and
that a level's advice is good advice. This module checks that what the page says
exists, exists, and that it does not send a reviewer to a check CI already runs.
"""

from __future__ import annotations

import ast
import fnmatch
import functools
import importlib.util
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_RUBRIC = _ROOT / "docs" / "review-rubric.md"
_REVIEW = _ROOT / ".github" / "prompts" / "review.md"
_DEFINITION = _ROOT / ".github" / "rulesets" / "master.json"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_RELEASE = _WORKFLOWS / "release.yml"
_PR_TEMPLATE = _ROOT / ".github" / "pull_request_template.md"
_COMPONENT = _ROOT / "custom_components" / "sensi"
_MANIFEST = _COMPONENT / "manifest.json"
_SAMPLE = _ROOT / "tests" / "sample.json"

_SCRIPT = _ROOT / "scripts" / "check_ruleset.py"
_spec = importlib.util.spec_from_file_location("check_ruleset", _SCRIPT)
check_ruleset = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_ruleset)

# The one required check the opening list does not name. A green suite is a
# gate, but whether a new test checks the right thing is not something CI can
# see - that is §6 - so review is not told to skip it.
_TEST_RUN = "pytest (Python 3.14)"

# Each term in the opening list: the required check that gates it, and a string
# the job behind that check must contain, so the term names what the job runs.
_GATES = {
    "formatting": ("ruff", "ruff format --check"),
    "lint": ("ruff", "ruff check"),
    "coverage threshold": ("line coverage >= threshold", "--cov-fail-under"),
    "manifest validity": ("hassfest", "hassfest"),
    "HACS metadata": ("HACS", "hacs/action"),
    "requirements sync": (
        "manifest requirements match requirements_component.txt",
        "scripts/check_requirements_sync.py",
    ),
}

# What each gate compares or runs. A priority level that names one of these is
# asking the reviewer to redo the gate. Each entry is either in the gate's job
# or a committed file, which `test_every_gate_subject_belongs_to_its_gate`
# checks, so the table cannot drift into names no gate uses.
_GATE_SUBJECTS = {
    "formatting": ("ruff", "ruff.toml"),
    "lint": ("ruff", "ruff.toml"),
    "coverage threshold": ("--cov-fail-under", ".coveragerc"),
    "manifest validity": ("hassfest",),
    "HACS metadata": ("hacs.json",),
    "requirements sync": (
        "requirements",
        "requirements_component.txt",
        "scripts/check_requirements_sync.py",
    ),
}

# Backticked tokens the page uses that are none of the checkable kinds, and why
# each is on the page. An entry here must still be on the page and must not
# classify any other way, so the table cannot keep a stale or redundant row.
_EXEMPT = {
    "[...]": "Python subscript syntax, the idiom §3 says not to read payloads with",
    ".get(..., default)": "the dict idiom §3 says to read payloads with",
    "null": "the JSON value a payload field can arrive as",
    "open": "a blocking builtin, named in §4 as what not to call on the loop",
    "requests": "a blocking HTTP library, named in §4 as what not to use",
    "time.sleep": "a blocking call, named in §4 as what not to call on the loop",
    "feat:": "a commit prefix; tests/test_instruction_docs.py owns what is said about it",
    "fix:": "a commit prefix; tests/test_instruction_docs.py owns what is said about it",
}


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse whitespace; Markdown wraps a phrase across lines anywhere."""
    return " ".join(text.split())


def _backticked(text: str) -> list[str]:
    """Return every single-backtick token in a document, in order."""
    without_fences = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return re.findall(r"`([^`\n]+)`", without_fences)


def _terms(listing: str) -> list[str]:
    """Split "a, b, c and d" into its items."""
    return re.split(r", | and ", listing)


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------


def _opening() -> str:
    """Return the paragraphs above the first `##` heading."""
    head = _read(_RUBRIC).split("\n## ", 1)[0]
    return _flat(head)


def _section(heading: str) -> str:
    """Return the `##`/`###` section titled `heading`, up to the next heading."""
    lines = _read(_RUBRIC).splitlines()
    starts = [
        i
        for i, line in enumerate(lines)
        if re.fullmatch(r"#{2,3} " + re.escape(heading), line)
    ]
    assert len(starts) == 1, (
        f"docs/review-rubric.md has {len(starts)} {heading!r} sections"
    )
    body = []
    for line in lines[starts[0] + 1 :]:
        if line.startswith("## ") or line.startswith("### "):
            break
        body.append(line)
    return "\n".join(body)


def _levels() -> dict[int, str]:
    """Return each numbered priority level's body, keyed by its number."""
    found = re.findall(r"^### (\d+)\. (.+)$", _read(_RUBRIC), re.MULTILINE)
    assert len(found) >= 7, f"docs/review-rubric.md has {len(found)} priority levels"
    return {int(number): _section(f"{number}. {title}") for number, title in found}


def _opening_gates() -> list[str]:
    """Return the gate list the page opens with."""
    sentence = re.search(
        r"automated gates already cover (.+?) — do not spend review", _opening()
    )
    assert sentence is not None, "docs/review-rubric.md's opening gate list is gone"
    return _terms(sentence.group(1))


def _review_gates() -> list[str]:
    """Return the gate list `review.md` step 1 restates."""
    sentence = re.search(
        r"\*\*Do not re-check what CI already checks\.\*\* (.+?) are gated\.",
        _flat(_read(_REVIEW)),
    )
    assert sentence is not None, "review.md's step 1 gate list is gone"
    return _terms(sentence.group(1))


# --------------------------------------------------------------------------
# The tree the page is checked against
# --------------------------------------------------------------------------


@functools.cache
def _tracked() -> frozenset[str]:
    """Return every path git has in the index."""
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return frozenset(part for part in out.split("\0") if part)


@functools.cache
def _tracked_dirs() -> frozenset[str]:
    return frozenset(
        parent.as_posix() for path in _tracked() for parent in Path(path).parents
    )


@functools.cache
def _tracked_basenames() -> frozenset[str]:
    return frozenset(Path(path).name for path in _tracked())


@functools.cache
def _module_trees() -> dict[str, ast.Module]:
    """Return every component module, parsed, keyed by its stem."""
    trees = {
        path.stem: ast.parse(_read(path)) for path in sorted(_COMPONENT.glob("*.py"))
    }
    assert len(trees) >= 10, f"found {len(trees)} component modules"
    return trees


@functools.cache
def _names_in(stem: str) -> frozenset[str]:
    """Return every name a component module defines, uses or imports."""
    names: set[str] = set()
    for node in ast.walk(_module_trees()[stem]):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.update(node.name.split("."))
            if node.asname:
                names.add(node.asname)
    return frozenset(names)


@functools.cache
def _component_names() -> frozenset[str]:
    return frozenset(name for stem in _module_trees() for name in _names_in(stem))


@functools.cache
def _payload_fields() -> frozenset[str]:
    """Return the payload keys the component reads, plus the baseline fixture's keys."""
    fields: set[str] = set()
    for tree in _module_trees().values():
        for node in ast.walk(tree):
            key = None
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
            ):
                key = node.args[0]
            elif isinstance(node, ast.Subscript):
                key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                fields.add(key.value)

    def walk(value: object) -> None:
        if isinstance(value, dict):
            fields.update(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(json.loads(_read(_SAMPLE)))
    return frozenset(fields)


@functools.cache
def _manifest_keys() -> frozenset[str]:
    return frozenset(json.loads(_read(_MANIFEST)))


def _definition() -> dict:
    return json.loads(_read(_DEFINITION))


@functools.cache
def _check_jobs() -> dict[str, str]:
    """Return every workflow job's check name, mapped to the job as text."""
    jobs: dict[str, str] = {}
    for path in sorted(_WORKFLOWS.glob("*.y*ml")):
        for job_id, job in (yaml.safe_load(_read(path)).get("jobs") or {}).items():
            jobs[job.get("name", job_id)] = json.dumps(job)
    return jobs


# --------------------------------------------------------------------------
# Token classification
#
# Hand-rolled and total: a backticked token in a shape this cannot sort raises,
# so it cannot slip past every assertion below by matching none of them.
# --------------------------------------------------------------------------

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")


def _checkable_kind(token: str) -> str | None:
    """Return the kind of a token that can be joined to the tree, or None."""
    if re.search(r"<[^>]+>", token):
        pattern = re.sub(r"<[^>]+>", "*", token)
        return "path pattern" if fnmatch.filter(_tracked(), pattern) else None
    path = token.rstrip("/")
    if path in _tracked() or path in _tracked_dirs():
        return "path"
    if "/" not in path and path in _tracked_basenames():
        return "path"
    if token in _manifest_keys():
        return "manifest key"
    if token in _payload_fields():
        return "payload field"
    name = token.removesuffix("()")
    if not _IDENTIFIER.fullmatch(name):
        return None
    if "." in name:
        # `client._emit_loop`: a component module, and a name defined in it.
        module, member = name.split(".")
        if module in _module_trees() and member in _names_in(module):
            return "component name"
        return None
    return "component name" if name in _component_names() else None


def classify(token: str) -> str:
    """Return the kind of a backticked token from the rubric, or raise."""
    if not token:
        raise ValueError("empty token")
    kind = _checkable_kind(token)
    if kind is not None:
        return kind
    if token in _EXEMPT:
        return "exempt"
    raise ValueError(f"cannot classify {token!r}")


@pytest.mark.parametrize(
    ("token", "kind"),
    [
        ("tests/sample.json", "path"),
        ("tests/e2e/", "path"),
        ("capabilities.py", "path"),
        ("tests/test_<module>.py", "path pattern"),
        ("version", "manifest key"),
        ("wifi_mac_address", "payload field"),
        ("redact_token", "component name"),
        ("stop()", "component name"),
        ("client._emit_loop", "component name"),
        ("time.sleep", "exempt"),
    ],
)
def test_classify_sorts_the_shapes_the_page_uses(token: str, kind: str) -> None:
    """The classifier answers correctly for each shape on the page."""
    assert classify(token) == kind


@pytest.mark.parametrize(
    "token",
    [
        "",
        "docs/no-such-page.md",
        "tests/no_<such>_file.py",
        "no_such_helper",
        "client.no_such_member",
        "utils._emit_loop",
    ],
)
def test_classify_raises_on_a_token_it_cannot_join(token: str) -> None:
    """A token that joins to nothing fails loudly instead of being skipped."""
    with pytest.raises(ValueError):
        classify(token)


def test_every_backticked_token_on_the_page_classifies() -> None:
    """No token on the page escapes classification."""
    tokens = _backticked(_read(_RUBRIC))
    assert len(tokens) >= 30, f"found {len(tokens)} backticked tokens"
    unclassified = []
    for token in tokens:
        try:
            classify(token)
        except ValueError:
            unclassified.append(token)
    assert not unclassified, (
        f"docs/review-rubric.md names tokens nothing here can check: {unclassified}"
    )


@pytest.mark.parametrize("token", sorted(_EXEMPT))
def test_every_exemption_is_on_the_page_and_needed(token: str) -> None:
    """An exemption is a row a reader has to trust; it must still be earning it."""
    assert f"`{token}`" in _read(_RUBRIC), (
        f"{token!r} is exempt but no longer on the page"
    )
    assert _checkable_kind(token) is None, (
        f"{token!r} now classifies as {_checkable_kind(token)!r}; drop its exemption"
    )


# --------------------------------------------------------------------------
# The gates
# --------------------------------------------------------------------------


def test_the_opening_list_is_every_required_check_a_review_can_skip() -> None:
    """Claim: "The automated gates already cover ... — do not spend review on those"."""
    required = check_ruleset._required_checks(_definition())
    assert len(required) >= 6, f"master.json requires {sorted(required)}"
    assert _TEST_RUN in required, (
        f"{_TEST_RUN!r} is no longer a required check; the exemption here is dead"
    )
    assert _opening_gates() == list(_GATES), (
        f"the page lists {_opening_gates()}; this module maps {list(_GATES)}"
    )
    mapped = {check for check, _ in _GATES.values()}
    assert mapped == required - {_TEST_RUN}, (
        f"required but not on the page: {sorted(required - {_TEST_RUN} - mapped)}; "
        f"on the page but not required: {sorted(mapped - required)}"
    )


@pytest.mark.parametrize(("term", "gate"), sorted(_GATES.items()))
def test_each_listed_gate_runs_what_its_term_names(
    term: str, gate: tuple[str, str]
) -> None:
    """A term mapped to a check whose job runs something else is not gated."""
    check, needle = gate
    jobs = _check_jobs()
    assert check in jobs, f"no workflow job reports {check!r}"
    assert needle in jobs[check], (
        f"the {check!r} job no longer runs {needle!r}, so {term!r} is not gated"
    )


def test_the_opening_says_why_the_test_run_is_not_on_the_list() -> None:
    """Claim: "The test suite is required too, but ... that is §6"."""
    assert "The test suite is required too" in _opening()
    assert "that is §6" in _opening()
    assert "### 6. Tests" in _read(_RUBRIC)


def test_the_review_prompt_lists_the_same_gates_as_the_page() -> None:
    """`review.md` step 1 is the copy an assistant reads."""
    assert [term.lower() for term in _review_gates()] == [
        term.lower() for term in _opening_gates()
    ]


def test_every_gate_subject_belongs_to_its_gate() -> None:
    """The subject table names only what a gate's job runs or a committed file."""
    assert set(_GATE_SUBJECTS) == set(_GATES)
    for term, subjects in _GATE_SUBJECTS.items():
        job = _check_jobs()[_GATES[term][0]]
        for subject in subjects:
            assert subject in job or subject in _tracked(), (
                f"{subject!r} is neither in the {_GATES[term][0]!r} job nor a committed file"
            )


def _gate_subjects() -> frozenset[str]:
    return frozenset(
        subject for subjects in _GATE_SUBJECTS.values() for subject in subjects
    )


def _names_a_gate_subject(token: str) -> bool:
    subjects = _gate_subjects()
    return token in subjects or Path(token).name in {Path(s).name for s in subjects}


@pytest.mark.parametrize("number", sorted(_levels()))
def test_no_priority_level_asks_review_to_redo_a_gate(number: int) -> None:
    """A level that names what a gate compares sends review to a check that cannot fail.

    §2 asked "Do `manifest.json` `requirements` and `requirements_component.txt`
    still agree?" while the requirements-sync job was a required check (#274).
    """
    tokens = _backticked(_levels()[number])
    gated = [token for token in tokens if _names_a_gate_subject(token)]
    assert not gated, f"§{number} names what a gate already checks: {gated}"


def test_the_levels_were_scanned() -> None:
    """The level scan found tokens; an empty scan would pass every level."""
    tokens = [token for body in _levels().values() for token in _backticked(body)]
    assert len(tokens) >= 25, f"found {len(tokens)} backticked tokens in §1–§7"


def test_what_not_to_do_points_back_to_the_list_rather_than_repeating_it() -> None:
    """A partial copy of the list is how the page named 2 of the gates here."""
    body = _flat(_section("What not to do in review"))
    assert "the automated gates at the top of this page" in body
    named = [token for token in _backticked(body) if _names_a_gate_subject(token)]
    assert not named, f"'What not to do' names gate tools again: {named}"
    repeated = [
        term for term in _GATES if re.search(rf"\b{re.escape(term)}\b", body, re.I)
    ]
    assert not repeated, f"'What not to do' repeats gate terms: {repeated}"


# --------------------------------------------------------------------------
# What the levels say about the code
# --------------------------------------------------------------------------


def _function(stem: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    found = [
        node
        for node in ast.walk(_module_trees()[stem])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(found) == 1, f"{stem}.py defines {name} {len(found)} times"
    return found[0]


def _self_attribute(node: ast.AST) -> str | None:
    """Return `x` for `self.x`, else None."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return None


@pytest.mark.parametrize("helper", ["redact_token", "to_bool", "to_int", "to_float"])
def test_the_value_helpers_the_levels_name_are_in_utils(helper: str) -> None:
    """§1 names `redact_token`; §3 names `to_bool` / `to_int` / `to_float`."""
    assert f"`{helper}`" in _read(_RUBRIC)
    defined = {
        node.name
        for node in _module_trees()["utils"].body
        if isinstance(node, ast.FunctionDef)
    }
    assert helper in defined, f"utils.py no longer defines {helper}"


def test_the_emit_loop_is_a_background_task_that_stop_cancels() -> None:
    """Claim: "`client._emit_loop` is a background task; ... the same treatment in `stop()`"."""
    assert "`client._emit_loop` is a background task" in _flat(_read(_RUBRIC))
    holders = []
    for node in ast.walk(_module_trees()["client"]):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        if not (
            isinstance(call.func, ast.Attribute)
            and call.func.attr.endswith("create_background_task")
        ):
            continue
        started = call.args[0] if call.args else None
        if (
            isinstance(started, ast.Call)
            and _self_attribute(started.func) == "_emit_loop"
        ):
            holders.extend(
                filter(None, (_self_attribute(target) for target in node.targets))
            )
    assert len(holders) == 1, (
        f"client.py starts _emit_loop as a background task into {holders}"
    )
    cancels = [
        node
        for node in ast.walk(_function("client", "stop"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cancel"
        and _self_attribute(node.func.value) == holders[0]
    ]
    assert cancels, f"stop() no longer cancels self.{holders[0]}"


def test_the_futures_map_the_level_names_exists() -> None:
    """Claim: "A never-resolved future in `_futures` is a slow leak"."""
    assert "`_futures`" in _read(_RUBRIC)
    targets: list[ast.expr] = []
    for node in ast.walk(_function("client", "__init__")):
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets.append(node.target)
    assigned = {_self_attribute(target) for target in targets}
    assert "_futures" in assigned, "client.py's __init__ no longer sets self._futures"


def test_both_config_entry_exceptions_are_raised() -> None:
    """Claim: "Is `ConfigEntryAuthFailed` raised where ..., and `ConfigEntryNotReady` where ..."."""
    raised = set()
    for tree in _module_trees().values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                if isinstance(exc, ast.Name):
                    raised.add(exc.id)
    for name in ("ConfigEntryAuthFailed", "ConfigEntryNotReady"):
        assert f"`{name}`" in _read(_RUBRIC)
        assert name in raised, f"nothing in the component raises {name}"


def test_the_aux_heat_switch_is_gated_on_its_capability() -> None:
    """Claim: "gated on the matching capability in `capabilities.py` ... The aux heat switch is the pattern"."""
    assert "The aux heat switch is the pattern" in _flat(_read(_RUBRIC))
    switch = [
        node
        for node in _module_trees()["switch"].body
        if isinstance(node, ast.ClassDef) and node.name == "SensiAuxHeatSwitch"
    ]
    assert len(switch) == 1, "switch.py no longer defines SensiAuxHeatSwitch"
    available = [
        node
        for node in switch[0].body
        if isinstance(node, ast.FunctionDef) and node.name == "available"
    ]
    assert len(available) == 1, "the aux heat switch no longer overrides available"
    assert "capabilities.operating_mode_settings.aux" in ast.unparse(available[0])
    assert {"operating_mode_settings", "aux"} <= _names_in("capabilities")


def test_the_release_workflow_owns_the_manifest_version() -> None:
    """Claim: "Does `manifest.json` `version` change by hand? The release workflow owns it"."""
    assert "The release workflow owns it." in _flat(_read(_RUBRIC))
    runs = [
        step.get("run", "")
        for job in yaml.safe_load(_read(_RELEASE))["jobs"].values()
        for step in job.get("steps", [])
    ]
    writes = [
        run
        for run in runs
        if "custom_components/sensi/manifest.json" in run and ".version = " in run
    ]
    assert len(writes) == 1, (
        f"release.yml writes the manifest version in {len(writes)} steps"
    )


def test_the_template_sections_the_page_names_exist() -> None:
    """The page sends intended breaks to *Risk* and approval to *How it was verified*."""
    headings = re.findall(r"^## (.+)$", _read(_PR_TEMPLATE), re.MULTILINE)
    for section in ("Risk", "How it was verified"):
        assert f"*{section}*" in _read(_RUBRIC)
        assert section in headings, (
            f"the pull request template has no {section!r} section"
        )


def test_the_release_notes_the_commit_prefix_level_names_are_generated() -> None:
    """Claim: "`feat:` and `fix:` are what a user reads in the generated release notes"."""
    assert "the generated release notes" in _flat(_read(_RUBRIC))
    generated = [
        step
        for job in yaml.safe_load(_read(_RELEASE))["jobs"].values()
        for step in job.get("steps", [])
        if (step.get("with") or {}).get("generate_release_notes") is True
    ]
    assert len(generated) == 1, (
        f"release.yml generates release notes in {len(generated)} steps"
    )
