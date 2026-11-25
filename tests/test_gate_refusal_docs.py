"""Join the gate's refusal classes to the page that explains the gate.

`tests/fixtures/gate-refusal-corpus.json` is the table every repository of the
fleet runs its Bash gate against, and `docs/SECURITY-AI.md` is the page a
reader trusts about what that gate refuses. They are separate files, so the
corpus grew, the hook was strengthened, and nothing joined either to the
sentence about it: the page went on describing a gate that refused less
(Danathar/zfs-kinoite-complex#612 found the same drift and #613 added this
check there).

This module reads the `class` of every row whose verdict is `refuse`, looks
each one up in `CLASS_KEYWORDS`, and fails naming every class whose keywords
the page does not carry, or that has no keywords declared here at all.

A keyword is not proof the page explains the class well. It is the smallest
thing that stops the page going silent: a class with no mention at all is what
this catches, and a reviewer still reads the sentence. The corpus is a pinned
shared copy and is never edited here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/gate-refusal-corpus.json"
SECURITY_AI = ROOT / "docs/SECURITY-AI.md"

# Per refusal class, the words the page must carry for it. Every keyword listed
# must appear (case-insensitive, line wraps ignored), so a class with several
# spellings lists each one. A new class in the corpus has no entry here and
# fails until someone declares what the page says about it.
CLASS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "comment": ("inside a comment",),
    "compound command": ("compound command",),
    "configuration override": ("safe.bareRepository", "--config-env"),
    "configuration reach": ("XDG_CONFIG_HOME", "printf -v", "mapfile"),
    "configuration removal": (
        "unset",
        "export -n",
        "+x",
        "env -u",
        "env -i",
        "--ignore-environment",
        "exec -c",
        "GIT_CONFIG",
    ),
    "directory change": ("directory change", "pushd"),
    "environment": ("GIT_EXTERNAL_DIFF",),
    "gh --jq environment": ("--jq",),
    "here-document": ("here-document",),
    "local ref": ("git branch -D",),
    "name spelling": ("extglob", "g?t"),
    "named repository": ("--git-dir", "--work-tree", "--bare"),
    "option that reads a file": ("--shallow-file", "--contents"),
    "plain-file read": ("plain-file mode",),
    "push": ("git push",),
    "redirection": ("redirection",),
    "shell expansion": ("brace", "substitution"),
    "unlisted program": ("setarch",),
    "wrapper": ("wrapper", "flock", "find -exec"),
    "write through git": ("--output=",),
}


def refusal_classes(corpus: dict) -> list[str]:
    """Return the distinct `class` of every row the gate has to refuse, sorted."""
    rows = (row for row in corpus["rows"] if row["verdict"] == "refuse")
    return sorted({row["class"] for row in rows})


def undocumented(
    corpus: dict, doc: str, keywords: dict[str, tuple[str, ...]]
) -> list[str]:
    """Return one message per refusal class that `doc` does not explain.

    A class with no entry in `keywords` is reported as well: silence about a
    class the gate refuses is the failure this exists to catch, whether the
    page or the table is the one that fell behind.
    """
    flowed = " ".join(doc.split()).lower()
    problems = []
    for name in refusal_classes(corpus):
        if name not in keywords:
            problems.append(f"{name!r}: no keywords declared in CLASS_KEYWORDS")
            continue
        missing = [word for word in keywords[name] if word.lower() not in flowed]
        if missing:
            problems.append(f"{name!r}: SECURITY-AI.md does not mention {missing}")
    return problems


class GateRefusalsAreDocumentedTests(unittest.TestCase):
    """docs/SECURITY-AI.md names every class of command the gate refuses."""

    def test_every_refusal_class_in_the_corpus_is_in_security_ai(self) -> None:
        """Every class a `refuse` row carries is explained on the page."""
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        doc = SECURITY_AI.read_text(encoding="utf-8")
        self.assertEqual(undocumented(corpus, doc, CLASS_KEYWORDS), [])

    def test_the_keywords_cover_exactly_the_classes_the_corpus_refuses(self) -> None:
        """A class the table keeps after the corpus drops it is a stale promise."""
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        self.assertEqual(sorted(CLASS_KEYWORDS), refusal_classes(corpus))


class UndocumentedCheckTests(unittest.TestCase):
    """The check above fails for the reasons it claims to."""

    CORPUS: ClassVar[dict] = {
        "rows": [
            {"class": "configuration removal", "verdict": "refuse"},
            {"class": "wrapper", "verdict": "refuse"},
            {"class": "ordinary use", "verdict": "allow"},
        ]
    }
    KEYWORDS: ClassVar[dict] = {
        "configuration removal": ("env -i", "exec -c"),
        "wrapper": ("wrapper",),
    }
    DOC = "A wrapper is refused. So are env -i and\nexec -c."

    def test_a_page_that_carries_every_keyword_passes(self) -> None:
        """Keywords match across a line wrap and in any case."""
        self.assertEqual(undocumented(self.CORPUS, self.DOC, self.KEYWORDS), [])

    def test_a_class_the_page_never_mentions_is_named(self) -> None:
        """A class with none of its keywords on the page is reported by name."""
        problems = undocumented(self.CORPUS, "env -i and exec -c only.", self.KEYWORDS)
        self.assertEqual(len(problems), 1)
        self.assertIn("'wrapper'", problems[0])

    def test_one_missing_keyword_of_a_class_is_named(self) -> None:
        """A single absent keyword is reported, and the present one is not."""
        problems = undocumented(self.CORPUS, "A wrapper. Also env -i.", self.KEYWORDS)
        self.assertEqual(len(problems), 1)
        self.assertIn("'configuration removal'", problems[0])
        self.assertIn("exec -c", problems[0])
        self.assertNotIn("env -i'", problems[0])

    def test_a_class_with_no_declared_keywords_is_named(self) -> None:
        """A class the table does not list fails rather than passing silently."""
        problems = undocumented(self.CORPUS, self.DOC, {"wrapper": ("wrapper",)})
        self.assertEqual(len(problems), 1)
        self.assertIn("'configuration removal'", problems[0])
        self.assertIn("no keywords declared", problems[0])

    def test_an_allowed_class_needs_no_mention(self) -> None:
        """Only `refuse` rows contribute a class."""
        self.assertEqual(
            refusal_classes(self.CORPUS), ["configuration removal", "wrapper"]
        )


if __name__ == "__main__":
    unittest.main()
