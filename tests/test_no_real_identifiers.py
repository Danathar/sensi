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

Which values count is decided by `docs/review-rubric.md` and by the
`capture-payload` runbook, not by this module: both name `icd_id`,
`serial_number`, `unique_hardware_id`, `wifi_mac_address` and the
`registration` address fields as the set a captured payload has to lose before
it becomes a fixture. A guard that reads a subset of that list reports success
over the fields it does not read, so all of them are checked here.

Two of those need care rather than another name in a pattern.
`unique_hardware_id` is an integer in the payload, so a check that only reads
quoted values does not see it; and `city` and `state` are ordinary words in a
thermostat integration -- an entity state, a review state -- so they are read
only inside a `registration` mapping, where they mean a building. `address1`,
`address2` and `postal_code` mean the same thing wherever they appear and are
read everywhere.

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
# The fields that identify a physical unit, in either form they are written
# in: a JSON mapping, and a keyword argument in a test built from a real
# session. The unquoted branch is digits only, because
# `unique_hardware_id` is a number in the payload while a bare word after `=`
# is an expression (`serial_number=device.info.serial_number`) rather than a
# committed value.
_HARDWARE = re.compile(
    r"""(?x)
    ['"]?\b(serial_number|wifi_mac_address|unique_hardware_id)\b['"]?
    \s*[:=]\s*
    (?: ['"]([^'"\n]*)['"] | (\d+) )
    """
)

# The `registration` fields that name a building rather than a device. The
# first three are unambiguous wherever they appear; `city` and `state` are read
# only inside a registration mapping, for the reason in the module docstring.
_ADDRESS_ANYWHERE = re.compile(
    r"""(?ix)
    ['"]?\b(address1|address2|postal_code)\b['"]?
    \s*[:=]\s*
    ['"]([^'"\n]*)['"]
    """
)
_ADDRESS_IN_REGISTRATION = re.compile(
    r"""(?ix)
    ['"]?\b(address1|address2|postal_code|city|state)\b['"]?
    \s*[:=]\s*
    ['"]([^'"\n]*)['"]
    """
)
_REGISTRATION_KEY = re.compile(r"""(?i)['"]?\bregistration\b['"]?\s*[:=]\s*\{""")
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

# The invented hardware and location values this module assigns, to prove the
# checks fire. Listed here for the same reason as `_FAKE_OPAQUE` below: the
# alternative is exempting this file, and a path this scan skips is a path a
# real value could be pasted into.
_FAKE_HARDWARE_ID = "4419382"
_FAKE_KWARG_SERIAL = "ZR41WQ88X2K7"
_FAKE_STREET = "1742 Ridgecrest Drive"
_FAKE_UNIT = "Apt 6B"
_FAKE_POSTCODE = "99709"
_FAKE_CITY = "Fairbanks"
_FAKE_STATE = "Alaska"

ALLOWED_HARDWARE_VALUES = frozenset(
    {
        "TESTSERIAL0001",
        "001122334455",
        "S1",  # a deliberately short serial used to test truncation
        "1",  # the synthetic unique_hardware_id, a number in the payload
        "",
        _FAKE_HARDWARE_ID,
        _FAKE_KWARG_SERIAL,
    }
)

# The placeholders standing in for the account's address. `capture-payload.md`
# tells a scrubber to reuse exactly these, so they are the whole permitted set;
# anything else in a `registration` block came from a real account.
ALLOWED_LOCATION_VALUES = frozenset(
    {
        "Somewhere",
        "Madison",
        "Wisconsin",
        "53719",
        "",
        _FAKE_STREET,
        _FAKE_UNIT,
        _FAKE_POSTCODE,
        _FAKE_CITY,
        _FAKE_STATE,
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


def _hardware_offenders(
    text: str,
    relative_path: str,
    allowed: frozenset[str] = ALLOWED_HARDWARE_VALUES,
) -> list[str]:
    """Report assignments in `text` naming a unit with a real-looking value."""
    offenders: list[str] = []
    for key, quoted, unquoted in _HARDWARE.findall(text):
        value = quoted or unquoted
        if value in allowed:
            continue
        offenders.append(f"{relative_path}: {key} = {_mask(value)}")
    return offenders


def _registration_regions(text: str) -> list[str]:
    """Return the `registration` mappings in `text`, read with braces matched.

    A captured payload arrives either as a fixture or pasted into a comment --
    `data.py` carries one of each -- so this reads the text rather than a
    parsed document, and gets both.
    """
    regions: list[str] = []
    for match in _REGISTRATION_KEY.finditer(text):
        start = text.rindex("{", match.start(), match.end())
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    regions.append(text[start : index + 1])
                    break
    return regions


def _location_offenders(
    text: str,
    relative_path: str,
    allowed: frozenset[str] = ALLOWED_LOCATION_VALUES,
) -> list[str]:
    """Address fields in `text` carrying something other than a placeholder."""
    found: list[tuple[str, str]] = _ADDRESS_ANYWHERE.findall(text)
    for region in _registration_regions(text):
        found.extend(_ADDRESS_IN_REGISTRATION.findall(region))

    offenders: list[str] = []
    for key, value in found:
        if value in allowed:
            continue
        entry = f"{relative_path}: {key} = {_mask(value)}"
        if entry not in offenders:
            offenders.append(entry)
    return offenders


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
        ".claude/hooks/gate-git-file-arguments.py",
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


def test_every_serial_mac_and_hardware_id_is_synthetic() -> None:
    """The same rule for the other three values that name a physical unit."""
    offenders: list[str] = []
    for path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders.extend(_hardware_offenders(text, str(path.relative_to(_ROOT))))

    assert offenders == [], (
        "serial, MAC or hardware id values that are not synthetic:\n  "
        + "\n  ".join(offenders)
    )


def test_every_registration_address_is_a_placeholder() -> None:
    """A captured payload names the building the thermostat is in.

    `docs/review-rubric.md` puts the `registration` address fields in the same
    list as the device identifiers, and they are the entry on it that is about
    a person rather than a unit: a street address and a postal code locate a
    home, and a thermostat payload says whether anyone is currently heating it.
    """
    offenders: list[str] = []
    for path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders.extend(_location_offenders(text, str(path.relative_to(_ROOT))))

    assert offenders == [], (
        "address fields that are not the committed placeholders:\n  "
        + "\n  ".join(offenders)
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


def _mapping(key: str, value: str) -> str:
    """Write a `key: value` pair at run time rather than in this source.

    The checks above read this file like any other, so a test case written as
    a literal would be a committed assignment of exactly the shape they
    reject, and would have to be allowlisted to keep the suite green --
    which is how an exemption gets added to a guard for the guard's own sake.
    Assembling the pair here leaves nothing in the text to match.
    """
    return '{"' + key + '": "' + value + '"}'


def _unquoted_mapping(key: str, value: str) -> str:
    """Build the same, for the number `unique_hardware_id` arrives as."""
    return '{"' + key + '": ' + value + "}"


def _keyword_argument(key: str, value: str) -> str:
    """Build the same, for the form a test written from a session uses."""
    return "INFO = dict(" + key + '="' + value + '")'


@pytest.mark.parametrize(
    ("build", "key", "value"),
    [
        # The payload form: a number, which a quoted-value check cannot see.
        (_unquoted_mapping, "unique_hardware_id", _FAKE_HARDWARE_ID),
        # A keyword argument in a test written from a real session.
        (_keyword_argument, "serial_number", _FAKE_KWARG_SERIAL),
    ],
)
def test_a_real_hardware_value_is_rejected_in_either_form(
    build, key: str, value: str
) -> None:
    """A serial in a mapping was the only shape the previous check read."""
    source = build(key, value)

    assert _hardware_offenders(source, "tests/sample_new.json", frozenset()) != []


def test_an_expression_is_not_a_committed_hardware_value() -> None:
    """`entity.py` passes the field through; that is not a value to reject."""
    source = "serial_number=device.info.serial_number,"

    assert _hardware_offenders(source, "custom_components/sensi/entity.py") == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("address1", _FAKE_STREET),
        ("address2", _FAKE_UNIT),
        ("postal_code", _FAKE_POSTCODE),
        ("city", _FAKE_CITY),
        ("state", _FAKE_STATE),
    ],
)
def test_a_captured_address_is_rejected(key: str, value: str) -> None:
    """Every field the runbook tells a scrubber to replace."""
    source = '{"registration": ' + _mapping(key, value) + "}"

    assert _location_offenders(source, "tests/sample_new.json", frozenset()) != []


def test_a_street_address_is_read_outside_a_registration_block_too() -> None:
    """`address1` means one thing wherever a payload gets pasted."""
    source = "# captured: " + _mapping("address1", _FAKE_STREET)

    assert _location_offenders(source, "custom_components/sensi/data.py", frozenset())


def test_city_and_state_are_read_only_inside_a_registration_block() -> None:
    """Both are ordinary words here: an entity state, a review state.

    Checking them everywhere would redden the guard on code that has nothing to
    do with an address, and a guard that reddens on ordinary code gets weakened
    rather than obeyed.
    """
    source = "review = " + _mapping("state", "APPROVED")

    assert _location_offenders(source, "scripts/pr_metrics.py", frozenset()) == []


def test_the_registration_block_is_read_to_its_closing_brace() -> None:
    """A nested mapping must not end the region early."""
    inner = _mapping("city", _FAKE_CITY)
    source = (
        '{"registration": {"nested": {"a": 1}, '
        + inner.lstrip("{").rstrip("}")
        + "}, "
        + _mapping("state", "later").lstrip("{").rstrip("}")
        + "}"
    )
    offenders = _location_offenders(source, "tests/sample_new.json", frozenset())

    assert len(offenders) == 1, offenders
    assert "city" in offenders[0]


def test_the_committed_placeholders_stay_accepted() -> None:
    """The fixtures must keep passing, or the guard gets an exemption."""
    for name in ("sample.json", "sample_with_nulls.json"):
        text = (_ROOT / "tests" / name).read_text(encoding="utf-8")

        assert _location_offenders(text, f"tests/{name}") == []
        assert _hardware_offenders(text, f"tests/{name}") == []


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
