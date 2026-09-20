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

# Options that name a path pytest creates, truncates, or deletes. The target
# check below inspects only arguments that do not start with `-`, so an option
# carrying its own path is invisible to it, and the path does not have to be
# inside `tests/` - or inside the repository. `--basetemp` is the sharpest of
# them: pytest `rm_rf`s the directory before it uses it.
#
# Same rule as the list above - an option that writes a path and is not here is
# a bug in the list.
REFUSED_WRITE = frozenset(
    {"--junitxml", "--junit-xml", "--log-file", "--debug", "--basetemp", "--report-log"}
)

# `--cov-report` is the one of these that has a legitimate spelling: AGENTS.md
# documents `--cov-report=term-missing`. Only the destination forms write a
# path, and they are the ones carrying a `:` - `xml:out.xml`, `html:dir`,
# `lcov:out.info`, `annotate:dir`.
VALUED_WRITE = frozenset({"--cov-report"})

# pytest defaults `confcutdir` to the directory holding the ini file, which is
# ROOT here - but that is a default, and the wrapper's guarantee should not rest
# on one. Pinning it means a conftest.py above the repository is never imported
# on the wrapper's account, whatever pytest.ini says or stops saying.
PINNED = (f"--confcutdir={ROOT}",)


def _refused_option(arg: str) -> bool:
    """Whether `arg` reaches code by a route the target check cannot see.

    argparse expands a bundled short group, so `-xp evil` is `-x -p evil` and
    `-xpevil` is `-x -p evil` again. A check that reads only the first two
    characters sees the `-x` and forwards the rest, which is how `-c` and `-p`
    got through. Every letter in a group is an option until one of them takes
    a value and swallows the remainder, and which letters do that is pytest's
    table rather than anything this wrapper can know - so a refused letter
    anywhere in the group refuses the whole argument.

    The cost of that is a value written attached to its own option: `-kcov` is
    refused because of the `c`. Write it as two arguments, `-k cov`, which
    this check reads as an option and a value it does not inspect. The long
    forms take their value with `=`.
    """

    name = arg.split("=", 1)[0]
    if name in REFUSED_LONG or name in REFUSED_WRITE:
        return True
    if arg.startswith("--"):
        return False
    return any(f"-{letter}" in REFUSED_SHORT for letter in arg[1:])


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
    pending = ""
    for arg in argv:
        # A `--cov-report` destination arrives either attached with `=` or as
        # the next argument, and only the destination forms carry a `:`.
        name, _, attached = arg.partition("=")
        value = attached if name in VALUED_WRITE else (arg if pending else "")
        pending = name if name in VALUED_WRITE and not attached else ""
        if value and ":" in value:
            problems.append(f"{arg}: writes a report to a path of its own")
            continue

        if arg.startswith("-"):
            if _refused_option(arg):
                why = (
                    "creates, truncates or deletes a path of its own"
                    if arg.partition("=")[0] in REFUSED_WRITE
                    else "reaches code by a route the target check cannot see"
                )
                problems.append(f"{arg}: {why}")
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
