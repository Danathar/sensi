"""`tests/_workflows.py` lists what GitHub runs, and every scan goes through it.

GitHub runs a file directly in `.github/workflows/` if it ends in `.yml` or
`.yaml`. #439 moved fifteen scans onto one helper so that a `.yaml` workflow
could not slip past rules such as "only the labeler uses
`pull_request_target`". Two tests already check that the helper reads both
suffixes. No `.yaml` workflow is committed, though, so nothing else exercised
the second suffix: dropping `*.yaml` from either of the two `git ls-files`
scans that list the directory themselves, a test going back to its own
`.glob("*.yml")`, or the helper descending into subdirectories GitHub ignores,
each left every test green. These tests close those three.
"""

import ast
from pathlib import Path
import subprocess

from tests._workflows import WORKFLOWS, workflow_paths

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = _ROOT / "tests"
_HELPER = _TESTS / "_workflows.py"

_SUFFIXES = (".yml", ".yaml")


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on: push\n", encoding="utf-8")
    return path


def test_the_helper_lists_both_suffixes_and_nothing_else(tmp_path: Path) -> None:
    """A `.yaml` workflow is listed; a nested or differently named file is not."""
    expected = [
        _touch(tmp_path / "a.yml"),
        _touch(tmp_path / "b.yaml"),
        _touch(tmp_path / "c.yml"),
    ]
    # GitHub ignores these: a subdirectory, another suffix, a disabled copy.
    _touch(tmp_path / "nested" / "d.yml")
    _touch(tmp_path / "nested" / "e.yaml")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / "f.yml.disabled")
    _touch(tmp_path / "README.md")
    # A looser glob such as `*.y*ml` would also take this page.
    _touch(tmp_path / "page.y.html")

    assert workflow_paths(tmp_path) == expected


def test_the_helper_sorts_across_suffixes(tmp_path: Path) -> None:
    """The order is by name, not grouped by suffix, so scans are stable."""
    names = ["a.yaml", "b.yml", "c.yaml"]
    for name in reversed(names):
        _touch(tmp_path / name)

    assert [path.name for path in workflow_paths(tmp_path)] == names


def test_the_default_directory_is_the_committed_workflow_set() -> None:
    """With no argument the helper lists exactly what git tracks there."""
    tracked = subprocess.run(
        ["git", "ls-files", "--", *(f".github/workflows/*{s}" for s in _SUFFIXES)],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()

    assert WORKFLOWS == _ROOT / ".github" / "workflows"
    assert tracked
    assert [p.relative_to(_ROOT).as_posix() for p in workflow_paths()] == sorted(
        tracked
    )


def _string_constants(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _test_sources() -> dict[Path, ast.AST]:
    return {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(_TESTS.rglob("*.py"))
        if path not in {_HELPER, Path(__file__).resolve()}
    }


def test_no_test_globs_yaml_files_itself() -> None:
    """A `.glob("*.yml")` over a directory is the bug #439 removed."""
    offenders = []
    for path, tree in _test_sources().items():
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"glob", "rglob"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            pattern = node.args[0].value
            if pattern.endswith(("ml", "ml*")) and ".y" in pattern:
                offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno} {pattern}")

    assert offenders == []


def test_every_git_listing_of_the_workflows_names_both_suffixes() -> None:
    """A `git ls-files` scan of the directory carries `*.yml` and `*.yaml`."""
    prefix = ".github/workflows/*"
    checked = []
    for path, tree in _test_sources().items():
        patterns = {
            value for value in _string_constants(tree) if value.startswith(prefix)
        }
        if not patterns:
            continue
        checked.append(path.name)
        assert patterns == {f"{prefix}{suffix}" for suffix in _SUFFIXES}, (
            path.relative_to(_ROOT),
            sorted(patterns),
        )

    # The two scans that list tracked files rather than call the helper.
    assert {"test_ci_network_guard.py", "test_workflow_summaries.py"} <= set(checked)
