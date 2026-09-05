# AGENTS.md

Guidance for coding agents working in this repository. Humans should read
[CONTRIBUTING.md](CONTRIBUTING.md); this file is the same ground truth written
for an agent that has to act without asking.

This is the canonical instruction file. `CLAUDE.md`,
`.github/copilot-instructions.md` and `.cursor/rules/` all point here — change
this file, and re-check those if the change affects a rule they restate.

## What this repository is

A [Home Assistant](https://www.home-assistant.io/) custom integration for
[Sensi](https://sensi.emerson.com/en-us) thermostats, distributed through HACS.
Domain: `sensi`. All shipped code lives in `custom_components/sensi/`.

It talks to `rt.sensiapi.io` over socket.io using the protocol the Sensi mobile
app uses. **That protocol is reverse engineered and undocumented.** Emerson can
change it without notice, and there is no spec to check against. Almost every
convention below follows from that one fact.

## Commands

```bash
pip install -r requirements_test.txt   # Python >= 3.14.2

pytest                                 # whole suite (~11s)
pytest tests/e2e                       # end-to-end only
pytest --cov=custom_components.sensi --cov-report=term-missing

ruff check .                           # lint  (config: ruff.toml)
ruff format .                          # format

python3 scripts/check_requirements_sync.py
```

Run `pytest` and both `ruff` commands before proposing a change. CI runs
exactly these, plus Home Assistant's `hassfest` and HACS validation.

## Rules

**Never log, commit, or print a credential.** Access tokens, refresh tokens,
and real `icd_id` values must not appear in source, log samples, test fixtures,
commit messages, or PR descriptions. `auth.py` exports `redact_token` — use it
for anything token-shaped. This has already been a real incident in this
repository's history; treat it as the highest-priority rule here.

**Parse defensively.** Fields the app sends today may be missing tomorrow, and a
`KeyError` deep in a payload parser takes the whole integration down. Use
`.get()` with a sensible default and the `to_bool` / `to_int` / `to_float`
helpers in `utils.py`. Degrade to "unknown" rather than raising. `data.py` and
`capabilities.py` are the models for this.

**Do not add `pyproject.toml`.** It is gitignored on purpose — the devcontainer
image supplies one. Ruff config belongs in `ruff.toml`, pytest config in
`pytest.ini`.

**Keep `manifest.json` `requirements` in sync with `requirements_component.txt`.**
`scripts/check_requirements_sync.py` enforces this and CI fails on drift.

**Do not bump `manifest.json` `version` by hand.** The release workflow derives
it from commit messages.

**`strings.json` cannot contain literal URLs.** hassfest rejects them. Pass them
through `description_placeholders` instead — `SENSI_LOGIN_URL` in `const.py` is
the existing example.

**Changing an entity `unique_id`, the config flow, or the stored credential
shape is breaking** for existing installs. Do not do it incidentally; if a task
requires it, say so explicitly in the PR.

**This repository is a fork of `iprak/sensi`.** `manifest.json` `documentation`,
`issue_tracker` and `codeowners` point at *this* repository, not upstream. Home
Assistant sends users to `issue_tracker` when a custom integration raises, so
pointing it upstream files this fork's bugs on a maintainer who did not publish
this code. Credit for the original work belongs in `README.md` and `LICENSE`,
which is where it is - `codeowners` is a "who is responsible" field, not a
credit field. Do not point any of the three back at upstream.

## Layout

```
custom_components/sensi/
  __init__.py      async_setup_entry / async_unload_entry, config option helpers
  auth.py          token refresh, credential storage, redact_token
  client.py        socket.io client - connect, event queue, emit loop, setters
  coordinator.py   DataUpdateCoordinator wrapper (30s interval)
  data.py          SensiDevice / State - raw payload parsing
  capabilities.py  what a given thermostat model supports
  entity.py        shared base entity
  const.py         constants; LOGGER lives here
  event.py         dataclasses for the socket.io event payloads
  utils.py         to_bool / to_int / to_float / bool_to_onoff
  climate.py binary_sensor.py number.py sensor.py switch.py   platforms

tests/             one test module per source module
tests/e2e/         end-to-end tests against a scripted fake socket.io backend
tests/sample.json  captured (scrubbed) device payload
scripts/           repository checks
```

## Testing

Every source module has a matching `tests/test_<module>.py`. New behaviour needs
a test; a bug fix needs a test that fails before the fix.

`tests/e2e/` is different in kind. It patches `socketio.AsyncClient` with
`FakeSensiBackend` — a scripted stand-in for the Sensi server — and drives the
integration the way Home Assistant does: config flow, entry setup, platform
forwarding, service calls, coordinator refresh, unload. Reach for it when the
thing you want to prove spans more than one module, or when the assertion you
care about is "what actually went out on the wire".

Two things about that fake that are easy to get wrong:

- It implements `shutdown()` as well as `disconnect()`. `SensiClient._async_disconnect`
  calls `shutdown()` inside `contextlib.suppress(Exception)`, so a fake missing
  it looks like a clean teardown while doing nothing at all.
- The fixture sets `hass.config.units = US_CUSTOMARY_SYSTEM`, because the
  captured payloads report `display_scale: "f"`. Without it, temperature
  assertions are really assertions about Home Assistant's F-to-C rounding.

Coverage is gated at 93% by `.github/workflows/coverage-gate.yml`. Do not lower
the threshold to make a change pass.

Prefer adding a payload fixture under `tests/` over hand-building a dict inline
when the change is driven by a shape the existing fixtures do not cover. Scrub
it first.

## Commits and pull requests

[Conventional Commits](https://www.conventionalcommits.org/). The prefix decides
the released version, so choose it on that basis:

| Prefix | Effect |
| --- | --- |
| `feat:` | minor version bump — user-visible integration capability only |
| `fix:` | patch bump — user-visible bug fix only |
| `ci:` `docs:` `test:` `refactor:` `chore:` | no version bump |

Tooling, CI, lint config, and agent instruction files are **not** `feat:`.

Fill in `.github/pull_request_template.md`. The *How it was verified* and *Risk*
sections are the ones reviewers act on.

## Reference

| Document | What it is for |
| --- | --- |
| [`docs/review-rubric.md`](docs/review-rubric.md) | what a review checks, in priority order |
| [`docs/quality.md`](docs/quality.md) | what each CI gate proves, and what is not covered at all |
| [`docs/metrics.md`](docs/metrics.md) | change acceptance, and how to read it here |
| [`docs/risk-tiers.md`](docs/risk-tiers.md) | what the automatic `tier/*` label means and what it requires of you |
| [`docs/SECURITY-AI.md`](docs/SECURITY-AI.md) | what an agent may and may not do here - read this before acting autonomously |
| [`docs/reflections/`](docs/reflections/) | knowledge that outlived the change that produced it |
| [`.claude/checkpoint.md`](.claude/checkpoint.md) | where the repository stands right now |
| [`.claude/session-summary.md`](.claude/session-summary.md) | decisions already made, so they are not re-litigated |
| [`.claude/memory/`](.claude/memory/) | corrections that came out of real mistakes on this codebase |
| [`.github/prompts/`](.github/prompts/) | reusable prompts for triage, protocol changes, upgrades, review, release |

`.claude/settings.json` is checked in: it allows read-only inspection and the
test/lint commands without asking, asks before anything that pushes or
publishes, and runs a PostToolUse hook that sorts imports and formats any
edited `.py` file.

## When you are unsure

- A payload field you cannot find in `tests/sample.json` — say so rather than
  guessing its type. There is no schema to check against.
- Anything that would require a real thermostat to verify — implement it, and
  state plainly in the PR that it is unverified against hardware.
- A change that touches `auth.py` token handling — flag it for human review.
