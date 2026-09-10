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
import os
from pathlib import Path
import sys
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


# --------------------------------------------------------------------------
# Talking to GitHub.
#
# Every test above patches `fetch_live` out, so the function that actually
# produces the answer -- two `gh api` calls, a name match between them, and
# two failure paths -- runs in none of them. It is the only part of the script
# that can report a protected branch when GitHub says otherwise, so it is
# exercised here for real: a recording `gh` on PATH, and assertions on the
# argv it was handed rather than on what got printed.
# --------------------------------------------------------------------------


_GH_STUB = """#!{python}
import json, os, sys

with open(os.environ["GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")

replies = json.loads(os.environ["GH_REPLIES"])
reply = replies.get(sys.argv[-1])
if reply is None:
    sys.stderr.write("unstubbed call: " + " ".join(sys.argv[1:]) + "\\n")
    sys.exit(3)
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("code", 0))
"""


@pytest.fixture(name="gh")
def gh_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Put a recording `gh` on PATH and return (set_replies, recorded_argv)."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text(_GH_STUB.format(python=sys.executable), encoding="utf-8")
    stub.chmod(0o755)

    log = tmp_path / "gh.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("GH_LOG", str(log))
    monkeypatch.setenv("GH_REPLIES", "{}")
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ['PATH']}")

    def set_replies(replies: dict) -> None:
        monkeypatch.setenv("GH_REPLIES", json.dumps(replies))

    def calls() -> list[list[str]]:
        return [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line
        ]

    return set_replies, calls


def test_fetch_live_asks_for_the_named_ruleset_by_its_id(gh) -> None:
    """The listing gives ids, not definitions; the detail call is the payload.

    Both paths are asserted because a wrong one is not an error -- `gh api`
    would fail, and the failure reads as "cannot reach GitHub" rather than
    "this script asks for the wrong thing".
    """
    set_replies, calls = gh
    set_replies(
        {
            "repos/Danathar/sensi/rulesets": {
                "stdout": json.dumps(
                    [{"id": 7, "name": "protect master"}, {"id": 9, "name": "tags"}]
                )
            },
            "repos/Danathar/sensi/rulesets/7": {
                "stdout": json.dumps({"id": 7, "name": "protect master"})
            },
        }
    )

    live = check_ruleset.fetch_live("Danathar/sensi", "protect master")

    assert live == {"id": 7, "name": "protect master"}
    assert calls() == [
        ["api", "repos/Danathar/sensi/rulesets"],
        ["api", "repos/Danathar/sensi/rulesets/7"],
    ]


def test_fetch_live_matches_on_name_not_on_being_first(gh) -> None:
    """A repository can hold several rulesets, and only one is the agreement.

    Returning whichever came back first would compare `master`'s protection
    against a rule for tags and report drift, or worse, agreement.
    """
    set_replies, calls = gh
    set_replies(
        {
            "repos/Danathar/sensi/rulesets": {
                "stdout": json.dumps([{"id": 9, "name": "tags"}])
            }
        }
    )

    assert check_ruleset.fetch_live("Danathar/sensi", "protect master") is None
    assert calls() == [["api", "repos/Danathar/sensi/rulesets"]]


def test_fetch_live_takes_the_repository_it_is_given(gh) -> None:
    """`--repo` has to reach the API call, or it checks the wrong repository."""
    set_replies, calls = gh
    set_replies({"repos/other/fork/rulesets": {"stdout": "[]"}})

    assert check_ruleset.fetch_live("other/fork", "protect master") is None
    assert calls() == [["api", "repos/other/fork/rulesets"]]


def test_an_empty_listing_is_no_ruleset_rather_than_a_crash(gh) -> None:
    """`gh api` can exit 0 having printed nothing.

    A JSONDecodeError here would surface as an unhandled traceback, which no
    caller distinguishes from the script not having run at all.
    """
    set_replies, _ = gh
    set_replies({"repos/Danathar/sensi/rulesets": {"stdout": ""}})

    assert check_ruleset.fetch_live("Danathar/sensi", "protect master") is None


def test_a_failed_listing_is_raised_not_read_as_no_ruleset(gh) -> None:
    """The difference between "unprotected" and "unknown".

    Both would exit non-zero, but only one of them says the branch is open.
    """
    set_replies, _ = gh
    set_replies(
        {
            "repos/Danathar/sensi/rulesets": {
                "code": 1,
                "stderr": "gh: HTTP 404\ngh: Not Found (repos/Danathar/sensi/rulesets)\n",
            }
        }
    )

    with pytest.raises(check_ruleset.DefinitionError) as raised:
        check_ruleset.fetch_live("Danathar/sensi", "protect master")

    # The last line, because gh puts the useful part after the status line.
    assert "gh: Not Found (repos/Danathar/sensi/rulesets)" in str(raised.value)
    assert "Danathar/sensi" in str(raised.value)


def test_a_failed_listing_with_no_stderr_still_says_something(gh) -> None:
    """`gh` exits non-zero silently when it cannot find a token."""
    set_replies, _ = gh
    set_replies({"repos/Danathar/sensi/rulesets": {"code": 1}})

    with pytest.raises(check_ruleset.DefinitionError, match="gh failed"):
        check_ruleset.fetch_live("Danathar/sensi", "protect master")


def test_a_failed_detail_read_is_raised_not_silently_skipped(gh) -> None:
    """The ruleset exists and could not be read; that is not "no ruleset"."""
    set_replies, _ = gh
    set_replies(
        {
            "repos/Danathar/sensi/rulesets": {
                "stdout": json.dumps([{"id": 7, "name": "protect master"}])
            },
            "repos/Danathar/sensi/rulesets/7": {"code": 1, "stderr": "gh: HTTP 403\n"},
        }
    )

    with pytest.raises(check_ruleset.DefinitionError, match="Unable to read ruleset 7"):
        check_ruleset.fetch_live("Danathar/sensi", "protect master")


def test_online_mode_runs_end_to_end_against_a_live_ruleset(
    agreed: dict, gh, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` -> real `fetch_live` -> `compare`, with nothing patched out.

    The name searched for is the definition's own `name`, so renaming it in
    the file makes the script stop finding the ruleset it is enforcing.
    """
    set_replies, calls = gh
    live = _live(agreed)
    set_replies(
        {
            "repos/Danathar/sensi/rulesets": {
                "stdout": json.dumps([{"id": 42, "name": agreed["name"]}])
            },
            "repos/Danathar/sensi/rulesets/42": {"stdout": json.dumps(live)},
        }
    )

    exit_code = check_ruleset.main([])

    assert exit_code == 0
    assert "matches the committed definition" in capsys.readouterr().out
    assert calls() == [
        ["api", "repos/Danathar/sensi/rulesets"],
        ["api", "repos/Danathar/sensi/rulesets/42"],
    ]


def test_online_mode_reports_drift_read_from_github(
    agreed: dict, gh, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of the online mode, with the API call left in."""
    set_replies, _ = gh
    live = _live(agreed, bypass_actors=[{"actor_id": 1, "actor_type": "Integration"}])
    set_replies(
        {
            "repos/Danathar/sensi/rulesets": {
                "stdout": json.dumps([{"id": 42, "name": agreed["name"]}])
            },
            "repos/Danathar/sensi/rulesets/42": {"stdout": json.dumps(live)},
        }
    )

    exit_code = check_ruleset.main([])

    assert exit_code == 1
    assert "bypass_actors" in capsys.readouterr().out


def test_repo_reaches_the_api_call(agreed: dict, gh) -> None:
    """`--repo` is how this is pointed at a fork; it must not be ignored."""
    set_replies, calls = gh
    set_replies({"repos/someone/sensi/rulesets": {"stdout": "[]"}})

    assert check_ruleset.main(["--repo", "someone/sensi"]) == 1
    assert calls() == [["api", "repos/someone/sensi/rulesets"]]


# --------------------------------------------------------------------------
# Definition validation that nothing above reaches.
# --------------------------------------------------------------------------


def test_a_definition_that_targets_tags_is_refused(
    tmp_path: Path, agreed: dict
) -> None:
    """A branch rule with `target: tag` applies to no branch at all."""
    agreed["target"] = "tag"
    path = tmp_path / "ruleset.json"
    path.write_text(json.dumps(agreed), encoding="utf-8")

    with pytest.raises(check_ruleset.DefinitionError, match="target must be 'branch'"):
        check_ruleset.load_definition(path)


def test_a_definition_pinned_to_a_branch_name_is_refused(
    tmp_path: Path, agreed: dict
) -> None:
    """`master` renamed leaves a rule that reads as protection and guards nothing.

    `~DEFAULT_BRANCH` follows the role. A literal ref does not, and the diff
    that renames the branch never touches this file.
    """
    agreed["conditions"]["ref_name"]["include"] = ["refs/heads/master"]
    path = tmp_path / "ruleset.json"
    path.write_text(json.dumps(agreed), encoding="utf-8")

    with pytest.raises(check_ruleset.DefinitionError, match="~DEFAULT_BRANCH"):
        check_ruleset.load_definition(path)


def test_required_checks_of_a_ruleset_without_the_rule_is_empty(agreed: dict) -> None:
    """A live ruleset can lack the rule entirely.

    `compare` subtracts one set from the other, so returning None or raising
    here would turn "every required check was dropped" into a crash or a pass.
    """
    live = _live(agreed)
    live["rules"] = [r for r in live["rules"] if r["type"] != "required_status_checks"]

    assert check_ruleset._required_checks(live) == set()
    assert any("no longer required" in p for p in check_ruleset.compare(agreed, live))
