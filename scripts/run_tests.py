#!/usr/bin/env python3
"""Run the test suite, refusing any target that is not part of it.

`.claude/settings.json` auto-approves the command that runs the tests, so what
that command can be pointed at is part of the security boundary rather than an
ergonomic detail. `pytest` reads `testpaths` from `pytest.ini` only when the
command line names no target, so an allow rule spelled `Bash(pytest:*)` also
approves `pytest` with any path after it - including a path outside this
repository, which leaves nothing in the diff for a reviewer to see.

This wrapper forwards to pytest unchanged except for one rule: every target it
is given must resolve inside `tests/`, and conftest discovery is pinned to the
repository so nothing above it is imported either. Running agent-written code
is still possible, because that is what a test suite is. The point is that the
code has to be a file in the tree, where `git status` shows it and review
reaches it.
"""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


# Options that load code, or load the settings that load code. `-p` imports a
# plugin module before collection, `--pyargs` reinterprets targets as dotted
# module names so checking them as paths stops meaning anything, `-c` chooses
# the ini file whose `addopts` pytest then applies, `-o` sets that ini option
# inline, and `--confcutdir` widens the directories pytest imports `conftest.py`
# from - `--confcutdir=/` reaches a conftest in any ancestor of the repository.
# None of them names a path the target check below would see.
#
# Keep this list short and keep the reason with each entry. An option that
# reaches code and is not here is a bug in the list, not in the target check.
REFUSED_LONG = frozenset(
    {"--pyargs", "--config-file", "--override-ini", "--confcutdir"}
)
REFUSED_SHORT = frozenset({"-p", "-c", "-o"})

# pytest defaults `confcutdir` to the directory holding the ini file, which is
# ROOT here - but that is a default, and the wrapper's guarantee should not rest
# on one. Pinning it means a conftest.py above the repository is never imported
# on the wrapper's account, whatever pytest.ini says or stops saying.
PINNED = (f"--confcutdir={ROOT}",)


def _refused_option(arg: str) -> bool:
    """Whether `arg` reaches code by a route the target check cannot see.

    Both spellings of each short option: `-p name` and `-pname`, `-c file` and
    `-cfile`. The long forms take their value with `=`.
    """

    if arg.split("=", 1)[0] in REFUSED_LONG:
        return True
    if arg.startswith("--"):
        return False
    return arg[:2] in REFUSED_SHORT


def refusals(argv: list[str]) -> list[str]:
    """Return one message per argument this wrapper will not forward.

    A bare argument is checked only when it resolves to something that exists.
    Option values arrive as separate arguments - `-k redact` - and cannot be
    told from targets by shape, but a value that is not a path executes
    nothing, and a target that does not exist makes pytest exit rather than
    run. What is left is the case that matters: an existing file or directory
    outside `tests/`.
    """

    problems = []
    for arg in argv:
        if arg.startswith("-"):
            if _refused_option(arg):
                problems.append(
                    f"{arg}: reaches code by a route the target check cannot see"
                )
            continue
        target = Path(arg.split("::", 1)[0])
        resolved = (target if target.is_absolute() else ROOT / target).resolve()
        if not resolved.exists():
            continue
        if resolved != TESTS and TESTS not in resolved.parents:
            problems.append(f"{arg}: resolves to {resolved}, outside {TESTS}")
    return problems


def main(argv: list[str]) -> int:
    """Forward to pytest, or refuse every argument at once and explain."""

    problems = refusals(argv)
    if problems:
        for problem in problems:
            print(f"run_tests: refusing {problem}", file=sys.stderr)
        print(
            "run_tests: this wrapper is what .claude/settings.json allows to "
            "run without a prompt; run pytest directly if you mean it",
            file=sys.stderr,
        )
        return 2
    command = [sys.executable, "-m", "pytest", *PINNED, *argv]
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
