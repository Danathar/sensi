# Checkpoint

Where this repository stands right now. One screen, current state only — the
history is in [`session-summary.md`](session-summary.md).

**Updated:** 2026-09-05

## State

Master is green: `pytest` (464 tests), `ruff`, the 93% coverage gate, hassfest,
HACS validation, and the requirements sync check all pass.

Coverage: **98%** overall, `client.py` at **100%**. Gate is 93%, and what is
measured is defined by the committed `.coveragerc`.

## In flight

Nothing. The ACMM remediation is complete - #39 (L0), #41 (L2), #44 (L3) and
#45 (L4) are merged, and #46 closed the quality and security findings that
followed. Every issue that was open at the start of this work is closed.

## Automation that is now live

- `labeler.yml` applies path, `tier/*` and `size/*` labels to every pull request.
- `nightly.yml` runs the whole gate daily, plus a leg against the *latest* Home
  Assistant as advance warning. A failure opens one self-closing issue.
- `ai-fix.yml` is **inert**. It needs both an `ANTHROPIC_API_KEY` secret and an
  `AI_FIX_ENABLED` repository variable set to `true`. Read `docs/SECURITY-AI.md`
  before enabling it — the second switch exists because `ai-fix-requested` is
  applied automatically by the issue-filing bot.

## Next

1. Widening the ruff rule set beyond the starts-green subset, as its own change.
2. `climate.py` (92%) and `sensor.py` (94%) are now the weakest modules.

## Before you touch anything

Read [`AGENTS.md`](../AGENTS.md). The three things that most often go wrong here:

- Credentials must never reach source, logs, fixtures, or a PR description.
- Payload parsing degrades, never raises — the protocol is undocumented.
- `feat:` and `fix:` bump the version users see in HACS. Tooling is not `feat:`.
