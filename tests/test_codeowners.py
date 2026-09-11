"""`.github/CODEOWNERS` declares the control plane, and nothing reads it.

The file's own header states the two jobs it does: GitHub auto-requests review
from the owner of every path a pull request touches, and - once the master
ruleset sets `require_code_owner_review` - makes that review mandatory. Both
jobs are done by *matching paths*, and every failure mode is silent.

A path renamed out from under a rule leaves the rule matching nothing. Nothing
errors: the file still parses, GitHub still applies it, and the path it was
meant to protect is now owned by the catch-all like any other file. That is
the same outcome as deleting the rule, arrived at by editing an unrelated
file.

Ordering is the second silent failure. The LAST matching pattern wins, so a
broad pattern appended below a specific one voids the specific one without
changing a character of it. The file says so in a comment; no test says so.

An owner that GitHub cannot resolve is the third. A handle that lost its `@`
is not a syntax error, it is a rule with no owners - so the path stops
requesting review while still looking owned in the diff.

None of that is checked anywhere. `tests/test_check_ruleset.py` and
`tests/test_required_checks_match_workflows.py` cover the ruleset end of
branch protection; the ruleset cannot condition on paths at all
(docs/branch-protection.md, "What this does not cover"), which is precisely
why the path list lives here. This file is the join nothing else makes.

Note what is deliberately NOT asserted: that every high-risk path has a
specific owner. `.github/risk-tiers.yml` puts four files in `tier/breaking`
and CODEOWNERS calls out one of them. Whether the other three belong in the
control plane is a judgement for a human, not an invariant - pinning it here
would turn a maintainer's decision into a red test.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CODEOWNERS = _ROOT / ".github" / "CODEOWNERS"
_RULESET = _ROOT / ".github" / "rulesets" / "master.json"
_BRANCH_PROTECTION = _ROOT / "docs" / "branch-protection.md"
_MANIFEST = _ROOT / "custom_components" / "sensi" / "manifest.json"

# The paths docs/branch-protection.md names as the control plane: "workflows,
# the ruleset, the AI policy, `AGENTS.md`, `custom_components/sensi/auth.py`".
# Each entry is a committed file that must end up owned by something narrower
# than the catch-all, so that turning `require_code_owner_review` on actually
# scopes review to these paths rather than to everything equally.
#
# A file added here is a claim that it is part of the boundary. A file removed
# is a decision that it is not - both belong in a diff, which is the point of
# listing them rather than deriving them.
_CONTROL_PLANE = (
    ".github/workflows/ci.yml",
    ".github/workflows/coverage-gate.yml",
    ".github/workflows/labeler.yml",
    ".github/workflows/nightly.yml",
    ".github/workflows/release.yml",
    ".github/workflows/validate.yml",
    ".github/rulesets/master.json",
    ".github/CODEOWNERS",
    ".github/risk-tiers.yml",
    "docs/SECURITY-AI.md",
    "docs/branch-protection.md",
    "AGENTS.md",
    ".claude/settings.json",
    "custom_components/sensi/auth.py",
)

# `@user`, or `@org/team`. GitHub also accepts a bare email address, which is
# not used here; adding one would need this pattern widened deliberately
# rather than a test quietly passing on a typo that resolves to nobody.
_OWNER = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:/[A-Za-z0-9._-]+)?$")


@dataclass(frozen=True)
class Rule:
    """One `<pattern> <owner>...` line, with the line it came from."""

    line_number: int
    pattern: str
    owners: tuple[str, ...]

    def __str__(self) -> str:
        """Render as it appears in the file, for assertion messages."""
        return f"{_CODEOWNERS.name}:{self.line_number}: {self.pattern}"


def _parse(text: str) -> list[Rule]:
    """Every rule in `text`, in file order - which is match-precedence order."""
    rules: list[Rule] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        rules.append(Rule(number, pattern, tuple(owners)))
    return rules


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a CODEOWNERS pattern into a regex over repository-relative paths.

    Gitignore semantics, which is what GitHub applies: a leading `/` anchors
    at the repository root, a trailing `/` restricts the match to what is
    *inside* that directory, a pattern with no internal `/` matches at any
    depth, a single `*` does not cross a `/` and `**` does.

    Hand-rolled rather than delegated to `fnmatch` (whose `*` crosses `/`, so
    it answers a different question) or to `pathlib.Path.match` (whose `**`
    handling changed in 3.13).
    """
    directory_only = pattern.endswith("/")
    body = pattern.rstrip("/")
    rooted = body.startswith("/")
    body = body.lstrip("/")
    # A pattern floats only when it carries no separator at all: `auth.py`
    # owns every `auth.py` in the tree, while both `/docs/x.md` and `docs/x.md`
    # mean the one at the root.
    anchored = rooted or "/" in body

    out: list[str] = []
    index = 0
    while index < len(body):
        if body.startswith("**", index):
            out.append(".*")
            index += 2
        elif body[index] == "*":
            out.append("[^/]*")
            index += 1
        elif body[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(body[index]))
            index += 1

    prefix = "" if anchored else r"(?:.*/)?"
    # `dir/` owns the files under `dir`; `file` owns itself, and owns what is
    # under it when it turns out to name a directory.
    suffix = "/.*" if directory_only else r"(?:/.*)?"
    return re.compile(f"^{prefix}{''.join(out)}{suffix}$")


def _matches(pattern: str, path: str) -> bool:
    """Whether `pattern` claims `path`."""
    return _pattern_to_regex(pattern).search(path) is not None


def _owner_of(rules: list[Rule], path: str) -> Rule | None:
    """Return the rule that wins for `path` - the last one that matches it."""
    for rule in reversed(rules):
        if _matches(rule.pattern, path):
            return rule
    return None


def _tracked_paths() -> list[str]:
    """Every committed path, as CODEOWNERS sees them: root-relative, no `./`.

    Read from git rather than from a directory walk so that build output, a
    virtualenv or a stale `coverage.xml` in the working tree cannot satisfy a
    rule that no committed file satisfies.
    """
    completed = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [path for path in completed.stdout.split("\0") if path]


@pytest.fixture(scope="module")
def rules() -> list[Rule]:
    """Return the committed rules, in precedence order."""
    return _parse(_CODEOWNERS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tracked() -> list[str]:
    """Every committed path in the repository."""
    return _tracked_paths()


# --- the reader itself -----------------------------------------------------
# Every assertion below is "for each rule" or "for each path". Both quantify
# over a list this file builds, and both are vacuously true over an empty one,
# so a parser that silently stopped reading would turn the whole module green.


def test_codeowners_is_committed() -> None:
    """The file the rest of this module reads exists."""
    assert _CODEOWNERS.is_file(), f"{_CODEOWNERS} is missing"


def test_rules_were_read(rules: list[Rule]) -> None:
    """The parser found rules, and gave each of them an owner."""
    assert len(rules) >= 2, f"only {len(rules)} rule(s) parsed"
    assert all(rule.owners for rule in rules)


def test_tracked_paths_were_read(tracked: list[str]) -> None:
    """`git ls-files` answered, with the paths this repository is made of."""
    assert len(tracked) > 50, f"only {len(tracked)} tracked path(s)"
    assert ".github/CODEOWNERS" in tracked


def test_comments_and_blanks_are_not_rules() -> None:
    """A comment line, a blank line and a trailing comment are all handled."""
    parsed = _parse(
        "# leading comment\n\n   \n*    @a\n/docs/  @b @c  # why docs are owned\n"
    )
    assert [(rule.pattern, rule.owners) for rule in parsed] == [
        ("*", ("@a",)),
        ("/docs/", ("@b", "@c")),
    ]
    assert [rule.line_number for rule in parsed] == [4, 5]


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        # The catch-all reaches every depth.
        ("*", "README.md", True),
        ("*", "custom_components/sensi/auth.py", True),
        # A rooted directory owns what is inside it, and nothing beside it.
        ("/.github/workflows/", ".github/workflows/ci.yml", True),
        ("/.github/workflows/", ".github/workflows/nested/ci.yml", True),
        ("/.github/workflows/", ".github/workflows", False),
        ("/.github/workflows/", ".github/labeler.yml", False),
        ("/.github/workflows/", "docs/.github/workflows/ci.yml", False),
        # A rooted file owns exactly itself.
        ("/AGENTS.md", "AGENTS.md", True),
        ("/AGENTS.md", "docs/AGENTS.md", False),
        ("/AGENTS.md", "AGENTS.md.bak", False),
        ("/custom_components/sensi/auth.py", "custom_components/sensi/auth.py", True),
        (
            "/custom_components/sensi/auth.py",
            "custom_components/sensi/client.py",
            False,
        ),
        # A bare name floats to any depth - the case that makes `fnmatch` and
        # a rooted regex give different answers.
        ("auth.py", "custom_components/sensi/auth.py", True),
        ("auth.py", "auth.py", True),
        # One separator is enough to root it, with or without the leading `/`.
        ("docs/quality.md", "docs/quality.md", True),
        ("docs/quality.md", "custom_components/docs/quality.md", False),
        # A single star stops at a separator; a double star crosses it.
        ("/docs/*.md", "docs/quality.md", True),
        ("/docs/*.md", "docs/reflections/README.md", False),
        ("/docs/**/*.md", "docs/reflections/README.md", True),
        # `?` is one character, and not a separator.
        ("/docs/qualit?.md", "docs/quality.md", True),
        ("/docs/docs?quality.md", "docs/docs/quality.md", False),
        # A dot in a pattern is a dot, not "any character".
        ("/hacs.json", "hacsxjson", False),
    ],
)
def test_matcher(pattern: str, path: str, expected: bool) -> None:
    """The matcher answers gitignore semantics, not `fnmatch`'s."""
    assert _matches(pattern, path) is expected


# --- the rules --------------------------------------------------------------


def test_every_rule_matches_a_committed_path(
    rules: list[Rule], tracked: list[str]
) -> None:
    """No rule names a path that no longer exists.

    This is the rename failure. The rule stays in the file, reads as
    protection in a diff, and claims nothing.
    """
    dead = [
        str(rule)
        for rule in rules
        if not any(_matches(rule.pattern, p) for p in tracked)
    ]
    assert not dead, "rules matching no committed path: " + "; ".join(dead)


def test_every_rule_owns_a_committed_path(
    rules: list[Rule], tracked: list[str]
) -> None:
    """No rule is shadowed by a later one.

    Matching something is not the same as owning it: the last match wins, so
    a rule can match a hundred paths and still decide nothing. This is the
    ordering failure, and it is invisible in the shadowed rule's own text.
    """
    owning = {_owner_of(rules, path) for path in tracked}
    shadowed = [str(rule) for rule in rules if rule not in owning]
    assert not shadowed, "rules shadowed by a later rule: " + "; ".join(shadowed)


def test_every_committed_path_has_an_owner(
    rules: list[Rule], tracked: list[str]
) -> None:
    """Nothing in the repository is unowned.

    Today this is the first rule's job. Narrowing or deleting that catch-all
    would leave new files silently requesting review from nobody.
    """
    unowned = [path for path in tracked if _owner_of(rules, path) is None]
    assert not unowned, f"{len(unowned)} unowned path(s), e.g. {unowned[:5]}"


def test_the_catch_all_comes_first(rules: list[Rule], tracked: list[str]) -> None:
    """Only the first rule may own everything.

    A second catch-all anywhere below would win for every path and reduce the
    whole control-plane list to decoration.
    """
    assert rules[0].pattern == "*", f"first rule is {rules[0]}"
    for rule in rules[1:]:
        claimed = [path for path in tracked if _matches(rule.pattern, path)]
        assert len(claimed) < len(tracked), f"{rule} matches every committed path"


def test_owners_are_resolvable_handles(rules: list[Rule]) -> None:
    """Every owner is a `@handle` or `@org/team`.

    A handle that lost its `@` is not a parse error - the line keeps working
    and the path stops requesting review from anyone.
    """
    bad = [
        f"{rule}: {owner}"
        for rule in rules
        for owner in rule.owners
        if not _OWNER.fullmatch(owner)
    ]
    assert not bad, "unresolvable owners: " + "; ".join(bad)


def test_no_pattern_is_repeated(rules: list[Rule]) -> None:
    """A pattern listed twice makes the first listing dead on arrival."""
    seen: dict[str, Rule] = {}
    for rule in rules:
        assert rule.pattern not in seen, f"{rule} repeats {seen[rule.pattern]}"
        seen[rule.pattern] = rule


# --- the join docs/branch-protection.md promises ----------------------------


@pytest.mark.parametrize("path", _CONTROL_PLANE)
def test_control_plane_path_is_specifically_owned(
    path: str, rules: list[Rule], tracked: list[str]
) -> None:
    """Each control-plane path is owned by a rule narrower than the catch-all.

    Being owned by `*` is the same as not being in the file. When
    `require_code_owner_review` goes on, a control-plane path that falls
    through to the default is reviewed exactly like a docstring change.
    """
    assert path in tracked, f"{path} is not committed"
    owner = _owner_of(rules, path)
    assert owner is not None and owner.pattern != "*", (
        f"{path} falls through to the catch-all"
    )


def test_manifest_codeowners_are_owners_here(rules: list[Rule]) -> None:
    """The maintainer the integration publishes is an owner in this file.

    `manifest.json`'s `codeowners` is what Home Assistant shows users as
    responsible for the integration; this file is what GitHub asks to review
    a change. They are maintained by hand in two places and a handle change
    that lands in one of them leaves the other pointing at a stale account.
    """
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    published = manifest["codeowners"]
    assert published, "manifest.json declares no codeowners"

    owners = {owner for rule in rules for owner in rule.owners}
    missing = sorted(set(published) - owners)
    assert not missing, f"manifest codeowners absent from CODEOWNERS: {missing}"


def test_branch_protection_doc_still_names_this_file() -> None:
    """The prose that explains why these paths are the boundary still exists.

    `_CONTROL_PLANE` above is a hand-kept list, and its justification lives in
    docs/branch-protection.md. If that section is rewritten away, the list
    here is the only remaining statement of intent and should be revisited
    rather than left orphaned.
    """
    doc = _BRANCH_PROTECTION.read_text(encoding="utf-8")
    for mention in (".github/CODEOWNERS", "custom_components/sensi/auth.py"):
        assert mention in doc, f"docs/branch-protection.md no longer names {mention}"


def test_doc_and_ruleset_agree_on_code_owner_review() -> None:
    """The documented enforcement state matches the committed ruleset.

    docs/branch-protection.md states `require_code_owner_review` is **false**
    and argues for it; CODEOWNERS' own header says the file is "a declaration,
    not a gate" until that changes. Flipping the ruleset is a one-word edit in
    a different file, and it changes what every rule above costs when it is
    wrong: a stale pattern goes from harmless to a path that merges without
    the review the ruleset believes it required.
    """
    ruleset = json.loads(_RULESET.read_text(encoding="utf-8"))
    pull_request = [
        rule for rule in ruleset["rules"] if rule.get("type") == "pull_request"
    ]
    assert len(pull_request) == 1, f"{len(pull_request)} pull_request rules"
    enforced = pull_request[0]["parameters"]["require_code_owner_review"]

    doc = " ".join(_BRANCH_PROTECTION.read_text(encoding="utf-8").split())
    stated = re.findall(r"`require_code_owner_review` is \*\*(true|false)\*\*", doc)
    assert len(stated) == 1, (
        "docs/branch-protection.md no longer states require_code_owner_review "
        f"exactly once (found {stated})"
    )
    assert stated[0] == str(enforced).lower(), (
        f"ruleset has require_code_owner_review={enforced}, "
        f"docs/branch-protection.md says {stated[0]}"
    )
