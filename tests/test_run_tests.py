"""Tests for scripts/run_tests.py.

This script is not part of the component, so the coverage gate does not measure
it - but `.claude/settings.json` allows it to run without a permission prompt,
which makes what it agrees to run a boundary in the same sense the PostToolUse
hook is one (#173). The assertion that matters is the refusal: a target outside
`tests/` must not reach pytest, because a file outside the tree is executed
with nothing left in the diff for a reviewer to see.

The refusal logic is pure, so these tests call `refusals` directly rather than
running the suite inside the suite. One test calls `main` as well, because a
refusal that the entry point never consults is a pure function and not a gate.
"""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "run_tests.py"
_spec = importlib.util.spec_from_file_location("run_tests", _SCRIPT)
run_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_tests)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["tests"],
        ["tests/test_utils.py"],
        ["tests/test_utils.py::TestRedactToken"],
        ["tests/e2e"],
        ["-q", "--cov=custom_components/sensi"],
        ["-k", "redact"],
        ["--cov=custom_components.sensi", "--cov-report=term-missing"],
        ["--cov-report", "term"],
        ["--cov-report=term-missing:skip-covered"],
        ["--cov-report", "term:skip-covered"],
        ["--cov-report=xml"],
        ["-x", "tests"],
    ],
    ids=[
        "bare",
        "the suite directory",
        "one module",
        "one node id",
        "a subdirectory",
        "options with no target",
        "a keyword filter",
        "the documented coverage command",
        "a terminal coverage report given as two arguments",
        "a terminal report with the skip-covered modifier",
        "the skip-covered modifier given as two arguments",
        "an xml report written where .coveragerc says",
        "stop on first failure",
    ],
)
def test_an_argument_inside_the_suite_is_forwarded(argv: list[str]) -> None:
    """Everything the repository actually runs has to keep working."""

    assert run_tests.refusals(argv) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["/etc/passwd"],
        ["tests/../custom_components/sensi/auth.py"],
        ["custom_components"],
        ["scripts/run_tests.py"],
        ["-c", "/etc/hosts"],
    ],
    ids=[
        "an absolute path outside the repository",
        "traversal back out through tests/",
        "the component",
        "this script",
        "an ini file outside the repository",
    ],
)
def test_a_target_outside_the_suite_is_refused(argv: list[str]) -> None:
    """The refusal is on the RESOLVED path, so traversal does not get through."""

    assert run_tests.refusals(argv), (
        f"{argv} resolves outside tests/ and would be executed by an allowed, "
        "unprompted command"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["-p", "evil"],
        ["-pevil"],
        ["--pyargs"],
        ["-o", "addopts=-p evil"],
        ["--override-ini=addopts=-p evil"],
        ["--config-file=/tmp/evil.ini"],
        ["--confcutdir=/"],
        ["--confcutdir", "/"],
        ["-xp", "evil"],
        ["-xpevil"],
        ["-xc", "/tmp/evil.ini"],
        ["-sxo", "addopts=-p evil"],
        # pytest-xdist's `--tx popen//python=PROG` runs PROG as a distributed
        # worker's interpreter; the value is not a path the target check sees.
        ["--tx", "popen//python=/tmp/evil"],
        ["--tx=popen//python=/tmp/evil"],
        ["-d", "--tx", "popen//python=/tmp/evil", "tests"],
        # `--px` adds a proxy gateway, which pytest-xdist makes before any
        # worker and whether or not a `--tx` names it; `-n 1` turns
        # distribution on without `--tx`, so this ran PROG and then the suite.
        ["-n", "1", "--px", "id=p//popen//python=/tmp/evil", "tests"],
        ["--px=id=p//popen//python=/tmp/evil"],
        # pytest-picked appends `--parent-branch` to `git diff` as its last
        # word, so a value starting with `-` is a git option: this truncated
        # the settings file. `--picked` swaps in paths from `git status`
        # after the target check, so a test file outside `tests/` ran.
        [
            "--picked",
            "--mode=branch",
            "--parent-branch=--output=.claude/settings.json",
        ],
        ["--parent-branch", "master", "tests"],
        ["--picked"],
        ["--picked=first", "tests"],
        # pytest's debugger options run code interactively instead of by name.
        # `--trace` breaks into pdb at the start of every test and `--pdb` on
        # the first failure; a pdb prompt runs any statement read from stdin,
        # so `printf '!PROG\nc\n' | run_tests.py --trace tests` ran PROG before
        # the first test body. `--pdbcls` names an importable dotted path.
        ["--trace", "tests"],
        ["--trace"],
        ["--pdb", "tests"],
        ["--pdb"],
        ["--pdbcls=IPython.terminal.debugger:TerminalPdb"],
        ["--pdbcls", "mod:Cls"],
    ],
)
def test_an_option_that_loads_code_is_refused(argv: list[str]) -> None:
    """These reach code without naming a path the target check can see.

    `--confcutdir=/` is the one that is not an import by name: it tells pytest
    to import `conftest.py` from every ancestor of the target, so a file in
    `/` or in the directory above the checkout runs before collection with
    nothing in the tree to show for it.

    The bundled spellings are here because argparse expands `-xc file` to
    `-x -c file`, so a refusal that reads only the first letter of the
    argument sees the `-x` and forwards the rest.
    """

    assert run_tests.refusals(argv), (
        f"{argv} reaches an importable module or the ini that names one"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--junitxml=.claude/settings.json"],
        ["--junit-xml", "/tmp/pwned.xml"],
        ["--log-file=scripts/run_tests.py"],
        ["--debug=/tmp/pwned.log"],
        ["--report-log=/tmp/pwned.jsonl"],
        ["--basetemp=.claude/hooks"],
        ["--basetemp", "/tmp/victim"],
        ["--rootdir=/tmp"],
        ["--rootdir", ".claude"],
        ["--snapshot-dirname=/tmp/pwned"],
        ["--snapshot-dirname", "../../.claude"],
        ["--cov-report=xml:/tmp/pwned.xml"],
        ["--cov-report", "html:/tmp/pwned"],
        ["--cov-report=lcov:docs/SECURITY-AI.md"],
        ["--cov-report=annotate:.github/workflows"],
        ["--cov-report=json:/tmp/pwned.json"],
        ["--cov-report=markdown:.claude/settings.json"],
        ["--cov-config=/tmp/coverage.ini", "--cov-report=xml"],
        ["--cov-config", "tests/coverage.ini"],
    ],
)
def test_an_option_that_writes_a_path_is_refused(argv: list[str]) -> None:
    """These create, truncate or delete a path the target check never sees.

    The target check reads only arguments that do not start with `-`, so an
    option carrying its own path is invisible to it, and nothing requires the
    path to be inside `tests/` or inside the repository. `--junitxml` is
    written even when collection fails, so no test has to run; `--basetemp`
    deletes the directory recursively before pytest uses it; `--rootdir`
    puts `.pytest_cache/` inside whatever directory it names. Every one of
    them reaches the `Edit` deny list in `.claude/settings.json` from a
    command that list allows without a prompt.

    `--cov-report` keeps its documented spelling, `term-missing`, and the
    `:skip-covered` modifier a terminal type can carry. A `:` after a file
    type is a destination, and those are refused whether the value is
    attached with `=` or given as the next argument.

    `--cov-config` is refused in both spellings because the file it names is
    where a report's destination is decided: `.coveragerc` sets `[xml]
    output`, so a config of the caller's own can send `--cov-report=xml`
    anywhere without a `:` appearing on the command line. The two-argument
    case names a path inside `tests/` on purpose - the target check would
    forward that path, so the option itself has to be the refusal.
    """

    assert run_tests.refusals(argv), (
        f"{argv} writes or deletes a path of its own choosing"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--pastebin=all"],
        ["--pastebin=failed", "tests"],
        ["--pastebin", "all"],
        ["tests", "--pastebin", "failed"],
    ],
)
def test_an_option_that_uploads_the_session_log_is_refused(argv: list[str]) -> None:
    """`--pastebin` POSTs the whole session log to https://bpa.st.

    That log carries the absolute rootdir, the plugin list, and every
    failure's traceback with its locals. docs/SECURITY-AI.md forbids posting
    logs or fixtures to a pastebin, and this wrapper runs without the prompt
    that would otherwise catch it, so the option itself has to be refused -
    in both the `=` and the two-argument spelling.
    """

    refused = run_tests.refusals(argv)
    assert refused, f"{argv} uploads the session log"
    assert any("paste service" in problem for problem in refused)


def test_every_pytest_option_is_forwarded_or_refused(
    pytestconfig: pytest.Config,
) -> None:
    """Claim: no option the installed plugins accept is left unreviewed.

    The refused sets are a denylist, and a denylist misses what nobody read:
    ten follow-up commits each closed a route the lists had missed, `--rootdir`
    the latest. This reads the live parser - pytest's own options and every
    plugin's - so an option that a dependency bump adds is seen the first time
    the suite runs with it, and has to be put on one side or the other.
    `FORWARDED` lives in the wrapper rather than here; see its comment.

    `_parser.optparser` is private to pytest. If an upgrade renames it, this
    test errors rather than passing.
    """

    parser = pytestconfig._parser.optparser
    live = {name for action in parser._actions for name in action.option_strings}
    refused = (
        run_tests.REFUSED_LONG
        | run_tests.REFUSED_SHORT
        | run_tests.REFUSED_WRITE
        | run_tests.REFUSED_CONFIG
        | run_tests.REFUSED_SEND
        | run_tests.VALUED_WRITE
    )
    assert not run_tests.FORWARDED & refused, "an option cannot be on both sides"
    unreviewed = live - run_tests.FORWARDED - refused
    assert not unreviewed, f"forward or refuse in run_tests.py: {sorted(unreviewed)}"


@pytest.mark.parametrize(
    "argv",
    [
        ["@.env"],
        ["@notes.txt"],
        ["tests", "@args.txt"],
        ["-k", "@args.txt"],
        ["--cov-report", "@args.txt"],
    ],
    ids=[
        "a read-denied file",
        "a file that does not exist yet",
        "after a target inside the suite",
        "as an option's value",
        "as the value of the one valued write option",
    ],
)
def test_an_argument_file_is_refused(argv: list[str]) -> None:
    """An `@FILE` argument becomes the file's lines before pytest parses any.

    Every other check here reads the words it is handed, and `@notes.txt` is
    neither an option nor an existing path, so a file holding
    `--junitxml=.claude/settings.json` or a target outside `tests/` reached
    pytest unread. `@.env` was a read as well: pytest reports the first line
    that is not a target as "file or directory not found", which printed the
    first line of a file the Read deny rules withhold. argparse expands the
    prefix wherever the argument sits, so the value position is refused too.
    """

    assert run_tests.refusals(argv), f"{argv} hands pytest arguments nobody checked"


def test_a_directory_above_the_repository_is_refused() -> None:
    """Spelled relatively, so the check has to resolve before it compares."""

    assert run_tests.refusals([".."])


def test_a_target_that_does_not_exist_is_left_to_pytest() -> None:
    """Deliberate: pytest exits on an unknown target, so nothing runs anyway.

    Not checking it is what lets an option value through - `-k redact` passes
    `redact` as its own argument, and it is indistinguishable from a target by
    shape alone.
    """

    assert run_tests.refusals(["/nonexistent/evil.py"]) == []


def test_a_value_that_is_not_a_path_is_not_mistaken_for_a_target() -> None:
    """`-k redact` passes `redact` as its own argument; it executes nothing."""

    assert run_tests.refusals(["-k", "redact", "-m", "not slow"]) == []


def test_every_refused_argument_is_reported_not_just_the_first() -> None:
    """A caller fixing one refusal at a time learns nothing about the rest."""

    problems = run_tests.refusals(["--pyargs", "/etc/passwd", "tests"])
    assert len(problems) == 2


def test_main_refuses_before_it_reaches_pytest(capsys: pytest.CaptureFixture) -> None:
    """The gate is `main`, not `refusals`; a refusal it ignores is not one.

    No pytest subprocess starts here, which is the assertion: `main` returns
    the refusal exit code and the target is never collected.
    """

    assert run_tests.main(["/etc/passwd"]) == 2

    stderr = capsys.readouterr().err
    assert "/etc/passwd" in stderr
    assert "run pytest directly if you mean it" in stderr


def test_main_pins_conftest_discovery_to_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command `main` runs carries `--confcutdir=<repo>` ahead of argv.

    pytest defaults `confcutdir` to the ini file's directory, which is the
    repository root - but the wrapper's guarantee that nothing above the
    checkout is imported should not rest on a default. It is asserted against
    the argv handed to `subprocess.run`, which is the only place it exists.
    """

    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        seen.append(command)
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(run_tests.subprocess, "run", fake_run)

    assert run_tests.main(["-q", "tests"]) == 0
    assert len(seen) == 1
    command = seen[0]
    assert f"--confcutdir={run_tests.ROOT}" in command
    assert command.index(f"--confcutdir={run_tests.ROOT}") < command.index("-q"), (
        "the pin is the wrapper's, not the caller's, so it goes ahead of argv"
    )
    assert command[-2:] == ["-q", "tests"], "argv must be forwarded unchanged"


def test_the_script_exists_and_is_executable() -> None:
    """An allow rule naming a missing or unrunnable path guards nothing.

    That the path is *tracked* is asserted in `tests/test_agent_format_hook.py`,
    where every path the allow and ask lists name is resolved against git.
    """

    assert _SCRIPT.is_file()
    assert _SCRIPT.stat().st_mode & 0o111, f"{_SCRIPT} is not executable"
