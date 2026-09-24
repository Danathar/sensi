# Checkpoint

Where this repository stands right now. One screen, current state only — the
history is in [`session-summary.md`](session-summary.md).

**Updated:** 2026-09-15

## State

Master is green: `pytest`, `ruff`, the coverage gate, hassfest, HACS
validation, and the requirements sync check all pass.

The gate is 93%. What is measured is defined by the committed `.coveragerc`,
which has carried `branch = true` since #86, so the measured number covers
branches as well as statements.

No test count and no measured coverage percentage are written down here. Both
move with almost every merge, and a figure cached in prose is wrong the day
after it is written. A `coverage-gate.yml` run is the live answer.

## In flight

Not listed here, for the same reason: open work changes faster than this file
does. The open issues and pull requests are the live answer.

The ACMM remediation this section used to track is finished — #39 (L0), #41
(L2), #44 (L3) and #45 (L4) are merged, and #46 closed the quality and security
findings that followed.

## Automation that is now live

- `labeler.yml` applies path, `tier/*` and `size/*` labels to every pull request.
- `nightly.yml` runs the whole gate daily, plus a leg against the *latest* Home
  Assistant as advance warning. A failure opens one self-closing issue.
- `ai-fix.yml` is **gone**: issue #110, removed by PR #118. Autonomous
  maintenance runs through Hive at ACMM L5 and through nothing else; there is
  no repository-local agent workflow to enable. See `docs/SECURITY-AI.md`,
  "One autonomous path, and it is Hive".

## Next

1. Widening the ruff rule set beyond the starts-green subset, as its own change.
2. Which module is weakest is a question for a fresh coverage run, not for a
   number cached in this file.

## Before you touch anything

Read [`AGENTS.md`](../AGENTS.md). The three things that most often go wrong here:

- Credentials must never reach source, logs, fixtures, or a PR description.
- Payload parsing degrades, never raises — the protocol is undocumented.
- `feat:` and `fix:` decide how a change reads in the generated release notes.
  The prefix no longer decides the version — CalVer is chosen when the release
  is cut. Tooling is not `feat:`.
