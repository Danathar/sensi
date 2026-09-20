"""The PreToolUse gate, `.claude/hooks/gate-git-file-arguments.py`.

`git diff`, `git log` and `git show` are on the allow list in
`.claude/settings.json`, so they run with no permission prompt. Three of their
options do file I/O the deny list exists to withhold: `--no-index` reads any
two paths on disk, `--output` creates or truncates any path, and `-O` /
`--orderfile` makes git open a path. The deny rules bind the `Read` and `Edit`
tools, not a shell command, so the hook is the only thing standing between the
allow list and `secrets.yaml` or `.claude/hooks/`.

Each test feeds the committed script a payload on stdin, the way Claude Code
does, and checks the exit code: 0 lets the call through, 2 blocks it and puts
the reason on stderr. The script is run by its own path so a lost executable
bit or a broken shebang fails here too.
"""

from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_HOOK = _ROOT / ".claude" / "hooks" / "gate-git-file-arguments.py"
_SETTINGS = _ROOT / ".claude" / "settings.json"


def _payload(command: str, *, tool: str = "Bash") -> str:
    """Return a PreToolUse payload of the shape Claude Code sends."""

    return json.dumps(
        {
            "session_id": "0000",
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"command": command},
        }
    )


def _run(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_HOOK)],
        input=stdin,
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


# --------------------------------------------------------------------------
# The file itself.
# --------------------------------------------------------------------------


def test_the_hook_is_executable_with_a_python_shebang() -> None:
    """Claude Code runs the path as a command; without both it never runs."""

    mode = _HOOK.stat().st_mode
    assert mode & stat.S_IXUSR, "the hook has lost its executable bit"
    first_line = _HOOK.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env python3"


# --------------------------------------------------------------------------
# What passes. The allow list is there so these run without a prompt; a gate
# that refused ordinary git reads would be turned off within the day.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git diff",
        "git diff --stat HEAD",
        "git diff -- custom_components/sensi/climate.py",
        "git log --oneline -10",
        "git show HEAD --stat",
        "git status --short",
        "ruff check .",
        # The wide "any word after git" subcommand test must not turn a commit
        # message into a refusal: git commit takes none of the gated options.
        'git commit -m "diff"',
        "git diff secrets.yaml",
        "git diff HEAD~1..HEAD",
        "git diff HEAD~1 HEAD",
        "git diff master feature",
        "git diff --cached",
    ],
)
def test_ordinary_commands_pass(command: str) -> None:
    """The allow list exists so these run without a prompt; keep it that way."""

    completed = _run(_payload(command))
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""


def test_a_payload_for_another_tool_passes() -> None:
    """The gate is registered for Bash; anything else is not its business."""

    completed = _run(_payload("git diff --no-index a b", tool="Read"))
    assert completed.returncode == 0


def test_stdin_that_is_not_json_passes() -> None:
    """Fail open on a bad payload.

    A gate that failed closed on its own bugs would make every unparseable
    payload look like an attack, and would be turned off for it.
    """

    completed = _run("not json")
    assert completed.returncode == 0


# --------------------------------------------------------------------------
# What is refused, and the spelling each case covers.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "fragment"),
    [
        # --no-index, first and last position: an option can go anywhere.
        ("git diff --no-index /dev/null secrets.yaml", "--no-index"),
        ("git diff /dev/null secrets.yaml --no-index", "--no-index"),
        # --output, attached and separated, on all three subcommands.
        ("git diff --output=.claude/settings.json", "--output"),
        ("git diff --output .claude/settings.json HEAD", "--output"),
        ("git log --output=/tmp/x -1", "--output"),
        ("git log --output /tmp/x -1", "--output"),
        ("git show --output=/tmp/x HEAD", "--output"),
        ("git show --output /tmp/x HEAD", "--output"),
        # -O short, bare and clustered; --orderfile long.
        ("git diff -O/etc/passwd", "-O"),
        ("git diff -tO/etc/passwd", "-O"),
        ("git diff --orderfile /etc/passwd", "--orderfile"),
        # git accepts unambiguous abbreviations; the gate matches by prefix so
        # it does not have to know which ones git accepts this version.
        ("git diff --o=/tmp/x", "--o"),
        # git -c key=value diff: the subcommand is not the first non-option.
        ("git -c core.pager=cat diff --no-index a b", "--no-index"),
        # Git enters --no-index mode implicitly when a path points outside the
        # working tree, even when the option is omitted.
        ("git diff secrets.yaml /dev/null", "/dev/null"),
        ("git diff /dev/null secrets.yaml", "/dev/null"),
        ("git diff -- secrets.yaml /dev/null", "/dev/null"),
        ("git diff secrets.yaml ..", ".."),
        ("git diff ../secrets.yaml /dev/null", "../secrets.yaml"),
        ("git diff ~/secrets.yaml secrets.yaml", "~/secrets.yaml"),
        ("git diff /tmp/a /tmp/b", "/tmp/a"),
    ],
)
def test_file_arguments_are_refused(command: str, fragment: str) -> None:
    """Each spelling is blocked and the reason names the option."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"
    assert "Blocked" in completed.stderr
    assert fragment in completed.stderr


@pytest.mark.parametrize(
    "command",
    [
        # shlex does not brace-expand, glob or substitute; each of these
        # reaches git as the full refused option while a naive parse sees
        # something harmless. The gate refuses the characters instead.
        "git diff --no-inde{x,x} a b",
        "git diff --outpu[t]=x",
        "git diff $F a b",
        "git diff $(printf -- --no-index) a b",
        "git log -- '*.py'",
    ],
)
def test_shell_expansion_in_a_gated_command_is_refused(command: str) -> None:
    """A word the shell would rewrite is refused rather than guessed at."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"
    assert "expanded by the shell" in completed.stderr


def test_an_unbalanced_quote_is_refused() -> None:
    """What bash would run is not what the gate saw, and bash rejects it too."""

    completed = _run(_payload("git diff --no-index 'a b"))
    assert completed.returncode == 2
    assert "unbalanced quote" in completed.stderr


def test_a_mid_word_hash_does_not_hide_the_rest_of_the_command() -> None:
    """A `#` inside a word is not a comment.

    Bash starts a comment only at the start of a word; shlex's default starts
    one mid-word, which would truncate `a#b c --no-index` to `a`.
    """

    completed = _run(_payload("git diff a#b c --no-index"))
    assert completed.returncode == 2
    assert "--no-index" in completed.stderr


def test_ungated_git_subcommands_take_the_options_freely() -> None:
    """Only diff, log and show are gated.

    A `--output` on anything else is not the capability this hook is about,
    and refusing it would be noise.
    """

    completed = _run(_payload("git commit --output=x -m msg"))
    assert completed.returncode == 0


# --------------------------------------------------------------------------
# The wiring in .claude/settings.json. The script can be perfect and never run.
# --------------------------------------------------------------------------


def _hook_entries(event: str) -> list[dict]:
    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    entries = []
    for block in settings.get("hooks", {}).get(event, []):
        for entry in block.get("hooks", []):
            entries.append({"matcher": block.get("matcher"), **entry})
    return entries


def test_the_gate_is_registered_for_pre_tool_use_on_bash() -> None:
    """Exactly one PreToolUse command, matched to Bash, and it is this script.

    The command is written against `$CLAUDE_PROJECT_DIR`; a relative path here
    resolves against whatever directory the tool call ran in, which is not
    reliably the repository root.
    """

    entries = _hook_entries("PreToolUse")
    assert len(entries) == 1, f"expected one PreToolUse hook, got {entries}"
    entry = entries[0]

    assert entry["matcher"] == "Bash"
    assert entry["type"] == "command"
    assert entry["command"] == (
        "$CLAUDE_PROJECT_DIR/.claude/hooks/gate-git-file-arguments.py"
    )


def test_the_allow_list_still_carries_the_commands_the_gate_covers() -> None:
    """The allow rules and the gate are checked together.

    If these leave the allow list the gate is dead code; if the gate leaves
    while these stay, the finding in #202 is open again. Neither may drift
    alone.
    """

    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    allowed = set(settings["permissions"]["allow"])
    for rule in ("Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)"):
        assert rule in allowed, f"{rule} left the allow list; is the gate still needed?"


def test_ruff_is_allowed_only_as_the_exact_documented_commands() -> None:
    """No `ruff` allow rule may take arguments (#210).

    `ruff check --output-file=PATH` creates or truncates PATH, `ruff format
    PATH` and `ruff check --fix PATH` rewrite it in place, and the deny rules
    bind `Edit`, not a shell command. The gate above covers git only, and no
    prefix rule can cover ruff: `--output-file` and `--fix` may sit anywhere
    in the argument list. So the allow list names whole commands, exactly the
    ones AGENTS.md documents, and anything else asks.

    `ruff format .` is not one of them, even though it takes no argument.
    It rewrites every Python file `ruff.toml` reaches, and `ruff.toml` is an
    ordinary editable file: set `line-length = 50` there and the formatter
    rewrites `.claude/hooks/gate-git-file-arguments.py` and
    `scripts/run_tests.py`, both on the `Edit` deny list. Only the check
    forms may run without a prompt.
    """

    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    allowed = settings["permissions"]["allow"]
    ruff_rules = [rule for rule in allowed if rule.startswith("Bash(ruff")]

    assert ruff_rules, (
        "ruff left the allow list; AGENTS.md says it runs without a prompt"
    )
    for rule in ruff_rules:
        assert not rule.endswith(":*)"), (
            f"{rule} is a prefix rule; it lets ruff write any path (#210)"
        )
    assert set(ruff_rules) == {"Bash(ruff check .)", "Bash(ruff format --check .)"}
    assert "Bash(ruff format .)" not in allowed, (
        "ruff format . rewrites the protected hook and wrapper once ruff.toml "
        "changes, so it must ask"
    )
