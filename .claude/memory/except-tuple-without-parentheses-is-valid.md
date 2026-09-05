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
2026.6.0), so the code is correct as written. Verify with `python3 -m py_compile`
before reporting a syntax problem in this repository — the interpreter here is
newer than most of the syntax knowledge that reads as settled.

**Source** — a false-positive bug report raised while reviewing `client.py`.
