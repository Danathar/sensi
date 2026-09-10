"""Committed fixtures may contain synthetic device identifiers and nothing else.

`docs/SECURITY-AI.md` says real `icd_id` values must not appear in source,
fixtures, logs, commit messages or pull requests. Until #112 the repository
disagreed with its own policy: a captured Home Assistant debug log and several
fixtures carried a real thermostat's identifier, serial and MAC.

This is the check that keeps them out. It is written as an **allowlist**, which
matters more than it looks: a denylist of the known-bad values would have to
contain those values to test for them, so the guard against committing a real
identifier would itself commit one -- and a hash of a short structured
identifier is not much better, since it lets a guess be confirmed. Listing what
is permitted needs no knowledge of what is not, and it catches identifiers
nobody has seen yet rather than only the ones already found.

So: every identifier-shaped value in a tracked text file must be one of the
synthetic constants below. Anything else fails, and the failure names the file
without echoing the value.

The same reasoning decides which files are read. An allowlist of extensions
would fail *open* on the file type nobody has added yet -- a credential in a
`.sh`, a `Dockerfile` or a `.env` would simply not be looked at -- so the file
set is every tracked file, and text is decided from the bytes rather than from
the name.
"""

from pathlib import Path
import re
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# Eight-octet Sensi icd_id, in either separator style.
_ICD = re.compile(r"\b(?:[0-9a-fA-F]{2}[-:]){5,7}[0-9a-fA-F]{2}\b")
# A credential shape that must never appear in a fixture at all.
_JWT = re.compile(rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER = re.compile(rb"[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}")
# `"key": "value"` for the fields that identify a physical unit.
_HARDWARE = re.compile(
    r"['\"](serial_number|wifi_mac_address)['\"]\s*:\s*['\"]([^'\"]+)['\"]"
)

# The device identifiers this repository is allowed to contain. Add to this
# list deliberately; do not widen the pattern.
ALLOWED_ICD_IDS = frozenset(
    {
        "aa-bb-cc-dd-ee-ff-00-01",
        "aa-bb-cc-dd-ee-ff-00-02",
        "aa-bb-cc-dd-ee-ff-00-03",
        "aa-bb-cc-dd-ee-ff-00-04",
        "aa:bb:cc:dd:ee:ff:00:01",
        "aa:bb:cc:dd:ee:ff:00:02",
        # Placeholders that predate #112.
        "ff:00:00:ff:ff:ff:00:00",
        "ff:00:00:ff:00:00:ff:00",
    }
)

ALLOWED_HARDWARE_VALUES = frozenset(
    {
        "TESTSERIAL0001",
        "001122334455",
        "S1",  # a deliberately short serial used to test truncation
        "",
    }
)


def _tracked_files() -> list[Path]:
    """Every file git is tracking, which is the set the policy is about.

    Walking the checkout answers a different question: it includes untracked
    scratch that is nobody's committed content, and it misses nothing only by
    accident. Asking git is what makes "committed" in `SECURITY-AI.md` and the
    thing this module actually reads the same set.
    """
    listing = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
        text=True,
    )
    paths = (_ROOT / name for name in listing.stdout.split("\0") if name)
    return sorted(path for path in paths if path.is_file())


def _is_text(blob: bytes) -> bool:
    """Decide text from the bytes, so a new suffix needs no maintenance."""
    return b"\x00" not in blob and _decodes_as_utf8(blob)


def _decodes_as_utf8(blob: bytes) -> bool:
    try:
        blob.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _tracked_text_files() -> list[Path]:
    return [path for path in _tracked_files() if _is_text(path.read_bytes())]


def _mask(value: str) -> str:
    """Name a value without reproducing it."""
    return f"<{len(value)} chars, starts {value[:2]!r}>"


def test_the_scan_reads_the_files_it_claims_to() -> None:
    """Otherwise everything below passes by finding nothing to check."""
    files = _tracked_text_files()

    assert len(files) > 40, "the file walk stopped seeing the repository"
    names = {path.name for path in files}
    assert "sample.json" in names, "the fixtures are not being read"
    assert "data.py" in names, "the component source is not being read"


def test_the_scan_reads_files_an_extension_allowlist_would_miss() -> None:
    """The suffixes below were outside the filter this scan used to carry.

    A shell helper, a container definition and an agent rule file are all
    plausible places for a real credential to be pasted, so a narrowing of the
    file filter has to fail here rather than quietly shrink the coverage.
    """
    scanned = {str(path.relative_to(_ROOT)) for path in _tracked_text_files()}

    assert {
        "Dockerfile",
        ".claude/hooks/format-edited-python.sh",
        ".cursor/rules/sensi.mdc",
        "pytest.ini",
        "ruff.toml",
    } <= scanned


def test_the_credential_scan_reaches_binary_files_too() -> None:
    """`_JWT` and `_BEARER` run over bytes, so a blob is no place to hide one."""
    scanned_for_credentials = set(_tracked_files())
    binaries = {
        path for path in scanned_for_credentials if not _is_text(path.read_bytes())
    }

    assert binaries, "no binary file is tracked, so this claim proves nothing"
    assert not binaries & set(_tracked_text_files()), (
        "a binary file reached the text scans, which read with errors='replace'"
    )


def test_every_device_identifier_is_synthetic() -> None:
    """A real thermostat id must not be reachable from a checkout."""
    offenders: list[str] = []
    for path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for value in _ICD.findall(text):
            if value not in ALLOWED_ICD_IDS:
                offenders.append(
                    f"{path.relative_to(_ROOT)}: {_mask(value)} is not an allowed "
                    "synthetic identifier"
                )

    assert offenders == [], (
        "device identifiers that are not in ALLOWED_ICD_IDS:\n  "
        + "\n  ".join(offenders)
    )


def test_every_serial_and_mac_is_synthetic() -> None:
    """The same rule for the other two values that name a physical unit."""
    offenders: list[str] = []
    for path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for key, value in _HARDWARE.findall(text):
            if value not in ALLOWED_HARDWARE_VALUES:
                offenders.append(f"{path.relative_to(_ROOT)}: {key} = {_mask(value)}")

    assert offenders == [], (
        "serial or MAC values that are not synthetic:\n  " + "\n  ".join(offenders)
    )


def test_no_fixture_carries_a_bearer_credential() -> None:
    """Distinct from the identifiers, and worse if it happens.

    `tests/test_utils.py` holds one synthetic JWT on purpose -- it is what
    `redact_token` is tested against -- so JWT-shaped strings are allowed only
    there, and only while that file stays the single exception.

    Both patterns are byte searches, so this runs over every tracked file
    rather than the text ones: a credential embedded in a blob is still a
    committed credential, and the search costs the same.
    """
    jwt_files: list[str] = []
    bearer_files: list[str] = []
    for path in _tracked_files():
        blob = path.read_bytes()
        rel = str(path.relative_to(_ROOT))
        if _JWT.search(blob):
            jwt_files.append(rel)
        if _BEARER.search(blob):
            bearer_files.append(rel)

    assert jwt_files == ["tests/test_utils.py"], (
        f"JWT-shaped strings outside the redaction test: {jwt_files}"
    )
    assert bearer_files == [], f"Bearer credentials in tracked files: {bearer_files}"


def test_no_captured_log_is_committed() -> None:
    """#112 removed a 755 KB Home Assistant debug capture that nothing read.

    It carried hundreds of occurrences of a real identifier and was referenced
    by no test, no workflow and no doc. A captured log is the shape of file
    that accumulates operational data without anyone re-reading it, so the
    cheapest guard is not to have one.
    """
    logs = [
        str(path.relative_to(_ROOT))
        for path in _tracked_files()
        if path.suffix == ".log"
    ]

    assert logs == [], (
        f"captured log(s) committed: {logs}. If a sample is genuinely needed, "
        "write a minimal synthetic one and add it to this test's exceptions."
    )


@pytest.mark.parametrize(
    "path",
    [
        "tests/sample.json",
        "tests/sample_with_nulls.json",
        "tests/sample_with_humidification.json",
    ],
)
def test_the_fixtures_still_carry_an_identifier(path: str) -> None:
    """Sanitising must not have emptied the fixtures.

    Each of these drives tests that key on a device id; replacing the value
    with nothing would leave them passing against a shape they no longer
    exercise.
    """
    text = (_ROOT / path).read_text(encoding="utf-8")
    found = set(_ICD.findall(text))

    assert found, f"{path} no longer contains any device identifier"
    assert found <= ALLOWED_ICD_IDS
