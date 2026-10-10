#!/usr/bin/env python3
"""Run the test suite, refusing any target that is not part of it.

`.claude/settings.json` auto-approves the command that runs the tests, so what
that command can be pointed at is part of the security boundary rather than an
ergonomic detail. `pytest` reads `testpaths` from `pytest.ini` only when the
command line names no target, so an allow rule spelled `Bash(pytest:*)` also
approves `pytest` with any path after it - including a path outside this
repository, which leaves nothing in the diff for a reviewer to see.

This wrapper forwards to pytest with three rules. Every target it is given
must resolve inside `tests/`, and conftest discovery is pinned to the repository
so nothing above it is imported either. The options that load code by a route
the target check cannot see, or run it in an interactive debugger reading
stdin (`-p`, `-c`, `-o`, `--pyargs`, `--confcutdir`, `--tx`, `--px`,
`--picked`, `--parent-branch`, `--trace`, `--pdb`, `--pdbcls`) are refused. And the options
that create, truncate or delete a path of their own (`--junitxml`,
`--log-file`, `--basetemp` and the rest of `REFUSED_WRITE`, `--cov-config`, and
the `--cov-report` destination forms such as `xml:DEST`) are refused too,
because an option's path is not a target and does not have to be inside the
repository. Before any of that, an argument starting with `@` is refused:
pytest replaces it with the lines of the file it names, so every argument in
that file would reach pytest without passing the three rules. `--pastebin` is
refused the same way: it uploads the session log to a public paste service,
which docs/SECURITY-AI.md rules out.
Running agent-written code is still possible, because that is what a test
suite is. The point is that the code has to be a file in the tree, where
`git status` shows it and review reaches it.
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
# `--tx` is not pytest's own; it is pytest-xdist's, and pytest-xdist arrives
# as a pinned dependency of pytest-homeassistant-custom-component, so it is
# always installed when the suite runs. `--tx` names the gateway a distributed
# worker runs under, and `--tx popen//python=PROG` makes that worker's
# interpreter PROG - any program on disk - which pytest-xdist then executes
# once distributed mode is on (`-d`, `--dist`). `python3 scripts/run_tests.py
# -d --tx popen//python=PROG tests` therefore runs PROG with no path on the
# command line the target check could see and no permission prompt. The value
# is not a path this check inspects, so the option itself has to be refused;
# refusing it leaves the default gateway, which runs this same interpreter.
#
# `--px` is the same option under another name. It adds a proxy gateway, and
# pytest-xdist makes every proxy gateway the moment it sets up distribution -
# before any worker, whether or not a `--tx` ever names it with `via=`. So
# `--px id=p//popen//python=PROG` runs PROG too, and it does not need `--tx` to
# turn distribution on: `-n 1` does that by itself. `python3 scripts/run_tests.py
# -n 1 --px id=p//popen//python=PROG tests` ran PROG and then the suite, exit 0.
#
# `--picked` and `--parent-branch` are pytest-picked's, another pinned
# dependency of the same harness. `--picked` replaces the targets with the
# paths `git status` (or, with `--mode=branch`, `git diff`) reports, after the
# target check has run, so a changed test file outside `tests/` runs as if it
# had been named. `--parent-branch` is appended to that `git diff` as the last
# word, and git reads a word starting with `-` as an option: `python3
# scripts/run_tests.py --picked --mode=branch
# --parent-branch=--output=.claude/settings.json` truncated the settings file
# and wrote git's name-status list into it, and the same value naming the gate
# hook left a hook that no longer parses. `--parent-branch` is refused as well
# as `--picked` because the git call is the harm, whichever option enables it.
#
# `--trace`, `--pdb` and `--pdbcls` are pytest's own, and they run code a
# different way: not by importing a module named on the command line, but by
# dropping the run into an interactive debugger that executes whatever Python it
# reads from standard input. `--trace` breaks into `pdb` at the start of every
# test, so it needs no failure; `--pdb` breaks in on the first error, which is
# trivial to force. At a `(Pdb)` prompt the `!` prefix runs any statement, so
# `printf '!PROG\nc\n' | python3 scripts/run_tests.py --trace tests` ran PROG
# before the first test body with nothing written to the tree for review to
# see. `--pdbcls=module:classname` names an importable dotted path, the same
# code-by-name route as `--pyargs`. None of the three names a path the target
# check sees, and no CI or documented command uses any of them, so refusing
# them costs the suite nothing. (`--trace-config` only prints conftest
# discovery and is not a debugger - it is not refused.)
#
# Keep this list short and keep the reason with each entry. An option that
# reaches code and is not here is a bug in the list, not in the target check.
REFUSED_LONG = frozenset(
    {
        "--pyargs",
        "--config-file",
        "--override-ini",
        "--confcutdir",
        "--tx",
        "--px",
        "--picked",
        "--parent-branch",
        "--trace",
        "--pdb",
        "--pdbcls",
    }
)
REFUSED_SHORT = frozenset({"-p", "-c", "-o"})

# Options that name a path pytest creates, truncates, or deletes. The target
# check below inspects only arguments that do not start with `-`, so an option
# carrying its own path is invisible to it, and the path does not have to be
# inside `tests/` - or inside the repository. `--basetemp` is the sharpest of
# them: pytest `rm_rf`s the directory before it uses it.
#
# `--rootdir` belongs here although it reads like a setting: pytest keeps its
# cache under the root directory, so `--rootdir=DIR` creates `.pytest_cache/`
# (a README, a `.gitignore`, `CACHEDIR.TAG` and `v/cache/*`) inside any existing
# DIR, and `--cache-clear` then deletes that folder there. `--snapshot-dirname`
# is syrupy's, another dependency of the test harness: it names the directory
# snapshots are written to and cleaned out of, joined to each test's directory,
# and an absolute name replaces that directory outright.
#
# Same rule as the list above - an option that writes a path and is not here is
# a bug in the list.
REFUSED_WRITE = frozenset(
    {
        "--junitxml",
        "--junit-xml",
        "--log-file",
        "--debug",
        "--basetemp",
        "--report-log",
        "--rootdir",
        "--snapshot-dirname",
    }
)

# `--cov-config` names the coverage configuration file, and that file chooses
# where every report is written - `.coveragerc` sets `[xml] output` here - so a
# config outside the tree can point `--cov-report=xml` at any path without a
# `:` ever appearing on the command line. It also lists `[run] plugins`, which
# coverage imports. Refusing the option leaves the repository's own
# `.coveragerc` in force, which is what pytest-cov reads when nothing else is
# named.
REFUSED_CONFIG = frozenset({"--cov-config"})

# `--pastebin=all` (or `=failed`) makes pytest POST the whole session log -
# the absolute rootdir, every plugin, each failure's traceback and locals - to
# https://bpa.st, a public paste service, and print the link. That is the one
# thing docs/SECURITY-AI.md says never to do with logs or fixtures, and this
# wrapper runs without the prompt that would otherwise stop it. No CI job or
# documented command uses it.
REFUSED_SEND = frozenset({"--pastebin"})

# `--cov-report` is the one of these that has a legitimate spelling: AGENTS.md
# documents `--cov-report=term-missing`. Its value is `TYPE[:SUFFIX]`, and what
# the suffix means depends on the type: after a terminal type it is a display
# modifier (`term-missing:skip-covered`), after a file type it is the
# destination (`xml:out.xml`, `html:dir`, `lcov:out.info`, `annotate:dir`,
# `json:out.json`, `markdown:out.md`). Only the second kind writes a path, so
# only a suffix on a type outside TERMINAL_REPORTS is refused.
VALUED_WRITE = frozenset({"--cov-report"})
TERMINAL_REPORTS = frozenset({"term", "term-missing"})

# Every other option the installed pytest and its plugins accept, each one
# reviewed and judged safe to forward. The wrapper does not consult this set -
# anything not refused above is forwarded whether or not it is here. The suite
# does: `test_every_pytest_option_is_forwarded_or_refused` reads the live
# parser and fails on an option that is in neither this set nor a refused one,
# so a plugin option that a dependency bump adds turns the suite red until
# someone decides which side it belongs on, instead of reaching this
# no-prompt command unread. Moving an option from here to a refused set is
# always allowed; adding one here needs the same review the refused sets got.
#
# The list lives here and not in the test file because the network guard's own
# test refuses any file under `tests/` that spells pytest-socket's escape
# options, and two of them are on this list.
FORWARDED = frozenset(
    {
        # pytest core: selection, collection and its reporting. None of these
        # names a path pytest writes or a module it imports by name; `--ignore`
        # and `--deselect` only narrow what the target check already allowed,
        # and `--doctest-modules` imports only the modules under the targets.
        *("-k", "-m", "-x", "--exitfirst", "--maxfail", "--markers"),
        *("--strict", "--strict-config", "--strict-markers"),
        *("--co", "--collect-only", "--collectonly", "--noconftest"),
        *("--ignore", "--ignore-glob", "--deselect", "--import-mode"),
        *("--keep-duplicates", "--keepduplicates", "--collect-in-virtualenv"),
        *("--continue-on-collection-errors", "--disable-plugin-autoload"),
        *("--doctest-modules", "--doctest-glob", "--doctest-report"),
        *("--doctest-ignore-import-errors", "--doctest-continue-on-failure"),
        *("--fixtures", "--funcargs", "--fixtures-per-test", "--runxfail"),
        *("--setup-only", "--setuponly", "--setup-show", "--setupshow"),
        *("--setup-plan", "--setupplan", "-h", "--help", "-V", "--version"),
        *("--trace-config", "--traceconfig", "--assert"),
        # `-W` sets a warnings filter. A category written as `module.Class`
        # makes pytest import that module, but only from `sys.path`, where an
        # agent-written module is a file in the tree like any test.
        *("-W", "--pythonwarnings"),
        # pytest core: terminal output only.
        *("-v", "--verbose", "-q", "--quiet", "--verbosity", "-r", "--tb"),
        *("--no-header", "--no-summary", "--no-fold-skipped"),
        *("--force-short-summary", "--disable-warnings"),
        *("--disable-pytest-warnings", "-l", "--showlocals", "--no-showlocals"),
        *("--xfail-tb", "--show-capture", "--full-trace", "--fulltrace"),
        *("--color", "--code-highlight", "--durations", "--durations-min"),
        *("-s", "--capture", "--junit-prefix", "--junitprefix"),
        # pytest core: the cache and stepwise state. They write only under the
        # root directory's `.pytest_cache/`, which is this repository once
        # `--rootdir` and `-o cache_dir` are refused.
        *("--lf", "--last-failed", "--ff", "--failed-first", "--nf"),
        *("--new-first", "--lfnf", "--last-failed-no-failures"),
        *("--cache-show", "--cache-clear", "--sw", "--stepwise"),
        *("--sw-skip", "--stepwise-skip", "--sw-reset", "--stepwise-reset"),
        # pytest core: logging formats and levels. The one that names a file,
        # `--log-file`, is refused; the mode and format of that file are inert
        # without it.
        *("--log-level", "--log-format", "--log-date-format", "--log-cli-level"),
        *("--log-cli-format", "--log-cli-date-format", "--log-file-mode"),
        *("--log-file-level", "--log-file-format", "--log-file-date-format"),
        *("--log-auto-indent", "--log-disable"),
        # pytest-picked: the mode only chooses between `git status` and
        # `git diff`, and does nothing without the refused `--picked`.
        "--mode",
        # pytest-github-actions-annotate-failures: output only.
        "--exclude-warning-annotations",
        # pytest-timeout: durations and the mechanism that enforces them.
        *("--timeout", "--timeout-method", "--timeout_method"),
        *("--timeout-disable-debugger-detection", "--session-timeout"),
        # pytest-xdist: local workers only. `--tx` and `--px`, the options that
        # choose a worker's interpreter, are refused; without a remote gateway
        # `--rsyncdir` copies nothing, and `--looponfail` reruns the same
        # targets in a subprocess of this interpreter.
        *("-n", "--numprocesses", "--maxprocesses", "--max-worker-restart"),
        *("-d", "--dist", "--loadscope-reorder", "--no-loadscope-reorder"),
        *("--rsyncdir", "--rsyncignore", "--testrunuid", "--maxschedchunk"),
        *("-f", "--looponfail"),
        # pytest-homeassistant-custom-component: the recorder fixtures' database
        # URL. This repository loads no recorder fixture, so nothing reads it.
        *("--dburl", "--drop-existing-db"),
        # anyio and pytest-asyncio: event loop modes.
        *("--anyio-mode", "--asyncio-mode", "--asyncio-debug"),
        # pytest-cov: what is measured and how it is judged. The options that
        # choose where a report is written are `--cov-config` and the file
        # forms of `--cov-report`, both refused above.
        *("--cov", "--cov-reset", "--no-cov-on-fail", "--no-cov"),
        *("--cov-fail-under", "--cov-append", "--cov-branch"),
        *("--cov-precision", "--cov-context"),
        # pytest-socket: forwarded, but they do not lift the network guard -
        # Home Assistant's test plugin disables sockets again before every
        # test, and tests/test_ci_network_guard.py runs green with each one.
        *("--disable-socket", "--force-enable-socket", "--allow-hosts"),
        "--allow-unix-socket",
        # syrupy: snapshot behaviour. The snapshot directory's name is refused
        # above; the rest only decide what is compared, written or reported
        # inside the default `__snapshots__/` beside each test.
        *("--snapshot-update", "--snapshot-update-new-only"),
        *("--snapshot-warn-unused", "--snapshot-disable-unused"),
        *("--snapshot-no-cleanup", "--snapshot-details"),
        *("--snapshot-default-extension", "--snapshot-no-colors"),
        *("--snapshot-patch-pycharm-diff", "--snapshot-diff-mode"),
        *("--snapshot-ignore-file-extensions", "--snapshot-declaration-order"),
    }
)

# pytest builds its parser with `fromfile_prefix_chars="@"`, so argparse
# replaces any argument that starts with `@` by the lines of the file it names,
# one argument per line, before a single option is parsed. The checks below
# see only the `@name` word, which is neither an option nor an existing path,
# so everything in the file - `--junitxml=.claude/settings.json`, a target
# outside `tests/` - was forwarded unread. It is also a read: pytest reports
# the first line that is not a target as "file or directory not found", so
# `@.env` printed the first line of a file the Read deny rules withhold.
# argparse expands the prefix wherever the argument sits, an option's value
# included, so the refusal does not care what comes before it.
FROMFILE_PREFIX = "@"

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
    if name in REFUSED_LONG | REFUSED_WRITE | REFUSED_CONFIG | REFUSED_SEND:
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
        if arg.startswith(FROMFILE_PREFIX):
            problems.append(
                f"{arg}: pytest reads more arguments from that file, "
                "where these checks cannot see them"
            )
            pending = ""
            continue

        # A `--cov-report` value arrives either attached with `=` or as the
        # next argument. A `:` after a file type is a destination; after a
        # terminal type it is a display modifier and writes nothing.
        name, _, attached = arg.partition("=")
        value = attached if name in VALUED_WRITE else (arg if pending else "")
        pending = name if name in VALUED_WRITE and not attached else ""
        kind, _, suffix = value.partition(":")
        if suffix and kind not in TERMINAL_REPORTS:
            problems.append(f"{arg}: writes a report to a path of its own")
            continue

        if arg.startswith("-"):
            if _refused_option(arg):
                if name in REFUSED_WRITE:
                    why = "creates, truncates or deletes a path of its own"
                elif name in REFUSED_CONFIG:
                    why = "chooses the config that names where reports are written"
                elif name in REFUSED_SEND:
                    why = "uploads the session log to a public paste service"
                else:
                    why = "reaches code by a route the target check cannot see"
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
