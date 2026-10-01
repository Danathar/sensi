"""The guard behind README's "Nothing in CI reaches `rt.sensiapi.io`".

README's Project status section says the CI suite never talks to a real
device: "Nothing in CI reaches `rt.sensiapi.io` or a physical thermostat."
Nothing in this repository enforces that. It holds because
pytest-homeassistant-custom-component, before every test, refuses DNS lookups
of anything but a literal address and replaces `socket.socket` so that opening
an internet socket raises, and because its autouse cleanup fixture then fails
the test if any socket was refused, even one the code under test caught.

The second half matters because a refusal raised deep inside socket.io or
aiohttp can be caught on its way up, by a broad `except` in a retry loop for
example, and the test that reached out would then pass. Only the cleanup check
turns that into a failure.

No test pinned either half, and no test stopped one from opting out. These do.
"""

from pathlib import Path
import re
import socket
import subprocess
import sys
from urllib.parse import urlparse

import pytest
from pytest_socket import SocketBlockedError

from custom_components.sensi.auth import OAUTH_URL, OAUTH_URL2
from custom_components.sensi.client import SOCKET_URL

_ROOT = Path(__file__).resolve().parents[1]

# Every backend host the integration talks to at runtime.
_BACKEND_HOSTS = sorted(
    {urlparse(url).hostname for url in (SOCKET_URL, OAUTH_URL, OAUTH_URL2)}
)

# A documentation address (RFC 5737): a literal, so no DNS lookup is involved
# and only the socket guard stands between the test and the network.
UNROUTABLE = ("203.0.113.1", 443)


def test_the_backend_hosts_are_the_ones_readme_names() -> None:
    """The hosts checked below include the one README names."""
    assert "rt.sensiapi.io" in _BACKEND_HOSTS
    assert all(host and host.endswith(".sensiapi.io") for host in _BACKEND_HOSTS)


@pytest.mark.parametrize("host", _BACKEND_HOSTS)
def test_resolving_a_backend_host_is_refused(host: str) -> None:
    """A lookup of the real backend fails before any packet leaves."""
    with pytest.raises(RuntimeError, match="DNS resolution disabled"):
        socket.getaddrinfo(host, 443)


# The guard warns each time it refuses; here the refusal is the point.
EXPECTED_REFUSAL = pytest.mark.filterwarnings(
    "ignore:A test tried to use socket.socket:UserWarning"
)


def swallow(error: SocketBlockedError) -> None:
    """Forget one refusal so this test's own cleanup check does not fail it.

    The cleanup check counts refusals on the error class; it is exercised in
    full by the subprocess test below.
    """
    type(error).instances.remove(error)


@EXPECTED_REFUSAL
def test_opening_an_internet_socket_is_refused() -> None:
    """An internet socket cannot be created at all, even to a literal address."""
    with pytest.raises(SocketBlockedError) as refused:
        socket.create_connection(UNROUTABLE, timeout=1)
    swallow(refused.value)


_SWALLOWING_TEST = """
import socket


def test_reaches_out_and_hides_it():
    try:
        socket.create_connection(("203.0.113.1", 443), timeout=1)
    except Exception:
        pass
"""


def test_a_refused_socket_fails_the_test_even_when_caught(tmp_path: Path) -> None:
    """A connection attempt the code catches still fails the test that made it."""
    (tmp_path / "pytest.ini").write_text("[pytest]\nasyncio_mode = auto\n")
    (tmp_path / "test_inner.py").write_text(_SWALLOWING_TEST)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "the test opens sockets" in output, output
    assert re.search(r"\b1 passed\b.*\b1 error\b", output), output


# --------------------------------------------------------------------------
# Nothing opts out
# --------------------------------------------------------------------------
#
# pytest-socket offers per-test escapes: the `socket_enabled` fixture, the
# `enable_socket` and `allow_hosts` markers, the same names as functions, and
# the `--force-enable-socket` / `--allow-hosts` options. A conftest could also
# empty the refusal list the cleanup check reads. Any of them would make README's
# sentence depend on which test reached out.

_OPT_OUTS = re.compile(
    r"\b(?:socket_enabled|enable_socket|allow_hosts|socket_allow_hosts)\b"
    r"|--force-enable-socket|--allow-hosts|-p\s*no:(?:socket|homeassistant)"
    # Emptying the refusal count that the cleanup check reads.
    r"|\bHASocketBlockedError\b|\.instances\b"
)


def _tracked(*patterns: str) -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--", *patterns],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return [_ROOT / path for path in listed]


def _opt_outs(paths: list[Path]) -> list[str]:
    this_file = Path(__file__).resolve()
    return [
        f"{path.relative_to(_ROOT)}:{number}: {line.strip()}"
        for path in paths
        if path.resolve() != this_file
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if _OPT_OUTS.search(line)
    ]


def test_the_opt_out_pattern_matches_every_escape() -> None:
    """The scan below would see each spelling of an opt-out."""
    for line in (
        "async def test_x(socket_enabled):",
        "@pytest.mark.enable_socket",
        '@pytest.mark.allow_hosts(["1.2.3.4"])',
        "pytest_socket.socket_allow_hosts(['1.2.3.4'])",
        "addopts = --force-enable-socket",
        "pytest --allow-hosts=1.2.3.4",
        "pytest -p no:socket",
        "pytest -pno:homeassistant",
        "HASocketBlockedError.instances = []",
        "type(error).instances.clear()",
    ):
        assert _OPT_OUTS.search(line), line


def test_no_test_or_configuration_opts_out_of_the_guard() -> None:
    """No committed test, conftest, pytest config or workflow lifts the guard."""
    scanned = _tracked(
        "tests/*.py",
        "pytest.ini",
        "setup.cfg",
        "pyproject.toml",
        "tox.ini",
        ".github/workflows/*.yml",
    )
    assert _ROOT / "tests" / "e2e" / "conftest.py" in scanned
    assert _ROOT / "pytest.ini" in scanned
    assert _opt_outs(scanned) == []
