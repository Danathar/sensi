"""Every section of the documentation pages has a row in `docs/section-map.txt`.

The pages here keep stating facts about the automation - what a workflow
checks, what `.claude/settings.json` allows and denies, what the test wrapper
refuses - and a section goes stale when the machine changes and no test reads
it. Each time that was found, it was fixed by its own join test. What made it
keep happening is that leaving a section unread was the silent default:
nothing told the author of a new section that no test would ever open it.

This module removes the default. `docs/section-map.txt` lists every tracked
page and, under it, every `##` and `###` heading with one of two lines:
`joined:` and the test that reads that section, or `prose:` and why no test
does. A new, renamed or deleted section fails here until the map says what
it is, so an unchecked section of machine claims is a visible
`prose: unchecked:` line in a reviewed diff rather than nothing at all.

It does not prove any sentence true. A `joined:` row is checked only for the
test existing under `tests/` and opening the page in code rather than in a
docstring or comment; whether it reads that section is the reviewer's call,
made once, when the row is written.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_MAP = _ROOT / "docs" / "section-map.txt"
_MAP_RELATIVE = _MAP.relative_to(_ROOT).as_posix()
_THIS_TEST = Path(__file__).resolve().relative_to(_ROOT).as_posix()

# Root-level pages and docs/ without its subdirectories, plus the two docs/
# indexes that describe tooling and the Copilot brief. docs/reflections/ is a
# dated record, and .claude/ and .github/prompts/ are procedures read by
# tests/test_slash_commands.py and tests/test_prompt_procedures.py.
_TRACKED_PAGE = re.compile(r"^(?:[^/]+|docs/[^/]+)\.md$")
_EXTRA_PAGES = {
    ".github/copilot-instructions.md",
    "docs/agent-tasks/README.md",
    "docs/metrics/README.md",
}

_HEADING = re.compile(r"^(#{2,3}) +(.+?)(?: +#+)? *$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_PAGE_LINE = re.compile(r"^\[(?P<page>[^\]]+)\]$")
_ROW_LINE = re.compile(r"^  (?P<kind>joined|prose): *(?P<value>.*)$")

Rows = dict[str, dict[str, tuple[str, str]]]


def _tracked_pages() -> set[str]:
    listing = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z", "--", "*.md"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = {name for name in listing.split("\0") if name}
    tracked = {name for name in names if _TRACKED_PAGE.match(name)}
    return tracked | (_EXTRA_PAGES & names)


def _headings(text: str) -> list[str]:
    """Return the `##`/`###` headings outside fenced blocks, as `## Text`."""
    found = []
    fence = ""
    for line in text.splitlines():
        opened = _FENCE.match(line)
        if opened:
            marker = opened.group(1)
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        match = _HEADING.match(line)
        if match:
            found.append(f"{match.group(1)} {match.group(2)}")
    return found


def _parse_map(text: str) -> Rows:
    """Return page -> heading -> (kind, value); raise on a line it cannot place."""
    pages: Rows = {}
    page = None
    heading = None
    for number, line in enumerate(text.splitlines(), start=1):
        where = f"{_MAP_RELATIVE}:{number}"
        if not line.strip() or (line.startswith("#") and not _HEADING.match(line)):
            continue
        if heading is not None and not _ROW_LINE.match(line):
            raise AssertionError(f"{where}: {heading!r} has no joined:/prose: line")
        if match := _PAGE_LINE.match(line):
            page = match.group("page")
            if page in pages:
                raise AssertionError(f"{where}: [{page}] is listed twice")
            pages[page] = {}
        elif _HEADING.match(line):
            if page is None:
                raise AssertionError(f"{where}: heading before any [page] line")
            if line in pages[page]:
                raise AssertionError(f"{where}: {line!r} is listed twice")
            heading = line
        elif match := _ROW_LINE.match(line):
            if heading is None:
                raise AssertionError(f"{where}: classification with no heading")
            value = match.group("value").strip()
            if not value:
                raise AssertionError(f"{where}: {match.group('kind')}: needs a value")
            pages[page][heading] = (match.group("kind"), value)
            heading = None
        else:
            raise AssertionError(f"{where}: cannot place {line!r}")
    if heading is not None:
        raise AssertionError(f"{_MAP_RELATIVE}: {heading!r} has no joined:/prose: line")
    return pages


def _docstrings(tree: ast.AST) -> set[int]:
    """Return the ids of the string nodes that are docstrings."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                found.add(id(body[0].value))
    return found


def _opens_page(test_text: str, page: str) -> bool:
    """Whether a test names the page in code, not just in a docstring or comment.

    Tests spell a path whole (`_ROOT / "docs/quality.md"`) or in parts
    (`_ROOT / "docs" / "quality.md"`), so the file name is what is looked for.
    A `README.md` below the root is only told apart from the root one by its
    directory, so that has to be named too.
    """
    path = Path(page)
    needles = [path.name]
    if path.name == "README.md" and path.parent != Path():
        needles.append(path.parent.name)
    tree = ast.parse(test_text)
    skipped = _docstrings(tree)
    strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skipped
    ]
    return all(any(needle in value for value in strings) for needle in needles)


def _page_mismatch(rows: Rows, pages: set[str]) -> tuple[list[str], list[str]]:
    """Return (tracked pages with no block, blocks for pages not tracked)."""
    return sorted(pages - set(rows)), sorted(set(rows) - pages)


def _unclassified(rows: Rows, root: Path) -> dict[str, list[str]]:
    """Return page -> headings it has that the map gives no row."""
    found = {}
    for page, page_rows in sorted(rows.items()):
        text = (root / page).read_text(encoding="utf-8")
        missing = [h for h in _headings(text) if h not in page_rows]
        if missing:
            found[page] = missing
    return found


def _stale(rows: Rows, root: Path) -> dict[str, list[str]]:
    """Return page -> rows naming headings the page no longer has."""
    found = {}
    for page, page_rows in sorted(rows.items()):
        text = (root / page).read_text(encoding="utf-8")
        gone = sorted(set(page_rows) - set(_headings(text)))
        if gone:
            found[page] = gone
    return found


def _bad_join(page: str, heading: str, test: str, root: Path) -> str | None:
    """Return why a `joined:` row does not hold, or None when it does."""
    if test == _THIS_TEST:
        return "this test reads headings, not what a section says"
    path = root / test
    if not (test.startswith("tests/") and test.endswith(".py") and path.is_file()):
        return f"{page} {heading!r} is joined to {test}, not a test file under tests/"
    if not _opens_page(path.read_text(encoding="utf-8"), page):
        return f"{page} {heading!r} is joined to {test}, which never opens {page}"
    return None


# --- the readers, on inputs built for them ------------------------------------


def test_headings_reads_both_levels_and_nothing_else() -> None:
    """`##` and `###` only; `#`, `####` and a missing space are not headings."""
    text = "# Title\n## One\ntext\n### Two\n#### Three\n##No space\n"
    assert _headings(text) == ["## One", "### Two"]


def test_headings_skips_fenced_blocks() -> None:
    """A `#` line inside a fence is a shell comment, not a heading."""
    text = "## Out\n```bash\n## no\n```\n~~~~\n### nor\n```\n~~~~\n### Back\n"
    assert _headings(text) == ["## Out", "### Back"]


def test_a_fence_closes_only_on_its_own_marker_at_least_as_long() -> None:
    """A shorter or different fence marker inside a fence does not close it."""
    assert _headings("````\n```\n## in\n````\n## Out\n") == ["## Out"]
    assert _headings("```\n~~~\n## in\n```\n## Out\n") == ["## Out"]


def test_headings_drops_a_closing_hash_sequence() -> None:
    """`## Closed ##` is the heading `Closed`."""
    assert _headings("## Closed ##\n") == ["## Closed"]


def test_the_map_reader_reads_rows_and_skips_comments() -> None:
    """Pages, headings and both kinds of row are read; `#` comments are not."""
    text = (
        "# comment\n\n[a.md]\n## One\n  joined: tests/x.py\n"
        "### Two\n  prose: narrative: why\n\n[b.md]\n"
    )
    assert _parse_map(text) == {
        "a.md": {
            "## One": ("joined", "tests/x.py"),
            "### Two": ("prose", "narrative: why"),
        },
        "b.md": {},
    }


@pytest.mark.parametrize(
    "text",
    [
        "[a.md]\n## One\n## Two\n  prose: x\n",
        "[a.md]\n## One\n",
        "[a.md]\n## One\n  prose:\n",
        "[a.md]\n[a.md]\n",
        "[a.md]\n## One\n  prose: x\n## One\n  prose: y\n",
        "## One\n  prose: x\n",
        "[a.md]\n  prose: x\n",
        "[a.md]\n## One\njoined: tests/x.py\n",
    ],
)
def test_the_map_reader_refuses_a_line_it_cannot_place(text: str) -> None:
    """Unclassified, empty, duplicate or orphaned lines are errors, not skips."""
    with pytest.raises(AssertionError):
        _parse_map(text)


def test_a_path_in_code_opens_the_page() -> None:
    """Both spellings of a path count."""
    for text in (
        'DOC = ROOT / "docs/quality.md"\n',
        'DOC = ROOT / "docs" / "quality.md"\n',
    ):
        assert _opens_page(text, "docs/quality.md"), text


def test_a_docstring_or_comment_does_not_open_the_page() -> None:
    """Naming the page in prose is not reading it."""
    text = '"""Reads docs/quality.md."""\n# docs/quality.md\ndef f():\n    """quality.md"""\n'
    assert not _opens_page(text, "docs/quality.md")


def test_a_nested_readme_needs_its_directory_named() -> None:
    """`README.md` alone opens the root page, not `docs/metrics/README.md`."""
    assert _opens_page('DOC = "README.md"\n', "README.md")
    assert not _opens_page('DOC = "README.md"\n', "docs/metrics/README.md")
    assert _opens_page(
        'DOC = ROOT / "docs" / "metrics" / "README.md"\n', "docs/metrics/README.md"
    )


# --- the checks, each on a small tree built for it ----------------------------


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Return a repository with one page, a test that reads it, and a stray."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "page.md").write_text("# Page\n## Kept\n## New\n")
    (tmp_path / "tests" / "test_reads.py").write_text('DOC = "page.md"\n')
    (tmp_path / "elsewhere.py").write_text('DOC = "page.md"\n')
    return tmp_path


def test_a_heading_with_no_row_is_reported(tree: Path) -> None:
    """A section added without a row fails."""
    rows = {"page.md": {"## Kept": ("prose", "x")}}
    assert _unclassified(rows, tree) == {"page.md": ["## New"]}


def test_a_row_for_a_missing_heading_is_reported(tree: Path) -> None:
    """A section renamed or deleted under its row fails."""
    rows = {
        "page.md": {
            "## Kept": ("prose", "x"),
            "## New": ("prose", "x"),
            "## Gone": ("prose", "x"),
        }
    }
    assert _stale(rows, tree) == {"page.md": ["## Gone"]}
    assert _unclassified(rows, tree) == {}


def test_a_join_outside_tests_or_to_this_test_is_refused(tree: Path) -> None:
    """Only a test file can be joined, and not the one that reads headings."""
    assert _bad_join("page.md", "## Kept", "tests/test_reads.py", tree) is None
    for test in ("elsewhere.py", "tests/absent.py"):
        assert "not a test file" in _bad_join("page.md", "## Kept", test, tree)
    assert "reads headings" in _bad_join("page.md", "## Kept", _THIS_TEST, tree)


def test_a_join_to_a_test_that_never_opens_the_page_is_refused(tree: Path) -> None:
    """A test that names the page only in its docstring does not count."""
    (tree / "tests" / "test_other.py").write_text(
        '"""Reads page.md."""\nDOC = "x.md"\n'
    )
    reason = _bad_join("page.md", "## Kept", "tests/test_other.py", tree)
    assert "never opens" in reason


def test_the_page_lists_compare_both_ways() -> None:
    """A page missing from the map and a map block for no page both show."""
    rows = {"a.md": {}, "b.md": {}}
    assert _page_mismatch(rows, {"a.md", "c.md"}) == (["c.md"], ["b.md"])


# --- the committed map --------------------------------------------------------


@pytest.fixture(scope="module")
def rows() -> Rows:
    """Return the committed map."""
    return _parse_map(_MAP.read_text(encoding="utf-8"))


def test_the_map_lists_exactly_the_tracked_pages(rows: Rows) -> None:
    """Every tracked page has a block, and every block is a tracked page."""
    unlisted, untracked = _page_mismatch(rows, _tracked_pages())
    assert unlisted == [], (
        f"tracked pages with no [page] block in {_MAP_RELATIVE}; "
        "add one with a row per heading"
    )
    assert untracked == [], f"{_MAP_RELATIVE} lists pages that are not tracked"


def test_every_heading_has_a_row(rows: Rows) -> None:
    """A new section is classified in the change that adds it."""
    assert _unclassified(rows, _ROOT) == {}, (
        f"sections {_MAP_RELATIVE} does not classify; give each a joined: line "
        "naming the test that reads it, or a prose: line saying why none does"
    )


def test_every_row_names_a_heading_the_page_has(rows: Rows) -> None:
    """A renamed or deleted section takes its row with it."""
    assert _stale(rows, _ROOT) == {}, (
        f"{_MAP_RELATIVE} classifies headings the page no longer has; "
        "rename or drop the rows"
    )


def test_every_joined_test_exists_and_opens_the_page(rows: Rows) -> None:
    """A `joined:` row names a test under tests/ that opens the page in code."""
    bad = [
        reason
        for page, page_rows in sorted(rows.items())
        for heading, (kind, test) in page_rows.items()
        if kind == "joined" and (reason := _bad_join(page, heading, test, _ROOT))
    ]
    assert len([1 for p in rows.values() for k, _ in p.values() if k == "joined"]) > 0
    assert bad == []


def test_every_prose_row_says_which_kind_it_is(rows: Rows) -> None:
    """`prose:` is `unchecked:` (machine claims nothing reads) or `narrative:`."""
    bad = [
        f"{page} {heading!r}: {value!r}"
        for page, page_rows in sorted(rows.items())
        for heading, (kind, value) in page_rows.items()
        if kind == "prose" and not value.startswith(("unchecked: ", "narrative: "))
    ]
    assert bad == []
