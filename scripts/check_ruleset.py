#!/usr/bin/env python3
"""Compare the master branch ruleset GitHub is enforcing against the one in Git.

A ruleset is repository configuration, not a file, so nothing in a checkout
proves what is actually being enforced. `.github/rulesets/master.json` says what
was agreed; this says whether GitHub agrees, and names the difference when it
does not.

It is deliberately asymmetric. Extra rules are fine -- somebody tightening the
branch is not drift worth failing on. What it fails on is *weakening*: a bypass
actor that was not agreed, enforcement switched off, a rule removed, or a
required check dropped. Those are the changes that quietly return the branch to
the state issue #109 was opened about, and none of them shows up in a diff.

Two modes:

    python3 scripts/check_ruleset.py --offline    # is the definition sane?
    python3 scripts/check_ruleset.py              # does GitHub match it?

`--offline` needs no token and no network, so it can run anywhere. The online
mode reads rulesets through `gh api`, which needs admin on the repository.

Exit status: 0 matched (or offline and valid), 1 drift or not applied yet,
2 the definition or the environment is unusable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

DEFAULT_DEFINITION = (
    Path(__file__).resolve().parents[1] / ".github/rulesets/master.json"
)
DEFAULT_REPO = "Danathar/sensi"

# Rules whose absence takes the branch back to unprotected. Checked by type, so
# a rule renamed upstream fails loudly instead of being silently skipped.
REQUIRED_RULE_TYPES = frozenset(
    {"deletion", "non_fast_forward", "pull_request", "required_status_checks"}
)


class DefinitionError(RuntimeError):
    """The committed definition is not usable."""


def load_definition(path: Path) -> dict:
    """Read the ruleset definition and check it says what it must."""
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DefinitionError(f"Unable to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DefinitionError(f"{path} is not valid JSON: {exc}") from exc

    if definition.get("target") != "branch":
        raise DefinitionError("target must be 'branch'")
    if definition.get("enforcement") != "active":
        raise DefinitionError(
            "enforcement must be 'active'; a definition that ships disabled "
            "protects nothing and reads as though it does"
        )
    include = definition.get("conditions", {}).get("ref_name", {}).get("include")
    if include != ["~DEFAULT_BRANCH"]:
        raise DefinitionError(
            "conditions.ref_name.include must be exactly ['~DEFAULT_BRANCH'] so "
            "the rule follows the default branch rather than a name that can move"
        )

    types = {rule.get("type") for rule in definition.get("rules", [])}
    missing = REQUIRED_RULE_TYPES - types
    if missing:
        raise DefinitionError(f"definition is missing rule type(s): {sorted(missing)}")

    checks = _required_checks(definition)
    if not checks:
        raise DefinitionError(
            "required_status_checks lists no checks, so the pull request it "
            "forces would have nothing to pass"
        )
    return definition


def _rule(document: dict, rule_type: str) -> dict | None:
    for rule in document.get("rules", []):
        if rule.get("type") == rule_type:
            return rule
    return None


def _required_checks(document: dict) -> set[str]:
    rule = _rule(document, "required_status_checks")
    if rule is None:
        return set()
    return {
        check.get("context")
        for check in rule.get("parameters", {}).get("required_status_checks", [])
    }


def fetch_live(repo: str, name: str) -> dict | None:
    """Return the live ruleset called `name`, or None if there is not one."""
    listing = subprocess.run(  # noqa: S603
        ["gh", "api", f"repos/{repo}/rulesets"],
        capture_output=True,
        text=True,
        check=False,
    )
    if listing.returncode != 0:
        raise DefinitionError(
            f"Unable to list rulesets for {repo}: "
            f"{listing.stderr.strip().splitlines()[-1] if listing.stderr.strip() else 'gh failed'}"
        )
    for entry in json.loads(listing.stdout or "[]"):
        if entry.get("name") == name:
            detail = subprocess.run(  # noqa: S603
                ["gh", "api", f"repos/{repo}/rulesets/{entry['id']}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if detail.returncode != 0:
                raise DefinitionError(f"Unable to read ruleset {entry['id']}")
            return json.loads(detail.stdout)
    return None


def compare(definition: dict, live: dict) -> list[str]:
    """Every way the live ruleset is weaker than the definition."""
    problems: list[str] = []

    if live.get("enforcement") != "active":
        problems.append(
            f"enforcement is {live.get('enforcement')!r}, not 'active' - the "
            "ruleset exists but is not stopping anything"
        )

    # The one that matters most. A bypass actor is invisible in every other
    # view: the rules all still read as enforced, for everyone except whoever
    # was added here.
    if live.get("bypass_actors") != definition.get("bypass_actors", []):
        problems.append(
            f"bypass_actors differ: agreed {definition.get('bypass_actors', [])}, "
            f"live {live.get('bypass_actors')}"
        )

    live_include = live.get("conditions", {}).get("ref_name", {}).get("include")
    agreed_include = definition["conditions"]["ref_name"]["include"]
    if live_include != agreed_include:
        problems.append(
            f"ref_name.include differs: agreed {agreed_include}, live {live_include}"
        )

    for rule in definition.get("rules", []):
        rule_type = rule["type"]
        live_rule = _rule(live, rule_type)
        if live_rule is None:
            problems.append(f"rule {rule_type!r} is not applied")
            continue
        # Only the parameters that were agreed. GitHub fills in defaults for
        # the rest, and failing on those would make this cry drift forever.
        for key, agreed in (rule.get("parameters") or {}).items():
            if key == "required_status_checks":
                continue
            actual = (live_rule.get("parameters") or {}).get(key)
            if actual != agreed:
                problems.append(
                    f"{rule_type}.{key}: agreed {agreed!r}, live {actual!r}"
                )

    missing_checks = _required_checks(definition) - _required_checks(live)
    if missing_checks:
        problems.append(f"required checks no longer required: {sorted(missing_checks)}")

    return problems


def main(argv: list[str] | None = None) -> int:
    """Check the definition, and unless offline, what GitHub is enforcing."""
    parser = argparse.ArgumentParser(
        description="Check the live master ruleset against the committed definition.",
    )
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Validate the definition only; do not contact GitHub.",
    )
    args = parser.parse_args(argv)

    try:
        definition = load_definition(args.definition)
    except DefinitionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    checks = sorted(_required_checks(definition))
    if args.offline:
        print(
            f"Definition is valid: {len(definition['rules'])} rules, "
            f"{len(checks)} required checks, "
            f"{len(definition.get('bypass_actors', []))} bypass actors."
        )
        return 0

    try:
        live = fetch_live(args.repo, definition["name"])
    except DefinitionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if live is None:
        print(f"{args.repo} has no ruleset named {definition['name']!r}.")
        print()
        print("The branch is unprotected. Apply the agreed definition with:")
        print(
            f"  gh api --method POST repos/{args.repo}/rulesets --input {args.definition}"
        )
        print()
        print("Read docs/branch-protection.md first - there is an ordering")
        print("dependency, and applying this before it is met breaks releases.")
        return 1

    problems = compare(definition, live)
    if problems:
        print(f"Ruleset {definition['name']!r} on {args.repo} is weaker than agreed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"Ruleset {definition['name']!r} on {args.repo} matches the committed definition."
    )
    print(f"  {len(checks)} required checks, no bypass actors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
