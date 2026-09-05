---
description: Run the full local gate - tests, lint, format, requirements sync
---

Run every check CI runs, in the order that fails fastest, and report the result.

```bash
ruff format --check .
ruff check .
python3 scripts/check_requirements_sync.py
pytest --cov=custom_components.sensi --cov-report=term-missing
```

Then:

- If `ruff format --check` fails, run `ruff format .` and show the diff rather
  than describing it.
- If `ruff check` fails, prefer `ruff check --fix` for the mechanical rules
  (import sorting, redundant annotations) and fix the rest by hand. Do not add
  `# noqa` to silence a rule without saying why in the same line comment.
- If coverage is below 90% the `coverage-gate` workflow will fail. Name the
  modules that lost coverage and the specific missing lines — do not just report
  the total.
- Report the actual numbers (tests passed, coverage percentage). Do not say
  "all checks pass" without them.

Note that `hassfest` and HACS validation also run in CI and cannot be reproduced
locally; if the change touches `manifest.json`, `strings.json`, `translations/`
or the repository layout, say so explicitly.
