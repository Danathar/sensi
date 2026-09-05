# Contributing

> Working on this repository with a coding assistant? [AGENTS.md](AGENTS.md) is
> the machine-readable version of this guide, and is what `CLAUDE.md`,
> `.github/copilot-instructions.md` and `.cursor/rules/` all point at.

This is a [Home Assistant](https://www.home-assistant.io/) custom integration for
[Sensi](https://sensi.emerson.com/en-us) thermostats. It talks to the same
socket.io endpoint the Sensi mobile app uses, which was reverse engineered - so
the protocol is undocumented and can change without warning. That shapes most of
the conventions below.

## Getting set up

The repository ships a devcontainer (`.devcontainer/`) built on
`ghcr.io/iprak/custom-integration-image`, which is the quickest path: open the
folder in VS Code, reopen in the container, and everything is installed.

To work outside the container:

```bash
python3 -m venv .venv           # Home Assistant requires Python >= 3.14.2
source .venv/bin/activate
pip install -r requirements_test.txt
```

`requirements_test.txt` pins `pytest-homeassistant-custom-component`, which in
turn pins the Home Assistant release the suite runs against. That pin is what
decides the minimum Python version - bump it deliberately, in its own commit,
not as a side effect of another change.

## Running the checks

```bash
pytest                                    # the whole suite
pytest tests/e2e                          # just the end-to-end tests
pytest --cov=custom_components.sensi --cov-report=term-missing

ruff check .                              # lint
ruff format .                             # format

python3 scripts/check_requirements_sync.py  # manifest vs requirements_component.txt
```

CI runs the same things:

| Workflow | What it enforces |
| --- | --- |
| `.github/workflows/ci.yml` | the pytest suite |
| `.github/workflows/coverage-gate.yml` | line coverage stays at or above the threshold |
| `.github/workflows/validate.yml` | `ruff`, hassfest, HACS, and requirements sync |
| `.github/workflows/labeler.yml` | applies path, risk-tier and size labels |
| `.github/workflows/nightly.yml` | the whole gate nightly, plus a run against the *latest* Home Assistant as advance warning |

## Layout

```
custom_components/sensi/
  auth.py          token refresh and credential storage
  client.py        the socket.io client - connection, event queue, setters
  coordinator.py   DataUpdateCoordinator wrapper around the client
  data.py          SensiDevice / State - parsing of raw payloads
  capabilities.py  what a given thermostat model supports
  entity.py        shared base entity
  climate.py binary_sensor.py number.py sensor.py switch.py   platforms
tests/             unit tests, one module per source module
tests/e2e/         end-to-end tests against a scripted fake socket.io backend
```

## Conventions

**Follow Home Assistant core style.** Docstrings on every module, class, and
function; `async` everywhere in the entity and client layers; no blocking I/O on
the event loop. `ruff.toml` encodes the parts of this that are mechanical.

**Never log or commit credentials.** `auth.py` has a `redact_token` helper -
use it. Access tokens, refresh tokens, and `icd_id` values from a real account
do not belong in log samples, test fixtures, or commit messages.

**Sample payloads live in `tests/`.** `sample.json` and
`sample_with_humidification.json` are captured (and scrubbed) responses. When a
change is driven by a payload the current fixtures do not cover, add a fixture
rather than hand-building a dict in the test.

**Parsing is defensive by default.** Fields the app sends today may be absent
tomorrow. `data.py` and `capabilities.py` use `.get()` with sensible defaults and
the `to_bool` / `to_int` / `to_float` helpers in `utils.py`. Prefer degrading to
"unknown" over raising.

**Keep `manifest.json` and `requirements_component.txt` in sync.** CI fails if
they drift; `scripts/check_requirements_sync.py` is the check.

## Tests

Every source module has a matching `tests/test_<module>.py`. New behaviour needs
a test, and a bug fix needs a test that fails before the fix.

`tests/e2e/` is different in kind: it patches `socketio.AsyncClient` with a
scripted fake backend and drives the integration the way Home Assistant does -
config flow, entry setup, entity states, service calls, coordinator refresh. Use
it when the thing you want to prove spans more than one module. See
`tests/e2e/conftest.py` for the fake backend and how to script responses.

Coverage is gated in CI. The threshold lives in `.github/workflows/coverage-gate.yml`;
raise it when the suite improves, and do not lower it to make a PR pass.

## Commits and pull requests

Commit subjects use [Conventional Commits](https://www.conventionalcommits.org/)
(`fix:`, `feat:`, `ci:`, `docs:`, `test:`, `refactor:`). The prefix no longer
selects a version number - see [Releases](#releases) - but release notes are
generated from the merged pull requests, so it decides how the change reads to
a user. `feat:` and `fix:` are the two that describe something they can
observe; everything else is maintenance.

Fill in the pull request template. The two sections reviewers rely on most are
*How it was verified* and *Risk*: say whether you exercised the change against a
real thermostat, and say plainly if it touches the config flow, stored
credentials, or entity unique IDs, since those break existing installs.

## Releases

This fork publishes **CalVer**: `YYYY.M.PATCH`, no `v` prefix - `2026.9.0`,
`2026.9.1`, `2026.10.0`. Pre-releases add a `b1` or `rc1` suffix.

It deliberately does not continue upstream's `v2.x` semver line. Upstream keeps
releasing from a different tree, and a shared numbering would mean two
different releases called `v2.1.7`; "I'm on 2.1.7" would stop identifying which
code someone is running. A year-based line cannot collide, the differing tag
shape (`2026.9.0` against upstream's `v2.1.6`) makes the two obvious at a
glance, and Home Assistant itself uses CalVer. Because `2026.x` compares as
newer than `2.1.x`, an existing install upgrades cleanly rather than stranding.

To cut one, run the **Release** workflow with the version. It validates before
it writes anything - shape, that the tag is free, that the number is newer than
the latest stable tag, and that a `b1`/`rc1` suffix agrees with the pre-release
checkbox - then sets `manifest.json`, commits, tags, and publishes with notes
generated from the merged pull requests. Use **dry_run** to check a version
without publishing. Do not tag by hand: the manifest version and the tag have
to move together, and the workflow is what guarantees that.

The attached `sensi.zip` is for people installing by hand. HACS does not use
it - that would need `zip_release` and `filename` in `hacs.json`, both
deliberately unset - so HACS installs `custom_components/sensi/` from the tag
and there is one fewer thing to get wrong.

### Syncing with upstream

Land an upstream sync as a **single merge commit** titled
`chore(upstream): sync iprak/sensi vX.Y.Z`. Release notes are generated from
merged pull requests, so a sync that arrives as thirty individual commits lists
iprak's work as though it were this fork's. One merge, one line, credit where
it belongs.

## Further reading

- [`docs/review-rubric.md`](docs/review-rubric.md) — what a review checks, in
  priority order, and what it should not spend itself on
- [`docs/quality.md`](docs/quality.md) — what each CI gate proves, and what is
  deliberately not covered
- [`docs/metrics.md`](docs/metrics.md) — change acceptance, and how to read it
  honestly on a single-maintainer repository
- [`docs/risk-tiers.md`](docs/risk-tiers.md) — what the automatic `tier/*` label
  on your pull request means, and what each tier asks of you
- [`docs/SECURITY-AI.md`](docs/SECURITY-AI.md) — what automated agents are
  permitted to do here, and the two switches that gate the AI-fix workflow

## Reporting a problem

Open an issue with the Home Assistant version, the integration version from
`manifest.json`, your thermostat model, and the relevant `custom_components.sensi`
log lines at debug level:

```yaml
logger:
  default: warning
  logs:
    custom_components.sensi: debug
```

Scrub tokens and `icd_id` values before pasting.
