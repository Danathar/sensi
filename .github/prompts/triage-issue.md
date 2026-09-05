# Triage a user-reported problem

You are triaging an issue against the Sensi Home Assistant integration. Read
`AGENTS.md` first.

## Input

Paste the issue body, including the Home Assistant version, the integration
version, the thermostat model, and the `custom_components.sensi` log lines.

## Do this

1. **Check for credentials in what you were given.** If the report contains a
   token or a real `icd_id`, say so immediately and do not repeat the value
   anywhere in your answer.

2. **Place the failure in the lifecycle.** Which is it?
   - config flow / authentication — `config_flow.py`, `auth.py`
   - initial setup, before entities exist — `__init__.py`, `client.wait_for_devices`
   - connection dropped mid-session — `client._connect`, `_async_disconnect`,
     the reconnect and token-refresh path
   - a coordinator update failing — `coordinator.py`, `client.async_update_devices`
   - one entity wrong while the rest are fine — that platform module and `data.py`

   The log tells you: `Engine.IO connection dropped`, `jwt expired`,
   `Timed out waiting for event`, and `Updating devices - reconnecting` each
   pin it to a different place. `client.py` carries annotated log samples for
   the connection cases — read them before theorising.

3. **Decide whether it is a protocol change.** A payload field that is suddenly
   absent, an unexpected error code, or a setter that started returning a
   string instead of JSON all point at Emerson changing something rather than
   at a regression here. Compare against `tests/sample.json`. If it is a
   protocol change, switch to `protocol-change.md`.

4. **Reproduce it as a test before proposing a fix.** Almost every real case can
   be reproduced by scripting `FakeSensiBackend` in `tests/e2e/` — a missing
   field, an error ack, a connection that fails once and then succeeds. If you
   cannot reproduce it, say so plainly rather than guessing at a fix.

5. **Say what you cannot know.** The protocol is undocumented and there is no
   hardware in CI. If confirming the diagnosis needs a real thermostat, state
   that and say exactly what the reporter should capture.

## Output

- One-line diagnosis, and the specific file and function.
- The evidence from the log that supports it.
- A failing test that reproduces it, or an explicit statement that it cannot be
  reproduced without hardware.
- The fix, or the next question for the reporter — not both hedged together.
