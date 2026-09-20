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
* `<(true)` beside a path is `--no-index` again: bash substitutes a /dev/fd
  path, which is outside the repository, and git prints the other operand
  whole. The word reaches this script as a bare `<(`, so it is refused by
  that prefix along with `>(`.
* `git diff HEAD >.claude/settings.json` is `--output` in the shell's own
  spelling: bash opens the target for writing before git starts, so the file
  is truncated whatever git then prints, and `>>`, `>|`, `&>`, `2>err`,
  `>&file` and `<>file` each open a path the same way. Bash also lets the
  redirection precede the command name, so `>.claude/settings.json git diff
  HEAD` is the same command. An output redirection in a segment that runs
  git is refused wherever it is written, for every subcommand and not only
  the three gated ones, since `git status`, `git branch` and `git add` are
  allow-listed too and the write is the shell's, not git's; `2>&1`, an input
  redirection and a redirection on another command of the same string are
  left alone.
* `~/secrets.yaml` is `$HOME/secrets.yaml` to bash and, to a check that read
  the word as typed, a directory called `~` inside the repository. A word
  that begins with `~` is outside the repository by definition here, whatever
  `HOME` happens to be.

No permission rule can close this. `deny` matching is by command prefix, and
every one of these is an option that can be written anywhere in the argument
list, so a rule can only ever name one spelling of one position.

Exit 2 blocks the call and returns the message on stderr to the agent. Every
other path exits 0: a gate that fails closed on its own bugs would make an
unparseable payload look like an attack.
"""

import json
from pathlib import Path
import re
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

# Process substitution. bash replaces `<(command)` and `>(command)` with a
# /dev/fd path before git runs, so `git diff <(true) secrets.yaml` names a
# path outside the repository without spelling one, and git diff implies
# --no-index and prints secrets.yaml whole. shlex breaks the word at the `(`
# and hands back `<(` on its own, which carries none of `_UNEXPANDED` and
# resolves to a path inside the repository, so a word opening either way is
# refused by its prefix instead.
_PROCESS_SUBSTITUTION = ("<(", ">(")

# A `{`, then a `,` or a `..` somewhere after it, then a `}` somewhere after
# that. See `_brace_would_expand`.
_EXPANDING_BRACE = re.compile(r"\{.*(?:,|\.\.).*\}", re.DOTALL)

# A redirection operator as shlex hands it back: `punctuation_chars` glues a
# run of `<>&|` into one token, so `>`, `>>`, `>|`, `&>`, `&>>`, `>&`, `<`,
# `<<`, `<<<`, `<&` and `<>` each arrive whole, and the descriptor of `2>err`
# arrives as the token `2` before it. `|`, `|&`, `&&` and `||` carry no angle
# bracket and stay the separators they are; `<(` and `>(` carry a `(` and are
# the process substitutions above. See `_writing_redirection`.
_REDIRECTION = re.compile(r"^[<>&|]*[<>][<>&|]*$")

# The tokens that end one simple command and begin the next, so that a
# redirection is charged to the command it is written in: `echo x >out; git
# diff HEAD` is echo's redirection, `git diff HEAD | jq . >out` is jq's. A
# newline is whitespace to shlex and never a token, so two commands on two
# lines are read as one segment here, which can only over-refuse.
_SEPARATORS = frozenset({";", "&", "&&", "|", "||", "|&", "(", ")"})


def _brace_would_expand(word: str) -> bool:
    """Whether bash would brace-expand `word` before git sees it.

    The test is deliberately cruder than bash's own: a `{`, then a `,` or a
    `..` anywhere after it, then a `}` anywhere after that. Every expansion
    bash performs has that shape, so nothing bash would rewrite is called
    literal. Git's own revision syntax - `HEAD@{1}`, `main@{upstream}`,
    `@{-1}`, `@{2.days.ago}` - has no comma and no `..` inside the braces and
    stays allowed, as does a `{` that never closes, which bash leaves alone.

    No nesting or matching is tracked, on purpose. A depth counter that closes
    a brace at the first `}` misses the comma in `{--src-prefix=x},--no-index}`,
    which bash expands to `--src-prefix=x}` and `--no-index` (the `{` pairs
    with the *last* `}` it can), and every refinement toward bash's real
    matching rule is a chance to disagree with it in some other direction.
    Over-refusing is the safe direction: `HEAD@{2}..HEAD@{1}` is refused too,
    though bash would not expand it, and the refusal says to write
    `HEAD~2..HEAD~1`.

    `${VAR}` is refused as well - a runtime-built argument this script cannot
    inspect. The `$` in `_UNEXPANDED` refuses it first; this is the same
    answer by another route.

    The word checked is the shlex token: bash's word boundaries with the quote
    marks removed. Removing quotes never removes a brace, a comma or a dot, so
    a word bash would expand still has the shape here, and a quoted comma
    (`{a",",b}`) or a quoted operator (`{a';',b}`) cannot hide it. A fully
    quoted `"{a,b}"`, which bash leaves alone, is refused as the price of
    that.
    """

    return "${" in word or _EXPANDING_BRACE.search(word) is not None


def _segments(words: list[str]) -> list[list[str]]:
    """Split the words into simple commands at the shell's separators."""

    found: list[list[str]] = [[]]
    for word in words:
        if word in _SEPARATORS or (word and set(word) <= set(";&|")):
            found.append([])
            continue
        found[-1].append(word)
    return [segment for segment in found if segment]


def _writing_redirection(segment: list[str]) -> str | None:
    """Return the first redirection in `segment` that opens a path for writing.

    Every operator with a `>` in it does - `>`, `>>`, `>|`, `&>`, `&>>`, and
    `<>`, which opens read-write and creates the file - and so does `>&` when
    its target is a path (`>&file` is bash's older `&>file`). The exception
    is a target that names a descriptor: `>&1`, `2>&1` and `>&-` duplicate
    or close a descriptor and touch no path. `<`, `<<`, `<<<` and `<&` open
    nothing for writing. The spelling returned is the one typed, descriptor
    included, so the refusal can name it.
    """

    for index, word in enumerate(segment):
        if not _REDIRECTION.match(word) or ">" not in word:
            continue
        target = segment[index + 1] if index + 1 < len(segment) else ""
        if word.endswith("&") and (target.isdigit() or target == "-"):
            continue
        descriptor = (
            segment[index - 1] if index and segment[index - 1].isdigit() else ""
        )
        return f"{descriptor}{word}{target}"
    return None


def _is_outside_repo(word: str) -> bool:
    """Whether `word` is a path pointing outside the repository.

    `git diff` enters `--no-index` mode implicitly when at least one path points
    outside the working tree, even when the option is omitted. Refusing paths
    outside the repository closes that loophole.

    A leading `~` is outside by definition: bash expands it to a home
    directory before git runs, never to a path under this checkout, so the
    answer must not depend on where `HOME` points or whether it is set.
    """

    if word.startswith("-"):
        return False
    if word.startswith("~"):
        return True

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


def _runs_git(words: list[str]) -> bool:
    """Whether `words` name git at all, whichever subcommand follows."""

    return any(word == "git" or word.endswith("/git") for word in words)


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

    # The redirection is the shell's write, not git's, so it is refused on
    # every git segment: `git status >.claude/settings.json` is allow-listed
    # and truncates the file as surely as `git diff` would.
    for segment in _segments(words):
        if not _runs_git(segment):
            continue
        redirection = _writing_redirection(segment)
        if redirection is not None:
            print(
                f"Blocked: {redirection} makes the shell open a file for writing "
                "before git runs, which truncates it whatever git then prints - "
                "the same write --output makes, in the shell's own spelling, and "
                "one bash accepts before the command name as readily as after it. "
                "`git diff`, `git log` and `git show` print to stdout; read that "
                "instead. 2>&1, >&2, an input redirection, and a redirection on "
                "another command of the same string are not refused.",
                file=sys.stderr,
            )
            return 2

    if not _runs_gated_git(words):
        return 0

    for word in words:
        if (
            any(char in word for char in _UNEXPANDED)
            or _brace_would_expand(word)
            or word.startswith(_PROCESS_SUBSTITUTION)
        ):
            print(
                f"Blocked: {word!r} is expanded by the shell, so the argument "
                "git receives is not the one written here. Spell the arguments "
                "of git diff, git log and git show out literally. A brace is "
                "refused when a comma or a .. follows it before a }; a range "
                "between two reflog entries such as HEAD@{2}..HEAD@{1} is "
                "refused with it, so write HEAD~2..HEAD~1 instead.",
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
