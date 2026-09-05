#!/usr/bin/env python3
"""Classify a pull request into a risk tier and a size bucket.

Rules live in `.github/risk-tiers.yml`; the prose behind them is in
`docs/risk-tiers.md`. The tier is the *highest* one whose paths the change
touches - it is not additive and not a score.

Reads the changed files and the line counts from `gh`, or from arguments so it
can be exercised without a pull request:

    python3 scripts/classify_pr.py --pr 44
    python3 scripts/classify_pr.py --files custom_components/sensi/client.py --lines 30
    git diff --name-only master... | python3 scripts/classify_pr.py --stdin --lines 0

Prints JSON describing the labels to apply.
"""

import argparse
from fnmatch import fnmatch
import json
from pathlib import Path
import shutil
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / ".github" / "risk-tiers.yml"

# GitHub rejects a label description longer than this with HTTP 422.
MAX_LABEL_DESCRIPTION = 100


def die(message: str) -> None:
    """Print an error and exit non-zero."""
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def matches(path: str, pattern: str) -> bool:
    """Return True if a repository path matches a rule pattern.

    `**` is treated the way the labeler config uses it: a prefix match on the
    directory, not fnmatch's single-segment behaviour.
    """
    if pattern == "**":
        return True
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return fnmatch(path, pattern)


def load_rules() -> dict:
    """Load and validate the tier and size rules."""
    if not RULES.exists():
        die(f"{RULES} not found")

    rules = yaml.safe_load(RULES.read_text())

    # Checked here rather than discovered as an HTTP 422 in CI, where the
    # symptom is the unrelated-looking "'tier/runtime' not found" from the
    # subsequent add-label call.
    for tier in rules["tiers"]:
        description = tier.get("label_description", "")
        if len(description) > MAX_LABEL_DESCRIPTION:
            die(
                f"{tier['name']}: label_description is {len(description)} "
                f"characters; GitHub allows {MAX_LABEL_DESCRIPTION}"
            )

    return rules


def fetch_pr(number: int, repo: str | None) -> tuple[list[str], int]:
    """Return the changed paths and total changed lines for a pull request."""
    if not shutil.which("gh"):
        die("the GitHub CLI (gh) is required; see https://cli.github.com/")

    command = ["gh", "pr", "view", str(number), "--json", "files"]
    if repo:
        command += ["--repo", repo]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        die(result.stderr.strip() or "gh pr view failed")

    files = json.loads(result.stdout)["files"]
    paths = [f["path"] for f in files]
    lines = sum(f.get("additions", 0) + f.get("deletions", 0) for f in files)
    return paths, lines


def classify(paths: list[str], lines: int, rules: dict) -> dict:
    """Return the tier and size labels for a set of changed paths."""
    tier = None
    for candidate in rules["tiers"]:
        if any(
            matches(path, pattern) for path in paths for pattern in candidate["paths"]
        ):
            tier = candidate
            break

    size = None
    for candidate in rules["sizes"]:
        cap = candidate.get("max_lines")
        if cap is None or lines <= cap:
            size = candidate
            break

    return {
        "changed_files": len(paths),
        "changed_lines": lines,
        "tier": tier
        and {
            "name": tier["name"],
            "colour": tier.get("label_colour", "ededed"),
            # The short one - GitHub rejects a description over 100 characters.
            # The long `description` in the rules file is for humans reading it.
            "description": tier.get("label_description", ""),
        },
        "size": size
        and {
            "name": size["name"],
            "colour": "c5def5",
            "description": f"{lines} lines changed",
        },
    }


def main() -> int:
    """Parse arguments, classify, and print the result as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pr", type=int, help="pull request number to read via gh")
    source.add_argument("--files", nargs="+", help="changed paths")
    source.add_argument("--stdin", action="store_true", help="read paths from stdin")
    parser.add_argument("--lines", type=int, help="total additions + deletions")
    parser.add_argument("--repo", help="OWNER/REPO (defaults to the current one)")
    args = parser.parse_args()

    if args.pr:
        paths, lines = fetch_pr(args.pr, args.repo)
        if args.lines is not None:
            lines = args.lines
    else:
        paths = args.files or [line.strip() for line in sys.stdin if line.strip()]
        if args.lines is None:
            die("--lines is required unless --pr is used")
        lines = args.lines

    if not paths:
        die("no changed paths")

    print(json.dumps(classify(paths, lines, load_rules()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
