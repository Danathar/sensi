# Checkpoint

Where this repository stands right now. One screen, current state only — the
history is in [`session-summary.md`](session-summary.md).

**Updated:** 2026-09-05

## State

Master is green: `pytest` (415 tests), `ruff`, the 90% coverage gate, hassfest,
HACS validation, and the requirements sync check all pass.

Coverage: **96%** overall, `client.py` at **90%**. Gate is 90%.

## In flight

ACMM remediation, working the Hive-filed gap issues from L0 upward. L0 (#39),
L2 (#41) and L3 (#44) are merged; L4 is the change this checkpoint ships with.

## Automation that is now live

- `labeler.yml` applies path, `tier/*` and `size/*` labels to every pull request.
- `nightly.yml` runs the whole gate daily, plus a leg against the *latest* Home
  Assistant as advance warning. A failure opens one self-closing issue.
- `ai-fix.yml` is **inert**. It needs both an `ANTHROPIC_API_KEY` secret and an
  `AI_FIX_ENABLED` repository variable set to `true`. Read `docs/SECURITY-AI.md`
  before enabling it — the second switch exists because `ai-fix-requested` is
  applied automatically by the issue-filing bot.

## Next

1. #36 / #38 — re-measure what the e2e tier actually closed before deciding what
   is left; do not close them on assumption.
2. Widening the ruff rule set beyond the starts-green subset, as its own change.

## Before you touch anything

Read [`AGENTS.md`](../AGENTS.md). The three things that most often go wrong here:

- Credentials must never reach source, logs, fixtures, or a PR description.
- Payload parsing degrades, never raises — the protocol is undocumented.
- `feat:` and `fix:` bump the version users see in HACS. Tooling is not `feat:`.
