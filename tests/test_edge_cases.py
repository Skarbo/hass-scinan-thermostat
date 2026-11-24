"""Error paths and edge cases: command failures, availability logging, odd input."""

import asyncio
import copy
import logging
from unittest.mock import patch

from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
    PRESET_COMFORT,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed
from pyscinan import ScinanLocalClient, ScinanProtocolError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.scinan_thermostat import async_remove_config_entry_device
from custom_components.scinan_thermostat.const import DOMAIN
from custom_components.scinan_thermostat.repairs import (
    MoveToLocalRepairFlow,
    RemoveCloudEntryRepairFlow,
)

from .fake_cloud import USERNAME, error
from .test_config_flow import _start
from .test_init_climate import LOCAL_ENTITY, call, cloud_entry, local_entry, setup, wait_for

MAIN_ENTITY = "climate.thermostat_000b02"
CONTROL = "/v2.0/sensor/control"


# ---------------------------------------------------------------- history


async def test_repeated_status_pushes_dont_write_new_states(hass: HomeAssistant, thermostat):
    """The thermostat repeats its status every 15 s; HA should only see real changes."""
    await setup(hass, local_entry())
    first = hass.states.get(LOCAL_ENTITY)
    assert "last_updated" not in first.attributes
    for _ in range(3):
        await thermostat.push_status()  # same status again
    await asyncio.sleep(0.2)
    assert hass.states.get(LOCAL_ENTITY).last_updated == first.last_updated

    thermostat.state.current = 22.5
    await thermostat.push_status()
    await wait_for(lambda: hass.states.get(LOCAL_ENTITY).last_updated != first.last_updated)


async def test_availability_is_logged_once(hass: HomeAssistant, thermostat, caplog):
    caplog.set_level(logging.INFO, logger="custom_components.scinan_thermostat")
    await setup(hass, local_entry())
    await thermostat.drop_clients()  # connection lost; the client reconnects by itself
    await wait_for(lambda: "is unavailable" in caplog.text)
    await wait_for(lambda: "is available again" in caplog.text, timeout=6)
    assert caplog.text.count("is unavailable") == 1
    assert hass.states.get(LOCAL_ENTITY).state != STATE_UNAVAILABLE


# --------------------------------------------------------------- commands


async def test_cloud_command_auth_failure_starts_reauth(hass: HomeAssistant, cloud):
    entry = cloud_entry()
    await setup(hass, entry)
    cloud.queue(CONTROL, error("20007", "wrong password"))
    with pytest.raises(HomeAssistantError, match="authentication"):
        await call(hass, SERVICE_SET_TEMPERATURE, MAIN_ENTITY, temperature=21)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_cloud_command_error(hass: HomeAssistant, cloud):
    await setup(hass, cloud_entry())
    cloud.queue(CONTROL, error("20001", "device not exists"))
    with pytest.raises(HomeAssistantError, match="Cloud command failed"):
        await call(hass, SERVICE_SET_TEMPERATURE, MAIN_ENTITY, temperature=21)


async def test_cloud_preset_sets_mode(hass: HomeAssistant, cloud):
    await setup(hass, cloud_entry())
    await call(hass, SERVICE_SET_PRESET_MODE, MAIN_ENTITY, preset_mode=PRESET_COMFORT)
    assert cloud.calls(CONTROL)[-1]["sensor_id"] == "12"


async def test_cloud_all_offline_warning(hass: HomeAssistant, cloud, caplog):
    cloud.device_list = copy.deepcopy(cloud.device_list)  # don't change the shared list
    for group in cloud.device_list:
        for dev in group["devices"]:
            dev["online"] = "0"
    await setup(hass, cloud_entry())
    assert "devices online" in caplog.text


async def test_local_command_when_disconnected(hass: HomeAssistant, thermostat):
    entry = local_entry()
    await setup(hass, entry)
    await entry.runtime_data.client.close()
    with pytest.raises(HomeAssistantError, match="not connected"):
        await entry.runtime_data.async_set_temperature("x", 21)


async def test_set_temperature_without_temperature(hass: HomeAssistant, thermostat):
    await setup(hass, local_entry())
    entity = hass.data[CLIMATE_DOMAIN].get_entity(LOCAL_ENTITY)
    await entity.async_set_temperature()  # nothing to do
    assert thermostat.state.target == 22.0


# ------------------------------------------------------------- config flow


async def test_cloud_login_unexpected_error(hass: HomeAssistant, cloud):
    result = await _start(hass, "cloud")
    with patch("pyscinan.cloud.ScinanCloudClient.authenticate", side_effect=RuntimeError):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": USERNAME, "password": "x"}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_local_entry_is_named_after_cloud_thermostat(hass: HomeAssistant, cloud, thermostat):
    await setup(hass, cloud_entry())
    thermostat.device_id = "1038000000000B02"
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["title"] == "Thermostat_000B02"
    await hass.async_block_till_done()


async def test_reconfigure_local_cannot_connect(hass: HomeAssistant, thermostat):
    entry = local_entry()
    entry.add_to_hass(hass)
    await thermostat.stop()
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1"}
    )
    assert result["errors"] == {CONF_HOST: "connection_refused"}


@pytest.mark.parametrize(
    ("side_effect", "error_key"),
    [(ScinanProtocolError("garbage"), "not_a_thermostat"), (RuntimeError, "unknown")],
)
async def test_local_probe_unusual_errors(hass: HomeAssistant, thermostat, side_effect, error_key):
    result = await _start(hass, "local")
    with patch("pyscinan.local.ScinanLocalClient.connect", side_effect=side_effect):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1"}
        )
    assert result["errors"] == {CONF_HOST: error_key}


async def test_empty_address_after_cleanup(hass: HomeAssistant, thermostat):
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http://"}
    )
    assert result["errors"] == {CONF_HOST: "invalid_host"}


# ----------------------------------------------------------------- setup


async def test_local_first_refresh_failure_closes_connection(hass: HomeAssistant, thermostat):
    entry = local_entry()
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.scinan_thermostat.coordinator.ScinanLocalCoordinator."
            "_async_update_data",
            side_effect=UpdateFailed("boom"),
        ),
        patch("pyscinan.local.ScinanLocalClient.close", autospec=True,
              side_effect=ScinanLocalClient.close) as close,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert close.called


async def test_local_device_cannot_be_deleted_from_device_page(hass: HomeAssistant, thermostat):
    entry = local_entry()
    await setup(hass, entry)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert not await async_remove_config_entry_device(hass, entry, devices[0])


async def test_entry_from_a_newer_version_is_not_loaded(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, version=2, data={})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


# --------------------------------------------------------------- repairs


async def test_repair_flows_for_a_removed_cloud_entry(hass: HomeAssistant):
    move = MoveToLocalRepairFlow("gone")
    move.hass = hass
    result = await move.async_step_init()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_removed"

    remove = RemoveCloudEntryRepairFlow("gone")
    remove.hass = hass
    result = await remove.async_step_init()
    assert result["type"] is FlowResultType.CREATE_ENTRY
