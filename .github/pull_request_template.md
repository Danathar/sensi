## What changed

<!-- One or two sentences. What does this PR do, and why now? -->

## Why

<!--
Link the issue it closes ("Closes #123"), or describe the bug/behaviour that
prompted it. For a fix, say what the user-visible symptom was.
-->

## How it was verified

<!-- Tick what you actually did. Delete what does not apply. -->

- [ ] `pytest` passes locally
- [ ] `ruff check .` and `ruff format --check .` are clean
- [ ] New or changed behaviour is covered by a test
- [ ] Ran against a real thermostat (say which model, and what you exercised)

## Risk

<!--
See docs/risk-tiers.md if this repository has one. Otherwise, in a sentence:
what breaks for existing users if this is wrong, and how would they notice?
-->

- [ ] This changes the config flow, stored credentials, or entity unique IDs
      (these are breaking for existing installs - call it out explicitly)
- [ ] This bumps `manifest.json` `requirements` (keep `requirements_component.txt` in sync)

## Notes for reviewers

<!-- Anything non-obvious: a protocol quirk, a sample payload, a known gap. -->
