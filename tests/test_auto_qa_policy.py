"""The committed `.github/auto-qa-tuning.json`, joined to what it describes.

`tests/test_auto_qa_tuner.py` exercises `scripts/auto_qa_tuner.py` against
hand-written policy fixtures, and checks that the shipped file still carries
the keys `evaluate()` indexes. What no test checked is whether the *values* in
the shipped file are true of this repository.

They are all copies of something else. `current` is a copy of `MIN_COVERAGE` in
`coverage-gate.yml`; `variable` and `enforced_in` are the strings the tuner
prints as apply-instructions, so they have to name an environment key and a
workflow that exist; `source` is the report name the nightly guard tests for;
`policy.headroom` is restated in a comment in the workflow it constrains. Three
more keys - `floor`, `direction` and `policy.rationale` - are read by nobody at
all, so nothing at all stopped them describing a policy the tuner does not
implement.

Every drift here is silent. A `current` that lags `MIN_COVERAGE` makes the
nightly reason about a gate CI is not enforcing, and its "to apply, set
`MIN_COVERAGE: 94`" line then proposes a *lowering* in the voice of a ratchet.
"""

import ast
import importlib.util
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]

_POLICY = _ROOT / ".github" / "auto-qa-tuning.json"
_TUNER = _ROOT / "scripts" / "auto_qa_tuner.py"
_TUNER_TEST = _ROOT / "tests" / "test_auto_qa_tuner.py"
_COVERAGE_GATE = _ROOT / ".github" / "workflows" / "coverage-gate.yml"
_NIGHTLY = _ROOT / ".github" / "workflows" / "nightly.yml"

# The gate the file ships with. A second gate would need its own joins - the
# ones below are all specific to line coverage - so adding one has to fail
# here rather than arrive unchecked.
_GATE_NAME = "line_coverage"

# Keys in the shipped gate that `scripts/auto_qa_tuner.py` never subscripts,
# mapped to the test in this module that gives each one meaning. A key that is
# read by no code and asserted by no test describes nothing.
_UNREAD_GATE_KEYS = {
    "source": "test_the_source_names_the_report_the_tuner_defaults_to",
    "floor": "test_the_thresholds_are_ordered",
}
_UNREAD_POLICY_KEYS = {
    "direction": "test_up_only_is_what_evaluate_actually_does",
    "rationale": "test_the_rationale_only_cites_keys_the_policy_carries",
}

_spec = importlib.util.spec_from_file_location("auto_qa_tuner_policy", _TUNER)
auto_qa_tuner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auto_qa_tuner)


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    """Return a repository-relative path, for readable failure messages."""
    return path.relative_to(_ROOT).as_posix()


def _tracked_files() -> frozenset[str]:
    """Return every path git tracks, read from git rather than a walk.

    A walk would see `coverage.xml` and any local virtualenv, so a path the
    policy names could "exist" without being committed.
    """
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return frozenset(part for part in out.split("\0") if part)


def _policy() -> dict:
    """Return the committed policy document."""
    return json.loads(_read(_POLICY))


def _gate() -> dict:
    """Return the committed line-coverage gate definition."""
    return _policy()["gates"][_GATE_NAME]


def _workflow(path: Path) -> dict:
    """Return a parsed workflow file."""
    return yaml.safe_load(_read(path))


def _step(path: Path, name: str) -> dict:
    """Return the named step from anywhere in a workflow."""
    for job in _workflow(path)["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == name:
                return step
    raise AssertionError(f"{_rel(path)} has no step named {name!r}")


def _tuner_tree() -> ast.Module:
    """Return the parsed source of the tuner script."""
    return ast.parse(_read(_TUNER))


def _subscripted_keys(tree: ast.Module, variable: str) -> set[str]:
    """Return the constant string keys the source indexes on `variable`."""
    keys = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == variable
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys


def _tuner_option_default(option: str) -> str:
    """Return the default `main()` gives an argparse option, from the source.

    Read from the AST rather than by running the parser, because the value
    under test is the one committed in the script, not one a test can pass.
    """
    for node in ast.walk(_tuner_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        names = [a.value for a in node.args if isinstance(a, ast.Constant)]
        if option not in names:
            continue
        for keyword in node.keywords:
            if keyword.arg != "default":
                continue
            value = keyword.value
            if isinstance(value, ast.Call) and value.args:
                value = value.args[0]
            assert isinstance(value, ast.Constant), (
                f"the default for {option} is not a literal this test can read"
            )
            return str(value.value)
    raise AssertionError(f"{_rel(_TUNER)} defines no {option} option with a default")


def _comment_text(document: dict) -> str:
    """Return a policy `$comment` block as one whitespace-collapsed line."""
    return re.sub(r"\s+", " ", " ".join(document["$comment"])).strip()


def test_the_file_carries_exactly_the_top_level_keys_these_tests_join() -> None:
    """A new top-level section would arrive with nothing checking it."""
    assert set(_policy()) == {"$comment", "version", "gates", "review"}


def test_the_only_gate_is_the_one_with_joins() -> None:
    """Every assertion below is specific to the line-coverage gate."""
    assert set(_policy()["gates"]) == {_GATE_NAME}, (
        "a second gate needs its own joins to whatever enforces it; the tests "
        "in this module only reach line coverage"
    )


def test_the_schema_version_is_the_shape_these_tests_encode() -> None:
    """Nothing reads `version`, so it can only mean what a test makes it mean."""
    assert _policy()["version"] == 1


def test_every_committed_gate_key_is_read_by_the_tuner_or_named_here() -> None:
    """A key no code reads and no test asserts describes nothing."""
    read = _subscripted_keys(_tuner_tree(), "gate")
    committed = set(_gate())

    assert committed == read | set(_UNREAD_GATE_KEYS), (
        f"{_rel(_POLICY)}'s gate keys must be the ones {_rel(_TUNER)} indexes "
        f"plus those listed in _UNREAD_GATE_KEYS (read: {sorted(read)})"
    )


def test_every_committed_policy_key_is_read_by_the_tuner_or_named_here() -> None:
    """The same rule inside the nested `policy` block."""
    read = _subscripted_keys(_tuner_tree(), "policy")
    committed = set(_gate()["policy"])

    assert committed == read | set(_UNREAD_POLICY_KEYS), (
        f"{_rel(_POLICY)}'s policy keys must be the ones {_rel(_TUNER)} "
        f"indexes plus those listed in _UNREAD_POLICY_KEYS (read: {sorted(read)})"
    )


@pytest.mark.parametrize(
    "test_name", sorted({*_UNREAD_GATE_KEYS.values(), *_UNREAD_POLICY_KEYS.values()})
)
def test_each_unread_key_names_a_test_that_exists(test_name: str) -> None:
    """The escape hatch above is only honest while the named tests are real."""
    assert callable(globals().get(test_name)), (
        f"{test_name} is named as the meaning of an unread policy key but is "
        "not defined in this module"
    )


def test_the_current_threshold_is_the_one_ci_enforces() -> None:
    """`current` is a second copy of `MIN_COVERAGE`, and drifts silently.

    The tuner compares the measurement against this number and prints a line
    telling a maintainer to set the workflow variable to `current + step`. If
    `current` lags the workflow, that instruction lowers the gate.
    """
    gate = _gate()
    env = _workflow(_COVERAGE_GATE)["env"]

    assert gate["variable"] in env, (
        f"{_rel(_POLICY)} tells maintainers to set {gate['variable']}, which "
        f"{_rel(_COVERAGE_GATE)} does not define"
    )
    assert int(env[gate["variable"]]) == gate["current"], (
        f"{_rel(_POLICY)} calls the gate {gate['current']}%, "
        f"{_rel(_COVERAGE_GATE)} enforces {env[gate['variable']]}%"
    )


def test_enforced_in_names_a_committed_workflow() -> None:
    """The apply-instruction has to point at a file that exists."""
    assert _gate()["enforced_in"] in _tracked_files()


def test_enforced_in_names_the_workflow_that_actually_fails_a_low_run() -> None:
    """A tracked-but-wrong path would pass the check above and still mislead.

    The gate is the `--cov-fail-under` that reads the variable; that is what
    makes this workflow, rather than any other that mentions coverage, the
    place a raise takes effect.
    """
    gate = _gate()
    enforcing = _read(_ROOT / gate["enforced_in"])

    assert f'--cov-fail-under="${{{gate["variable"]}}}"' in enforcing, (
        f"{gate['enforced_in']} does not fail a run using {gate['variable']}, "
        "so setting that variable there would not move any gate"
    )


def test_the_thresholds_are_ordered() -> None:
    """`floor <= current <= ceiling`, the invariant an up-only ratchet needs.

    `floor` is read by no code at all. A `floor` above `current` would mean the
    committed gate is already below the policy's own minimum, and nothing but
    this assertion would say so.
    """
    gate = _gate()

    assert 0 <= gate["floor"] <= gate["current"] <= gate["ceiling"] <= 100


def test_the_step_and_observation_counts_are_positive() -> None:
    """A zero step is a ratchet that proposes nothing, forever."""
    policy = _gate()["policy"]

    assert policy["step"] >= 1
    assert policy["min_observations"] >= 1


def test_the_shipped_numbers_can_still_raise_the_gate() -> None:
    """Ordering is not enough: the numbers have to admit a proposal.

    With a perfect measurement the shipped gate must propose a raise. A
    headroom wider than the distance to 100, a zero step, or a ceiling at the
    current threshold each leave the tuner silent while still ordered.
    """
    gate = _gate()

    result = auto_qa_tuner.evaluate(100.0, gate)

    assert result["change"] is True, result["reason"]
    assert result["proposed"] == gate["current"] + gate["policy"]["step"]


def test_a_measurement_at_the_threshold_proposes_nothing() -> None:
    """The headroom guard, against the committed numbers rather than fixtures."""
    gate = _gate()

    result = auto_qa_tuner.evaluate(float(gate["current"]), gate)

    assert result["change"] is False
    assert result["proposed"] == gate["current"]


@pytest.mark.parametrize("measured", [0.0, 50.0, 92.9, 93.0, 97.5, 99.99, 100.0])
def test_up_only_is_what_evaluate_actually_does(measured: float) -> None:
    """`direction` is read by no code; this is what makes it a claim.

    Whatever the measurement, the proposal never comes in under the threshold
    already in force - a tuner that tracked the measurement downwards would be
    a gate that erases its own history on one bad night.
    """
    gate = _gate()

    assert gate["policy"]["direction"] == "up_only"
    assert auto_qa_tuner.evaluate(measured, gate)["proposed"] >= gate["current"]


def test_the_tuner_cannot_apply_what_it_proposes() -> None:
    """`review.owner` is only "human" while the script cannot write files."""
    review = _policy()["review"]
    assert set(review) == {"owner", "cadence", "note"}
    assert review["owner"] == "human"

    for node in ast.walk(_tuner_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"write", "write_text", "writelines"}, (
                f"{_rel(_TUNER)} writes a file, so the policy's human review "
                "is no longer what stands between a proposal and a change"
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open", f"{_rel(_TUNER)} opens a file"


def test_the_source_names_the_report_the_tuner_defaults_to() -> None:
    """`source` is read by no code; the default in the script is what runs."""
    assert _gate()["source"] == _tuner_option_default("--coverage")


def test_the_nightly_guard_tests_for_the_report_the_source_names() -> None:
    """The guard, the default and `source` are three copies of one filename.

    The nightly step runs the tuner only `if [ -f coverage.xml ]`, passing no
    `--coverage`, so a rename anywhere in that chain ends gate proposals
    silently - the step succeeds and writes nothing.
    """
    source = _gate()["source"]
    body = _step(_NIGHTLY, "Propose a gate adjustment")["run"]

    assert f"[ -f {source} ]" in body
    assert "--coverage" not in body, (
        "the step overrides the default report path, so `source` no longer "
        "describes what the nightly reads"
    )


def test_the_suite_that_produces_that_report_runs_before_the_tuner() -> None:
    """Nothing writes the report unless the test step asks for the XML."""
    body = _step(_NIGHTLY, "Tests with coverage")["run"]

    assert "--cov-report=xml" in body, (
        "without the XML report the tuner's guard never fires and the nightly "
        "stops proposing, with no failure anywhere"
    )


def test_the_enforcing_workflow_quotes_the_policy_headroom() -> None:
    """The comment explaining why the gate sits below the measurement.

    It is prose restating `policy.headroom`; asserting exactly one match keeps
    a reworded comment loud rather than letting a second stale copy appear.
    """
    quoted = re.findall(r"calls for (\d+) points? of headroom", _read(_COVERAGE_GATE))

    assert quoted == [str(_gate()["policy"]["headroom"])]


def test_the_rationale_only_cites_keys_the_policy_carries() -> None:
    """The rationale names its numbers in backticks; renaming one strands it."""
    gate = _gate()
    rationale = " ".join(gate["policy"]["rationale"])
    cited = set(re.findall(r"`([a-z_]+)`", rationale))

    assert cited, "the rationale cites no policy key, so nothing joins it"
    assert cited <= set(gate) | set(gate["policy"])


def test_the_comment_points_at_prose_and_code_that_exist() -> None:
    """`$comment` names the script and the doc; both must be committed."""
    comment = _comment_text(_policy())
    named = set(re.findall(r"(?<![\w./-])[\w][\w./-]*\.(?:py|md|yml|json)", comment))

    assert named, "the comment names no file, so it cannot be checked"
    assert named <= _tracked_files()
    assert _rel(_TUNER) in named


def test_the_quality_doc_describes_the_policy_by_path() -> None:
    """docs/quality.md is the prose half the comment defers to."""
    comment = _comment_text(_policy())
    doc = re.search(r"Prose: ([\w./-]+\.md)", comment)
    assert doc, f"{_rel(_POLICY)}'s comment no longer names its prose"

    text = _read(_ROOT / doc.group(1))

    assert _rel(_POLICY) in text
    assert _rel(_TUNER) in text


def test_the_nightly_is_the_job_the_review_cadence_describes() -> None:
    """The cadence promises proposals in a job summary; one job writes them."""
    cadence = _policy()["review"]["cadence"]
    body = _step(_NIGHTLY, "Propose a gate adjustment")["run"]

    assert "nightly" in cadence
    assert "job summary" in cadence
    assert "GITHUB_STEP_SUMMARY" in body, (
        "the proposal goes to stdout, where the cadence says a reader will "
        "not be looking"
    )


def test_the_tuner_tests_fixture_ships_the_values_the_file_ships() -> None:
    """`tests/test_auto_qa_tuner.py`'s `gate()` claims to carry them.

    Its fixture is the shape every tuner test starts from, so a shipped value
    that stops matching turns that whole module into a test of a policy this
    repository does not use.
    """
    spec = importlib.util.spec_from_file_location("auto_qa_tuner_tests", _TUNER_TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.gate()
    shipped = _gate()

    for key, value in fixture.items():
        if key == "policy":
            continue
        assert value == shipped[key], f"gate() ships {key}={value!r}"
    for key, value in fixture["policy"].items():
        assert value == shipped["policy"][key], f"gate() ships policy.{key}={value!r}"
