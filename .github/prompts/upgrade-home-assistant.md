# Bump the pinned Home Assistant / test harness

`pytest-homeassistant-custom-component` in `requirements_test.txt` ships a
pinned Home Assistant. That pin — not anything else in the repository — decides
which Home Assistant release the suite runs against, and therefore the minimum
Python version. Treat it as a deliberate change with its own commit.

## Do this

1. **Bump only the pin**, in its own commit, with nothing else in the diff.

2. **Record what moved.** Report the resolved `homeassistant` version before and
   after, not just the harness version. `.github/workflows/tests.yml` has a
   `Record resolved versions` step that prints it.

3. **Check the Python floor.** If the new Home Assistant raises its minimum
   Python, `python-version` in `tests.yml`, `coverage-gate.yml` and
   `validate.yml`, and `target-version` in `ruff.toml`, all have to move
   together. The matrix comment in `tests.yml` explains why a second, older
   interpreter is not tested — do not add one to "be safe"; pip will silently
   resolve an older Home Assistant and the job will report on a release nobody
   runs.

4. **Read the failures as signal.** Home Assistant deprecations surface here
   first. A `DeprecationWarning` about an entity property, a config-entry API,
   or a helper import is the actual work of the upgrade — fix it rather than
   suppressing it.

5. **Re-run hassfest.** It validates against the new core and catches manifest
   and translation problems the unit suite cannot.

6. **Watch coverage.** Harness changes can move which lines are reachable. If
   the gate fails, check whether real coverage dropped or whether a branch
   simply stopped existing.

## Output

- Old and new resolved `homeassistant` version.
- Every deprecation the bump surfaced, and what was done about each.
- Whether the minimum Python moved, and which files were updated if so.
