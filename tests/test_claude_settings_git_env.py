"""The `env` block in `.claude/settings.json` that makes git refuse bare repos.

`.claude/settings.json` lets an agent run `git log` and `git show` without a
permission prompt. Git decides which repository a command belongs to by
looking at the current directory, and a directory that looks like a bare
repository (`HEAD`, `objects/`, `refs/`) is taken as one. A branch can commit
such a directory as ordinary files, with its own `config` and
`info/attributes`. The config names a program for git to run while it renders
a diff (`diff.x.textconv`), and the attributes switch it on for every path.
An agent whose working directory is inside that folder then starts the
program, with no prompt, by running a pre-approved `git show` or `git log -p`.
The Bash guard hook cannot see this: the program comes from file content, not
from the command line.

The settings file closes it with `GIT_CONFIG_*` variables that set
`safe.bareRepository=explicit` for every command the agent runs. These tests
pin that block exactly, then plant the repository above and check the program
runs without the block and is refused with it. The repository's own `.git/`
is not bare, so ordinary history commands still work.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

_ROOT = Path(__file__).resolve().parents[1]
_SETTINGS = _ROOT / ".claude" / "settings.json"
_EXPECTED_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "safe.bareRepository",
    "GIT_CONFIG_VALUE_0": "explicit",
}
_SUBJECT = "planted"


def _settings_env() -> dict[str, str]:
    """Return the `env` block of the committed settings file."""
    return json.loads(_SETTINGS.read_text(encoding="utf-8")).get("env", {})


def _clean_env(home: Path) -> dict[str, str]:
    """Return a git environment with no user or system config and no GIT_*."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


def _git(
    cwd: Path, env: dict[str, str], *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run git in `cwd` and return the finished process."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _ok(cwd: Path, env: dict[str, str], *args: str, stdin: str | None = None) -> str:
    """Run git, require success, and return stdout without the newline."""
    done = _git(cwd, env, *args, stdin=stdin)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


def _planted_clone(tmp_path: Path, marker: Path) -> Path:
    """Clone a repository that carries a committed bare repository `evil/`.

    Showing the commit inside `evil/` renders a diff through a textconv
    program that creates `marker`. Returns the clone's path.
    """
    env = _clean_env(tmp_path)
    src = tmp_path / "src"
    evil = src / "evil"
    _ok(tmp_path, env, "init", "-q", str(src))
    _ok(src, env, "init", "-q", "--bare", "evil")

    # Give the bare repository one commit, so there is a diff to render.
    blob = _ok(evil, env, "hash-object", "-w", "--stdin", stdin="data\n")
    tree = _ok(evil, env, "mktree", stdin=f"100644 blob {blob}\tfile.txt\n")
    commit = _ok(
        evil,
        env,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit-tree",
        tree,
        "-m",
        _SUBJECT,
    )
    _ok(evil, env, "symbolic-ref", "HEAD", "refs/heads/main")
    _ok(evil, env, "update-ref", "refs/heads/main", commit)

    # The textconv program and the attribute that switches it on for every path.
    _ok(evil, env, "config", "diff.x.textconv", f"touch '{marker}'; cat")
    (evil / "info").mkdir(exist_ok=True)
    (evil / "info" / "attributes").write_text("* diff=x\n", encoding="utf-8")
    (evil / "refs" / "tags").mkdir(parents=True, exist_ok=True)
    (evil / "refs" / "tags" / ".keep").write_text("", encoding="utf-8")

    _ok(src, env, "add", "evil")
    _ok(
        src,
        env,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-qm",
        "x",
    )
    _ok(tmp_path, env, "clone", "-q", str(src), "victim")
    return tmp_path / "victim"


def test_settings_env_sets_safe_bare_repository_explicit() -> None:
    """The settings file carries exactly the three variables, nothing else."""
    assert _settings_env() == _EXPECTED_ENV


def test_committed_bare_repository_cannot_run_a_program_during_history_commands(
    tmp_path: Path,
) -> None:
    """`git show` and `git log -p` run the planted program only without the env."""
    marker = tmp_path / "ran"
    victim = _planted_clone(tmp_path, marker)
    plain = _clean_env(tmp_path)
    agent = {**plain, **_settings_env()}
    commands = (("show",), ("log", "-p", "-1"))

    # Without the setting the planted program runs, so the refusal below is real.
    for args in commands:
        marker.unlink(missing_ok=True)
        _git(victim / "evil", plain, *args)
        assert marker.exists(), f"precondition: git {' '.join(args)} runs the program"

    for args in commands:
        marker.unlink(missing_ok=True)
        for cwd, full in (
            (victim / "evil", args),
            (victim, ("-C", "evil", *args)),
        ):
            done = _git(cwd, agent, *full)
            assert done.returncode != 0, f"git {' '.join(full)} should refuse"
            assert "safe.bareRepository" in done.stderr
            assert not marker.exists(), f"git {' '.join(full)} ran the program"

    # The checkout's own repository (.git/, not bare) still works with the env.
    assert _git(victim, agent, "status", "--short").returncode == 0
    assert _ok(victim, agent, "log", "-1", "--format=%s") == "x"
