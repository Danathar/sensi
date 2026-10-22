"""Tests for scripts/mutate_changed.py.

Not part of the component, so not measured by the coverage gate - but this
script decides whether a pull request's changed lines are asserted on, and
it can be wrong in two quiet ways. A mutant it never generates is a gap it
never reports, and a mutant it misreads - the wrong line changed, the wrong
test file chosen, an equivalents entry that matches nothing - turns the gate
red or green for the wrong reason.

Everything here runs on in-memory sources and temporary directories. No test
writes a mutant into this checkout or starts pytest: the parts that do are
`run` and its helpers, which the mutation job itself exercises on every pull
request.
"""

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "mutate_changed.py"
_spec = importlib.util.spec_from_file_location("mutate_changed", _SCRIPT)
mutate_changed = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mutate_changed
_spec.loader.exec_module(mutate_changed)

Mutant = mutate_changed.Mutant


def _mutated(source: str, changed: set[int]) -> set[tuple[int, str]]:
    """Return (line, mutated text) for every mutant of source's changed lines."""
    found = mutate_changed.mutants("scripts/example.py", source, changed)
    return {(mutant.line, mutant.mutated) for mutant in found}


# --------------------------------------------------------------------------
# Which lines changed
# --------------------------------------------------------------------------

_DIFF = """\
diff --git a/scripts/example.py b/scripts/example.py
index 1111111..2222222 100644
--- a/scripts/example.py
+++ b/scripts/example.py
@@ -3,0 +4,2 @@ def main():
+    first = 1
+++ b/not-a-header.py
@@ -10 +12 @@ def main():
-    old = 1
+    new = 1
@@ -20,2 +21,0 @@ def main():
-    gone = 1
-    gone = 2
diff --git a/old.py b/old.py
deleted file mode 100644
index 3333333..0000000
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-x = 1
diff --git a/scripts/new.py b/scripts/new.py
new file mode 100644
index 0000000..4444444
--- /dev/null
+++ b/scripts/new.py
@@ -0,0 +1,3 @@
+a = 1
+b = 2
+c = 3
"""


def test_changed_lines_reads_each_hunk_s_new_side() -> None:
    """Lines added or rewritten count; a deletion-only hunk adds none.

    The second added line of the first hunk reads `+++ b/...` in the diff. It
    is content, not a file header, because it comes after the hunk started.
    """
    assert mutate_changed.changed_lines(_DIFF) == {
        "scripts/example.py": {4, 5, 12},
        "scripts/new.py": {1, 2, 3},
    }


def test_changed_lines_of_an_empty_diff_is_nothing() -> None:
    """No diff, no files: the gate then has nothing to mutate."""
    assert mutate_changed.changed_lines("") == {}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("custom_components/sensi/example.py", True),
        ("scripts/example.py", True),
        (".claude/hooks/example.py", True),
        ("scripts/mutate_changed.py", False),
        ("tests/test_example.py", False),
        ("docs/example.py", False),
        ("custom_components/sensi/manifest.json", False),
    ],
)
def test_in_scope_is_python_under_the_three_directories(
    path: str, expected: bool
) -> None:
    """The script itself is left out: its own tests would judge its mutants."""
    assert mutate_changed.in_scope(path) is expected


def test_changed_python_lines_diffs_the_working_tree_against_the_merge_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The git half: a real repository, a rewritten line and an added one."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "docs").mkdir()
    script = repo / "scripts" / "example.py"
    script.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    (repo / "docs" / "example.py").write_text("x = 1\n", encoding="utf-8")
    git = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
    script.write_text("a = 1\nb = 20\nc = 3\nd = 4\n", encoding="utf-8")
    (repo / "docs" / "example.py").write_text("x = 2\n", encoding="utf-8")
    monkeypatch.setattr(mutate_changed, "ROOT", repo)

    changed = mutate_changed.changed_python_lines("HEAD")

    assert changed == {"scripts/example.py": {2, 4}}


# --------------------------------------------------------------------------
# Which mutants are made
# --------------------------------------------------------------------------


def test_an_if_test_is_forced_both_ways_and_a_return_value_dropped() -> None:
    """The two edits behind most of the gate's fail-open survivors in #655."""
    source = "def check(a, b):\n    if a == b:\n        return a\n    return None\n"

    assert _mutated(source, {2, 3}) == {
        (2, "if True:"),
        (2, "if False:"),
        (2, "if a != b:"),
        (3, "return None"),
    }


def test_only_the_changed_lines_are_mutated() -> None:
    """An unchanged line is not this pull request's to answer for."""
    assert _mutated("a = 1\nb = 2\n", {2}) == {(2, "b = 3"), (2, "b = 1")}


def test_an_ordering_is_negated_and_moved_across_its_boundary() -> None:
    """`<` becomes `>=` and `<=`; the second is the off-by-one."""
    assert _mutated("ok = a < b\n", {1}) == {(1, "ok = a >= b"), (1, "ok = a <= b")}


def test_and_becomes_or_and_each_operand_is_dropped() -> None:
    """An operand dropped is how `if hexa or short or long:` lost `long`."""
    assert _mutated("ok = a and b\n", {1}) == {
        (1, "ok = a or b"),
        (1, "ok = b"),
        (1, "ok = a"),
    }
    assert _mutated("ok = a or b or c\n", {1}) == {
        (1, "ok = a and b and c"),
        (1, "ok = b or c"),
        (1, "ok = a or c"),
        (1, "ok = a or b"),
    }


def test_a_not_is_dropped() -> None:
    """The operand stands alone."""
    assert _mutated("ok = not a\n", {1}) == {(1, "ok = a")}


def test_an_integer_moves_by_one_both_ways() -> None:
    """`split("=", 1)` to `2`, and `[0]` to `[-1]`, are both in #655's table."""
    assert _mutated("n = part(x, 1)[0]\n", {1}) == {
        (1, "n = part(x, 2)[0]"),
        (1, "n = part(x, 0)[0]"),
        (1, "n = part(x, 1)[1]"),
        (1, "n = part(x, 1)[-1]"),
    }


def test_a_boolean_constant_is_flipped() -> None:
    """True and False are not integers here, though Python says they are."""
    assert _mutated("flag = True\n", {1}) == {(1, "flag = False")}


def test_continue_and_break_are_dropped() -> None:
    """Each becomes `pass`; the unchanged `if` above them is left alone."""
    source = "for item in items:\n    if item:\n        continue\n    break\n"

    assert _mutated(source, {3, 4}) == {(3, "pass"), (4, "pass")}


def test_a_while_test_is_forced_false_only() -> None:
    """A loop forced true would be a hang, not a finding."""
    assert _mutated("while a:\n    a = step(a)\n", {1}) == {(1, "while False:")}


def test_a_return_none_and_an_already_forced_test_are_left_alone() -> None:
    """An edit that changes nothing is not a mutant.

    `if True:` yields one mutant, not two: forcing the test false and flipping
    the constant produce the same line.
    """
    assert _mutated("def f():\n    return None\n", {2}) == set()
    assert _mutated("if True:\n    pass\n", {1}) == {(1, "if False:")}


def test_two_edits_that_produce_the_same_line_are_one_mutant() -> None:
    """Either `not` of `not not a` dropped leaves `not a`."""
    found = mutate_changed.mutants("scripts/example.py", "ok = not not a\n", {1})

    assert [mutant.mutated for mutant in found] == ["ok = not a"]


def test_a_statement_over_several_lines_is_replaced_whole() -> None:
    """`return all(...)` over three lines becomes one `return None`."""
    source = "def every(a):\n    return all(\n        a\n    )\n"
    lines = source.splitlines(keepends=True)

    (mutant,) = mutate_changed.mutants("scripts/example.py", source, {3})

    assert (mutant.line, mutant.end) == (2, 4)
    assert mutant.original == "return all( a )"
    assert mutant.mutated == "return None"
    assert mutant.apply(lines) == "def every(a):\n    return None\n"


def test_the_splice_counts_columns_in_bytes() -> None:
    """The parser's column offsets are UTF-8 bytes; `é` is two of them."""
    assert _mutated('ok = "é" == a\n', {1}) == {(1, "ok = 'é' != a")}


def test_an_operator_is_parenthesised_where_its_place_needs_it() -> None:
    """Inside a conditional expression the swapped `and` keeps its grouping."""
    assert _mutated("v = a or b if c else d\n", {1}) == {
        (1, "v = (a and b) if c else d"),
        (1, "v = b if c else d"),
        (1, "v = a if c else d"),
    }


def test_a_mutant_that_does_not_parse_is_not_kept() -> None:
    """The check every candidate goes through before it is kept."""
    assert mutate_changed._parses("x = 1\n")
    assert not mutate_changed._parses("x = (\n")


def test_apply_replaces_exactly_the_mutated_lines() -> None:
    """The lines before and after the mutant are the original bytes."""
    mutant = Mutant("scripts/example.py", 2, 2, "b", "B\n")

    assert mutant.apply(["a\n", "b\n", "c\n"]) == "a\nB\nc\n"


# --------------------------------------------------------------------------
# Which tests judge a file
# --------------------------------------------------------------------------


@pytest.fixture(name="tests")
def tests_fixture(tmp_path: Path) -> Path:
    """Return a tests directory naming fictional modules in each way it can."""
    tests = tmp_path / "tests"
    (tests / "e2e").mkdir(parents=True)
    files = {
        "test_path.py": '"hooks" / "example-hook.py"\n',
        "test_dotted.py": "from custom_components.sensi.example import thing\n",
        "test_package.py": "from custom_components.sensi import (\n    example,\n)\n",
        "test_lookalike.py": '"test_example.py" custom_components.sensi.example_two\n',
        "e2e/test_flow.py": "def test_flow():\n    pass\n",
        "conftest.py": '"example.py"\n',
        "helper.py": '"example.py"\n',
    }
    for name, text in files.items():
        (tests / name).write_text(text, encoding="utf-8")
    return tests


def test_a_component_module_is_judged_by_its_importers_and_every_e2e_test(
    tests: Path,
) -> None:
    """A look-alike name is not a match, and a non-test file is never run."""
    found = mutate_changed.tests_for("custom_components/sensi/example.py", tests)

    assert found == [
        "tests/e2e/test_flow.py",
        "tests/test_dotted.py",
        "tests/test_package.py",
    ]


def test_a_hook_is_judged_by_the_tests_that_name_its_file(tests: Path) -> None:
    """The hook is loaded by path, and no end-to-end test drives it."""
    found = mutate_changed.tests_for(".claude/hooks/example-hook.py", tests)

    assert found == ["tests/test_path.py"]


def test_the_package_init_is_judged_by_tests_importing_the_package(
    tests: Path,
) -> None:
    """`from custom_components.sensi import ...` loads `__init__.py`."""
    found = mutate_changed.tests_for("custom_components/sensi/__init__.py", tests)

    assert found == ["tests/e2e/test_flow.py", "tests/test_package.py"]


# --------------------------------------------------------------------------
# The equivalents file
# --------------------------------------------------------------------------

_ENTRIES = """\
# A comment.

file: scripts/example.py
line: 12
original: if count > 0:
mutant: if count >= 0:
reason: equivalent; count starts at 1

# Another comment.
file: .claude/hooks/example-hook.py
line: 3
original: return "refused: " + name
mutant: return None
reason: fail-closed; the caller refuses on None
"""


def test_the_equivalents_are_read_as_file_original_mutant_keys() -> None:
    """The line number is for the reader; it is not part of the key."""
    assert mutate_changed.read_equivalents(_ENTRIES) == {
        ("scripts/example.py", "if count > 0:", "if count >= 0:"),
        (".claude/hooks/example-hook.py", 'return "refused: " + name', "return None"),
    }


def test_the_committed_equivalents_file_parses() -> None:
    """A malformed edit to the list fails here, not in the mutation job."""
    text = mutate_changed.EQUIVALENTS.read_text(encoding="utf-8")

    assert isinstance(mutate_changed.read_equivalents(text), set)
    assert mutate_changed.EQUIVALENTS == _ROOT / mutate_changed.EQUIVALENTS_NAME


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("file: a.py\nline: 1\noriginal: x\nmutant: y\n", "no reason"),
        ("file: a.py\nline: one\noriginal: x\nmutant: y\nreason: r\n", "not a number"),
        ("file: a.py\nfile: b.py\n", "given twice"),
        ("fiel: a.py\n", "expected one of"),
        ("a stray line\n", "expected one of"),
    ],
)
def test_a_malformed_entry_is_refused(text: str, message: str) -> None:
    """An entry that cannot be matched must not pass as an empty list."""
    with pytest.raises(ValueError, match=message):
        mutate_changed.read_equivalents(text)


def test_a_printed_survivor_round_trips_once_it_has_a_reason() -> None:
    """The job prints the block a reviewer pastes; without a reason it fails."""
    mutant = Mutant("scripts/example.py", 4, 4, "if a == b:", "    if a != b:\n")

    listed = mutate_changed.read_equivalents(mutant.entry("equivalent; a is b"))

    assert listed == {mutant.key}
    with pytest.raises(ValueError, match="no reason"):
        mutate_changed.read_equivalents(mutant.entry())


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------

_LISTED = Mutant("scripts/example.py", 1, 1, "ok = a and b", "ok = a or b\n")
_NEW = Mutant("scripts/example.py", 2, 2, "n = 1", "n = 2\n")


def test_only_unlisted_survivors_count() -> None:
    """A listed survivor is a reviewer's decision, not a finding."""
    assert mutate_changed.unlisted([_LISTED, _NEW], {_LISTED.key}) == [_NEW]


def test_an_unlisted_survivor_fails_the_run_and_is_printed_as_an_entry(
    capsys: pytest.CaptureFixture,
) -> None:
    """The output is the block to paste, so a reviewer can act on it."""
    assert mutate_changed.report([_LISTED, _NEW], {_LISTED.key}) == 1

    output = capsys.readouterr().out
    assert _NEW.entry() in output
    assert _LISTED.entry() not in output
    assert "1 survivor(s) are listed" in output


def test_listed_survivors_alone_pass_the_run(capsys: pytest.CaptureFixture) -> None:
    """Every survivor accounted for is a green job."""
    assert mutate_changed.report([_LISTED], {_LISTED.key}) == 0
    assert mutate_changed.report([], set()) == 0


def test_a_file_no_test_loads_has_every_mutant_survive(
    capsys: pytest.CaptureFixture,
) -> None:
    """With no test to run, nothing is copied or started."""
    plan = [("scripts/example.py", [_NEW], [])]

    assert mutate_changed.run(plan, jobs=2) == [_NEW]
    assert "no test file loads it" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


def test_nothing_changed_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A pull request that touches no mutated Python passes at once."""
    monkeypatch.setattr(mutate_changed, "changed_python_lines", lambda base: {})

    assert mutate_changed.main(["--base", "origin/master"]) == 0
    assert "No Python lines changed" in capsys.readouterr().out


def test_a_git_failure_cannot_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """A base that is not in the clone is an error, not an empty diff."""

    def fail(base: str) -> dict:
        raise subprocess.CalledProcessError(128, ["git"], stderr="fatal: bad ref")

    monkeypatch.setattr(mutate_changed, "changed_python_lines", fail)

    assert mutate_changed.main([]) == 2


def _example_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, equivalents: str
) -> None:
    """Point the script at a checkout with one changed line and one test."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts" / "example.py").write_text("ok = a and b\n", "utf-8")
    (tmp_path / "tests" / "test_example.py").write_text('"example.py"\n', "utf-8")
    (tmp_path / "equivalents.txt").write_text(equivalents, encoding="utf-8")
    monkeypatch.setattr(mutate_changed, "ROOT", tmp_path)
    monkeypatch.setattr(mutate_changed, "EQUIVALENTS", tmp_path / "equivalents.txt")
    monkeypatch.setattr(
        mutate_changed, "changed_python_lines", lambda base: {"scripts/example.py": {1}}
    )


def test_list_prints_the_plan_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`--list` is the dry run: the mutants and the tests, no verdict."""
    _example_checkout(tmp_path, monkeypatch, "")

    def refuse(plan: list, jobs: int) -> list:
        raise AssertionError("--list ran the mutants")

    monkeypatch.setattr(mutate_changed, "run", refuse)

    assert mutate_changed.main(["--list"]) == 0
    output = capsys.readouterr().out
    assert "scripts/example.py: 3 mutant(s); tests: tests/test_example.py" in output
    assert "1: ok = a and b  ->  ok = a or b" in output


def test_the_run_fails_on_a_survivor_the_equivalents_do_not_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan reaches `run`, and its survivors reach the verdict."""
    _example_checkout(tmp_path, monkeypatch, "")
    seen = []

    def survive(plan: list, jobs: int) -> list:
        seen.extend(plan)
        return [mutant for _, found, _ in plan for mutant in found]

    monkeypatch.setattr(mutate_changed, "run", survive)

    assert mutate_changed.main(["--jobs", "1"]) == 1
    ((path, found, tests),) = seen
    assert path == "scripts/example.py"
    assert len(found) == 3
    assert tests == ["tests/test_example.py"]


def test_the_run_passes_when_every_survivor_is_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same survivor, listed with a reason, is a green job."""
    _example_checkout(tmp_path, monkeypatch, _LISTED.entry("equivalent here") + "\n")
    monkeypatch.setattr(mutate_changed, "run", lambda plan, jobs: [_LISTED])

    assert mutate_changed.main([]) == 0


def test_a_malformed_equivalents_file_cannot_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A list the script cannot read is an error, not an empty list."""
    _example_checkout(tmp_path, monkeypatch, "file: scripts/example.py\n")
    monkeypatch.setattr(mutate_changed, "run", lambda plan, jobs: [])

    assert mutate_changed.main([]) == 2
