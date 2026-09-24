"""Tests for `docs/metrics/README.md`, the prose that models `scripts/pr_metrics.py`.

The script itself is pinned by `tests/test_pr_metrics.py`. This module pins the
*document*, which is a hand-written copy of the script's interface: four
invocations, a table naming and defining every reported column, the author
buckets, a claim about which gates review this repository, and a baseline
table that is literally the script's own rendered output pasted in.

Nothing joined the two ends. `test_instruction_docs.py` reads this file for its
relative links, for the backticked paths it names, and for the one copy of the
coverage-gate percentage it carries - so a renamed flag, a renamed column, a
reordered author split, or a baseline table in a shape the renderer no longer
produces all leave the suite green while the document describes a tool that no
longer exists.

Every assertion below is driven by text parsed out of the document, so the
failure is always "the doc says X and the machine does Y", never a second copy
of the doc living in a test.
"""

import ast
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sys

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_DOC = _ROOT / "docs" / "metrics" / "README.md"
_SCRIPT = _ROOT / "scripts" / "pr_metrics.py"
_RULESET = _ROOT / ".github" / "rulesets" / "master.json"
_COVERAGE_GATE = _ROOT / ".github" / "workflows" / "coverage-gate.yml"

_spec = importlib.util.spec_from_file_location("pr_metrics_for_doc", _SCRIPT)
pr_metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_metrics)

_TEXT = _DOC.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Reading the document
# --------------------------------------------------------------------------


def _section(title: str) -> str:
    """Return the body of the `## <title>` section, without its heading."""
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", _TEXT, re.M | re.S)
    assert match, f"docs/metrics/README.md has no '## {title}' section"
    body = match.group(1)
    assert body.strip(), f"docs/metrics/README.md's '## {title}' section is empty"
    return body


def _fence(body: str, info: str) -> str:
    """Return the single ```<info> fenced block inside a section body."""
    blocks = re.findall(rf"^```{info}\n(.*?)^```$", body, re.M | re.S)
    assert len(blocks) == 1, (
        f"expected exactly one ```{info} block in the section, found {len(blocks)}"
    )
    return blocks[0]


def _table(body: str) -> tuple[list[str], list[list[str]]]:
    """Return the (header cells, data rows) of the single table in a body."""
    lines = [line for line in body.splitlines() if line.strip().startswith("|")]
    assert len(lines) >= 3, "expected a markdown table with at least one row"

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    header = cells(lines[0])
    assert all(set(cell) <= set("-: ") for cell in cells(lines[1])), (
        "the second line of a markdown table must be its alignment row"
    )
    return header, [cells(line) for line in lines[2:]]


def _normalised(text: str) -> str:
    """Return text with every run of whitespace collapsed to one space."""
    return " ".join(text.split())


def _required_contexts() -> list[str]:
    """Return the status checks `master.json` requires on the default branch."""
    ruleset = json.loads(_RULESET.read_text(encoding="utf-8"))
    for rule in ruleset["rules"]:
        if rule["type"] == "required_status_checks":
            return [
                check["context"]
                for check in rule["parameters"]["required_status_checks"]
            ]
    raise AssertionError(".github/rulesets/master.json has no required checks")


def _min_coverage() -> str:
    """Return MIN_COVERAGE as `coverage-gate.yml` sets it."""
    match = re.search(
        r"^\s*MIN_COVERAGE:\s*(\d+)\s*$",
        _COVERAGE_GATE.read_text(encoding="utf-8"),
        re.M,
    )
    assert match, "coverage-gate.yml no longer sets MIN_COVERAGE"
    return match.group(1)


# --------------------------------------------------------------------------
# Building pull requests to push through the script
# --------------------------------------------------------------------------

_CREATED = "2026-01-01T00:00:00Z"


def _pull(
    number: int,
    *,
    login: str = "someone",
    is_bot: bool = False,
    created: str = _CREATED,
    hours: float | None = None,
    reviews: int = 0,
    lines: int = 0,
) -> dict:
    """Return one pull request in the shape `gh pr list --json` emits."""
    merged = None
    if hours is not None:
        seconds = round(hours * 3600)
        minute, second = divmod(seconds, 60)
        hour, minute = divmod(minute, 60)
        merged = f"2026-01-01T{hour:02d}:{minute:02d}:{second:02d}Z"
    return {
        "number": number,
        "title": f"PR {number}",
        "state": "MERGED" if merged else "CLOSED",
        "author": {"login": login, "is_bot": is_bot},
        "createdAt": created,
        "closedAt": merged or created,
        "mergedAt": merged,
        "additions": lines,
        "deletions": 0,
        "changedFiles": 1,
        "reviews": [{"state": "APPROVED"} for _ in range(reviews)],
        "comments": [],
    }


def _run_documented(
    monkeypatch, argv: list[str], pulls: list[dict]
) -> list[tuple[int, str | None]]:
    """Run `main()` on a documented argv and return what `fetch` was asked for."""
    captured: list[tuple[int, str | None]] = []

    def fake_fetch(limit: int, repo: str | None) -> list[dict]:
        captured.append((limit, repo))
        return pulls

    monkeypatch.setattr(pr_metrics, "fetch", fake_fetch)
    monkeypatch.setattr(pr_metrics.sys, "argv", ["pr_metrics.py", *argv])
    assert pr_metrics.main() == 0
    return captured


# --------------------------------------------------------------------------
# "How to get the numbers" - the documented invocations
# --------------------------------------------------------------------------

_HOW_TO = _section("How to get the numbers")


def _documented_commands() -> list[tuple[list[str], str]]:
    """Return (argv, trailing comment) for every line of the bash fence."""
    commands = []
    for line in _fence(_HOW_TO, "bash").splitlines():
        if not line.strip():
            continue
        code, _, comment = line.partition("#")
        commands.append((shlex.split(code), comment.strip()))
    assert commands, "docs/metrics/README.md documents no commands"
    return commands


_COMMANDS = _documented_commands()


@pytest.mark.parametrize(
    "argv", [argv for argv, _ in _COMMANDS], ids=lambda argv: " ".join(argv[1:])
)
def test_every_documented_command_invokes_the_committed_script(argv) -> None:
    """Each line runs the script at the path the document spells."""
    assert argv[0] == "python3", f"documented command does not use python3: {argv}"
    script = _ROOT / argv[1]
    assert script == _SCRIPT and script.is_file(), (
        f"docs/metrics/README.md documents {argv[1]!r}, which is not "
        f"{_SCRIPT.relative_to(_ROOT)}"
    )


@pytest.mark.parametrize(
    "argv", [argv for argv, _ in _COMMANDS], ids=lambda argv: " ".join(argv[2:])
)
def test_every_documented_command_is_accepted_by_the_script(monkeypatch, argv) -> None:
    """A documented flag the parser does not define exits 2, not 0.

    `argparse` is the whole reason this is checkable: a renamed or removed
    option makes the documented line die at the boundary, and nothing else in
    the suite runs these particular argument lists.
    """
    _run_documented(monkeypatch, argv[2:], [_pull(1, hours=1.0)])


def test_the_bare_command_fetches_the_number_of_pulls_its_comment_claims(
    monkeypatch, capsys
) -> None:
    """`# last 50 closed PRs` is the parser's default, not a remembered number."""
    argv, comment = _COMMANDS[0]
    assert argv[2:] == [], "the first documented command is meant to take no flags"

    documented = re.search(r"\b(\d+)\b", comment)
    assert documented, f"the bare command's comment states no count: {comment!r}"

    calls = _run_documented(monkeypatch, [], [_pull(1, hours=1.0)])
    capsys.readouterr()

    assert calls == [(int(documented.group(1)), None)], (
        f"docs/metrics/README.md says the default is {documented.group(1)}; the script "
        f"asked for {calls}"
    )


def test_the_bare_commands_comment_describes_closed_pulls(monkeypatch) -> None:
    """`closed PRs` is a claim about the `gh` query, so read the argv."""
    _, comment = _COMMANDS[0]
    assert "closed" in comment.lower(), (
        f"the bare command's comment no longer says which pulls it reads: {comment!r}"
    )

    recorded: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(command, **_):
        recorded.append(command)
        return _Result()

    monkeypatch.setattr(pr_metrics.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(pr_metrics.subprocess, "run", fake_run)
    pr_metrics.fetch(50, None)

    assert len(recorded) == 1, recorded
    argv = recorded[0]
    assert argv[:3] == ["gh", "pr", "list"], argv
    assert argv[argv.index("--state") + 1] == "closed", (
        f"docs/metrics/README.md says these are closed PRs; {argv} asks for something else"
    )


def test_the_documented_limit_is_the_limit_the_script_requests(monkeypatch) -> None:
    """`--limit 200` must reach `fetch` as 200, whatever number the doc uses."""
    argv = next(argv for argv, _ in _COMMANDS if "--limit" in argv)
    documented = int(argv[argv.index("--limit") + 1])

    calls = _run_documented(monkeypatch, argv[2:], [_pull(1, hours=1.0)])

    assert calls == [(documented, None)]


def test_the_documented_since_selects_by_the_date_a_pull_was_opened(
    monkeypatch, capsys
) -> None:
    """The "window" the column table names is the open date, not the close date."""
    argv = next(argv for argv, _ in _COMMANDS if "--since" in argv)
    cutoff = argv[argv.index("--since") + 1]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff), cutoff

    day_before = f"{int(cutoff[:4]) - 1}{cutoff[4:]}T23:00:00Z"
    # Opened before the cutoff and closed well inside it: selecting on the
    # close date would pull it back into a window it does not belong to.
    stale = _pull(2, created=day_before)
    stale["closedAt"] = f"{int(cutoff[:4]) + 1}{cutoff[4:]}T00:00:00Z"
    _run_documented(
        monkeypatch,
        argv[2:],
        [_pull(1, created=f"{cutoff}T00:00:00Z", hours=1.0), stale],
    )
    out = capsys.readouterr().out

    assert out.splitlines()[0] == f"Pull requests considered since {cutoff}: 1", out


def test_the_documented_json_flag_emits_something_pipeable(monkeypatch, capsys) -> None:
    """`# for piping somewhere` is only true if the output parses as JSON."""
    argv, comment = next(
        (argv, comment) for argv, comment in _COMMANDS if "--json" in argv
    )
    assert comment, "the --json line lost the comment that says what it is for"

    _run_documented(monkeypatch, argv[2:], [_pull(1, hours=1.0, lines=5)])
    out = capsys.readouterr().out

    payload = json.loads(out)
    assert payload["buckets"]["all"]["proposed"] == 1
    assert "| Author |" not in out, "--json still emitted the Markdown table"


# --------------------------------------------------------------------------
# "Reads through `gh` [...] No third-party packages."
# --------------------------------------------------------------------------

_TOOLING_CLAIM = _normalised(_HOW_TO.rsplit("```", 1)[1])


def test_the_document_still_makes_the_tooling_claim() -> None:
    """The two checks below are worthless if the sentence they check is gone."""
    assert "`gh auth status`" in _TOOLING_CLAIM, _TOOLING_CLAIM
    assert "No third-party packages" in _TOOLING_CLAIM, _TOOLING_CLAIM


def test_the_script_imports_only_the_standard_library() -> None:
    """Prove the "No third-party packages" claim from the script's own imports."""
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert imported, "the import scan found nothing, so it proves nothing"
    outside = sorted(imported - set(sys.stdlib_module_names))
    assert not outside, (
        f"docs/metrics/README.md says {_SCRIPT.name} needs no third-party packages, but "
        f"it imports {outside}"
    )


def test_the_script_carries_no_credential_of_its_own() -> None:
    """Show the script hands `gh` nothing, as "whatever `gh auth status` reports".

    A token read from the environment and passed through would make the
    reported numbers depend on something other than the operator's own `gh`
    login, which is the one thing the sentence promises.
    """
    source = _SCRIPT.read_text(encoding="utf-8")
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "Authorization"):
        assert name not in source, f"{_SCRIPT.name} names {name}"

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ):
            keywords = {keyword.arg for keyword in node.keywords}
            assert "env" not in keywords, (
                f"{_SCRIPT.name} overrides the environment it runs `gh` in"
            )


# --------------------------------------------------------------------------
# "What each column means"
# --------------------------------------------------------------------------

_COLUMNS_SECTION = _section("What each column means")
_COLUMN_HEADER, _COLUMN_ROWS = _table(_COLUMNS_SECTION)
_DOCUMENTED_COLUMNS = [
    re.fullmatch(r"\*\*(.+)\*\*", row[0]).group(1)
    for row in _COLUMN_ROWS
    if re.fullmatch(r"\*\*(.+)\*\*", row[0])
]


def _rendered(report: dict) -> list[list[str]]:
    """Return `render()`'s table as header + data rows of stripped cells."""
    lines = [
        line for line in pr_metrics.render(report).splitlines() if line.startswith("|")
    ]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]


def _report(pulls: list[dict], since: str | None = None) -> dict:
    """Return the script's own summary of a set of pull requests."""
    return pr_metrics.summarise(pulls, since)


def test_the_column_table_defines_a_column_for_every_rendered_one() -> None:
    """The table is the definition of the output, so it must match it exactly."""
    assert len(_DOCUMENTED_COLUMNS) == len(_COLUMN_ROWS), (
        "every row of the column table must name its column in bold; "
        f"{len(_COLUMN_ROWS) - len(_DOCUMENTED_COLUMNS)} row(s) do not"
    )
    assert _COLUMN_HEADER == ["Column", "Definition", "What a bad value looks like"]

    rendered_header = _rendered(_report([_pull(1, hours=1.0)]))[0]
    assert rendered_header[0] == "Author", rendered_header
    assert rendered_header[1:] == _DOCUMENTED_COLUMNS, (
        "docs/metrics/README.md defines columns "
        f"{_DOCUMENTED_COLUMNS} but the table pr_metrics.py renders has "
        f"{rendered_header[1:]}"
    )


# Each documented column is given meaning by the named test. A column with no
# entry here - a new one, or a renamed one - fails rather than arriving with a
# definition nothing checks.
_COLUMN_MEANING_TESTS = {
    "Proposed": "test_proposed_counts_every_closed_pull_in_the_window",
    "Merged": "test_merged_and_closed_unmerged_partition_the_proposed_pulls",
    "Closed unmerged": "test_merged_and_closed_unmerged_partition_the_proposed_pulls",
    "Acceptance": "test_acceptance_is_merged_over_proposed",
    "Median h to merge": "test_median_hours_measures_open_to_merge_of_merged_pulls",
    "Median reviews": "test_median_reviews_counts_submissions_on_merged_pulls",
    "Median lines": "test_median_lines_is_additions_plus_deletions_of_merged_pulls",
}


@pytest.mark.parametrize("column", _DOCUMENTED_COLUMNS)
def test_every_documented_column_has_a_test_giving_it_meaning(column: str) -> None:
    """A definition in prose is only a definition if something enforces it."""
    assert column in _COLUMN_MEANING_TESTS, (
        f"docs/metrics/README.md defines the column {column!r}, which no test in "
        f"{Path(__file__).name} joins to pr_metrics.py"
    )
    assert _COLUMN_MEANING_TESTS[column] in globals(), (
        f"{_COLUMN_MEANING_TESTS[column]} is named as the test for {column!r} "
        "but does not exist"
    )


def _definition(column: str) -> str:
    """Return the Definition cell docs/metrics/README.md gives a column."""
    for row in _COLUMN_ROWS:
        if row[0] == f"**{column}**":
            return _normalised(row[1])
    raise AssertionError(f"docs/metrics/README.md has no row for the column {column!r}")


# Three merged and two abandoned, with the unmerged pair carrying the largest
# churn and the longest life so that any median leaking them in moves.
_MIXED = [
    _pull(1, hours=1.0, reviews=0, lines=10),
    _pull(2, hours=2.0, reviews=1, lines=100),
    _pull(3, hours=6.0, reviews=5, lines=1000),
    _pull(4, reviews=9, lines=9999),
    _pull(5, reviews=9, lines=9999),
]


def test_proposed_counts_every_closed_pull_in_the_window() -> None:
    """Pin "closed PRs in the window": merged or not, and only inside `--since`."""
    assert _definition("Proposed") == "closed PRs in the window"

    assert _report(_MIXED)["buckets"]["all"]["proposed"] == len(_MIXED)
    assert _report(_MIXED)["total_considered"] == len(_MIXED)

    windowed = _report(
        [*_MIXED, _pull(6, created="2025-06-01T00:00:00Z")], "2026-01-01"
    )
    assert windowed["buckets"]["all"]["proposed"] == len(_MIXED)


def test_merged_and_closed_unmerged_partition_the_proposed_pulls() -> None:
    """Pin "of those, merged" and "of those, abandoned or rejected", nothing else."""
    assert _definition("Merged") == "of those, merged"
    assert _definition("Closed unmerged") == "of those, abandoned or rejected"

    bucket = _report(_MIXED)["buckets"]["all"]
    assert bucket["merged"] == 3
    assert bucket["closed_unmerged"] == 2
    assert bucket["merged"] + bucket["closed_unmerged"] == bucket["proposed"]

    # A pull GitHub still labels MERGED but never merged is not merged.
    stale = _pull(7)
    stale["state"] = "MERGED"
    assert _report([stale])["buckets"]["all"]["merged"] == 0


def test_acceptance_is_merged_over_proposed() -> None:
    """Pin "merged / proposed", rendered as a percentage of those two numbers."""
    assert _definition("Acceptance") == "merged / proposed"

    bucket = _report(_MIXED)["buckets"]["all"]
    assert bucket["acceptance_rate"] == bucket["merged"] / bucket["proposed"]

    rows = _rendered(_report(_MIXED))
    acceptance = rows[0].index("Acceptance")
    assert rows[2][acceptance] == "60%"


def test_median_hours_measures_open_to_merge_of_merged_pulls() -> None:
    """Pin "open → merge, hours": createdAt to mergedAt, where both exist."""
    assert _definition("Median h to merge") == "open → merge, hours"

    bucket = _report(_MIXED)["buckets"]["all"]
    assert bucket["median_hours_to_merge"] == 2.0
    assert bucket["max_hours_to_merge"] == 6.0

    # "open → merge" names the merge, not the close: a merged pull whose
    # recorded close differs from its merge is aged by the merge.
    merged_later = _pull(11, hours=4.0)
    merged_later["closedAt"] = "2026-03-01T00:00:00Z"
    assert _report([merged_later])["buckets"]["all"]["median_hours_to_merge"] == 4.0

    # Closing a pull without merging it never contributes an age, however long
    # it stayed open.
    abandoned = _pull(8)
    abandoned["closedAt"] = "2026-12-31T00:00:00Z"
    assert (
        _report([*_MIXED, abandoned])["buckets"]["all"]["median_hours_to_merge"] == 2.0
    )


def test_median_reviews_counts_submissions_on_merged_pulls() -> None:
    """Pin "review submissions before merge": the length of the reviews list."""
    assert _definition("Median reviews") == "review submissions before merge"

    bucket = _report(_MIXED)["buckets"]["all"]
    assert bucket["median_reviews_before_merge"] == 1

    unreviewed = _report([_pull(9, hours=1.0)])["buckets"]["all"]
    assert unreviewed["median_reviews_before_merge"] == 0


def test_median_lines_is_additions_plus_deletions_of_merged_pulls() -> None:
    """Pin "additions + deletions of merged PRs": both halves, merged only."""
    assert _definition("Median lines") == "additions + deletions of merged PRs"

    bucket = _report(_MIXED)["buckets"]["all"]
    assert bucket["median_lines_changed"] == 100

    split = _pull(10, hours=1.0, lines=40)
    split["deletions"] = 60
    assert _report([split])["buckets"]["all"]["median_lines_changed"] == 100


# --------------------------------------------------------------------------
# "Rows are split by author: `all`, `human`, and `bot`"
# --------------------------------------------------------------------------

_AUTHOR_CLAIM = _normalised(_COLUMNS_SECTION.split("|")[-1])
_DOCUMENTED_BUCKETS = re.findall(r"`([a-z]+)`", _AUTHOR_CLAIM.split(".")[0])


def test_the_author_split_sentence_is_still_there() -> None:
    """The bucket names are read out of this sentence, so it must still say them."""
    assert "Rows are split by author" in _AUTHOR_CLAIM, _AUTHOR_CLAIM
    assert _DOCUMENTED_BUCKETS, f"no bucket names in {_AUTHOR_CLAIM!r}"


def test_the_rendered_rows_are_the_documented_buckets_in_the_documented_order() -> None:
    """A reordered or renamed bucket is a silent change to every reading."""
    pulls = [
        _pull(1, login="a-human", hours=1.0),
        _pull(2, login="dependabot[bot]", hours=3.0),
    ]
    rows = _rendered(_report(pulls))[2:]

    assert [row[0] for row in rows] == _DOCUMENTED_BUCKETS, (
        f"docs/metrics/README.md says the rows are {_DOCUMENTED_BUCKETS}; "
        f"pr_metrics.py renders {[row[0] for row in rows]}"
    )


def test_bot_means_both_spellings_the_document_names() -> None:
    """Pin "GitHub Apps and `[bot]` accounts" as two signals in the payload."""
    assert "GitHub Apps" in _AUTHOR_CLAIM, _AUTHOR_CLAIM
    assert "`[bot]`" in _AUTHOR_CLAIM, _AUTHOR_CLAIM

    app = _pull(1, login="sensi-hive", is_bot=True, hours=1.0)
    suffixed = _pull(2, login="renovate[bot]", is_bot=False, hours=1.0)
    person = _pull(3, login="Danathar", hours=1.0)

    buckets = _report([app, suffixed, person])["buckets"]
    assert buckets["bot"]["proposed"] == 2
    assert buckets["human"]["proposed"] == 1
    assert buckets["all"]["proposed"] == 3


def test_the_all_row_is_the_two_halves_together() -> None:
    """Pin "a single blended acceptance rate hides the thing worth knowing"."""
    assert "blended acceptance rate" in _AUTHOR_CLAIM, _AUTHOR_CLAIM

    buckets = _report(
        [
            _pull(1, login="person", hours=1.0),
            _pull(2, login="bot-account[bot]"),
        ]
    )["buckets"]

    assert buckets["human"]["acceptance_rate"] == 1.0
    assert buckets["bot"]["acceptance_rate"] == 0.0
    assert buckets["all"]["acceptance_rate"] == 0.5


# --------------------------------------------------------------------------
# "Reading it honestly"
# --------------------------------------------------------------------------

_HONESTLY = _section("Reading it honestly")

# How each gate the prose names is spelled in `master.json`. A phrase the
# document adds and this table does not know about fails, rather than being
# quietly accepted as a gate nobody requires.
_GATE_CONTEXTS = {
    "`pytest`": "pytest (Python 3.14)",
    f"the {_min_coverage()}% coverage gate": "line coverage >= threshold",
    "`ruff`": "ruff",
    "hassfest": "hassfest",
    "HACS validation": "HACS",
    "the `manifest.json`/`requirements_component.txt` sync check": (
        "manifest requirements match requirements_component.txt"
    ),
}


def _named_gates() -> list[str]:
    """Return the gates the "Zero reviews" paragraph lists as the only reviewer."""
    match = re.search(
        r"the automated gates — (.+?) — are the only reviewer",
        _normalised(_HONESTLY),
    )
    assert match, (
        "docs/metrics/README.md no longer names the gates that stand in for a reviewer"
    )
    return [
        re.sub(r"^and ", "", item.strip())
        for item in match.group(1).split(",")
        if item.strip()
    ]


def test_a_repository_that_merges_everything_reads_at_one_hundred_percent() -> None:
    """Pin "reads at 100% regardless of how good the work is", the stated caveat."""
    assert "reads at 100%" in _normalised(_HONESTLY)

    rows = _rendered(_report([_pull(n, hours=1.0) for n in range(1, 8)]))
    acceptance = rows[0].index("Acceptance")
    assert rows[2][acceptance] == "100%"


@pytest.mark.parametrize("gate", _named_gates())
def test_every_gate_the_prose_names_is_a_required_check(gate: str) -> None:
    """The prose calls these the only reviewer, so they must actually be required."""
    assert gate in _GATE_CONTEXTS, (
        f"docs/metrics/README.md names the gate {gate!r}, which is not joined to any "
        "context in .github/rulesets/master.json"
    )
    assert _GATE_CONTEXTS[gate] in _required_contexts(), (
        f"docs/metrics/README.md calls {gate!r} a gate, but master.json does not "
        f"require {_GATE_CONTEXTS[gate]!r}"
    )


def test_the_prose_names_every_required_check() -> None:
    """Show "are the only reviewer" is false once a required gate goes unlisted."""
    claimed = {
        _GATE_CONTEXTS[gate] for gate in _named_gates() if gate in _GATE_CONTEXTS
    }
    missing = sorted(set(_required_contexts()) - claimed)

    assert not missing, (
        "docs/metrics/README.md says the automated gates are the only reviewer but "
        f"does not name {missing}"
    )


# --------------------------------------------------------------------------
# "Baseline" - the script's own output, pasted in
# --------------------------------------------------------------------------

_BASELINE = _section("Baseline")
_BASELINE_HEADER, _BASELINE_ROWS = _table(_BASELINE)


def _baseline_pulls() -> list[dict]:
    """Return pull requests that reproduce every non-`all` row of the baseline."""
    pulls: list[dict] = []
    for row in _BASELINE_ROWS:
        author = row[0]
        if author == "all":
            continue
        proposed, merged, unmerged = (int(cell) for cell in row[1:4])
        assert merged + unmerged == proposed, f"baseline row {author} does not add up"

        hours, reviews, lines = (float(row[5]), int(row[6]), int(row[7]))
        for _ in range(merged):
            pulls.append(
                _pull(
                    len(pulls) + 1,
                    login=f"{author}-account[bot]" if author == "bot" else author,
                    is_bot=author == "bot",
                    hours=hours,
                    reviews=reviews,
                    lines=lines,
                )
            )
        for _ in range(unmerged):
            pulls.append(
                _pull(
                    len(pulls) + 1,
                    login=f"{author}-account[bot]" if author == "bot" else author,
                    is_bot=author == "bot",
                )
            )
    assert pulls, "the baseline table describes no pull requests"
    return pulls


def test_the_baseline_table_is_shaped_like_the_renderers_output() -> None:
    """The table is pasted output, so a renamed column makes it a fake."""
    rendered = _rendered(_report([_pull(1, hours=1.0)]))
    assert rendered[0] == _BASELINE_HEADER, (
        f"docs/metrics/README.md's baseline header is {_BASELINE_HEADER}; pr_metrics.py "
        f"renders {rendered[0]}"
    )
    assert [row[0] for row in _BASELINE_ROWS], "the baseline table has no rows"
    assert all(len(row) == len(_BASELINE_HEADER) for row in _BASELINE_ROWS)


def test_the_baseline_numbers_are_output_the_renderer_still_produces() -> None:
    """Every baseline row must come back out of `render()`, `all` row included.

    This is the strongest thing the document can be held to: the table is not a
    description of the output, it *is* the output, so pull requests rebuilt
    from its own cells have to render the same table - including the `all` row,
    which is then a check that the snapshot is internally consistent.
    """
    report = _report(_baseline_pulls())
    rendered = _rendered(report)
    assert rendered[0] == _BASELINE_HEADER

    assert [row[0] for row in rendered[2:]] == [row[0] for row in _BASELINE_ROWS], (
        "the baseline names author rows "
        f"{[row[0] for row in _BASELINE_ROWS]}; rebuilding them renders "
        f"{[row[0] for row in rendered[2:]]}"
    )
    for documented, produced in zip(_BASELINE_ROWS, rendered[2:], strict=True):
        assert documented == produced, (
            f"docs/metrics/README.md's baseline row {documented} is not what "
            f"pr_metrics.py renders for it: {produced}"
        )


def test_the_baseline_states_the_window_it_was_taken_over() -> None:
    """Pin "over all closed pull requests": the snapshot used no `--since`."""
    assert "over all closed pull requests" in _normalised(_BASELINE)

    report = _report(_baseline_pulls())
    assert report["since"] is None
    first = pr_metrics.render(report).splitlines()[0]
    assert first == f"Pull requests considered: {report['total_considered']}", first
