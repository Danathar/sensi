"""The prose behind the risk tiers, `docs/risk-tiers.md`.

This document is the human-readable model of three machine-readable things:
the tier and size rows in `.github/risk-tiers.yml`, the ordering and bucketing
logic in `scripts/classify_pr.py`, and the label application in
`.github/workflows/labeler.yml`. Until this module existed nothing opened it.

The five references to the path in `tests/` all use it as an input *string* -
a glob-matching case in `tests/test_classify_pr.py`, a path-resolution case in
`tests/test_contributor_templates.py`, an EditorConfig case in
`tests/test_file_conventions.py` - and it is not listed in
`tests/test_instruction_docs.py`'s `_PROSE_FILES`, so unlike every other
document under `docs/` not even its markdown links were resolved. Six
falsehoods written into it at once (the wrong module in two tier lists, a
restated size bound, a renamed classifier flag, a restated character cap, and
the central ordering rule inverted from "highest" to "lowest") left the whole
suite green.

What makes the drift silent rather than loud: nothing in the pipeline reads
this file. A tier can be renamed, reordered, given a path or lose one, and a
size bucket can move, with the labels on every pull request changing
accordingly and the document that explains them saying the old thing.

So every assertion below is derived, never restated: the tier names and their
order come from `.github/risk-tiers.yml`, the platform modules come from
`SUPPORTED_PLATFORMS` in `custom_components/sensi/__init__.py`, the size
bounds are replayed through `classify_pr.classify()`, the documented commands
are run through `classify_pr.main()`, and the default branch comes from what
the workflows actually trigger on.
"""

import ast
import importlib.util
import io
import json
from pathlib import Path
import re
import shlex
import subprocess

import pytest
import yaml

from homeassistant.const import Platform

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "risk-tiers.md"
_RULES = _ROOT / ".github" / "risk-tiers.yml"
_WORKFLOW = _ROOT / ".github" / "workflows" / "labeler.yml"
_SCRIPT = _ROOT / "scripts" / "classify_pr.py"
_COMPONENT = _ROOT / "custom_components" / "sensi"
_E2E_CONFTEST = _ROOT / "tests" / "e2e" / "conftest.py"

_spec = importlib.util.spec_from_file_location("classify_pr_doc", _SCRIPT)
classify_pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(classify_pr)

# The pattern the catch-all tier uses. It matches every path by construction,
# so it says nothing about which paths a tier is *about* - every derivation
# below that asks "which tier owns this path" has to exclude it.
_CATCH_ALL = "**"

# Shipped component files that match no tier but the catch-all, and so carry
# `tier/support` - the tier whose own label says it "cannot reach a user's
# installation". Resolved by issue #252: `const.py` now carries
# `tier/breaking` and `utils.py` `tier/runtime`, so this mapping stays empty.
_SHIPPED_BUT_UNTIERED: dict[str, str] = {}


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """Return a file's text with every run of whitespace collapsed to a space.

    The document is hard-wrapped prose, so a sentence quoted here is split
    across lines at whatever column the wrap fell on. Searching the raw text
    would make each assertion sensitive to re-wrapping rather than the claim.
    """
    return " ".join(_read(path).split())


def _rel(path: Path) -> str:
    """Return a repository-relative path, for readable failure messages."""
    return path.relative_to(_ROOT).as_posix()


def _tracked() -> frozenset[str]:
    """Return every path git tracks, read from git rather than a walk.

    A walk would see `coverage.xml`, `__pycache__` and any local virtualenv,
    so a path the prose names could "exist" without being committed.
    """
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return frozenset(entry for entry in out.split("\0") if entry)


_TRACKED = _tracked()
_TRACKED_DIRS = frozenset(
    str(parent)
    for path in _TRACKED
    for parent in Path(path).parents
    if str(parent) != "."
)


def _rules() -> dict:
    """Return the parsed rules file."""
    return yaml.safe_load(_read(_RULES))


def _tiers() -> list[dict]:
    """Return the tier rows in file order, which is the order that decides."""
    return _rules()["tiers"]


def _sizes() -> list[dict]:
    """Return the size rows in file order, smallest first."""
    return _rules()["sizes"]


# `### `tier/breaking` — can break an existing install on upgrade`
_TIER_HEADING = re.compile(r"^### `(tier/[\w-]+)` — (.+?)\s*$", re.MULTILINE)


def _sections() -> dict[str, str]:
    """Return each tier's documented body, keyed by tier name.

    The body runs from its own heading to the next heading of any level, so a
    claim cannot drift into a neighbouring tier's section and still be found.
    """
    text = _read(_DOC)
    matches = list(_TIER_HEADING.finditer(text))
    bodies: dict[str, str] = {}
    for match in matches:
        start = match.end()
        following = re.search(r"^#{2,3} ", text[start:], re.MULTILINE)
        end = start + following.start() if following else len(text)
        bodies[match.group(1)] = text[start:end]
    assert len(bodies) == len(matches), f"{_rel(_DOC)} documents a tier twice"
    return bodies


_BACKTICKED_PATH = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*"
    r"\.(?:py|json|toml|ini|yml|yaml|md|mdc|txt|sh|cfg)|"
    r"[A-Za-z0-9_][A-Za-z0-9_./-]*/)`"
)


def _path_exists(name: str) -> bool:
    """Return whether a path named in prose resolves to something committed.

    Three spellings, because the document uses all three: a repository path,
    a bare file name (the tier lists write `client.py`, not
    `custom_components/sensi/client.py`), and a directory.
    """
    bare = name.rstrip("/")
    if name in _TRACKED or bare in _TRACKED or bare in _TRACKED_DIRS:
        return True
    inside = f"custom_components/sensi/{bare}"
    if inside in _TRACKED or inside in _TRACKED_DIRS:
        return True
    return any(Path(tracked).name == bare for tracked in _TRACKED)


def _component_path(basename: str) -> str:
    """Return the repository path of a component module named bare in prose."""
    return f"custom_components/sensi/{basename}"


def _owning_patterns(tier: dict) -> list[str]:
    """Return a tier's patterns with the catch-all removed."""
    return [pattern for pattern in tier["paths"] if pattern != _CATCH_ALL]


# --------------------------------------------------------------------------
# The tiers the document describes
# --------------------------------------------------------------------------


def test_the_documented_tiers_are_the_committed_tiers_in_order() -> None:
    """Order is the rule: the first matching tier in file order wins."""
    documented = [match.group(1) for match in _TIER_HEADING.finditer(_read(_DOC))]
    committed = [tier["name"] for tier in _tiers()]
    assert documented == committed, (
        f"{_rel(_DOC)} documents {documented} but {_rel(_RULES)} declares "
        f"{committed}; the order is what decides which tier wins"
    )


@pytest.mark.parametrize("tier", _tiers(), ids=lambda tier: tier["name"])
def test_each_heading_tagline_agrees_with_the_label_description(tier: dict) -> None:
    """The heading and the label GitHub shows must say the same thing.

    Not equality: the document shortens "the live Sensi service" to "the live
    service", so the tagline is required to be an in-order subsequence of the
    words in `label_description`. Rewording either side out of agreement
    fails; shortening for prose does not.
    """
    headings = dict(_TIER_HEADING.findall(_read(_DOC)))
    tagline = headings[tier["name"]].lower()
    description = tier["label_description"].lower()

    words = iter(re.findall(r"[\w']+", description))
    missing = [word for word in re.findall(r"[\w']+", tagline) if word not in words]
    assert not missing, (
        f"{_rel(_DOC)}'s `{tier['name']}` heading says {tagline!r}, which does "
        f"not read in order against its label_description {description!r}; "
        f"{missing} is out of order or absent"
    )


@pytest.mark.parametrize("tier", _tiers(), ids=lambda tier: tier["name"])
def test_each_tier_section_states_what_is_required(tier: dict) -> None:
    """A tier the reader cannot act on is a tier that does nothing."""
    body = _sections()[tier["name"]]
    assert "**What is required:**" in body, (
        f"{_rel(_DOC)}'s `{tier['name']}` section no longer says what it requires"
    )


@pytest.mark.parametrize(
    "tier_name",
    [tier["name"] for tier in _tiers() if _owning_patterns(tier)][:2],
    ids=lambda name: name,
)
def test_a_tier_lists_exactly_the_modules_it_owns(tier_name: str) -> None:
    """The two file-listing tiers name their paths, both directions.

    `tier/breaking` and `tier/runtime` enumerate bare module names in prose.
    A path added to one of them in the rules file and not here leaves the
    document describing a narrower tier than the one being applied.
    """
    tier = next(row for row in _tiers() if row["name"] == tier_name)
    committed = {Path(pattern).name for pattern in _owning_patterns(tier)}
    # The first paragraph of the section, which is the list itself.
    listing = _sections()[tier_name].strip().split("\n\n")[0]
    documented = set(_BACKTICKED_PATH.findall(listing))

    assert documented == committed, (
        f"{_rel(_DOC)}'s `{tier_name}` section lists {sorted(documented)} but "
        f"{_rel(_RULES)} gives it {sorted(committed)}"
    )


def _supported_platforms() -> list[str]:
    """Return SUPPORTED_PLATFORMS from `__init__.py`, by AST then by value.

    Read from the source rather than imported so the obligation comes from
    what the integration actually sets up: a sixth platform added there has
    to appear in `tier/behaviour` and in the prose, or this fails.
    """
    tree = ast.parse(_read(_COMPONENT / "__init__.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SUPPORTED_PLATFORMS"
            for target in node.targets
        ):
            names = [
                element.attr
                for element in node.value.elts
                if isinstance(element, ast.Attribute)
            ]
            assert len(names) == len(node.value.elts), (
                "SUPPORTED_PLATFORMS no longer lists Platform attributes"
            )
            return [Platform[name].value for name in names]
    raise AssertionError("SUPPORTED_PLATFORMS is not assigned in __init__.py")


def test_tier_behaviour_is_the_platform_modules_and_the_strings() -> None:
    """The platform modules are a computed set, not a remembered list."""
    behaviour = next(tier for tier in _tiers() if tier["name"] == "tier/behaviour")
    expected = {
        _component_path(f"{platform}.py") for platform in _supported_platforms()
    } | {
        _component_path("strings.json"),
        _component_path("translations/**"),
    }
    assert set(behaviour["paths"]) == expected, (
        f"{_rel(_RULES)} gives tier/behaviour {sorted(behaviour['paths'])}, but "
        f"the platforms the integration sets up make it {sorted(expected)}"
    )

    section = " ".join(_sections()["tier/behaviour"].split())
    assert "The platform modules" in section, (
        f"{_rel(_DOC)} no longer describes tier/behaviour as the platform modules"
    )
    # Scoped to this section, not to the whole document: `capabilities.py` is
    # also a `tier/runtime` path, so a document-wide search for it is
    # satisfied by that list and says nothing about the requirement here.
    for named in ("`strings.json`", "`translations/`", "`capabilities.py`"):
        assert named in section, (
            f"{_rel(_DOC)}'s tier/behaviour section no longer names {named}"
        )


def test_tier_support_is_the_catch_all_and_comes_last() -> None:
    """The default tier is last, and it is the only one matching everything."""
    tiers = _tiers()
    assert tiers[-1]["name"] == "tier/support", (
        f"{_rel(_RULES)} no longer ends with the catch-all tier"
    )
    assert tiers[-1]["paths"] == [_CATCH_ALL], (
        f"tier/support is {tiers[-1]['paths']}, not the catch-all"
    )
    catch_alls = [tier["name"] for tier in tiers if _CATCH_ALL in tier["paths"]]
    assert catch_alls == ["tier/support"], (
        f"more than one tier matches everything: {catch_alls}"
    )
    assert "The default." in _sections()["tier/support"], (
        f"{_rel(_DOC)} no longer calls tier/support the default"
    )


def test_every_shipped_module_is_tiered_or_declared() -> None:
    """A shipped file reaching only the catch-all is labelled as harmless.

    `tier/support` says "cannot reach a user's installation". Every file under
    `custom_components/sensi/` that matches no other tier carries that label
    anyway, so each one must be declared here with a reason. A third is a
    failure, not a silent addition - see issue #252.
    """
    owning = [
        (tier["name"], pattern)
        for tier in _tiers()
        for pattern in _owning_patterns(tier)
    ]
    untiered = {
        path
        for path in _TRACKED
        if path.startswith("custom_components/sensi/")
        and not any(classify_pr.matches(path, pattern) for _, pattern in owning)
    }
    assert untiered == set(_SHIPPED_BUT_UNTIERED), (
        f"shipped modules reaching only the catch-all are {sorted(untiered)}, "
        f"but this module declares {sorted(_SHIPPED_BUT_UNTIERED)}; a new one "
        "is labelled tier/support, which says it cannot reach an installation"
    )


# --------------------------------------------------------------------------
# The rule the whole document exists to state
# --------------------------------------------------------------------------


def test_the_document_states_that_the_highest_tier_wins() -> None:
    """The claim itself, in the wording the behavioural tests check."""
    text = _flat(_DOC)
    assert "It is the highest tier whose paths the change touches" in text, (
        f"{_rel(_DOC)} no longer states that the highest matching tier wins"
    )
    assert "not a score, not additive" in text, (
        f"{_rel(_DOC)} no longer rules out an additive reading"
    )
    assert "Tiers are evaluated in file order, highest first" in text, (
        f"{_rel(_DOC)} no longer ties the ordering to the order of the rules file"
    )


def test_the_classifier_stops_at_the_first_matching_tier() -> None:
    """File order can only decide if the loop breaks on the first match."""
    tree = ast.parse(_read(_SCRIPT))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "classify"
    )
    loop = next(node for node in ast.walk(function) if isinstance(node, ast.For))
    assert any(isinstance(node, ast.Break) for node in ast.walk(loop)), (
        "classify() no longer stops at the first matching tier, so the order "
        f"of {_rel(_RULES)} does not decide the tier"
    )


@pytest.mark.parametrize(
    ("higher", "lower"),
    [
        (higher, lower)
        for index, higher in enumerate(_tiers())
        for lower in _tiers()[index + 1 :]
    ],
    ids=lambda tier: tier["name"],
)
def test_the_higher_tier_wins_over_every_lower_one(higher: dict, lower: dict) -> None:
    """Replayed over every ordered pair, not just the documented example."""
    higher_path = _owning_patterns(higher)[0]
    lower_patterns = _owning_patterns(lower)
    lower_path = lower_patterns[0] if lower_patterns else "docs/risk-tiers.md"
    if lower_path.endswith("/**"):
        lower_path = f"{lower_path[:-3]}/en.json"

    result = classify_pr.classify([lower_path, higher_path], 1, _rules())
    assert result["tier"]["name"] == higher["name"], (
        f"a change touching {higher_path} and {lower_path} classified as "
        f"{result['tier']['name']}, not {higher['name']}"
    )


def test_the_worked_example_in_the_prose_classifies_the_way_it_says() -> None:
    """The example is read out of the document, not restated here."""
    match = re.search(
        r"A change touching both `([\w.]+)` and a doc is `(tier/[\w-]+)`",
        _flat(_DOC),
    )
    assert match, f"{_rel(_DOC)} no longer carries its worked example"
    module, expected = match.group(1), match.group(2)

    result = classify_pr.classify([_component_path(module), _rel(_DOC)], 10, _rules())
    assert result["tier"]["name"] == expected, (
        f"{_rel(_DOC)} says {module} plus a doc is {expected}, but the "
        f"classifier answers {result['tier']['name']}"
    )


# --------------------------------------------------------------------------
# Size buckets
# --------------------------------------------------------------------------


_SIZE_BOUND = re.compile(r"`(size/[\w-]+)`(?: ≤(\d+))?")


def _documented_sizes() -> list[tuple[str, int | None]]:
    """Return the size table as the prose states it, in documented order."""
    sentence = re.search(
        r"(`size/XS`[^.]*counting additions plus deletions)", _flat(_DOC)
    )
    assert sentence, f"{_rel(_DOC)} no longer carries the size table"
    return [
        (name, int(bound) if bound else None)
        for name, bound in _SIZE_BOUND.findall(sentence.group(1))
    ]


def test_the_size_table_matches_the_committed_buckets() -> None:
    """Name, order and bound, all three, in both directions."""
    documented = _documented_sizes()
    committed = [(size["name"], size.get("max_lines")) for size in _sizes()]
    assert documented == committed, (
        f"{_rel(_DOC)} states {documented} but {_rel(_RULES)} declares {committed}"
    )


@pytest.mark.parametrize(
    ("name", "bound"),
    [(name, bound) for name, bound in _documented_sizes() if bound is not None],
)
def test_a_documented_size_bound_is_inclusive(name: str, bound: int) -> None:
    """The prose writes ≤, and `classify()` uses `lines <= cap`."""
    rules = _rules()
    path = ["docs/risk-tiers.md"]
    assert classify_pr.classify(path, bound, rules)["size"]["name"] == name, (
        f"{bound} lines is not {name}, but {_rel(_DOC)} writes ≤{bound}"
    )
    assert classify_pr.classify(path, bound + 1, rules)["size"]["name"] != name, (
        f"{bound + 1} lines is still {name}, so the ≤{bound} bound in "
        f"{_rel(_DOC)} is not the bound being applied"
    )


def test_the_uncapped_bucket_is_last_and_catches_everything() -> None:
    """The phrase "above that" only holds if the last bucket has no cap."""
    assert "above that, counting additions plus deletions" in _flat(_DOC)
    sizes = _sizes()
    uncapped = [size["name"] for size in sizes if size.get("max_lines") is None]
    assert uncapped == [sizes[-1]["name"]], (
        f"{_rel(_RULES)} leaves {uncapped} uncapped; exactly the last bucket "
        "may be, or a change above the largest bound gets no size label"
    )
    largest = sizes[-2]["max_lines"]
    result = classify_pr.classify(["docs/risk-tiers.md"], largest * 10, _rules())
    assert result["size"]["name"] == sizes[-1]["name"]


def test_size_counts_additions_plus_deletions() -> None:
    """The prose says how the number is arrived at; the script must agree."""
    assert "counting additions plus deletions" in _flat(_DOC)
    tree = ast.parse(_read(_SCRIPT))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_pr"
    )
    fields = {
        node.args[0].value
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert {"additions", "deletions"} <= fields, (
        f"fetch_pr reads {sorted(fields)}, not the additions plus deletions "
        f"{_rel(_DOC)} describes"
    )


# --------------------------------------------------------------------------
# The commands the document tells a contributor to run
# --------------------------------------------------------------------------


def _documented_commands() -> list[str]:
    """Return the classifier invocations from the bash fence."""
    fence = re.search(r"```bash\n(.*?)```", _read(_DOC), re.DOTALL)
    assert fence, f"{_rel(_DOC)} no longer shows how to run the classifier"
    commands = [line.strip() for line in fence.group(1).splitlines() if line.strip()]
    assert commands, "the fence is empty"
    return commands


@pytest.mark.parametrize("command", _documented_commands())
def test_every_documented_command_runs(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A renamed or dropped flag makes argparse exit 2 here, not in CI.

    The pipeline example is split on `|` and only the classifier half is run;
    its left-hand side is checked separately.
    """
    invocation = command.split("|")[-1].strip()
    argv = shlex.split(invocation)
    assert argv[:2] == ["python3", "scripts/classify_pr.py"], (
        f"{_rel(_DOC)} runs the classifier as {argv[:2]}"
    )

    monkeypatch.setattr(classify_pr.sys, "argv", ["classify_pr.py", *argv[2:]])
    monkeypatch.setattr(
        classify_pr,
        "fetch_pr",
        lambda number, repo: (["custom_components/sensi/client.py"], 30),
    )
    monkeypatch.setattr(
        classify_pr.sys, "stdin", io.StringIO("custom_components/sensi/client.py\n")
    )

    assert classify_pr.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["tier"]["name"].startswith("tier/")
    assert result["size"]["name"].startswith("size/")


def test_the_pipeline_example_diffs_against_the_default_branch() -> None:
    """`master...` has to be the branch the workflows actually gate."""
    piped = [command for command in _documented_commands() if "|" in command]
    assert len(piped) == 1, f"{_rel(_DOC)} shows {len(piped)} piped examples"
    match = re.search(r"git diff --name-only (\S+)\.\.\.", piped[0])
    assert match, f"{_rel(_DOC)}'s piped example is no longer a git diff"

    branches = set()
    for workflow in sorted((_ROOT / ".github" / "workflows").glob("*.yml")):
        # PyYAML resolves the `on:` key to the boolean True.
        triggers = yaml.safe_load(_read(workflow)).get(True) or {}
        if not isinstance(triggers, dict):
            continue
        for event in ("pull_request", "pull_request_target", "push"):
            settings = triggers.get(event) or {}
            if isinstance(settings, dict):
                branches.update(settings.get("branches") or [])
    assert branches == {match.group(1)}, (
        f"{_rel(_DOC)} diffs against {match.group(1)}..., but the workflows "
        f"gate pull requests into {sorted(branches)}"
    )


# --------------------------------------------------------------------------
# The label descriptions, and the cap GitHub imposes on them
# --------------------------------------------------------------------------


def test_the_documented_cap_is_the_cap_the_script_enforces() -> None:
    """One number, stated in prose and applied in code."""
    match = re.search(r"is capped at (\d+) characters", _flat(_DOC))
    assert match, f"{_rel(_DOC)} no longer states the label description cap"
    assert int(match.group(1)) == classify_pr.MAX_LABEL_DESCRIPTION, (
        f"{_rel(_DOC)} says {match.group(1)} but classify_pr.py enforces "
        f"{classify_pr.MAX_LABEL_DESCRIPTION}"
    )


@pytest.mark.parametrize("tier", _tiers(), ids=lambda tier: tier["name"])
def test_every_committed_label_description_fits(tier: dict) -> None:
    """The document's whole reason for mentioning the cap."""
    description = tier["label_description"]
    assert len(description) <= classify_pr.MAX_LABEL_DESCRIPTION, (
        f"{tier['name']}: label_description is {len(description)} characters"
    )


def test_the_script_rejects_an_over_long_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The document says it fails locally; prove it does."""
    rules = _rules()
    rules["tiers"][0]["label_description"] = "x" * (
        classify_pr.MAX_LABEL_DESCRIPTION + 1
    )
    path = tmp_path / "risk-tiers.yml"
    path.write_text(yaml.safe_dump(rules), encoding="utf-8")
    monkeypatch.setattr(classify_pr, "RULES", path)

    with pytest.raises(SystemExit):
        classify_pr.load_rules()

    assert "`scripts/classify_pr.py` rejects an over-long" in _flat(_DOC)


def test_both_descriptions_are_explained_and_both_are_carried() -> None:
    """The document distinguishes the two; every tier must carry both."""
    text = _flat(_DOC)
    assert "Each tier carries two descriptions." in text
    for tier in _tiers():
        for key in ("label_description", "description"):
            assert tier.get(key), f"{tier['name']} has no {key}"
    assert "HTTP 422" in text, (
        f"{_rel(_DOC)} no longer explains what GitHub does with an over-long one"
    )
    assert "422" in _read(_SCRIPT) and "422" in _read(_WORKFLOW), (
        "the HTTP 422 the document warns about is no longer explained where "
        "it is handled"
    )


# --------------------------------------------------------------------------
# The workflow that applies what this document describes
# --------------------------------------------------------------------------


def test_the_named_workflow_runs_the_classifier() -> None:
    """The document names one workflow; it has to be the one that labels."""
    text = _flat(_DOC)
    match = re.search(r"applied\s+automatically by `([^`]+)`", text)
    assert match, f"{_rel(_DOC)} no longer names the workflow that applies labels"
    named = match.group(1)
    assert named in _TRACKED, f"{_rel(_DOC)} names the uncommitted {named}"
    assert named == _rel(_WORKFLOW)

    bodies = "\n".join(
        step.get("run", "")
        for job in yaml.safe_load(_read(_WORKFLOW))["jobs"].values()
        for step in job["steps"]
    )
    assert "scripts/classify_pr.py" in bodies, (
        f"{named} no longer runs the classifier this document describes"
    )


def test_the_workflow_applies_one_tier_and_one_size() -> None:
    """One `tier/*` label and one `size/*` label is a promise about labels."""
    text = _flat(_DOC)
    prefixes = set(re.findall(r"one `(\w+)/\*` label", text))
    assert prefixes == {"tier", "size"}, (
        f"{_rel(_DOC)} promises {sorted(prefixes)} labels per pull request"
    )
    for prefix in prefixes:
        key = "tiers" if prefix == "tier" else "sizes"
        names = [row["name"] for row in _rules()[key]]
        assert all(name.startswith(f"{prefix}/") for name in names), (
            f"{_rel(_RULES)} declares {key} not all named {prefix}/*: {names}"
        )

    workflow = _read(_WORKFLOW)
    assert 'for key in ("tier", "size")' in workflow, (
        f"{_rel(_WORKFLOW)} no longer emits exactly a tier and a size"
    )
    assert "tier/*|size/*)" in workflow, (
        f"{_rel(_WORKFLOW)} no longer drops the stale tier and size labels, so "
        f"a pull request can carry more than the one of each {_rel(_DOC)} promises"
    )


# --------------------------------------------------------------------------
# The document and the rules file point at each other
# --------------------------------------------------------------------------


def test_the_document_and_the_rules_file_cross_reference() -> None:
    """Editing them together only works if each one names the other."""
    assert f"({Path('..') / _rel(_RULES)})".replace("\\", "/") in _read(_DOC), (
        f"{_rel(_DOC)} no longer links to {_rel(_RULES)}"
    )
    assert _rel(_DOC) in _read(_RULES), (
        f"{_rel(_RULES)} no longer points at the prose behind it"
    )
    assert "and this file\ntogether" in _read(_DOC) or "and this file together" in (
        _flat(_DOC)
    ), f"{_rel(_DOC)} no longer requires the two to be edited together"


def test_every_path_the_document_names_is_committed() -> None:
    """A renamed module must not leave the prose describing a missing file."""
    missing = sorted(
        {
            name
            for name in _BACKTICKED_PATH.findall(_read(_DOC))
            if not _path_exists(name)
        }
    )
    assert not missing, f"{_rel(_DOC)} names uncommitted paths: {missing}"


def test_the_runtime_requirement_names_the_fake_that_exists() -> None:
    """`tier/runtime` requires end-to-end cover through a named fake."""
    section = " ".join(_sections()["tier/runtime"].split())
    assert "`FakeSensiBackend`" in section, (
        f"{_rel(_DOC)} no longer names the end-to-end fake tier/runtime requires"
    )
    assert "tests/e2e" in _TRACKED_DIRS
    defined = {
        node.name
        for node in ast.walk(ast.parse(_read(_E2E_CONFTEST)))
        if isinstance(node, ast.ClassDef)
    }
    assert "FakeSensiBackend" in defined, (
        f"{_rel(_E2E_CONFTEST)} no longer defines the fake {_rel(_DOC)} requires"
    )
