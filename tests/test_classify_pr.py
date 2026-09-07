"""Tests for scripts/classify_pr.py.

Not part of the component, so not measured by the coverage gate - but this is
the script `.github/workflows/labeler.yml` runs on every pull request, and its
output decides which `tier/*` and `size/*` labels get applied. A wrong answer
here is not a red build: it is a quietly mislabelled pull request, so a change
to `config_flow.py` can arrive carrying `tier/support` ("cannot reach a user's
installation") and be reviewed as though it were a docs edit.

Three things the script gets to decide, and each is asserted here against the
mutant that would break it:

* which tier wins when a change touches more than one of them - the first
  matching tier in `.github/risk-tiers.yml`, not the last and not a score;
* what `**` means in a rule pattern - a prefix match on the directory, which
  `fnmatch` alone does not do;
* whether a `label_description` is short enough for GitHub, which is checked
  here so it fails locally instead of as an HTTP 422 mid-workflow.

The script resolves the rules path at import time, so the tests that need
their own rules point the module-level RULES at a temporary file.
"""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "classify_pr.py"
_spec = importlib.util.spec_from_file_location("classify_pr", _SCRIPT)
classify_pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(classify_pr)

# A miniature rules file with the same shape as the real one: tiers ordered
# most severe first, sizes ordered smallest first with an uncapped last entry.
SAMPLE_RULES = {
    "tiers": [
        {
            "name": "tier/breaking",
            "label_colour": "b60205",
            "label_description": "Can break an install",
            "paths": ["custom_components/sensi/config_flow.py"],
        },
        {
            "name": "tier/runtime",
            "label_colour": "d93f0b",
            "label_description": "Runs against the live service",
            "paths": ["custom_components/sensi/client.py"],
        },
        {
            "name": "tier/behaviour",
            "label_colour": "fbca04",
            "label_description": "User-visible",
            "paths": [
                "custom_components/sensi/climate.py",
                "custom_components/sensi/translations/**",
            ],
        },
    ],
    "sizes": [
        {"name": "size/XS", "max_lines": 20},
        {"name": "size/S", "max_lines": 100},
        {"name": "size/XL", "max_lines": None},
    ],
}


@pytest.fixture
def rules_file(monkeypatch, tmp_path):
    """Point the module's RULES at a temporary rules file."""

    def _write(rules):
        path = tmp_path / "risk-tiers.yml"
        path.write_text(yaml.safe_dump(rules), encoding="utf-8")
        monkeypatch.setattr(classify_pr, "RULES", path)
        return path

    return _write


def test_die_prints_to_stderr_and_exits_one(capsys):
    """Every failure path in the script goes through this."""
    with pytest.raises(SystemExit) as excinfo:
        classify_pr.die("no changed paths")

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "error: no changed paths"
    assert captured.out == ""


# --- matches() -------------------------------------------------------------


def test_the_catch_all_pattern_matches_anything():
    """`**` on its own is how tier/support catches every remaining path."""
    assert classify_pr.matches("README.md", "**")
    assert classify_pr.matches("custom_components/sensi/client.py", "**")


def test_a_directory_pattern_is_a_prefix_match_not_an_fnmatch():
    """`dir/**` must reach nested paths; plain fnmatch stops at one segment."""
    pattern = "custom_components/sensi/translations/**"

    assert classify_pr.matches("custom_components/sensi/translations/en.json", pattern)
    assert classify_pr.matches(
        "custom_components/sensi/translations/nested/deeper/fr.json", pattern
    )


def test_a_directory_pattern_does_not_match_a_sibling_with_the_same_prefix():
    """The trailing slash is load-bearing: `translations.md` is not in it."""
    pattern = "custom_components/sensi/translations/**"

    assert not classify_pr.matches("custom_components/sensi/translations.md", pattern)
    assert not classify_pr.matches("custom_components/sensi/client.py", pattern)


def test_a_plain_pattern_is_an_exact_fnmatch():
    """An exact path in the rules matches that path and nothing near it."""
    assert classify_pr.matches(
        "custom_components/sensi/client.py", "custom_components/sensi/client.py"
    )
    assert not classify_pr.matches(
        "custom_components/sensi/client_test.py", "custom_components/sensi/client.py"
    )


def test_a_wildcard_pattern_still_goes_through_fnmatch():
    """Globs other than `**` keep their fnmatch meaning.

    Which is `fnmatch`'s, not a shell's: its `*` crosses a `/`, so `docs/*.md`
    reaches a nested file too. Asserted rather than assumed, because a rule
    author reading `docs/*.md` would expect the shell's answer.
    """
    assert classify_pr.matches("docs/risk-tiers.md", "docs/*.md")
    assert classify_pr.matches("docs/nested/risk-tiers.md", "docs/*.md")
    assert not classify_pr.matches("README.md", "docs/*.md")
    assert not classify_pr.matches("docs/risk-tiers.txt", "docs/*.md")


# --- load_rules() ----------------------------------------------------------


def test_load_rules_reads_the_file(rules_file):
    """The happy path: valid YAML in, parsed rules out."""
    rules_file(SAMPLE_RULES)

    assert classify_pr.load_rules() == SAMPLE_RULES


def test_a_missing_rules_file_is_reported_by_path(monkeypatch, tmp_path, capsys):
    """Named explicitly, because the next symptom would be a KeyError."""
    missing = tmp_path / "absent.yml"
    monkeypatch.setattr(classify_pr, "RULES", missing)

    with pytest.raises(SystemExit) as excinfo:
        classify_pr.load_rules()

    assert excinfo.value.code == 1
    assert f"{missing} not found" in capsys.readouterr().err


def test_an_over_long_label_description_is_rejected_with_both_lengths(
    rules_file, capsys
):
    """GitHub answers 422; this is the check that fires before we get there."""
    rules = json.loads(json.dumps(SAMPLE_RULES))
    rules["tiers"][1]["label_description"] = "x" * (
        classify_pr.MAX_LABEL_DESCRIPTION + 1
    )
    rules_file(rules)

    with pytest.raises(SystemExit) as excinfo:
        classify_pr.load_rules()

    assert excinfo.value.code == 1
    assert (
        f"tier/runtime: label_description is "
        f"{classify_pr.MAX_LABEL_DESCRIPTION + 1} characters; GitHub allows "
        f"{classify_pr.MAX_LABEL_DESCRIPTION}" in capsys.readouterr().err
    )


def test_a_description_of_exactly_the_limit_is_accepted(rules_file):
    """The boundary is inclusive - 100 characters is what GitHub allows."""
    rules = json.loads(json.dumps(SAMPLE_RULES))
    rules["tiers"][0]["label_description"] = "x" * classify_pr.MAX_LABEL_DESCRIPTION
    rules_file(rules)

    assert classify_pr.load_rules()["tiers"][0]["label_description"] == (
        "x" * classify_pr.MAX_LABEL_DESCRIPTION
    )


def test_a_tier_with_no_label_description_is_not_rejected(rules_file):
    """It is optional; only an over-long one is a problem."""
    rules = json.loads(json.dumps(SAMPLE_RULES))
    del rules["tiers"][0]["label_description"]
    rules_file(rules)

    assert "label_description" not in classify_pr.load_rules()["tiers"][0]


# --- classify() ------------------------------------------------------------


def test_the_highest_tier_wins_regardless_of_the_order_of_the_paths():
    """The tier is not additive and not last-write-wins; it is the first match."""
    both = [
        "custom_components/sensi/climate.py",
        "custom_components/sensi/config_flow.py",
    ]

    for paths in (both, list(reversed(both))):
        result = classify_pr.classify(paths, 10, SAMPLE_RULES)
        assert result["tier"]["name"] == "tier/breaking"


def test_a_single_file_gets_its_own_tier():
    """The lower tiers are reachable when nothing more severe is touched."""
    result = classify_pr.classify(
        ["custom_components/sensi/client.py"], 5, SAMPLE_RULES
    )

    assert result["tier"]["name"] == "tier/runtime"
    assert result["tier"]["colour"] == "d93f0b"
    assert result["tier"]["description"] == "Runs against the live service"


def test_a_tier_matched_through_a_directory_pattern():
    """`translations/**` is the only glob in the real rules file."""
    result = classify_pr.classify(
        ["custom_components/sensi/translations/en.json"], 5, SAMPLE_RULES
    )

    assert result["tier"]["name"] == "tier/behaviour"


def test_no_matching_tier_leaves_the_tier_null():
    """Without a `**` catch-all a path can match nothing; that is not a crash."""
    result = classify_pr.classify(["README.md"], 5, SAMPLE_RULES)

    assert result["tier"] is None
    assert result["size"]["name"] == "size/XS"


def test_a_tier_without_a_colour_or_description_gets_the_defaults():
    """Both keys are optional in the rules file."""
    rules = json.loads(json.dumps(SAMPLE_RULES))
    del rules["tiers"][0]["label_colour"]
    del rules["tiers"][0]["label_description"]

    result = classify_pr.classify(["custom_components/sensi/config_flow.py"], 5, rules)

    assert result["tier"] == {
        "name": "tier/breaking",
        "colour": "ededed",
        "description": "",
    }


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (0, "size/XS"),
        (20, "size/XS"),
        (21, "size/S"),
        (100, "size/S"),
        (101, "size/XL"),
        (100_000, "size/XL"),
    ],
)
def test_the_size_bucket_boundaries_are_inclusive(lines, expected):
    """`max_lines: 20` means 20 is XS and 21 is not."""
    result = classify_pr.classify(["README.md"], lines, SAMPLE_RULES)

    assert result["size"]["name"] == expected


def test_the_uncapped_size_is_what_catches_a_large_change():
    """`max_lines: null` is the open-ended bucket, not a bucket of zero."""
    rules = json.loads(json.dumps(SAMPLE_RULES))
    del rules["sizes"][2]["max_lines"]

    assert (
        classify_pr.classify(["README.md"], 5_000, rules)["size"]["name"] == "size/XL"
    )


def test_no_matching_size_leaves_the_size_null():
    """Every bucket capped and the change bigger than all of them."""
    rules = json.loads(json.dumps(SAMPLE_RULES))
    rules["sizes"] = [{"name": "size/XS", "max_lines": 20}]

    result = classify_pr.classify(["README.md"], 21, rules)

    assert result["size"] is None
    assert result["tier"] is None


def test_the_size_label_description_carries_the_line_count():
    """The workflow writes this straight into the label's description."""
    result = classify_pr.classify(
        ["custom_components/sensi/client.py", "README.md"], 42, SAMPLE_RULES
    )

    assert result["size"] == {
        "name": "size/S",
        "colour": "c5def5",
        "description": "42 lines changed",
    }
    assert result["changed_files"] == 2
    assert result["changed_lines"] == 42


# --- fetch_pr() ------------------------------------------------------------


@pytest.fixture
def gh(monkeypatch):
    """Record the gh command and answer it with a scripted result."""
    calls = []

    def _install(returncode=0, stdout="", stderr="", present=True):
        monkeypatch.setattr(
            classify_pr.shutil, "which", lambda name: "/usr/bin/gh" if present else None
        )

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        monkeypatch.setattr(classify_pr.subprocess, "run", fake_run)
        return calls

    return _install


def test_fetch_pr_sums_additions_and_deletions(gh):
    """A pull request's size is both sides of the diff, not just additions."""
    calls = gh(
        stdout=json.dumps(
            {
                "files": [
                    {
                        "path": "custom_components/sensi/client.py",
                        "additions": 30,
                        "deletions": 4,
                    },
                    {"path": "README.md", "additions": 1, "deletions": 2},
                ]
            }
        )
    )

    paths, lines = classify_pr.fetch_pr(44, None)

    assert paths == ["custom_components/sensi/client.py", "README.md"]
    assert lines == 37
    assert calls[0][0] == ["gh", "pr", "view", "44", "--json", "files"]


def test_fetch_pr_treats_a_file_with_no_counts_as_zero(gh):
    """`gh` omits the counts for a rename; that is 0 lines, not a KeyError."""
    gh(stdout=json.dumps({"files": [{"path": "docs/metrics.md"}]}))

    assert classify_pr.fetch_pr(44, None) == (["docs/metrics.md"], 0)


def test_fetch_pr_passes_the_repo_through(gh):
    """`--repo` is how the script is run against a repository it is not in."""
    calls = gh(stdout=json.dumps({"files": []}))

    classify_pr.fetch_pr(7, "Danathar/sensi")

    assert calls[0][0] == [
        "gh",
        "pr",
        "view",
        "7",
        "--json",
        "files",
        "--repo",
        "Danathar/sensi",
    ]


def test_fetch_pr_without_the_gh_cli_says_so(gh, capsys):
    """The one dependency the workflow does not install itself."""
    gh(present=False)

    with pytest.raises(SystemExit) as excinfo:
        classify_pr.fetch_pr(44, None)

    assert excinfo.value.code == 1
    assert "the GitHub CLI (gh) is required" in capsys.readouterr().err


def test_fetch_pr_reports_the_gh_error(gh, capsys):
    """The gh error itself is more useful than anything this script could add."""
    gh(returncode=1, stderr="  GraphQL: Could not resolve to a PullRequest.  \n")

    with pytest.raises(SystemExit) as excinfo:
        classify_pr.fetch_pr(999, None)

    assert excinfo.value.code == 1
    assert (
        "error: GraphQL: Could not resolve to a PullRequest." in capsys.readouterr().err
    )


def test_fetch_pr_falls_back_when_gh_fails_silently(gh, capsys):
    """A non-zero exit with nothing on stderr still has to say something."""
    gh(returncode=2, stderr="   ")

    with pytest.raises(SystemExit) as excinfo:
        classify_pr.fetch_pr(44, None)

    assert excinfo.value.code == 1
    assert "error: gh pr view failed" in capsys.readouterr().err


# --- main() ----------------------------------------------------------------


def _run_main(monkeypatch, argv, stdin=None):
    """Invoke main() with a scripted argv and optional stdin."""
    monkeypatch.setattr(classify_pr.sys, "argv", ["classify_pr.py", *argv])
    if stdin is not None:
        monkeypatch.setattr(classify_pr.sys, "stdin", stdin.splitlines(keepends=True))
    return classify_pr.main()


def test_main_with_files_prints_the_classification_as_json(
    monkeypatch, rules_file, capsys
):
    """What the workflow redirects into classification.json."""
    rules_file(SAMPLE_RULES)

    assert (
        _run_main(
            monkeypatch,
            ["--files", "custom_components/sensi/client.py", "--lines", "30"],
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "changed_files": 1,
        "changed_lines": 30,
        "tier": {
            "name": "tier/runtime",
            "colour": "d93f0b",
            "description": "Runs against the live service",
        },
        "size": {
            "name": "size/S",
            "colour": "c5def5",
            "description": "30 lines changed",
        },
    }


def test_main_reads_paths_from_stdin(monkeypatch, rules_file, capsys):
    """`git diff --name-only master... | classify_pr.py --stdin --lines 0`."""
    rules_file(SAMPLE_RULES)

    assert (
        _run_main(
            monkeypatch,
            ["--stdin", "--lines", "0"],
            stdin="custom_components/sensi/climate.py\n\n   \nREADME.md\n",
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["changed_files"] == 2
    assert result["changed_lines"] == 0
    assert result["tier"]["name"] == "tier/behaviour"
    assert result["size"]["name"] == "size/XS"


def test_main_with_pr_takes_the_line_count_from_gh(monkeypatch, rules_file, gh, capsys):
    """This is the workflow's own invocation: `--pr` and no `--lines`."""
    rules_file(SAMPLE_RULES)
    gh(
        stdout=json.dumps(
            {
                "files": [
                    {
                        "path": "custom_components/sensi/config_flow.py",
                        "additions": 200,
                        "deletions": 1,
                    }
                ]
            }
        )
    )

    assert _run_main(monkeypatch, ["--pr", "44"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["changed_lines"] == 201
    assert result["tier"]["name"] == "tier/breaking"
    assert result["size"]["name"] == "size/XL"


def test_an_explicit_lines_overrides_what_gh_reported(
    monkeypatch, rules_file, gh, capsys
):
    """`--lines` alongside `--pr` wins; otherwise it would be silently ignored."""
    rules_file(SAMPLE_RULES)
    gh(
        stdout=json.dumps(
            {"files": [{"path": "README.md", "additions": 900, "deletions": 0}]}
        )
    )

    assert _run_main(monkeypatch, ["--pr", "44", "--lines", "0"]) == 0

    assert json.loads(capsys.readouterr().out)["changed_lines"] == 0


def test_lines_is_required_without_pr(monkeypatch, capsys):
    """Nothing else can supply the count, so guessing would mislabel the size."""
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, ["--files", "README.md"])

    assert excinfo.value.code == 1
    assert "--lines is required unless --pr is used" in capsys.readouterr().err


def test_an_empty_change_is_refused(monkeypatch, capsys):
    """An empty stdin would otherwise classify as tier-less size/XS."""
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, ["--stdin", "--lines", "0"], stdin="\n   \n")

    assert excinfo.value.code == 1
    assert "no changed paths" in capsys.readouterr().err


def test_a_source_argument_is_required(monkeypatch, capsys):
    """--pr, --files and --stdin are mutually exclusive and one is required."""
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, ["--lines", "5"])

    assert excinfo.value.code == 2
    assert "one of the arguments" in capsys.readouterr().err


def test_pr_and_files_cannot_be_combined(monkeypatch, capsys):
    """Two sources of truth for the changed paths is a usage error."""
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, ["--pr", "44", "--files", "README.md"])

    assert excinfo.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


# --- the real .github/risk-tiers.yml ---------------------------------------


def test_the_committed_rules_file_loads():
    """The check in load_rules() is only useful if it runs on the real file."""
    rules = classify_pr.load_rules()

    assert [tier["name"] for tier in rules["tiers"]] == [
        "tier/breaking",
        "tier/runtime",
        "tier/behaviour",
        "tier/support",
    ]
    assert rules["sizes"][-1]["max_lines"] is None


def test_every_committed_label_description_fits_in_a_github_label():
    """The 422 this exists to prevent."""
    for tier in classify_pr.load_rules()["tiers"]:
        assert (
            len(tier.get("label_description", "")) <= classify_pr.MAX_LABEL_DESCRIPTION
        ), tier["name"]


@pytest.mark.parametrize(
    ("path", "tier"),
    [
        ("custom_components/sensi/config_flow.py", "tier/breaking"),
        ("custom_components/sensi/auth.py", "tier/breaking"),
        ("custom_components/sensi/manifest.json", "tier/breaking"),
        ("custom_components/sensi/entity.py", "tier/breaking"),
        ("custom_components/sensi/client.py", "tier/runtime"),
        ("custom_components/sensi/coordinator.py", "tier/runtime"),
        ("custom_components/sensi/__init__.py", "tier/runtime"),
        ("custom_components/sensi/data.py", "tier/runtime"),
        ("custom_components/sensi/capabilities.py", "tier/runtime"),
        ("custom_components/sensi/event.py", "tier/runtime"),
        ("custom_components/sensi/climate.py", "tier/behaviour"),
        ("custom_components/sensi/binary_sensor.py", "tier/behaviour"),
        ("custom_components/sensi/number.py", "tier/behaviour"),
        ("custom_components/sensi/sensor.py", "tier/behaviour"),
        ("custom_components/sensi/switch.py", "tier/behaviour"),
        ("custom_components/sensi/strings.json", "tier/behaviour"),
        ("custom_components/sensi/translations/en.json", "tier/behaviour"),
        ("tests/test_client.py", "tier/support"),
        ("scripts/classify_pr.py", "tier/support"),
        ("docs/risk-tiers.md", "tier/support"),
        (".github/workflows/labeler.yml", "tier/support"),
        ("README.md", "tier/support"),
    ],
)
def test_each_committed_rule_path_lands_in_its_own_tier(path, tier):
    """Every path listed in the rules file, classified one at a time.

    A path that quietly stops matching its rule falls through to
    `tier/support` - "cannot reach a user's installation" - which is the one
    mislabelling that makes a risky change look safe.
    """
    result = classify_pr.classify([path], 1, classify_pr.load_rules())

    assert result["tier"]["name"] == tier


def test_a_component_file_in_no_rule_still_lands_in_support():
    """The `**` catch-all means the real rules always produce a tier."""
    result = classify_pr.classify(
        ["custom_components/sensi/utils.py"], 1, classify_pr.load_rules()
    )

    assert result["tier"]["name"] == "tier/support"
