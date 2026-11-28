"""How much of the Bash gate its own tests reach.

`.claude/hooks/gate-git-file-arguments.py` is the PreToolUse hook that keeps
allow-listed commands from reading or writing files the deny list withholds.
The coverage gate measures `custom_components/sensi` only, and the hook's
tests run it as a subprocess the way Claude Code does, so a new refusal rule
could ship with no test reaching it and every check would stay green.

This test runs the hook's tests again under coverage, following the hook into
its subprocesses, and fails when more of its statements go unreached than
`_UNREACHED_CEILING` allows. The ceiling is the count on the day it was set;
lower it whenever a test reaches one more line. The badge and the coverage
gate's total stay about the integration alone.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import coverage

_ROOT = Path(__file__).resolve().parents[1]
_HOOK = _ROOT / ".claude" / "hooks" / "gate-git-file-arguments.py"

# Statements of the hook no test reaches. Lower it when tests improve; never
# raise it to make a change pass - add the test the new statement needs.
_UNREACHED_CEILING = 20


def _hook_tests() -> list[str]:
    """Every test module that names the hook, except this one."""

    here = Path(__file__).resolve()
    return sorted(
        str(path.relative_to(_ROOT))
        for path in (_ROOT / "tests").glob("test_*.py")
        if path.resolve() != here and _HOOK.name in path.read_text(encoding="utf-8")
    )


def test_the_hook_tests_are_found() -> None:
    """The selection must keep finding the gate's own test module."""

    assert "tests/test_git_file_argument_gate.py" in _hook_tests()


def test_the_hook_stays_under_its_unreached_ceiling(tmp_path: Path) -> None:
    """A new hook statement with no test reaching it fails here."""

    rcfile = tmp_path / "hook.coveragerc"
    data_file = tmp_path / ".coverage"
    rcfile.write_text(
        "[run]\n"
        f"source = {_HOOK.parent}\n"
        "branch = true\n"
        "parallel = true\n"
        # Start coverage in every Python subprocess, which is how the tests
        # run the hook.
        "patch = subprocess\n"
        f"data_file = {data_file}\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    # The hook's shebang is `/usr/bin/env python3`; make that the interpreter
    # coverage is installed in.
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"

    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--rcfile={rcfile}",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *_hook_tests(),
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-4000:]

    cov = coverage.Coverage(data_file=str(data_file), config_file=str(rcfile))
    cov.combine()
    cov.load()
    _, statements, _, missing, _ = cov.analysis2(str(_HOOK))

    assert statements, "coverage recorded nothing for the hook"
    assert len(missing) <= _UNREACHED_CEILING, (
        f"{len(missing)} statements of {_HOOK.name} are reached by no test "
        f"(ceiling {_UNREACHED_CEILING}): {missing}"
    )
