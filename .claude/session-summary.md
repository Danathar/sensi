# Session summary

A running record of what agent sessions on this repository actually did, so the
next one starts from the current state rather than re-deriving it. Newest first.

Keep entries short and factual. What changed, what was learned, what is still
open. Not a changelog — `git log` is the changelog. This is for the things that
are not in the diff: why a decision went the way it did, and what the next
session needs to know before touching the same area.

A single-line pointer to the live state lives in
[`.claude/checkpoint.md`](checkpoint.md); this file is the history behind it.

---

## 2026-09-05 — ACMM remediation, levels 0 through 3

**Goal.** Work the open ACMM gap issues (#5–#34) filed by the Hive evaluation,
lowest level first, building each artifact to do real work rather than to
satisfy a file-existence check.

### Landed

| PR | Level | What |
| --- | --- | --- |
| #39 | L0 | `tests/e2e/`, coverage gate, `ruff.toml` + lint job, `CONTRIBUTING.md`, PR template |
| #41 | L2 | `AGENTS.md`, `CLAUDE.md`, Copilot instructions, Cursor rules, `.editorconfig`, prompt catalog, slash commands, correction memory |
| #44 | L3 | review rubric, quality and metrics docs, `.claude/settings.json` + format hook, `tests.yml` → `ci.yml`, job timeouts |
| this | L4 | labeller and risk tiers, nightly compliance, gated AI-fix workflow, AI security policy, reflections |

**Coverage moved materially.** `tests/e2e/` was the first coverage of the
connect handshake, the emit loop, and the reconnect path. `client.py` went 52% →
90%; the repository total 85% → 96%. The gate is set at 90%, below the current
number on purpose, so an unrelated change is not blocked by a line it did not
touch.

### Decisions worth not re-litigating

- **`AGENTS.md` is canonical.** `CLAUDE.md` and `.cursor/rules/` point at it.
  `.github/copilot-instructions.md` deliberately duplicates the hard rules
  instead, because Copilot reads that file directly and will not follow a link.
  If a rule changes, check that file too.
- **The ruff rule set is the set the tree already satisfied.** A lint gate that
  starts red is a lint gate nobody turns on. Widening it is a separate change.
- **`ruff` is pinned** in `requirements_test.txt`. Unpinned, a new ruff release
  turns master red on its own, because new releases add rules to selected sets.
- **Coverage lives in `coverage-gate.yml`, not `ci.yml`.** `ci.yml` is the fast
  "do the tests pass" signal; the gate owns measurement and enforcement.
- **`feat:` was wrong for the L0 work** and was amended to `ci:` before merge.
  Commit prefixes drive the version users see in HACS.

### Learned the hard way

Recorded as individual files in [`.claude/memory/`](memory/) — a fake socket.io
client must implement `shutdown()`; `except ValueError, TypeError:` is valid on
3.14; the `hass` fixture must be created before anything that patches
`Store.async_load`; temperature assertions need `US_CUSTOMARY_SYSTEM`.

### Deliberately left inert

The L4 issues asked for automation that acts on the repository. The labeller and
the nightly run are live — they only add labels and open one self-closing issue.
`ai-fix.yml` is not: it needs an `ANTHROPIC_API_KEY` secret **and** an
`AI_FIX_ENABLED` variable. Two switches, because `ai-fix-requested` is applied
automatically by the issue-filing bot, so one switch would mean a key added for
an unrelated purpose silently starts autonomous work on every issue it files.

### Still open at the end of this entry

- #36 and #38 — quality findings about `client.py` coverage and the missing
  end-to-end tier. Both were largely addressed by #39; they need a final pass to
  confirm what remains rather than being closed on assumption.

### Notes for whoever is next

- There is no thermostat in CI and there never will be. Anything whose
  correctness depends on what a real unit returns is unverified — say so rather
  than implying otherwise.
- `tests/e2e/FakeSensiBackend` is the cheapest way to reproduce a user-reported
  connection bug. Script the backend; do not reach into private client state.
- Do not create `pyproject.toml`. It is gitignored because the devcontainer
  image supplies one.
