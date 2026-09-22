"""The values in `.github/dependabot.yml`, not just its existence.

`tests/test_workflow_action_pins.py` already asserts the two things that make
the file present at all: that it watches `github-actions`, and that it watches
the repository root. Everything else in it - the schema version, the weekly
cadence, the group that collapses a week of bumps into one pull request, the
`ci` commit prefix, the `dependencies` label - was committed once and opened by
nothing since.

That is the expensive half to get wrong, because every failure here is silent.
Dependabot does not report a configuration it disagrees with on a pull request
or in a check run; it writes to a repository-settings page nobody opens on a
normal day. A `version:` it does not recognise, an `open-pull-requests-limit`
of zero, or an ignore rule that swallows everything all end the same way: no
pull requests, no error, and the pins the file exists to move quietly stop
moving. "Pinned" becomes "stuck on whatever was current the day it was pinned"
- which is the sentence the file's own header opens with.

The file also carries three prose claims about the repository around those
values, and prose is not checked by a schema:

* that four first-party actions is what a grouped update is saving the
  reviewer from - a count of what `.github/workflows/` actually uses;
* that `github-actions` is the *only* ecosystem configured, deliberately;
* that the Python side is pinned in two places Dependabot cannot see, which is
  the argument for leaving it out.

Each is re-derived below from the tree rather than restated, so the claim fails
when the tree moves under it.
"""

from pathlib import Path
import re
import subprocess

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / ".github" / "dependabot.yml"
_WORKFLOWS = _ROOT / ".github" / "workflows"
_LABELER = _ROOT / ".github" / "labeler.yml"
_AGENTS = _ROOT / "AGENTS.md"
_CLAUDE = _ROOT / "CLAUDE.md"

# The ecosystem this repository configures. The header argues at length for
# the absence of every other one.
_ACTIONS = "github-actions"

# `uses: owner/repo@ref`, tolerating `- uses:` and any indentation. The same
# shape `test_workflow_action_pins.py` reads; here only the owner/repo half
# matters, because that is what a Dependabot group pattern is matched against.
_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)")

# GitHub Actions runs either extension, so both are scanned - a `.yaml`
# workflow left out of the count below would understate the actions in play.
_WORKFLOW_GLOBS = ("*.yml", "*.yaml")

# The owner every action GitHub publishes sits under. "First-party" in the
# header means these.
_FIRST_PARTY_OWNER = "actions"

# Dependabot's own cadences, and the noun each one reads as in prose.
_INTERVAL_NOUNS = {"daily": "day", "weekly": "week", "monthly": "month"}

# Enough of the number words to cover any plausible action count.
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# The sentence in the `groups:` comment that counts the ungrouped pull requests
# grouping avoids: "Four separate bumps a week for first-party actions".
_BUMPS_CLAIM = re.compile(
    r"(?P<count>\w+) separate bumps a (?P<noun>\w+) for first-party actions",
    re.IGNORECASE,
)

# The `| Prefix | Use for |` table in AGENTS.md. Each row is read as its
# backticked prefixes plus the text describing what they are for.
_TABLE_ROW = re.compile(r"^\|(?P<prefixes>[^|]+)\|(?P<use>[^|]+)\|\s*$")
_PREFIX = re.compile(r"`(?P<prefix>[a-z]+):`")

# A `run:` line that invokes the suite, in either of the two spellings the
# workflows use - `run: pytest ...` and a `pytest` line inside a block scalar.
# The job key `pytest:` and the job name `name: pytest (...)` are not
# invocations, so the character after the word has to be shell, not YAML.
_RUNS_PYTEST = re.compile(r"^(?:run:\s+)?pytest(?:\s|\\|$)")

# Dependabot's default when `open-pull-requests-limit` is absent. Zero is a
# valid value and means "propose nothing".
_DEFAULT_PR_LIMIT = 5


def _text(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _prose() -> str:
    """Return the file's comments as one whitespace-squashed line.

    The claims below are argued across wrapped comment lines, each carrying
    its own `#`. Matching a sentence against the raw text finds the half that
    happens to fit on one line and misses the rest.
    """
    comments = [
        line.strip().lstrip("#")
        for line in _text(_CONFIG).splitlines()
        if line.strip().startswith("#")
    ]
    return " ".join(" ".join(comments).split())


def _config() -> dict:
    """Return the parsed `.github/dependabot.yml`."""
    parsed = yaml.safe_load(_text(_CONFIG))
    assert isinstance(parsed, dict), ".github/dependabot.yml is not a mapping"
    return parsed


def _updates() -> list[dict]:
    """Return the `updates:` entries, which is where every value below lives."""
    updates = _config().get("updates")
    assert isinstance(updates, list) and updates, (
        "`updates:` is missing or empty, so Dependabot proposes nothing at all"
    )
    return updates


def _actions_entry() -> dict:
    """Return the single `github-actions` update entry."""
    entries = [
        entry for entry in _updates() if entry.get("package-ecosystem") == _ACTIONS
    ]
    assert len(entries) == 1, (
        f"expected exactly one {_ACTIONS} entry, found {len(entries)}"
    )
    return entries[0]


def _workflow_paths() -> list[Path]:
    """Return every workflow file, whichever extension it uses."""
    return sorted(path for glob in _WORKFLOW_GLOBS for path in _WORKFLOWS.glob(glob))


def _action_names() -> set[str]:
    """Return every `owner/repo` a workflow runs an action from.

    A local composite action and a container image are excluded: neither is a
    thing Dependabot proposes an update for, so neither belongs in a count of
    what the group covers.
    """
    names: set[str] = set()
    for path in _workflow_paths():
        for line in _text(path).splitlines():
            match = _USES.match(line)
            if match is None:
                continue
            ref = match.group("ref")
            if ref.startswith(("./", "docker://")):
                continue
            names.add(ref.split("@", 1)[0])
    assert names, "no `uses:` found in .github/workflows; the scan above is broken"
    return names


def _tracked() -> list[str]:
    """Return every path git tracks."""
    listed = subprocess.run(
        ["git", "ls-files"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(line for line in listed.stdout.splitlines() if line)


def _matches(pattern: str, name: str) -> bool:
    """Return whether a Dependabot group `pattern` covers dependency `name`.

    Hand-rolled rather than `fnmatch`, whose `*` also matches `/` and `[`, and
    which would report every assertion below as passing for the wrong reason.
    Dependabot's patterns are shell-style globs over the dependency name; for
    `github-actions` the dependency name is `owner/repo`.
    """
    expression = "".join(
        ".*" if character == "*" else re.escape(character) for character in pattern
    )
    return re.fullmatch(expression, name) is not None


@pytest.fixture(name="entry")
def entry_fixture() -> dict:
    """Return the `github-actions` update entry under test."""
    return _actions_entry()


# --------------------------------------------------------------------------
# The values that decide whether Dependabot reads the file at all.
# --------------------------------------------------------------------------


def test_the_schema_version_is_the_one_dependabot_reads() -> None:
    """A `version:` Dependabot does not know makes the whole file inert.

    Version 1 is a different, retired schema; anything else is rejected
    outright. Either way the failure arrives as silence.
    """
    assert _config().get("version") == 2, (
        "dependabot.yml must declare `version: 2`; a v1 or unknown version is "
        "not read, and no pull request is ever opened"
    )


def test_the_pattern_matcher_used_below_is_not_vacuous() -> None:
    """Guard the reader, or every group assertion passes by matching nothing.

    `*` is the only wildcard the committed config uses, and a matcher stuck at
    True or False would report the group as covering every action either way.
    """
    assert _matches("*", "actions/checkout")
    assert _matches("actions/*", "actions/checkout")
    assert not _matches("actions/*", "hacs/action")
    assert not _matches("actions/checkout", "actions/checkout-v2")


def test_nothing_caps_the_pull_requests_at_zero(entry: dict) -> None:
    """`open-pull-requests-limit: 0` disables an ecosystem without saying so.

    It is the documented way to turn updates off while leaving a configuration
    that still reads as switched on.
    """
    limit = entry.get("open-pull-requests-limit", _DEFAULT_PR_LIMIT)
    assert limit > 0, (
        "open-pull-requests-limit is 0, which stops Dependabot opening pull "
        "requests for the pinned actions entirely"
    )


def test_no_ignore_rule_freezes_the_pins(entry: dict) -> None:
    """An ignore rule re-creates the problem this file exists to solve.

    Not a style objection: a pin nobody proposes moving is the "stuck on
    whatever was current the day it was pinned" state the header names, and an
    ignore entry produces exactly that while the file still looks configured.
    """
    ignored = entry.get("ignore", [])
    assert not ignored, (
        f"the {_ACTIONS} entry ignores {ignored}, which stops those pins "
        "moving; if that is deliberate, say why here and in the file"
    )


# --------------------------------------------------------------------------
# Cadence, and the prose that counts on it.
# --------------------------------------------------------------------------


def test_the_actions_are_checked_weekly(entry: dict) -> None:
    """Interval is the one value with no safe default: it is required."""
    schedule = entry.get("schedule")
    assert isinstance(schedule, dict), "`schedule:` is required on every entry"
    assert schedule.get("interval") == "weekly", (
        f"interval is {schedule.get('interval')!r}; the header argues for a "
        "weekly cadence, and the comment beside `groups:` counts on it"
    )


def test_the_grouping_comment_states_the_configured_cadence() -> None:
    """Prose saying "a week" and a value saying `weekly` are one decision.

    Moving the interval to `daily` and leaving the comment behind turns the
    argument for grouping - the volume it saves a reviewer from - into a
    number that is a seventh of the truth.
    """
    claim = _BUMPS_CLAIM.search(_prose())
    assert claim is not None, (
        "the `groups:` comment no longer states how many bumps a period "
        "grouping avoids; the cadence claim below has nothing to check"
    )
    interval = _actions_entry()["schedule"]["interval"]
    assert claim.group("noun").lower() == _INTERVAL_NOUNS[interval], (
        f"the comment says 'a {claim.group('noun')}' while the schedule is {interval!r}"
    )


def test_the_grouping_comment_counts_the_first_party_actions() -> None:
    """The comment's number is a count of the tree, so the tree decides it.

    A fifth `actions/*` action arriving is exactly when the argument for
    grouping gets stronger, and exactly when the sentence making that argument
    silently becomes wrong.
    """
    claim = _BUMPS_CLAIM.search(_prose())
    assert claim is not None, "the `groups:` comment no longer counts anything"
    stated = claim.group("count").lower()
    assert stated in _NUMBER_WORDS, (
        f"'{stated}' is not a number word this test can resolve; spell the "
        "count out so it stays checkable"
    )
    first_party = {
        name for name in _action_names() if name.split("/", 1)[0] == _FIRST_PARTY_OWNER
    }
    assert _NUMBER_WORDS[stated] == len(first_party), (
        f"the comment says {stated} first-party actions; the workflows use "
        f"{len(first_party)}: {sorted(first_party)}"
    )


# --------------------------------------------------------------------------
# The group, and what it has to cover to mean what the comment says.
# --------------------------------------------------------------------------


def test_one_group_collects_every_update(entry: dict) -> None:
    """Promising "one pull request for all of them" is a claim about a count.

    Two groups is two pull requests a week, which is the volume the comment
    says grouping exists to avoid.
    """
    groups = entry.get("groups")
    assert isinstance(groups, dict) and len(groups) == 1, (
        f"expected exactly one group, found {sorted(groups or {})}; the "
        "comment promises one pull request for all of them"
    )


def test_the_group_covers_every_action_the_workflows_run(entry: dict) -> None:
    """A pattern narrower than the tree leaves actions bumping one by one.

    `actions/*` is the tempting narrowing, and it would silently drop the
    three third-party actions - the ones whose updates a reviewer most wants
    to see arrive together with everything else, not alone on a Tuesday.
    """
    patterns = [
        pattern
        for group in entry["groups"].values()
        for pattern in group.get("patterns", [])
    ]
    assert patterns, "the group carries no `patterns:`, so it collects nothing"
    uncovered = sorted(
        name
        for name in _action_names()
        if not any(_matches(pattern, name) for pattern in patterns)
    )
    assert not uncovered, (
        f"{uncovered} match no group pattern in {patterns}, so each gets its "
        "own pull request"
    )


def test_the_group_excludes_nothing(entry: dict) -> None:
    """`exclude-patterns:` would undo the coverage asserted above.

    It is checked separately because it is the one key that can empty a group
    while leaving `patterns: ["*"]` in place and reading as covering
    everything.
    """
    for name, group in entry["groups"].items():
        assert not group.get("exclude-patterns"), (
            f"group {name!r} excludes {group['exclude-patterns']}, which "
            "leaves those actions to bump on their own"
        )


# --------------------------------------------------------------------------
# The commit prefix, against the convention the repository documents.
# --------------------------------------------------------------------------


def _documented_prefixes() -> dict[str, str]:
    """Return `{prefix: what it is for}` from AGENTS.md's prefix table."""
    prefixes: dict[str, str] = {}
    for line in _text(_AGENTS).splitlines():
        row = _TABLE_ROW.match(line)
        if row is None:
            continue
        use = row.group("use").strip()
        for prefix in _PREFIX.finditer(row.group("prefixes")):
            prefixes[prefix.group("prefix")] = use
    assert len(prefixes) >= 3, (
        "AGENTS.md's commit-prefix table did not parse; every assertion below "
        "would pass against an empty mapping"
    )
    return prefixes


def test_the_commit_prefix_is_one_the_repository_documents() -> None:
    """Dependabot writes commits too, and nothing else checks what it writes.

    Every other commit in the repository is written by someone who read
    AGENTS.md. This one is written by a bot from a value in a file, which is
    the only commit prefix in the repository that can drift away from the
    documented set without a human seeing it happen.
    """
    prefix = _actions_entry()["commit-message"]["prefix"]
    documented = _documented_prefixes()
    assert prefix in documented, (
        f"commit prefix {prefix!r} is not in AGENTS.md's table "
        f"({sorted(documented)}); Dependabot would open pull requests with a "
        "subject the repository's own convention does not allow"
    )


def test_the_commit_prefix_is_not_a_user_visible_one() -> None:
    """A dependency bump is maintenance, and the release notes are generated.

    `feat:` and `fix:` are the rows AGENTS.md reserves for something a user can
    observe. A weekly action bump arriving under one of them puts CI
    maintenance at the top of the notes users read.
    """
    prefix = _actions_entry()["commit-message"]["prefix"]
    use = _documented_prefixes()[prefix]
    assert "user-visible" not in use, (
        f"AGENTS.md describes `{prefix}:` as {use!r}, which is a row reserved "
        "for changes a user can observe"
    )


def test_the_commit_prefix_is_one_claude_md_names_for_ci_changes() -> None:
    """CLAUDE.md narrows the table further for tooling and CI specifically.

    The two documents are hand-kept copies of one rule, so the prefix is
    checked against both: agreeing with the table while disagreeing with the
    sentence naming CI is still a contradiction somebody has to resolve.
    """
    sentence = re.search(
        r"Tooling, CI, docs and agent instructions are(?P<prefixes>[^.]+)\.",
        " ".join(_text(_CLAUDE).split()),
    )
    assert sentence is not None, (
        "CLAUDE.md no longer states which prefix a CI change uses; drop this "
        "test with it rather than leaving it matching nothing"
    )
    allowed = {match.group("prefix") for match in _PREFIX.finditer(sentence.group())}
    assert allowed, "no prefixes parsed out of CLAUDE.md's sentence"
    prefix = _actions_entry()["commit-message"]["prefix"]
    assert prefix in allowed, (
        f"CLAUDE.md says a CI change is {sorted(allowed)}; Dependabot is "
        f"configured to write {prefix!r}"
    )


def test_the_commit_prefix_carries_no_punctuation() -> None:
    """Dependabot appends the colon and space itself.

    Writing `ci:` here produces `ci:: bump ...`, which is a Conventional
    Commits subject with a type of `ci` and an empty one after it - visibly
    wrong, but only on a pull request nobody has opened yet.
    """
    prefix = _actions_entry()["commit-message"]["prefix"]
    assert prefix == prefix.strip().rstrip(":"), (
        f"commit prefix {prefix!r} carries its own separator; Dependabot adds "
        "`: ` after it"
    )


# --------------------------------------------------------------------------
# The label, against the repository's own label vocabulary.
# --------------------------------------------------------------------------


def test_every_label_dependabot_applies_is_one_the_repository_defines() -> None:
    """A label named only here is created on first use and means nothing.

    `.github/labeler.yml` is where this repository's path labels are defined.
    A typo in the value below does not fail anything: GitHub creates the label
    on the first pull request, in a generated colour, and the dependency
    filter a maintainer saved silently stops matching.

    The interaction in the other direction - `sync-labels: true` removing a
    label the config defines but does not match, which is what happens to this
    one today - is tracked in #246 and deliberately not asserted here.
    """
    labels = _actions_entry().get("labels")
    assert labels, "`labels:` is empty, so nothing marks these as dependency work"
    defined = set(yaml.safe_load(_text(_LABELER)))
    unknown = sorted(set(labels) - defined)
    assert not unknown, (
        f"{unknown} are applied by Dependabot but defined nowhere in "
        f".github/labeler.yml ({sorted(defined)})"
    )


def _labeler_globs(labels: set[str]) -> set[str]:
    """Return every glob `.github/labeler.yml` attaches to `labels`.

    Only the v5 `changed-files:` shape carries a glob; the other matchers read
    a branch name and are walked past. A label with no rule contributes
    nothing rather than raising, because whether it has one is the thing the
    caller is asserting.
    """
    rules = yaml.safe_load(_text(_LABELER))
    globs: set[str] = set()
    for label in labels:
        for matcher in rules.get(label) or []:
            for inner in matcher.get("changed-files") or []:
                for patterns in inner.values():
                    if isinstance(patterns, list):
                        globs.update(patterns)
                    else:
                        globs.add(patterns)
    return globs


def test_the_dependency_label_is_the_one_the_manifests_carry() -> None:
    """The label has to be the same one a hand-written bump would get.

    A `dependencies` label that marks Dependabot's pull requests and a
    different rule marking the requirements files would split one review
    filter into two, which is the failure this join exists to catch.
    """
    labels = set(_actions_entry()["labels"])
    globs = _labeler_globs(labels)
    tracked = set(_tracked())
    manifests = {
        "requirements_component.txt",
        "requirements_test.txt",
        "custom_components/sensi/manifest.json",
    }
    assert manifests <= tracked, (
        f"{sorted(manifests - tracked)} is no longer committed; this test "
        "names the dependency manifests explicitly"
    )
    assert manifests <= globs, (
        f"the label(s) {sorted(labels)} Dependabot applies cover {sorted(globs)}, "
        f"not the dependency manifests {sorted(manifests - globs)}"
    )


# --------------------------------------------------------------------------
# "Only github-actions is configured here", and the reason given for it.
# --------------------------------------------------------------------------


def test_github_actions_is_the_only_ecosystem_configured() -> None:
    """The header states this as a decision, so it is asserted as one.

    A second ecosystem arriving is fine; a second ecosystem arriving while the
    paragraph explaining why there is only one stays put is not.
    """
    ecosystems = sorted(entry.get("package-ecosystem") for entry in _updates())
    assert ecosystems == [_ACTIONS], (
        f"the file configures {ecosystems}; its header says only {_ACTIONS} is "
        "configured and explains at length why the Python side is left out"
    )


def test_the_python_side_really_is_pinned_in_the_requirements_files() -> None:
    """Half of the reason `pip` is left out: the pins are already exact.

    The argument is that pointing Dependabot at these would produce noise
    rather than updates. That only holds while the files pin exactly - a range
    is a place an update *would* have something to say.
    """
    for name in ("requirements_component.txt", "requirements_test.txt"):
        path = _ROOT / name
        assert path.exists(), f"{name} is named by dependabot.yml's header"
        for number, line in enumerate(_text(path).splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "-r ")):
                continue
            assert "==" in stripped, (
                f"{name}:{number}: {stripped!r} is not an exact pin, so the "
                "header's reason for leaving pip out no longer holds"
            )


def test_the_python_side_really_is_pinned_inside_workflow_run_blocks() -> None:
    """The other half: a `pip install` in a `run:` block Dependabot cannot see.

    Dependabot reads manifests, not shell. Every workflow that runs the suite
    installs it from a requirements file first, which is what makes those
    files the thing that decides which Home Assistant CI runs against - and
    what makes them invisible to an ecosystem Dependabot would point at a
    manifest. A workflow that installed the suite by naming packages inline
    would move that decision somewhere the header does not describe.
    """
    running, installing = set(), set()
    for path in _workflow_paths():
        for line in _text(path).splitlines():
            stripped = line.strip()
            if _RUNS_PYTEST.match(stripped):
                running.add(path.name)
            if "pip install" in stripped and "requirements" in stripped:
                installing.add(path.name)
    assert running, "no workflow runs pytest; the scan above is broken"
    assert running <= installing, (
        f"{sorted(running - installing)} runs the suite without installing "
        "from a requirements file; the header names those files as one of the "
        "two places the Python side is pinned"
    )


def test_no_action_manifest_sits_outside_the_watched_directory() -> None:
    """A composite action's own pins are watched by nothing.

    `directory: /` covers `.github/workflows/` and a root `action.yml`. A
    composite action committed under `.github/actions/` has `uses:` lines of
    its own that Dependabot never proposes moving - and that
    `test_workflow_action_pins.py`, which globs `.github/workflows/` only,
    never checks are pinned either. There are none today; this is what makes
    the first one arrive loudly.
    """
    unwatched = [
        path
        for path in _tracked()
        if Path(path).name in {"action.yml", "action.yaml"} and "/" in path
    ]
    assert not unwatched, (
        f"{unwatched} is an action manifest outside the repository root; add "
        "a `directory:` entry for it, or its pins never move"
    )
