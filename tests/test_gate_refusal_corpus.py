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

The canonical copy lives in Danathar/atomic-image-builder, whose
docs/gate-refusal-corpus.md documents the row format. The copy here is pinned
by SHA-256: change a row there, then copy the file here and update the pin.

The hook is run the way Claude Code runs it, through the command
`.claude/settings.json` registers, so a registration that stops pointing at
the gate fails here as well.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/gate-refusal-corpus.json"
SETTINGS = ROOT / ".claude/settings.json"

REFUSE = "refuse"
ALLOW = "allow"
ROW_FIELDS = {"id", "class", "verdict", "requires", "command", "why"}

# SHA-256 of the canonical tests/fixtures/gate-refusal-corpus.json in
# Danathar/atomic-image-builder. Copy the file from there; never edit it here.
CANONICAL_SHA256 = "8a4bf0f7118af630f2549633cb91d33a324312d5750bbc83e0f0a8489700cf71"


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
                self.assertEqual(set(row), ROW_FIELDS)
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
                words = row["command"].split()
                self.assertTrue(
                    any(
                        " ".join(words[i : i + len(p.split())]) == p
                        for p in row["requires"]
                        for i in range(len(words))
                    ),
                    f"{row['command']!r} names none of {row['requires']}",
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
