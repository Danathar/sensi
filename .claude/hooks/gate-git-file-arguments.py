#!/usr/bin/env python3
"""Refuse the `git` arguments that read and write files the tool rules deny.

Wired to PreToolUse for `Bash` in `.claude/settings.json`. `git diff`, `git log`
and `git show` are on the allow list, so they run with no permission prompt, and
three of their arguments do file I/O that has nothing to do with the repository:

* `--no-index PATH PATH` diffs two paths on disk, inside the repository or not,
  and prints both files' contents. It reads `secrets.yaml`, `.env` and
  `config/**` straight past the `Read` deny rules, which bind the `Read` tool
  and not a shell command. Git also enters `--no-index` mode implicitly when
  `git diff` names at least one path outside the working tree, even when the
  option is omitted, so any path pointing outside the repository is refused too.
* `--output PATH` (and `--output=PATH`) creates or truncates any path before it
  writes the diff into it, which reaches every `Edit` deny rule the same way -
  `.claude/settings.json`, `.claude/hooks/**`, `scripts/run_tests.py`,
  `docs/SECURITY-AI.md` and `.github/workflows/**`.
* `--orderfile PATH` / `-O PATH` makes git open a path to read sort patterns
  out of it.

No permission rule can close this. `deny` matching is by command prefix, and
every one of these is an option that can be written anywhere in the argument
list, so a rule can only ever name one spelling of one position.

Exit 2 blocks the call and returns the message on stderr to the agent. Every
other path exits 0: a gate that fails closed on its own bugs would make an
unparseable payload look like an attack.
"""

import json
from pathlib import Path
import shlex
import sys

_REPO = Path(__file__).resolve().parents[2]

# The allow-listed read-only subcommands. `git commit`, `git add` and the rest
# take none of the options below, and denying a brace or a glob inside a commit
# message would be a refusal with no threat behind it.
_GATED_SUBCOMMANDS = frozenset({"diff", "log", "show"})

# Compared against the name of a long option with any `=value` removed. The
# match is "this dangerous name starts with what was typed", so every
# abbreviation git would accept - `--no-inde`, `--outpu`, `--o` - is refused
# too, without this file having to track which abbreviations are ambiguous in
# which git version.
_REFUSED_LONG = ("no-index", "output", "orderfile")

# `-O` is `--orderfile` spelled short, and git accepts it clustered with other
# short options (`-tO/etc/passwd`), so the letter is looked for anywhere in the
# cluster rather than only at its head.
_REFUSED_SHORT = "O"

# shlex quotes and splits, and does nothing else: no brace expansion, no
# pathname expansion, no substitution. A word holding one of these characters
# is a word whose final form this script cannot see, and every one of them can
# rebuild a refused option out of pieces that are not refused -
# `--no-inde{x,x}`, `--outpu[t]`, `$F`, `$(printf -- --no-index)`. A brace is
# the one character here that bash sometimes leaves alone, so it is judged by
# `_brace_would_expand` below rather than by its presence.
_UNEXPANDED = "*?[]$`"


def _brace_would_expand(word: str) -> bool:
    """Whether bash would brace-expand `word` before git sees it.

    Bash's own rule, and only the half of it that matters here: a brace is
    expanded when a comma or a `..` sequence sits inside it - `{a,b}`,
    `{1..9}`, `a{,b}`, `{{a,b}}` - and is a literal otherwise. Git's own
    revision syntax relies on the literal form: `HEAD@{1}`, `main@{upstream}`
    and `@{-1}` reach git exactly as typed, and refusing them blocks the
    ordinary diff against the previous commit for no gain.

    This expands nothing; it asks whether bash would, and it errs toward yes.
    The comma or `..` is looked for at any depth, since `{{a,b}}` is `{a} {b}`
    to bash; a `{` that never closes counts; and `${VAR}` counts, as a
    runtime-built argument this script cannot inspect (the `$` in
    `_UNEXPANDED` refuses it first - this is the same answer by another
    route). What it never does is call a word literal that bash would
    rewrite: every expansion bash performs has a comma or `..` between a `{`
    and a `}`. A `..` *between* two literal braces (`HEAD@{2}..HEAD@{1}`) is
    not inside one and is left alone.
    """

    depth = 0
    for index, char in enumerate(word):
        pair = word[index : index + 2]
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(depth - 1, 0)
        elif (depth > 0 and (char == "," or pair == "..")) or pair == "${":
            return True
    return depth > 0


def _is_outside_repo(word: str) -> bool:
    """Whether `word` is a path pointing outside the repository.

    `git diff` enters `--no-index` mode implicitly when at least one path points
    outside the working tree, even when the option is omitted. Refusing paths
    outside the repository closes that loophole.
    """

    if word.startswith("-"):
        return False

    base = Path.cwd()
    if not base.is_relative_to(_REPO):
        base = _REPO

    try:
        path = Path(word).expanduser()
        resolved = (base / path).resolve()
        return not resolved.is_relative_to(_REPO)
    except ValueError, RuntimeError:
        return True


def _refusal(word: str) -> str | None:
    """Return why `word` is refused, or None if it is not."""

    if word.startswith("--"):
        name = word[2:].split("=", 1)[0]
        if name and any(refused.startswith(name) for refused in _REFUSED_LONG):
            return f"--{name} reads or writes a path outside the repository"
        return None

    if word.startswith("-") and _REFUSED_SHORT in word[1:]:
        return f"{word} carries -O, which makes git read an arbitrary path"

    if word != "git" and not word.endswith("/git") and _is_outside_repo(word):
        return f"{word} points outside the repository (implicit --no-index)"

    return None


def _runs_gated_git(words: list[str]) -> bool:
    """Whether `words` runs one of the gated git subcommands.

    Any word after a `git` counts, rather than the first word that does not
    look like an option: `git -c key=value diff` would otherwise be read as
    running `key=value` and skip the gate. The cost of the wider test is that
    `git commit -m "diff"` is gated too, which changes nothing - the refusals
    below still only fire on an option `git commit` does not take.
    """

    for index, word in enumerate(words):
        if word != "git" and not word.endswith("/git"):
            continue
        if any(later in _GATED_SUBCOMMANDS for later in words[index + 1 :]):
            return True
    return False


def main() -> int:
    """Block the call when the command is a gated git with a refused argument."""

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(command, str):
        return 0

    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # The default `#` comment character is why this is not `shlex.split`: bash
    # starts a comment only at the start of a word, shlex starts one mid-word,
    # so `--no-index a#b` would be read as `--no-index a` and the rest of the
    # command - the part doing the reading - would never be seen.
    lexer.commenters = ""
    try:
        words = list(lexer)
    except ValueError:
        # Unbalanced quoting. What bash would run is not what this saw, and the
        # command is malformed for bash too, so refusing costs nothing.
        print(
            "Blocked: the command could not be parsed as a shell command "
            "(unbalanced quote). Rewrite it and try again.",
            file=sys.stderr,
        )
        return 2

    if not _runs_gated_git(words):
        return 0

    for word in words:
        if any(char in word for char in _UNEXPANDED) or _brace_would_expand(word):
            print(
                f"Blocked: {word!r} is expanded by the shell, so the argument "
                "git receives is not the one written here. Spell the arguments "
                "of git diff, git log and git show out literally.",
                file=sys.stderr,
            )
            return 2

        reason = _refusal(word)
        if reason is not None:
            print(
                f"Blocked: {reason}. `git diff`, `git log` and `git show` run "
                "without a permission prompt, so their file-reading and "
                "file-writing options are refused here - see "
                "docs/SECURITY-AI.md. Read the file with the Read tool, which "
                "the deny list governs.",
                file=sys.stderr,
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
