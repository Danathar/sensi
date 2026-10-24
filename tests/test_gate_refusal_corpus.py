"""Run the fleet's shared refusal corpus against this repository's Bash gate.

Six repositories each carry their own copy of the PreToolUse hook that keeps
allow-listed commands such as `git diff` and `gh pr view` from reading `.env`
or writing a file. The copies are separate code, so a bypass fixed in one
repository says nothing about the other five
(Danathar/atomic-image-builder#609).
`tests/fixtures/gate-refusal-corpus.json` is the one table they share: each
row is a command, the verdict every gate has to reach, and the command
prefixes the row depends on. This repository runs the rows its own allow list
makes reachable, so a newly found bypass is one new row, and every repository
that allows the command fails until its gate refuses it.

A row with a `wraps` field puts a wrapper no allow rule covers (`flock`,
`watch`, `find -exec`) in front of the command it requires. It runs wherever
that inner command is allowed, because every gate refuses the protected action
behind any wrapper, whether or not Claude Code would prompt first: bypass and
auto permission modes prompt for nothing.

The canonical copy lives in Danathar/atomic-image-builder, whose
docs/gate-refusal-corpus.md documents the row format. The copy here is pinned
by SHA-256: change a row there, then copy the file here and update the pin.

The hook is run the way Claude Code runs it, through the command
`.claude/settings.json` registers, so a registration that stops pointing at
the gate fails here as well.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
import glob
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/gate-refusal-corpus.json"
SETTINGS = ROOT / ".claude/settings.json"

REFUSE = "refuse"
ALLOW = "allow"
ROW_FIELDS = {"id", "class", "verdict", "requires", "command", "why"}
OPTIONAL_FIELDS = {"wraps"}

# SHA-256 of the canonical tests/fixtures/gate-refusal-corpus.json in
# Danathar/atomic-image-builder. Copy the file from there; never edit it here.
CANONICAL_SHA256 = "05e29d13f2f2ca116e0f3137842184bf90a18add6ae48fe4c18b5b5e19ce3fd8"


def allow_prefixes(settings: dict) -> list[str]:
    """Return the command prefix each wildcard `Bash(...)` allow rule covers.

    The fleet spells a prefix rule three ways (`git diff:*`, `git diff *`,
    `git diff*`); all three cover `git diff` and what follows it. A rule with
    no wildcard allows one exact command and covers no prefix.
    """
    prefixes = []
    for rule in settings.get("permissions", {}).get("allow", []):
        if not (rule.startswith("Bash(") and rule.endswith(")")):
            continue
        body = rule[len("Bash(") : -1]
        for suffix in (":*", " *", "*"):
            if body.endswith(suffix):
                prefixes.append(body[: -len(suffix)])
                break
    return prefixes


def covered(prefix: str, prefixes: list[str]) -> bool:
    """Say whether a prefix rule covers `prefix` at a word boundary."""
    return any(prefix == rule or prefix.startswith(rule + " ") for rule in prefixes)


def applies(row: dict, prefixes: list[str]) -> bool:
    """Say whether every prefix the row requires is allowed here."""
    return all(covered(prefix, prefixes) for prefix in row["requires"])


# The one-letter escapes bash decodes inside `$'...'`.
ANSI_C_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}
# `\xHH`, `\uHHHH` and `\UHHHHHHHH`: the most hex digits each one reads.
ANSI_C_HEX = {"x": 2, "u": 4, "U": 8}
GLOB_CHARS = re.compile(r"[*?[]")
# An innermost extglob group, `@(...)`, `?(...)`, `*(...)`, `+(...)` or
# `!(...)`, which matches no more than `*` does.
EXTGLOB_GROUP = re.compile(r"[@?*+!]\([^()]*\)")
MAX_BRACE_WORDS = 32


def ansi_c(text: str, start: int) -> tuple[str, int]:
    r"""Decode the `$'...'` body that begins at `start`.

    Return the decoded text and the index just past its closing quote.

    `\'` does not end the string, as it does not in bash. A backslash
    before anything bash does not decode is kept.
    """
    out, i = [], start
    while i < len(text) and text[i] != "'":
        if text[i] != "\\" or i + 1 == len(text):
            out.append(text[i])
            i += 1
            continue
        escape = text[i + 1]
        if escape in ANSI_C_ESCAPES:
            out.append(ANSI_C_ESCAPES[escape])
            i += 2
        elif escape in ANSI_C_HEX and (
            digits := re.match(rf"[0-9a-fA-F]{{1,{ANSI_C_HEX[escape]}}}", text[i + 2 :])
        ):
            out.append(chr(int(digits.group(), 16)))
            i += 2 + len(digits.group())
        elif digits := re.match(r"[0-7]{1,3}", text[i + 1 :]):
            out.append(chr(int(digits.group(), 8) & 0xFF))
            i += 1 + len(digits.group())
        else:
            out.append("\\")
            i += 1
    return "".join(out), i + 1


def shell_words(text: str) -> list[tuple[str, bool]]:
    """Split `text` into words the way bash reads them, before expansion.

    Each word comes back with its quotes and backslashes removed, `$'...'`
    decoded and `$"..."` read as `"..."`, plus whether any of it was quoted.
    An unterminated quote runs to the end of the text.
    """
    words: list[tuple[str, bool]] = []
    word, quoted, started, i = "", False, False, 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            if started:
                words.append((word, quoted))
            word, quoted, started = "", False, False
            i += 1
            continue
        started = True
        if text.startswith("$'", i):
            body, i = ansi_c(text, i + 2)
            word, quoted = word + body, True
        elif text.startswith('$"', i):
            i += 1
        elif char == "'":
            close = text.find("'", i + 1)
            close = len(text) if close < 0 else close
            word, quoted, i = word + text[i + 1 : close], True, close + 1
        elif char == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                if text[i] == "\\" and text[i + 1 : i + 2] in ('"', "\\", "$", "`"):
                    i += 1
                word += text[i : i + 1]
                i += 1
            quoted, i = True, i + 1
        elif char == "\\":
            word, quoted, i = word + text[i + 1 : i + 2], True, i + 2
        else:
            word, i = word + char, i + 1
    if started:
        words.append((word, quoted))
    return words


def brace_words(word: str) -> list[str]:
    """Return the words bash expands an unquoted brace group into, minus empty ones."""
    group = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if not group:
        return [word] if word else []
    expanded = [
        expanded_word
        for alternative in group.group(1).split(",")
        for expanded_word in brace_words(
            word[: group.start()] + alternative + word[group.end() :]
        )
    ]
    return expanded[:MAX_BRACE_WORDS]


def name_patterns(text: str) -> list[str]:
    """Return the words of `text` as patterns a command name is matched against.

    A word is read the way the shell reads a command name: unquoted brace
    groups are expanded, and an unquoted wildcard stands for any file it can
    match, so `/usr/bin/g?t` is the pattern `g?t`, and so is a bare `g?t`,
    which bash expands to `git` beside a file of that name and then looks
    up on PATH. An extglob group (`/usr/bin/g@(i)t`, under `shopt -s
    extglob`) is read as the `*` it can match no more than. Every other
    word matches only itself.
    """
    patterns = []
    for word, quoted in shell_words(text):
        if quoted:
            patterns.append(glob.escape(word))
            continue
        while EXTGLOB_GROUP.search(word):
            word = EXTGLOB_GROUP.sub("*", word)
        for expanded in brace_words(word):
            if GLOB_CHARS.search(expanded):
                patterns.append(PurePosixPath(expanded).name)
            else:
                patterns.append(glob.escape(expanded))
    return patterns


def inner_words(row: dict) -> list[str]:
    r"""Return the words a row's command is checked against for what it requires.

    A row without `wraps` is read the way the shell reads its command line,
    so `{git,} diff`, `/usr/bin/g?t diff`, a bare `g?t diff`, `/usr/bin/g@(i)t
    diff` (an extglob group, read as `*`), `$'git' diff` and `$'\x67it' diff`
    all name `git diff`, as they all run it. A quoted string stays one word,
    so `env -S 'git diff ...'` names nothing without `wraps`. A wrapped row is
    read the way the shell hands it to the wrapper, and each of those words
    is read again as a command line, so `env -S 'git diff ...'` names
    `git diff` as well, and so do `script -qc 'g""it diff ...'` and
    `script -qc "$'git' diff ..."`, as the shell that runs the string joins
    the name back together.

    Each word is a pattern (see `name_patterns`); `names()` matches a
    required prefix against them.
    """
    if "wraps" not in row:
        return name_patterns(row["command"])
    return [
        pattern
        for word, _ in shell_words(row["command"])
        for pattern in name_patterns(word)
    ]


def names(words: list[str], prefix: str) -> bool:
    """Say whether consecutive `words` (from `inner_words`) spell out `prefix`."""
    wanted = prefix.split()
    return any(
        all(
            fnmatchcase(name, pattern)
            for name, pattern in zip(wanted, words[i : i + len(wanted)], strict=True)
        )
        for i in range(len(words) - len(wanted) + 1)
    )


def hook_command(settings: dict) -> str:
    """Return the PreToolUse hook command registered for Bash."""
    for entry in settings["hooks"]["PreToolUse"]:
        if entry.get("matcher") == "Bash":
            return entry["hooks"][0]["command"]
    raise AssertionError(".claude/settings.json registers no PreToolUse hook for Bash")


def decide(command: str, hook: str) -> tuple[str, subprocess.CompletedProcess]:
    """Run the hook on `command` the way Claude Code does and read its verdict."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        ["bash", "-c", hook],
        input=payload,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT)},
        check=False,
    )
    refused = result.returncode == 2 or '"deny"' in result.stdout
    return (REFUSE if refused else ALLOW), result


class CorpusShapeTests(unittest.TestCase):
    """The table is read by gates in two languages, so its shape is pinned."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the corpus once."""
        cls.corpus = json.loads(CORPUS.read_text(encoding="utf-8"))

    def test_the_copy_matches_the_canonical_corpus(self) -> None:
        """The copy here is byte for byte the pinned canonical file."""
        digest = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            CANONICAL_SHA256,
            "tests/fixtures/gate-refusal-corpus.json differs from the pinned "
            "canonical copy; change rows in Danathar/atomic-image-builder and "
            "copy the file here",
        )

    def test_the_schema_version_is_one_this_file_reads(self) -> None:
        """The corpus uses the schema version this runner understands."""
        self.assertEqual(self.corpus["schema"], 1)

    def test_every_row_has_exactly_the_documented_fields(self) -> None:
        """Each row carries exactly the documented fields, all filled in."""
        for row in self.corpus["rows"]:
            with self.subTest(row=row.get("id")):
                self.assertLessEqual(ROW_FIELDS, set(row))
                self.assertLessEqual(set(row), ROW_FIELDS | OPTIONAL_FIELDS)
                self.assertIn(row["verdict"], (REFUSE, ALLOW))
                self.assertTrue(row["requires"], "a row has to name what it depends on")
                for prefix in row["requires"]:
                    self.assertIsInstance(prefix, str)
                    self.assertEqual(prefix, prefix.strip())
                self.assertTrue(row["command"].strip())
                self.assertTrue(row["why"].strip())

    def test_row_ids_are_unique(self) -> None:
        """No two rows share an id."""
        ids = [row["id"] for row in self.corpus["rows"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_row_requires_the_command_it_starts_with(self) -> None:
        """A row's command names one of the prefixes it requires.

        A row whose command is outside every prefix it requires would run in
        a repository that never allows the command, and fail there for a gate
        that has nothing to do with it.
        """
        for row in self.corpus["rows"]:
            with self.subTest(row=row["id"]):
                words = inner_words(row)
                self.assertTrue(
                    any(names(words, prefix) for prefix in row["requires"]),
                    f"{row['command']!r} names none of {row['requires']}",
                )

    def test_a_wrapped_row_starts_with_the_wrapper_it_names(self) -> None:
        """A row's `wraps` is its first word, and never a prefix it requires.

        `wraps` is what tells a reader the row is not reachable through an
        allow rule alone. If the wrapper were one of the required prefixes,
        the row would run only where the wrapper itself is allowed.
        """
        for row in self.corpus["rows"]:
            if "wraps" not in row:
                continue
            with self.subTest(row=row["id"]):
                wrapper = row["wraps"]
                self.assertIsInstance(wrapper, str)
                self.assertTrue(
                    wrapper and wrapper == wrapper.strip() and " " not in wrapper
                )
                self.assertEqual(shlex.split(row["command"])[0], wrapper)
                self.assertFalse(
                    any(prefix.split()[0] == wrapper for prefix in row["requires"])
                )

    def test_both_verdicts_are_present(self) -> None:
        """The corpus holds both refuse and allow rows.

        Refusals alone would pass a gate that refuses everything, and a gate
        that refuses everything gets switched off.
        """
        verdicts = {row["verdict"] for row in self.corpus["rows"]}
        self.assertEqual(verdicts, {REFUSE, ALLOW})


class PrefixMatchTests(unittest.TestCase):
    """How an allow rule is read as a command prefix."""

    def test_each_fleet_spelling_of_a_prefix_rule_covers_the_prefix(self) -> None:
        """`:*`, ` *` and `*` all make a prefix rule."""
        for rule in ("Bash(git diff:*)", "Bash(git diff *)", "Bash(git diff*)"):
            with self.subTest(rule=rule):
                prefixes = allow_prefixes({"permissions": {"allow": [rule]}})
                self.assertTrue(covered("git diff", prefixes))

    def test_an_exact_rule_covers_no_prefix(self) -> None:
        """A rule without a wildcard covers no prefix."""
        prefixes = allow_prefixes({"permissions": {"allow": ["Bash(ruff check)"]}})
        self.assertFalse(covered("ruff check", prefixes))

    def test_a_prefix_is_covered_only_at_a_word_boundary(self) -> None:
        """`gh pr` covers `gh pr view` but not `gh prx view`."""
        prefixes = allow_prefixes({"permissions": {"allow": ["Bash(gh pr:*)"]}})
        self.assertTrue(covered("gh pr view", prefixes))
        self.assertFalse(covered("gh prx view", prefixes))
        prefixes = allow_prefixes({"permissions": {"allow": ["Bash(gh pr view:*)"]}})
        self.assertFalse(covered("gh pr", prefixes))


class CorpusVerdictTests(unittest.TestCase):
    """Every reachable row gets the corpus verdict from this repository's gate."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the corpus and keep the rows this allow list reaches."""
        cls.corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        cls.prefixes = allow_prefixes(settings)
        cls.hook = hook_command(settings)
        cls.rows = [row for row in cls.corpus["rows"] if applies(row, cls.prefixes)]

    def test_enough_rows_apply_here_to_mean_something(self) -> None:
        """At least 20 rows apply here.

        If the allow list stopped covering `git diff`, every row would be
        skipped and the verdict test would pass on nothing.
        """
        self.assertGreaterEqual(len(self.rows), 20)

    def test_the_rows_that_apply_here_hold_both_verdicts(self) -> None:
        """Both refuse and allow rows reach this gate.

        The corpus as a whole holding both verdicts says nothing about the
        rows run here: if only refusals applied, a gate that refused every
        command would pass, and nothing would show that the commands this
        repository allows on purpose still get through.
        """
        verdicts = {row["verdict"] for row in self.rows}
        self.assertEqual(verdicts, {REFUSE, ALLOW})

    def test_every_row_this_repository_allows_is_decided_the_way_it_says(self) -> None:
        """The gate refuses each refuse row and lets each allow row through."""
        for row in self.rows:
            with self.subTest(row=row["id"], command=row["command"]):
                verdict, result = decide(row["command"], self.hook)
                self.assertIn(result.returncode, (0, 2), result.stderr)
                self.assertEqual(
                    verdict,
                    row["verdict"],
                    f"the corpus says {row['verdict']} {row['command']!r}: {row['why']}",
                )


def probe(case: type[unittest.TestCase], name: str, **fixture) -> unittest.TestResult:
    """Run one test of `case` against `fixture` in place of what setUpClass reads."""
    result = unittest.TestResult()
    type("Probe", (case,), fixture)(name).run(result)
    return result


class HarnessContractTests(unittest.TestCase):
    """The runner's rules, on inputs the real files never supply.

    Here every row's prefixes are allowed together, the hook answers only with
    exit status 2, the Bash hook is the first one registered, and the table is
    valid. So the branches below never run against the real files, and each
    could be dropped without a failure. Danathar/atomic-image-builder#616 added
    the same tests to the canonical runner.
    """

    ROW = {
        "id": "r",
        "class": "c",
        "verdict": REFUSE,
        "requires": ["git diff"],
        "command": "git diff /dev/null ./.env",
        "why": "w",
    }

    def corpus(self, *rows: dict) -> dict:
        """Build a schema-1 corpus holding `rows`."""
        return {"schema": 1, "rows": list(rows)}

    def assertProbeFails(self, result: unittest.TestResult) -> None:
        """Say the probed guard failed on its input rather than erroring."""
        self.assertEqual(result.errors, [])
        self.assertTrue(result.failures, "the guard let a broken input through")

    def test_a_row_runs_only_when_every_prefix_it_requires_is_allowed(self) -> None:
        """A row naming two prefixes needs both of them allowed."""
        row = {**self.ROW, "requires": ["git diff", "git status"]}
        self.assertFalse(applies(row, ["git status"]))
        self.assertFalse(applies(row, ["git diff"]))
        self.assertTrue(applies(row, ["git diff", "git status"]))

    def test_only_bash_rules_cover_a_prefix(self) -> None:
        """A wildcard rule for another tool covers no command prefix."""
        settings = {"permissions": {"allow": ["Read(./docs/*)", "WebFetch(domain:*)"]}}
        self.assertEqual(allow_prefixes(settings), [])

    def test_the_hook_registered_for_bash_is_the_one_run(self) -> None:
        """The Bash entry is found behind another tool's, and its absence fails."""
        entries = [
            {"matcher": "Edit", "hooks": [{"command": "edit-hook"}]},
            {"matcher": "Bash", "hooks": [{"command": "bash-hook"}]},
        ]
        self.assertEqual(hook_command({"hooks": {"PreToolUse": entries}}), "bash-hook")
        with self.assertRaises(AssertionError):
            hook_command({"hooks": {"PreToolUse": entries[:1]}})

    def test_a_deny_decision_on_stdout_is_a_refusal(self) -> None:
        """A hook may refuse with exit status 0 and a deny decision on stdout."""
        deny = (
            '{"hookSpecificOutput": {"hookEventName": "PreToolUse",'
            ' "permissionDecision": "deny"}}'
        )
        verdict, result = decide("git diff", f"cat >/dev/null; printf '%s' '{deny}'")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(verdict, REFUSE)

    def test_exit_status_decides_when_stdout_is_silent(self) -> None:
        """With nothing on stdout, exit status 2 refuses and 0 allows."""
        self.assertEqual(decide("git diff", "cat >/dev/null; exit 2")[0], REFUSE)
        self.assertEqual(decide("git diff", "cat >/dev/null; exit 0")[0], ALLOW)

    def test_the_hook_reads_the_command_from_a_bash_tool_payload(self) -> None:
        """The hook gets the command inside a Bash tool payload on stdin."""
        hook = (
            "python3 -c 'import json,sys; p=json.load(sys.stdin);"
            ' sys.exit(2 if p["tool_name"] == "Bash"'
            ' and p["tool_input"]["command"] == "git diff x" else 0)\''
        )
        self.assertEqual(decide("git diff x", hook)[0], REFUSE)
        self.assertEqual(decide("git diff y", hook)[0], ALLOW)

    def test_a_table_of_refusals_alone_is_rejected(self) -> None:
        """The both-verdicts guard fails on a table with no allow row."""
        self.assertProbeFails(
            probe(
                CorpusShapeTests,
                "test_both_verdicts_are_present",
                corpus=self.corpus(self.ROW),
            )
        )

    def test_a_row_outside_the_prefix_it_requires_is_rejected(self) -> None:
        """The prefix guard fails on a row whose command it does not name."""
        row = {**self.ROW, "command": "cat .env"}
        self.assertProbeFails(
            probe(
                CorpusShapeTests,
                "test_a_row_requires_the_command_it_starts_with",
                corpus=self.corpus(row),
            )
        )

    def test_a_repeated_row_id_is_rejected(self) -> None:
        """The id guard fails when two rows share an id."""
        other = {**self.ROW, "verdict": ALLOW, "command": "git diff"}
        self.assertProbeFails(
            probe(
                CorpusShapeTests,
                "test_row_ids_are_unique",
                corpus=self.corpus(self.ROW, other),
            )
        )

    def test_a_row_with_a_field_missing_or_extra_is_rejected(self) -> None:
        """The field guard fails on a row short of a field or carrying an extra."""
        short = {key: value for key, value in self.ROW.items() if key != "why"}
        for row in (short, {**self.ROW, "note": "n"}):
            with self.subTest(fields=sorted(row)):
                self.assertProbeFails(
                    probe(
                        CorpusShapeTests,
                        "test_every_row_has_exactly_the_documented_fields",
                        corpus=self.corpus(row),
                    )
                )

    def test_a_wrapped_row_runs_where_its_inner_command_is_allowed(self) -> None:
        """No allow rule covers `flock`; the row runs wherever `git diff` is allowed."""
        row = {
            **self.ROW,
            "wraps": "flock",
            "command": "flock /tmp/l git diff /dev/null ./.env",
        }
        self.assertTrue(applies(row, ["git diff"]))
        self.assertFalse(applies(row, ["git status"]))

    def test_a_wrapped_row_names_what_it_requires_inside_a_quoted_word(self) -> None:
        """`env -S 'git diff ...'` names `git diff` once its quoted word is split."""
        row = {
            **self.ROW,
            "wraps": "env",
            "command": "env -S 'git diff /dev/null ./.env'",
        }
        self.assertIn("git", inner_words(row))
        self.assertTrue(
            probe(
                CorpusShapeTests,
                "test_a_row_requires_the_command_it_starts_with",
                corpus=self.corpus(row),
            ).wasSuccessful()
        )

    def test_a_row_names_what_it_requires_in_any_spelling_the_shell_runs(
        self,
    ) -> None:
        """A brace group, path wildcard or `$'...'` name runs `git`, so names it."""
        for command in (
            "{git,} diff /dev/null ./.env",
            "/usr/bin/g?t diff /dev/null ./.env",
            "/usr/bin/g[i]t diff /dev/null ./.env",
            "$'git' diff /dev/null ./.env",
            '$"git" diff /dev/null ./.env',
            "$'\\x67it' diff /dev/null ./.env",
            "$'\\147it' diff /dev/null ./.env",
            "$'\\u0067it' diff /dev/null ./.env",
            "touch git; g?t diff /dev/null ./.env",
            "shopt -s extglob\n/usr/bin/g@(i)t diff /dev/null ./.env",
            "shopt -s extglob\n/usr/bin/@(git) diff /dev/null ./.env",
            "shopt -s extglob\n/usr/bin/g@(i)@(t) diff /dev/null ./.env",
        ):
            with self.subTest(command=command):
                row = {**self.ROW, "command": command}
                self.assertTrue(
                    probe(
                        CorpusShapeTests,
                        "test_a_row_requires_the_command_it_starts_with",
                        corpus=self.corpus(row),
                    ).wasSuccessful()
                )

    def test_a_spelling_the_shell_does_not_run_as_the_name_is_rejected(
        self,
    ) -> None:
        """A quoted wildcard, brace or extglob group names nothing, nor a brace-built word.

        `g{i,x}t` runs `git gxt`, and `g@(i)x` matches no file named git.
        """
        for command in (
            "'/usr/bin/g?t' diff /dev/null ./.env",
            "'/usr/bin/g@(i)t' diff /dev/null ./.env",
            "/usr/bin/g@(i)x diff /dev/null ./.env",
            "'{git,}' diff /dev/null ./.env",
            "g{i,x}t diff /dev/null ./.env",
            "/usr/bin/g?t status",
            "$'\\x67itx' diff /dev/null ./.env",
        ):
            with self.subTest(command=command):
                row = {**self.ROW, "command": command}
                self.assertProbeFails(
                    probe(
                        CorpusShapeTests,
                        "test_a_row_requires_the_command_it_starts_with",
                        corpus=self.corpus(row),
                    )
                )

    def test_a_row_without_wraps_is_read_as_written(self) -> None:
        """Only a wrapped row has its quoted words split."""
        row = {**self.ROW, "command": "env -S 'git diff /dev/null ./.env'"}
        self.assertNotIn("git", inner_words(row))

    def test_a_wrapped_row_led_by_another_program_is_rejected(self) -> None:
        """The wrapper guard fails on a row whose first word is another program."""
        row = {
            **self.ROW,
            "wraps": "flock",
            "command": "watch git diff /dev/null ./.env",
        }
        self.assertProbeFails(
            probe(
                CorpusShapeTests,
                "test_a_wrapped_row_starts_with_the_wrapper_it_names",
                corpus=self.corpus(row),
            )
        )

    def test_a_wrapped_row_that_requires_its_wrapper_is_rejected(self) -> None:
        """The wrapper guard fails when the wrapper is also a required prefix."""
        row = {
            **self.ROW,
            "wraps": "flock",
            "requires": ["flock", "git diff"],
            "command": "flock /tmp/l git diff /dev/null ./.env",
        }
        self.assertProbeFails(
            probe(
                CorpusShapeTests,
                "test_a_wrapped_row_starts_with_the_wrapper_it_names",
                corpus=self.corpus(row),
            )
        )

    def test_too_few_reachable_rows_is_rejected(self) -> None:
        """The row-count guard fails when nothing applies here."""
        self.assertProbeFails(
            probe(
                CorpusVerdictTests,
                "test_enough_rows_apply_here_to_mean_something",
                rows=[],
            )
        )

    def test_reachable_refusals_alone_are_rejected(self) -> None:
        """The reachable-verdicts guard fails when only refuse rows apply here."""
        self.assertProbeFails(
            probe(
                CorpusVerdictTests,
                "test_the_rows_that_apply_here_hold_both_verdicts",
                rows=[self.ROW],
            )
        )

    def test_a_hook_that_crashes_fails_even_an_allow_row(self) -> None:
        """Exit status 1 is a hook error, not an allow, so the verdict test fails.

        Claude Code does not treat a crash as a refusal, so an allow row would
        match it on the verdict alone.
        """
        row = {**self.ROW, "verdict": ALLOW, "command": "git diff"}
        self.assertProbeFails(
            probe(
                CorpusVerdictTests,
                "test_every_row_this_repository_allows_is_decided_the_way_it_says",
                rows=[row],
                hook="cat >/dev/null; exit 1",
            )
        )

    def test_the_guards_pass_the_real_files(self) -> None:
        """Each probed guard passes on the real table and the real allow list.

        So the probes above fail for the input they were given, not for how a
        test is run outside its suite.
        """
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        for name in (
            "test_both_verdicts_are_present",
            "test_a_row_requires_the_command_it_starts_with",
            "test_row_ids_are_unique",
            "test_every_row_has_exactly_the_documented_fields",
            "test_a_wrapped_row_starts_with_the_wrapper_it_names",
        ):
            with self.subTest(test=name):
                result = probe(CorpusShapeTests, name, corpus=corpus)
                self.assertTrue(result.wasSuccessful())
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        prefixes = allow_prefixes(settings)
        rows = [row for row in corpus["rows"] if applies(row, prefixes)]
        for name in (
            "test_enough_rows_apply_here_to_mean_something",
            "test_the_rows_that_apply_here_hold_both_verdicts",
        ):
            with self.subTest(test=name):
                result = probe(CorpusVerdictTests, name, rows=rows)
                self.assertTrue(result.wasSuccessful())


if __name__ == "__main__":
    unittest.main()
