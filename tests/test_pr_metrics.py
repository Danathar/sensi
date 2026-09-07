"""Tests for scripts/pr_metrics.py.

Not part of the component, so not measured by the coverage gate - but this is
the script `docs/metrics.md` and the README point at when they claim the
project measures outcomes rather than activity. Its numbers are the ones an
acceptance-rate comparison between human- and agent-authored changes is read
from, and every one of them is a silent number: a wrong median or a
mis-bucketed author does not fail anything, it just gets believed.

The arithmetic that decides those numbers - which pull requests count as
merged, which author bucket they land in, and which of them reach the medians
- is what these tests pin, each against the mutation that would change the
answer without changing the shape of the output.
"""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pr_metrics.py"
_spec = importlib.util.spec_from_file_location("pr_metrics", _SCRIPT)
pr_metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr_metrics)


def pull(
    number=1,
    *,
    login="human",
    bot=False,
    created="2026-01-01T00:00:00Z",
    merged=None,
    state=None,
    additions=0,
    deletions=0,
    reviews=0,
    **extra,
):
    """Return one pull request in the shape `gh pr list --json` emits."""
    record = {
        "number": number,
        "title": f"PR {number}",
        "state": state or ("MERGED" if merged else "CLOSED"),
        "author": {"login": login, "is_bot": bot},
        "createdAt": created,
        "closedAt": merged or created,
        "mergedAt": merged,
        "additions": additions,
        "deletions": deletions,
        "changedFiles": 1,
        "reviews": [{"state": "APPROVED"} for _ in range(reviews)],
        "comments": [],
    }
    record.update(extra)
    return record


def stub_gh(monkeypatch, *, stdout="[]", stderr="", returncode=0, present=True):
    """Replace `shutil.which` and `subprocess.run`; return the calls made."""
    calls = []

    monkeypatch.setattr(
        pr_metrics.shutil,
        "which",
        lambda name: "/usr/bin/gh" if present and name == "gh" else None,
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(pr_metrics.subprocess, "run", fake_run)
    return calls


# --------------------------------------------------------------------------
# die
# --------------------------------------------------------------------------


def test_die_reports_on_stderr_and_exits_one(capsys):
    """Errors belong on stderr so a `--json` run stays pipeable."""
    with pytest.raises(SystemExit) as excinfo:
        pr_metrics.die("boom")

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "error: boom"
    assert captured.out == ""


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------


def test_fetch_refuses_when_gh_is_absent(monkeypatch, capsys):
    """Without `gh` there is no data source, so say so rather than traceback."""
    stub_gh(monkeypatch, present=False)

    with pytest.raises(SystemExit) as excinfo:
        pr_metrics.fetch(50, None)

    assert excinfo.value.code == 1
    assert "https://cli.github.com/" in capsys.readouterr().err


def test_fetch_asks_for_closed_pulls_at_the_requested_limit(monkeypatch):
    """The limit reaches `gh` as a string, and only closed pulls are asked for."""
    calls = stub_gh(monkeypatch)

    pr_metrics.fetch(200, None)

    command, kwargs = calls[0]
    assert command[:3] == ["gh", "pr", "list"]
    assert command[command.index("--state") + 1] == "closed"
    assert command[command.index("--limit") + 1] == "200"
    assert "--repo" not in command
    assert kwargs["check"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_fetch_passes_repo_through_when_given(monkeypatch):
    """`--repo` is what lets the script report on a repository it is not in."""
    calls = stub_gh(monkeypatch)

    pr_metrics.fetch(50, "Danathar/sensi")

    command = calls[0][0]
    assert command[command.index("--repo") + 1] == "Danathar/sensi"


def test_fetch_requests_every_field_the_report_consumes(monkeypatch):
    """A field dropped from FIELDS is a metric that silently becomes empty."""
    calls = stub_gh(monkeypatch)

    pr_metrics.fetch(50, None)

    requested = set(calls[0][0][calls[0][0].index("--json") + 1].split(","))
    assert requested == set(pr_metrics.FIELDS)
    assert requested >= {
        "author",
        "createdAt",
        "mergedAt",
        "additions",
        "deletions",
        "reviews",
    }


def test_fetch_surfaces_ghs_own_error(monkeypatch, capsys):
    """`gh`'s message is more useful than anything this script could invent."""
    stub_gh(monkeypatch, returncode=1, stderr="  gh: not authenticated  \n")

    with pytest.raises(SystemExit):
        pr_metrics.fetch(50, None)

    assert "error: gh: not authenticated" in capsys.readouterr().err


def test_fetch_falls_back_when_gh_fails_silently(monkeypatch, capsys):
    """A non-zero exit with empty stderr still has to produce a message."""
    stub_gh(monkeypatch, returncode=1, stderr="   \n")

    with pytest.raises(SystemExit):
        pr_metrics.fetch(50, None)

    assert "error: gh pr list failed" in capsys.readouterr().err


def test_fetch_returns_the_parsed_json(monkeypatch):
    """Success returns decoded records, not the raw text."""
    stub_gh(monkeypatch, stdout=json.dumps([pull(7)]))

    assert pr_metrics.fetch(50, None)[0]["number"] == 7


# --------------------------------------------------------------------------
# parse_time / hours_between
# --------------------------------------------------------------------------


def test_parse_time_reads_githubs_z_suffix():
    """`fromisoformat` needs the offset spelled out, so Z is rewritten."""
    parsed = pr_metrics.parse_time("2026-01-01T12:00:00Z")

    assert parsed.utcoffset().total_seconds() == 0
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 1, 1, 12)


def test_parse_time_keeps_an_explicit_offset():
    """Rewriting Z must not disturb a timestamp that already carries an offset."""
    parsed = pr_metrics.parse_time("2026-01-01T12:00:00+02:00")

    assert parsed.utcoffset().total_seconds() == 7200


@pytest.mark.parametrize("value", [None, ""])
def test_parse_time_of_nothing_is_nothing(value):
    """An unmerged pull has a null `mergedAt`; that is not an error."""
    assert pr_metrics.parse_time(value) is None


def test_hours_between_counts_fractional_hours():
    """Ninety minutes is 1.5 hours, not 1 - the median depends on the fraction."""
    assert pr_metrics.hours_between(
        "2026-01-01T00:00:00Z", "2026-01-01T01:30:00Z"
    ) == pytest.approx(1.5)


def test_hours_between_spans_days_and_offsets():
    """Two timestamps in different zones still measure elapsed time correctly."""
    assert pr_metrics.hours_between(
        "2026-01-01T00:00:00Z", "2026-01-02T02:00:00+02:00"
    ) == pytest.approx(24.0)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (None, "2026-01-01T00:00:00Z"),
        ("2026-01-01T00:00:00Z", None),
        (None, None),
    ],
)
def test_hours_between_needs_both_ends(start, end):
    """A missing end is an unmerged pull, which contributes no age at all."""
    assert pr_metrics.hours_between(start, end) is None


# --------------------------------------------------------------------------
# is_bot
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ({"login": "octocat", "is_bot": False}, False),
        ({"login": "dependabot", "is_bot": True}, True),
        ({"login": "hive-wild-mole[bot]", "is_bot": False}, True),
        ({"login": "octocat"}, False),
        ({}, False),
        (None, False),
    ],
)
def test_is_bot_recognises_both_spellings(author, expected):
    """`gh` reports app authorship as `is_bot`, but only sometimes.

    The `[bot]` login suffix is the fallback, and a deleted author comes back
    as null - which must not raise, or a single ghost pull request takes the
    whole report down.
    """
    assert pr_metrics.is_bot(author) is expected


# --------------------------------------------------------------------------
# summarise
# --------------------------------------------------------------------------


def test_summarise_of_nothing_reports_nothing():
    """No pull requests means no buckets at all, not zero-filled ones."""
    report = pr_metrics.summarise([], None)

    assert report == {"total_considered": 0, "since": None, "buckets": {}}


def test_summarise_splits_human_and_bot_and_keeps_all():
    """Every pull is counted twice: once in `all`, once in its author bucket."""
    report = pr_metrics.summarise(
        [
            pull(1, login="octocat"),
            pull(2, login="hive-wild-mole[bot]"),
            pull(3, bot=True, login="dependabot"),
        ],
        None,
    )

    assert report["total_considered"] == 3
    assert report["buckets"]["all"]["proposed"] == 3
    assert report["buckets"]["human"]["proposed"] == 1
    assert report["buckets"]["bot"]["proposed"] == 2


def test_summarise_omits_an_author_bucket_nobody_is_in():
    """An all-bot window must not report a `human` row with a 0% acceptance."""
    report = pr_metrics.summarise([pull(1, bot=True)], None)

    assert set(report["buckets"]) == {"all", "bot"}


def test_summarise_counts_merged_from_merged_at_not_state():
    """`state` can say MERGED on a pull `gh` gives no `mergedAt`; trust the date."""
    report = pr_metrics.summarise([pull(1, state="MERGED", merged=None)], None)

    assert report["buckets"]["all"]["merged"] == 0
    assert report["buckets"]["all"]["closed_unmerged"] == 1
    assert report["buckets"]["all"]["acceptance_rate"] == 0.0


def test_summarise_rounds_the_acceptance_rate_to_three_places():
    """One in three is 0.333 - a rate is useless if it is a repeating float."""
    report = pr_metrics.summarise(
        [
            pull(1, merged="2026-01-01T01:00:00Z"),
            pull(2),
            pull(3),
        ],
        None,
    )

    assert report["buckets"]["all"]["acceptance_rate"] == 0.333
    assert report["buckets"]["all"]["merged"] == 1
    assert report["buckets"]["all"]["closed_unmerged"] == 2


def test_summarise_reports_median_and_max_age_of_merged_pulls_only():
    """The unmerged pull must not drag the time-to-merge numbers anywhere."""
    report = pr_metrics.summarise(
        [
            pull(1, merged="2026-01-01T01:00:00Z"),
            pull(2, merged="2026-01-01T02:00:00Z"),
            pull(3, merged="2026-01-01T10:00:00Z"),
            pull(4),
        ],
        None,
    )
    bucket = report["buckets"]["all"]

    assert bucket["median_hours_to_merge"] == 2.0
    assert bucket["max_hours_to_merge"] == 10.0


def test_summarise_rounds_hours_to_one_place():
    """Sixty-five minutes reports as 1.1 hours, not 1.0833333333333333."""
    report = pr_metrics.summarise([pull(1, merged="2026-01-01T01:05:00Z")], None)

    assert report["buckets"]["all"]["median_hours_to_merge"] == 1.1
    assert report["buckets"]["all"]["max_hours_to_merge"] == 1.1


def test_summarise_leaves_the_age_of_an_instant_merge_out():
    """A pull merged in the same second contributes no measurable age.

    `ages` is built with `if (hours := hours_between(...))`, so a zero-hour
    span is dropped alongside the None from an unmerged pull. It still counts
    as merged - only the duration is missing - and with nothing else in the
    window the medians are None rather than 0.0.
    """
    report = pr_metrics.summarise([pull(1, merged="2026-01-01T00:00:00Z")], None)
    bucket = report["buckets"]["all"]

    assert bucket["merged"] == 1
    assert bucket["acceptance_rate"] == 1.0
    assert bucket["median_hours_to_merge"] is None
    assert bucket["max_hours_to_merge"] is None


def test_summarise_reports_no_durations_when_nothing_merged():
    """Every merged-only metric is None, not zero, when nothing was accepted."""
    report = pr_metrics.summarise([pull(1), pull(2)], None)
    bucket = report["buckets"]["all"]

    assert bucket["acceptance_rate"] == 0.0
    assert bucket["median_hours_to_merge"] is None
    assert bucket["max_hours_to_merge"] is None
    assert bucket["median_reviews_before_merge"] is None
    assert bucket["median_lines_changed"] is None


def test_summarise_counts_reviews_on_merged_pulls():
    """Review rounds are the "how much review did it need" half of the metric."""
    report = pr_metrics.summarise(
        [
            pull(1, merged="2026-01-01T01:00:00Z", reviews=1),
            pull(2, merged="2026-01-01T01:00:00Z", reviews=3),
            pull(3, reviews=9),
        ],
        None,
    )

    assert report["buckets"]["all"]["median_reviews_before_merge"] == 2.0


def test_summarise_treats_a_null_reviews_list_as_no_reviews():
    """`gh` can emit null rather than [] for a pull nobody reviewed."""
    empty = pull(1, merged="2026-01-01T01:00:00Z", reviews=0)
    null = pull(2, merged="2026-01-01T01:00:00Z")
    null["reviews"] = None

    assert (
        pr_metrics.summarise([empty], None)["buckets"]["all"][
            "median_reviews_before_merge"
        ]
        == 0
    )
    assert (
        pr_metrics.summarise([null], None)["buckets"]["all"][
            "median_reviews_before_merge"
        ]
        == 0
    )


def test_summarise_measures_churn_as_additions_plus_deletions():
    """A change that deletes 500 lines is not a zero-line change."""
    report = pr_metrics.summarise(
        [pull(1, merged="2026-01-01T01:00:00Z", additions=10, deletions=500)],
        None,
    )

    assert report["buckets"]["all"]["median_lines_changed"] == 510


def test_summarise_defaults_missing_line_counts_to_zero():
    """A record without the count keys must not raise mid-report."""
    record = pull(1, merged="2026-01-01T01:00:00Z")
    del record["additions"]
    del record["deletions"]

    report = pr_metrics.summarise([record], None)

    assert report["buckets"]["all"]["median_lines_changed"] == 0


def test_summarise_since_drops_pulls_opened_earlier():
    """`--since` is a window on when the change was proposed, not merged."""
    report = pr_metrics.summarise(
        [
            pull(1, created="2025-12-31T23:59:59Z"),
            pull(2, created="2026-01-02T00:00:00Z"),
        ],
        "2026-01-01",
    )

    assert report["total_considered"] == 1
    assert report["since"] == "2026-01-01"
    assert report["buckets"]["all"]["proposed"] == 1


def test_summarise_since_is_inclusive_of_midnight():
    """The cutoff is `>=`, so a pull opened at 00:00:00 on the day is in."""
    report = pr_metrics.summarise(
        [pull(1, created="2026-01-01T00:00:00Z")], "2026-01-01"
    )

    assert report["total_considered"] == 1


def test_summarise_without_since_keeps_everything():
    """No window means no filtering, however old the pull request is."""
    report = pr_metrics.summarise([pull(1, created="2015-06-01T00:00:00Z")], None)

    assert report["total_considered"] == 1
    assert report["since"] is None


def test_summarise_keeps_the_buckets_independent():
    """Each bucket's medians come from its own pulls, not from `all`."""
    report = pr_metrics.summarise(
        [
            pull(1, login="octocat", merged="2026-01-01T10:00:00Z", additions=100),
            pull(2, bot=True, merged="2026-01-01T01:00:00Z", additions=2),
            pull(3, bot=True),
        ],
        None,
    )

    assert report["buckets"]["human"]["acceptance_rate"] == 1.0
    assert report["buckets"]["human"]["median_hours_to_merge"] == 10.0
    assert report["buckets"]["human"]["median_lines_changed"] == 100
    assert report["buckets"]["bot"]["acceptance_rate"] == 0.5
    assert report["buckets"]["bot"]["median_hours_to_merge"] == 1.0
    assert report["buckets"]["bot"]["median_lines_changed"] == 2


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------


def rendered_rows(text):
    """Return the table's data rows, keyed by their first column."""
    rows = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if cells and cells[0] in ("all", "human", "bot"):
            rows[cells[0]] = cells
    return rows


def test_render_reports_the_scope_and_the_count():
    """The header line is the only place the window is stated."""
    report = pr_metrics.summarise([pull(1)], None)

    assert pr_metrics.render(report).splitlines()[0] == ("Pull requests considered: 1")


def test_render_names_the_since_date_when_one_was_given():
    """A filtered count that does not say it is filtered is a misleading count."""
    report = pr_metrics.summarise([pull(1)], "2026-01-01")

    assert pr_metrics.render(report).splitlines()[0] == (
        "Pull requests considered since 2026-01-01: 1"
    )


def test_render_emits_a_markdown_table_header():
    """The output is pasted into issues, so it has to be valid Markdown."""
    lines = pr_metrics.render(pr_metrics.summarise([pull(1)], None)).splitlines()

    assert lines[2].startswith("| Author | Proposed | Merged |")
    assert lines[3] == "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"


def test_render_orders_rows_all_human_bot_whatever_the_input_order():
    """`bot` first in the data must still print last; the table is a comparison."""
    report = pr_metrics.summarise([pull(1, bot=True), pull(2, login="octocat")], None)
    order = [
        line.split("|")[1].strip()
        for line in pr_metrics.render(report).splitlines()
        if line.split("|")[1:2]
        and line.split("|")[1].strip() in ("all", "human", "bot")
    ]

    assert order == ["all", "human", "bot"]


def test_render_skips_an_absent_bucket():
    """An all-bot window prints two rows, not an empty `human` one."""
    rows = rendered_rows(
        pr_metrics.render(pr_metrics.summarise([pull(1, bot=True)], None))
    )

    assert set(rows) == {"all", "bot"}


def test_render_shows_the_acceptance_rate_as_a_percentage():
    """0.667 is read as a percentage by everyone; print it as one."""
    report = pr_metrics.summarise(
        [
            pull(1, merged="2026-01-01T01:00:00Z"),
            pull(2, merged="2026-01-01T02:00:00Z"),
            pull(3),
        ],
        None,
    )

    assert rendered_rows(pr_metrics.render(report))["all"][4] == "67%"


def test_render_writes_a_dash_for_a_missing_number():
    """None must not reach the table as the word "None"."""
    rows = rendered_rows(pr_metrics.render(pr_metrics.summarise([pull(1)], None)))

    assert rows["all"] == ["all", "1", "0", "1", "0%", "-", "-", "-"]


def test_render_writes_every_measured_number_in_column_order():
    """The row is proposed, merged, unmerged, rate, hours, reviews, lines."""
    report = pr_metrics.summarise(
        [
            pull(1, merged="2026-01-01T02:00:00Z", additions=3, deletions=4, reviews=2),
            pull(2),
        ],
        None,
    )

    assert rendered_rows(pr_metrics.render(report))["all"] == [
        "all",
        "2",
        "1",
        "1",
        "50%",
        "2.0",
        "2",
        "7",
    ]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def run_main(monkeypatch, argv, pulls):
    """Run `main()` with the given arguments over a fixed set of pulls."""
    calls = []

    def fake_fetch(limit, repo):
        calls.append((limit, repo))
        return pulls

    monkeypatch.setattr(pr_metrics, "fetch", fake_fetch)
    monkeypatch.setattr(pr_metrics.sys, "argv", ["pr_metrics.py", *argv])
    return pr_metrics.main(), calls


def test_main_fetches_fifty_of_the_current_repo_by_default(monkeypatch, capsys):
    """The documented default is 50; `--repo` unset means the current one."""
    rc, calls = run_main(monkeypatch, [], [pull(1)])
    capsys.readouterr()

    assert rc == 0
    assert calls == [(50, None)]


def test_main_passes_limit_and_repo_through(monkeypatch, capsys):
    """`--limit` arrives as an int, which is what `fetch` formats for `gh`."""
    rc, calls = run_main(
        monkeypatch, ["--limit", "200", "--repo", "Danathar/sensi"], [pull(1)]
    )
    capsys.readouterr()

    assert rc == 0
    assert calls == [(200, "Danathar/sensi")]


def test_main_applies_since_after_fetching(monkeypatch, capsys):
    """`--since` filters what `gh` returned; it is not a `gh` argument."""
    rc, calls = run_main(
        monkeypatch,
        ["--since", "2026-01-01"],
        [pull(1, created="2025-01-01T00:00:00Z"), pull(2)],
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert calls == [(50, None)]
    assert out.splitlines()[0] == "Pull requests considered since 2026-01-01: 1"


def test_main_says_so_when_nothing_matched(monkeypatch, capsys):
    """An empty table is worse than a sentence saying the window was empty."""
    rc, _ = run_main(monkeypatch, ["--since", "2030-01-01"], [pull(1)])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "No closed pull requests matched."


def test_main_prints_the_markdown_table_by_default(monkeypatch, capsys):
    """Without `--json` the output is the human-facing table."""
    rc, _ = run_main(monkeypatch, [], [pull(1, merged="2026-01-01T01:00:00Z")])
    out = capsys.readouterr().out

    assert rc == 0
    assert "| Author | Proposed | Merged |" in out
    assert not out.startswith("{")


def test_main_emits_indented_json_when_asked(monkeypatch, capsys):
    """`--json` is for piping somewhere, so it must be the whole report."""
    rc, _ = run_main(
        monkeypatch,
        ["--json", "--since", "2026-01-01"],
        [pull(1, merged="2026-01-01T01:00:00Z")],
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "\n  " in out
    payload = json.loads(out)
    assert payload["total_considered"] == 1
    assert payload["since"] == "2026-01-01"
    assert payload["buckets"]["all"]["acceptance_rate"] == 1.0


def test_main_rejects_a_non_numeric_limit(monkeypatch):
    """`--limit` is typed, so a typo fails at the boundary, not inside `gh`."""
    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, ["--limit", "many"], [])

    assert excinfo.value.code == 2
