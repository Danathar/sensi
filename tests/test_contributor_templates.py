"""The three files that speak for this repository, and that no test read.

`.github/pull_request_template.md` is the only place a contributor is told how
to verify a change. `.github/ISSUE_TEMPLATE/bug_report.md` is the only place a
user is told how to produce a log we can read. `.acmm.yml` is read by nothing
in this repository at all - it answers an external maturity evaluation, which
is exactly why nobody here notices when it stops being true.

`grep -rl` over `tests/` and `scripts/` returns nothing for any of the three.
The only tests that touch their bytes are the two directory-wide scans, and
neither reads a claim: `tests/test_no_real_identifiers.py` walks
`git ls-files -z` looking for credentials, and `tests/test_file_conventions.py`
checks each tracked text file against `.editorconfig`. Both stay green with
every sentence in all three files wrong.

Each fails differently, and all three fail quietly:

- The pull request checklist names commands. When it names one CI does not run,
  the contributor ticks a box that proves nothing and finds out from a red gate
  on a change they believed they had checked.
- A GitHub *legacy* Markdown issue template appears in the chooser only if its
  YAML frontmatter parses and carries `name` and `about`. Corrupt it and the
  form disappears with no error anywhere. Its debug recipe is worth more than
  the form: the logger key is a hand copy of the component's package path, so
  moving or renaming the component makes the recipe produce no logs - at the
  one moment somebody is relying on it.
- An `.acmm.yml` waiver says "this capability exists, here is what provides it,
  here is why the file check cannot see it". The first entry rests entirely on
  `.github/workflows/ai-fix.yml` being gone and on nothing else matching the
  criterion. Nothing stops a later workflow re-introducing an in-repository
  autonomous path while the waiver keeps telling an outside reader the only
  such file was removed.

Conventions carried from `tests/test_codeowners.py`,
`tests/test_instruction_docs.py` and `tests/test_file_conventions.py`:

- The literals are parsed out of the documents, not restated here. A test that
  restates them is a second copy to keep in step, and deleting the line it
  copied would still pass. What is hard-coded is the *shape* of a claim, plus a
  small set of required mentions so that deleting a load-bearing sentence fails
  instead of turning an assertion into a no-op.
- The classifier that sorts a backticked literal into a kind is hand-rolled,
  carries its own case table, and **raises** on a shape it cannot check, so a
  literal written in a new form is a loud failure rather than a skipped one.
- Every scan asserts how much it found before asserting anything about it.
  Two empty sets agree.

Deliberately not asserted: that `.acmm.yml`'s "removed in PR #118" names the
right pull request (no offline evidence), that the waived criteria are the ones
the external evaluation actually defines (it lives in another repository), that
the bug form's `Settings > Developer tools > Actions` path matches a Home
Assistant menu (it is a UI string, not a value this tree owns), and that the
`Run tests` step is a bare `pytest` with no arguments - that argv contract is
already owned by `tests/test_ci_workflow.py`, so the join here compares the
checklist against the gate rather than pinning the gate.
"""

from __future__ import annotations

import ast
import functools
import logging
from pathlib import Path
import re
import shlex
import subprocess

import pytest
import yaml

from homeassistant.components.logger import DOMAIN as LOGGER_DOMAIN, SERVICE_SET_LEVEL

_ROOT = Path(__file__).resolve().parent.parent
_PR_TEMPLATE = _ROOT / ".github" / "pull_request_template.md"
_ISSUE_TEMPLATE_DIR = _ROOT / ".github" / "ISSUE_TEMPLATE"
_BUG_REPORT = _ISSUE_TEMPLATE_DIR / "bug_report.md"
_ACMM = _ROOT / ".acmm.yml"
_RISK_TIERS_YML = _ROOT / ".github" / "policies" / "risk-tiers.yml"
_SECURITY_AI = _ROOT / "docs" / "SECURITY-AI.md"
_SYNC_SCRIPT = _ROOT / "scripts" / "check_requirements_sync.py"
_CONST = _ROOT / "custom_components" / "sensi" / "const.py"
_WORKFLOWS = _ROOT / ".github" / "workflows"

# The pull request template must keep saying these things. Every other
# assertion below quantifies over what it finds in the file, so deleting a
# sentence would otherwise remove an assertion rather than fail one.
_PR_REQUIRED_MENTIONS = (
    "docs/risk-tiers.md",
    "tier/*",
    "`manifest.json`",
    "`requirements_component.txt`",
)

# The Risk section claims three kinds of change are breaking for existing
# installs. Each maps to a module that `.github/policies/risk-tiers.yml` must still
# classify as `tier/breaking` - if one is demoted, the template is promising
# something the labeller no longer does.
_BREAKING_CLAIMS = {
    "config flow": "custom_components/sensi/config_flow.py",
    "stored credentials": "custom_components/sensi/auth.py",
    "entity unique IDs": "custom_components/sensi/entity.py",
}

# `.acmm.yml` names one path that must NOT exist. Asserting the absence is the
# whole point of the waiver, so the path is listed here and also required to
# still be mentioned in the file.
_MUST_STAY_ABSENT = (".github/workflows/ai-fix.yml",)

# Re-introducing the waived criterion looks like one of these in a workflow.
_AI_FIX_MARKERS = ("ANTHROPIC_API_KEY", "anthropics/claude-code", "ai-fix-requested")

# Flags a gate may add to a command the checklist names without changing what
# the command checks. `--output-format=github` moves the report from the job
# log to a review annotation; everything else has to match, in both directions.
_PRESENTATION_FLAGS = frozenset({"--output-format=github"})

_COMMAND_TOOLS = ("pytest", "ruff")
_PATH_SUFFIXES = (".md", ".json", ".txt", ".yml", ".yaml", ".py")


# --------------------------------------------------------------------------
# Tree
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
def _tracked_or_new() -> frozenset[str]:
    """Return indexed paths plus untracked ones git would not ignore.

    An absence assertion has to see a file that has been added to the worktree
    but not yet staged, or re-introducing a workflow passes until the moment it
    is committed.
    """
    out = subprocess.run(
        [
            "git",
            "-C",
            str(_ROOT),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return frozenset(part for part in out.split("\0") if part)


@functools.cache
def _tracked_basenames() -> frozenset[str]:
    """Return the basename of every indexed path."""
    return frozenset(path.rsplit("/", 1)[-1] for path in _tracked())


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Literal classification
#
# Hand-rolled and total: a backticked literal one of these documents gains in a
# shape this cannot sort raises, so it cannot slip past every assertion below
# by matching none of them.
# --------------------------------------------------------------------------


def classify(literal: str) -> str:
    """Return the kind of a backticked literal from a contributor document."""
    if not literal:
        raise ValueError("empty literal")
    if " > " in literal:
        return "ui-path"
    if literal.startswith("[") and literal.endswith("]"):
        return "log-marker"
    words = literal.split()
    if words[0] in _COMMAND_TOOLS:
        return "command"
    if literal.endswith(_PATH_SUFFIXES) and len(words) == 1:
        return "path"
    if len(words) == 1 and re.fullmatch(r"[a-z_]+", literal):
        return "manifest-key"
    raise ValueError(f"cannot classify {literal!r}")


def _backticked(text: str) -> list[str]:
    """Return every single-backtick literal in a document, in order."""
    without_fences = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return re.findall(r"`([^`\n]+)`", without_fences)


def _split_argv(argv: list[str]) -> tuple[list[str], frozenset[str]]:
    """Return an argv's positional words and its flags, separately."""
    positional = [word for word in argv if not word.startswith("-")]
    flags = frozenset(word for word in argv if word.startswith("-"))
    return positional, flags


# --------------------------------------------------------------------------
# Workflows
# --------------------------------------------------------------------------


@functools.cache
def _workflow(name: str) -> dict:
    """Return a parsed workflow file."""
    return yaml.safe_load(_read(_WORKFLOWS / name))


def _run_argvs(workflow_name: str) -> list[list[str]]:
    """Return the argv of every command line in a workflow's `run:` bodies."""
    argvs: list[list[str]] = []
    for job in _workflow(workflow_name).get("jobs", {}).values():
        for step in job.get("steps", []):
            run = step.get("run")
            if not run:
                continue
            for line in run.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                try:
                    argvs.append(shlex.split(stripped))
                except ValueError:  # pragma: no cover - malformed shell
                    continue
    return argvs


@functools.cache
def _gate_argvs() -> tuple[tuple[str, ...], ...]:
    """Return the argv of every command the pull request gates run."""
    argvs: list[tuple[str, ...]] = []
    for name in ("ci.yml", "validate.yml"):
        argvs.extend(tuple(argv) for argv in _run_argvs(name))
    return tuple(argvs)


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------


@functools.cache
def _pr_sections() -> dict[str, str]:
    """Return the pull request template split by its `##` headings."""
    sections: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in _read(_PR_TEMPLATE).splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(body)
            current = line[3:].strip()
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        sections[current] = "\n".join(body)
    return sections


def _frontmatter(path: Path) -> dict:
    """Return the YAML frontmatter of a legacy Markdown issue template."""
    text = _read(path)
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name} has no frontmatter opener")
    end = text.find("\n---\n", 3)
    if end == -1:
        raise ValueError(f"{path.name} has no frontmatter terminator")
    loaded = yaml.safe_load(text[4 : end + 1])
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} frontmatter is not a mapping")
    return loaded


def _fenced_blocks(text: str) -> list[str]:
    """Return the body of every fenced code block."""
    return re.findall(r"^```[^\n]*\n(.*?)^```", text, flags=re.DOTALL | re.MULTILINE)


@functools.cache
def _acmm() -> dict:
    """Return the parsed waiver file."""
    return yaml.safe_load(_read(_ACMM))


@functools.cache
def _acmm_reasons() -> str:
    """Return every waiver's reasoning as folded YAML resolves it.

    The file wraps each `reason` as a `>-` block, so a citation in the raw
    bytes is split across lines that no heading and no path ever contains.
    """
    return " ".join(" ".join(waiver["reason"].split()) for waiver in _acmm()["waivers"])


@functools.cache
def _component_logger_name() -> str:
    """Return the logger name the component actually registers.

    `const.py` builds it from `__package__`, so it is the package path of the
    module - the value the bug report form hand-copies.
    """
    tree = ast.parse(_read(_CONST))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "LOGGER" not in targets:
            continue
        call = node.value
        assert isinstance(call, ast.Call), "const.LOGGER is not a call"
        assert ast.unparse(call.func) == "logging.getLogger"
        assert [ast.unparse(a) for a in call.args] == ["__package__"], (
            "const.LOGGER no longer derives its name from the package path"
        )
        return ".".join(_CONST.relative_to(_ROOT).parts[:-1])
    raise AssertionError("const.py defines no LOGGER")


# --------------------------------------------------------------------------
# The classifier's own case table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("literal", "kind"),
    [
        ("pytest", "command"),
        ("ruff check .", "command"),
        ("ruff format --check .", "command"),
        ("manifest.json", "path"),
        ("requirements_component.txt", "path"),
        ("docs/risk-tiers.md", "path"),
        ("requirements", "manifest-key"),
        ("[custom_components.sensi]", "log-marker"),
        ("Settings > Developer tools > Actions", "ui-path"),
    ],
)
def test_classify_sorts_the_shapes_these_documents_use(literal: str, kind: str) -> None:
    """The classifier answers correctly for each shape in the tree."""
    assert classify(literal) == kind


@pytest.mark.parametrize("literal", ["", "make lint", "Some Prose Here", "3.14"])
def test_classify_raises_on_a_shape_it_cannot_check(literal: str) -> None:
    """A literal in an unknown shape fails loudly instead of being skipped."""
    with pytest.raises(ValueError):
        classify(literal)


# --------------------------------------------------------------------------
# Pull request template
# --------------------------------------------------------------------------


def test_pull_request_template_is_tracked_and_substantial() -> None:
    """The template exists in the index and was read, not guessed at."""
    assert ".github/pull_request_template.md" in _tracked()
    assert len(_read(_PR_TEMPLATE).splitlines()) > 20


def test_pull_request_template_sections_were_parsed() -> None:
    """The section reader found the headings every later test scopes to."""
    sections = _pr_sections()
    assert set(sections) == {
        "What changed",
        "Why",
        "How it was verified",
        "Risk",
        "Notes for reviewers",
    }
    assert all(body.strip() for body in sections.values())


@pytest.mark.parametrize("mention", _PR_REQUIRED_MENTIONS)
def test_pull_request_template_still_makes_its_load_bearing_claims(
    mention: str,
) -> None:
    """Deleting one of these lines fails here rather than silently."""
    assert mention in _read(_PR_TEMPLATE)


def test_every_backticked_literal_in_the_pull_request_template_classifies() -> None:
    """No literal in the template escapes classification."""
    literals = _backticked(_read(_PR_TEMPLATE))
    assert len(literals) >= 5
    for literal in literals:
        classify(literal)


def test_every_command_the_checklist_names_is_the_command_a_gate_runs() -> None:
    """A ticked verification box is the run that gates the pull request.

    Positional words must match exactly in both directions - a checklist that
    names fewer paths, or a gate that checks more, is a different run. Flags
    must match too, except the ones that only move where a failure is
    reported: `ruff format .` rewrites where `ruff format --check .` gates,
    and the box would read the same either way.
    """
    commands = [
        literal
        for literal in _backticked(_pr_sections()["How it was verified"])
        if classify(literal) == "command"
    ]
    assert len(commands) >= 3, commands

    gate_argvs = _gate_argvs()
    assert len(gate_argvs) >= 5
    for command in commands:
        wanted_positional, wanted_flags = _split_argv(shlex.split(command))
        matches = [
            argv
            for argv in gate_argvs
            if _split_argv(list(argv))[0] == wanted_positional
            and _split_argv(list(argv))[1] - _PRESENTATION_FLAGS == wanted_flags
        ]
        assert matches, f"no CI step runs {command!r}"


def test_every_path_the_template_names_is_committed() -> None:
    """Backticked paths resolve, by basename where a file is named alone."""
    text = _read(_PR_TEMPLATE)
    literals = [lit for lit in _backticked(text) if classify(lit) == "path"]
    assert len(literals) >= 2, literals
    for literal in literals:
        assert literal in _tracked() or literal in _tracked_basenames(), literal

    # Named in prose rather than backticks, and the whole point of the Risk
    # comment - it is where the tier rules are explained.
    for prose_path in re.findall(r"\bdocs/[\w./-]+\.md\b", text):
        assert prose_path in _tracked(), prose_path


def test_the_requirements_pairing_matches_the_script_that_enforces_it() -> None:
    """The template names the two paths `check_requirements_sync.py` compares.

    The instruction "keep `requirements_component.txt` in sync" is a hand copy
    of that script's two constants and of the manifest key it reads.
    """
    tree = ast.parse(_read(_SYNC_SCRIPT))
    constants = {
        target.id: ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert {"MANIFEST", "REQUIREMENTS"} <= set(constants), constants

    text = _read(_PR_TEMPLATE)
    for name in ("MANIFEST", "REQUIREMENTS"):
        basenames = re.findall(r"'([^']+)'", constants[name])
        assert basenames, constants[name]
        assert f"`{basenames[-1]}`" in text, (
            f"{basenames[-1]} is what the sync check reads and the template "
            "no longer names it"
        )

    subscripts = {
        node.slice.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
    }
    assert "requirements" in subscripts
    assert "`requirements`" in text


def test_the_tier_label_promise_matches_the_labeller_rules() -> None:
    """`tier/*` is the prefix `.github/policies/risk-tiers.yml` actually produces."""
    rules = yaml.safe_load(_read(_RISK_TIERS_YML))
    names = [tier["name"] for tier in rules["tiers"]]
    assert len(names) >= 3
    assert all(name.startswith("tier/") for name in names), names
    assert "tier/*" in _read(_PR_TEMPLATE)


@pytest.mark.parametrize(("phrase", "path"), sorted(_BREAKING_CLAIMS.items()))
def test_each_breaking_claim_is_backed_by_a_breaking_tier_path(
    phrase: str, path: str
) -> None:
    """What the Risk section calls breaking is what the labeller calls it."""
    risk = _pr_sections()["Risk"]
    assert risk.count(phrase) == 1, f"{phrase!r} appears {risk.count(phrase)} times"

    rules = yaml.safe_load(_read(_RISK_TIERS_YML))
    breaking = next(t for t in rules["tiers"] if t["name"] == "tier/breaking")
    assert path in breaking["paths"], (
        f"the template calls a {phrase} change breaking, but risk-tiers.yml no "
        f"longer puts {path} in tier/breaking"
    )


def test_every_committed_checkbox_is_unchecked() -> None:
    """A pre-ticked box would have every pull request claim it was verified."""
    boxes = re.findall(r"^- \[(.)\]", _read(_PR_TEMPLATE), flags=re.MULTILINE)
    assert len(boxes) >= 6, boxes
    assert set(boxes) == {" "}, boxes


def test_the_verification_and_risk_boxes_are_in_their_own_sections() -> None:
    """Moving a box out of its section would change what it is asking."""
    verified = re.findall(
        r"^- \[ \] (.+)$", _pr_sections()["How it was verified"], flags=re.MULTILINE
    )
    risk = re.findall(r"^- \[ \] (.+)$", _pr_sections()["Risk"], flags=re.MULTILINE)
    assert len(verified) >= 4, verified
    assert len(risk) >= 2, risk
    assert any("pytest" in line for line in verified)
    assert any("manifest.json" in line for line in risk)


def test_every_html_comment_in_the_template_is_closed() -> None:
    """An unterminated `<!--` swallows the rest of the template on GitHub.

    GitHub renders the file into the pull request body with no validation, so
    a missing `-->` silently removes every section below it.
    """
    text = _read(_PR_TEMPLATE)
    opens = [m.start() for m in re.finditer(r"<!--", text)]
    closes = [m.start() for m in re.finditer(r"-->", text)]
    assert len(opens) >= 4
    assert len(opens) == len(closes), (opens, closes)
    for index, start in enumerate(opens):
        assert closes[index] > start
        if index + 1 < len(opens):
            assert closes[index] < opens[index + 1], "nested or unclosed comment"


# --------------------------------------------------------------------------
# Issue templates
# --------------------------------------------------------------------------


@functools.cache
def _issue_template_paths() -> tuple[Path, ...]:
    """Return every legacy Markdown issue template in the chooser directory."""
    return tuple(sorted(_ISSUE_TEMPLATE_DIR.glob("*.md")))


def test_the_issue_template_directory_was_read() -> None:
    """The chooser directory holds the templates these tests then check."""
    paths = _issue_template_paths()
    assert paths, "no issue templates found"
    assert _BUG_REPORT in paths


@pytest.mark.parametrize("path", _issue_template_paths(), ids=lambda p: p.name)
def test_every_issue_template_would_appear_in_the_chooser(path: Path) -> None:
    """Frontmatter parses and carries the keys GitHub requires.

    A legacy Markdown template with unparseable frontmatter is dropped from
    the chooser silently - there is no error and no warning anywhere.
    """
    front = _frontmatter(path)
    assert isinstance(front.get("name"), str) and front["name"].strip()
    assert isinstance(front.get("about"), str) and front["about"].strip()
    for optional in ("title", "labels", "assignees"):
        assert isinstance(front.get(optional, ""), str), optional


def test_issue_template_names_are_unique() -> None:
    """Two templates sharing a name are indistinguishable in the chooser."""
    names = [_frontmatter(path)["name"] for path in _issue_template_paths()]
    assert len(names) == len(set(names)), names


def test_any_label_an_issue_template_requests_is_one_the_repository_uses() -> None:
    """A template cannot apply a label nothing else in the repository defines."""
    rules = yaml.safe_load(_read(_RISK_TIERS_YML))
    known = {tier["name"] for tier in rules["tiers"]}
    known |= {size["name"] for size in rules["sizes"]}
    labeller = yaml.safe_dump(yaml.safe_load(_read(_WORKFLOWS / "labeler.yml")))
    known |= set(re.findall(r"[\w/-]+", labeller))
    for path in _issue_template_paths():
        requested = [
            label.strip()
            for label in _frontmatter(path).get("labels", "").split(",")
            if label.strip()
        ]
        for label in requested:
            assert label in known, f"{path.name} requests unknown label {label!r}"


def test_the_debug_recipe_names_the_logger_the_component_registers() -> None:
    """The form's logger key is the package path `const.py` derives."""
    text = _read(_BUG_REPORT)
    logger_name = _component_logger_name()
    assert logger_name == "custom_components.sensi"

    blocks = _fenced_blocks(text)
    assert len(blocks) == 1, blocks
    recipe = yaml.safe_load(blocks[0])
    assert isinstance(recipe, dict), recipe
    assert recipe["action"] == f"{LOGGER_DOMAIN}.{SERVICE_SET_LEVEL}"
    assert list(recipe["data"]) == [logger_name], recipe["data"]
    assert recipe["data"][logger_name].upper() in logging.getLevelNamesMapping()


def test_the_debug_marker_in_the_prose_is_the_same_logger_name() -> None:
    """`[custom_components.sensi]` is what a user greps for in their log."""
    markers = [
        literal
        for literal in _backticked(_read(_BUG_REPORT))
        if classify(literal) == "log-marker"
    ]
    assert markers, "the form no longer tells the reporter what to look for"
    assert all(marker.strip("[]") == _component_logger_name() for marker in markers)


def test_every_backticked_literal_in_the_bug_form_classifies() -> None:
    """No literal in the form escapes classification."""
    literals = _backticked(_read(_BUG_REPORT))
    assert len(literals) >= 2
    for literal in literals:
        classify(literal)


# --------------------------------------------------------------------------
# ACMM waivers
# --------------------------------------------------------------------------


def test_the_waiver_file_parses_into_waivers() -> None:
    """`.acmm.yml` is a mapping with a non-empty `waivers` list."""
    document = _acmm()
    assert isinstance(document, dict), document
    assert isinstance(document.get("waivers"), list)
    assert document["waivers"], "no waivers to check"


def test_every_waiver_is_well_formed() -> None:
    """Each entry carries the id, the provider and the reasoning."""
    ids = []
    for waiver in _acmm()["waivers"]:
        assert set(waiver) == {"id", "satisfied_by", "reason"}, waiver
        assert waiver["id"].startswith("acmm:"), waiver["id"]
        assert waiver["satisfied_by"].strip()
        assert len(waiver["reason"].split()) >= 20, waiver["id"]
        ids.append(waiver["id"])
    assert len(ids) == len(set(ids)), ids


def test_every_path_a_waiver_names_resolves_or_is_asserted_absent() -> None:
    """A waiver's reasoning cannot cite a file that is not what it says."""
    paths = set(
        re.findall(
            r"(?<![\w./-])\.?[\w][\w./-]*\.(?:yml|yaml|md|py|json)\b", _acmm_reasons()
        )
    )
    assert len(paths) >= 2, paths
    absent_basenames = {path.rsplit("/", 1)[-1] for path in _MUST_STAY_ABSENT}
    for path in paths:
        if path in _MUST_STAY_ABSENT or path in absent_basenames:
            continue
        assert path in _tracked() or path in _tracked_basenames(), path


@pytest.mark.parametrize("path", _MUST_STAY_ABSENT)
def test_the_removed_workflow_is_still_removed(path: str) -> None:
    """The waiver's basis is this file's absence, so absence is the assertion."""
    assert path.rsplit("/", 1)[-1] in _acmm_reasons(), (
        f"{path} is no longer mentioned - the waiver it justified may have "
        "changed basis"
    )
    assert path not in _tracked_or_new(), (
        f"{path} exists again while .acmm.yml still waives the criterion it matched"
    )
    assert not (_ROOT / path).exists()


def test_no_workflow_reintroduces_the_waived_criterion() -> None:
    """Nothing under `.github/workflows/` re-opens the second autonomous path.

    The waiver tells an external reader that the only file matching these
    criteria was removed. A new workflow carrying the same markers makes that
    statement false, from outside, with nothing here to notice.
    """
    workflows = sorted(_WORKFLOWS.glob("*.yml")) + sorted(_WORKFLOWS.glob("*.yaml"))
    assert len(workflows) >= 5, workflows
    for path in workflows:
        text = _read(path)
        for marker in _AI_FIX_MARKERS:
            assert marker not in text, f"{path.name} contains {marker!r}"


def test_the_cited_security_policy_heading_exists_exactly_once() -> None:
    """Each `docs/SECURITY-AI.md, "..."` citation resolves to one heading."""
    quoted = re.findall(r'docs/SECURITY-AI\.md[^".]*"([^"]+)"', _acmm_reasons())
    assert quoted, "no heading citation found in .acmm.yml"
    doc = _read(_SECURITY_AI)
    headings = [
        line.lstrip("#").strip() for line in doc.splitlines() if line.startswith("#")
    ]
    assert len(headings) >= 5
    for phrase in quoted:
        assert headings.count(phrase) == 1, (
            f"{phrase!r} matches {headings.count(phrase)} headings in "
            "docs/SECURITY-AI.md"
        )


def test_every_waiver_points_at_the_security_policy() -> None:
    """The reasoning is kept where a human reads it, not only in the waiver."""
    for waiver in _acmm()["waivers"]:
        assert "docs/SECURITY-AI.md" in waiver["reason"], waiver["id"]
