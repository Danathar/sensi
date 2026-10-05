# Operations runbook

What to watch, and what to do when something goes wrong, for whoever holds the
merge button on a repository that [Hive](https://github.com/hivecommons/hive)
agents work on. Each entry gives the signal first and the response after it.
The rules behind the responses are in [docs/SECURITY-AI.md](SECURITY-AI.md);
this page does not restate them.

## What to watch

| Signal | Where it shows up | Healthy |
|---|---|---|
| Checks on `master` | the CI, Coverage gate and Validate workflows | all six required checks green ([docs/branch-protection.md](branch-protection.md)) |
| Nightly compliance, 06:17 UTC | the Nightly compliance workflow | `pinned Home Assistant` green; no open `Nightly compliance failing` issue |
| Home Assistant advance warning | the `latest Home Assistant (advance warning)` job in the same run | green, or a failure already understood |
| Monthly release, 09:00 UTC on the 1st | the Release workflow | the run publishes, or skips because nothing user-visible changed; a red run is covered under [A release is refused](#a-release-is-refused) |
| Agent pull requests waiting | `gh pr list -R Danathar/sensi --label hold` | reviewed in batches; none left to go stale |

## Stopping the agents

**Signal:** agents are producing pull requests faster than they can be
reviewed, or one is repeating a mistake.

Pause this repository in Hive. Hive records the pause, and who paused it,
when and why where known, under `project.paused_repos` in its
configuration. A pause stops agent activity here and leaves open pull
requests where they are. Nothing in this repository has to change, because
nothing in it starts an agent.

## An agent pull request is wrong

**Signal:** a pull request from `danathar-atomic-hive` that should not merge.

Comment on what is wrong, or close it with the reason. It cannot merge
itself, so leave the `hold` label on until someone has reviewed it. The
footer at the end of the body names the agent that opened it
(`— hive: agent=<name>`), which tells you which lane the mistake came from.

If two agent pull requests make the same change, keep the better one. Close
the other with a comment linking what superseded it, as was done for #384
and #385.

## An agent pull request touches the security boundary

**Signal:** an agent's diff edits `.github/workflows/`, `.claude/settings.json`,
`.claude/hooks/`, `.claude/commands/`, `scripts/run_tests.py` or
`docs/SECURITY-AI.md`.

Close it. Those paths are outside what an automated change may touch
([docs/SECURITY-AI.md, "Never"](SECURITY-AI.md#never)). If the change is
right anyway, a person makes it in a pull request of their own.
`.github/CODEOWNERS` lists these paths in its control-plane section.

## The nightly run fails

**Signal:** an open issue titled `Nightly compliance failing`. The pinned leg
failed, which is the same configuration pull requests are gated on, so
something broke without a code change.

Open the linked run and compare the `Record resolved versions` step summary
with the last green run. A changed transitive dependency is the usual cause.
Fix it in a pull request. The issue closes itself when a later nightly is
green again.

A failure in `latest Home Assistant (advance warning)` alone opens no issue
and gates nothing. Treat it as notice that the next Home Assistant upgrade
needs work, and read the warnings summary in that job's log.

## A release is refused

**Signal:** the Release workflow fails at one of the steps below. Each one
stops before anything is tagged, so nothing has been published.

- **`Validate the version`**, with "Tag ... already exists": the manifest
  still carries the number of the last release. The scheduled run on the 1st
  does this whenever something under `custom_components/sensi` changed but
  the version-bump pull request was not merged first, as on 2026-10-01. Run
  the Release workflow with **prepare**, close and reopen the pull request it
  opens so its checks start, merge it, then dispatch the release again. The
  step's other refusals (a number that is not CalVer, is not newer than the
  latest tag, or disagrees with the pre-release box) mean the bump pull
  request carried the wrong number: run **prepare** again with the right one.
- **`Decide the version`**: the manifest's version is not CalVer, or the
  version typed into a manual run is not the one the manifest carries. The
  workflow releases what the manifest says; to release a different number,
  run **prepare** for it and merge that first.
- **`Require green checks on this commit`**: the commit on `master` has a
  check that is not green. Fix or rerun that check on `master`, then dispatch
  the release again. Do not work around the step: it is what keeps a commit
  with a red check from being published.

[CONTRIBUTING.md, "Releases"](../CONTRIBUTING.md#releases) has the full two-step
procedure.

## `master` may no longer be protected

**Signal:** a ruleset change in the repository settings, or any doubt.

Run `python3 scripts/check_ruleset.py` (it needs admin on the repository). It
compares the live ruleset with `.github/rulesets/master.json` and fails on
any weakening. `--offline` checks only the committed definition.

## A credential is exposed

Follow [docs/SECURITY-AI.md, "If a credential is exposed"](SECURITY-AI.md#if-a-credential-is-exposed):
rotate first, investigate second.
