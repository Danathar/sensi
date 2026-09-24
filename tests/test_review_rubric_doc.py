"""The brief every review is run against, `docs/review-rubric.md`.

The rubric opens with one rule - the automated gates cover some things, "do
not spend review on those. Spend it on what CI cannot see" - and then seven
priority levels of questions. Three tests already open the page, each for one
narrow join: `tests/test_prompt_procedures.py` compares its heading numbers and
verdict names to `.github/prompts/review.md`, `tests/test_slash_commands.py`
compares four scrub-field names to the capture-payload table, and
`tests/test_instruction_docs.py` resolves its links. Nothing compared the
opening rule to the gates, or the questions to the code they name.

The rule had drifted from the gates. Its list named four of the six things a
required check enforces, leaving out HACS metadata and the requirements sync,
and level 2 asked a reviewer "Do `manifest.json` `requirements` and
`requirements_component.txt` still agree?" - the one question the
`manifest requirements match requirements_component.txt` required check
answers on every pull request. `review.md`'s step 1 left out the same gate, and
"What not to do" named two of them. The same change that adds this module
rewrites those passages.

The gate list is now read out of the page and joined to the required status
checks in `.github/rulesets/master.json` through the workflow job that runs
each gate, in both directions. The questions are checked for naming what a
gate already compares. Every backticked token on the page is classified, and
each class is joined to the file that defines it.
"""

import ast
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "review-rubric.md"
_REVIEW_PROMPT = _ROOT / ".github" / "prompts" / "review.md"
_RULESET = _ROOT / ".github" / "rulesets" / "master.json"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_RELEASE = _WORKFLOWS / "release.yml"
_PR_TEMPLATE = _ROOT / ".github" / "pull_request_template.md"
_AGENTS = _ROOT / "AGENTS.md"
_COMPONENT = _ROOT / "custom_components" / "sensi"

_TRACKED = frozenset(
    subprocess.run(
        ["git", "ls-files"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
)

# Each term the rubric's opening sentence lists, and a string that only the
# step enforcing it contains. The job holding that step is the status check.
_GATES = {
    "formatting": "ruff format --check",
    "lint": "ruff check",
    "coverage threshold": "--cov-fail-under",
    "manifest validity": "home-assistant/actions/hassfest",
    "HACS metadata": "hacs/action",
    "requirements sync": "scripts/check_requirements_sync.py",
}

# What each gate compares. A priority-level question that names one of these
# asks a reviewer to redo a required check.
_GATE_SUBJECTS = {
    "formatting": ("ruff format", "formatting"),
    "lint": ("ruff check", "lint"),
    "coverage threshold": ("--cov-fail-under", "coverage threshold"),
    "manifest validity": ("hassfest",),
    "HACS metadata": ("hacs.json", "HACS"),
    "requirements sync": ("requirements_component.txt", "check_requirements_sync"),
}

# Required checks that are not on the rubric's skip list, and why. The tests
# passing is gated, but level 6 asks whether the tests are the right ones -
# something the pass/fail signal cannot say.
_REQUIRED_BUT_REVIEWED = {
    "pytest (Python 3.14)": "level 6 reviews what the tests assert",
}

# Backticked tokens that are Python or protocol vocabulary rather than names
# from this repository. Each must still appear on the page.
_IDIOMS = frozenset(
    {".get(..., default)", "[...]", "null", "asyncio", "open", "requests", "time.sleep"}
)

# Names the rubric says are defined in the component, and the module they live
# in. `client._emit_loop` is written module-qualified on the page.
_COMPONENT_NAMES = {
    "redact_token": "utils.py",
    "to_bool": "utils.py",
    "to_int": "utils.py",
    "to_float": "utils.py",
    "client._emit_loop": "client.py",
    "stop()": "client.py",
    "_futures": "client.py",
    "unique_id": "entity.py",
}

# Home Assistant exceptions level 4 names. Each must be raised in the component,
# or choosing between them is not a review question here.
_HA_EXCEPTIONS = frozenset({"ConfigEntryAuthFailed", "ConfigEntryNotReady"})

# Payload fields level 1 says a fixture must have scrubbed. `data.py` is the
# parser, so a field it does not read is one no fixture needs.
_PAYLOAD_FIELDS = frozenset(
    {
        "icd_id",
        "serial_number",
        "unique_hardware_id",
        "wifi_mac_address",
        "registration",
    }
)

_MANIFEST_KEYS = frozenset({"version"})

_COMMIT_PREFIXES = frozenset({"feat:", "fix:"})


def _text() -> str:
    return _DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse whitespace; Markdown wraps a phrase across lines anywhere."""
    return " ".join(text.split())


def _section(heading: str) -> str:
    """Return the `## ` section titled `heading`, up to the next `## `."""
    match = re.search(
        r"^## " + re.escape(heading) + r"\n(.*?)(?=^## |\Z)", _text(), re.M | re.S
    )
    assert match is not None, f"docs/review-rubric.md has no {heading!r} section"
    return match.group(1)


def _split_list(phrase: str) -> list[str]:
    return [item.strip() for item in re.split(r", | and ", phrase)]


def _rubric_gates() -> list[str]:
    sentence = re.search(
        r"The automated gates already cover (.*?) — do not spend review on those\.",
        _flat(_text()),
    )
    assert sentence is not None, "the rubric's gate sentence is no longer recognisable"
    return _split_list(sentence.group(1))


def _prompt_gates() -> list[str]:
    sentence = re.search(
        r"\*\*Do not re-check what CI already checks\.\*\* (.*?) are gated\.",
        _flat(_REVIEW_PROMPT.read_text(encoding="utf-8")),
    )
    assert sentence is not None, "review.md's gate sentence is no longer recognisable"
    return _split_list(sentence.group(1))


def _required_checks() -> set[str]:
    ruleset = json.loads(_RULESET.read_text(encoding="utf-8"))
    (rule,) = [r for r in ruleset["rules"] if r["type"] == "required_status_checks"]
    return {check["context"] for check in rule["parameters"]["required_status_checks"]}


def _jobs() -> list[tuple[str, dict]]:
    """Return (check name, job) for every job in every workflow."""
    jobs = []
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_id, job in (data.get("jobs") or {}).items():
            jobs.append((job.get("name", job_id), job))
    return jobs


def _checks_running(needle: str) -> set[str]:
    """Return the check names of the jobs with a step that runs `needle`.

    A step runs it when `needle` is the action it uses, or a whole word of its
    `run:` script - `--cov-fail-under` must not match `--cov-fail-underx`.
    """
    found = set()
    for name, job in _jobs():
        for step in job.get("steps", []):
            action = step.get("uses", "").split("@")[0]
            if action == needle or re.search(
                r"(?<![\w-])" + re.escape(needle) + r"(?![\w-])", step.get("run", "")
            ):
                found.add(name)
    return found


def _gate_checks(term: str) -> set[str]:
    return _checks_running(_GATES[term]) & _required_checks()


def _module(filename: str) -> ast.Module:
    return ast.parse((_COMPONENT / filename).read_text(encoding="utf-8"))


def _defined(filename: str) -> set[str]:
    """Return every name a component module defines or assigns to `self`."""
    names = set()
    for node in ast.walk(_module(filename)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            names.add(node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _function(filename: str, cls: str, name: str) -> ast.AST:
    for node in ast.walk(_module(filename)):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == name
                ):
                    return item
    raise AssertionError(f"{filename} has no {cls}.{name}")


def _tokens() -> set[str]:
    return set(re.findall(r"`([^`\n]+)`", _text()))


# --------------------------------------------------------------------------
# The gates a review skips
# --------------------------------------------------------------------------


def test_the_opening_sentence_lists_exactly_the_gates_this_module_checks() -> None:
    """The list is read out of the page, not remembered."""
    assert _rubric_gates() == list(_GATES), (
        f"the rubric lists {_rubric_gates()}; this module checks {list(_GATES)}"
    )


def test_the_review_prompt_lists_the_same_gates_as_the_rubric() -> None:
    """Claim: "Formatting, lint, ... are gated" - review.md's copy of the list."""
    assert [g.lower() for g in _prompt_gates()] == [g.lower() for g in _GATES], (
        f"review.md lists {_prompt_gates()}; the rubric lists {_rubric_gates()}"
    )


@pytest.mark.parametrize("term", sorted(_GATES))
def test_every_gate_the_rubric_skips_is_a_required_check(term: str) -> None:
    """A gate that only runs, and does not block a merge, still needs review."""
    running = _checks_running(_GATES[term])
    assert running, f"no workflow step runs {_GATES[term]!r} for {term!r}"
    assert _gate_checks(term), (
        f"{term!r} runs in {sorted(running)}, none of which "
        f"{_RULESET.relative_to(_ROOT)} requires"
    )


def test_every_required_check_is_a_skipped_gate_or_named_as_reviewed() -> None:
    """The other direction: a new required check has to be classified here."""
    covered = set().union(*(_gate_checks(term) for term in _GATES))
    unclassified = _required_checks() - covered - set(_REQUIRED_BUT_REVIEWED)
    assert not unclassified, (
        f"required checks {sorted(unclassified)} are neither on the rubric's "
        "gate list nor in _REQUIRED_BUT_REVIEWED"
    )


def test_the_required_but_reviewed_exemptions_are_live() -> None:
    """An exemption for a check that is no longer required is dead weight."""
    for context in _REQUIRED_BUT_REVIEWED:
        assert context in _required_checks(), f"{context!r} is no longer required"
        assert context not in set().union(*(_gate_checks(t) for t in _GATES)), (
            f"{context!r} is exempted but also enforces a listed gate"
        )


def test_every_gate_has_the_subjects_a_question_must_not_name() -> None:
    """Adding a gate means saying what a question about it would name."""
    assert set(_GATE_SUBJECTS) == set(_GATES)


@pytest.mark.parametrize("term", sorted(_GATES))
def test_no_priority_level_asks_a_reviewer_to_redo_a_gate(term: str) -> None:
    """Claim: "do not spend review on those. Spend it on what CI cannot see.".

    Level 2 used to ask whether `requirements_component.txt` still agreed with
    the manifest, which a required check answers on every pull request.
    """
    levels = _flat(_section("Priority order"))
    named = [s for s in _GATE_SUBJECTS[term] if s.lower() in levels.lower()]
    assert not named, (
        f"a priority level names {named}, which the {term!r} gate "
        f"({sorted(_gate_checks(term))}) already checks"
    )


def test_what_not_to_do_names_all_of_the_gates_or_none_of_them() -> None:
    """A partial list reads as the complete one; it named two of six."""
    bullet = re.search(
        r"^- Do not re-flag (.*?)$", _section("What not to do in review"), re.M
    )
    assert bullet is not None, "the 'Do not re-flag' bullet is gone"
    text = bullet.group(1).lower()
    tools = ("ruff", "coverage", "hassfest", "hacs", "requirements")
    named = [tool for tool in tools if tool in text]
    assert named in ([], list(tools)), (
        f"'Do not re-flag' names {named} of the gates {list(tools)}"
    )
    if not named:
        assert "gates listed at the top" in text, (
            "'Do not re-flag' names no gate and no longer points at the list"
        )


# --------------------------------------------------------------------------
# Every name the questions use
# --------------------------------------------------------------------------


def _paths(tokens: set[str]) -> set[str]:
    return {t for t in tokens if "/" in t or re.search(r"\.(py|json|txt|md)$", t)}


def test_every_backticked_token_is_classified() -> None:
    """A new name on the page has to be joined to something before it lands."""
    classes = (
        _paths(_tokens())
        | _IDIOMS
        | set(_COMPONENT_NAMES)
        | _HA_EXCEPTIONS
        | _PAYLOAD_FIELDS
        | _MANIFEST_KEYS
        | _COMMIT_PREFIXES
    )
    unclassified = _tokens() - classes
    assert not unclassified, f"unclassified backticked tokens: {sorted(unclassified)}"


@pytest.mark.parametrize(
    "token",
    sorted(
        _IDIOMS
        | set(_COMPONENT_NAMES)
        | _HA_EXCEPTIONS
        | _PAYLOAD_FIELDS
        | _MANIFEST_KEYS
        | _COMMIT_PREFIXES
    ),
)
def test_every_classified_token_is_still_on_the_page(token: str) -> None:
    """The classification cannot go stale behind the page."""
    assert token in _tokens(), f"`{token}` is classified but no longer on the page"


@pytest.mark.parametrize("path", sorted(_paths(_tokens())))
def test_every_path_resolves(path: str) -> None:
    """Bare module names resolve in the component, the rest from the root."""
    if "<module>" in path:
        pattern = re.escape(path).replace(re.escape("<module>"), r"[a-z_]+")
        assert any(re.fullmatch(pattern, t) for t in _TRACKED), path
    elif path.endswith("/"):
        assert any(t.startswith(path) for t in _TRACKED), path
    elif "/" in path:
        assert path in _TRACKED, path
    else:
        assert path in _TRACKED or f"custom_components/sensi/{path}" in _TRACKED, path


@pytest.mark.parametrize(("token", "filename"), sorted(_COMPONENT_NAMES.items()))
def test_every_component_name_is_defined_where_the_rubric_implies(
    token: str, filename: str
) -> None:
    """Home Assistant's `unique_id` is set as the `_attr_unique_id` shorthand."""
    name = token.removesuffix("()").split(".")[-1]
    if "." in token:
        assert filename == token.split(".")[0] + ".py"
    defined = _defined(filename)
    assert name in defined or f"_attr_{name}" in defined, (
        f"{filename} no longer defines {name}"
    )


def test_redact_token_is_what_the_logging_goes_through() -> None:
    """Claim: "print a value that could be token-shaped without going through `redact_token`"."""
    assert "redact_token" in _defined("utils.py")
    calls = [
        node
        for filename in sorted(p.name for p in _COMPONENT.glob("*.py"))
        for node in ast.walk(_module(filename))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "LOGGER"
        and any(
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Name)
            and arg.func.id == "redact_token"
            for arg in node.args
        )
    ]
    assert calls, "no LOGGER call passes a value through redact_token"


def test_the_emit_loop_task_is_cancelled_in_stop() -> None:
    """Claim: "`client._emit_loop` is a background task ... the same treatment in `stop()`"."""
    started = set()
    for node in ast.walk(_module("client.py")):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "async_create_background_task"
            and "self._emit_loop()" in ast.unparse(node.value.args[0])
        ):
            started |= {ast.unparse(target) for target in node.targets}
    assert started, "client.py no longer starts _emit_loop as a background task"
    stop = _function("client.py", "SensiClient", "stop")
    cancelled = {
        ast.unparse(node.func.value)
        for node in ast.walk(stop)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cancel"
    }
    assert started & cancelled, (
        f"stop() cancels {sorted(cancelled)}; the emit loop runs as {sorted(started)}"
    )


@pytest.mark.parametrize("name", sorted(_HA_EXCEPTIONS))
def test_each_lifecycle_exception_is_raised(name: str) -> None:
    """Claim: "Is `ConfigEntryAuthFailed` raised where reauth is the right outcome"."""
    raised = [
        node
        for path in sorted(_COMPONENT.glob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Raise)
        and node.exc is not None
        and name in {n.id for n in ast.walk(node.exc) if isinstance(n, ast.Name)}
    ]
    assert raised, f"nothing in the component raises {name}"


@pytest.mark.parametrize("field", sorted(_PAYLOAD_FIELDS))
def test_every_scrubbed_field_is_one_the_parser_reads(field: str) -> None:
    """Level 1's list of what a fixture must have scrubbed is the parser's input."""
    literals = {
        node.value
        for node in ast.walk(_module("data.py"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert field in literals, f"data.py no longer reads {field!r}"


@pytest.mark.parametrize("key", sorted(_MANIFEST_KEYS))
def test_the_manifest_keys_level_two_names_exist(key: str) -> None:
    """A key the rubric warns about hand-editing has to be in the manifest."""
    manifest = json.loads((_COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert key in manifest


def test_the_release_workflow_owns_the_manifest_version() -> None:
    """Claim: "Does `manifest.json` `version` change by hand? The release workflow owns it."."""
    release = _RELEASE.read_text(encoding="utf-8")
    assert re.search(r"'\.version = \$v' \"\$manifest\"", release), (
        "release.yml no longer writes the manifest version"
    )
    assert 'manifest="custom_components/sensi/manifest.json"' in release


def test_the_aux_heat_switch_is_gated_on_a_capability() -> None:
    """Claim: "gated on the matching capability in `capabilities.py` ... The aux heat switch is the pattern"."""
    available = ast.unparse(_function("switch.py", "SensiAuxHeatSwitch", "available"))
    gate = re.search(r"\.capabilities\.([\w.]+)", available)
    assert gate is not None, "SensiAuxHeatSwitch.available reads no capability"
    assert gate.group(1).split(".")[-1] in _defined("capabilities.py")


def test_the_pr_template_sections_the_rubric_names_exist() -> None:
    """Level 2 cites the *Risk* section and the Approve row *How it was verified*."""
    cited = set(re.findall(r"the \*([A-Z][A-Za-z ]+)\* section", _flat(_text())))
    assert cited == {"Risk", "How it was verified"}, cited
    headings = set(
        re.findall(r"^## (.+)$", _PR_TEMPLATE.read_text(encoding="utf-8"), re.M)
    )
    assert cited <= headings, f"the PR template has no {sorted(cited - headings)}"


@pytest.mark.parametrize("prefix", sorted(_COMMIT_PREFIXES))
def test_the_user_facing_commit_prefixes_are_agents_mds(prefix: str) -> None:
    """Claim: "`feat:` and `fix:` are what a user reads in the generated release notes"."""
    assert re.search(
        r"^\| `" + re.escape(prefix) + r"` \| a user-visible ",
        _AGENTS.read_text(encoding="utf-8"),
        re.M,
    ), f"AGENTS.md's prefix table no longer calls {prefix} user-visible"
    release = yaml.safe_load(_RELEASE.read_text(encoding="utf-8"))
    generated = [
        step
        for job in release["jobs"].values()
        for step in job.get("steps", [])
        if (step.get("with") or {}).get("generate_release_notes") is True
    ]
    assert generated, "release.yml no longer generates release notes"
