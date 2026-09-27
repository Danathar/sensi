#!/usr/bin/env python3
"""Compare the master branch ruleset GitHub is enforcing against the one in Git.

A ruleset is repository configuration, not a file, so nothing in a checkout
proves what is actually being enforced. `.github/rulesets/master.json` says what
was agreed; this says whether GitHub agrees, and names the difference when it
does not.

It is deliberately asymmetric. Extra rules are fine, and so is a parameter set
stricter than agreed -- somebody tightening the branch is not drift worth
failing on. What it fails on is *weakening*: a bypass actor that was not
agreed, enforcement switched off, the target or an excluded ref changed so the
rule stops covering master, a rule removed, a required check dropped or no
longer pinned to the app agreed to report it, or a parameter relaxed below
what was agreed. Those are the changes that quietly return the branch to the
state issue #109 was opened about, and none of them shows up in a diff.

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


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _no_fewer_than(actual: object, agreed: object) -> bool:
    """Accept any count at least as large as the agreed one."""
    if _is_count(actual) and _is_count(agreed):
        return actual >= agreed
    return actual == agreed


def _not_switched_off(actual: object, agreed: object) -> bool:
    """Accept a protection that is on, whether or not it was agreed on."""
    return actual is True or actual == agreed


def _not_switched_on(actual: object, agreed: object) -> bool:
    """Accept an exemption that is off, whether or not it was agreed off."""
    return actual is False or actual == agreed


# Which live value honours an agreed parameter. A parameter's stronger
# direction is not always "on": `do_not_enforce_on_create` switched on skips
# the checks on a fresh branch, so for it the stricter value is False. A
# parameter not listed here is compared exactly, because its stronger direction
# is not obvious -- `allowed_merge_methods`, for instance, is a workflow choice
# rather than a protection, and a list that differs either way is a change to
# the agreement rather than a tightening of it.
PARAMETER_HONOURED_BY = {
    # pull_request
    "required_approving_review_count": _no_fewer_than,
    "require_code_owner_review": _not_switched_off,
    "dismiss_stale_reviews_on_push": _not_switched_off,
    "require_last_push_approval": _not_switched_off,
    "required_review_thread_resolution": _not_switched_off,
    # required_status_checks
    "strict_required_status_checks_policy": _not_switched_off,
    "do_not_enforce_on_create": _not_switched_on,
}


def _honours(key: str, actual: object, agreed: object) -> bool:
    """Whether the live value of `key` is at least as strict as the agreed one."""
    honoured_by = PARAMETER_HONOURED_BY.get(key)
    if honoured_by is None:
        return actual == agreed
    return honoured_by(actual, agreed)


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


def _required_status_checks(document: dict) -> list[dict]:
    rule = _rule(document, "required_status_checks")
    if rule is None:
        return []
    return rule.get("parameters", {}).get("required_status_checks", [])


def _required_checks(document: dict) -> set[str]:
    return {check.get("context") for check in _required_status_checks(document)}


def _required_check_sources(document: dict) -> dict[str, set[int | None]]:
    """Map each required check's context to the apps allowed to satisfy it.

    `None` means no `integration_id`: a status from any source will do.
    """
    sources: dict[str, set[int | None]] = {}
    for check in _required_status_checks(document):
        sources.setdefault(check.get("context"), set()).add(check.get("integration_id"))
    return sources


def fetch_live(repo: str, name: str) -> dict | None:
    """Return the live ruleset called `name`, or None if there is not one."""
    listing = subprocess.run(  # noqa: S603
        ["gh", "api", "--paginate", f"repos/{repo}/rulesets"],
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

    if live.get("target") != definition["target"]:
        problems.append(
            f"target is {live.get('target')!r}, not {definition['target']!r} - "
            "the ruleset no longer applies to branches"
        )

    live_ref_name = live.get("conditions", {}).get("ref_name", {})
    live_include = live_ref_name.get("include")
    agreed_include = definition["conditions"]["ref_name"]["include"]
    if live_include != agreed_include:
        problems.append(
            f"ref_name.include differs: agreed {agreed_include}, live {live_include}"
        )

    # Excluding fewer refs than agreed protects more; excluding one that was
    # not agreed - `~DEFAULT_BRANCH` above all - takes master back out.
    agreed_exclude = definition["conditions"]["ref_name"].get("exclude", [])
    extra_exclude = [
        ref for ref in live_ref_name.get("exclude") or [] if ref not in agreed_exclude
    ]
    if extra_exclude:
        problems.append(f"ref_name.exclude adds refs not agreed: {extra_exclude}")

    for rule in definition.get("rules", []):
        rule_type = rule["type"]
        live_rule = _rule(live, rule_type)
        if live_rule is None:
            problems.append(f"rule {rule_type!r} is not applied")
            continue
        # Only the parameters that were agreed. GitHub fills in defaults for
        # the rest, and failing on those would make this cry drift forever.
        # A live value stricter than the agreed one is not drift either; see
        # PARAMETER_HONOURED_BY for which direction is stricter.
        for key, agreed in (rule.get("parameters") or {}).items():
            if key == "required_status_checks":
                continue
            actual = (live_rule.get("parameters") or {}).get(key)
            if not _honours(key, actual, agreed):
                problems.append(
                    f"{rule_type}.{key}: agreed {agreed!r}, live {actual!r}"
                )

    live_sources = _required_check_sources(live)
    missing_checks = set(_required_check_sources(definition)) - set(live_sources)
    if missing_checks:
        problems.append(f"required checks no longer required: {sorted(missing_checks)}")

    # A check pinned to an app can only be satisfied by that app. Unpinned or
    # pinned to another app, a status from someone else passes it. An agreed
    # check with no pin accepts any source, so a live pin is only stricter.
    for context, agreed_ids in sorted(_required_check_sources(definition).items()):
        live_ids = live_sources.get(context)
        if live_ids is None or None in agreed_ids:
            continue
        loose = sorted(live_ids - agreed_ids, key=str)
        if loose:
            problems.append(
                f"required check {context!r}: agreed integration_id "
                f"{sorted(agreed_ids)}, live also accepts {loose}"
            )

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
        print("Read docs/branch-protection.md first - it explains each rule and why")
        print("bypass_actors must stay empty.")
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
