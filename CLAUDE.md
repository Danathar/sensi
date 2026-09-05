# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first.** It is the canonical instruction file for
this repository — what the project is, the rules that are not obvious from the
code, the layout, and how to test. Everything there applies here; this file only
adds the parts specific to working through Claude Code.

## The short version

Home Assistant custom integration for Sensi thermostats. Python 3.14+, all
shipped code in `custom_components/sensi/`. The Sensi socket.io protocol is
reverse engineered and undocumented, so parse defensively and never invent a
payload field you cannot find in `tests/sample.json`.

Before proposing any change:

```bash
pytest && ruff check . && ruff format --check .
```

Never log, commit, or print a token or a real `icd_id`. Use `redact_token` from
`auth.py`.

## Slash commands

`.claude/commands/` holds the repeatable workflows for this repository:

| Command | Use it for |
| --- | --- |
| `/check` | run the full local gate — pytest, ruff, requirements sync |
| `/add-entity` | add a new sensor/switch/number to an existing platform |
| `/capture-payload` | turn a real thermostat payload into a scrubbed test fixture |
| `/cover` | find the weakest module and raise its coverage |

## Project settings and the format hook

`.claude/settings.json` is checked in. It does two things:

- **Permissions.** Read-only inspection, `pytest` and `ruff` run without asking.
  Anything that leaves the machine or changes shared state — `git push`,
  `gh pr create`, `gh pr merge`, `gh release`, editing a workflow or
  `manifest.json` — asks first. Writing `pyproject.toml` and reading
  `secrets.yaml` / `.env` / `config/` are denied outright.
- **A PostToolUse hook** (`.claude/hooks/format-edited-python.sh`) that sorts
  imports and runs `ruff format` on any `.py` file after an edit, so a change
  never reaches CI failing `ruff format --check` for a reason nobody thought
  about. It never fails the tool call, and it never deletes "unused" imports —
  that is a semantic change and belongs in an explicit `/check`, not a silent
  hook.

## Session state

[`.claude/checkpoint.md`](.claude/checkpoint.md) is where the repository stands
right now; [`.claude/session-summary.md`](.claude/session-summary.md) is the
history behind it, including decisions that should not be re-litigated. Read the
checkpoint before starting, and add to the summary when you finish something
substantial.

## Corrections and memory

`.claude/memory/` holds corrections that came out of real review feedback —
things a reasonable agent gets wrong on this codebase without being told. Read
it before a non-trivial change, and add to it when a human corrects you on
something that will recur. One file per correction; see
`.claude/memory/README.md` for the format.

## Things worth knowing before you edit

**`pyproject.toml` is gitignored on purpose.** It comes from the devcontainer
image. Do not create one — ruff config goes in `ruff.toml`, pytest config in
`pytest.ini`.

**Coverage is gated at 90%.** `pytest --cov=custom_components.sensi` tells you
where you stand. `tests/e2e/` is usually the cheapest way to move it, because it
covers the connect handshake and emit loop that unit tests cannot reach.

**Commit prefixes decide the released version.** `feat:` and `fix:` bump it.
Tooling, CI, docs and agent instructions are `ci:` / `docs:` / `chore:`, never
`feat:`.

**Do not edit `manifest.json` `version`.** The release workflow owns it.
