# Quality signals

Where the evidence about this integration's health lives, what each signal
actually proves, and — more importantly — what it does not.

## The gates

Every pull request runs six checks. All are required.

| Check | Workflow | Proves | Does not prove |
| --- | --- | --- | --- |
| `pytest (Python 3.14)` | `ci.yml` | the suite passes on the pinned Home Assistant | anything about real hardware |
| `line coverage >= threshold` | `coverage-gate.yml` | ≥93% of `custom_components/sensi` lines execute | that the executed lines are *asserted* on |
| `ruff` | `validate.yml` | lint and format are clean | correctness |
| `hassfest` | `validate.yml` | the manifest, strings and translations satisfy Home Assistant core | runtime behaviour |
| `HACS` | `validate.yml` | repository metadata is installable through HACS | anything about the code |
| `manifest requirements match` | `validate.yml` | `manifest.json` and `requirements_component.txt` agree | that the pinned version works |

## Coverage

```bash
pytest --cov=custom_components.sensi --cov-report=term-missing
```

The gate is 93%, set in `MIN_COVERAGE` in `.github/workflows/coverage-gate.yml`,
and the measured number is written to the job summary of every run. What is
measured is defined by `.coveragerc`, which is committed so a local run and a CI
run are the same measurement.

Two tiers feed it:

- **`tests/`** — one module per source module, hermetic, fast. Covers parsing,
  capabilities, entity behaviour, and the config flow.
- **`tests/e2e/`** — the whole integration against `FakeSensiBackend`, a
  scripted stand-in for the Sensi socket.io server. This is the only tier that
  reaches the connect handshake, the emit loop, the reconnect path, and the
  coordinator refresh.

The e2e tier exists because those paths are exactly what unit tests are worst at.
Before it, `client.py` — the module with the most runtime risk — sat at 52% line
coverage while the repository read 85%.

| | before `tests/e2e/` | now |
| --- | --- | --- |
| `client.py` | 52% | **100%** |
| repository total | 85% | 98% |

**What the number does not mean.** Line coverage counts executed lines, not
asserted behaviour. A test that calls a function and asserts nothing raises
coverage and protects nothing. When raising coverage, target by risk — see
`.claude/commands/cover.md` — and never lower the threshold to make a change
pass.

## What is deliberately not covered

Stating these plainly is part of the signal.

- **Real hardware.** There is no thermostat in CI and cannot be. Anything whose
  correctness depends on how a physical unit responds is unverified until a
  human exercises it; the pull request template asks for that explicitly.
- **The protocol itself.** `rt.sensiapi.io` is reverse engineered and
  undocumented. The fixtures under `tests/` are captured payloads, so the suite
  proves the code handles *what Sensi sent on the day it was captured*. A
  protocol change is invisible to CI until a user reports it — which is why the
  parsing layer degrades rather than raising.
- **Upgrade paths.** Config entry migration across integration versions has no
  automated coverage.
- **Performance.** No benchmarks; the integration polls every 30 seconds and
  that has never been the constraint.

## Nightly

`.github/workflows/nightly.yml` runs the whole gate once a day, and separately
runs the suite against the **latest** Home Assistant rather than the pinned one.
Home Assistant ships monthly and deprecates aggressively; that second leg is how
an upcoming break arrives as a nightly notice instead of as a user's broken
installation. It is informational and never gates a pull request.

A failure of the *pinned* leg means something broke without a code change — a
transitive dependency, a yanked release. It opens a single issue that later runs
reuse and that closes itself when the run goes green again.

The same job runs `scripts/auto_qa_tuner.py`, which reads the measured coverage
against the policy in `.github/auto-qa-tuning.json` and reports whether the
threshold could be raised. It only ever proposes: a gate that raises itself
fails on a change nobody connected to it, and the person who has to understand
that failure was not in the loop when it moved.

## Trend

Every push to `master` publishes the measured number to the `coverage-data`
branch, which holds two files and no code:

| File | What it is |
| --- | --- |
| [`coverage-unit.json`](https://github.com/Danathar/sensi/blob/coverage-data/coverage-unit.json) | the shields.io payload behind the **Unit coverage** badge |
| [`coverage-trend.csv`](https://raw.githubusercontent.com/Danathar/sensi/coverage-data/coverage-trend.csv) | one row per push — `date,sha,percent` |

The badge is the snapshot; the CSV is the history, which the per-run artifact
cannot be because it expires with the run. `scripts/coverage_badge.py` writes
both, and the `publish` job in `coverage-gate.yml` pushes them. It runs on push
to `master` only and depends on the gate having passed, so a pull request
cannot move the published number and a failed run cannot overwrite a good
number with a bad one.

The badge is not the gate. Its colour uses the same `MIN_COVERAGE`, so it goes
yellow at exactly the point the gate would fail, but the committed floor in
`coverage-gate.yml` is still what actually blocks a merge — a ratchet, not a
graph. Raise it when the suite genuinely improves.

Because publishing only happens on a green master run, a red **Coverage gate**
badge next to a healthy-looking **Unit coverage** number means the number is
stale, not that coverage is fine. That is why both badges are there.

Change acceptance is tracked separately in [`docs/metrics.md`](metrics.md).

## Reproducing all of it locally

```bash
ruff format --check .
ruff check .
python3 scripts/check_requirements_sync.py
pytest --cov=custom_components.sensi --cov-report=term-missing
```

`hassfest` and HACS validation cannot be run locally; they need the workflow.
