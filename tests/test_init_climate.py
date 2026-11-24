"""Setup, climate entity and cloud/local hand-over tests."""

import asyncio
import json

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    PRESET_AWAY,
    PRESET_COMFORT,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.scinan_thermostat.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DOMAIN,
)

from .fake_cloud import PASSWORD, USERNAME
from .fake_thermostat import DEVICE_ID

LOCAL_ENTITY = "climate.thermostat_000a01"


def local_entry(device_id=DEVICE_ID, title="Thermostat 000A01") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=device_id,
        minor_version=2,
        title=title,
        data={CONF_CONNECTION_TYPE: CONNECTION_LOCAL, CONF_HOST: "127.0.0.1", "device_type": 9},
    )


def cloud_entry(**data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        minor_version=2,
        title=USERNAME,
        data={CONF_CONNECTION_TYPE: CONNECTION_CLOUD, CONF_USERNAME: USERNAME,
              CONF_PASSWORD: PASSWORD, **data},
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, service: str, entity: str, **data) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: entity, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def wait_for(predicate, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not predicate():  # noqa: ASYNC110 - polling HA state in a test
            await asyncio.sleep(0.02)


# ------------------------------------------------------------------ local


async def test_local_entity_state(hass: HomeAssistant, thermostat):
    await setup(hass, local_entry())
    state = hass.states.get(LOCAL_ENTITY)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 22.0
    assert state.attributes[ATTR_TEMPERATURE] == 22.0
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_COMFORT
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE
    assert state.attributes["min_temp"] == 5.0
    assert state.attributes["max_temp"] == 35.0
    assert state.attributes["connection"] == CONNECTION_LOCAL

    entity = er.async_get(hass).async_get(LOCAL_ENTITY)
    assert entity.unique_id == DEVICE_ID

    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.manufacturer == "Saswell"
    assert device.model == "Floor heating thermostat"
    assert device.model_id == "SAS920FHL"  # card: "Floor heating thermostat (SAS920FHL)"
    assert device.serial_number == DEVICE_ID


async def test_local_set_temperature(hass: HomeAssistant, thermostat):
    await setup(hass, local_entry())
    await call(hass, SERVICE_SET_TEMPERATURE, LOCAL_ENTITY, temperature=24.5)
    assert thermostat.state.target == 24.5
    state = hass.states.get(LOCAL_ENTITY)
    assert state.attributes[ATTR_TEMPERATURE] == 24.5
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING  # 22 < 24.5 (estimate)


async def test_local_off_is_away_and_temperature_cancels_it(hass: HomeAssistant, thermostat):
    await setup(hass, local_entry())

    await call(hass, SERVICE_TURN_OFF, LOCAL_ENTITY)
    state = hass.states.get(LOCAL_ENTITY)
    assert thermostat.state.away == 1
    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_AWAY
    assert state.attributes[ATTR_TEMPERATURE] == 5.0
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF

    await call(hass, SERVICE_TURN_ON, LOCAL_ENTITY)
    assert thermostat.state.away == 0
    assert hass.states.get(LOCAL_ENTITY).state == HVACMode.HEAT

    await call(hass, SERVICE_SET_HVAC_MODE, LOCAL_ENTITY, hvac_mode=HVACMode.OFF)
    assert thermostat.state.away == 1
    # Setting a temperature while away turns the thermostat back on (device behaviour).
    await call(hass, SERVICE_SET_TEMPERATURE, LOCAL_ENTITY, temperature=21.0)
    state = hass.states.get(LOCAL_ENTITY)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 21.0


@pytest.mark.parametrize(
    ("preset", "mode", "target"),
    [("Auto", 1, 16.0), ("Day or night", 2, 22.0), (PRESET_COMFORT, 0, 22.0)],
)
async def test_local_presets(hass: HomeAssistant, thermostat, preset, mode, target):
    await setup(hass, local_entry())
    await call(hass, SERVICE_SET_PRESET_MODE, LOCAL_ENTITY, preset_mode=PRESET_AWAY)
    assert thermostat.state.away == 1

    await call(hass, SERVICE_SET_PRESET_MODE, LOCAL_ENTITY, preset_mode=preset)
    state = hass.states.get(LOCAL_ENTITY)
    assert thermostat.state.away == 0
    assert thermostat.state.mode == mode
    assert state.attributes[ATTR_PRESET_MODE] == preset
    assert state.attributes[ATTR_TEMPERATURE] == target


async def test_local_push_updates_entity(hass: HomeAssistant, thermostat):
    await setup(hass, local_entry())
    thermostat.state.current = 23.5
    await thermostat.push_status()
    await wait_for(
        lambda: hass.states.get(LOCAL_ENTITY).attributes[ATTR_CURRENT_TEMPERATURE] == 23.5
    )


async def test_local_unavailable_when_connection_drops(hass: HomeAssistant, thermostat):
    entry = local_entry()
    await setup(hass, entry)
    await thermostat.stop()
    await wait_for(lambda: hass.states.get(LOCAL_ENTITY).state == STATE_UNAVAILABLE)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_local_not_ready_without_thermostat(hass: HomeAssistant, thermostat):
    await thermostat.stop()
    entry = local_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_local_not_ready_when_ip_is_other_thermostat(hass: HomeAssistant, thermostat):
    entry = local_entry(device_id="103800000000FFFF")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_local_unload_closes_connection(hass: HomeAssistant, thermostat):
    entry = local_entry()
    await setup(hass, entry)
    client = entry.runtime_data.client
    assert client.connected
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not client.connected


# ------------------------------------------------------------------ cloud


async def test_cloud_entities_and_control(hass: HomeAssistant, cloud):
    await setup(hass, cloud_entry())
    states = {s.entity_id: s for s in hass.states.async_all(CLIMATE_DOMAIN)}
    assert set(states) == {"climate.thermostat_000b02", "climate.thermostat_000c03"}
    main = states["climate.thermostat_000b02"]
    assert main.state == HVACMode.HEAT
    assert main.attributes[ATTR_TEMPERATURE] == 22.0
    assert main.attributes["connection"] == CONNECTION_CLOUD
    # Offline device (online=0 in the list) is unavailable
    assert states["climate.thermostat_000c03"].state == STATE_UNAVAILABLE

    lists_before = len(cloud.calls("/v2.0/device/list"))
    await call(hass, SERVICE_SET_TEMPERATURE, "climate.thermostat_000b02", temperature=21.3)
    [control] = cloud.calls("/v2.0/sensor/control")
    assert control["device_id"] == "1038000000000B02"
    assert control["sensor_id"] == "02"
    assert json.loads(control["control_data"]) == {"value": "21.5"}
    assert len(cloud.calls("/v2.0/device/list")) == lists_before + 1  # refreshed after command

    await call(hass, SERVICE_TURN_OFF, "climate.thermostat_000b02")
    control = cloud.calls("/v2.0/sensor/control")[-1]
    assert control["sensor_id"] == "03"  # away, not power, on type 9


async def test_cloud_auth_failure_starts_reauth(hass: HomeAssistant, cloud):
    entry = cloud_entry(password="wrong")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_cloud_not_ready_on_server_error(hass: HomeAssistant, cloud):
    cloud.queue("/v2.0/user/login", 500)
    entry = cloud_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_migrate_v1_entry(hass: HomeAssistant, cloud):
    """An entry from integration 1.x (no connection_type, v1 API settings) still loads."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=USERNAME,
        version=1,
        minor_version=1,
        title=USERNAME,
        data={
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            "api_version": "v1.0",
            "domain": "https://api.scinan.com",
            "api_token": "old-token",
        },
    )
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    assert entry.data[CONF_CONNECTION_TYPE] == CONNECTION_CLOUD
    assert "api_version" not in entry.data
    assert "domain" not in entry.data
    assert len(hass.states.async_all(CLIMATE_DOMAIN)) == 2


# ---------------------------------------------------------- cloud -> local


async def test_device_moves_from_cloud_to_local_entry(hass: HomeAssistant, cloud, thermostat):
    """Adding a local entry for a cloud thermostat keeps the entity (and its history)."""
    cloud_cfg = cloud_entry()
    await setup(hass, cloud_cfg)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(CLIMATE_DOMAIN, DOMAIN, "1038000000000B02")
    assert entity_id == "climate.thermostat_000b02"

    thermostat.device_id = "1038000000000B02"
    local_cfg = local_entry(device_id="1038000000000B02", title="Thermostat_000B02")
    await setup(hass, local_cfg)

    moved = registry.async_get(entity_id)
    assert moved.config_entry_id == local_cfg.entry_id
    assert moved.entity_id == entity_id
    assert hass.states.get(entity_id).attributes["connection"] == CONNECTION_LOCAL
    assert "1038000000000B02" not in cloud_cfg.runtime_data.data
    assert cloud_cfg.state is ConfigEntryState.LOADED

    # Removing the local entry gives the thermostat back to the cloud entry.
    await hass.config_entries.async_remove(local_cfg.entry_id)
    await hass.async_block_till_done()
    assert "1038000000000B02" in cloud_cfg.runtime_data.data
    assert hass.states.get(entity_id).attributes["connection"] == CONNECTION_CLOUD
