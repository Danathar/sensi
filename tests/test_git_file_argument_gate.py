"""The PreToolUse gate, `.claude/hooks/gate-git-file-arguments.py`.

`git diff`, `git log` and `git show` are on the allow list in
`.claude/settings.json`, so they run with no permission prompt. Three of their
options do file I/O the deny list exists to withhold: `--no-index` reads any
two paths on disk, `--output` creates or truncates any path, and `-O` /
`--orderfile` makes git open a path. The shell adds two spellings of its own:
an output redirection on the git command truncates its target before git runs
(`git diff HEAD >.claude/settings.json`, and bash reads `>.claude/settings.json
git diff HEAD` as the same command), and an unquoted `~` names a home file
without an absolute path or a `..` in the word as typed. The deny rules bind
the `Read` and `Edit` tools, not a shell command, so the hook is the only
thing standing between the allow list and `secrets.yaml` or `.claude/hooks/`.

The redirection is not git's alone: nine other allow rows carry a trailing
`:*`, and `python3 scripts/run_tests.py >.claude/settings.json` was approved
on its prefix while bash truncated the settings file. The hook refuses an
output redirection on a segment that runs one of those commands too, and the
list of them is derived from the settings file here, so a `:*` row added there
fails until the hook lists it.

An assignment written in front of the command name is part of that approved
string too, and git reads program names out of its environment:
`GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD` runs `prog` once per changed
path. The hook refuses an assignment before any of these commands, git
included.

Each test feeds the committed script a payload on stdin, the way Claude Code
does, and checks the exit code: 0 lets the call through, 2 blocks it and puts
the reason on stderr. The script is run by its own path so a lost executable
bit or a broken shebang fails here too.
"""

from __future__ import annotations

import json
import os
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
        # Redirections that write no path, and redirections on some other
        # command of the same string, are not git's.
        "git diff HEAD 2>&1",
        "git diff HEAD >&2",
        "git diff HEAD 1>&2",
        "git diff HEAD >&-",
        "git diff HEAD <<<''",
        "git diff HEAD 2>&1 | jq .",
        "git diff HEAD | jq . > out",
        "echo x > out; git diff HEAD",
        "echo x >> out && git diff HEAD",
        ">out echo x; git diff HEAD",
        ">out cat f | git diff --stat",
        "git status 2>&1; git diff HEAD",
        'git commit -m "x > y"',
        'git commit -m "a; b" | cat',
        "git diff -- 'a;b'",
        'echo "x)" ; git diff HEAD',
        "{fd}>out echo x; git diff HEAD",
        "2>&1 git diff HEAD",
        ">&2 git diff HEAD",
        # A tilde that does not lead the word, or that bash leaves alone.
        "git diff -- 'lit~eral'",
        "git diff HEAD -- x~",
        "git show HEAD:~/x",
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
        # An unquoted leading `~` is a home directory to bash, in any gated
        # subcommand, on either side of `--`, and for a named user too.
        ("git diff -- ~/.aws/credentials ~/.bashrc", "~/.aws/credentials"),
        ("git diff ~/.bashrc ~/.aws/credentials", "~/.bashrc"),
        ("git diff -- ~ ~/.bashrc", "~"),
        ("git diff -- ~root/.bashrc README.md", "~root/.bashrc"),
        ("git log -p -- ~/.ssh/config", "~/.ssh/config"),
        ("git show HEAD -- ~/.ssh/config", "~/.ssh/config"),
        ("git status; git diff -- ~/.aws/credentials ~/.bashrc", "~/.aws/credentials"),
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


@pytest.mark.parametrize(
    "command",
    [
        # Bash expands a brace only when a comma or a `..` range sits inside
        # it; any other brace is a literal, and git's own `@{...}` revision
        # syntax is spelled with exactly that. `git diff HEAD@{1}` is the
        # ordinary diff against the previous commit and touches none of the
        # gated options, so a gate that refused it was a false positive with
        # a real cost. The last case pins that a `{` which never closes is a
        # literal too.
        "git diff HEAD@{1}",
        "git diff HEAD@{1} -- docs/SECURITY-AI.md",
        "git log main@{upstream} -1",
        "git log @{2.days.ago} -1",
        "git show @{-1}",
        "git log HEAD@{1 -1",
    ],
)
def test_a_brace_bash_would_not_expand_is_left_alone(command: str) -> None:
    """Git's literal `@{...}` revision syntax passes."""

    completed = _run(_payload(command))
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        # The line is drawn where bash draws it, and errs toward refusing.
        # `@{1,2}` reads as revision syntax and is two words to bash; `{x..x}`
        # is a one-element sequence that rebuilds the option; a comma nested
        # one level down still expands (`{{a,b}}` is `{a} {b}`); and `${VAR}`
        # is a runtime-built argument the hook cannot inspect.
        "git diff HEAD@{1,2}",
        "git diff --no-inde{x..x} README.md docs/SECURITY-AI.md",
        "git diff {{README.md,secrets.yaml}}",
        "git diff ${SECRET} HEAD",
        # Mismatched braces (#225 review). Bash pairs the `{` with the last
        # `}` it can, so `{--src-prefix=x},--no-index}` becomes
        # `--src-prefix=x}` and `--no-index`. A depth counter that closed the
        # brace at the first `}` never saw the comma and let both through.
        "git diff {--src-prefix=x},--no-index} .env secrets.yaml",
        "git log {--format=%h},--output=.claude/settings.json} -1",
        # A quoted comma or a quoted operator inside the brace does not hide
        # the shape: bash still expands `{a",",b}` (the unquoted comma
        # splits) and `{a';',b}` (the quoted `;` is part of the word).
        'git diff {README.md",",secrets.yaml}',
        "git diff {README.md';',secrets.yaml}",
        # Refused although bash would not expand it: a `..` between two
        # reflog entries has the shape of a range inside a brace, and the
        # test does not track which `}` closes which `{`. The refusal says
        # to write HEAD~2..HEAD~1.
        "git log HEAD@{2}..HEAD@{1}",
    ],
)
def test_the_brace_test_is_what_bash_would_expand_not_the_spelling(
    command: str,
) -> None:
    """A brace bash would expand is still refused, at any depth."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"
    assert "expanded by the shell" in completed.stderr


@pytest.mark.parametrize(
    "command",
    [
        # bash replaces `<(...)` and `>(...)` with a /dev/fd path before git
        # runs, and a path under /dev is outside the repository, so `git diff
        # <(true) secrets.yaml` implies --no-index and prints secrets.yaml
        # whole. shlex breaks the word at the `(` and hands back `<(` on its
        # own, which spells no option and resolves to a path inside the
        # repository, so nothing refused it.
        "git diff <(true) secrets.yaml",
        "git diff <(true) ./secrets.yaml",
        "git diff -- <(true)",
        "git diff HEAD >(cat)",
        "git log -p <(true)",
        "git show HEAD:README.md <(true)",
    ],
)
def test_a_process_substitution_in_a_gated_command_is_refused(command: str) -> None:
    """A word that opens `<(` or `>(` is refused before it is resolved."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"
    assert "expanded by the shell" in completed.stderr


def test_git_really_prints_the_file_beside_a_process_substitution(
    tmp_path: Path,
) -> None:
    """The reach the substitution rule exists for, run for real.

    A stand-in file in a throwaway repository, never the real secrets.yaml:
    the test is that git prints it, and the hook has to refuse the command
    that does.
    """

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    (tmp_path / "secrets.yaml").write_text("STAND-IN-NOT-A-SECRET\n", encoding="utf-8")
    shown = subprocess.run(
        ["bash", "--norc", "--noprofile", "-c", "git diff <(true) ./secrets.yaml"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert "STAND-IN-NOT-A-SECRET" in shown.stdout, (
        "git diff no longer prints the file beside a process substitution; "
        "the substitution rule in the hook may be more than is needed"
    )
    completed = _run(_payload("git diff <(true) ./secrets.yaml"))
    assert completed.returncode == 2, (
        "the command just shown to print a file was not blocked"
    )


@pytest.mark.parametrize(
    ("command", "spelling"),
    [
        # The shell's own spelling of --output: every operator with a `>` in
        # it opens its target for writing before git runs, whatever the
        # target, and `<>` creates the file as well.
        ("git diff HEAD >.claude/settings.json", ">.claude/settings.json"),
        ("git diff HEAD > .claude/settings.json", ">.claude/settings.json"),
        ("git diff HEAD >.claude/hooks/gate-git-file-arguments.py", ">.claude/hooks/"),
        ("git diff HEAD > out.patch", ">out.patch"),
        ("git log -1 >> out", ">>out"),
        ("git diff 2>err", "2>err"),
        ("git diff &>/dev/null", "&>/dev/null"),
        ("git diff &>>log", "&>>log"),
        ("git show HEAD >| x", ">|x"),
        ("git diff HEAD >&secrets.yaml", ">&secrets.yaml"),
        ("git diff HEAD <>secrets.yaml", "<>secrets.yaml"),
        ("git diff HEAD 2>&1 >out", ">out"),
        ("git log -1; git diff HEAD >out", ">out"),
        ("echo x | git diff HEAD >out", ">out"),
        # Bash lets the redirection precede the command name; the two
        # spellings are the same command, and `git status; >x git diff HEAD`
        # is allowed on its `git status` prefix.
        (">.claude/settings.json git diff HEAD", ">.claude/settings.json"),
        ("git status; >.claude/settings.json git diff HEAD", ">.claude/settings.json"),
        ("2>err git log -1", "2>err"),
        (">> out git show HEAD", ">>out"),
        ("FOO=bar >out git diff HEAD", ">out"),
        (">out git diff --no-index /dev/null secrets.yaml", ">out"),
        # The write is the shell's, so every allow-listed git subcommand
        # carries it, not only the three whose options are gated.
        ("git status >.claude/settings.json", ">.claude/settings.json"),
        ("git branch > out", ">out"),
        ("git add -n . 2>err", "2>err"),
        # A quoted or escaped separator is a word of the git command, not
        # the end of it: bash hands git the literal `;` and opens the target
        # first (review on #239).
        ("git diff ';' >.claude/settings.json", ">.claude/settings.json"),
        ("git diff '|' >out", ">out"),
        (r"git diff \; >out", ">out"),
        ('git diff "a;b" >out', ">out"),
        # Bash's `{name}>` allocates a descriptor into a variable.
        ("git status; {fd}>out git diff HEAD", "{fd}>out"),
        ("git diff HEAD {fd}>out", "{fd}>out"),
    ],
)
def test_an_output_redirection_on_a_gated_git_is_refused(
    command: str, spelling: str
) -> None:
    """The write reaches every Edit deny rule the way --output does."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"
    assert "Blocked" in completed.stderr
    assert spelling in completed.stderr
    assert "read that instead" in completed.stderr


def test_bash_really_truncates_the_target_of_a_redirection_written_first(
    tmp_path: Path,
) -> None:
    """The reach the redirection rule exists for, run for real.

    A stand-in file in a throwaway repository: `>victim git diff HEAD HEAD`
    empties it before git prints anything, exactly as `git diff HEAD HEAD
    >victim` would, and the hook has to refuse both spellings.
    """

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    victim = tmp_path / "victim"
    victim.write_text("ORIGINAL-CONTENT\n", encoding="utf-8")
    subprocess.run(
        [
            "bash",
            "--norc",
            "--noprofile",
            "-c",
            "git status --short >/dev/null; >victim git diff HEAD HEAD",
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert "ORIGINAL-CONTENT" not in victim.read_text(encoding="utf-8"), (
        "bash no longer truncates the target of a redirection written before "
        "the command name; re-derive why the hook reads redirections per segment"
    )
    for command in (
        "git status; >victim git diff HEAD HEAD",
        "git diff HEAD HEAD >victim",
    ):
        completed = _run(_payload(command))
        assert completed.returncode == 2, f"{command!r} was not blocked"


# --------------------------------------------------------------------------
# The same write, on the allow-listed commands that are not git.
# --------------------------------------------------------------------------


def _gated_prefixes_from_settings() -> list[str]:
    """Return the `Bash(...:*)` allow rows other than git's, as command prefixes."""

    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    prefixes = [
        rule[len("Bash(") : -len(":*)")]
        for rule in settings["permissions"]["allow"]
        if rule.startswith("Bash(") and rule.endswith(":*)")
    ]
    return [prefix for prefix in prefixes if not prefix.startswith("git ")]


def test_every_allow_rule_with_arguments_is_refused_a_writing_redirection() -> None:
    """The list of gated commands lives in the hook; this keeps it honest.

    A rule ending in `:*` means "this command with any arguments", and a
    redirection is part of the string that rule matches, so every one of
    these writes any path the caller names unless the hook refuses it. The
    commands are derived from the settings file rather than restated, so a
    rule added there fails here until the hook lists it. The rows with no
    `:*` need no entry; `test_a_redirection_on_an_unlisted_command_is_left_alone`
    holds that half.
    """

    prefixes = _gated_prefixes_from_settings()
    assert len(prefixes) >= 9, prefixes
    for prefix in prefixes:
        command = f"{prefix} >.claude/settings.json"
        completed = _run(_payload(command))
        assert completed.returncode == 2, f"{command!r} was not blocked"
        assert ">.claude/settings.json" in completed.stderr
        assert prefix in completed.stderr


def test_the_gated_prefixes_are_exactly_the_settings_rows() -> None:
    """Neither the hook nor the settings file may carry a row the other lacks."""

    source = _HOOK.read_text(encoding="utf-8")
    start = source.index("_GATED_PREFIXES = (")
    end = source.index("\n)\n", start)
    listed = sorted(
        " ".join(json.loads(f"[{line.strip().strip('(),')}]"))
        for line in source[start:end].splitlines()[1:]
        if line.strip().startswith("(")
    )
    assert listed == sorted(_gated_prefixes_from_settings())


@pytest.mark.parametrize(
    ("command", "spelling"),
    [
        (
            "python3 scripts/run_tests.py >.claude/settings.json",
            ">.claude/settings.json",
        ),
        ("python3 scripts/run_tests.py tests/test_x.py >> out", ">>out"),
        (
            "python3 scripts/pr_metrics.py --json 2>docs/SECURITY-AI.md",
            "2>docs/SECURITY-AI.md",
        ),
        ("gh run view 1 --log >scripts/run_tests.py", ">scripts/run_tests.py"),
        ("gh pr diff 1 &>out", "&>out"),
        ("gh issue list >|out", ">|out"),
        ("gh run list <>out", "<>out"),
        ("gh pr view 1 >&out", ">&out"),
        ("gh issue view 1 2>&1 >out", ">out"),
        ("gh pr list >/dev/null", ">/dev/null"),
        # Bash lets the redirection precede the name; it is the same command,
        # and `git status; >out gh pr list` is allowed on its git prefix.
        (">out python3 scripts/run_tests.py", ">out"),
        ("git status; >out gh pr list", ">out"),
        ("FOO=1 python3 scripts/run_tests.py >out", ">out"),
        ("FOO=1 >out python3 scripts/run_tests.py", ">out"),
        ("time python3 scripts/run_tests.py >out", ">out"),
        ("command gh pr list >out", ">out"),
        ("gh pr view 1 {fd}>out", "{fd}>out"),
        # A `$(...)` is a command of its own; the gated command inside it is
        # decided on its own, redirection included.
        ("echo $(gh run view 1 --log >out)", ">out"),
        ("ls | gh pr list >out", ">out"),
        ("gh pr list 2>&1 | tee x; gh run list >out", ">out"),
        # A wrapper's own options come before the name it runs.
        ("command -p gh pr list >out", ">out"),
        ("env -i python3 scripts/run_tests.py >out", ">out"),
        # A comment after the write does not hide it, and a quoted `#` is
        # a word.
        ("gh pr list >out # ok", ">out"),
        ("gh pr list '#' >out", ">out"),
    ],
)
def test_an_output_redirection_on_a_gated_command_is_refused(
    command: str, spelling: str
) -> None:
    """The write reaches every Edit deny rule the way `git diff HEAD >out` does."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"
    assert "Blocked" in completed.stderr
    assert spelling in completed.stderr
    assert "read that instead" in completed.stderr


@pytest.mark.parametrize(
    "command",
    [
        # The refusal is the operator that opens a path for writing. A pipe,
        # a descriptor form and an input redirection open none, and AGENTS.md
        # tells a session to pipe the log output.
        "python3 scripts/run_tests.py 2>&1 | tail -5",
        "gh run view 123 --log-failed 2>&1 | sed 's/x/y/'",
        "gh pr view 1 --json title -q .title",
        "python3 scripts/run_tests.py <scripts/run_tests.py",
        "gh pr list >&2",
        "gh issue list 2>&-",
        "python3 scripts/run_tests.py tests -k 'a or b'",
        "python3 scripts/pr_metrics.py --limit 20 --json",
        "x=$(gh pr list); echo $x",
        # A `#` that begins a word after whitespace starts a comment bash
        # drops through the end of the line; the next line is still read.
        "gh pr list # output > file",
        "python3 scripts/run_tests.py # >(cat >out)",
        "git diff HEAD # > out",
        "gh pr list #c\ngh run list",
        "command -v gh",
        # The substitution is the outer command's; its body is the gated
        # command, decided on its own.
        "echo $(gh pr list)",
        "for n in $(gh pr list --json number -q '.[].number'); do echo $n; done",
        # An assignment on another command is left alone.
        "FOO=1 echo x; gh pr list",
        "x=1; gh pr list",
    ],
)
def test_reading_the_output_of_a_gated_command_still_works(command: str) -> None:
    """The allow list exists so these run without a prompt; keep it that way."""

    completed = _run(_payload(command))
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        # A command no allow rule covers prompts on its own, and refusing it
        # here would be the hook deciding a question the settings file already
        # decides; the three exact rows carry no `:*`, so a redirection makes
        # the string match none of them. A redirection on another command of
        # the same string is that command's own. The last two are refused by
        # the Bash tool itself before any rule or hook sees them ("does not
        # accept compound statements with redirection", Claude Code 2.1.267).
        "echo x >out",
        "ruff check . >out",
        "python3 scripts/check_requirements_sync.py >out",
        "python3 scripts/other_script.py >out",
        "echo x >out; gh pr list",
        "gh pr list | tee out",
        ">out echo x; python3 scripts/run_tests.py",
        "gh pr list; { gh run list; } >out",
        "(gh pr list) >out",
        "cat <(gh pr list) >out",
        "diff <(gh pr list) <(gh run list)",
    ],
)
def test_a_redirection_on_an_unlisted_command_is_left_alone(command: str) -> None:
    """The hook re-gates what the permission rules wave through, nothing more."""

    completed = _run(_payload(command))
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "command",
    [
        # A process substitution written as an argument runs its body as
        # part of the approved string, and the body is held to no rule.
        "gh pr list >(cat >out)",
        ">(cat >out) gh pr list",
        "python3 scripts/run_tests.py <(true)",
        "git status; gh run list >(tee out)",
        # A process substitution is a nested command as well, so the outer
        # command's redirection is still its own; the substitution is what
        # the refusal names.
        "python3 scripts/run_tests.py <(true) >out",
        "gh pr list >(cat) 2>out",
        # A command substitution or a backtick runs its body the same way.
        "python3 scripts/run_tests.py $(printf x >.claude/settings.json)",
        "python3 scripts/run_tests.py $(date) >out",
        "gh pr list `printf x >out`",
        "python3 scripts/run_tests.py tests -k $K",
        'gh pr view 1 --json "$F"',
        # A quoted `$` is refused with the rest, as it is in a gated git
        # command: the word is judged by its characters, not its quoting.
        "gh pr list --search 'a $b'",
    ],
)
def test_a_process_substitution_in_a_gated_command_that_is_not_git_is_refused(
    command: str,
) -> None:
    """The inner command is not this hook's to inspect, so the form is refused."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"
    assert "substitution" in completed.stderr


@pytest.mark.parametrize(
    "command",
    [
        # An assignment before the name is an environment the command runs
        # under: pytest reads PYTEST_ADDOPTS after the wrapper has inspected
        # only sys.argv, so `--junitxml` reaches it unseen (review on #244).
        "PYTEST_ADDOPTS=--junitxml=.claude/settings.json python3 scripts/run_tests.py tests",
        "PYTHONPATH=/tmp python3 scripts/pr_metrics.py",
        "GH_HOST=other gh pr list",
        "FOO=1 gh run list",
    ],
)
def test_an_assignment_before_a_gated_command_that_is_not_git_is_refused(
    command: str,
) -> None:
    """The wrapper cannot see its environment, so the hook refuses it."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"
    assert "assignment" in completed.stderr


@pytest.mark.parametrize(
    "command",
    [
        # git reads the names of programs to run out of its environment, and
        # the allow rule matches `git diff` on its prefix, so the assignment
        # in front of it is part of the approved string.
        "GIT_EXTERNAL_DIFF=/tmp/prog git diff HEAD~1 HEAD",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external "
        "GIT_CONFIG_VALUE_0=/tmp/prog git diff HEAD",
        "LD_PRELOAD=/tmp/lib.so git log -1",
        "GIT_PAGER=/tmp/prog git --paginate log -1",
        "PAGER=cat git log -1",
        # Behind `env` the assignment still stands before the command name.
        "env GIT_EXTERNAL_DIFF=/tmp/prog git show HEAD",
        # The subcommand does not matter: `git status`, `git branch` and
        # `git add` are allow-listed too, and the environment is git's either
        # way.
        "GIT_EXTERNAL_DIFF=/tmp/prog git status",
        # A second git command in the same string is judged on its own.
        "git diff HEAD; GIT_EXTERNAL_DIFF=/tmp/prog git log -1",
    ],
)
def test_an_assignment_before_git_is_refused(command: str) -> None:
    """`GIT_EXTERNAL_DIFF=prog git diff` runs prog once per changed path."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"
    assert "assignment" in completed.stderr


@pytest.mark.parametrize(
    "command",
    [
        # bash's append form. `NAME+=value` creates the variable when it is
        # unset, so this is the same environment as `NAME=value`; the hook
        # split on the first `=` and read the left half as an identifier,
        # which answered no for `GIT_EXTERNAL_DIFF+`, and the whole word went
        # on to be read as the command's name.
        "GIT_EXTERNAL_DIFF+=/tmp/prog git diff HEAD~1 HEAD",
        "LD_PRELOAD+=/tmp/lib.so git log -1",
        "PYTHONPATH+=/tmp python3 scripts/run_tests.py",
        "GH_HOST+=other gh pr list",
        # The export family: the same assignment written after the command
        # name instead of before it. Bash applies it to every command it runs
        # later in the string, so the git invocation carries none of its own.
        "export GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD~1 HEAD",
        "declare -x GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD~1 HEAD",
        "typeset -x GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD~1 HEAD",
        "readonly PATH=/tmp/bin; git diff HEAD",
        "export PYTHONPATH=/tmp; python3 scripts/run_tests.py",
        "export GH_HOST=other && gh pr list",
        "export GIT_EXTERNAL_DIFF=/tmp/prog\ngit show HEAD",
        # `env -S` splits a quoted string into a command, and the whole
        # invocation is one word to every scan below.
        "env -S 'GIT_EXTERNAL_DIFF=/tmp/prog git diff HEAD~1 HEAD'",
        "env -S'GIT_EXTERNAL_DIFF=/tmp/prog git diff HEAD'",
        "env --split-string='GIT_EXTERNAL_DIFF=/tmp/prog git log -1'",
    ],
)
def test_the_other_spellings_of_the_same_assignment_are_refused(
    command: str,
) -> None:
    """Three ways to put a variable in the command's environment, all refused.

    Each reaches the program `GIT_EXTERNAL_DIFF` names exactly as the leading
    `NAME=value` form does; the test below runs all three for real rather than
    arguing them from bash's manual.
    """

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"


@pytest.mark.parametrize(
    "command",
    [
        # An export reaches what bash runs *after* it and nothing earlier.
        "git diff HEAD; export GIT_EXTERNAL_DIFF=/tmp/prog",
        "git status && export FOO=1",
        # An export in a string that runs none of these commands is not this
        # hook's business; such a string matches no allow rule and prompts.
        "export GIT_EXTERNAL_DIFF=/tmp/prog",
        "export FOO=1; echo hi",
        "declare -x FOO=1; echo hi",
        # The builtins that set nothing.
        "export; git diff HEAD",
        "declare -p; git diff HEAD",
        # The word `export` as text rather than as a command name.
        "echo export FOO=1; git diff HEAD",
        "git log --grep=export -1",
        # `env -S` with nothing this hook gates inside it.
        "env -S 'echo hi'",
    ],
)
def test_an_export_that_reaches_no_gated_command_is_left_alone(
    command: str,
) -> None:
    """Over-refusing here would block ordinary work, so the rule is ordered."""

    completed = _run(_payload(command))
    assert completed.returncode == 0, f"{command!r} was blocked: {completed.stderr}"


@pytest.mark.parametrize(
    "command",
    [
        # A newline ends a simple command in bash exactly as a `;` does. It is
        # plain whitespace to shlex, so the words of both lines used to land
        # in one segment and the assignment on the second line stopped being a
        # leading one -- the position every rule below reads it in.
        "echo hi\nGIT_EXTERNAL_DIFF=/tmp/prog git diff HEAD",
        "git diff HEAD\nGIT_EXTERNAL_DIFF=/tmp/prog git log -1",
        "echo hi\nPYTHONPATH=/tmp python3 scripts/run_tests.py",
        "echo hi\nGH_HOST=other gh pr list",
        # The same for the redirection rule, which the merge over-refused
        # rather than under-refusing, and which must still refuse this.
        "echo hi\ngit diff HEAD >out",
    ],
)
def test_a_newline_ends_the_command_the_way_bash_ends_it(command: str) -> None:
    """Two lines are two commands, and each is judged on its own."""

    completed = _run(_payload(command))
    assert completed.returncode == 2, f"{command!r} was not blocked"


@pytest.mark.parametrize(
    "command",
    [
        # A `\`-continuation is not a command boundary: bash joins the lines.
        "git diff \\\n  HEAD",
        # A newline inside quotes is a character of its word.
        "git log --grep='one\ntwo' -1",
        # Two ordinary commands on two lines stay unprompted.
        "echo hi\ngit diff HEAD",
        "git status\ngit log -1",
    ],
)
def test_a_newline_that_is_not_a_boundary_is_left_alone(command: str) -> None:
    """Over-refusing a continuation would block ordinary multi-line work."""

    completed = _run(_payload(command))
    assert completed.returncode == 0, f"{command!r} was blocked: {completed.stderr}"


def test_every_spelling_really_reaches_the_external_diff_program(
    tmp_path: Path,
) -> None:
    """The three refusals above, run for real against git.

    `NAME+=value` with the variable unset, an `export` on an earlier command
    of the same string, and `env -S` each put the program in git's environment
    and git runs it once per changed path -- the same reach as the leading
    `NAME=value` form the test below covers.
    """

    marker = tmp_path / "ran"
    program = tmp_path / "prog.sh"
    program.write_text(f"#!/bin/sh\nprintf x >>{marker}\n", encoding="utf-8")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)

    work = tmp_path / "work"
    work.mkdir()
    tracked = work / "tracked.txt"

    def _git(*arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=str(work),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )

    _git("init", "-q")
    _git("config", "user.email", "test@example.invalid")
    _git("config", "user.name", "test")
    tracked.write_text("one\n", encoding="utf-8")
    _git("add", "tracked.txt")
    _git("commit", "-q", "-m", "one")
    tracked.write_text("two\n", encoding="utf-8")

    for spelling in (
        f"GIT_EXTERNAL_DIFF+={program} git diff",
        f"export GIT_EXTERNAL_DIFF={program}; git diff",
        f"env -S 'GIT_EXTERNAL_DIFF={program} git diff'",
    ):
        subprocess.run(
            ["bash", "-c", spelling],
            cwd=str(work),
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert marker.exists(), "no spelling reached the external diff program"
    assert marker.read_text(encoding="utf-8") == "xxx", (
        "a spelling this hook refuses did not in fact run the program: "
        f"{marker.read_text(encoding='utf-8')!r}"
    )


def test_git_really_runs_the_program_named_by_the_environment(
    tmp_path: Path,
) -> None:
    """The refusal is not theoretical: git executes GIT_EXTERNAL_DIFF itself."""

    marker = tmp_path / "ran"
    program = tmp_path / "prog.sh"
    program.write_text(f"#!/bin/sh\nprintf ran >{marker}\n", encoding="utf-8")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)

    work = tmp_path / "work"
    work.mkdir()
    tracked = work / "tracked.txt"

    def _git(*arguments: str, **environment: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=str(work),
            env={**os.environ, **environment},
            capture_output=True,
            text=True,
            check=True,
        )

    _git("init", "-q")
    _git("config", "user.email", "test@example.invalid")
    _git("config", "user.name", "test")
    tracked.write_text("one\n", encoding="utf-8")
    _git("add", "tracked.txt")
    _git("commit", "-q", "-m", "one")
    tracked.write_text("two\n", encoding="utf-8")

    _git("diff", GIT_EXTERNAL_DIFF=str(program))

    assert marker.exists(), "git did not run the external diff program"


def test_a_comment_is_dropped_only_where_bash_drops_it() -> None:
    """A `#` after whitespace starts a comment; elsewhere it is a character."""

    completed = _run(_payload("gh pr list #c\ngh run list >out"))
    assert completed.returncode == 2, (
        "the command on the line after a comment was hidden"
    )
    completed = _run(_payload("git diff --stat#x /dev/null secrets.yaml"))
    assert completed.returncode == 2, "a mid-word # hid the operands after it"


def test_bash_really_truncates_the_target_on_a_command_that_is_not_git(
    tmp_path: Path,
) -> None:
    """The reach the rule exists for, run for real.

    A stand-in in a throwaway directory where the wrapper does not even exist:
    bash opens the target before python3 runs, so the file is emptied although
    the command then fails.
    """

    victim = tmp_path / "victim"
    victim.write_text("ORIGINAL-CONTENT\n", encoding="utf-8")
    completed = subprocess.run(
        ["bash", "--norc", "--noprofile", "-c", "python3 scripts/run_tests.py >victim"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode != 0, "the stand-in command was meant to fail"
    assert "ORIGINAL-CONTENT" not in victim.read_text(encoding="utf-8"), (
        "bash no longer truncates the target before the command runs; re-derive "
        "why _GATED_PREFIXES exists"
    )
    completed = _run(_payload("python3 scripts/run_tests.py >victim"))
    assert completed.returncode == 2, (
        "the command just shown to truncate a file was not blocked"
    )


def test_git_really_reads_a_home_file_named_with_a_tilde(tmp_path: Path) -> None:
    """The reach the tilde rule exists for, run for real with a throwaway HOME.

    bash expands `~` before git runs, so `git diff -- ~/.aws/credentials
    ~/.bashrc` is a plain-file diff of two home files that neither start with
    `/` nor carry a `..` as typed. The hook reads the `~` lexically and
    refuses it as outside the repository whatever HOME is.
    """

    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "credentials").write_text(
        "STAND-IN-NOT-A-SECRET\n", encoding="utf-8"
    )
    (home / ".bashrc").write_text("export FIXTURE=1\n", encoding="utf-8")
    shown = subprocess.run(
        [
            "bash",
            "--norc",
            "--noprofile",
            "-c",
            "git diff -- ~/.aws/credentials ~/.bashrc",
        ],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(home)},
        check=False,
    )
    assert "STAND-IN-NOT-A-SECRET" in shown.stdout, (
        "git diff no longer prints a home file named through ~; the tilde rule "
        "in the hook may be more than is needed"
    )
    for env_home in (str(home), str(_ROOT)):
        completed = subprocess.run(
            [str(_HOOK)],
            input=_payload("git diff -- ~/.aws/credentials ~/.bashrc"),
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": os.environ.get("PATH", ""), "HOME": env_home},
            check=False,
        )
        assert completed.returncode == 2, (
            f"the command just shown to read a home file was not blocked with HOME={env_home}"
        )


def test_bash_really_truncates_behind_a_quoted_separator(tmp_path: Path) -> None:
    """The reach the quote-aware split exists for, run for real.

    `git diff ';' >victim` hands git a literal `;` and fails, but bash has
    opened `victim` for writing first; a split that read the `;` as a
    separator put the redirection in a segment with no git in it.
    """

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    victim = tmp_path / "victim"
    victim.write_text("ORIGINAL-CONTENT\n", encoding="utf-8")
    subprocess.run(
        ["bash", "--norc", "--noprofile", "-c", "git diff ';' >victim"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert "ORIGINAL-CONTENT" not in victim.read_text(encoding="utf-8"), (
        "bash no longer truncates the target of a redirection on a command "
        "carrying a quoted separator; re-derive why the split masks quotes"
    )
    completed = _run(_payload("git diff ';' >victim"))
    assert completed.returncode == 2, (
        "the command just shown to truncate was not blocked"
    )


@pytest.mark.parametrize(
    "command",
    [
        "diff <(ls a) <(ls b)",
        "cat <(echo x)",
    ],
)
def test_a_process_substitution_with_no_gated_git_is_not_gated(command: str) -> None:
    """A command string that runs no gated git is not this hook's business.

    The scope is the whole string, as it is for every shell-expansion rule
    here: `echo $x; git diff` is refused too, because `_runs_gated_git`
    marks the payload as gated wherever the git falls in it. So this pins
    only that the rule stays off when no gated git is present, not that a
    substitution in another command of the same string passes.
    """

    completed = _run(_payload(command))
    assert completed.returncode == 0, completed.stderr


def test_the_reflog_range_refusal_names_the_spelling_to_use() -> None:
    """The over-refusal has a cost; the message pays it back."""

    completed = _run(_payload("git log HEAD@{2}..HEAD@{1}"))
    assert completed.returncode == 2
    assert "HEAD~2..HEAD~1" in completed.stderr


# --------------------------------------------------------------------------
# The brace rule, checked against bash rather than against a hand-written
# expectation. Each word below is put through bash's own expansion, and what
# bash does is the ground truth: a word it turns into more than one is one
# the hook must refuse. The literal set - git's `@{...}` revision syntax - is
# asserted the other way, allowed. Words in neither class are only held to
# the first rule, so an over-refusal there is not a failure.
# --------------------------------------------------------------------------

# Git's revision syntax. Bash leaves each of these alone and the hook must
# too; `HEAD@{1` pins that an unclosed brace is a literal as well.
_LITERAL_BRACE_WORDS = (
    "HEAD@{1}",
    "main@{upstream}",
    "@{-1}",
    "@{2.days.ago}",
    "HEAD@{1",
)

# Everything the rule has to get right, in one place: the literal set, the
# ordinary expansions, the two bypasses found in review (mismatched braces,
# a quoted operator inside the brace), quoted and escaped commas, nesting,
# ranges, `${VAR}`, mismatched forms in both directions, braces after
# `--output`, and quoted jq/awk programs that bash leaves alone. Each word is
# inserted verbatim into a bash script, so the quoting is bash's.
_BRACE_CORPUS = _LITERAL_BRACE_WORDS + (
    "HEAD@{2}..HEAD@{1}",
    "{a,b}",
    "{1..3}",
    "x{1..3}y",
    "a{,b}",
    "{{a,b}}",
    "--no-inde{x,x}",
    "--outpu{t,t}=FILE",
    "HEAD@{1,2}",
    "{--src-prefix=x},--no-index}",
    "{a},b}",
    "{/tmp/reference';',./cosign.key}",
    '{a",",b}',
    "{a\\,b,c}",
    '"{a,b}"',
    "'{a,b}'",
    "{a,b",
    "{a,b}}",
    "{{a,b}",
    "${OPERANDS}",
    "--output={a,b}",
    "--output=x{,}",
    "'{print $1}'",
    "'{a:1}'",
    "'{a: .x, b: .y}'",
)


def _bash_expands(word: str) -> bool:
    """Whether bash turns `word` into more than one word.

    The word is inserted verbatim into the script text on purpose: the
    corpus is this file's, and the point is to hand bash the spelling the
    agent would type. `OPERANDS` is set so that `${OPERANDS}` splits into two
    words the way a runtime-built argument would.
    """

    completed = subprocess.run(
        ["bash", "--norc", "--noprofile", "-c", 'printf "%s\\0" ' + word],
        capture_output=True,
        env={"PATH": os.environ.get("PATH", ""), "OPERANDS": "/dev/null secrets.yaml"},
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.count(b"\0") > 1


def test_the_corpus_is_large_enough_and_bash_agrees_with_its_labels() -> None:
    """A corpus that shrank, or one bash reads differently, is a broken test."""

    assert len(_BRACE_CORPUS) >= 25
    assert len(set(_BRACE_CORPUS)) == len(_BRACE_CORPUS)
    for word in _LITERAL_BRACE_WORDS:
        assert not _bash_expands(word), f"bash expands {word!r}; it is not literal"
    expanding = [word for word in _BRACE_CORPUS if _bash_expands(word)]
    assert len(expanding) >= 15, expanding


@pytest.mark.parametrize("word", _BRACE_CORPUS)
def test_every_word_bash_expands_is_refused(word: str) -> None:
    """The hook is checked against bash, not against a hand-written label."""

    completed = _run(_payload(f"git diff {word}"))
    if _bash_expands(word):
        assert completed.returncode == 2, (
            f"bash expands {word!r}; the hook let it through"
        )
        assert "expanded by the shell" in completed.stderr


@pytest.mark.parametrize("word", _LITERAL_BRACE_WORDS)
def test_every_literal_word_is_allowed(word: str) -> None:
    """Git's `@{...}` syntax, which bash leaves alone, is not refused."""

    completed = _run(_payload(f"git log {word} -1"))
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""


@pytest.mark.parametrize(
    "command",
    [
        "jq '{a: .x, b: .y}' tests/sample.json",
        "awk '{print $1}' README.md",
    ],
)
def test_a_brace_outside_a_git_invocation_is_not_gated(command: str) -> None:
    """The gate is about git's arguments; a jq or awk program is not one."""

    completed = _run(_payload(command))
    assert completed.returncode == 0, completed.stderr


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
