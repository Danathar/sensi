# Testing an undocumented protocol

**Take-away.** The connection layer of a reverse-engineered client is testable,
cheaply, by scripting a fake server rather than by mocking the client's own
methods. Doing that took `client.py` from 52% to 90% line coverage in one
change. It still does not prove the integration works — only that it handles the
payloads we have seen.

## The situation

`custom_components/sensi/client.py` owns the socket.io connection to
`rt.sensiapi.io`: connect, the initial `state` event, per-device `get_info` and
`get_capabilities`, a background emit loop draining a queue, ack callbacks for
setters, disconnect, and a reconnect path that refreshes an expired token
mid-session.

It was the least-covered module in the repository and the one with the most
runtime risk. That combination is not a coincidence. Unit tests are worst
exactly where this code lives: nothing here is a pure function, most of it is
ordering, and the interesting failures are timing and lifecycle.

## What did not work

**Mocking the client's own methods.** Patching `wait_for_devices` or
`_connect` is what the existing tests did, and it is what makes setup tests
pass without covering anything. You end up asserting that the code calls the
method you replaced.

**Mocking `socketio.AsyncClient` with `MagicMock`.** The client does not call
methods on the socket and read return values; it *registers handlers* with
`@sio.event` and `@sio.on("*")` and waits on futures that those handlers
resolve. A `MagicMock` accepts the registration and then nothing ever fires. The
test hangs or times out, and the natural next move — reaching into `_futures`
to resolve them by hand — is testing the test.

## What worked

A fake that behaves like the *server*, not like the client's dependency.
`FakeSensiBackend` in `tests/e2e/conftest.py` holds device payloads and answers:

- `connect()` accepts, calls the registered `connect` handler, then **schedules**
  the initial `state` event as a task
- `emit("get_info")` schedules an `info` event; `emit("get_capabilities")`
  schedules a `capabilities` event
- `emit("set_temperature")` invokes the ack callback with a scripted response

Everything else — Home Assistant's config entry setup, the coordinator, the
platforms, the entities — runs for real.

### The ordering detail that makes it work

`SensiClient` creates the future it waits on *after* the call that should
trigger the response. So a fake that delivers inline never resolves anything:
the event arrives before anyone is listening.

Scheduling the delivery as a task fixes it, and the reason is worth writing
down. Between `sio.connect()` returning and `asyncio.wait_for(future, ...)`
suspending, the client never yields — `_create_event_future` is an `async def`
with no awaits inside it, and `asyncio.Queue.put` on an unbounded queue does not
suspend either. The scheduled task therefore cannot run until the client is
already waiting. The ordering is guaranteed by the absence of await points, not
by a sleep.

If a future refactor adds an `await` in that window, these tests will start
flaking. That is the trade: the fake is coupled to the client's suspension
points. It is a better coupling than the alternative, which was no coverage.

## What this bought

| | before | after |
| --- | --- | --- |
| `client.py` | 52% | 90% |
| repository total | 85% | 96% |

Twelve tests, covering the handshake, entity and device registry population,
unload, setup retry on connection failure, setter round-trips asserted on the
emitted wire payload, an error ack surfacing as `HomeAssistantError`, an
unsupported capability staying unavailable, and a coordinator refresh
reconnecting and applying new state.

It also found a real defect in the test approach itself:
`SensiClient._async_disconnect` calls `sio.shutdown()`, not `sio.disconnect()`,
inside `contextlib.suppress(Exception)`. A fake missing `shutdown()` raises
`AttributeError`, has it swallowed, and looks exactly like a clean teardown.
The generalisation — when stubbing something the production code calls inside a
blanket `suppress`, a wrong method name is *silent* — is in
`.claude/memory/fake-socket-must-implement-shutdown.md`.

## What it still does not prove

This is the part that matters more than the coverage number.

The fixtures under `tests/` are payloads captured from a real thermostat on the
day they were captured. The suite proves the code handles **those**. It cannot
prove anything about a payload Emerson has not sent yet, because there is no
schema, no changelog, and no announcement when the protocol changes.

So a green suite means "no regression against what we have seen". It does not
mean the integration works. That is why the parsing layer degrades instead of
raising, why unread fields are kept in fixtures rather than trimmed, and why the
pull request template asks whether a change was exercised against real hardware
— a question CI can never answer.

96% coverage on a reverse-engineered client is a floor under regressions, not a
correctness claim. Presenting it as the latter would be the more comfortable
reading and the wrong one.
