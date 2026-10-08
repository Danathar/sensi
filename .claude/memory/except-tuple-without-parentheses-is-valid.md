# `except ValueError, TypeError:` is valid Python 3.14 — do not "fix" it

**Symptom** — `client.py` contains `except ValueError, TypeError:`, which reads
as a Python 2 syntax error that somehow survived. The obvious move is to add
parentheses.

**Why the wrong answer looked right** — that spelling was a hard `SyntaxError`
from Python 3.0 through 3.13, and it is one of the most recognisable Python 2
relics.

**Rule** — PEP 758 (Python 3.14) allows `except` and `except*` to take an
unparenthesised tuple. This repository targets 3.14+ (`ruff.toml` sets
`target-version = "py314"`, and Home Assistant has required >= 3.14.2 since
2026.3.0), so the code is correct as written. Verify with `python3 -m py_compile`
before reporting a syntax problem in this repository — the interpreter here is
newer than most of the syntax knowledge that reads as settled.

Do not add the parentheses either: `ruff format` removes them under `py314`,
so the "fix" turns `ruff format --check` red in CI.

The one exception is `.claude/hooks/*.py`: these run under the host's `python3`, not
Home Assistant's, so they keep the parentheses and `ruff.toml` targets them at
py312. An unparseable gate hook fails open (#410).

The one real exposure behind this report is a core older than 2026.3.0, which
still runs Python 3.13 and cannot import the tree at all. `hacs.json` declares
`"homeassistant": "2026.9.0"` so HACS refuses the download with a clear message
instead; `tests/test_metadata.py` keeps that floor equal to the Home Assistant
release the test suite runs against.

**Source** — false-positive bug reports raised against `client.py`, and again
as #79 against `__init__.py` and `client.py`.
