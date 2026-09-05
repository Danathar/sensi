[![CI](https://github.com/Danathar/sensi/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/Danathar/sensi/actions/workflows/ci.yml)
[![Validate](https://github.com/Danathar/sensi/actions/workflows/validate.yml/badge.svg?branch=master)](https://github.com/Danathar/sensi/actions/workflows/validate.yml)
[![Coverage gate](https://github.com/Danathar/sensi/actions/workflows/coverage-gate.yml/badge.svg?branch=master)](docs/quality.md#coverage)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Danathar/sensi)
[![Maintenance assisted by Hivecommons Hive](https://img.shields.io/badge/maintenance%20assisted%20by-Hivecommons%20Hive-1f6feb)](https://github.com/hivecommons/hive)
[![ACMM L4 Security-Aware](https://img.shields.io/badge/ACMM-L4%20Security--Aware-2da44e)](https://github.com/hivecommons/hive#acmm-levels)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

# Sensi thermostat for Home Assistant

A Home Assistant custom integration for [Sensi](https://sensi.emerson.com/en-us) smart thermostats. It reads temperature, humidity and thermostat state, and lets you control operating mode, setpoints, fan and the thermostat's own configuration switches from Home Assistant.

There is no public Sensi API. The integration talks to the same backend the mobile app does, worked out by reverse engineering the app and building on earlier work in [`w1ll1am23/pysensi`](https://github.com/w1ll1am23/pysensi). Emerson can change that backend at any time, so treat this as something that can break without warning.

> [!NOTE]
> **This is a fork of [`iprak/sensi`](https://github.com/iprak/sensi).** See [About this fork](#about-this-fork) for what differs, [How this repository is maintained](#how-this-repository-is-maintained) for the agent fleet that reviews it, and [Thanks](#thanks) for credit where it belongs.

## Requirements

- A Sensi thermostat already set up in the Sensi mobile app (the app account is what this authenticates against).
- Home Assistant with `custom_components` support — the integration is installed there, not from the built-in integration list.
- A **refresh token** obtained by hand. Sensi app v8.6.3+ put reCaptcha in front of login, which cannot be replicated programmatically, so username/password sign-in is not an option. See [Getting a refresh token](#getting-a-refresh-token).

## Installation

### HACS

This fork is not in the default HACS listing, so add it as a custom repository:

1. In HACS, open the ⋮ menu → **Custom repositories**.
2. Add `https://github.com/Danathar/sensi` with type **Integration**.
3. Download **Sensi** from HACS, then restart Home Assistant.

If you want the upstream version instead, install [HACS](https://hacs.xyz/) and search for Sensi normally.

### Manual

Copy `custom_components/sensi/` into `<config directory>/custom_components/sensi/` and restart Home Assistant.

## Setup

### Getting a refresh token

1. Open Chrome or Edge and go to <https://manager.sensicomfort.com/>.
2. Press <kbd>F12</kbd> to open DevTools and select the **Network** tab.
3. Log in with your Sensi credentials. You do not need to subscribe or go any further.
4. Find the `token?device=` request and copy the `refresh_token` value from its **Response**.

You may see two `token?device=` requests — use the one that has a Response body. Other browsers work the same way. Repeat this whenever you change your Sensi password.

![How to get a refresh token](https://github.com/iprak/sensi/assets/6459774/3d33a6c1-6c07-4886-b4f0-3289e62d41e4)

> [!WARNING]
> A refresh token is a **bearer credential for your Sensi account** and is long-lived. Treat it like a password: do not paste it into issues, screenshots, or logs. If you have run an older build of this integration with debug logging on, your `home-assistant.log` may contain the token — see [Security](#security).

### Adding the integration

Use **Add Integration** on the Integrations page of your Home Assistant instance, pick **Sensi**, and paste the refresh token.

<img width="517" alt="Add integration dialog" src="https://github.com/user-attachments/assets/0a496b08-fe08-4dd6-8764-ecc4d7b692c1" />

You should end up with one device per thermostat and its related entities.

<img width="378" alt="Device page" src="https://github.com/user-attachments/assets/0b8bd8a9-7c6d-4569-b3ef-cf08c828cfca" />
<img width="376" alt="Entity list" src="https://github.com/user-attachments/assets/d45e65c3-7595-4063-a689-7e6f9f280499" />

Only one Sensi account can be configured at a time. Credentials live in a single domain-keyed store, so a second config entry would overwrite the first account's tokens; the integration declares `single_config_entry` to prevent that.

## What you get

Data refreshes every 30 seconds.

### Climate entity

| | |
| --- | --- |
| **Operating modes** | `Auto`, `Heat`, `Cool`, `Off` — which of these appear depends on the thermostat's own configuration |
| **Setpoints** | `Heat` and `Cool` use a single target temperature; `Auto` uses separate heat and cool setpoints |
| **Fan modes** | `Auto`, `On`, `Circulate` (10% duty cycle). Circulate depends on the thermostat |
| **Humidity** | Target humidity, when the thermostat has humidification enabled |

### Sensors and controls

**Enabled by default:** Temperature, Humidity, Online.

**Disabled by default** (enable them per-entity if you want them): Active Savings Event, Battery, Min/Max setpoints, Fan speed, WiFi strength.

**Configuration entities**, all of which vary by thermostat model: Auxiliary Heating, Continuous Backlight, Display Humidity, Display Time, Fan, Humidification, Keypad Lockout, and Temperature/Humidity offsets.

### Notes on specific entities

**Auxiliary heating** is a switch under device configuration, not a climate attribute. Home Assistant labels the resulting action as `Heating`.

**Humidification** is only available on thermostats where it was enabled during physical setup. Sensi works in 5% increments and values are rounded to the nearest step. When it is active the climate entity gains `min_humidity`, `max_humidity`, `humidity` (target) and `current_humidity`. The default Home Assistant card exposes the humidity level only, and **dehumidification is not supported**.

**Offsets** — the Temperature and Humidity offset number entities shift the values the thermostat displays. Their bounds come from the thermostat's own reported capabilities.

**Active Savings Event** is a diagnostic sensor for the [Active Savings Event status](https://sensi.copeland.com/en-us/support/active-savings-event). If you have opted in with your energy provider, that provider can make temporary adjustments during peak demand to reduce strain on the grid.

Its values are `Opt-out`, `Upcoming`, `Current`, `None`, `Unknown`, with the event start and end times as extra attributes. It mirrors the mobile app, where the visual notification appears `pre_duration + pre_gap + notification_time` minutes before the event — observed in practice as 120 + 0 + 5 minutes. Two caveats: the default climate card has no way to show this status, and the event does not lock down temperature control.

### Attributes

Sample attributes on the climate entity — some are supplied by Home Assistant itself rather than by this integration:

```yaml
hvac_modes: off, heat, cool, auto
min_temp: 50
max_temp: 73
target_temp_step: 1
fan_modes: auto, on, Circulate
current_temperature: 69
temperature: 69
current_humidity: 51
fan_mode: Circulate
hvac_action: heating
circulating_fan: true
circulating_fan_duty_cycle: 10
hvac_heat_stage: 100
hvac_cool_stage: 0
hvac_aux_stage: 100
attribution: Data provided by Sensi
friendly_name: Living Room
supported_features: 397
min_humidity: 5
max_humidity: 50
humidity: 5
```

On multi-stage systems (2-stage heat pumps, multi-stage auxiliary heat), `hvac_*_stage` carries the raw demand percentage: **50** means stage 1 of a 2-stage system is active, **100** means stage 2 — or a single-stage system — is active.

## Limitations

- **Simultaneous logins.** Using the mobile app and the integration at the same time usually works, but property changes occasionally fail to apply — likely a Sensi backend issue, or the thermostat briefly dropping offline.
- **Stale online status.** Incoming device data keeps reporting a thermostat as `online` for roughly 10 minutes after it loses WiFi. What happens to operations during that window is not known.
- **Temperature unit.** The unit shown comes from Home Assistant's `unit_system` setting, not from the thermostat. Make sure the two agree ([upstream issue #113](https://github.com/iprak/sensi/issues/113)).
- No public API, so any of this can break when Emerson changes their backend.

## About this fork

Upstream is [`iprak/sensi`](https://github.com/iprak/sensi); this fork tracks it and adds:

- **CI that runs on every pull request** — the pytest suite, a coverage floor, `ruff`, [hassfest](https://developers.home-assistant.io/blog/2020/04/16/hassfest/), HACS validation, and a nightly run against the latest Home Assistant release as advance warning.
- **An end-to-end test tier** covering setup, connection lifecycle and control, on top of the existing unit tests, plus contributor and agent documentation ([CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md), [docs/](docs/)).
- **Correctness and lifecycle fixes** found by a full-component scan: swapped humidity capability defaults that produced `min_humidity > max_humidity`; a device class passed where a state class belongs on the WiFi sensor, which silently kept it out of long-term statistics; leaked socket.io clients on reconnect; an aux-heat switch that could not be turned off if Home Assistant started while the thermostat was already aux heating; reauth accepting a token for a different Sensi account; hardcoded Fahrenheit offset bounds applied to a Celsius scale; and several unhandled-value crashes.

Full detail is in the commit history. Changes that make sense upstream get proposed there.

## Security

Older builds wrote the full refresh token to the log when debug logging was enabled for this integration. If you have run one of those with debug logging on, **treat those logs as containing a live credential** — refresh tokens are long-lived and some do not expire for years. Rotate by changing your Sensi password and obtaining a new refresh token. Current builds log only a short fingerprint.

Found a vulnerability? Please do not open a public issue for it. Use [private vulnerability reporting](https://github.com/Danathar/sensi/security/advisories/new) instead. [docs/SECURITY-AI.md](docs/SECURITY-AI.md) covers the extra surface that agent-assisted maintenance adds — prompt injection through the payloads this integration parses, and what agents are and are not allowed to touch.

## Development

[CONTRIBUTING.md](CONTRIBUTING.md) is the full guide — setup, the checks, the layout of the component, and the conventions that come from talking to an undocumented backend. Working with a coding assistant? [AGENTS.md](AGENTS.md) is the same guidance written for one.

The short version:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
pytest                                      # the whole suite
ruff check . && ruff format .               # lint and format
python3 scripts/check_requirements_sync.py  # manifest vs requirements_component.txt
```

The repository also ships a devcontainer, which installs all of that for you; the site comes up on `localhost:9123` with the user `test`/`test`.

CI enforces the same checks on every pull request: the pytest suite (`ci.yml`), a line-coverage floor of 93% (`coverage-gate.yml`), and `ruff` + hassfest + HACS + requirements sync (`validate.yml`). A nightly run repeats the gate against the *latest* Home Assistant release as advance warning.

Further reading in [docs/](docs/): [quality.md](docs/quality.md), [metrics.md](docs/metrics.md), [review-rubric.md](docs/review-rubric.md), [risk-tiers.md](docs/risk-tiers.md), and [SECURITY-AI.md](docs/SECURITY-AI.md) for how agent-assisted changes are handled.

## Breaking changes

<details>
<summary>Upstream release history with breaking changes</summary>

### 2.0.0

Major rewrite:

- Internals moved from `websockets` to `python-socketio`.
- Thermostat temperature and settings now align with the Sensi app, for better reliability.
- Humidification support added.
- Separate heat and cool setpoints in auto mode.
- Names and IDs of configuration settings and sensors corrected.

### 1.3.0

Authentication switched to `refresh_token` instead of username/password.

### 1.2.0

Entity and unique IDs corrected. The previous entities appear as duplicates or disabled — remove them and reference the new ones. New entity IDs are based on the thermostat's name rather than its device ID.

### 1.1.1

Battery level is now computed from a formula rather than reported directly; the raw voltage remains available as an attribute. Expect a warning like `The unit of sensor.sensi_..._battery (%) cannot be converted to the unit of previously compiled statistics (V)`.

### 1.1.0

Entity IDs changed to support multiple thermostats on one account. Previous entities appear as duplicates or disabled — remove the integration and add it back.

</details>

## Thanks

This integration exists because of [**@iprak**](https://github.com/iprak), who wrote and maintains [`iprak/sensi`](https://github.com/iprak/sensi) — the reverse engineering, the entity model, and essentially all of the functionality documented above are their work. This fork is a small set of fixes on top of it.

It in turn builds on [**@w1ll1am23**](https://github.com/w1ll1am23)'s [`pysensi`](https://github.com/w1ll1am23/pysensi), which worked out how to talk to the Sensi backend in the first place.

If this integration is useful to you, please support the upstream author:

<a href="https://buymeacoffee.com/leolite1q" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" height="32px" alt="Buy the upstream author a coffee"></a>

## How this repository is maintained

Maintenance here is assisted by [**Hive**](https://github.com/hivecommons/hive) — the agent-orchestration software from the [Hivecommons](https://github.com/hivecommons) project — which runs a fleet of AI agents against this repository at **ACMM level 4 (Security-Aware)**. This section is here because a reader deserves to know that before they install something into their home, and because the arrangement is unusual enough to be worth explaining rather than hinting at with a badge.

### What that actually means

Hive runs specialised agents — quality, security, CI, docs — continuously rather than when someone remembers to look. Each has a *policy mode* set by the ACMM level, and L4 is deliberately short of autonomy:

- All agents may **file issues**.
- The quality, security and CI agents may additionally **open pull requests**, which carry a hold label.
- Every other agent stays **advisory**: it reports, it does not act.
- **A human reviews and merges everything.** No agent merges its own work, and nothing reaches `master` without a person having read it.

The one agent-driven path that touches this repository directly, [`ai-fix.yml`](.github/workflows/ai-fix.yml), is inert unless *two* separate switches are on — an API key secret and an `AI_FIX_ENABLED` repository variable. Two rather than one is on purpose: the `ai-fix-requested` label is applied automatically, so a key added for something unrelated must not quietly start autonomous work. [docs/SECURITY-AI.md](docs/SECURITY-AI.md) sets out what agents may and may not touch, including the prompt-injection surface that comes with parsing an untrusted backend's payloads.

### Why it matters for this integration in particular

This is a fork of a reverse-engineered integration talking to an undocumented backend that Emerson can change without notice, maintained by one person in their spare time. Those are exactly the conditions under which projects rot quietly: the failure mode is not a dramatic break, it is a thermostat that stops reporting correctly six months from now while nobody is looking.

Continuous review pushes against that. The [nightly run](.github/workflows/nightly.yml) exercises the suite against the *latest* Home Assistant release as well as the pinned one, so a breaking core change shows up as a failed nightly rather than as your integration failing to load after an update.

### How it compounds

The point is not the volume of agent output — it is that each pass leaves the next one starting from a better position:

- **The gates ratchet.** Line coverage has a floor, currently 93%, enforced on every pull request. [`scripts/auto_qa_tuner.py`](scripts/auto_qa_tuner.py) *proposes* raising it when the suite genuinely improves, and deliberately never applies the change itself. The floor goes up as the tests get better and never quietly comes back down to let a change through.
- **Risk is classified, not guessed.** Every pull request gets a [risk tier](docs/risk-tiers.md) from the paths it touches, so a change to `auth.py` or `config_flow.py` — the files that decide whether an existing install still loads — is held to a different standard than a change to a doc.
- **Lessons are written down where the next pass will read them.** [`docs/reflections/`](docs/reflections/) holds what a piece of work taught about this codebase, and [AGENTS.md](AGENTS.md) is the standing brief. Agents and humans start from the same accumulated context instead of rediscovering the same constraint.
- **The measurement is of outcomes, not activity.** [`scripts/pr_metrics.py`](scripts/pr_metrics.py) tracks acceptance rate, time to merge and review rounds — [not lines written or PRs opened](docs/metrics.md). An agent that opens twenty pull requests of which three merge is worse than one that opens four of which four merge, and only that measurement tells them apart.

The concrete result so far: an end-to-end test tier built against a scripted stand-in for the Sensi server, which took `client.py` — the connection, reconnect and token-refresh paths, the highest-risk module in the component — from 52% line coverage to 100%, and the repository from 85% to 98%. [docs/quality.md](docs/quality.md) is honest about what those numbers do *not* prove.

Learn more: [Hive](https://github.com/hivecommons/hive) · [Hive Hub](https://hive.kubestellar.io) · [the full ACMM policy matrix](https://github.com/hivecommons/hive/blob/v4/src/docs/acmm-policy-matrix.md)

## About this project

> [!NOTE]
> Work on this fork is done with AI assistance and should be treated cautiously.
>
> This is a third-party integration. It is not an official Sensi, Emerson, or Copeland product, is not sanctioned by any of them, and is not an official Home Assistant integration. "Sensi" is a trademark of its owner and is used here only to say what this software talks to.
>
> It is provided as-is, with no promise that it is safe for your account, your data, your Home Assistant instance, or your HVAC equipment. Review changes before applying them and keep backups where appropriate. The maintainer is not responsible for account lockouts, credential exposure, equipment behavior, data loss, or other consequences of using this software.

## License

MIT, inherited from upstream. Copyright (c) 2022 Indu Prakash. See [LICENSE](LICENSE).
