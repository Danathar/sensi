# Respond to a Sensi protocol change

The Sensi socket.io protocol is reverse engineered from the mobile app. Emerson
changes it without notice and there is no spec. Read `AGENTS.md` first.

## Do this

1. **Establish what actually changed.** Get a current payload — see
   `.claude/commands/capture-payload.md` for the scrubbing rules — and diff it
   against `tests/sample.json`. Name the specific field, event, or error code.
   Do not proceed on a theory.

2. **Decide the failure mode you want.** The integration should degrade, never
   crash. A removed field becomes "unknown" or an unavailable entity; a new
   error code becomes a `HomeAssistantError` with the code in the message; a
   changed response type is handled for both shapes, the way
   `async_set_operating_mode` already accepts either a string or a dict.

3. **Fix it in the parsing layer, not at the call sites.** `data.py`,
   `capabilities.py` and `event.py` own payload shape. If a platform module has
   to know about the change, the parsing layer is leaking.

4. **Keep the old shape working.** Users run older firmware. Handle both unless
   you can show the old one is gone, and say which you did.

5. **Add the new payload as a fixture** and write a test that fails without the
   change. Cover both shapes.

6. **Check whether it affects setters.** A response-shape change usually hits
   `_async_invoke_setter` and its ack handling too, not just the read path.
   `tests/e2e/test_control.py` is where that gets asserted, on the emitted
   payload.

## Output

- The exact diff in the payload, field by field.
- The chosen degradation behaviour, and why.
- The fixture and tests added.
- Anything that still needs verifying against real hardware, stated plainly.
