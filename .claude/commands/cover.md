---
description: Find the weakest-covered code and raise it with real tests
argument-hint: [optional module name]
---

Raise test coverage. Target: $ARGUMENTS (if empty, pick the weakest module).

1. Measure first:

   ```bash
   pytest --cov=custom_components.sensi --cov-report=term-missing
   ```

2. Pick by risk, not by percentage. A missing line in `client.py`'s reconnect
   path matters more than a missing line in a `__str__`. State which you chose
   and why.

3. Choose the right tier:
   - Pure logic — parsing, helpers, state transitions — goes in
     `tests/test_<module>.py` with the existing `mock_json` / `mock_device`
     fixtures.
   - Anything involving the connection, the emit loop, the coordinator, or the
     entity lifecycle goes in `tests/e2e/`, using `FakeSensiBackend`. Extend the
     fake rather than reaching into private client state.

4. Write tests that would catch a real regression. A test that only executes a
   line without asserting on its effect raises the number and protects nothing —
   do not write one.

5. Report before/after per module and for the total, from the actual command
   output.

Do not lower the 90% threshold in `.github/workflows/coverage-gate.yml`.
