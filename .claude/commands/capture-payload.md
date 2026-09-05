---
description: Turn a real thermostat payload into a scrubbed test fixture
argument-hint: [path to the captured payload, or paste it]
---

Turn this captured Sensi payload into a test fixture: $ARGUMENTS

**Scrub it before anything else.** The payload comes from a real account. Replace,
do not delete:

| Field | Replace with |
| --- | --- |
| `icd_id` | the placeholder already used in `tests/sample.json` |
| `serial_number`, `unique_hardware_id`, `wifi_mac_address` | plausible fake values of the same shape |
| `registration.address1` / `address2` / `postal_code` / `city` / `state` | the placeholders already in `tests/sample.json` |
| any `access_token`, `refresh_token`, `Authorization` | remove the key entirely |

Keep the shape and the types exactly — the point of a fixture is that it is a
real payload. Do not tidy the field order or drop keys the code does not read
yet; the unread ones are the early warning when Emerson changes something.

Then:

1. Diff the scrubbed payload against `tests/sample.json`. If it is structurally
   identical, say so and stop — a second identical fixture is not worth the
   maintenance.
2. If it differs, save it as `tests/sample_<what-makes-it-different>.json` and
   add a matching fixture in `tests/conftest.py` alongside `mock_json`.
3. Add a test that would fail against the existing fixtures, so the new file is
   load-bearing.

Report which fields you scrubbed. Never paste the unscrubbed payload back into
the conversation, a commit message, or a PR description.
