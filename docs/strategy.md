# Strategy

Where this repository is going, what it is deliberately not doing, and where to
look to tell whether it is on course. This page records decisions. Current
numbers live in the places linked under [Where to look](#where-to-look).

## Goal: a stable integration

The integration is in beta. The conditions for calling it stable are listed in
the README under [Project status](../README.md#project-status), and they are
not repeated here, so they can only change in one place. Most of them need
real thermostats or real Home Assistant installs, which CI cannot provide.

## What gets attention first

Work is ranked by what it can cost a user. The first two come from
[docs/SECURITY-AI.md, "What makes this repository sensitive"](SECURITY-AI.md#what-makes-this-repository-sensitive):

1. **Credentials.** Nothing may leak a refresh token or a device identifier.
2. **Thermostat control.** A wrong mode or setpoint reaches physical
   equipment, possibly in an empty building.
3. **Staying loadable.** A Home Assistant release must not break the
   integration silently. The nightly run against the latest Home Assistant is
   the early warning.
4. **Everything else:** documentation, tooling, refactoring.

## How it is maintained: ACMM L6, on purpose

[Hive](https://github.com/hivecommons/hive) agents file issues and open pull
requests, and merge them once the required checks pass; outreach pull
requests stay held for a person
([Maintained with Hive (ACMM L6)](../README.md#maintained-with-hive-acmm-l6)).
That split is deliberate. The repository controls physical equipment, and
the protocol behind it is undocumented. That is why the required checks
and the `protect master` ruleset apply to agent pull requests exactly as to
a person's.

The ACMM evaluation opened eight L6 issues (#418 to #425). Their outcomes
follow that boundary:

- **Documented**, because the capability is real and only lacked a page:
  multi-agent orchestration (#419), this strategy page (#420), the operations
  runbook (#423) and task traceability (#424).
- **Waived to Hive** in `.acmm.yml`, because Hive already provides the
  capability outside this repository: issue generation (#418) and the audit
  trail (#425).
- **Waived to an existing file** in `.acmm.yml`: the risk-assessment config
  (#422). The rules already live at `.github/policies/risk-tiers.yml`, and a
  copy under the criterion's file names would drift.
- **Not planned:** the merge queue (#421). Every accepted file would have
  been either a merging workflow or a config GitHub does not read. The issue
  explains why.

A waiver cannot advance an ACMM level. The L6 file check is therefore not
expected to pass. Moving Hive itself to L6, where agents may merge, would
first need [docs/SECURITY-AI.md](SECURITY-AI.md) to change, because it states
that no agent merges its own work. That is a maintainer decision, made on
purpose, not something a criterion should drift into.

## Where to look

| Question | Where |
|---|---|
| Are agent pull requests being accepted, and how fast? | [docs/metrics/README.md](metrics/README.md) |
| What do the tests and coverage actually prove? | [docs/quality.md](quality.md) |
| Is `master` still protected the way the docs say? | [docs/branch-protection.md](branch-protection.md) |
| Which paths need the most careful review? | [docs/risk-tiers.md](risk-tiers.md) |
| What has a piece of work taught about this codebase? | [docs/reflections/](reflections/README.md) |
