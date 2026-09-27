"""Tests for scripts/check_requirements_sync.py.

Not part of the component, so not measured by the coverage gate - but this
script is a step in `nightly.yml` that can fail the whole run, and it is the
only thing standing between `manifest.json` and `requirements_component.txt`
drifting apart. Both of its failure modes matter: missing a real drift ships a
dependency Home Assistant will not install, and failing on files that agree
turns the nightly red for nothing.

The script resolves both paths at import time, so the tests point its
module-level MANIFEST and REQUIREMENTS at temporary files rather than editing
the real ones.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_requirements_sync.py"
)
_spec = importlib.util.spec_from_file_location("check_requirements_sync", _SCRIPT)
check_requirements_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_requirements_sync)


@pytest.fixture
def files(monkeypatch, tmp_path):
    """Point the script at a temporary manifest/requirements pair."""

    def _write(manifest_requirements, requirements_text):
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps({"domain": "sensi", "requirements": manifest_requirements}),
            encoding="utf-8",
        )
        requirements = tmp_path / "requirements_component.txt"
        requirements.write_text(requirements_text, encoding="utf-8")
        monkeypatch.setattr(check_requirements_sync, "ROOT", tmp_path)
        monkeypatch.setattr(check_requirements_sync, "MANIFEST", manifest)
        monkeypatch.setattr(check_requirements_sync, "REQUIREMENTS", requirements)
        return manifest, requirements

    return _write


def test_read_requirements_keeps_the_pins(tmp_path):
    """A pin is the whole line, version specifier included."""
    path = tmp_path / "requirements_component.txt"
    path.write_text("python-socketio==5.14.1\naiohttp==3.13.2\n", encoding="utf-8")

    assert check_requirements_sync.read_requirements(path) == {
        "python-socketio==5.14.1",
        "aiohttp==3.13.2",
    }


def test_read_requirements_ignores_comments_blanks_and_options(tmp_path):
    """Only the pins are dependencies; `-r` and `#` lines are not."""
    path = tmp_path / "requirements_component.txt"
    path.write_text(
        "# what Home Assistant installs\n"
        "\n"
        "   \n"
        "-r requirements_component.txt\n"
        "--index-url https://example.invalid/simple\n"
        "  python-socketio==5.14.1  \n",
        encoding="utf-8",
    )

    assert check_requirements_sync.read_requirements(path) == {
        "python-socketio==5.14.1"
    }


def test_in_sync_reports_the_count_and_exits_zero(files, capsys):
    """The nightly's happy path: agreement, exit 0, and the count printed."""
    files(
        ["python-socketio==5.14.1", "aiohttp==3.13.2"],
        "# comment\npython-socketio==5.14.1\naiohttp==3.13.2\n",
    )

    assert check_requirements_sync.main() == 0
    assert "OK: 2 requirement(s) in sync" in capsys.readouterr().out


def test_empty_on_both_sides_is_still_in_sync(files, capsys):
    """No dependencies is a legitimate state, not a drift."""
    files([], "# nothing pinned yet\n")

    assert check_requirements_sync.main() == 0
    assert "OK: 0 requirement(s) in sync" in capsys.readouterr().out


def test_a_requirement_only_in_the_manifest_fails(files, capsys):
    """Home Assistant would install it; the test environment would not."""
    files(["python-socketio==5.14.1", "aiohttp==3.13.2"], "python-socketio==5.14.1\n")

    assert check_requirements_sync.main() == 1
    out = capsys.readouterr().out
    assert (
        "::error file=requirements_component.txt::in manifest.json only: "
        "aiohttp==3.13.2" in out
    )
    assert "list different requirements; update both." in out


def test_a_requirement_only_in_the_requirements_file_fails(files, capsys):
    """The tests would install it; a real installation would not."""
    files(["python-socketio==5.14.1"], "python-socketio==5.14.1\naiohttp==3.13.2\n")

    assert check_requirements_sync.main() == 1
    assert (
        "::error file=manifest.json::in requirements_component.txt only: "
        "aiohttp==3.13.2" in capsys.readouterr().out
    )


def test_a_version_difference_is_reported_from_both_sides(files, capsys):
    """A changed pin is not one difference - it is missing on each side."""
    files(["python-socketio==5.14.1"], "python-socketio==5.13.0\n")

    assert check_requirements_sync.main() == 1
    out = capsys.readouterr().out
    assert "in manifest.json only: python-socketio==5.14.1" in out
    assert "in requirements_component.txt only: python-socketio==5.13.0" in out


def test_every_difference_is_annotated_in_sorted_order(files, capsys):
    """One annotation per drifted pin, so the log names all of them at once."""
    files(["aaa==1", "bbb==2", "ccc==3"], "ccc==3\n")

    assert check_requirements_sync.main() == 1
    annotations = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("::error")
    ]
    assert len(annotations) == 2
    assert annotations[0].endswith("in manifest.json only: aaa==1")
    assert annotations[1].endswith("in manifest.json only: bbb==2")


@pytest.mark.parametrize(
    "line",
    [
        "python-socketio==5.16.4  # CVE fix, see #113",
        "python-socketio == 5.16.4",
        "Python-SocketIO==5.16.4",
        "python_socketio==5.16.4",
        "python.socketio==5.16.4",
    ],
)
def test_an_equivalent_spelling_is_in_sync(files, capsys, line):
    """Each of these is the manifest's pin to pip, so none is drift."""
    files(["python-socketio==5.16.4"], f"# Keep in sync with manifest.json.\n{line}\n")

    assert check_requirements_sync.main() == 0
    assert "OK: 1 requirement(s) in sync" in capsys.readouterr().out


def test_extras_markers_and_specifier_order_do_not_matter(files, capsys):
    """Only whitespace and order differ, so the two lists agree."""
    files(
        ["aiohttp[Speedups,dev]>=3.9,<4; python_version >= '3.14'"],
        "aiohttp [dev, speedups] < 4 , >= 3.9 ;  python_version >= '3.14'\n",
    )

    assert check_requirements_sync.main() == 0


def test_a_different_marker_is_still_drift(files, capsys):
    """Normalising the spelling must not hide a real difference."""
    files(
        ["aiohttp==3.13.2; python_version >= '3.14'"],
        "aiohttp==3.13.2\n",
    )

    assert check_requirements_sync.main() == 1


def test_the_drift_report_quotes_each_line_as_written(files, capsys):
    """The annotation shows the file's own text, not the normalised form."""
    files(["Python_SocketIO==5.16.4"], "python-socketio==5.13.0  # old\n")

    assert check_requirements_sync.main() == 1
    out = capsys.readouterr().out
    assert "in manifest.json only: Python_SocketIO==5.16.4" in out
    assert "in requirements_component.txt only: python-socketio==5.13.0\n" in out


def test_a_hash_without_whitespace_before_it_is_not_a_comment(tmp_path):
    """A URL fragment is kept: pip needs whitespace before a comment `#`."""
    path = tmp_path / "requirements_component.txt"
    path.write_text(
        "pkg @ https://example.invalid/pkg.zip#sha256=ab\n", encoding="utf-8"
    )

    assert check_requirements_sync.read_requirements(path) == {
        "pkg @ https://example.invalid/pkg.zip#sha256=ab"
    }


def test_an_unrecognised_line_is_compared_as_written():
    """A line the pattern cannot parse is left alone, not guessed at."""
    assert check_requirements_sync.canonical("  ???  ") == "???"


def test_the_shipped_files_are_in_sync():
    """The check the nightly runs, run here against the real pair.

    This is the assertion that actually catches drift on a pull request; the
    nightly only notices once a day.
    """
    assert check_requirements_sync.main() == 0
