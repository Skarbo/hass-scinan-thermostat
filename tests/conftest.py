"""Fixtures: a fake thermostat (local TCP) and a fake Saswell cloud on localhost."""

from __future__ import annotations

from functools import partial
from unittest.mock import patch

from pyscinan import ScinanCloudClient, ScinanLocalClient
import pytest

from .fake_cloud import FakeCloud
from .fake_thermostat import FakeThermostat

INTEGRATION = "custom_components.scinan_thermostat"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations, socket_enabled):
    """Load our custom integration; allow real sockets to the localhost fakes."""
    return


@pytest.fixture(autouse=True)
def fast_cloud_refresh():
    with patch(f"{INTEGRATION}.coordinator.CLOUD_REFRESH_DELAY", 0):
        yield


@pytest.fixture
async def thermostat():
    dev = FakeThermostat()
    await dev.start()
    local = partial(ScinanLocalClient, port=dev.port, connect_timeout=1.0, command_timeout=0.5)
    with (
        patch(f"{INTEGRATION}.ScinanLocalClient", local),
        patch(f"{INTEGRATION}.migrate.ScinanLocalClient", local),
    ):
        yield dev
    await dev.stop()


@pytest.fixture
async def cloud():
    srv = FakeCloud()
    await srv.start()
    client = partial(ScinanCloudClient, api_domain=srv.url, timeout=2)
    with (
        patch(f"{INTEGRATION}.ScinanCloudClient", client),
        patch(f"{INTEGRATION}.config_flow.ScinanCloudClient", client),
        patch("pyscinan.cloud._RETRY_DELAY", 0),
    ):
        yield srv
    await srv.stop()
