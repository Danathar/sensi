"""The Claude Code hooks must parse on older Pythons (#410).

Claude Code runs `.claude/hooks/*.py` under whatever `python3` is first on PATH,
not Home Assistant's 3.14. A hook that fails to parse exits 1, which Claude Code
treats as a non-blocking error and runs the command anyway, so a SyntaxError in
the gate turns every refusal off. PEP 758's unparenthesised `except A, B:` is
3.14-only, which is exactly how that happened.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

HOOKS = sorted(
    (Path(__file__).resolve().parent.parent / ".claude" / "hooks").glob("*.py")
)
OLDEST = (3, 12)  # Ubuntu 24.04's python3


def test_there_are_python_hooks_to_check() -> None:
    """An empty glob would make the parse check pass vacuously."""
    assert HOOKS, "no .claude/hooks/*.py found; this test would check nothing"


@pytest.mark.parametrize("hook", HOOKS, ids=lambda p: p.name)
def test_every_python_hook_parses_on_the_oldest_supported_host_python(
    hook: Path,
) -> None:
    """A hook that does not parse exits 1, and Claude Code then runs the command."""
    try:
        ast.parse(
            hook.read_text(encoding="utf-8"), filename=str(hook), feature_version=OLDEST
        )
    except SyntaxError as exc:
        pytest.fail(
            f"{hook.name} does not parse on Python {OLDEST[0]}.{OLDEST[1]}: {exc}"
        )
