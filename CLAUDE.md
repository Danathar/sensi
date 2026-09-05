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
