"""Which files in `.github/workflows/` GitHub Actions runs.

GitHub runs a file directly in that directory if it ends in `.yml` or `.yaml`.
A test that checks a rule over every workflow reads its list from here. A scan
that globbed one extension would not narrow its check, it would exempt every
file spelled the other way: a `.yaml` workflow could hold a write token, an
unpinned `uses:` or a `pull_request_target` trigger while the suite stayed
green, never having looked at it.
"""

from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

_GLOBS = ("*.yml", "*.yaml")


def workflow_paths(directory: Path = WORKFLOWS) -> list[Path]:
    """Every workflow file in `directory`, whichever extension it uses."""
    return sorted(path for glob in _GLOBS for path in directory.glob(glob))
