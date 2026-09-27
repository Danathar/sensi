"""Tests for scripts/coverage_badge.py.

Not part of the component, so not measured by the coverage gate - but this
script is what *reports* that gate's number to everyone who looks at the
README, and a badge that lies is worse than no badge.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "coverage_badge.py"
_spec = importlib.util.spec_from_file_location("coverage_badge", _SCRIPT)
coverage_badge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coverage_badge)


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (100.0, "brightgreen"),
        (93.0, "brightgreen"),  # exactly at the gate passes
        (92.9, "yellow"),
        (85.0, "yellow"),
        (84.9, "red"),
        (0.0, "red"),
    ],
)
def test_badge_color_boundaries(percent, expected):
    """The gate is >=, so the colour has to flip at exactly the threshold."""
    assert coverage_badge.badge_color(percent) == expected


def test_color_thresholds_are_overridable():
    """CI passes the gate's own MIN_COVERAGE so the number is not duplicated."""
    assert coverage_badge.badge_color(90.0, high=95.0, low=80.0) == "yellow"
    assert coverage_badge.badge_color(96.0, high=95.0, low=80.0) == "brightgreen"


def test_badge_message_does_not_round_up_past_the_gate():
    """92.6 must not render as '93%' next to a 93% gate it does not meet."""
    payload = coverage_badge.badge_payload(92.6)
    assert payload["message"] == "92.6%"
    assert payload["color"] == "yellow"


def test_badge_payload_shape():
    """The payload carries exactly the four keys shields.io expects."""
    payload = coverage_badge.badge_payload(97.84)
    assert payload["schemaVersion"] == 1
    assert payload["label"] == "unit coverage"
    assert payload["message"] == "97.8%"


def test_writes_badge_and_appends_trend(tmp_path):
    """A new commit adds a row and leaves the existing history intact."""
    badge = tmp_path / "coverage-unit.json"
    trend = tmp_path / "coverage-trend.csv"
    trend.write_text("2026-09-01,deadbeef,97.00\n", encoding="utf-8")

    rc = coverage_badge.main(
        [
            "98.25",
            "--date",
            "2026-09-05",
            "--sha",
            "cafebabe",
            "--badge-out",
            str(badge),
            "--trend-out",
            str(trend),
        ]
    )

    assert rc == 0
    assert json.loads(badge.read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "label": "unit coverage",
        "message": "98.2%",
        "color": "brightgreen",
    }
    # Appends, never truncates - the history is the point.
    assert trend.read_text(encoding="utf-8").splitlines() == [
        "2026-09-01,deadbeef,97.00",
        "2026-09-05,cafebabe,98.25",
    ]


def _run(tmp_path, percent, sha, trend, date="2026-09-05"):
    """Invoke the script once against the given trend file."""
    return coverage_badge.main(
        [
            percent,
            "--date",
            date,
            "--sha",
            sha,
            "--badge-out",
            str(tmp_path / "coverage-unit.json"),
            "--trend-out",
            str(trend),
        ]
    )


def _badge_message(tmp_path):
    """Return the message the last written badge carries."""
    badge = tmp_path / "coverage-unit.json"
    return json.loads(badge.read_text(encoding="utf-8"))["message"]


def test_rerunning_the_same_commit_changes_nothing(tmp_path):
    """A workflow re-run publishes the same SHA twice; that is one row, not two.

    If it appended, the trend file would always differ and the publish job's
    "nothing changed, do not commit" guard could never fire.
    """
    trend = tmp_path / "coverage-trend.csv"

    _run(tmp_path, "97.84", "aaaaaaaa", trend)
    _run(tmp_path, "98.25", "bbbbbbbb", trend)
    before = trend.read_text(encoding="utf-8")
    _run(tmp_path, "98.25", "bbbbbbbb", trend)

    assert trend.read_text(encoding="utf-8") == before
    assert trend.read_text(encoding="utf-8").splitlines() == [
        "2026-09-05,aaaaaaaa,97.84",
        "2026-09-05,bbbbbbbb,98.25",
    ]


def test_rerunning_a_commit_on_a_later_day_keeps_its_original_row(tmp_path):
    """The row records when the commit was first measured, not the re-run date.

    Rewriting the date would change the file, so a re-run would push a commit
    that records nothing new.
    """
    trend = tmp_path / "coverage-trend.csv"
    _run(tmp_path, "99.95", "aaaaaaaa", trend, date="2026-09-08")
    before = trend.read_bytes()

    _run(tmp_path, "99.95", "aaaaaaaa", trend, date="2026-09-09")

    assert trend.read_bytes() == before


def test_rerunning_a_commit_with_a_different_number_keeps_the_first(tmp_path):
    """A commit already in the history is not re-measured by a re-run.

    The row and the badge both keep what was first published for it.
    """
    trend = tmp_path / "coverage-trend.csv"
    _run(tmp_path, "97.84", "aaaaaaaa", trend)
    _run(tmp_path, "98.25", "aaaaaaaa", trend)

    assert trend.read_text(encoding="utf-8").splitlines() == [
        "2026-09-05,aaaaaaaa,97.84"
    ]
    assert _badge_message(tmp_path) == "97.8%"


def test_rerunning_an_older_commit_does_not_rewrite_history_or_the_badge(tmp_path):
    """Re-running `aaa` after `bbb` published leaves `bbb` as the latest.

    Appending a second `aaa` row would record one commit twice, and writing
    its badge would show an older number than the newest row.
    """
    trend = tmp_path / "coverage-trend.csv"
    _run(tmp_path, "99.95", "aaaaaaaa", trend)
    _run(tmp_path, "98.50", "bbbbbbbb", trend)

    _run(tmp_path, "99.95", "aaaaaaaa", trend, date="2026-09-06")

    assert trend.read_text(encoding="utf-8").splitlines() == [
        "2026-09-05,aaaaaaaa,99.95",
        "2026-09-05,bbbbbbbb,98.50",
    ]
    assert _badge_message(tmp_path) == "98.5%"


def test_creates_the_trend_file_when_it_does_not_exist(tmp_path):
    """The very first publish has no CSV to append to."""
    trend = tmp_path / "nested" / "coverage-trend.csv"
    trend.parent.mkdir()
    _run(tmp_path, "91.00", "aaaaaaaa", trend)

    assert trend.read_text(encoding="utf-8") == "2026-09-05,aaaaaaaa,91.00\n"


@pytest.mark.parametrize("percent", ["-1", "101"])
def test_rejects_impossible_percentages(tmp_path, percent):
    """A nonsense percentage fails loudly rather than publishing a wrong badge."""
    with pytest.raises(SystemExit):
        coverage_badge.main(
            [
                percent,
                "--date",
                "2026-09-05",
                "--sha",
                "cafebabe",
                "--badge-out",
                str(tmp_path / "b.json"),
                "--trend-out",
                str(tmp_path / "t.csv"),
            ]
        )
