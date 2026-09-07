"""Tests for scripts/check_ruleset.py.

Not part of the component, so not measured by the coverage gate - but this is
the only thing that can tell anyone whether `master` is still protected. A
ruleset is repository configuration rather than a file, so the changes that
undo it (a bypass actor added, enforcement switched off, a required check
dropped) leave no trace in any diff and no trace in a checkout.

The direction that matters is the one that is easy to get backwards: the check
must fail on *weakening* and stay quiet about tightening. A checker that
reported drift every time somebody added a rule would be turned off within a
month, and then the bypass actor would go unnoticed too.
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_ruleset.py"
_spec = importlib.util.spec_from_file_location("check_ruleset", _SCRIPT)
check_ruleset = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_ruleset)

_DEFINITION = (
    Path(__file__).resolve().parent.parent / ".github" / "rulesets" / "master.json"
)


@pytest.fixture(name="agreed")
def agreed_fixture() -> dict:
    """Return the committed definition, as the checker loads it."""
    return json.loads(_DEFINITION.read_text(encoding="utf-8"))


def _live(agreed: dict, **overrides) -> dict:
    """Build a live ruleset like GitHub's response, matching unless overridden."""
    live = json.loads(json.dumps(agreed))
    live.update({"id": 42, "source": "Danathar/sensi", "source_type": "Repository"})
    live.update(overrides)
    return live


# --------------------------------------------------------------------------
# The committed definition itself.
# --------------------------------------------------------------------------


def test_the_committed_definition_is_valid() -> None:
    """The file that ships is the one the checker would accept."""
    definition = check_ruleset.load_definition(_DEFINITION)

    assert definition["enforcement"] == "active"
    assert definition["bypass_actors"] == []


def test_the_definition_targets_the_default_branch_by_role_not_by_name() -> None:
    """`master` renamed would otherwise leave the rule protecting nothing."""
    definition = check_ruleset.load_definition(_DEFINITION)

    assert definition["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]


def test_the_definition_requires_only_checks_that_always_run() -> None:
    """A required check that does not report blocks every pull request forever.

    `publish badge and trend` runs only on a push to master, so requiring it
    would deadlock the merge queue rather than gate it.
    """
    required = check_ruleset._required_checks(
        check_ruleset.load_definition(_DEFINITION)
    )

    assert "publish badge and trend" not in required
    assert "pytest (Python 3.14)" in required
    assert "line coverage >= threshold" in required


def test_a_definition_that_ships_disabled_is_refused(
    tmp_path: Path, agreed: dict
) -> None:
    """It would read as protection in review and enforce nothing in fact."""
    agreed["enforcement"] = "disabled"
    path = tmp_path / "ruleset.json"
    path.write_text(json.dumps(agreed), encoding="utf-8")

    with pytest.raises(check_ruleset.DefinitionError, match="enforcement"):
        check_ruleset.load_definition(path)


def test_a_definition_missing_a_rule_is_refused(tmp_path: Path, agreed: dict) -> None:
    """A definition can be weakened in review as easily as the live ruleset."""
    agreed["rules"] = [r for r in agreed["rules"] if r["type"] != "non_fast_forward"]
    path = tmp_path / "ruleset.json"
    path.write_text(json.dumps(agreed), encoding="utf-8")

    with pytest.raises(check_ruleset.DefinitionError, match="non_fast_forward"):
        check_ruleset.load_definition(path)


def test_a_definition_with_no_required_checks_is_refused(
    tmp_path: Path, agreed: dict
) -> None:
    """Forcing a pull request that has nothing to pass is not a gate."""
    for rule in agreed["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"] = []
    path = tmp_path / "ruleset.json"
    path.write_text(json.dumps(agreed), encoding="utf-8")

    with pytest.raises(check_ruleset.DefinitionError, match="no checks"):
        check_ruleset.load_definition(path)


def test_unreadable_and_malformed_definitions_are_reported(tmp_path: Path) -> None:
    """Both are 'nothing was measured', not 'nothing is wrong'."""
    with pytest.raises(check_ruleset.DefinitionError, match="Unable to read"):
        check_ruleset.load_definition(tmp_path / "absent.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(check_ruleset.DefinitionError, match="not valid JSON"):
        check_ruleset.load_definition(broken)


# --------------------------------------------------------------------------
# Comparing against what GitHub is actually enforcing.
# --------------------------------------------------------------------------


def test_an_exact_match_reports_nothing(agreed: dict) -> None:
    """Agreement is silence."""
    assert check_ruleset.compare(agreed, _live(agreed)) == []


def test_extra_rules_are_not_drift(agreed: dict) -> None:
    """Somebody tightening the branch must not make this cry wolf.

    A checker that fails on tightening gets muted, and then it is not watching
    for the loosening either.
    """
    live = _live(agreed)
    live["rules"].append({"type": "required_linear_history"})

    assert check_ruleset.compare(agreed, live) == []


def test_a_bypass_actor_added_out_of_band_is_drift(agreed: dict) -> None:
    """The failure this exists for.

    Every rule still reads as enforced afterwards -- for everyone except the
    actor that was added. It shows up in no diff and no checkout.
    """
    live = _live(
        agreed,
        bypass_actors=[
            {"actor_id": 5, "actor_type": "Integration", "bypass_mode": "always"}
        ],
    )

    problems = check_ruleset.compare(agreed, live)

    assert len(problems) == 1
    assert "bypass_actors" in problems[0]


def test_enforcement_switched_off_is_drift(agreed: dict) -> None:
    """A ruleset present but inert is the subtlest way back to unprotected."""
    problems = check_ruleset.compare(agreed, _live(agreed, enforcement="disabled"))

    assert any("not stopping anything" in problem for problem in problems)


def test_a_removed_rule_is_drift(agreed: dict) -> None:
    """Deleting the rule is the blunt way."""
    live = _live(agreed)
    live["rules"] = [r for r in live["rules"] if r["type"] != "deletion"]

    assert any(
        "'deletion' is not applied" in p for p in check_ruleset.compare(agreed, live)
    )


def test_a_dropped_required_check_is_drift(agreed: dict) -> None:
    """Dropping one check is the quiet version of dropping the rule."""
    live = _live(agreed)
    for rule in live["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"] = [
                c
                for c in rule["parameters"]["required_status_checks"]
                if c["context"] != "ruff"
            ]

    problems = check_ruleset.compare(agreed, live)

    assert any("no longer required" in p and "ruff" in p for p in problems)


def test_a_relaxed_rule_parameter_is_drift(agreed: dict) -> None:
    """Loosening a parameter is loosening the rule."""
    live = _live(agreed)
    for rule in live["rules"]:
        if rule["type"] == "pull_request":
            rule["parameters"]["dismiss_stale_reviews_on_push"] = False

    problems = check_ruleset.compare(agreed, live)

    assert any("dismiss_stale_reviews_on_push" in p for p in problems)


def test_a_parameter_github_defaults_in_is_not_drift(agreed: dict) -> None:
    """GitHub fills in fields the definition does not set.

    Comparing the whole parameter block would report those forever, so only
    what was actually agreed is compared.
    """
    live = _live(agreed)
    for rule in live["rules"]:
        if rule["type"] == "pull_request":
            rule["parameters"]["automatic_copilot_code_review_enabled"] = False

    assert check_ruleset.compare(agreed, live) == []


def test_a_widened_ref_condition_is_drift(agreed: dict) -> None:
    """Pointing the rule at a branch nobody uses disarms it in place."""
    live = _live(agreed)
    live["conditions"]["ref_name"]["include"] = ["refs/heads/nothing"]

    assert any("ref_name.include" in p for p in check_ruleset.compare(agreed, live))


# --------------------------------------------------------------------------
# End to end.
# --------------------------------------------------------------------------


def test_offline_mode_needs_no_network(capsys: pytest.CaptureFixture[str]) -> None:
    """So it can run anywhere, including without a token."""
    with patch.object(
        check_ruleset, "fetch_live", side_effect=AssertionError("network used")
    ):
        exit_code = check_ruleset.main(["--offline"])

    assert exit_code == 0
    assert "Definition is valid" in capsys.readouterr().out


def test_a_repository_with_no_ruleset_says_how_to_apply_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Say how to apply it, rather than reporting success.

    This is the state the repository is in today: unprotected. Exit 1 keeps
    that from reading as a pass for a branch nothing guards.
    """
    with patch.object(check_ruleset, "fetch_live", return_value=None):
        exit_code = check_ruleset.main([])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "no ruleset named" in output
    assert "gh api --method POST" in output
    # The ordering dependency is the thing most likely to be skipped.
    assert "docs/branch-protection.md" in output


def test_a_matching_live_ruleset_exits_zero(
    agreed: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    """The good case, end to end."""
    with patch.object(check_ruleset, "fetch_live", return_value=_live(agreed)):
        exit_code = check_ruleset.main([])

    assert exit_code == 0
    assert "matches the committed definition" in capsys.readouterr().out


def test_drift_exits_one_and_names_each_difference(
    agreed: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-zero exit is what a scheduled run would act on."""
    live = _live(
        agreed,
        bypass_actors=[
            {"actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "always"}
        ],
    )
    with patch.object(check_ruleset, "fetch_live", return_value=live):
        exit_code = check_ruleset.main([])

    assert exit_code == 1
    assert "weaker than agreed" in capsys.readouterr().out


def test_an_unusable_definition_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Distinct from drift: nothing was measured, so nothing is known."""
    exit_code = check_ruleset.main(["--definition", str(tmp_path / "absent.json")])

    assert exit_code == 2
    assert "Unable to read" in capsys.readouterr().err


def test_a_github_failure_is_reported_not_treated_as_clean(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Silence on an API failure would read exactly like a protected branch."""
    with patch.object(
        check_ruleset,
        "fetch_live",
        side_effect=check_ruleset.DefinitionError("Unable to list rulesets"),
    ):
        exit_code = check_ruleset.main([])

    assert exit_code == 2
    assert "Unable to list rulesets" in capsys.readouterr().err
