"""Water floor heating thermostats (SAS922WHL, device type 6), added by PR #4 / issue #2.

Type 6 turns off with the power command (S01); type 9 ignores it and uses away instead.
"""

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
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .test_init_climate import call, cloud_entry, local_entry, setup

WHL_ID = "1038000000000D04"
WHL_ENTITY = "climate.master_bedroom"
# Real type 6 device/list entry from issue #2 (device id replaced): powered off, mode 1 (Auto).
WHL_GROUP = {
    "type": 6,
    "devices": [
        {
            "id": WHL_ID,
            "title": "Master Bedroom",
            "company_id": "1038",
            "online": "1",
            "type": 6,
            "s00": "1689685928312,0,21.4,15.0,0,0,1,1,0,1,05,35",
        }
    ],
}


def last_control(cloud) -> tuple[str, str]:
    params = cloud.calls("/v2.0/sensor/control")[-1]
    return params["sensor_id"], json.loads(params["control_data"])["value"]


async def test_cloud_account_with_water_and_electric_thermostats(hass: HomeAssistant, cloud):
    """Both groups are read (the 1.x code only took the first matching group)."""
    cloud.device_list = [WHL_GROUP, *cloud.device_list]
    await setup(hass, cloud_entry())

    entities = {s.entity_id for s in hass.states.async_all(CLIMATE_DOMAIN)}
    assert entities == {WHL_ENTITY, "climate.thermostat_000b02", "climate.thermostat_000c03"}

    state = hass.states.get(WHL_ENTITY)
    assert state.state == HVACMode.OFF  # power field is 0
    assert state.attributes[ATTR_PRESET_MODE] == "Auto"
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 21.4
    assert state.attributes[ATTR_TEMPERATURE] == 15.0
    assert state.attributes["device_type"] == 6

    entity = er.async_get(hass).async_get(WHL_ENTITY)
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.model_id == "SAS922WHL"


async def test_cloud_water_heating_uses_power_for_on_off(hass: HomeAssistant, cloud):
    cloud.device_list = [WHL_GROUP]
    await setup(hass, cloud_entry())

    await call(hass, SERVICE_TURN_ON, WHL_ENTITY)
    assert last_control(cloud) == ("01", "1")
    await call(hass, SERVICE_TURN_OFF, WHL_ENTITY)
    assert last_control(cloud) == ("01", "0")
    await call(hass, SERVICE_SET_HVAC_MODE, WHL_ENTITY, hvac_mode=HVACMode.HEAT)
    assert last_control(cloud) == ("01", "1")
    await call(hass, SERVICE_SET_PRESET_MODE, WHL_ENTITY, preset_mode=PRESET_AWAY)
    assert last_control(cloud) == ("03", "1")  # away is still its own preset
    assert all(p["sensor_id"] != "03" for p in cloud.calls("/v2.0/sensor/control")[:-1])


async def test_local_water_heating_turns_off_with_power(hass: HomeAssistant, thermostat):
    thermostat.device_type = 6
    await setup(hass, local_entry())
    entity = "climate.thermostat_000a01"

    await call(hass, SERVICE_TURN_OFF, entity)
    state = hass.states.get(entity)
    assert thermostat.state.power == 0
    assert thermostat.state.away == 0  # not away
    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_COMFORT
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF

    await call(hass, SERVICE_TURN_ON, entity)
    assert thermostat.state.power == 1
    assert hass.states.get(entity).state == HVACMode.HEAT

    await call(hass, SERVICE_SET_PRESET_MODE, entity, preset_mode=PRESET_AWAY)
    state = hass.states.get(entity)
    assert thermostat.state.away == 1
    assert state.state == HVACMode.HEAT  # away doesn't mean off on type 6
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_AWAY


async def test_cloud_t13fhl_type_3(hass: HomeAssistant, cloud):
    """T13FHL-7(EN) WIFI reports type 3 (PR #11). Electric floor heating: Off is away."""
    cloud.device_list = [{"type": 3, "devices": [{
        "id": "1038000000000E05", "title": "Thermostat_000E05", "company_id": "1038",
        "online": "1", "type": 3, "s00": "1764000000000,1,21.0,22.0,0,0,0,0,0,0,05,35"}]}]
    await setup(hass, cloud_entry())
    entity = "climate.thermostat_000e05"
    state = hass.states.get(entity)
    assert state.state == HVACMode.HEAT
    assert state.attributes["device_type"] == 3
    device = dr.async_get(hass).async_get(er.async_get(hass).async_get(entity).device_id)
    assert device.model_id == "T13FHL"

    await call(hass, SERVICE_TURN_OFF, entity)
    assert last_control(cloud) == ("03", "1")  # away, not power

