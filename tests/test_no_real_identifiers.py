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

The credential searches need one more thing on top of that. `_JWT` and
`_BEARER` recognise a value by its *shape*, which works for an access token
because a JWT announces itself, and not at all for a refresh token, which is
an opaque string with no shape to match. A refresh token is the credential
`docs/SECURITY-AI.md` names first, the one this repository has already had
committed once, and the one that stays valid for years. So values are also
recognised by the *key* they are written against: anything long assigned to
`refresh_token`, `access_token`, `client_secret`, `api_key`, `password` or
`authorization` is treated as a credential regardless of what it looks like.
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
# A credential named by the key it is written against rather than by its shape.
# Deliberately tolerant of the forms the same assignment takes in this tree: a
# JSON `"refresh_token": "..."`, a keyword argument, and an annotated module
# constant whose value is wrapped onto the next line inside parentheses
# (`CLIENT_SECRET: Final = (\n    "..."\n)`). A secret written in the form the
# pattern does not read is a secret this test reports as absent.
_CREDENTIAL = re.compile(
    r"""(?ix)
    ['"]?\b(refresh[_-]?token|access[_-]?token|client[_-]?secret
            |api[_-]?key|password|authorization)\b['"]?
    (?:\s*:\s*[A-Za-z_][\w.\[\], |]*)?   # an optional type annotation
    \s*[:=]\s*\(?\s*                     # `:` in a mapping, `=` in code
    ['"]([^'"\n]{4,})['"]
    """
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

# How long a value assigned to a credential key has to be before it is treated
# as a real credential rather than a stand-in. Every such value committed today
# is a readable placeholder - `test_token`, `refresh_token_123`, `new_token` -
# and the longest is 23 characters; a Sensi refresh token is an opaque string
# far longer than that, and no bearer credential worth having is shorter than
# 20. Lowering this is widening the guard; raising it is narrowing it, and
# should be done only alongside the value that forced it.
MIN_CREDENTIAL_LENGTH = 20

# The invented values this module assigns to credential keys, to prove the
# check fires. They are fixtures of the guard rather than of the integration,
# but they are committed text like any other, so they are listed here instead
# of exempting this file - a path this scan skips is a path a real token could
# be pasted into.
_FAKE_OPAQUE = "c7d41a9e6b3f42a8be5107d9f3c28a41e0b6d7529af134c8"
_FAKE_SHORT = "9f2b7c14ad6e40518b3c2fa7d05e8963"
_FAKE_WRAPPED = "Kx7Hs2QpLm4Rt9Vb1Nc6Wd3Zf8Gj5Yh0Ua2Ee7T"
_FAKE_API_KEY = "7c1f0bd2e5a94836b0d1c7e4f2a6580913be4d7c"

# Committed values at or above that length. Listed rather than absorbed by a
# higher threshold: an exception that is visible in a diff is an exception
# somebody decided on.
ALLOWED_CREDENTIAL_VALUES = frozenset(
    {
        "bearer access_token_123",
        "token_for_someone_else",
        _FAKE_OPAQUE,
        _FAKE_SHORT,
        _FAKE_WRAPPED,
        _FAKE_API_KEY,
    }
)

# `auth.py` carries the two OAuth client secrets extracted from the vendor's
# Android app. They are public constants of the Sensi API rather than this
# repository's secrets - the integration cannot talk to the backend without
# them - so that one file is allowed to hold a `client_secret`. Exempted by
# path and key rather than by listing the values, which would copy them into a
# second file for nothing. Every other key is still checked in `auth.py`, and
# `client_secret` is still checked everywhere else.
_VENDOR_SECRET_FILE = "custom_components/sensi/auth.py"


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


def _credential_offenders(
    text: str,
    relative_path: str,
    allowed: frozenset[str] = ALLOWED_CREDENTIAL_VALUES,
) -> list[str]:
    """Credential-key assignments in `text` that carry a real-looking value.

    `allowed` is a parameter so the tests below can ask what this function does
    with a value nobody has approved. Passing an empty set is the only way to
    check that a value is caught, given that the values they use are committed
    here and therefore listed in the default.
    """
    offenders: list[str] = []
    for key, value in _CREDENTIAL.findall(text):
        if len(value) < MIN_CREDENTIAL_LENGTH:
            continue
        if value in allowed:
            continue
        if key.lower() == "client_secret" and relative_path == _VENDOR_SECRET_FILE:
            continue
        offenders.append(f"{relative_path}: {key} = {_mask(value)}")
    return offenders


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


def test_no_credential_key_carries_a_real_looking_value() -> None:
    """The shapeless half of the rule: a refresh token looks like nothing.

    `_JWT` and `_BEARER` cannot see an opaque token, so this reads the key it
    was written against instead.
    """
    offenders: list[str] = []
    for path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders.extend(_credential_offenders(text, str(path.relative_to(_ROOT))))

    assert offenders == [], (
        "credential keys carrying a value long enough to be a real credential:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "source",
    [
        # A fixture, which is where a captured payload gets pasted.
        f'{{"refresh_token": "{_FAKE_OPAQUE}"}}',
        # A keyword argument in a test written from a real session.
        f'config = AuthenticationConfig(refresh_token="{_FAKE_SHORT}")',
        # An annotated constant whose value is wrapped onto the next line.
        f'CLIENT_SECRET: Final = (\n    "{_FAKE_WRAPPED}"\n)',
        # A shell export in a file no extension allowlist would have read.
        f'API_KEY="{_FAKE_API_KEY}"',
    ],
)
def test_a_committed_credential_is_rejected_whatever_its_shape(source: str) -> None:
    """None of these is a JWT and none says `Bearer`; all four are credentials."""
    assert _credential_offenders(source, "tests/fixture.json", frozenset()) != []


@pytest.mark.parametrize(
    "source",
    [
        'refresh_token="test_token"',
        'access_token="new_access_token"',
        '"refresh_token": "Refresh token"',
        'refresh_token="token_for_someone_else"',
    ],
)
def test_the_placeholders_the_suite_already_uses_stay_accepted(source: str) -> None:
    """A guard that reddens on every test double gets weakened, not obeyed."""
    assert _credential_offenders(source, "tests/test_config_flow.py") == []


def test_only_auth_py_may_carry_a_client_secret() -> None:
    """The vendor's constants are public; a secret elsewhere is not."""
    source = f'CLIENT_SECRET: Final = "{_FAKE_WRAPPED}"'

    assert _credential_offenders(source, _VENDOR_SECRET_FILE, frozenset()) == []
    assert _credential_offenders(source, "tests/conftest.py", frozenset()) != []


def test_auth_py_is_still_checked_for_the_other_credential_keys() -> None:
    """The exemption is one key in one file, not an exemption for the file."""
    source = f'refresh_token = "{_FAKE_SHORT}"'

    assert _credential_offenders(source, _VENDOR_SECRET_FILE, frozenset()) != []


def test_the_vendor_client_secrets_are_the_reason_for_the_exemption() -> None:
    """If they leave `auth.py`, the exemption should leave with them.

    Asserted by reading the file rather than by naming the values, for the
    reason the module docstring gives: a guard against committing a secret
    must not need a copy of one.
    """
    text = (_ROOT / _VENDOR_SECRET_FILE).read_text(encoding="utf-8")
    found = _CREDENTIAL.findall(text)

    long_secrets = [
        key
        for key, value in found
        if key.lower() == "client_secret" and len(value) >= MIN_CREDENTIAL_LENGTH
    ]
    assert long_secrets, (
        f"{_VENDOR_SECRET_FILE} no longer carries a client secret, so the "
        "exemption in _credential_offenders is now unused - remove it"
    )


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
