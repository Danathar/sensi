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
  library into git itself. An assignment before a git segment is refused the
  way one before the other allow-listed commands already was. Five spellings
  of the same assignment are refused with it: bash's `NAME+=value` append
  form, which creates the variable when it is unset; the export family
  (`export NAME=value`, `declare -x`, `typeset -x`, `readonly`), which bash
  applies to every command it runs later in the same string; the bare
  `export NAME` (or `declare -x NAME`) that arms a name a *later* command
  assigns to - but not `declare NAME`, `readonly NAME` or `export -n NAME`,
  which export nothing; `set -a`, which makes every later assignment an
  exported one without naming a builtin at all; and `env -S`, which hides
  the whole invocation inside one word. The rule is ordered, which was decided
  in #256: an export reaches only the commands after it in the string, so
  `git diff HEAD; export FOO=1` is not refused.

  What this refusal does *not* rest on: Claude Code's own permission matcher
  documents that "an allow rule won't match past an assignment of any other
  variable" than a fixed set of known-safe ones, and that set is not
  published. So most of these spellings prompt on their own account today,
  and this gate is the layer that does not have to change when the safe set
  does. It is a backstop by design rather than by accident - a PreToolUse
  hook runs *before* permission rules are evaluated, and an exit 2 blocks the
  call even where an allow rule would have approved it. Nothing here assumes
  a Bash tool call's shell outlives the call: it does not (verified on Claude
  Code 2.1.267 - `export X=1` in one call, `echo ${X:-UNSET}` in the next,
  prints UNSET), and every rule below reads one command string only.
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
  a git segment is. Descriptor forms, input redirections on these commands,
  pipes and a command no allow rule covers are left alone - that one prompts
  on its own. The three rows with no `:*` (`ruff check .`, `ruff format --check .`,
  `python3 scripts/check_requirements_sync.py`) are gated the same way; see
  `_EXACT_ROWS`.
* `xargs` hides the operands altogether. It appends the words it reads from
  standard input, or from the file `-a FILE` names, to the command it runs,
  so `xargs git diff <list.txt` is `git diff /dev/null ./secrets.yaml` when
  list.txt says so, and no scan of the string sees either path. The
  permission matcher's `:*` test accepts `xargs <prefix>` as readily as
  `<prefix>` (Claude Code 2.1.267), so nothing prompts either. `xargs` is
  refused in front of any command an allow row approves - git's rows, the
  `:*` prefixes and the exact rows - wherever it stands in the wrapper chain
  (`timeout 5 xargs git diff`, `nice xargs gh pr view`). `xargs -r git diff`
  does not begin `xargs git diff` and would prompt, and is refused with the
  rest, because the prompt would ask about operands it cannot show. In front
  of a command no rule covers (`git diff --name-only | xargs echo`) xargs
  prompts on its own and is left alone.
* A wrapper can be written as a path. The matcher cuts the first word at its
  last slash or backslash and steps over it when a wrapper's name is left, so
  `/usr/bin/timeout 5 ruff check . >out` is approved on `ruff check .` and is
  gated here like `timeout 5`. It steps over `./shim/nohup` just as readily,
  and bash then runs ./shim/nohup, so any spelling ending in a wrapper's name
  in front of an approved command is refused unless it is the bare name or
  the system's copy (`/usr/bin/<name>`, `/bin/<name>`); see `_spelled_wrapper`.
* `git checkout -b NAME START` is not only a new branch. Git writes START's
  tree over the working tree, and that tree carries its own
  `.claude/settings.json`, `.claude/hooks/` and `scripts/run_tests.py`: the
  `Edit` deny rows bind the Edit tool, not git. `git checkout -b x 00ff1e2`
  put back that commit's allow list (`ruff check` and `ruff format` with any
  arguments) and its test wrapper (which forwarded `--junitxml=PATH`), and
  deleted this file, which did not exist yet. The hook command is read from
  disk on every call, and one that fails to start is a non-blocking error,
  so every refusal here went with it. Any remote branch - an unreviewed PR head
  included - is a start point too. `git checkout -b NAME` with nothing after
  the name leaves the files alone and is still approved.
* `git add --pathspec-from-file=PATH` reads its pathspecs out of PATH, which
  can be any file on disk, and the first line that matches no file comes
  back in git's error (`fatal: pathspec 'SECRET=value' did not match any
  files`). Creating a file named after that line and running it again
  gives the next one, so the whole of `secrets.yaml` or `.env` is readable
  a line at a time past the `Read` deny rows.
* `git add -f` stages a file `.gitignore` leaves out, and once a file is in
  the index `git diff --cached` and `git show :PATH` print it whole, naming
  nothing this hook could check. `git add -f secrets.yaml .env config`
  followed by `git diff --cached` printed all three. So the file is stopped
  where it enters the index: `git add` is refused `--force` (and its
  abbreviations and any short cluster holding `f`) and any operand shaped
  like a file the `Read` deny rows withhold; the other withheld shapes are
  in `.gitignore`, so a plain `git add .` leaves them out; see
  `_stages_a_withheld_file`.
* git reads standard input too. Under `--stdin`, `git log`, `git show` and
  `git diff` take revisions from it, one per line, and the first line that
  is not a revision ends the run with `fatal: bad revision '<that line>'`,
  so `git log --stdin <.env` printed the first line of `.env` (issue #273).
  The target of a bare `<` on an allow-listed git command must be inside the
  repository, carry none of the names the `Read` deny rows withhold (plus
  the secret shapes the sibling gates refuse on sight), and be spelled out.
  `</dev/null`, here-strings, `<&N` and a revision list inside the
  repository (`git log --stdin <revs.txt`) are left alone; see
  `_reading_redirection`.

Two shapes of the corpus in issue #250 are decided here as *not reachable*
rather than refused, so that a later pass does not have to work them out again.
Each rests on a documented property of the permission matcher, not on a guess:

* Anything behind `env`. `env -i`, `env -C DIR` / `env --chdir=DIR`,
  `env --split-string=`, and `env -u NAME` all reach the tool, but `env` is
  not one of the wrappers the matcher steps over (`timeout`, `time`, `nice`,
  `nohup`, `stdbuf`, `command`, `builtin` and `noglob` are, and a `:*` row
  also matches `xargs <prefix>`), so a string beginning `env ` matches no
  allow row and prompts. `_splits_a_string` still refuses the `env -S`
  spelling, because that one hides a whole invocation inside a single word
  and a gate that cannot see the words cannot be said to have decided
  anything about them.
* git's own global options - `git -c diff.external=prog diff`,
  `--config-env`, `--exec-path` - which do reach git and do load a program.
  `Bash(git diff:*)` is `Bash(git diff *)`: the space is part of the rule, so
  the rule matches only a string that *begins* `git diff `. A global option
  stands before the subcommand, so `git -c ... diff` begins `git -c` and
  matches nothing. The same answer covers a brace-built command name
  (`{,git} diff`), which begins `{,git}`.

The three rows with no `:*` were an open question in #250 and were decided in
#256: they are gated like the `:*` rows. A rule with no wildcard matches one
exact string. If the matcher compares the string with the redirection in it,
`ruff check . >path` matches nothing and prompts. But the matcher is also
documented to check a redirect target against the `Read`/`Edit` rules on its
own, which reads as the command half being matched *without* the redirection.
On that reading `ruff check . >custom_components/sensi/client.py` matches the
exact row, and the target is governed only by the `Edit` deny rows, which do
not name it. Refusing the redirection costs almost nothing, because nobody
needs lint output in a file, while the other reading leaves a live write. So
an output redirection on these commands is refused, and so are
`RUFF_OUTPUT_FILE=` in front of ruff and ruff's `--output-file`/`-o`.

No permission rule can close the rest. `deny` matching is by command prefix,
and every one of these is an option that can be written anywhere in the
argument list, so a rule can only ever name one spelling of one position.

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

# What a `<` on git must not feed it. The names are the `Read` deny rows of
# .claude/settings.json (`secrets.yaml`, `.env`, `config/**`) plus the secret
# shapes the sibling repositories' gates refuse on sight, so a key dropped
# beside the checkout is not readable the day before a deny row names it.
_DENIED_READ_NAMES = frozenset(
    {"secrets.yaml", ".env", "cosign.key", "id_rsa", "id_ed25519"}
)
_DENIED_READ_SUFFIXES = (".pem", ".p12")
_DENIED_READ_DIRECTORY = "config"

# The tokens that end one simple command and begin the next, so that a
# redirection is charged to the command it is written in: `echo x >out; git
# diff HEAD` is echo's redirection, `git diff HEAD | jq . >out` is jq's. A
# newline is whitespace to shlex and never a token of its own, so
# `_newlines_to_separators` rewrites an unquoted one to `;` before the lexer
# runs. It used to be left alone, on the reading that merging two lines into
# one segment could only over-refuse; for a redirection that is true, and for
# an assignment it is the other way round -- `echo hi\nGIT_EXTERNAL_DIFF=prog
# git diff HEAD` put the assignment in the middle of a segment, where it is
# not a *leading* one and no rule below reads it.
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

# The allow rows with no `:*`, each of which matches one exact string. They are
# gated the same way (decided in #256). If the permission matcher compares the
# string with a redirection in it, `ruff check . >path` matches no row and
# prompts, and refusing it costs nothing, because nobody needs lint output in
# a file. If the matcher checks the redirect target against the file rules on
# its own and compares only the command half, the same string matches the
# exact row and writes any path the `Edit` deny rows do not name. The
# documentation reads both ways, and refusing costs almost nothing, so the
# hook refuses. Ruff's own ways to write a file are refused with it: its
# `--output-file`/`-o` flag, and `RUFF_OUTPUT_FILE=` as a leading assignment,
# which the assignment rule for these prefixes already refuses.
# `test_git_file_argument_gate.py` derives this list from the settings file
# too.
_EXACT_ROWS = (
    ("ruff", "check", "."),
    ("ruff", "format", "--check", "."),
    ("python3", "scripts/check_requirements_sync.py"),
)

# Words that stand before the name of the command they run and are not part
# of the prefix an allow rule matches, the way a leading `NAME=value` is not:
# `time python3 scripts/run_tests.py >out` is the wrapper's redirection.
#
# The list is Claude Code's own - the wrappers its permission matcher steps
# over before matching a rule (`timeout`, `time`, `nice`, `stdbuf`, `nohup`,
# `command`, `builtin`, `noglob`, in 2.1.267) - plus `xargs`, which its `:*`
# test accepts in front of a prefix (`xargs git diff` matches
# `Bash(git diff:*)`), and `env` and `exec`, which it does not step over.
# Keeping it narrower than the matcher's was the bug: `time` was here and
# `timeout` was not, so `time python3 scripts/run_tests.py >out` was refused
# while `timeout 5 python3 scripts/run_tests.py >out` was not, and the
# matcher steps over both. A wrapper this hook lists and the matcher does
# not costs nothing, because the string prompts anyway; one the matcher lists
# and this hook does not is a hole. `xargs` in front of an allow-listed
# command is refused outright, by `_xargs_runs`, since stepping over it is
# not enough: the operands it adds are not in the string.
#
# A wrapper is recognised by its last path component, as the matcher
# recognises it: 2.1.267 compares `n[0]?.replace(/^.*[\\/]/, "")`, cutting
# at a `\` as well as a `/`, so it steps over `/usr/bin/timeout 5` as it
# does `timeout 5`. Compared whole here, `/usr/bin/timeout 5 ruff check .
# >out` named no wrapper, the gated prefix behind it was never looked for,
# and the redirection passed. The matcher steps over *any* path that ends in
# a wrapper's name, though, and the file at that path is what bash runs:
# `./shim/nohup git diff HEAD` is approved as `git diff HEAD` and runs
# ./shim/nohup, which anything in the tree could have written. So only the
# system's own copies, `/usr/bin/<name>` and `/bin/<name>`, are stepped
# over; every other spelling that ends in a wrapper's name in front of an
# approved command is refused by `_spelled_wrapper`, whether a literal path,
# one the shell builds (`$D/timeout`), or one whose `\` bash removes before
# the matcher's cut would (`/usr/bin\timeout` runs `/usr/bintimeout`).
#
# A wrapper's own options are still not modelled. `env -i python3 ...` matches
# no allow rule (see the header: `env` is not a wrapper the matcher steps
# over), and a leading assignment written after a wrapper's operand -
# `timeout 5 PYTHONPATH=/evil python3 ...` - is itself what stops the rule
# matching, so neither needs a rule of its own here.
_COMMAND_WRAPPERS = frozenset(
    {
        "time",
        "timeout",
        "nice",
        "nohup",
        "stdbuf",
        "command",
        "builtin",
        "noglob",
        "xargs",
        "env",
        "exec",
    }
)

# Everything up to the last `/` or `\` of a word: the matcher's own pattern.
_PATH_HEAD = re.compile(r"^.*[\\/]")


def _wrapper_name(word: str) -> str:
    """Return `word` as the matcher reads a wrapper: its last path component."""

    return _PATH_HEAD.sub("", word)


def _is_system_wrapper(word: str) -> bool:
    """Whether `word` is a wrapper's bare name or the system's own copy of it."""

    return any(
        word.startswith(directory) and word[len(directory) :] in _COMMAND_WRAPPERS
        for directory in ("", "/usr/bin/", "/bin/")
    )


# An assignment as bash's grammar spells it, which is wider than `NAME=value`:
# `NAME+=value` appends, and *creates* the variable when it is unset, so
# `GIT_EXTERNAL_DIFF+=prog git diff HEAD~1 HEAD` puts exactly the same program
# in git's environment. Splitting on the first `=` and asking whether the left
# half is an identifier answered no for that one, because the left half was
# `GIT_EXTERNAL_DIFF+`, and the word went on to be read as the command's name.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")

# `export NAME=value`, and the three builtins that spell the same thing with a
# flag. Bash applies what they set to every command it runs later in the same
# string, so the assignment reaches a git invocation that carries none of its
# own: `export GIT_EXTERNAL_DIFF=prog; git diff HEAD~1 HEAD` runs prog once per
# changed path. A builtin that assigns a value is refused whatever its options
# say, because a value given to a name that is already exported reaches every
# later command however the builtin was spelled (`readonly PATH=/x` included).
# Only a bare name - no value - has its options read, since what a bare name
# does depends on them: `export NAME`, `declare -x NAME` and `typeset -x NAME`
# export it for a later assignment, while `declare NAME`, `readonly NAME`,
# `export -n NAME` and `declare +x NAME` do not (bash 5.3).
_EXPORT_BUILTINS = frozenset({"export", "declare", "typeset", "readonly"})

# The git allow rows of `.claude/settings.json`, as the words after `git` that
# each rule approves with any arguments after them. `_GATED_SUBCOMMANDS` above
# is the narrower set whose *options* do file I/O; this is every prefix a rule
# approves, which is the set a substitution can ride in on. The whole prefix
# counts, not just the subcommand: `Bash(git checkout -b:*)` approves
# `git checkout -b topic` and not `git checkout $(...)`, which prompts, and a
# refusal there would take away the prompt the user could have answered.
# `test_git_file_argument_gate.py` derives this from the settings file, so a
# git row added there fails until it is here.
_ALLOWED_GIT_PREFIXES = (
    ("status",),
    ("diff",),
    ("log",),
    ("show",),
    ("branch",),
    ("add",),
    ("checkout", "-b"),
)

# `git add --pathspec-from-file=PATH` reads its pathspecs out of PATH, which
# can be any file on disk, and the first line that matches nothing comes back
# in git's own error: `fatal: pathspec 'SECRET=value' did not match any files`.
# Compared the way `_REFUSED_LONG` is, so `--pathspec-fr` is refused too. Of the
# allow-listed subcommands only `git add` takes the option (git 2.47.3).
_PATHSPEC_FILE = "pathspec-from-file"

# `git add --force` stages a gitignored file, and the index is readable by
# `git diff --cached` and `git show :PATH`, which name no path at all. Compared
# the way `_PATHSPEC_FILE` is, so `--fo` is refused too; git add has no short
# option that takes an argument, so an `f` anywhere in a short cluster is -f.
_FORCE = "force"

# `env -S "..."` (--split-string) splits a quoted string into a command of its
# own. The words inside it are one word to any scan of the string, so a git
# invocation written there is invisible to everything below.
_ENV_SPLIT_STRING = re.compile(r"^-S|^--split-string")

# The command names this hook gates, for the two tests that have to ask "would
# this string reach one of them" without being able to see the words.
_GUARDED_NAME = re.compile(r"\b(git|gh|python3|ruff)\b")

# Ruff's own file-writing flag, `--output-file PATH` / `--output-file=PATH`,
# and its short `-o`, which clap also takes attached (`-oPATH`) or at the end
# of a cluster of short flags (`-qo PATH`). The letter is looked for anywhere
# in a short cluster, as `-O` is for git.
_RUFF_OUTPUT_FLAG = re.compile(r"^--output-file(=|$)|^-[A-Za-z]*o")


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


def _newlines_to_separators(command: str) -> str:
    r"""Return the command with every unquoted newline rewritten to `;`.

    bash ends a simple command at a newline exactly as it does at a `;`, and
    the Bash tool is handed multi-line strings routinely. shlex treats a
    newline as plain whitespace, so the words of both lines landed in one
    segment: an assignment on the second line stopped being a *leading*
    assignment and the rule that refuses one never saw it.

    A newline inside quotes, and the one of a `\\`-continuation, are `Q` in
    the masked copy and are left as they are -- the first is a character of
    its word, the second is not a command boundary at all.
    """

    masked = _mask_quotes(command)
    return "".join(
        ";" if masked[index] == "\n" else char for index, char in enumerate(command)
    )


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


def _denied_read_shape(word: str) -> bool:
    """Whether `word` names a file the `Read` deny rows withhold, or one shaped like it.

    Judged on the name as typed and on the path it resolves to, so a link
    inside the repository (`revs.txt -> .env`) is judged by what it opens.
    """

    base = Path.cwd()
    if not base.is_relative_to(_REPO):
        base = _REPO
    try:
        resolved = (base / word).resolve()
    except ValueError, RuntimeError:
        return True
    for path in (Path(word), resolved):
        name = path.name
        if (
            name in _DENIED_READ_NAMES
            or name.startswith(".env.")
            or name.endswith(_DENIED_READ_SUFFIXES)
        ):
            return True
    return resolved.is_relative_to(_REPO / _DENIED_READ_DIRECTORY)


def _reading_redirection(
    segment: list[str], twins: list[str], moved: bool
) -> str | None:
    """Return the target of a bare `<` in `segment` that git could print back.

    Only `<` opens a path for reading - with a descriptor before it (`0<`,
    `3<`) it still arrives as `<` - and its twin must be the operator too, so
    a quoted `'<'` is an argument, not a redirection. `<<` and `<<<` carry
    content, `<&` duplicates a descriptor and `<>` is a write
    (`_writing_redirection`). `/dev/null` has nothing to print back. Any other
    target must be inside the repository, carry none of the denied shapes,
    and be spelled out: a glob, a brace, a `$` or a backtick is a path bash
    builds after this has read it. After a `cd` earlier in the string
    (`moved`), the directory bash opens the target from is not the one this
    resolves against, so every target but `/dev/null` is refused
    (`cd config && git log --stdin <configuration.yaml`).
    """

    for index, twin in enumerate(twins):
        if twin != "<":
            continue
        target = segment[index + 1] if index + 1 < len(segment) else ""
        if target == "/dev/null":
            continue
        if (
            moved
            or not target
            or any(char in target for char in _UNEXPANDED)
            or _brace_would_expand(target)
            or target.startswith(_PROCESS_SUBSTITUTION)
            or _is_outside_repo(target)
            or _denied_read_shape(target)
        ):
            return f"<{target}"
    return None


def _changes_directory(segment: list[str]) -> bool:
    """Whether this segment is a `cd`, `pushd` or `popd`."""

    words = _command_words(segment)[0]
    return bool(words) and words[0] in {"cd", "pushd", "popd"}


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


def _command_words(segment: list[str]) -> tuple[list[str], list[str], bool]:
    """Return the words the command in `segment` receives, and what led it.

    A redirection - its operator, its target and a descriptor written before
    it - is the shell's, and bash lets it stand anywhere in the simple
    command, so `>out python3 scripts/run_tests.py` and `python3 >out
    scripts/run_tests.py` both come back as `['python3',
    'scripts/run_tests.py']`. A leading `NAME=value` and a leading wrapper
    word are stepped over, since neither is part of the prefix an allow rule
    matches: the wrapper words come back second, in the order written, and
    whether an assignment led comes back third. A word is a wrapper when its
    last path component is one (`/usr/bin/timeout`, `./shim/nohup`), as the
    matcher reads it; `_spelled_wrapper` refuses the paths that are not the
    system's own copy. A `$` left behind by a nested
    `$(...)` is dropped from the end of a word, so the words are the ones
    typed around the substitution.
    """

    words: list[str] = []
    wrappers: list[str] = []
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
        if not words and _wrapper_name(word) in _COMMAND_WRAPPERS:
            wrappers.append(word)
            continue
        if not words and _ASSIGNMENT.match(word):
            assigned = True
            continue
        words.append(word)
    return words, wrappers, assigned


def _exports_a_name(segment: list[str]) -> bool:
    """Whether this segment is an export-family builtin that names a variable.

    What it sets is in the environment of every command bash runs after it in
    the same string, which is the reach a leading `NAME=value` has, written
    after the command name instead of before it. `export` on its own, and
    `declare -p`, name nothing and are not this.

    A name with no `=` counts when the builtin exports it. `export
    GIT_EXTERNAL_DIFF` exports the name first and a later command assigns to
    it - `export GIT_EXTERNAL_DIFF; GIT_EXTERNAL_DIFF=prog; git diff HEAD`
    puts the program in git's environment exactly as the one-word spelling
    does, and asking only whether the builtin's own word carried an `=`
    answered no for it. A bare name counts only for the spellings that
    export: `export` without `-n`, and `declare`/`typeset` with `-x`.
    `declare FOO`, `readonly FOO`, `export -n FOO` and `declare +x FOO` put
    nothing in a later command's environment (bash 5.3), and refusing the
    git command after them would block a call the user could have approved.

    A name *with* a value counts for every builtin in `_EXPORT_BUILTINS`,
    whatever its options: a value given to a name that is already exported
    reaches later commands however it was assigned. Which name is dangerous
    is deliberately not modelled: a table of variable names is a list to
    keep up to date, and over-refusing `export PATH=...; git diff` is the
    cheap direction to be wrong in when the refusal says to run the command
    without the export.
    """

    words, _wrappers, _assigned = _command_words(segment)
    if not words or words[0] not in _EXPORT_BUILTINS:
        return False
    names = [word for word in words[1:] if not word.startswith(("-", "+"))]
    if any(_ASSIGNMENT.match(word) for word in names):
        return True
    flags = "".join(word[1:] for word in words[1:] if word[:1] == "-" and word != "--")
    exports = "n" not in flags if words[0] == "export" else "x" in flags
    return exports and bool(names)


def _turns_on_allexport(segment: list[str]) -> bool:
    """Whether this segment is the `set` that makes later assignments exported.

    `set -a` and `set -o allexport` export every variable assigned after
    them, so a plain `GIT_EXTERNAL_DIFF=prog` standing as its own command
    reaches the next git invocation without naming a builtin from
    `_EXPORT_BUILTINS` at all. Only the arming spellings are read: `set +a`
    turns it back off and is not matched, which can only leave the flag set
    for longer than bash would - the over-refusing direction.
    """

    words, _wrappers, _assigned = _command_words(segment)
    if not words or words[0] != "set":
        return False
    return any(
        word == "allexport"
        or (word.startswith("-") and not word.startswith("--") and "a" in word[1:])
        for word in words[1:]
    )


def _is_bare_assignment(segment: list[str]) -> bool:
    """Whether this segment is an assignment standing as a command of its own.

    `GIT_EXTERNAL_DIFF=prog` with no command after it sets a shell variable,
    which no child process sees - so it is not refused on its own. Under
    `set -a` it sets an environment one, and `_turns_on_allexport` is what
    tells the two apart.
    """

    words, wrappers, assigned = _command_words(segment)
    return assigned and not words and not wrappers


def _allowed_git_prefix(words: list[str]) -> tuple[str, ...] | None:
    """Return the git allow row `words` runs under, if any.

    Wider than `_runs_gated_git`, which asks only about the three whose
    options do file I/O. A substitution rides in on whichever rule approved
    the string, and seven git rows carry a `:*`. The row's whole prefix has
    to be there, word for word: `git checkout $(...)` does not begin
    `git checkout -b `, so no rule approved it and it prompts on its own.
    """

    for index, word in enumerate(words):
        if word != "git" and not word.endswith("/git"):
            continue
        for start in range(index + 1, len(words)):
            for prefix in _ALLOWED_GIT_PREFIXES:
                if tuple(words[start : start + len(prefix)]) == prefix:
                    return prefix
    return None


def _after_git(words: list[str], subcommand: tuple[str, ...]) -> list[str] | None:
    """Return the words after `git <subcommand>`, when git runs that directly.

    The subcommand has to follow `git` at once, as the allow row that
    approves it is written: `git -C dir checkout -b topic HEAD~5` begins
    `git -C` and prompts on its own.
    """

    for index, word in enumerate(words):
        if word != "git" and not word.endswith("/git"):
            continue
        after = index + 1 + len(subcommand)
        if tuple(words[index + 1 : after]) == subcommand:
            return words[after:]
    return None


def _checkout_moves_the_tree(words: list[str]) -> str | None:
    """Return what `git checkout -b` was given beyond a branch name, if any.

    `git checkout -b topic` makes a branch at HEAD and leaves every file as it
    is. Anything more is a start point or an option, and a start point makes
    git write that commit's tree over the working tree:
    `git checkout -b x 00ff1e2` puts back the settings file and the test
    wrapper of that commit and deletes this hook, which did not exist yet.
    """

    operands = _after_git(words, ("checkout", "-b"))
    if operands is None:
        return None
    if len(operands) > 1 or any(word.startswith("-") for word in operands):
        return " ".join(operands)
    return None


def _reads_a_pathspec_file(words: list[str]) -> str | None:
    """Return the `--pathspec-from-file` option given to `git add`, if any."""

    operands = _after_git(words, ("add",))
    for word in operands or ():
        if word == "--":
            break
        name = word[2:].split("=", 1)[0] if word.startswith("--") else ""
        if name and _PATHSPEC_FILE.startswith(name):
            return word
    return None


def _stages_a_withheld_file(words: list[str]) -> str | None:
    """Return the `git add` word that would put a withheld file in the index.

    That is `--force` or `-f`, which stage what `.gitignore` leaves out, or
    an operand shaped like a file the `Read` deny rows withhold. Once staged,
    `git diff --cached` prints it whole and names nothing this hook can see.
    """

    options = True
    for word in _after_git(words, ("add",)) or ():
        if options and word == "--":
            options = False
            continue
        if options and word.startswith("--"):
            name = word[2:].split("=", 1)[0]
            if name and _FORCE.startswith(name):
                return word
            continue
        if options and word.startswith("-") and len(word) > 1:
            if "f" in word[1:]:
                return word
            continue
        if _denied_read_shape(word):
            return word
    return None


def _has_substitution(segment: list[str]) -> bool:
    """Whether any word of `segment` is built by running another command.

    Read off the words as typed rather than off their quote-masked twins, so
    a single-quoted `$` is refused too. That is the same answer `_UNEXPANDED`
    already gives for `git diff`, and the same one the `_GATED_PREFIXES`
    branch gives: bash expands `$` inside double quotes, the twins mask it
    either way, and a gate that told the two apart by reading the twin would
    miss `"$(...)"` - the spelling most likely to be written.
    """

    return any(
        word.startswith(_PROCESS_SUBSTITUTION) or "$" in word or "`" in word
        for word in segment
    )


def _env_arguments(segment: list[str]) -> list[str] | None:
    """Return the words `env` receives, when `env` is what this segment runs.

    `-S` is env's `--split-string` only when env is the command reading it.
    It is also git's pickaxe, and the two are the same two characters:
    `git log -S env` carries the letter *and* the word `env`, and a scan that
    asked whether the segment contained both refused a command that splits
    nothing - a false refusal of an allow-listed command, which is the
    expensive direction for a gate to be wrong in. Content cannot tell the
    two apart; only position can, so the search starts at the command name
    and reads nothing to the left of it.

    Redirections, their targets and leading assignments are stepped over the
    way `_command_words` steps over them, because bash lets all three stand
    before the command name. Unlike `_command_words` this stops *at* the
    wrapper rather than stepping over it, since the wrapper is the command
    whose options are being read.
    """

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
        if _ASSIGNMENT.match(word):
            index += 1
            continue
        if word == "env" or word.endswith("/env"):
            return segment[index + 1 :]
        return None
    return None


def _splits_a_string(segment: list[str]) -> bool:
    """Whether this segment is an `env -S`, which hides a command in a word."""

    arguments = _env_arguments(segment)
    if arguments is None:
        return False
    return any(_ENV_SPLIT_STRING.match(word) for word in arguments)


def _gated_prefix(segment: list[str]) -> tuple[str, ...] | None:
    """Return the gated allow row the segment's leading words match.

    `_GATED_PREFIXES` and `_EXACT_ROWS` alike. An exact row is matched as a
    prefix too, so `ruff check . --output-file=x` is read as `ruff check .`
    with more after it - a string that prompts today, and refusing it costs
    nothing.

    Behind a wrapper the name may stand after the wrapper's own options
    (`command -p gh pr list >out`), so every later word is tried as the
    start; without one, only the first word names the command.
    """

    words, wrappers, _assigned = _command_words(segment)
    starts = range(len(words)) if wrappers else range(min(len(words), 1))
    return _gated_row(words, starts)


def _gated_row(words: list[str], starts: range) -> tuple[str, ...] | None:
    """Return the first gated allow row that begins at one of `starts`."""

    for start in starts:
        for prefix in _GATED_PREFIXES + _EXACT_ROWS:
            if tuple(words[start : start + len(prefix)]) == prefix:
                return prefix
    return None


def _xargs_runs(segment: list[str]) -> str | None:
    """Return the allow-listed command `xargs` runs in `segment`, if it runs one.

    xargs appends the words it reads from standard input, or from the file
    `-a FILE` names, to the command it runs, so the operands that command
    receives are not in the string: `xargs git diff <list.txt` is `git diff
    /dev/null ./secrets.yaml` when list.txt says so, and every rule here
    reads words this string never carries. The matcher's `:*` test accepts
    `xargs <prefix>` as readily as `<prefix>`, so nothing prompts either.

    xargs counts wherever it stands in the wrapper chain: first, behind
    another wrapper (`timeout 5 xargs git diff`), or ahead of options of its
    own (`xargs -r -0 git diff`). As in `_gated_prefix`, every word after it
    is tried as the start of the command it runs, since its options are not
    modelled. The command counts when an allow row approves it - a git row,
    a `:*` prefix or an exact row. `xargs echo` and `xargs wc -l` match no
    row and prompt on their own, and so does `xargs git commit`, which is an
    ask row; refusing those would take away a prompt the user could answer.
    A path to xargs is xargs, in the chain and among the words after a
    wrapper's options (`timeout 5 /usr/bin/xargs git diff`).
    """

    words, wrappers, _assigned = _command_words(segment)
    chain = wrappers + words
    names = [_wrapper_name(word) for word in chain]
    if not wrappers or "xargs" not in names:
        return None
    after = chain[names.index("xargs") + 1 :]
    git_prefix = _allowed_git_prefix(after)
    if git_prefix is not None:
        return " ".join(("git", *git_prefix))
    row = _gated_row(after, range(len(after)))
    return " ".join(row) if row else None


def _spelled_wrapper(segment: list[str]) -> tuple[str, str] | None:
    r"""Return a word ending in a wrapper's name before an approved command.

    The matcher cuts a word at its last `/` or `\` and steps over it when
    what is left is a wrapper's name, so the rule that approves the string
    never sees that word, while bash runs the file it names: `./shim/nohup
    git diff HEAD` is approved as `git diff HEAD`. Only the bare name and the
    system's copies (`/usr/bin/<name>`, `/bin/<name>`) are left alone. The
    test is on the word's ending rather than its last component, because
    bash removes an unquoted `\` before this hook sees the word: the matcher
    reads `/usr/bin\timeout` as `timeout`, and it reaches here as
    `/usr/bintimeout`.

    The words read run from the start of the chain to the first `git` or the
    first gated row, which is where the matcher starts comparing. A command
    no allow row covers prompts on its own and is not read. Returns the word
    and the wrapper name it ends in.
    """

    words, wrappers, _assigned = _command_words(segment)
    chain = wrappers + words
    found = None
    for index, word in enumerate(chain):
        if word == "git" or word.endswith("/git"):
            return found
        if _gated_row(chain, range(index, index + 1)):
            return found
        if found is None and not _is_system_wrapper(word):
            name = next(
                (name for name in _COMMAND_WRAPPERS if word.endswith(name)), None
            )
            if name is not None:
                found = (word, name)
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
        command = _newlines_to_separators(_strip_comments(command))
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
    exported = False
    allexport = False
    # A `cd` earlier in the string moves where bash opens a relative `<`
    # target, so a later git's redirection cannot be resolved here. Only a
    # cd, pushd or popd sets it, and nothing clears it: a git segment between
    # the cd and the redirection does not move bash back.
    moved = False
    # The system's copies of the wrappers, where they lead a command. bash
    # runs them and git never receives them, so the operand scan below does
    # not read them as paths outside the repository.
    stepped_over: list[str] = []
    for segment, twins in _segments(words, masked):
        if _splits_a_string(segment) and _GUARDED_NAME.search(" ".join(segment)):
            print(
                "Blocked: `env -S` (--split-string) splits a quoted string into a "
                "command this hook never sees as words - the whole invocation is "
                "one word to any scan of the string - and the string here names "
                "git, gh, python3 or ruff. `env -S 'GIT_EXTERNAL_DIFF=prog git diff "
                "HEAD~1 HEAD'` is the assignment refused below, written where "
                "nothing can read it. Write the command out as a command.",
                file=sys.stderr,
            )
            return 2
        xargs_runs = _xargs_runs(segment)
        if xargs_runs is not None:
            print(
                f"Blocked: xargs adds the words it reads from standard input (or "
                f"from `-a FILE`) to `{xargs_runs}`, so the operands that command "
                "receives are not in this string and cannot be checked - `xargs "
                "git diff <list.txt` is `git diff /dev/null ./secrets.yaml` when "
                "list.txt says so. The allow rule matches `xargs <prefix>` as "
                "readily as `<prefix>`, so nothing else would prompt. Name the "
                "operands in the command itself instead. xargs in front of a "
                "command no allow rule covers (`xargs echo`) is not refused.",
                file=sys.stderr,
            )
            return 2
        spelled = _spelled_wrapper(segment)
        if spelled is not None:
            word, name = spelled
            print(
                f"Blocked: `{word}` ends in the name of the wrapper `{name}`. The "
                "permission matcher cuts a word at its last / or \\ and steps "
                "over it when what is left is a wrapper, so the allow rule matched "
                "only the words after it - while bash runs the file this word "
                "names, which is not the system's copy: `./shim/nohup git diff "
                "HEAD` is approved as `git diff HEAD` and runs ./shim/nohup. Use "
                f"the bare name `{name}` (or /usr/bin/{name}).",
                file=sys.stderr,
            )
            return 2
        stepped_over.extend(
            word for word in _command_words(segment)[1] if _is_system_wrapper(word)
        )
        if exported and (_runs_git(segment) or _gated_prefix(segment)):
            print(
                "Blocked: an exported name earlier in this string (`export "
                "NAME=value`, `declare -x`, `typeset -x`, `readonly`, a bare "
                "`export NAME` a later command assigns to, or any assignment "
                "after `set -a`) is "
                "in the environment of this command, which is the reach of a "
                "leading `NAME=value` written after the command name instead of "
                "before it: `export GIT_EXTERNAL_DIFF=prog; git diff HEAD~1 HEAD` "
                "runs prog once per changed path while the git invocation carries "
                "no assignment at all. The allow rule matches this command on its "
                "prefix, so nothing else would prompt. Run it without the export. "
                "An export written after the command it cannot reach, and one in "
                "a string that runs none of these commands, are not refused.",
                file=sys.stderr,
            )
            return 2
        # Armed *after* this segment has been judged, so the rule stays
        # ordered: an export reaches what bash runs after it and nothing
        # earlier, and `git diff HEAD; export FOO=1` is left alone. Decided in
        # #256, matching zfs-kinoite-complex#230 and aurora-zfs-simple#223:
        # the shell does not outlive a Bash call, so an export can only reach
        # a later command of the same string, and refusing the earlier one
        # would refuse a command the export cannot touch.
        if _exports_a_name(segment) or (allexport and _is_bare_assignment(segment)):
            exported = True
        if _turns_on_allexport(segment):
            allexport = True
        if _changes_directory(segment):
            moved = True
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
                    "token elsewhere, RUFF_OUTPUT_FILE= makes ruff write its "
                    "report to a path. Run the command without the assignment.",
                    file=sys.stderr,
                )
                return 2
            if prefix and prefix[0] == "ruff":
                flag = next(
                    (
                        word
                        for word in _command_words(segment)[0]
                        if _RUFF_OUTPUT_FLAG.match(word)
                    ),
                    None,
                )
                if flag is not None:
                    print(
                        f"Blocked: `{flag}` is ruff's --output-file, which writes "
                        "the report to a path of the caller's choosing - the same "
                        "write a redirection makes. Read ruff's output on stdout.",
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
                "instead. 2>&1, >&2, an input redirection from a file inside "
                "the repository, and a redirection on another command of the "
                "same string are not refused.",
                file=sys.stderr,
            )
            return 2
        fed = _reading_redirection(segment, twins, moved)
        if fed is not None and _allowed_git_prefix(_command_words(segment)[0]):
            print(
                f"Blocked: {fed} hands git a file on standard input. Under "
                "--stdin, git log, git show and git diff read revisions from it "
                "and quote the first line that is not one in their error "
                "(`fatal: bad revision 'TOKEN=abc'`), so `git log --stdin <.env` "
                "prints a line of a file the Read deny rows withhold. The target "
                "of a < on git must be inside the repository, must not be "
                "secrets.yaml, .env, .env.*, config/**, cosign.key, *.pem, *.p12, "
                "id_rsa or id_ed25519, and must be spelled out. Put the revisions "
                "in a file inside the repository or name them on the command "
                "line. </dev/null, here-strings and <&N are not refused; after a "
                "cd earlier in the command only </dev/null is.",
                file=sys.stderr,
            )
            return 2
        git_prefix = _allowed_git_prefix(_command_words(segment)[0])
        if (
            git_prefix is not None
            and git_prefix[0] not in _GATED_SUBCOMMANDS
            and _has_substitution(segment)
        ):
            print(
                f"Blocked: a substitution - $(...), a backtick, <(...) or >(...) - "
                f"in `git {' '.join(git_prefix)}` runs the command inside it as part of a "
                "string the allow rule approved on its prefix alone, and that "
                "inner command is held to no rule: `git status $(printf x >out)` "
                "truncates the file while git prints as usual, and a backtick "
                "spells the same thing. A `$(...)` is not one of the separators "
                "the permission "
                "matcher splits a string at, so the inner command is never matched "
                "against a rule of its own. This is the refusal `git diff`, "
                "`git log` and `git show` already make for every expansion; it "
                "reaches the other allow-listed subcommands because the rule that "
                "approved them is the same shape. Run the inner command on its own "
                "and write the result out, as for git diff.",
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
        start_point = _checkout_moves_the_tree(_command_words(segment)[0])
        if start_point is not None:
            print(
                f"Blocked: `{start_point}` after `git checkout -b NAME` is a "
                "start point or an option, and a start point makes git write "
                "that commit's files over the working tree - .claude/settings.json, "
                ".claude/hooks/ and scripts/run_tests.py included, which the Edit "
                "deny rules protect only from the Edit tool. `git checkout -b x "
                "00ff1e2` restores the allow list of that commit and deletes this "
                "hook. The allow rule matches the command on its prefix, so "
                "nothing else would prompt. Create the branch at HEAD with "
                "`git checkout -b NAME` alone; switching to another commit needs "
                "a command the user approves.",
                file=sys.stderr,
            )
            return 2
        pathspec_file = _reads_a_pathspec_file(_command_words(segment)[0])
        if pathspec_file is not None:
            print(
                f"Blocked: `{pathspec_file}` makes `git add` read its pathspecs "
                "out of a file, which can be any file on disk, and git prints the "
                "first line that matches nothing in its error - `git add "
                "--pathspec-from-file=secrets.yaml` shows a line of a file the "
                "Read deny rules withhold. Name the paths in the command instead.",
                file=sys.stderr,
            )
            return 2
        withheld = _stages_a_withheld_file(_command_words(segment)[0])
        if withheld is not None:
            print(
                f"Blocked: `{withheld}` would make `git add` put a file the Read "
                "deny rules withhold into the index - --force stages what "
                ".gitignore leaves out - and from there `git diff --cached` and "
                "`git show :PATH` print it whole: `git add -f secrets.yaml` then "
                "`git diff --cached` shows every line of secrets.yaml. The allow "
                "rule matches the command on its prefix, so nothing else would "
                "prompt. Stage the files you changed by name, without --force.",
                file=sys.stderr,
            )
            return 2

    if not _runs_gated_git(words):
        return 0

    for index, word in enumerate(words):
        if word in stepped_over:
            stepped_over.remove(word)
            continue
        # `</dev/null` is the shell's, not an operand: git never sees the word,
        # and the file has nothing to print back. Every other `<` target is
        # judged by `_reading_redirection` above and still passes through here.
        if word == "/dev/null" and index and masked[index - 1] == "<":
            continue
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
