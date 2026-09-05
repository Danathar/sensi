# Set `hass.config.units = US_CUSTOMARY_SYSTEM` before asserting on temperatures

**Symptom** — asking the climate entity to set 72 degrees produced
`ServiceValidationError: Provided temperature 161.6 is not valid`, and a state
push of 61.5°F came back as `current_temperature == 16.4`.

**Why the wrong answer looked right** — the numbers in `tests/sample.json` are
Fahrenheit, so 72 and 61.5 look like the values that should appear.

**Rule** — the `hass` test fixture defaults to the metric unit system, so every
temperature crossing the entity boundary is converted. The captured payloads
report `display_scale: "f"`, so an F-based assertion under a metric `hass` is
really an assertion about Home Assistant's F-to-C rounding, not about this
integration. Set the unit system before setting the entry up:

```python
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

hass.config.units = US_CUSTOMARY_SYSTEM
```

Also remember the climate entity uses `PRECISION_WHOLE`: a pushed 61.5 is
reported as 62. Use whole degrees in fixtures unless the rounding *is* the thing
under test.

**Source** — `tests/e2e/conftest.py`, PR #39.
