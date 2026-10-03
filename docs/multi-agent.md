# How the agents on this repository are coordinated

Several AI agents work on this repository, and none of them is coordinated by
anything in it. There is no dispatcher workflow, no orchestrator script and no
agent queue here. Orchestration is
[Hive](https://github.com/hivecommons/hive)'s job, and this page explains
how its work shows up on GitHub, and what keeps parallel agents from
undermining each other.

## Why the orchestrator is not in this repository

Hive is the only autonomous path into this repository. The reasoning is in
[docs/SECURITY-AI.md, "One autonomous path, and it is Hive"](SECURITY-AI.md#one-autonomous-path-and-it-is-hive):
two autonomous writers make two trust boundaries. An in-repository dispatcher
that started agents would be a second writer, and `.github/workflows/` is
outside what an automated change may touch anyway.

Hive's governor starts each agent on its own schedule. The roster and what
each agent is allowed to do at ACMM L5 are described in the README, under
[Maintained with Hive (ACMM L5)](../README.md#maintained-with-hive-acmm-l5).

## Recognising which agent did what

Everything Hive opens is authored by the `danathar-atomic-hive` GitHub App
and signed with a footer naming the agent:

```text
— hive: agent=quality backend=claude model=… effort=…
```

Most footers also name the backend and model; the ACMM evaluation's issues
(`agent=dashboard`) name only the agent. One early pull request, #179, has no
footer. The footer is the reliable signal. Branch prefixes are a convention
the agents follow, not something enforced:

| Agent | Branch prefixes seen here |
|---|---|
| `quality` | `quality/`, occasionally `test/` |
| `sec-check` | `sec/` |
| `scanner` | `scanner/` |
| `guide` | `guide/` |
| `architect` | `architect/`, `arch/`, `fix/` |

Issues also come from `ci-maintainer`, `strategist` and `dashboard`. The
`dashboard` lane files the ACMM evaluation's gap issues. `fix/`, `docs/`,
`sec/` and other prefixes are used by human-authored pull requests too, so
always check the author before reading a branch name as an agent's.

## How parallel work is kept apart

- **One issue, one pull request.** An agent's pull request names the issue it
  closes (`Closes #N`), so its scope is the issue's scope.
- **Every pull request goes through the same gates.** The required checks and
  the `protect master` ruleset apply to an agent's pull request exactly as to
  a person's ([docs/branch-protection.md](branch-protection.md)), and every
  agent is told to read [AGENTS.md](../AGENTS.md) first.
- **Nothing merges itself.** At ACMM L5, agent pull requests are hold-gated
  and a human merges. A human merge is therefore the point where overlapping
  work gets reconciled.

Overlap still happens. #384 (`architect`) and #385 (`scanner`) made the same
fix to `SensiClient.async_update_devices`. #385 merged, and #384 was closed
as superseded with a comment naming the merge commit. When two agent pull
requests cover the same change, keep the better one, then close the other
with a comment that links what replaced it.

## Related

- [docs/SECURITY-AI.md](SECURITY-AI.md): what agents may and may not touch
- [docs/risk-tiers.md](risk-tiers.md): the review standard each path carries
- [docs/review-rubric.md](review-rubric.md): how a pull request is reviewed
