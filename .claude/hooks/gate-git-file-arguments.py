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
* An assignment written before git is the same approval by another route, and
  git reads program names out of its environment.
  `GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD` runs `prog` once per changed
  path, with the two versions of the file in its arguments;
  `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external GIT_CONFIG_VALUE_0=prog`
  sets the same program through the config, and `LD_PRELOAD=lib.so` loads a
  library into git itself. `Bash(git diff:*)` matches the string on its
  prefix, and a leading assignment is part of what that prefix matches, so
  none of these prompts. An assignment before a git segment is refused the
  way one before the other allow-listed commands already was.
* `~/secrets.yaml` is `$HOME/secrets.yaml` to bash and, to a check that read
  the word as typed, a directory called `~` inside the repository. A word
  that begins with `~` is outside the repository by definition here, whatever
  `HOME` happens to be.
* The redirection is not git's alone. Nine other allow rows in
  `.claude/settings.json` carry a trailing `:*` - "this command with any
  arguments" - and an output redirection is part of the string that rule
  matches, so `python3 scripts/run_tests.py >.claude/settings.json`
  truncated the settings file before a test was collected (bash opens the
  target first, so the file is emptied even when the command then fails)
  and `gh run view 1 --log >scripts/run_tests.py` overwrote the wrapper
  that carries the test rules, both with no prompt; the wrapper's own
  refusal list covers pytest options, never a redirection it is never
  passed. Those commands are named in `_GATED_PREFIXES`, and an output
  redirection in a segment that runs one of them is refused the way one on
  a git segment is. Descriptor forms, input redirections, pipes and a
  command no allow rule covers are left alone - that one prompts on its
  own - and the rows with no `:*` (`ruff check .`, `ruff format --check .`,
  `python3 scripts/check_requirements_sync.py`) need no entry, because a
  redirection makes the string match none of them.

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

# The descriptor bash reads off the front of a redirection: the digits of
# `2>err`, or the `{name}` of `{fd}>file`, which allocates a descriptor into a
# variable. Named so the refusal can quote the spelling as typed.
_DESCRIPTOR = re.compile(r"^(?:[0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\})$")

# The tokens that end one simple command and begin the next, so that a
# redirection is charged to the command it is written in: `echo x >out; git
# diff HEAD` is echo's redirection, `git diff HEAD | jq . >out` is jq's. A
# newline is whitespace to shlex and never a token, so two commands on two
# lines are read as one segment here, which can only over-refuse.
_SEPARATORS = frozenset({";", "&", "&&", "|", "||", "|&", "(", ")"})

# The characters shlex glues into one word when they stand together; see
# `_punctuation_pieces`.
_PUNCTUATION = frozenset("();<>|&")

# The allow rows of `.claude/settings.json` that carry a trailing `:*`, other
# than git's, which `_runs_git` covers: each is a command prefix the
# permission layer approves with any arguments after it, and a shell output
# redirection is part of "any arguments". `test_git_file_argument_gate.py`
# derives this list from the settings file rather than restating it, so a
# rule added there fails until it is listed here. None of these commands takes
# a flag that names a file to write: `scripts/run_tests.py` refuses the pytest
# options that do (see its header), `scripts/pr_metrics.py` takes `--limit`,
# `--since`, `--repo` and `--json`, and the `gh` read verbs print to stdout.
_GATED_PREFIXES = (
    ("python3", "scripts/run_tests.py"),
    ("python3", "scripts/pr_metrics.py"),
    ("gh", "pr", "view"),
    ("gh", "pr", "diff"),
    ("gh", "pr", "list"),
    ("gh", "issue", "view"),
    ("gh", "issue", "list"),
    ("gh", "run", "view"),
    ("gh", "run", "list"),
)

# Words that stand before the name of the command they run and are not part
# of the prefix an allow rule matches, the way a leading `NAME=value` is not:
# `time python3 scripts/run_tests.py >out` is the wrapper's redirection. A
# wrapper's own options are not modelled (`env -i python3 ...` matches nothing
# here), and such a string matches no allow rule either, so it prompts.
_COMMAND_WRAPPERS = frozenset(
    {"time", "command", "builtin", "exec", "env", "nohup", "nice"}
)


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


def _mask_quotes(command: str) -> str:
    """Return the command with every quoted or escaped character replaced by `Q`.

    shlex removes the quotes as it splits, so the `;` of `git diff ';' >out`
    arrives as the same word as the `;` of `git diff; >out`, and a split that
    read it as a separator put the redirection in a segment with no git in it
    while bash handed git a literal `;` and truncated the file (review on
    #239). The masked copy keeps every quoted region the same length and free
    of shell syntax, so lexing it the same way gives one word per real word,
    and a word that is a separator in the masked copy is one bash would
    honour; a quoted one is not.
    """

    masked: list[str] = []
    quote = ""
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            masked.append("Q")
        elif quote:
            if char == quote:
                quote = ""
            elif quote == '"' and char == "\\":
                escaped = True
            masked.append("Q")
        elif char == "\\":
            escaped = True
            masked.append("Q")
        elif char in "'\"":
            quote = char
            masked.append("Q")
        else:
            masked.append(char)
    return "".join(masked)


def _strip_comments(command: str) -> str:
    """Return the command with every shell comment removed.

    A `#` that begins a word after whitespace (or the start of the string)
    starts a comment bash drops through the end of the line, so `python3
    scripts/run_tests.py # output > file` opens nothing and must not be
    refused for the `>` in the comment. The spans are found on the
    quote-masked copy, where a quoted `#` is a `Q`, and cut from the command
    itself; `_lex` is then handed a string with no comment in it, and its
    `commenters = ""` still holds for the `#` this leaves in place: one inside
    a word (`HEAD^#x`), which is a character of the word, and one straight
    after an operator (`;#`), which is kept as a word and can only
    over-refuse.
    """

    masked = _mask_quotes(command)
    kept: list[str] = []
    index = 0
    while index < len(command):
        if masked[index] == "#" and (index == 0 or masked[index - 1] in " \t\n"):
            end = masked.find("\n", index)
            index = len(command) if end == -1 else end
            continue
        kept.append(command[index])
        index += 1
    return "".join(kept)


def _lex(command: str) -> list[str]:
    """Split a command into bash's words, with operators as words of their own."""

    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # The default `#` comment character is why this is not `shlex.split`: bash
    # starts a comment only at the start of a word, shlex starts one mid-word,
    # so `--no-index a#b` would be read as `--no-index a` and the rest of the
    # command - the part doing the reading - would never be seen.
    lexer.commenters = ""
    words: list[str] = []
    for word in lexer:
        if len(word) > 1 and set(word) <= _PUNCTUATION:
            words.extend(_punctuation_pieces(word))
        else:
            words.append(word)
    return words


def _punctuation_pieces(run: str) -> list[str]:
    """Return a glued run of shell punctuation as the words bash reads it as.

    shlex glues a run of `();<>|&` into one word, so the `)` that closes a
    `$(...)` and the `;` after it arrive as `);`, which is neither the `)`
    that ends the nested command nor the separator that ends the outer one,
    and `x=$(gh pr list); echo $x` was read as one segment. Each paren is a
    word of its own, except that a `<` or `>` right before a `(` is the
    opening of a process substitution and stays with it; whatever lies
    between parens is kept whole, and is the separator or redirection it was.
    """

    pieces: list[str] = []
    for piece in re.split(r"([()])", run):
        if not piece:
            continue
        if piece == "(" and pieces and pieces[-1][-1] in "<>":
            piece = pieces[-1][-1] + piece
            pieces[-1] = pieces[-1][:-1]
            if not pieces[-1]:
                pieces.pop()
        pieces.append(piece)
    return pieces


def _segments(words: list[str], masked: list[str]) -> list[tuple[list[str], list[str]]]:
    """Split the words into simple commands at the separators bash honours.

    `masked` is the same list lexed from `_mask_quotes`; a word is a separator
    only when its masked twin is, so a quoted `;` or `|` stays a word of its
    command. Each segment is returned with its twins in the same order, so a
    check that needs to know what bash would quote can read the twin of the
    word it is judging.
    """

    found: list[tuple[list[str], list[str]]] = [([], [])]
    outer: list[tuple[list[str], list[str]]] = []
    for word, twin in zip(words, masked, strict=True):
        # A `$(...)` is a nested command: its words become a segment of their
        # own, and the command around it goes on after the `)` with the `$`
        # still in place, so `python3 scripts/run_tests.py $(date) >out` is
        # one command whose redirection is its own, and `echo $(gh run view 1
        # --log >out)` is a gh command with a redirection. A split that
        # ended the outer command at the `(` put the redirection in a
        # segment with no command in it.
        if twin == "(" and found[-1][0] and found[-1][1][-1].endswith("$"):
            outer.append(found.pop())
            found.append(([], []))
            continue
        if twin in _PROCESS_SUBSTITUTION:
            # A process substitution is a nested command too, and its `<(`
            # stays in the outer command as the operand it becomes, so
            # `python3 scripts/run_tests.py <(true) >out` is one command
            # whose redirection is its own, and the `<(` refusal for a gated
            # git still sees the word.
            found[-1][0].append(word)
            found[-1][1].append(twin)
            outer.append(found.pop())
            found.append(([], []))
            continue
        if twin == ")" and outer:
            found.append(outer.pop())
            continue
        if twin in _SEPARATORS or (twin and set(twin) <= set(";&|")):
            found.append(([], []))
            continue
        found[-1][0].append(word)
        found[-1][1].append(twin)
    return [segment for segment in found if segment[0]]


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
            segment[index - 1]
            if index and _DESCRIPTOR.match(segment[index - 1])
            else ""
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


def _command_words(segment: list[str]) -> tuple[list[str], bool, bool]:
    """Return the words the command in `segment` receives, and whether a wrapper led.

    A redirection - its operator, its target and a descriptor written before
    it - is the shell's, and bash lets it stand anywhere in the simple
    command, so `>out python3 scripts/run_tests.py` and `python3 >out
    scripts/run_tests.py` both come back as `['python3',
    'scripts/run_tests.py']`. A leading `NAME=value` and a leading wrapper
    word are stepped over, since neither is part of the prefix an allow rule
    matches. A `$` left behind by a nested `$(...)` is dropped from the end of
    a word, so the words are the ones typed around the substitution.
    """

    words: list[str] = []
    wrapped = False
    assigned = False
    index = 0
    while index < len(segment):
        word = segment[index]
        if _REDIRECTION.match(word):
            index += 2  # the operator and its target
            continue
        if (
            _DESCRIPTOR.match(word)
            and index + 1 < len(segment)
            and _REDIRECTION.match(segment[index + 1])
        ):
            index += 1  # the descriptor; the operator is next
            continue
        index += 1
        if word.startswith(_PROCESS_SUBSTITUTION):
            continue  # the substitution's opening; its body is a segment of its own
        if word.endswith("$"):
            word = word[:-1]
            if not word:
                continue
        if not words:
            name = word.split("=", 1)[0]
            if ("=" in word and name.isidentifier()) or word in _COMMAND_WRAPPERS:
                wrapped = wrapped or word in _COMMAND_WRAPPERS
                assigned = assigned or word not in _COMMAND_WRAPPERS
                continue
        words.append(word)
    return words, wrapped, assigned


def _gated_prefix(segment: list[str]) -> tuple[str, ...] | None:
    """Return the `_GATED_PREFIXES` entry the segment's leading words match.

    Behind a wrapper the name may stand after the wrapper's own options
    (`command -p gh pr list >out`), so every later word is tried as the
    start; without one, only the first word names the command.
    """

    words, wrapped, _assigned = _command_words(segment)
    starts = range(len(words)) if wrapped else range(min(len(words), 1))
    for start in starts:
        for prefix in _GATED_PREFIXES:
            if tuple(words[start : start + len(prefix)]) == prefix:
                return prefix
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

    try:
        command = _strip_comments(command)
        words = _lex(command)
        masked = _lex(_mask_quotes(command))
    except ValueError:
        # Unbalanced quoting. What bash would run is not what this saw, and the
        # command is malformed for bash too, so refusing costs nothing.
        print(
            "Blocked: the command could not be parsed as a shell command "
            "(unbalanced quote). Rewrite it and try again.",
            file=sys.stderr,
        )
        return 2

    if len(words) != len(masked):
        # The masked copy split differently, so which words are separators
        # cannot be told. Refused rather than guessed at.
        print(
            "Blocked: the command's quoting could not be matched to its words. "
            "Rewrite it and try again.",
            file=sys.stderr,
        )
        return 2

    # The redirection is the shell's write, not git's, so it is refused on
    # every git segment: `git status >.claude/settings.json` is allow-listed
    # and truncates the file as surely as `git diff` would.
    for segment, _twins in _segments(words, masked):
        if not _runs_git(segment):
            prefix = _gated_prefix(segment)
            if prefix and (
                any(word.startswith(_PROCESS_SUBSTITUTION) for word in segment)
                or any("$" in word or "`" in word for word in segment)
            ):
                print(
                    f"Blocked: a substitution - $(...), a backtick, <(...) or >(...) - "
                    f"in `{' '.join(prefix)}` runs the command inside it as part of a "
                    "string the allow rule approved on its prefix alone, and that "
                    "inner command is held to no rule: `gh pr list >(cat >out)` and "
                    "`python3 scripts/run_tests.py $(printf x >out)` truncate the "
                    "file while the command prints as usual. Write the inner command "
                    "as a command of its own, and the arguments of these commands "
                    "out literally, as for git diff.",
                    file=sys.stderr,
                )
                return 2
            redirection = _writing_redirection(segment) if prefix else None
            if redirection is not None:
                print(
                    f"Blocked: {redirection} makes the shell open a file for writing "
                    f"before `{' '.join(prefix or ())}` runs, which truncates it "
                    "whatever the command then prints. The allow rule for that "
                    "command matches its prefix and the redirection is the rest of "
                    "the string, so nothing else would prompt - it is the same write "
                    "this hook refuses for `git diff HEAD >out`. These commands print "
                    "to stdout; read that instead, or pipe it. 2>&1, >&2, an input "
                    "redirection, and a redirection on a command no allow rule "
                    "covers are not refused.",
                    file=sys.stderr,
                )
                return 2
            if prefix and _command_words(segment)[2]:
                print(
                    f"Blocked: an assignment before `{' '.join(prefix)}` is an "
                    "environment the command runs under, and for these commands "
                    "that changes what runs or where it goes - PYTEST_ADDOPTS= adds "
                    "options the wrapper never sees, PYTHONPATH= puts a module of "
                    "its own ahead of the wrapper's imports, GH_HOST= sends the "
                    "token elsewhere. Run the command without the assignment.",
                    file=sys.stderr,
                )
                return 2
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
        if _command_words(segment)[2]:
            print(
                "Blocked: an assignment before git is an environment git runs "
                "under, and git reads the names of programs to run out of it: "
                "GIT_EXTERNAL_DIFF=prog makes `git diff` run prog once per "
                "changed path, GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external "
                "sets the same program through the config, and LD_PRELOAD=lib.so "
                "loads a library into git itself. The allow rule matches the "
                "command on its prefix and the assignment is part of the string "
                "it matches, so nothing else would prompt. Run git without the "
                "assignment.",
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
