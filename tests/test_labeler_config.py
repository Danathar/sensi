"""The `actions/labeler` configuration, `.github/labeler.yml`.

Not the workflow of almost the same name. `.github/workflows/labeler.yml` is
covered by `tests/test_labeler_workflow.py`; this is the config that workflow
passes to `actions/labeler@v5.0.0` as `configuration-path`, and until this
file existed nothing in the suite opened it.

The reason that matters is `sync-labels: true`. With it, the config is
subtractive as well as additive: a rule that stops matching does not fail the
run, it *removes* the label from the pull request. So both ways this file can
break are silent.

* **Shape.** v5 requires the `changed-files:` / `any-glob-to-any-file:`
  nesting. The v4 shape - a bare list of globs under the label - is what most
  examples still show, and a rule written that way matches nothing.
* **Dead globs.** Most of the globs name one committed file. A rename leaves
  the glob pointing at nothing and the label simply stops appearing.

Everything below is derived from the committed files rather than restated from
them: the rules are parsed out of `.github/labeler.yml`, the paths they are
replayed over come from `git ls-files`, and the risky paths in the cross-file
join come from `.github/policies/risk-tiers.yml`. A rule added to the config is
therefore covered by these tests the moment it is added.
"""

from pathlib import Path
import re
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / ".github" / "labeler.yml"
_WORKFLOW = _ROOT / ".github" / "workflows" / "labeler.yml"
_RISK_TIERS = _ROOT / ".github" / "policies" / "risk-tiers.yml"

# The labels the assertions below name directly. Any other label the config
# grows is still covered - every test that walks `_rules()` sees it - but
# these four carry a claim specific enough to state.
_INTEGRATION = "integration"
_TESTS = "tests"
_E2E = "e2e"
_CI = "ci"

# The catch-all tier in `.github/policies/risk-tiers.yml`. It matches every path by
# construction, so it says nothing about which paths are risky.
_CATCH_ALL = "**"

# The match keys actions/labeler v5 understands inside `changed-files:`. The
# set is closed on purpose: an unknown key is not an error at runtime, it is
# a rule that matches nothing, so `any-glob-to-any-fil` has to fail here.
_MATCH_KEYS = frozenset(
    {
        "any-glob-to-any-file",
        "any-glob-to-all-files",
        "all-globs-to-any-file",
        "all-globs-to-all-files",
    }
)

# The other matchers v5 accepts beside `changed-files:`. Neither carries a
# glob over the tree, so they are walked past rather than parsed.
_OTHER_MATCHERS = frozenset({"base-branch", "head-branch"})

# Tracked files that match no rule in the config. This is a decision, not an
# oversight to be papered over: each entry is here because a reviewer looking
# at a pull request that touches it gains nothing from a location label, or
# because adding it to a rule is a separate change nobody has made yet. The
# list is asserted exactly, so the seventeenth file fails this suite rather
# than joining it quietly.
#
# The five `.github/` entries and `.coveragerc` are CI configuration, which is
# what the `ci:` rule exists to mark; `custom_components/__init__.py` ships
# inside the package `integration:` describes. Those are the ones to revisit
# first if this list is ever worked through.
_UNLABELLED = {
    ".acmm.yml": "ACMM policy; read by the hive, not by this repository's CI",
    ".coveragerc": "coverage config - CI configuration the `ci:` rule misses",
    ".devcontainer/configuration.yaml": "devcontainer only; never runs in CI",
    ".devcontainer/devcontainer.json": "devcontainer only; never runs in CI",
    ".dockerignore": "build context for the devcontainer image",
    ".gitattributes": "git behaviour, not a build input",
    ".github/CODEOWNERS": "review routing - CI configuration the `ci:` rule misses",
    ".github/auto-qa-tuning.json": "QA tuning - CI configuration the `ci:` rule misses",
    ".github/dependabot.yml": "pin updates - CI configuration the `ci:` rule misses",
    ".github/rulesets/master.json": (
        "branch protection - CI configuration the `ci:` rule misses"
    ),
    ".gitignore": "git behaviour, not a build input",
    "Dockerfile": "devcontainer image; never runs in CI",
    "LICENSE": "licence text",
    "LICENSE.MIT": "licence text",
    "custom_components/__init__.py": (
        "namespace stub shipped in the package `integration:` describes"
    ),
    "hacs.json": "HACS metadata; `dependencies:` covers the component manifest only",
}


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _tracked() -> list[str]:
    """Every path git tracks, which is every path a pull request can change."""
    listed = subprocess.run(
        ["git", "ls-files"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(line for line in listed.stdout.splitlines() if line)


def _rules() -> dict[str, list[str]]:
    """Return `{label: [glob, ...]}` parsed out of the committed config.

    Raises rather than skipping if a rule is not in the v5 shape - that shape
    is the thing under test, and a rule quietly dropped from this mapping
    would take every assertion built on it out of the run too.
    """
    config = yaml.safe_load(_read(_CONFIG))
    rules: dict[str, list[str]] = {}
    for label, matchers in config.items():
        globs: list[str] = []
        assert isinstance(matchers, list), (
            f"{label}: rules are a list of matchers in actions/labeler v5; "
            f"this one is {type(matchers).__name__}"
        )
        for matcher in matchers:
            assert isinstance(matcher, dict), (
                f"{label}: v5 requires each matcher to be a mapping such as "
                f"`changed-files:`; got {matcher!r}. A bare list of globs is "
                "the v4 shape and matches nothing under v5"
            )
            unknown = sorted(set(matcher) - {"changed-files"} - _OTHER_MATCHERS)
            assert not unknown, (
                f"{label}: {unknown} are not actions/labeler matchers; a "
                "matcher it does not recognise applies the label to nothing"
            )
            for clause in matcher.get("changed-files", []):
                assert isinstance(clause, dict), (
                    f"{label}: each `changed-files` clause is a mapping such "
                    f"as `any-glob-to-any-file:`; got {clause!r}"
                )
                for key, patterns in clause.items():
                    assert key in _MATCH_KEYS, (
                        f"{label}: {key!r} is not one of {sorted(_MATCH_KEYS)}; "
                        "a typo here silently matches nothing"
                    )
                    globs.extend(patterns)
        rules[label] = globs
    return rules


def _to_regex(glob: str) -> re.Pattern[str]:
    """Compile one labeler glob the way minimatch reads it.

    `**` crosses directory separators, `*` and `?` do not. v5 dropped the
    `dot` option and always matches dotfiles, so no leading-dot special case
    is needed here - which is the only reason `**/*.md` reaches
    `.claude/memory/`.
    """
    out = ""
    index = 0
    while index < len(glob):
        if glob.startswith("**/", index):
            out += "(?:.*/)?"
            index += 3
        elif glob.startswith("/**", index) and index + 3 == len(glob):
            out += "(?:/.*)?"
            index += 3
        elif glob.startswith("**", index):
            out += ".*"
            index += 2
        elif glob[index] == "*":
            out += "[^/]*"
            index += 1
        elif glob[index] == "?":
            out += "[^/]"
            index += 1
        else:
            out += re.escape(glob[index])
            index += 1
    return re.compile(f"^{out}$")


def _matches(path: str, glob: str) -> bool:
    """Whether one tracked path is matched by one labeler glob."""
    return _to_regex(glob).match(path) is not None


def _labels_for(path: str, rules: dict[str, list[str]]) -> set[str]:
    """Every label the committed config would apply to `path`."""
    return {
        label
        for label, globs in rules.items()
        if any(_matches(path, glob) for glob in globs)
    }


def _is_literal(glob: str) -> bool:
    """Whether a glob names exactly one path rather than a set of them."""
    return not any(character in glob for character in "*?[")


# --------------------------------------------------------------------------
# Guard the machinery. A matcher that agreed with everything, or a file list
# that came back empty, would make every assertion below vacuous.
# --------------------------------------------------------------------------


def test_the_config_is_the_one_the_workflow_runs() -> None:
    """These tests are about a file only because a workflow points at it."""
    workflow = yaml.safe_load(_read(_WORKFLOW))
    configured = [
        step.get("with", {})
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/labeler@")
    ]

    assert configured, "no job in labeler.yml runs actions/labeler any more"
    for options in configured:
        assert options.get("configuration-path") == ".github/labeler.yml", (
            f"actions/labeler reads {options.get('configuration-path')!r}, so "
            "this file is testing a config nothing runs"
        )
        assert options.get("sync-labels") is True, (
            "sync-labels is no longer true; the assertions here are written "
            "for a config that removes labels as well as adding them"
        )


def test_the_tracked_file_scan_sees_the_repository() -> None:
    """An empty `git ls-files` would agree with every claim below."""
    tracked = _tracked()

    assert len(tracked) >= 100, (
        f"git ls-files returned {len(tracked)} paths, so the replay below is "
        "not looking at this repository"
    )
    for required in (".github/labeler.yml", "custom_components/sensi/client.py"):
        assert required in tracked, f"{required} is not tracked"


@pytest.mark.parametrize(
    ("path", "glob", "expected"),
    [
        # `**` crosses separators; `*` stops at one.
        ("custom_components/sensi/client.py", "custom_components/sensi/**", True),
        (
            "custom_components/sensi/translations/en.json",
            "custom_components/sensi/**",
            True,
        ),
        ("custom_components/other/client.py", "custom_components/sensi/**", False),
        ("README.md", "**/*.md", True),
        (".claude/memory/tooling-changes-are-not-feat.md", "**/*.md", True),
        ("docs/reflections/README.md", "**/*.md", True),
        ("docs/quality.txt", "**/*.md", False),
        # A literal glob is exactly one path, not a prefix of several.
        ("pytest.ini", "pytest.ini", True),
        ("tests/pytest.ini", "pytest.ini", False),
        ("ruff.toml", "ruff.toml", True),
        ("ruff.toml.bak", "ruff.toml", False),
        # `dir/**` does not reach a sibling that merely shares the prefix.
        ("tests/e2e/test_connect.py", "tests/e2e/**", True),
        ("tests/e2e_helpers.py", "tests/e2e/**", False),
    ],
)
def test_the_glob_matcher_reads_globs_the_way_minimatch_does(
    path: str, glob: str, expected: bool
) -> None:
    """Every claim in this file is only as good as this function."""
    assert _matches(path, glob) is expected


# --------------------------------------------------------------------------
# The config itself.
# --------------------------------------------------------------------------


def test_every_rule_is_in_the_v5_shape() -> None:
    """The v4 shape parses as YAML and matches nothing at runtime."""
    rules = _rules()

    assert len(rules) >= 5, (
        f"the config defines {len(rules)} labels; the parse above is not "
        "reading the committed file"
    )
    for label, globs in rules.items():
        assert globs, f"{label}: no globs, so the label can never be applied"


def test_the_committed_rules_all_use_the_semantics_replayed_here() -> None:
    """`_labels_for` models `any-glob-to-any-file` and only that.

    The other three keys are valid v5 and mean something different - one
    requires every glob to match, another requires every changed file to
    match. A rule switched to one of them would still be replayed as if any
    single match were enough, and every coverage claim below would be answering
    a question the config no longer asks.
    """
    config = yaml.safe_load(_read(_CONFIG))
    used = {
        key
        for matchers in config.values()
        for matcher in matchers
        for clause in matcher.get("changed-files", [])
        for key in clause
    }

    assert used == {"any-glob-to-any-file"}, (
        f"the config uses {sorted(used)}; the replay in this file models "
        "any-glob-to-any-file, so anything else needs _labels_for extended "
        "before it can be trusted here"
    )


def test_every_literal_glob_names_a_committed_file() -> None:
    """A rename leaves the glob inert, and sync-labels then strips the label."""
    tracked = set(_tracked())
    dead = sorted(
        f"{label}: {glob}"
        for label, globs in _rules().items()
        for glob in globs
        if _is_literal(glob) and glob not in tracked
    )

    assert not dead, (
        "these rules name a path that is not committed, so they match "
        f"nothing and the label they carry is never applied: {dead}"
    )


def test_every_wildcard_glob_matches_at_least_one_tracked_file() -> None:
    """The same failure, for the globs a literal check cannot see."""
    tracked = _tracked()
    inert = sorted(
        f"{label}: {glob}"
        for label, globs in _rules().items()
        for glob in globs
        if not _is_literal(glob) and not any(_matches(path, glob) for path in tracked)
    )

    assert not inert, f"these globs match no tracked file: {inert}"


@pytest.mark.parametrize(
    ("label", "directory"),
    [
        (_INTEGRATION, "custom_components/sensi/"),
        (_TESTS, "tests/"),
        (_E2E, "tests/e2e/"),
        (_CI, ".github/workflows/"),
    ],
)
def test_the_label_for_a_directory_covers_all_of_it(label: str, directory: str) -> None:
    """A rule that covers most of a directory is the shape that misleads.

    `integration` missing one module reads, to a reviewer scanning labels, as
    a change that did not touch the integration.
    """
    rules = _rules()
    assert label in rules, f"the config no longer defines a {label!r} label"

    contained = [path for path in _tracked() if path.startswith(directory)]
    assert contained, f"nothing is tracked under {directory}"

    missing = sorted(
        path for path in contained if label not in _labels_for(path, rules)
    )
    assert not missing, (
        f"{label!r} does not cover every file under {directory}: {missing}"
    )


@pytest.mark.parametrize(("narrow", "wide"), [(_E2E, _TESTS)])
def test_a_narrow_label_never_appears_without_its_wider_one(
    narrow: str, wide: str
) -> None:
    """`e2e` is a subset of `tests`; a file carrying one and not the other is a hole."""
    rules = _rules()
    orphans = sorted(
        path
        for path in _tracked()
        if narrow in _labels_for(path, rules) and wide not in _labels_for(path, rules)
    )

    assert not orphans, f"labelled {narrow!r} but not {wide!r}: {orphans}"


def test_every_risky_path_also_gets_a_location_label() -> None:
    """The cross-file join to `.github/policies/risk-tiers.yml`.

    A path listed in `tier/breaking` or `tier/runtime` is, by that file's own
    description, one that can break an existing install or drive live state.
    Such a path arriving with a risk tier and no location label tells a
    reviewer the blast radius twice and where it landed not at all.
    """
    tiers = yaml.safe_load(_read(_RISK_TIERS))["tiers"]
    risky = sorted(
        {glob for tier in tiers for glob in tier["paths"] if glob != _CATCH_ALL}
    )
    assert len(risky) >= 10, (
        f"only {len(risky)} non-catch-all risk paths were parsed out of "
        "risk-tiers.yml; this join is not reading that file"
    )

    rules = _rules()
    tracked = _tracked()
    unlocated = []
    for glob in risky:
        covered = [path for path in tracked if _matches(path, glob)]
        for path in covered:
            if not _labels_for(path, rules):
                unlocated.append(f"{glob} -> {path}")

    assert not unlocated, (
        "these paths carry a risk tier but no labeler rule, so the pull "
        f"request says how risky it is and not where it landed: {sorted(unlocated)}"
    )


def test_the_files_no_rule_matches_are_exactly_the_reviewed_list() -> None:
    """Turn the gap into an allowlist, so the next one fails rather than joins.

    `_UNLABELLED` is asserted in both directions on purpose. A new file that
    no rule matches is the failure this exists to catch; a file that has since
    been brought under a rule has to leave the list, or the annotation next to
    it becomes a stale reason for an exemption nobody needs.
    """
    rules = _rules()
    observed = {path for path in _tracked() if not _labels_for(path, rules)}
    expected = set(_UNLABELLED)

    assert observed == expected, (
        "files matching no labeler rule have changed.\n"
        f"  newly unmatched: {sorted(observed - expected)}\n"
        f"  now matched, remove from _UNLABELLED: {sorted(expected - observed)}"
    )
