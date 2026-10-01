"""The network guard still holds once the integration is running.

`tests/test_ci_network_guard.py` explains why README can say "Nothing in CI
reaches `rt.sensiapi.io`". This repeats its two runtime checks inside the e2e
tier, with Home Assistant started and the integration set up through
`sensi_entry`, so a fixture in `tests/e2e/conftest.py` that lifted the guard
would fail here.
"""

import socket

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_socket import SocketBlockedError

from homeassistant.core import HomeAssistant
from tests.test_ci_network_guard import EXPECTED_REFUSAL, UNROUTABLE, swallow


@EXPECTED_REFUSAL
async def test_the_guard_holds_with_the_integration_loaded(
    hass: HomeAssistant, sensi_entry: MockConfigEntry
) -> None:
    """Neither the backend lookup nor an internet socket gets through."""
    with pytest.raises(RuntimeError, match="DNS resolution disabled"):
        await hass.async_add_executor_job(socket.getaddrinfo, "rt.sensiapi.io", 443)
    with pytest.raises(SocketBlockedError) as refused:
        await hass.async_add_executor_job(
            lambda: socket.create_connection(UNROUTABLE, timeout=1)
        )
    swallow(refused.value)
