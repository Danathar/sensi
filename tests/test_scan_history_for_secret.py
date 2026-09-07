"""Tests for scripts/scan_history_for_secret.py.

Not part of the component, so not measured by the coverage gate - but this is
the tool that answers #108's acceptance criterion 3, "a fresh clone/search
cannot recover the leaked values from reachable history". A verification tool
is worth exactly what its failures are worth: one that reported "clean" while a
credential sat in an unreferenced-but-reachable blob would end an incident
early, on the wrong answer.

So both directions are tested against real throwaway repositories rather than
mocks - a secret still present is found, and a repository without it reports
clean - along with the two guards that keep the tool from becoming the next
incident: it refuses to read needles from a tracked file, and it refuses
needles too short to mean anything.

Every assertion here checks identifiers and exit status. The one thing asserted
about content is negative: that the value never appears in the output.
"""

import importlib.util
from pathlib import Path
import subprocess

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "scan_history_for_secret.py"
)
_spec = importlib.util.spec_from_file_location("scan_history_for_secret", _SCRIPT)
scan_history_for_secret = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_history_for_secret)

# Not a credential: a synthetic literal long enough to clear MIN_NEEDLE_LENGTH,
# and obviously fake so that a future reader does not have to wonder.
FAKE_SECRET = "not-a-real-token-0123456789abcdef"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    """Build a repository whose history contains FAKE_SECRET in an older commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")

    leaky = repo / "client.py"
    leaky.write_text(f'TOKEN = "{FAKE_SECRET}"\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "leak: hardcode a token")

    # Redact on the tip, exactly as this repository did: the current tree is
    # clean while history is not, which is the state the tool exists for.
    leaky.write_text('TOKEN = os.environ["TOKEN"]\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix: stop hardcoding the token")
    return repo


@pytest.fixture(name="needles")
def needles_fixture(tmp_path: Path) -> Path:
    """Write a needle file outside any repository, as the runbook instructs."""
    path = tmp_path / "leaked.secrets"
    path.write_text(f"# never commit this file\n{FAKE_SECRET}\n", encoding="utf-8")
    return path


def test_a_secret_only_in_history_is_still_found(repo: Path, needles: Path) -> None:
    """Redacting the tip is what created this incident's false sense of safety."""
    exit_code = scan_history_for_secret.main(
        ["--secrets-file", str(needles), "--repo", str(repo)]
    )

    assert exit_code == 1


def test_the_finding_names_the_blob_and_the_commit(
    repo: Path, needles: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Identifiers are the whole output: they are what a rewrite is scoped to."""
    scan_history_for_secret.main(["--secrets-file", str(needles), "--repo", str(repo)])
    output = capsys.readouterr().out

    assert "1 blob(s)" in output
    assert "client.py" in output
    assert "leak: hardcode a token" in output


def test_the_secret_is_never_printed(
    repo: Path, needles: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one assertion about content, and it is that there is none.

    A verification tool that echoed what it found would copy the credential
    into CI logs, terminal scrollback and pasted output - which is how a
    contained leak becomes an uncontained one during the cleanup.
    """
    scan_history_for_secret.main(["--secrets-file", str(needles), "--repo", str(repo)])
    captured = capsys.readouterr()

    assert FAKE_SECRET not in captured.out
    assert FAKE_SECRET not in captured.err


def test_a_repository_without_the_secret_reports_clean(
    tmp_path: Path, needles: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The post-rewrite case. Exit 0 is what the runbook gates on."""
    clean = tmp_path / "clean"
    clean.mkdir()
    _git(clean, "init", "-q", "-b", "main")
    _git(clean, "config", "user.email", "test@example.invalid")
    _git(clean, "config", "user.name", "Test")
    (clean / "client.py").write_text('TOKEN = os.environ["TOKEN"]\n', encoding="utf-8")
    _git(clean, "add", "-A")
    _git(clean, "commit", "-q", "-m", "initial")

    exit_code = scan_history_for_secret.main(
        ["--secrets-file", str(needles), "--repo", str(clean)]
    )

    assert exit_code == 0
    assert "No reachable blob" in capsys.readouterr().out


def test_a_tracked_secrets_file_is_refused(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Committing the list of live credentials would be the same accident again."""
    tracked = repo / "leaked.secrets"
    tracked.write_text(f"{FAKE_SECRET}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "oops")

    exit_code = scan_history_for_secret.main(
        ["--secrets-file", str(tracked), "--repo", str(repo)]
    )

    assert exit_code == 2
    assert "tracked by Git" in capsys.readouterr().err


def test_a_secrets_file_outside_the_repository_is_allowed(
    repo: Path, needles: Path
) -> None:
    """The recommended arrangement: the file lives nowhere Git can reach it."""
    assert (
        scan_history_for_secret.main(
            ["--secrets-file", str(needles), "--repo", str(repo)]
        )
        == 1
    )


def test_a_short_needle_is_refused_rather_than_matching_everything(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refuse a needle short enough to match incidental text.

    `abc` matches half the repository, and a report naming every file is
    indistinguishable from no report at all.
    """
    short = tmp_path / "short.secrets"
    short.write_text("abc\n", encoding="utf-8")

    exit_code = scan_history_for_secret.main(
        ["--secrets-file", str(short), "--repo", str(repo)]
    )

    assert exit_code == 2
    assert "Refusing to scan" in capsys.readouterr().err


def test_an_empty_secrets_file_is_refused(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Zero needles would scan nothing and exit 0 - "clean" for the wrong reason."""
    empty = tmp_path / "empty.secrets"
    empty.write_text("# only a comment\n\n", encoding="utf-8")

    exit_code = scan_history_for_secret.main(
        ["--secrets-file", str(empty), "--repo", str(repo)]
    )

    assert exit_code == 2
    assert "no secrets to search for" in capsys.readouterr().err


def test_a_missing_secrets_file_is_reported(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Say which file could not be read, rather than reporting a clean scan."""
    exit_code = scan_history_for_secret.main(
        ["--secrets-file", str(tmp_path / "nope.secrets"), "--repo", str(repo)]
    )

    assert exit_code == 2
    assert "Unable to read" in capsys.readouterr().err


def test_an_oversized_blob_is_skipped(
    repo: Path, needles: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size guard is a real branch; without a test it is only a comment."""
    monkeypatch.setattr(scan_history_for_secret, "MAX_BLOB_BYTES", 1)

    assert (
        scan_history_for_secret.main(
            ["--secrets-file", str(needles), "--repo", str(repo)]
        )
        == 0
    )


def test_this_repositorys_own_gitignore_covers_the_secrets_file() -> None:
    """Keep the runbook's own advice safe to follow.

    The tool tells operators to name the file *.secrets, so that has to be
    ignored here, or following the instructions stages a credential.
    """
    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()

    assert "*.secrets" in gitignore.splitlines()
