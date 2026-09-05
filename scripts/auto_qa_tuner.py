#!/usr/bin/env python3
"""Propose an adjustment to the coverage gate, based on what it measured.

Reads `.github/auto-qa-tuning.json` for the policy and a coverage XML report
for the measurement, and prints whether the threshold should move.

It deliberately **proposes and never applies**. A gate that raises itself is a
gate that fails on a change nobody connected to it, and the person who has to
understand the failure was not in the loop when it moved.

    python3 scripts/auto_qa_tuner.py                       # reads ./coverage.xml
    python3 scripts/auto_qa_tuner.py --coverage build/coverage.xml
    python3 scripts/auto_qa_tuner.py --markdown >> "$GITHUB_STEP_SUMMARY"

Exit status is 0 whether or not a change is proposed; a proposal is information,
not a failure.
"""

import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / ".github" / "auto-qa-tuning.json"


def die(message: str) -> None:
    """Print an error and exit non-zero."""
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def measured_coverage(path: Path) -> float:
    """Return line coverage as a percentage from a Cobertura XML report."""
    if not path.exists():
        die(f"{path} not found - run pytest with --cov-report=xml first")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as err:
        die(f"{path} is not valid XML: {err}")
    return float(root.get("line-rate", 0)) * 100


def evaluate(measured: float, gate: dict) -> dict:
    """Decide whether the threshold should move, and say why."""
    policy = gate["policy"]
    current = gate["current"]
    headroom = policy["headroom"]
    step = policy["step"]
    ceiling = gate["ceiling"]

    result = {
        "measured": round(measured, 2),
        "current": current,
        "proposed": current,
        "change": False,
    }

    if measured < current:
        result["reason"] = (
            f"Measured {measured:.2f}% is below the {current}% gate. "
            "The gate has already failed; nothing to tune."
        )
        return result

    if current >= ceiling:
        result["reason"] = (
            f"Threshold is at the {ceiling}% ceiling. The remaining lines are "
            "defensive paths that need real hardware to exercise."
        )
        return result

    if measured - current < headroom:
        result["reason"] = (
            f"Measured {measured:.2f}% is only {measured - current:.2f} points "
            f"above the {current}% gate; {headroom} points of headroom are "
            "required before raising it."
        )
        return result

    proposed = min(current + step, ceiling, int(measured - headroom))
    if proposed <= current:
        result["reason"] = (
            f"A raise would leave less than {headroom} points of headroom."
        )
        return result

    result["proposed"] = proposed
    result["change"] = True
    result["reason"] = (
        f"Measured {measured:.2f}% has held {measured - current:.2f} points above "
        f"the {current}% gate. Raising to {proposed}% keeps at least {headroom} "
        f"points of headroom.\n\nThis needs {policy['min_observations']} "
        "consecutive nightly runs at this level before it is worth acting on - "
        "this tool sees one run, so treat a single proposal as a data point, "
        "not a decision."
    )
    return result


def render(result: dict, gate: dict, markdown: bool) -> str:
    """Format the outcome for a terminal or a job summary."""
    if not markdown:
        verdict = "PROPOSE" if result["change"] else "HOLD"
        return (
            f"[{verdict}] coverage gate\n"
            f"  measured: {result['measured']}%\n"
            f"  current:  {result['current']}%\n"
            f"  proposed: {result['proposed']}%\n"
            f"  {result['reason']}"
        )

    heading = (
        f"### Coverage gate: propose raising to {result['proposed']}%"
        if result["change"]
        else "### Coverage gate: no change proposed"
    )
    lines = [
        heading,
        "",
        f"- Measured: **{result['measured']}%**",
        f"- Current threshold: **{result['current']}%**",
        "",
        result["reason"],
    ]
    if result["change"]:
        lines += [
            "",
            f"To apply, set `{gate['variable']}: {result['proposed']}` in "
            f"`{gate['enforced_in']}`. Nothing here does that for you.",
        ]
    return "\n".join(lines)


def main() -> int:
    """Parse arguments, evaluate the policy, print the outcome."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--coverage", type=Path, default=Path("coverage.xml"), help="Cobertura XML"
    )
    parser.add_argument("--markdown", action="store_true", help="emit Markdown")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    if not POLICY.exists():
        die(f"{POLICY} not found")

    gate = json.loads(POLICY.read_text())["gates"]["line_coverage"]
    result = evaluate(measured_coverage(args.coverage), gate)

    print(
        json.dumps(result, indent=2)
        if args.json
        else render(result, gate, args.markdown)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
