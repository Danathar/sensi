# Request `hass` before any fixture that patches `Store.async_load`

**Symptom** — a test decorated with
`@pytest.mark.usefixtures("stored_credentials", ...)` failed during *setup* with
`KeyError: 'categories'` raised from `homeassistant/helpers/category_registry.py`,
plus an unrelated-looking timezone assertion in the `hass` fixture teardown.

**Why the wrong answer looked right** — `usefixtures` and a parameter list look
interchangeable, and the patch is obviously scoped to the test.

**Rule** — `homeassistant.helpers.storage.Store.async_load` is patched
*globally*, and Home Assistant loads its own registries — category, entity,
device, area — through that same `Store` while the `hass` fixture starts up. A
fixture that makes `async_load` return the Sensi credential dict will therefore
feed that dict to the category registry.

Fixtures named in `usefixtures` are set up before fixtures in the parameter
list. So request `hass` as a parameter *first*, and the patching fixture after
it:

```python
async def test_x(
    hass: HomeAssistant,
    sensi_backend: FakeSensiBackend,
    stored_credentials: None,          # after hass, deliberately
    enable_custom_integrations: None,
) -> None:
```

**Source** — `tests/e2e/test_setup.py::test_setup_retries_when_the_backend_is_unreachable`,
PR #39.
