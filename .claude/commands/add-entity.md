---
description: Add a new entity to an existing Sensi platform
argument-hint: [what the entity should expose]
---

Add an entity exposing: $ARGUMENTS

Work in this order.

1. **Find the data first.** Confirm the value exists in `tests/sample.json` (or
   `tests/sample_with_humidification.json`). If it does not, stop and say so —
   the Sensi protocol is undocumented and there is no schema to guess from.

2. **Decide the platform.** `sensor.py` for read-only values, `binary_sensor.py`
   for on/off readings, `switch.py` for a toggleable thermostat setting,
   `number.py` for a bounded numeric setting. Each uses a table of
   `*EntityDescription` objects near the top of the module — add to that table
   rather than writing a new class, unless the behaviour genuinely differs.

3. **Gate it on capabilities.** Most settings are model-dependent. Check
   `capabilities.py` for the matching capability and make the entity report
   unavailable when the thermostat does not support it, the way the aux heat
   switch does. Do not create an entity that is permanently broken on some
   models.

4. **Parse defensively.** `.get()` with a default, and the `to_bool` / `to_int`
   / `to_float` helpers from `utils.py`.

5. **Write the state parsing in `data.py`** if the value is not already on
   `State`, and cover it in `tests/test_data.py`.

6. **Test both tiers.** A unit test in `tests/test_<platform>.py`, and — if the
   entity writes back to the thermostat — an end-to-end test in
   `tests/e2e/test_control.py` asserting on the emitted event payload, not just
   on the resulting entity state.

7. **Check the naming.** New entities need an entry in `strings.json` and
   `translations/en.json` if they are user-facing. No literal URLs there.

Finish by running `/check`.

Do not change any existing entity's `unique_id` — that breaks existing installs.
