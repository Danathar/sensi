"""Run the git lookup in docs/agent-tasks/README.md, the way a reader would.

The page's first link in the chain is the merge commit: `master`'s
first-parent history holds one `Merge pull request #N from Danathar/<branch>`
line per change, and the page gives the command that lists them. A reader who
wants the merge for #N runs that command and looks for `#N`.

This repository is a fork, and the history underneath it is upstream
`iprak/sensi`'s, which has its own `Merge pull request #N` lines numbered from
the same #1. When this test was written, 33 numbers appeared twice in
`git log --first-parent --oneline master` (#37, #76, #100, ...), so the
page's plain command gave two answers for them - #76, one of the two early
pull requests the page names under its gaps, among them.

The command is run, not just read, because its claim depends on the shape of
the history rather than on any flag being valid. CI checks out at depth 1, so
the check that matters there runs against a small repository built in a
temporary directory with both kinds of merge line. The same lookup is also run
against this clone's own history when the clone is not shallow.

The `gh` commands on the page are not run: they need the network and a token.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_DOC = _ROOT / "docs" / "agent-tasks" / "README.md"

# One line of the lookup's `--oneline` output: short hash, then the subject.
_FORK_MERGE = re.compile(r"^Merge pull request #(\d+) from Danathar/")

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)


def _lookup_command() -> str:
    """Return the page's one-line `git log --first-parent` command."""
    blocks = re.findall(
        r"```bash\n(.*?)```", _DOC.read_text(encoding="utf-8"), flags=re.DOTALL
    )
    commands = [block.strip() for block in blocks if "--first-parent" in block]
    assert len(commands) == 1, (
        f"expected exactly one `--first-parent` command block in {_DOC.name}, "
        f"found {len(commands)}"
    )
    assert "\n" not in commands[0], "the merge lookup is expected to be one line"
    assert commands[0].startswith("git log "), commands[0]
    return commands[0]


def _run_lookup(repo: Path, env: dict[str, str] | None = None) -> list[str]:
    """Run the page's command verbatim in `repo` and return its output lines."""
    result = subprocess.run(
        ["bash", "-c", _lookup_command()],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def _subjects(lines: list[str]) -> list[str]:
    """Drop the short hash from each `--oneline` line."""
    return [line.split(" ", 1)[1] if " " in line else "" for line in lines]


class _Repo:
    """A throwaway repository whose `master` is shaped like this fork's."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(path),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        self.git("init", "-q", "-b", "master")
        self.commit("Initial commit")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def commit(self, message: str) -> None:
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def land(self, subject: str, branch: str) -> None:
        """Merge a one-commit branch into master with a merge commit."""
        self.git("checkout", "-q", "-b", branch, "master")
        self.commit(f"work on {branch}")
        self.git("checkout", "-q", "master")
        self.git(
            "merge", "-q", "--no-ff", "-m", f"{subject}\n\nbody of {branch}", branch
        )


@pytest.fixture
def fork(tmp_path: Path) -> _Repo:
    """Upstream merges, then this fork's merges reusing the same numbers."""
    repo = _Repo(tmp_path)
    repo.land("Merge pull request #1 from neilbrookins/patch-1", "up-1")
    repo.land("Merge pull request #2 from iprak/fix-warning", "up-2")
    repo.commit("Relax entity availability after connection failure (#3)")
    repo.land("Merge pull request #1 from Danathar/fix/redact", "fork-1")
    repo.land("Merge pull request #2 from Danathar/ci/add-tests", "fork-2")
    repo.commit("Update README.md")
    repo.land("Merge pull request #4 from Danathar/quality/test-x", "fork-4")
    return repo


def test_the_lookup_lists_only_this_forks_merges(fork: _Repo) -> None:
    """Upstream's `Merge pull request #1` must not answer a lookup for our #1."""
    subjects = _subjects(_run_lookup(fork.path, fork.env))
    assert subjects == [
        "Merge pull request #4 from Danathar/quality/test-x",
        "Merge pull request #2 from Danathar/ci/add-tests",
        "Merge pull request #1 from Danathar/fix/redact",
    ]


def test_the_lookup_stays_on_masters_first_parent(fork: _Repo) -> None:
    """A merge made on a branch before it landed is not a change on master."""
    fork.git("checkout", "-q", "-b", "quality/behind", "master")
    fork.commit("the change")
    fork.git("checkout", "-q", "master")
    fork.commit("Update README.md again")
    fork.git("checkout", "-q", "quality/behind")
    fork.git(
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "Merge pull request #9 from Danathar/side",
        "master",
    )
    fork.git("checkout", "-q", "master")
    fork.git(
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "Merge pull request #5 from Danathar/quality/behind",
        "quality/behind",
    )
    subjects = _subjects(_run_lookup(fork.path, fork.env))
    assert subjects[0] == "Merge pull request #5 from Danathar/quality/behind"
    assert not any("#9 " in subject for subject in subjects)


def test_the_page_says_why_the_lookup_is_filtered() -> None:
    """Without the reason, the filter reads as decoration and gets dropped."""
    text = " ".join(_DOC.read_text(encoding="utf-8").split())
    assert "iprak/sensi" in text
    assert "reuse many of the same numbers" in text


def test_the_lookup_on_this_clone_names_each_merge_once() -> None:
    """Run the page's command on the real history, when the clone has it."""
    shallow = subprocess.run(
        ["git", "-C", str(_ROOT), "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if shallow != "false":
        pytest.skip("shallow clone: no history to run the lookup against")
    if subprocess.run(
        ["git", "-C", str(_ROOT), "rev-parse", "--verify", "-q", "master"],
        capture_output=True,
        check=False,
    ).returncode:
        pytest.skip("no local master branch in this clone")

    subjects = _subjects(_run_lookup(_ROOT))
    numbers = [match.group(1) for s in subjects if (match := _FORK_MERGE.match(s))]
    assert numbers, "the lookup printed no merges, so nothing below checks anything"
    assert len(numbers) == len(subjects), [
        s for s in subjects if not _FORK_MERGE.match(s)
    ]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1}, key=int)
    assert not duplicates, f"the lookup names these pull requests twice: {duplicates}"

    every_fork_merge = [
        s
        for s in subprocess.run(
            ["git", "-C", str(_ROOT), "log", "--first-parent", "--format=%s", "master"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if _FORK_MERGE.match(s)
    ]
    assert subjects == every_fork_merge
