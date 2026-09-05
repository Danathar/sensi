#!/usr/bin/env python3
"""Report pull request acceptance metrics for this repository.

Answers the question ACMM calls "PR acceptance": of the changes that were
proposed, how many were taken, how long did they take, and how much review did
they need. Splitting that by author matters here because a growing share of the
changes are agent-authored - an acceptance rate is only interesting next to the
one it is being compared against.

Reads through `gh`, so it uses whatever credentials `gh auth status` reports.
No third-party packages, no network code of its own.

    python3 scripts/pr_metrics.py                    # default: last 50 merged/closed
    python3 scripts/pr_metrics.py --limit 200
    python3 scripts/pr_metrics.py --since 2026-01-01
    python3 scripts/pr_metrics.py --json
"""

import argparse
from collections import defaultdict
from datetime import datetime
import json
import shutil
import statistics
import subprocess
import sys

FIELDS = [
    "number",
    "title",
    "state",
    "author",
    "createdAt",
    "closedAt",
    "mergedAt",
    "additions",
    "deletions",
    "changedFiles",
    "reviews",
    "comments",
]


def die(message: str) -> None:
    """Print an error and exit non-zero."""
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def fetch(limit: int, repo: str | None) -> list[dict]:
    """Return closed pull requests as dictionaries, newest first."""
    if not shutil.which("gh"):
        die("the GitHub CLI (gh) is required; see https://cli.github.com/")

    command = [
        "gh",
        "pr",
        "list",
        "--state",
        "closed",
        "--limit",
        str(limit),
        "--json",
        ",".join(FIELDS),
    ]
    if repo:
        command += ["--repo", repo]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        die(result.stderr.strip() or "gh pr list failed")

    return json.loads(result.stdout)


def parse_time(value: str | None) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def hours_between(start: str | None, end: str | None) -> float | None:
    """Return the whole hours between two GitHub timestamps."""
    first, last = parse_time(start), parse_time(end)
    if not first or not last:
        return None
    return (last - first).total_seconds() / 3600


def is_bot(author: dict) -> bool:
    """Return True if the pull request author is an app or a bot account."""
    login = (author or {}).get("login", "")
    return (author or {}).get("is_bot", False) or login.endswith("[bot]")


def summarise(pulls: list[dict], since: str | None) -> dict:
    """Reduce raw pull requests to the reported metrics."""
    if since:
        cutoff = parse_time(f"{since}T00:00:00Z")
        pulls = [p for p in pulls if parse_time(p["createdAt"]) >= cutoff]

    buckets: dict[str, list[dict]] = defaultdict(list)
    for pull in pulls:
        buckets["all"].append(pull)
        buckets["bot" if is_bot(pull.get("author")) else "human"].append(pull)

    report = {"total_considered": len(pulls), "since": since, "buckets": {}}

    for name, group in buckets.items():
        if not group:
            continue

        merged = [p for p in group if p.get("mergedAt")]
        rejected = [p for p in group if not p.get("mergedAt")]

        ages = [
            hours
            for p in merged
            if (hours := hours_between(p["createdAt"], p["mergedAt"]))
        ]
        reviews = [len(p.get("reviews") or []) for p in merged]
        churn = [p.get("additions", 0) + p.get("deletions", 0) for p in merged]

        report["buckets"][name] = {
            "proposed": len(group),
            "merged": len(merged),
            "closed_unmerged": len(rejected),
            "acceptance_rate": round(len(merged) / len(group), 3),
            "median_hours_to_merge": round(statistics.median(ages), 1)
            if ages
            else None,
            "max_hours_to_merge": round(max(ages), 1) if ages else None,
            "median_reviews_before_merge": statistics.median(reviews)
            if reviews
            else None,
            "median_lines_changed": statistics.median(churn) if churn else None,
        }

    return report


def render(report: dict) -> str:
    """Render the report as a Markdown table."""
    lines = []
    scope = f" since {report['since']}" if report["since"] else ""
    lines.append(f"Pull requests considered{scope}: {report['total_considered']}\n")

    header = (
        "| Author | Proposed | Merged | Closed unmerged | Acceptance | "
        "Median h to merge | Median reviews | Median lines |"
    )
    lines.append(header)
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")

    for name in ("all", "human", "bot"):
        bucket = report["buckets"].get(name)
        if not bucket:
            continue

        def show(value: object) -> str:
            return "-" if value is None else str(value)

        lines.append(
            f"| {name} | {bucket['proposed']} | {bucket['merged']} | "
            f"{bucket['closed_unmerged']} | {bucket['acceptance_rate']:.0%} | "
            f"{show(bucket['median_hours_to_merge'])} | "
            f"{show(bucket['median_reviews_before_merge'])} | "
            f"{show(bucket['median_lines_changed'])} |"
        )

    return "\n".join(lines)


def main() -> int:
    """Parse arguments, fetch, and print the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=50, help="pull requests to fetch")
    parser.add_argument("--since", help="ignore pull requests opened before YYYY-MM-DD")
    parser.add_argument("--repo", help="OWNER/REPO (defaults to the current one)")
    parser.add_argument("--json", action="store_true", help="emit JSON, not Markdown")
    args = parser.parse_args()

    report = summarise(fetch(args.limit, args.repo), args.since)

    if not report["total_considered"]:
        print("No closed pull requests matched.")
        return 0

    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
