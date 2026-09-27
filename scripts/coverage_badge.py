#!/usr/bin/env python3
"""Turn a measured coverage percentage into durable badge and trend files.

The coverage number is otherwise visible only inside a single run: the job
summary disappears into the run's history and the uploaded `coverage.xml`
artifact expires. This script is the CI-facing half of publishing it
somewhere durable - it writes a shields.io endpoint-badge payload and
appends one row to a CSV trend file.

It only produces the files. `.github/workflows/coverage-gate.yml` is what
pushes them to the `coverage-data` branch, and only for a push to master.

    python3 scripts/coverage_badge.py 97.8 \
        --date 2026-09-05 --sha "$GITHUB_SHA" \
        --badge-out coverage-unit.json --trend-out coverage-trend.csv

The colour thresholds default to the gate in coverage-gate.yml, but CI
passes `--high "$MIN_COVERAGE"` so the badge turns yellow at exactly the
point the gate would fail rather than at a number duplicated here.
"""

import argparse
import json
from pathlib import Path
import sys

DEFAULT_HIGH = 93.0
DEFAULT_LOW = 85.0


def badge_color(
    percent: float, high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW
) -> str:
    """Green at or above the gate, yellow while it is still respectable, red below."""
    if percent >= high:
        return "brightgreen"
    if percent >= low:
        return "yellow"
    return "red"


def badge_payload(
    percent: float,
    label: str = "unit coverage",
    high: float = DEFAULT_HIGH,
    low: float = DEFAULT_LOW,
) -> dict:
    """Build the shields.io endpoint badge payload.

    One decimal rather than a rounded integer: 92.6% rendered as "93%" reads
    as passing a 93% gate that it actually fails.
    """
    return {
        "schemaVersion": 1,
        "label": label,
        "message": f"{percent:.1f}%",
        "color": badge_color(percent, high=high, low=low),
    }


def trend_row(date: str, sha: str, percent: float) -> str:
    """Format one CSV history row: date, commit, percentage."""
    return f"{date},{sha},{percent:.2f}"


def write_trend(path: Path, date: str, sha: str, percent: float) -> bool:
    """Append a row for `sha`, unless any row already records that commit.

    Returns whether a row was appended.

    Re-running a push-triggered workflow publishes the same SHA again, on the
    re-run's date and possibly after later commits have published. Appending
    would record the commit twice; rewriting its row would change its date,
    so the caller's "nothing changed, do not commit" check would push a
    commit that records nothing new. A commit already in the history is left
    exactly as it was first recorded, which makes a re-run a real no-op.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []

    if any(line.split(",")[1:2] == [sha] for line in lines):
        return False

    lines.append(trend_row(date, sha, percent))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def main(argv=None) -> int:
    """Record the trend row and, if one was recorded, write the badge payload.

    A commit already in the trend leaves the badge alone too: it keeps the
    number of the newest recorded commit. Otherwise re-running an older push
    after a newer one published would set the badge back to the older number.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("percent", type=float, help="coverage percentage (0-100)")
    parser.add_argument("--label", default="unit coverage")
    parser.add_argument("--high", type=float, default=DEFAULT_HIGH)
    parser.add_argument("--low", type=float, default=DEFAULT_LOW)
    parser.add_argument("--date", required=True, help="UTC date, YYYY-MM-DD")
    parser.add_argument("--sha", required=True, help="commit SHA")
    parser.add_argument(
        "--badge-out", required=True, help="path to write the badge JSON"
    )
    parser.add_argument(
        "--trend-out", required=True, help="path to append the trend row to"
    )
    args = parser.parse_args(argv)

    if not 0 <= args.percent <= 100:
        parser.error("percent must be between 0 and 100")

    if not write_trend(Path(args.trend_out), args.date, args.sha, args.percent):
        print(f"{args.sha} is already in the trend; leaving both files unchanged.")
        return 0

    with open(args.badge_out, "w", encoding="utf-8") as f:
        json.dump(
            badge_payload(args.percent, label=args.label, high=args.high, low=args.low),
            f,
        )
        f.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
