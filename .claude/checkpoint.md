# Checkpoint

Where this repository stands right now. One screen, current state only — the
history is in [`session-summary.md`](session-summary.md).

**Updated:** 2026-09-05

## State

Master is green: `pytest` (415 tests), `ruff`, the 90% coverage gate, hassfest,
HACS validation, and the requirements sync check all pass.

Coverage: **96%** overall, `client.py` at **90%**. Gate is 90%.

## In flight

ACMM remediation, working the Hive-filed gap issues from L0 upward. L0 (#39) and
L2 (#41) are merged. L3 and L4 are the remaining ACMM work.

## Next

1. ACMM L4 — #26–#34.
2. #36 / #38 — re-measure what the e2e tier actually closed before deciding what
   is left; do not close them on assumption.
3. Widening the ruff rule set beyond the starts-green subset, as its own change.

## Before you touch anything

Read [`AGENTS.md`](../AGENTS.md). The three things that most often go wrong here:

- Credentials must never reach source, logs, fixtures, or a PR description.
- Payload parsing degrades, never raises — the protocol is undocumented.
- `feat:` and `fix:` bump the version users see in HACS. Tooling is not `feat:`.
