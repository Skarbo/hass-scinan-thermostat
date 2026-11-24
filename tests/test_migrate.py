"""Moving cloud thermostats to local: options steps, Repairs, device clean-up."""

import json
from pathlib import Path

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.scinan_thermostat import async_remove_config_entry_device
from custom_components.scinan_thermostat.const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DOMAIN,
)

from .fake_thermostat import DEVICE_ID as STALE_ID
from .test_init_climate import cloud_entry, setup

MAIN_ID = "1038000000000B02"
OTHER_ID = "1038000000000C03"
MAIN_ENTITY = "climate.thermostat_000b02"
TRANSLATIONS = json.loads(
    (Path(__file__).parents[1] / "custom_components/scinan_thermostat/translations/en.json")
    .read_text()
)
OPTIONS_TEXTS = TRANSLATIONS["options"]
REPAIR_TEXTS = TRANSLATIONS["issues"]["local_available"]["fix_flow"]


# ------------------------------------------------------------------ helpers


def owners(device: dr.DeviceEntry) -> set[str]:
    """Config entries owning a device (HA 2026.11: exactly one, `config_entry_id`)."""
    if hasattr(dr.DeviceRegistry, "async_get_device_by_identifier"):
        return {device.config_entry_id}
    return set(device.config_entries)


def get_device(hass: HomeAssistant, device_id: str) -> dr.DeviceEntry | None:
    dev_reg = dr.async_get(hass)
    if hasattr(dev_reg, "async_get_devices"):
        devices = dev_reg.async_get_devices(identifiers={(DOMAIN, device_id)})
        assert len(devices) <= 1, devices  # never a duplicate device
        return devices[0] if devices else None
    return dev_reg.async_get_device(identifiers={(DOMAIN, device_id)})


def issue(hass: HomeAssistant, key: str, entry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{key}_{entry.entry_id}")


def local_entries(hass: HomeAssistant):
    return [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.data.get(CONF_CONNECTION_TYPE) == CONNECTION_LOCAL
    ]


def assert_translated(result, section: dict) -> None:
    """The frontend only shows translated labels: every field and error needs a text."""
    step = section["step"][result["step_id"]]
    assert step.get("description"), result["step_id"]
    for key in result["data_schema"].schema:
        assert str(key) in step.get("data", {}), f"no label for field {key!r}"
    for error in (result.get("errors") or {}).values():
        assert error in section["error"], f"no text for error {error!r}"


async def open_move_steps(hass: HomeAssistant, entry):
    """Options → Move to local → intro → first thermostat step."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "move_to_local"}
    )
    assert result["step_id"] == "move_to_local"
    assert_translated(result, OPTIONS_TEXTS)
    first = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert_translated(first, OPTIONS_TEXTS)
    return result, first


async def answer(hass: HomeAssistant, result, host: str | None):
    data = {CONF_HOST: host} if host else {}
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    if result["type"] is FlowResultType.FORM:
        assert_translated(result, OPTIONS_TEXTS)
    return result


# -------------------------------------------------------------------- tests


async def test_move_one_thermostat_keeps_names_and_history(hass: HomeAssistant, cloud, thermostat):
    thermostat.device_id = MAIN_ID
    cloud_cfg = cloud_entry()
    await setup(hass, cloud_cfg)
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    main_device = get_device(hass, MAIN_ID)
    dev_reg.async_update_device(main_device.id, name_by_user="Floor 08 - Main bedroom")
    assert issue(hass, "local_available", cloud_cfg) is not None

    intro, result = await open_move_steps(hass, cloud_cfg)
    names = intro["description_placeholders"]["names"]
    assert names.splitlines() == ["- Floor 08 - Main bedroom", "- Thermostat_000C03"]  # sorted

    assert result["step_id"] == "move_device"
    assert result["description_placeholders"]["name"] == "Floor 08 - Main bedroom"
    assert result["description_placeholders"]["device_id"] == MAIN_ID
    assert [str(k) for k in result["data_schema"].schema] == [CONF_HOST]
    result = await answer(hass, result, "127.0.0.1")

    assert result["description_placeholders"]["name"] == "Thermostat_000C03"
    assert result["last_step"] is True
    result = await answer(hass, result, None)  # keep it in the cloud
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    [local] = local_entries(hass)
    assert local.unique_id == MAIN_ID
    assert local.title == "Floor 08 - Main bedroom"
    assert local.data[CONF_HOST] == "127.0.0.1"
    assert local.state is ConfigEntryState.LOADED

    entity = ent_reg.async_get(MAIN_ENTITY)  # same entity id
    assert entity.config_entry_id == local.entry_id
    assert hass.states.get(MAIN_ENTITY).attributes["connection"] == CONNECTION_LOCAL
    device = get_device(hass, MAIN_ID)
    assert device.name_by_user == "Floor 08 - Main bedroom"
    assert owners(device) == {local.entry_id}  # no longer under the cloud hub
    assert entity.device_id == device.id

    # The other thermostat is still in the cloud, so the notice stays.
    assert issue(hass, "local_available", cloud_cfg) is not None
    assert issue(hass, "cloud_empty", cloud_cfg) is None


async def test_move_step_errors(hass: HomeAssistant, cloud, thermostat):
    thermostat.device_id = MAIN_ID
    cloud_cfg = cloud_entry()
    await setup(hass, cloud_cfg)

    _, result = await open_move_steps(hass, cloud_cfg)
    assert result["description_placeholders"]["device_id"] == MAIN_ID
    thermostat.send_hello = thermostat.answer_commands = False  # connects, but stays silent
    result = await answer(hass, result, "127.0.0.1")
    assert result["errors"] == {CONF_HOST: "no_answer"}
    result = await answer(hass, result, "192.168.1")
    assert result["errors"] == {CONF_HOST: "invalid_host"}
    assert result["description_placeholders"]["device_id"] == MAIN_ID  # same step again
    thermostat.send_hello = thermostat.answer_commands = True
    result = await answer(hass, result, "127.0.0.1")  # answers now: on to the next one

    assert result["description_placeholders"]["device_id"] == OTHER_ID
    result = await answer(hass, result, "127.0.0.1")
    assert result["errors"] == {CONF_HOST: "duplicate_ip"}
    assert local_entries(hass) == []


async def test_wrong_thermostat_at_ip(hass: HomeAssistant, cloud, thermostat):
    thermostat.device_id = MAIN_ID
    cloud_cfg = cloud_entry()
    await setup(hass, cloud_cfg)
    _, result = await open_move_steps(hass, cloud_cfg)
    result = await answer(hass, result, None)  # skip MAIN
    result = await answer(hass, result, "127.0.0.1")  # MAIN's IP entered for OTHER
    assert result["errors"] == {CONF_HOST: "wrong_device"}


async def test_nothing_entered_changes_nothing(hass: HomeAssistant, cloud, thermostat):
    cloud_cfg = cloud_entry()
    await setup(hass, cloud_cfg)
    _, result = await open_move_steps(hass, cloud_cfg)
    result = await answer(hass, result, None)
    result = await answer(hass, result, None)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert local_entries(hass) == []
    assert issue(hass, "local_available", cloud_cfg) is not None


async def test_stale_cloud_device_can_be_moved_or_deleted(hass: HomeAssistant, cloud, thermostat):
    """A thermostat the cloud account lost (like after a Wi-Fi change) is still offered."""
    cloud_cfg = cloud_entry()
    cloud_cfg.add_to_hass(hass)
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    stale = dev_reg.async_get_or_create(
        config_entry_id=cloud_cfg.entry_id,
        identifiers={(DOMAIN, STALE_ID)},
        name="Thermostat_000A01",
    )
    dev_reg.async_update_device(stale.id, name_by_user="Floor 07 - Kids room")
    ent_reg.async_get_or_create(
        CLIMATE_DOMAIN, DOMAIN, STALE_ID, config_entry=cloud_cfg, device_id=stale.id,
        suggested_object_id="floor_07_kids_room",
    )
    assert await hass.config_entries.async_setup(cloud_cfg.entry_id)
    await hass.async_block_till_done()

    main = get_device(hass, MAIN_ID)
    assert await async_remove_config_entry_device(hass, cloud_cfg, stale)  # deletable
    assert not await async_remove_config_entry_device(hass, cloud_cfg, main)  # still in cloud

    _, result = await open_move_steps(hass, cloud_cfg)
    assert result["description_placeholders"]["name"] == "Floor 07 - Kids room"  # sorted first
    result = await answer(hass, result, "127.0.0.1")  # the fake is STALE_ID
    result = await answer(hass, result, None)
    result = await answer(hass, result, None)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    [local] = local_entries(hass)
    entity = ent_reg.async_get("climate.floor_07_kids_room")
    assert entity.config_entry_id == local.entry_id
    assert hass.states.get("climate.floor_07_kids_room").state == "heat"
    device = get_device(hass, STALE_ID)
    assert device.name_by_user == "Floor 07 - Kids room"
    assert owners(device) == {local.entry_id}
    assert entity.device_id == device.id


async def test_repairs_move_then_remove_empty_cloud_hub(
    hass: HomeAssistant, hass_client, cloud, thermostat
):
    """Account with one thermostat: Repairs moves it, then offers to delete the hub."""
    thermostat.device_id = MAIN_ID
    cloud.device_list = [{"type": "9", "devices": [cloud.device_list[0]["devices"][0]]}]
    assert await async_setup_component(hass, "repairs", {})
    cloud_cfg = cloud_entry()
    await setup(hass, cloud_cfg)
    client = await hass_client()

    async def fix(issue_key: str, *answers: dict):
        resp = await client.post(
            "/api/repairs/issues/fix",
            json={"handler": DOMAIN, "issue_id": f"{issue_key}_{cloud_cfg.entry_id}"},
        )
        flow = await resp.json()
        for data in answers:
            assert flow["type"] == "form", flow
            texts = REPAIR_TEXTS if issue_key == "local_available" else None
            if texts:
                step = texts["step"][flow["step_id"]]
                assert all(f["name"] in step.get("data", {}) for f in flow["data_schema"])
            resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json=data)
            flow = await resp.json()
        return flow

    assert issue(hass, "local_available", cloud_cfg) is not None
    # Nothing entered: the notice stays.
    flow = await fix("local_available", {}, {})
    assert flow["type"] == "abort" and flow["reason"] == "nothing_moved"
    assert issue(hass, "local_available", cloud_cfg) is not None

    flow = await fix("local_available", {}, {CONF_HOST: "127.0.0.1"})
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert len(local_entries(hass)) == 1
    assert issue(hass, "local_available", cloud_cfg) is None
    assert issue(hass, "cloud_empty", cloud_cfg) is not None

    flow = await fix("cloud_empty", {})
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(cloud_cfg.entry_id) is None
    [local] = local_entries(hass)
    assert local.state is ConfigEntryState.LOADED
    assert hass.states.get(MAIN_ENTITY).attributes["connection"] == CONNECTION_LOCAL
    assert issue(hass, "cloud_empty", cloud_cfg) is None


async def test_no_move_option_when_everything_is_local(hass: HomeAssistant, cloud, thermostat):
    thermostat.device_id = MAIN_ID
    cloud.device_list = [{"type": "9", "devices": [cloud.device_list[0]["devices"][0]]}]
    cloud_cfg = cloud_entry()
    await setup(hass, cloud_cfg)
    _, result = await open_move_steps(hass, cloud_cfg)
    await answer(hass, result, "127.0.0.1")
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(cloud_cfg.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "move_to_local"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "nothing_to_move"
    assert cloud_cfg.data[CONF_CONNECTION_TYPE] == CONNECTION_CLOUD


def test_all_texts_exist():
    """Repairs and options use the same step and error texts."""
    for section in (OPTIONS_TEXTS, REPAIR_TEXTS):
        assert section["step"]["move_device"]["data"]["host"] == "IP address"
        assert "{name}" in section["step"]["move_device"]["description"]
        assert "{names}" in section["step"]["move_to_local"]["description"]
        assert set(section["error"]) >= {"cannot_connect", "wrong_device", "duplicate_ip"}
        assert set(section["abort"]) >= {"nothing_to_move", "nothing_moved"}
