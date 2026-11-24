"""Config flow tests."""

import socket
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import (
    CONF_API_TOKEN,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.scinan_thermostat.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DOMAIN,
)

from .fake_cloud import PASSWORD, TOKEN, USERNAME
from .fake_thermostat import DEVICE_ID


async def _start(hass: HomeAssistant, choice: str):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"local", "cloud"}
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": choice}
    )


def _local_entry(host="127.0.0.1", device_id=DEVICE_ID) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        minor_version=2,
        title=f"Thermostat {device_id[-6:]}",
        data={CONF_CONNECTION_TYPE: CONNECTION_LOCAL, CONF_HOST: host, "device_type": 9},
    )


# ------------------------------------------------------------------ local


async def test_local_creates_entry(hass: HomeAssistant, thermostat):
    result = await _start(hass, "local")
    assert result["step_id"] == "local"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " 127.0.0.1 "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Thermostat 000A01"
    assert result["result"].unique_id == DEVICE_ID
    assert result["data"] == {
        CONF_CONNECTION_TYPE: CONNECTION_LOCAL,
        CONF_HOST: "127.0.0.1",
        "device_type": 9,
    }


async def test_local_port_closed(hass: HomeAssistant, thermostat):
    await thermostat.stop()  # nothing listens on the port any more
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "connection_refused"}


@pytest.mark.parametrize(
    ("typed", "error"),
    [
        ("192.168.1", "invalid_host"),
        ("192.168.1.300", "invalid_host"),
        ("not an ip", "invalid_host"),
    ],
)
async def test_local_bad_input(hass: HomeAssistant, thermostat, typed, error):
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: typed})
    assert result["errors"] == {CONF_HOST: error}


async def test_local_unknown_host_name(hass: HomeAssistant, thermostat):
    async def no_such_name(*args, **kwargs):
        raise socket.gaierror(-2, "Name does not resolve")

    result = await _start(hass, "local")
    with patch("pyscinan.local.asyncio.open_connection", no_such_name):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "fpfpfp"}
        )
    assert result["errors"] == {CONF_HOST: "unknown_host"}


async def test_local_input_is_cleaned_up(hass: HomeAssistant, thermostat):
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " http://127.0.0.1:2000/\u00a0"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "127.0.0.1"


async def test_local_silent_thermostat(hass: HomeAssistant, thermostat):
    thermostat.send_hello = thermostat.answer_commands = False
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["errors"] == {CONF_HOST: "no_answer"}


async def test_local_missing_first_status_still_works(hass: HomeAssistant, thermostat):
    """pyscinan 0.1.1 asks for the status a second time (seen once on real hardware)."""
    thermostat.ignore_status_requests = 1
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_local_already_configured_updates_host(hass: HomeAssistant, thermostat):
    entry = _local_entry(host="10.0.0.99")
    entry.add_to_hass(hass)
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "127.0.0.1"


async def test_reconfigure_local(hass: HomeAssistant, thermostat):
    entry = _local_entry(host="10.0.0.99")
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "127.0.0.1"
    await hass.async_block_till_done()  # the reload runs in the background
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_reconfigure_local_wrong_device(hass: HomeAssistant, thermostat):
    entry = _local_entry(host="10.0.0.99", device_id="103800000000FFFF")
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert entry.data[CONF_HOST] == "10.0.0.99"


# ------------------------------------------------------------------ cloud


async def test_cloud_creates_entry(hass: HomeAssistant, cloud):
    result = await _start(hass, "cloud")
    assert result["step_id"] == "cloud"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["data"][CONF_CONNECTION_TYPE] == CONNECTION_CLOUD
    assert result["data"][CONF_API_TOKEN] == TOKEN
    await hass.async_block_till_done()


async def test_cloud_invalid_auth(hass: HomeAssistant, cloud):
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cloud_cannot_connect(hass: HomeAssistant, cloud):
    cloud.queue("/v2.0/user/login", 500)
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_cloud_already_configured(hass: HomeAssistant, cloud):
    MockConfigEntry(domain=DOMAIN, unique_id=USERNAME, data={}).add_to_hass(hass)
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_cloud_reauth(hass: HomeAssistant, cloud):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        minor_version=2,
        data={CONF_CONNECTION_TYPE: CONNECTION_CLOUD, CONF_USERNAME: USERNAME,
              CONF_PASSWORD: "old", CONF_API_TOKEN: None},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == PASSWORD
    assert entry.data[CONF_API_TOKEN] == TOKEN
    await hass.async_block_till_done()


async def test_cloud_options(hass: HomeAssistant, cloud):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        minor_version=2,
        data={CONF_CONNECTION_TYPE: CONNECTION_CLOUD, CONF_USERNAME: USERNAME,
              CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"move_to_local", "settings"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {CONF_SCAN_INTERVAL: 120}
    assert entry.runtime_data.update_interval.total_seconds() == 120


async def test_local_has_no_options(hass: HomeAssistant):
    entry = _local_entry()
    entry.add_to_hass(hass)
    from custom_components.scinan_thermostat.config_flow import ScinanConfigFlow

    assert not ScinanConfigFlow.async_supports_options_flow(entry)


async def test_reconfigure_cloud_updates_password(hass: HomeAssistant, cloud):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        minor_version=2,
        data={CONF_CONNECTION_TYPE: CONNECTION_CLOUD, CONF_USERNAME: USERNAME,
              CONF_PASSWORD: "old-password", CONF_API_TOKEN: None},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure_cloud"
    assert result["description_placeholders"] == {"username": USERNAME}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "old-password"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PASSWORD] == PASSWORD
    assert entry.data[CONF_API_TOKEN] == TOKEN
    assert entry.data[CONF_USERNAME] == USERNAME
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED

