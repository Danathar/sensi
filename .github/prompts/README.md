# Prompt catalog

Reusable prompts for the recurring work on this repository. They are written to
be pasted into any assistant — nothing here depends on a particular tool.

| Prompt | Use it when |
| --- | --- |
| [`triage-issue.md`](triage-issue.md) | a user has reported a problem with logs attached |
| [`protocol-change.md`](protocol-change.md) | Sensi changed something and the integration broke |
| [`upgrade-home-assistant.md`](upgrade-home-assistant.md) | bumping the pinned Home Assistant / test harness |
| [`release-readiness.md`](release-readiness.md) | before triggering the release workflow |

Claude Code users have the same workflows as slash commands under
`.claude/commands/`. The rules that apply to *any* change here live in
`AGENTS.md`; these prompts assume it has been read.

## Adding one

Add a prompt when a task has been done twice and getting it right depended on
knowing something not in the code — a protocol quirk, an ordering constraint, a
place the obvious approach fails. A prompt that only restates `AGENTS.md` is
noise. Keep them specific to this integration.
