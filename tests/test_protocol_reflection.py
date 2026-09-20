"""The protocol-testing reflection, joined to the code it explains.

`docs/reflections/testing-an-undocumented-protocol.md` was the last tracked
non-test file whose contents no test opened. `docs/reflections/README.md`
indexes it by file name and nothing read a line of the body.

The body is not narrative. It is a set of precise claims about live code: which
host `client.py` connects to, which events it emits and waits for, where the
fake server lives and what it schedules, and - the load-bearing part - *why*
the end-to-end tier works at all. That argument rests on two absences in
`client.py`: `_create_event_future` has no await points, and the emit queue is
unbounded so `put` never suspends. The document says in as many words that if a
refactor adds an `await` in that window, the end-to-end tests will start
flaking. Nothing checked either absence.

Six simultaneous falsehoods were accepted by the whole suite before this
module: the host renamed, `get_capabilities` renamed, "no awaits inside it"
reversed to "an await inside it", the cited correction file repointed at a path
that does not exist, `FakeSensiBackend` moved to a module that does not exist,
and the fix for the cancelled-future bug restated as `future.done() or`.

Conventions carried from `tests/test_prompt_procedures.py`,
`tests/test_slash_commands.py` and `tests/test_licensing.py`:

- The document is hard-wrapped, so every multi-word needle is matched against
  whitespace-collapsed text. Matching the raw bytes would make these tests
  sensitive to re-wrapping rather than to the claim.
- Names are read out of the document, not hard-coded, wherever the document is
  the thing being checked - the host, the fake's class and module, the cited
  correction file. A test that hard-coded them would pass after the rename it
  exists to catch.
- The fake's scripted behaviour is *exercised*, not grepped: the real
  `FakeSensiBackend` answers a real `FakeSensiSocket` on a real event loop, so
  a bullet that stops being true fails here.
- Every scan asserts how much it found before asserting anything about it, so a
  reader that silently returned nothing cannot turn an assertion green.

Deliberately not asserted here, because another module already owns it:
`_async_disconnect` calling `shutdown()` rather than `disconnect()`
(`tests/test_correction_memory.py`, `tests/test_instruction_docs.py`) and the
no-ack-is-a-failure behaviour the bug section describes
(`tests/test_client_events.py`). Both are deferred with a test asserting those
modules still carry them, so the deferral cannot rot.

Also deliberately not asserted: the coverage table's numbers. They are a dated
record of what two changes bought, not a claim about the tree today, and
`docs/reflections/` is kept as a record rather than refreshed. The historical
framing itself is pinned instead, so a rewrite into a live claim has to come
back through here.
"""

from __future__ import annotations

import ast
import asyncio
import functools
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest

from custom_components.sensi import client as client_module
from custom_components.sensi.data import OperatingMode, State
from tests.e2e.conftest import FakeSensiBackend, FakeSensiSocket, load_sample

_ROOT = Path(__file__).resolve().parent.parent
_REFLECTIONS = _ROOT / "docs" / "reflections"
_DOC = _REFLECTIONS / "testing-an-undocumented-protocol.md"
_INDEX = _REFLECTIONS / "README.md"
_CLIENT = _ROOT / "custom_components" / "sensi" / "client.py"
_COMPONENT = _ROOT / "custom_components" / "sensi"
_E2E_CONFTEST = _ROOT / "tests" / "e2e" / "conftest.py"
_PR_TEMPLATE = _ROOT / ".github" / "pull_request_template.md"
_CLIENT_EVENT_TESTS = _ROOT / "tests" / "test_client_events.py"
_CORRECTION_TESTS = _ROOT / "tests" / "test_correction_memory.py"
_INSTRUCTION_TESTS = _ROOT / "tests" / "test_instruction_docs.py"

_TEXT = _DOC.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Return the text with every run of whitespace collapsed to one space."""
    return " ".join(text.split())


_FLAT = _flat(_TEXT)


@functools.cache
def _module(path: Path) -> ast.Module:
    """Return the parsed module at the given path."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Return the function of that name, searching nested scopes too."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == name
        ):
            return node
    raise AssertionError(f"no function named {name!r}")


def _called_attributes(node: ast.AST) -> set[str]:
    """Return the attribute names called anywhere inside the node."""
    return {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }


def _string_args(node: ast.AST, callee: str) -> list[str]:
    """Return the first string argument of every call to the named callee."""
    found = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != callee or not child.args:
            continue
        first = child.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append(first.value)
    return found


async def _drain(backend: FakeSensiBackend) -> None:
    """Let every task the backend scheduled run to completion."""
    for _ in range(5):
        await asyncio.sleep(0)


def _wired_socket(backend: FakeSensiBackend) -> tuple[FakeSensiSocket, list, list]:
    """Return a socket wired up the way `SensiClient._connect` wires one.

    The handlers are registered through the same `event` / `on("*")` surface
    the client uses, and both record what they were called with.
    """
    socket = FakeSensiSocket(backend)
    connects: list[None] = []
    events: list[tuple] = []

    async def connect() -> None:
        connects.append(None)

    socket.event(connect)

    @socket.on("*")
    async def any_event(event, data) -> None:
        events.append((event, data))

    return socket, connects, events


# ------------------------------------------------------------------ index


def _index_rows() -> dict[str, str]:
    """Return the index table as {linked file name: take-away}."""
    rows = {}
    for line in _INDEX.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\|\s*\[`([^`]+)`\]\(([^)]+)\)\s*\|(.+)\|\s*$", line)
        if match:
            name, target, takeaway = match.groups()
            assert name == target, (
                f"the index row for {name} links to {target}; the link text and "
                "the target must be the same file"
            )
            rows[name] = takeaway.strip()
    return rows


def test_the_index_lists_every_committed_reflection_and_nothing_else() -> None:
    """The README index and the directory must agree, both directions."""
    rows = _index_rows()
    assert len(rows) >= 2, f"read only {len(rows)} index rows; the reader is broken"

    committed = {
        path.name for path in _REFLECTIONS.glob("*.md") if path.name != "README.md"
    }
    assert rows.keys() == committed, (
        "docs/reflections/README.md's index and the directory disagree: "
        f"indexed but absent {sorted(rows.keys() - committed)}, "
        f"committed but unindexed {sorted(committed - rows.keys())}"
    )


def test_this_reflection_is_indexed_with_a_take_away() -> None:
    """The index row exists and says what the reader gets, not just a title."""
    takeaway = _index_rows()[_DOC.name]
    assert len(takeaway.split()) >= 5, (
        f"the index take-away for {_DOC.name} is {takeaway!r}; the README asks "
        "for what the reader should take away"
    )


def test_the_file_name_follows_the_rule_the_readme_states() -> None:
    """README: dated reflections are `YYYY-MM-DD-slug.md`, durable ones a slug."""
    index_text = _flat(_INDEX.read_text(encoding="utf-8"))
    assert "`YYYY-MM-DD-slug.md` for dated ones and a plain slug" in index_text, (
        "docs/reflections/README.md no longer states the naming rule this test "
        "enforces; re-read it and update this module"
    )

    dated = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$")
    slug = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.md$")
    for name in _index_rows():
        assert dated.match(name) or slug.match(name), (
            f"{name} matches neither naming form the README allows"
        )

    assert not dated.match(_DOC.name), (
        f"{_DOC.name} now carries a date prefix; it is filed as durable "
        "knowledge, and a dated record is read differently"
    )


def test_the_document_opens_with_its_take_away() -> None:
    """README: start with what the reader should take away, not a narrative."""
    body = _TEXT.split("\n", 1)[1].lstrip()
    assert body.startswith("**Take-away.**"), (
        "the document no longer opens with its take-away; the README asks for "
        f"that first, and it now opens {body[:60]!r}"
    )


# ------------------------------------------------- the connection layer


def test_the_socket_host_is_the_one_the_document_names() -> None:
    """The host in the prose must be the host `SOCKET_URL` points at."""
    hosts = set(re.findall(r"`([a-z0-9.-]+\.(?:io|com|net))`", _FLAT))
    assert len(hosts) == 1, f"expected one backticked host in the document, got {hosts}"

    documented = hosts.pop()
    actual = urlparse(client_module.SOCKET_URL).netloc
    assert documented == actual, (
        f"the document says the client connects to {documented}; SOCKET_URL is "
        f"{client_module.SOCKET_URL}"
    )


def test_the_getter_events_the_document_names_are_the_ones_the_client_emits() -> None:
    """`get_info` / `get_capabilities` are named in prose and emitted in code."""
    wait_for_devices = _function(_module(_CLIENT), "wait_for_devices")
    emitted = set(_string_args(wait_for_devices, "_send_event"))
    assert emitted, "no `_send_event` call with a literal event name was found"

    documented = set(re.findall(r"`(get_[a-z_]+)`", _FLAT))
    assert documented == emitted, (
        "the document and `wait_for_devices` disagree about the per-device "
        f"getters: document {sorted(documented)}, code {sorted(emitted)}"
    )


def test_the_response_events_the_document_names_are_the_ones_the_client_awaits() -> (
    None
):
    """`state`, `info` and `capabilities` are what `_on_event` dispatches on."""
    on_event = _function(_module(_CLIENT), "_on_event")
    dispatched = {
        node.value
        for node in ast.walk(on_event)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert {"state", "info", "capabilities"} <= dispatched, (
        "`_on_event` no longer dispatches on the three events the document "
        f"describes; it compares against {sorted(dispatched)}"
    )

    for event in ("state", "info", "capabilities"):
        assert f"`{event}`" in _FLAT, (
            f"`{event}` is dispatched by `_on_event` but the document's account "
            "of the connection layer no longer mentions it"
        )


def test_the_emit_loop_still_drains_the_queue() -> None:
    """The document describes "a background emit loop draining a queue"."""
    assert "a background emit loop draining a queue" in _FLAT
    emit_loop = _function(_module(_CLIENT), "_emit_loop")
    called = _called_attributes(emit_loop)
    assert "get_nowait" in called, (
        "`_emit_loop` no longer drains the queue with `get_nowait`; it calls "
        f"{sorted(called)}"
    )


def test_the_reconnect_path_still_refreshes_an_expired_token() -> None:
    """The document: "a reconnect path that refreshes an expired token"."""
    assert "refreshes an expired token" in _FLAT
    connect = _function(_module(_CLIENT), "_connect")
    called = _called_attributes(connect) | {
        node.func.id
        for node in ast.walk(connect)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "try_refresh_access_token" in called, (
        f"`_connect` no longer refreshes the access token; it calls {sorted(called)}"
    )
    assert "is_token_expired" in called, (
        "`_connect` no longer decides on token expiry, so the document's "
        "account of the reconnect path is no longer what the code does"
    )


def test_the_client_registers_handlers_the_way_the_document_says() -> None:
    """It "registers handlers with `@sio.event` and `@sio.on("*")`"."""
    assert '`@sio.event` and `@sio.on("*")`' in _FLAT

    connect = _function(_module(_CLIENT), "_connect")
    by_event = set()
    catch_all = []
    for node in ast.walk(connect):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Attribute) and decorator.attr == "event":
                by_event.add(node.name)
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "on"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                catch_all.append(decorator.args[0].value)

    assert by_event == {"connect", "connect_error", "disconnect"}, (
        f"`@sio.event` now registers {sorted(by_event)}; the document's account "
        "of what a MagicMock socket would have to fire is out of date"
    )
    assert catch_all == ["*"], (
        f'the catch-all registration is now {catch_all}, not the `sio.on("*")` '
        "the document names"
    )


# ------------------------------------------------------------- the fake


def _documented_fake() -> tuple[str, str]:
    """Return the fake's class name and module path as the document gives them."""
    match = re.search(r"`(\w+)` in `([\w./]+\.py)`", _FLAT)
    assert match, "the document no longer says where the fake lives"
    return match.group(1), match.group(2)


def test_the_fake_lives_where_the_document_says() -> None:
    """The class name and module path in the prose must both resolve."""
    class_name, module_path = _documented_fake()

    path = _ROOT / module_path
    assert path.is_file(), f"the document points at {module_path}, which is absent"

    classes = {
        node.name for node in ast.walk(_module(path)) if isinstance(node, ast.ClassDef)
    }
    assert class_name in classes, (
        f"the document says {class_name} is in {module_path}; that module "
        f"defines {sorted(classes)}"
    )
    assert FakeSensiBackend.__name__ == class_name, (
        "the fake the end-to-end tier actually uses is "
        f"{FakeSensiBackend.__name__}, not the {class_name} the document names"
    )
    assert FakeSensiBackend.__module__.replace(".", "/") + ".py" == module_path


async def test_connect_accepts_and_schedules_the_initial_state_event() -> None:
    """Bullet one: connect, call the `connect` handler, *schedule* `state`."""
    backend = FakeSensiBackend([load_sample("sample.json")])
    socket, connects, events = _wired_socket(backend)

    await socket.connect("https://example.invalid")

    assert connects == [None], "the registered `connect` handler was not called"
    assert events == [], (
        "the initial `state` event was delivered inline; the document's "
        "ordering argument requires it to be scheduled as a task, because the "
        "client only creates the future it waits on after connect() returns"
    )

    await _drain(backend)
    assert [name for name, _ in events] == ["state"], (
        f"expected the scheduled task to deliver `state`, got {events}"
    )
    assert events[0][1] == backend.state_payload()
    await backend.shutdown()


@pytest.mark.parametrize(
    ("emitted", "expected"),
    [("get_info", "info"), ("get_capabilities", "capabilities")],
)
async def test_the_getters_schedule_the_events_the_document_names(
    emitted: str, expected: str
) -> None:
    """Bullets two and three: `get_info` -> `info`, `get_capabilities` -> `capabilities`."""
    device = load_sample("sample.json")
    backend = FakeSensiBackend([device])
    socket, _, events = _wired_socket(backend)

    await socket.emit(emitted, {"icd_id": device["icd_id"]})
    assert events == [], f"{emitted} answered inline rather than as a task"

    await _drain(backend)
    assert [name for name, _ in events] == [expected], (
        f"{emitted} should schedule an `{expected}` event; saw {events}"
    )
    assert events[0][1]["icd_id"] == device["icd_id"]
    await backend.shutdown()


async def test_a_setter_emit_invokes_the_ack_callback() -> None:
    """Bullet four: `emit("set_temperature")` invokes the ack callback."""
    device = load_sample("sample.json")
    backend = FakeSensiBackend([device])
    socket, _, _ = _wired_socket(backend)
    acks: list[tuple] = []

    await socket.emit(
        "set_temperature",
        {"icd_id": device["icd_id"], "target_temp": 71, "mode": "heat"},
        None,
        lambda *args: acks.append(args),
    )
    assert acks == [], "the ack was invoked inline rather than as a task"

    await _drain(backend)
    assert len(acks) == 1, f"the ack callback was invoked {len(acks)} times"

    # The ack is compared against the request rather than against `ack_for`'s
    # own return value: a backend that stopped scripting `set_temperature` and
    # fell back to its empty success ack would match itself either way, and the
    # round-trip the document describes would be untested.
    error, payload = acks[0]
    assert error is None, f"the scripted setter ack reported an error: {error}"
    assert payload["target_temp"] == 71 and payload["mode"] == "heat", (
        "the scripted ack no longer echoes the emitted setter payload, so a "
        f"setter round-trip cannot be asserted on it: {payload}"
    )
    await backend.shutdown()


def test_the_document_names_every_getter_the_backend_scripts() -> None:
    """The bullet list and `responses_for` must agree, both directions."""
    responses_for = _function(_module(_E2E_CONFTEST), "responses_for")
    scripted = {
        node.value
        for node in ast.walk(responses_for)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("get_")
    }
    documented = set(re.findall(r"`emit\(\"(get_[a-z_]+)\"\)`", _FLAT))
    assert scripted == documented, (
        "the document's bullets and the backend's scripted getters disagree: "
        f"document {sorted(documented)}, backend {sorted(scripted)}"
    )


# ----------------------------------------------------- the ordering claim


def test_create_event_future_has_no_await_points() -> None:
    """The claim the whole end-to-end tier rests on, first half.

    "`_create_event_future` is an `async def` with no awaits inside it". If a
    refactor adds one, the scheduled deliveries can run before the client is
    waiting and the end-to-end tests start flaking.
    """
    assert "with no awaits inside it" in _FLAT

    node = _function(_module(_CLIENT), "_create_event_future")
    assert isinstance(node, ast.AsyncFunctionDef), (
        "`_create_event_future` is no longer an `async def`"
    )

    suspension_points = [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Await | ast.AsyncWith | ast.AsyncFor)
    ]
    assert not suspension_points, (
        "`_create_event_future` now suspends (line(s) "
        f"{sorted({child.lineno for child in suspension_points})}). The "
        "end-to-end fake delivers its scripted events as tasks and relies on "
        "this function not yielding before the client awaits the future; see "
        f"{_DOC.relative_to(_ROOT)}."
    )


def test_the_event_queue_is_constructed_unbounded() -> None:
    """The claim's second half: `put` on an unbounded queue does not suspend."""
    assert "on an unbounded queue does not\nsuspend" in _TEXT or (
        "on an unbounded queue does not suspend" in _FLAT
    )

    constructions = [
        node.value
        for node in ast.walk(_module(_CLIENT))
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "Queue"
    ]
    assert len(constructions) == 1, (
        f"expected exactly one asyncio.Queue construction, found {len(constructions)}"
    )
    call = constructions[0]
    assert not call.args and not call.keywords, (
        "the emit queue is now constructed with a bound; a bounded queue makes "
        "`put` a suspension point and breaks the ordering the end-to-end fake "
        "depends on"
    )


async def test_putting_on_an_unbounded_queue_does_not_suspend() -> None:
    """The behaviour the ordering argument asserts, checked rather than assumed."""
    queue = asyncio.Queue()
    ran: list[str] = []
    asyncio.get_running_loop().call_soon(ran.append, "scheduled")

    await queue.put(object())

    assert ran == [], (
        "awaiting `put` on an unbounded queue yielded to the loop, so a task "
        "scheduled before it ran first; the document's ordering argument no "
        "longer holds on this Python"
    )
    await asyncio.sleep(0)
    assert ran == ["scheduled"], "the check itself never let the loop run"


def test_the_client_creates_the_future_after_triggering_the_response() -> None:
    """The client creates the future it waits on after the triggering call."""
    assert "creates the future it waits on *after* the call" in _FLAT

    wait_for_devices = _function(_module(_CLIENT), "wait_for_devices")
    sends = [
        node
        for node in ast.walk(wait_for_devices)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_send_event"
    ]
    creates = [
        node
        for node in ast.walk(wait_for_devices)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_create_event_future"
    ]
    assert sends and creates, (
        f"`wait_for_devices` now makes {len(sends)} sends and {len(creates)} "
        "future creations; the reader found nothing to compare"
    )
    assert max(node.lineno for node in sends) < min(node.lineno for node in creates), (
        "`wait_for_devices` now creates a future before emitting the event "
        "that triggers its response. The end-to-end fake schedules its "
        "deliveries for the opposite order; see "
        f"{_DOC.relative_to(_ROOT)}."
    )


def test_the_flaking_warning_is_backed_by_the_two_absence_tests() -> None:
    """The warning is only worth printing if something enforces it."""
    assert "adds an `await` in that window, these tests will start flaking" in _FLAT
    for name in (
        "test_create_event_future_has_no_await_points",
        "test_the_event_queue_is_constructed_unbounded",
        "test_putting_on_an_unbounded_queue_does_not_suspend",
    ):
        assert name in globals(), (
            f"{name} is what gives the document's flaking warning teeth; it is "
            "gone from this module"
        )


# ---------------------------------------------------------------- the bug


def _quoted_guard() -> list[str]:
    """Return the python fence the document quotes, as stripped lines."""
    fences = re.findall(r"```python\n(.*?)```", _TEXT, re.DOTALL)
    assert len(fences) == 1, f"expected one python fence, found {len(fences)}"
    return [line.strip() for line in fences[0].splitlines() if line.strip()]


def test_the_quoted_snippet_is_the_broken_version_the_document_says_it_is() -> None:
    """The fence shows the guard *before* the fix, and the prose says so."""
    source = _CLIENT.read_text(encoding="utf-8")
    lines = _quoted_guard()

    assert "if not future.done():" in lines, (
        "the quoted snippet no longer shows the guard the document calls buggy; "
        f"it is now {lines}"
    )
    assert "if not future.done():" not in source, (
        "client.py carries the unguarded form the document says was the bug: a "
        "timed-out setter cancels the future, `done()` then reports True, and "
        "`future.result()` raises CancelledError past the entity layer"
    )

    for line in lines:
        if line == "if not future.done():":
            continue
        assert line in source, (
            f"the quoted snippet's line {line!r} is no longer in client.py, so "
            "the document quotes code that never existed in this shape"
        )


def test_the_setter_guard_still_carries_the_cancelled_check() -> None:
    """The document states the fix as adding `future.cancelled() or`."""
    fix = re.search(r"Adding `([^`]+)` to the guard fixes it", _FLAT)
    assert fix, "the document no longer states the fix this test enforces"
    assert fix.group(1) == "future.cancelled() or", (
        f"the document now says the fix is {fix.group(1)!r}; this module "
        "checks for the cancelled() check and must be re-read"
    )

    setter = _function(_module(_CLIENT), "_async_emit_setter")
    guards = [
        node
        for node in ast.walk(setter)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.BoolOp)
        and isinstance(node.test.op, ast.Or)
    ]
    assert len(guards) == 1, (
        f"`_async_emit_setter` has {len(guards)} `or` guards; expected the one "
        "the document describes"
    )

    operands = ast.dump(guards[0].test)
    assert "cancelled" in operands and "done" in operands, (
        "the timeout guard no longer tests both `cancelled()` and `done()`; "
        f"it is now `{ast.unparse(guards[0].test)}`"
    )
    returned = ast.unparse(guards[0].body[0])
    assert "Future not done" in returned, (
        f"the guard no longer returns the documented failure; it returns {returned}"
    )


def test_the_no_ack_behaviour_is_pinned_by_the_client_event_tests() -> None:
    """The bug's behaviour is owned elsewhere; assert the owner still has it."""
    tests = _CLIENT_EVENT_TESTS.read_text(encoding="utf-8")
    assert "test_no_ack_at_all_is_reported_as_a_failure" in tests, (
        "tests/test_client_events.py no longer carries the no-ack test this "
        "module defers to; the document's "
        '"no ack is reported as a failure" claim is now unchecked'
    )


def test_the_shutdown_generalisation_points_at_a_committed_correction() -> None:
    """The cited correction file must exist, and its own tests must still own it."""
    cited = re.findall(r"`(\.claude/memory/[\w.-]+\.md)`", _FLAT)
    assert len(cited) == 1, f"expected one cited correction file, found {cited}"

    path = _ROOT / cited[0]
    assert path.is_file(), (
        f"the document cites {cited[0]}, which is not committed; the "
        "generalisation it defers to has nowhere to live"
    )

    owner = _CORRECTION_TESTS.read_text(encoding="utf-8")
    assert path.name in owner, (
        f"tests/test_correction_memory.py no longer reads {path.name}, so "
        "nothing checks the correction this document defers to"
    )
    assert "_async_disconnect" in _INSTRUCTION_TESTS.read_text(encoding="utf-8"), (
        "tests/test_instruction_docs.py no longer pins `_async_disconnect`'s "
        "use of shutdown(); this module defers that claim to it"
    )


# ------------------------------------------- what it still does not prove


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"status": None, "display_scale": None},
        {"display_temp": "not a number", "humidity": "high"},
        {"operating_mode": "banana", "circulating_fan": None, "humidity_control": None},
    ],
    ids=["null", "empty", "null-containers", "junk-numbers", "unknown-enum"],
)
def test_the_parsing_layer_degrades_instead_of_raising(payload) -> None:
    """The parsing layer degrades instead of raising, as the document says."""
    assert "the parsing layer degrades instead of raising" in _FLAT

    state = State(payload)

    assert state.is_online is False
    assert str(state)


def test_an_unknown_operating_mode_becomes_the_unknown_member() -> None:
    """Degrading means a defined fallback, not an attribute that is missing."""
    assert State({"operating_mode": "banana"}).operating_mode is OperatingMode.UNKNOWN


def _fixture_keys(value, into: set[str]) -> None:
    """Collect every mapping key in a captured payload, at any depth."""
    if isinstance(value, dict):
        for key, child in value.items():
            into.add(key)
            _fixture_keys(child, into)
    elif isinstance(value, list):
        for child in value:
            _fixture_keys(child, into)


def test_unread_fields_are_kept_in_the_fixtures() -> None:
    """Unread fields are kept in the fixtures rather than trimmed."""
    assert "unread fields are kept in fixtures rather than trimmed" in _FLAT

    keys: set[str] = set()
    for sample in sorted(_ROOT.glob("tests/sample*.json")):
        _fixture_keys(json.loads(sample.read_text(encoding="utf-8")), keys)
    assert len(keys) > 50, f"read only {len(keys)} fixture keys; the reader is broken"

    component = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_COMPONENT.glob("*.py"))
    )
    unread = {key for key in keys if f'"{key}"' not in component}
    assert unread, (
        "every key in the captured payloads is now read by the component. The "
        "fixtures are captures of what the thermostat sent, and the document "
        "says the unread parts are kept deliberately - trimming them loses the "
        "record of what the protocol actually carries"
    )


def test_the_pull_request_template_still_asks_about_real_hardware() -> None:
    """The pull request template asks whether a change was run on real hardware."""
    assert "asks whether a change was exercised against real hardware" in _FLAT

    template = _PR_TEMPLATE.read_text(encoding="utf-8")
    hardware = [
        line
        for line in template.splitlines()
        if line.startswith("- [ ]") and "real thermostat" in line
    ]
    assert len(hardware) == 1, (
        ".github/pull_request_template.md no longer has an unchecked "
        "real-thermostat item; the document names it as the question CI can "
        f"never answer. Checklist items: {[line for line in template.splitlines() if line.startswith('- [')]}"
    )


# ------------------------------------------------ deliberately historical


_HISTORICAL = (
    "| `client.py` | 52% | 90% | **100%** |",
    "| repository total | 85% | 96% | 98% |",
    "Twelve tests, covering the handshake",
)


@pytest.mark.parametrize("claim", _HISTORICAL)
def test_the_numbers_stay_a_dated_record(claim: str) -> None:
    """The table says what two changes bought, not what the tree measures now.

    `docs/reflections/` is kept as a record rather than refreshed, so these
    numbers are deliberately joined to nothing. Pinning the framing is what
    stops a rewrite into a live claim from slipping past unnoticed: if one of
    these lines changes, the change has to come back through this module and
    decide whether the new wording is still historical.
    """
    assert claim in _FLAT, (
        f"{claim!r} is no longer in the document. If the coverage story was "
        "rewritten as a claim about the tree today, it needs joining to a "
        "coverage run rather than left as prose"
    )


def test_the_table_is_framed_as_before_and_after() -> None:
    """The columns are what make the numbers a record rather than a claim."""
    assert "| | before | after the fake | after the error paths |" in _TEXT, (
        "the coverage table's before/after framing is gone; without it the "
        "numbers read as a statement about the current tree"
    )
