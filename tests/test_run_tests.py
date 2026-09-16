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
        ["--pdb"],
        ["-k", "redact"],
    ],
    ids=[
        "bare",
        "the suite directory",
        "one module",
        "one node id",
        "a subdirectory",
        "options with no target",
        "an option that starts with -p only as --",
        "a keyword filter",
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
    ],
)
def test_an_option_that_loads_code_is_refused(argv: list[str]) -> None:
    """These reach code without naming a path the target check can see."""

    assert run_tests.refusals(argv), (
        f"{argv} reaches an importable module or the ini that names one"
    )


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


def test_the_script_exists_and_is_executable() -> None:
    """An allow rule naming a missing or unrunnable path guards nothing.

    That the path is *tracked* is asserted in `tests/test_agent_format_hook.py`,
    where every path the allow and ask lists name is resolved against git.
    """

    assert _SCRIPT.is_file()
    assert _SCRIPT.stat().st_mode & 0o111, f"{_SCRIPT} is not executable"
