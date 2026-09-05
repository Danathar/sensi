# A fake socket.io client must implement `shutdown()`, not just `disconnect()`

**Symptom** — an end-to-end test asserting that unloading the config entry tears
the connection down saw zero disconnects, while the log clearly showed
`Disconnecting`. Nothing raised.

**Why the wrong answer looked right** — `disconnect()` is the obvious method to
stub, and it is the one `python-socketio` documents most prominently. The log
line appeared because `_async_disconnect` logs *before* it tears anything down.

**Rule** — `SensiClient._async_disconnect` calls `sio.shutdown()`, not
`sio.disconnect()`, and it does so inside `contextlib.suppress(Exception)`. A
fake missing `shutdown()` therefore raises `AttributeError`, has it swallowed,
and looks exactly like a clean teardown. Any stand-in for `socketio.AsyncClient`
must implement `shutdown()`. More generally: when stubbing something the
production code calls inside a blanket `suppress`, check the real call site for
the method name — a wrong guess is silent.

**Source** — `tests/e2e/test_setup.py::test_unload_disconnects_the_socket`,
PR #39.
