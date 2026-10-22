---
description: Run the full local gate - tests, lint, format, requirements sync
---

Run every check CI runs, in the order that fails fastest, and report the result.

```bash
ruff format --check .
ruff check .
python3 scripts/check_requirements_sync.py
python3 scripts/run_tests.py --cov=custom_components.sensi --cov-report=term-missing
```

Then:

- If `ruff format --check` fails, run `ruff format .` (it asks first, because
  it rewrites files) and show the diff rather than describing it.
- If `ruff check` fails, prefer `ruff check --fix` for the mechanical rules
  (import sorting, redundant annotations) and fix the rest by hand. Do not add
  `# noqa` to silence a rule without saying why in the same line comment.
- If coverage is below 93% the `coverage-gate` workflow will fail. Name the
  modules that lost coverage and the specific missing lines — do not just report
  the total.
- Report the actual numbers (tests passed, coverage percentage). Do not say
  "all checks pass" without them.

Note that `hassfest` and HACS validation also run in CI and cannot be reproduced
locally; if the change touches `manifest.json`, `strings.json`, `translations/`
or the repository layout, say so explicitly.

CI also runs `scripts/mutate_changed.py` on every pull request, in the advisory
`mutation (changed lines)` job. It is not in the block above because it runs
the tests once for every small edit of every changed line, which takes
minutes. If that job reports a survivor, add a test that fails on the edit it
prints, or list the edit in `tests/mutation-equivalents.txt` with a reason.
