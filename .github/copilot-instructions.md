# GitHub Copilot instructions

Home Assistant custom integration for Sensi thermostats. Python 3.14+, async
throughout. All shipped code lives in `custom_components/sensi/`.

`AGENTS.md` in the repository root is the full version of this file. The rules
below are repeated here because Copilot reads this file directly rather than
following a pointer.

## Hard rules

- **Never emit a credential.** No access tokens, refresh tokens, or real
  `icd_id` values in code, logs, docstrings, test fixtures, or commit messages.
  `auth.py` exports `redact_token` — use it for anything token-shaped.
- **Parse defensively.** The Sensi socket.io protocol is reverse engineered and
  undocumented. Use `.get()` with a default and the `to_bool` / `to_int` /
  `to_float` helpers from `utils.py`. Never index a payload dict directly.
- **Do not create `pyproject.toml`.** It is gitignored — the devcontainer image
  provides one. Ruff config lives in `ruff.toml`, pytest config in `pytest.ini`.
- **Do not edit `manifest.json` `version`.** The release workflow owns it.
- **Keep `manifest.json` `requirements` and `requirements_component.txt` in
  sync.** CI fails on drift.
- **No literal URLs in `strings.json`.** hassfest rejects them; pass them
  through `description_placeholders`.

## Style

- Home Assistant core conventions. Docstrings on every module, class, and
  function — pydocstyle is enforced by `ruff.toml`.
- `async` everywhere in the client and entity layers. No blocking I/O on the
  event loop.
- Imports follow Home Assistant's isort layout: `homeassistant` is first-party,
  and plain `import x` interleaves with `from x import y` within a section.
  `ruff check --fix` will sort them for you.
- Line length 88, formatted by `ruff format`.

## Tests

- One test module per source module: `tests/test_<module>.py`.
- `tests/e2e/` drives the whole integration against a scripted fake socket.io
  backend — use it when the assertion is about what went out on the wire.
- New behaviour needs a test. A bug fix needs a test that fails before the fix.
- Coverage is gated at 90%.

Before treating a change as complete: `pytest && ruff check . && ruff format --check .`

## Commit messages

Conventional Commits. `feat:` and `fix:` bump the released version, so use them
only for user-visible integration changes. Tooling, CI and docs are `ci:`,
`docs:`, `test:`, `refactor:` or `chore:`.
