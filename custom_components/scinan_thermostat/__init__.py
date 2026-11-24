"""Scinan Saswell thermostat integration (cloud or local)."""

from __future__ import annotations

import logging
from typing import cast

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_API_TOKEN,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyscinan import ScinanCloudClient, ScinanError, ScinanLocalClient

from .const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .coordinator import (
    ScinanCloudCoordinator,
    ScinanConfigEntry,
    ScinanCoordinator,
    ScinanLocalCoordinator,
)
from .migrate import cloud_thermostats, device_id_of, is_cloud_entry, local_device_ids

ISSUE_LOCAL_AVAILABLE = "local_available"
ISSUE_CLOUD_EMPTY = "cloud_empty"

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: ScinanConfigEntry) -> bool:
    """Set up a cloud account or a local thermostat."""
    coordinator: ScinanCoordinator
    if entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_LOCAL:
        coordinator = await _async_setup_local(hass, entry)
    else:
        coordinator = await _async_setup_cloud(hass, entry)

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    if not is_cloud_entry(entry):
        # Before the entity is added: move the thermostat's device from a cloud hub
        # to this entry, so it keeps its name and area (HA 2026.11+).
        _async_take_over_device(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if not is_cloud_entry(entry) and not _DEVICE_HAS_SINGLE_ENTRY:
        # Older HA: the device is shared by both entries now; drop the cloud hub.
        _async_detach_from_cloud(hass, entry)
    async_update_issues(hass, entry)
    return True


async def _async_setup_cloud(
    hass: HomeAssistant, entry: ScinanConfigEntry
) -> ScinanCloudCoordinator:
    client = ScinanCloudClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        token=entry.data.get(CONF_API_TOKEN),
        session=async_get_clientsession(hass),
    )
    coordinator = ScinanCloudCoordinator(
        hass,
        entry,
        client,
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_UPDATE_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()
    if client.token and client.token != entry.data.get(CONF_API_TOKEN):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_API_TOKEN: client.token}
        )
    return coordinator


async def _async_setup_local(
    hass: HomeAssistant, entry: ScinanConfigEntry
) -> ScinanLocalCoordinator:
    host = entry.data[CONF_HOST]
    client = ScinanLocalClient(host)
    try:
        device = await client.connect()
    except ScinanError as err:
        raise ConfigEntryNotReady(f"Cannot connect to thermostat at {host}: {err}") from err

    if device.device_id != entry.unique_id:
        await client.close()
        raise ConfigEntryNotReady(
            f"{host} is thermostat {device.device_id}, expected {entry.unique_id}. "
            "Reconfigure the entry with the right IP address."
        )

    coordinator = ScinanLocalCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await client.close()
        raise

    # A cloud entry may still show this thermostat; reload it so the device
    # moves to this local entry (same unique id, so the entity and its history stay).
    # This must finish before our climate platform adds the entity.
    for other in _loaded_cloud_entries(hass):
        if entry.unique_id in other.runtime_data.data:
            _LOGGER.debug("Moving %s from cloud entry %s to local", entry.unique_id, other.title)
            await hass.config_entries.async_reload(other.entry_id)
    return coordinator


# HA 2026.11 made a device belong to a single config entry (and added
# async_get_device_by_identifier + new_config_entry_id to move it). Older HA
# versions let a device belong to several entries.
_DEVICE_HAS_SINGLE_ENTRY = hasattr(dr.DeviceRegistry, "async_get_device_by_identifier")


@callback
def _async_take_over_device(hass: HomeAssistant, entry: ScinanConfigEntry) -> None:
    if not _DEVICE_HAS_SINGLE_ENTRY or entry.unique_id is None:
        return
    dev_reg = dr.async_get(hass)
    identifier = (DOMAIN, entry.unique_id)
    if dev_reg.async_get_device_by_identifier(identifier, entry.entry_id) is not None:
        return
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == entry.entry_id or not is_cloud_entry(other):
            continue
        device = dev_reg.async_get_device_by_identifier(identifier, other.entry_id)
        if device is not None:
            _LOGGER.debug("Moving device %s from cloud entry %s", entry.unique_id, other.title)
            dev_reg.async_update_device(device.id, new_config_entry_id=entry.entry_id)
            return


@callback
def _async_detach_from_cloud(hass: HomeAssistant, entry: ScinanConfigEntry) -> None:
    """HA < 2026.11 only."""
    if entry.unique_id is None:
        return
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, entry.unique_id)})
    if device is None:
        return
    for other_id in device.config_entries - {entry.entry_id}:
        other = hass.config_entries.async_get_entry(other_id)
        if other is not None and other.domain == DOMAIN and is_cloud_entry(other):
            _LOGGER.debug("Detaching %s from cloud entry %s", entry.unique_id, other.title)
            dev_reg.async_update_device(device.id, remove_config_entry_id=other_id)


@callback
def async_update_issues(hass: HomeAssistant, current: ScinanConfigEntry | None = None) -> None:
    """Repairs: suggest moving cloud thermostats to local, and removing empty cloud hubs.

    `current` is the entry being set up (not LOADED yet, but its runtime_data is set).
    """
    local = local_device_ids(hass)
    entries = _loaded_cloud_entries(hass)
    if current is not None and is_cloud_entry(current) and current not in entries:
        entries.append(current)
    for entry in entries:
        coordinator = cast(ScinanCloudCoordinator, entry.runtime_data)
        remaining = cloud_thermostats(hass, entry)
        account = coordinator.account_device_ids
        _set_issue(hass, entry, ISSUE_LOCAL_AVAILABLE, bool(remaining))
        _set_issue(
            hass,
            entry,
            ISSUE_CLOUD_EMPTY,
            not remaining and bool(account) and account <= local,
        )


@callback
def _set_issue(hass: HomeAssistant, entry: ConfigEntry, key: str, active: bool) -> None:
    issue_id = f"{key}_{entry.entry_id}"
    if not active:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=key,
        translation_placeholders={"account": entry.title},
        data={"entry_id": entry.entry_id},
    )


def _loaded_cloud_entries(hass: HomeAssistant) -> list[ScinanConfigEntry]:
    return [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.data.get(CONF_CONNECTION_TYPE, CONNECTION_CLOUD) == CONNECTION_CLOUD
        and other.state is ConfigEntryState.LOADED
    ]


async def _async_update_listener(hass: HomeAssistant, entry: ScinanConfigEntry) -> None:
    """Options changed: reload."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ScinanConfigEntry) -> bool:
    """Unload an entry and close the connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Entry deleted. Local: let cloud entries show the thermostat again."""
    if entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_LOCAL:
        for other in _loaded_cloud_entries(hass):
            hass.config_entries.async_schedule_reload(other.entry_id)
    else:
        for key in (ISSUE_LOCAL_AVAILABLE, ISSUE_CLOUD_EMPTY):
            ir.async_delete_issue(hass, DOMAIN, f"{key}_{entry.entry_id}")


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ScinanConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting a thermostat the cloud account no longer has (stale device)."""
    if not is_cloud_entry(entry):
        return False
    return device_id_of(device) not in entry.runtime_data.data


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.1 (cloud only) -> 1.2 (connection_type stored)."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        # v1.x entries were cloud only and could pick the Scinan v1.0 API, which is
        # gone. Drop the API version/domain; everything uses Saswell v2.0 now.
        data = {
            k: v for k, v in entry.data.items() if k not in ("api_version", "domain")
        }
        hass.config_entries.async_update_entry(
            entry,
            data={CONF_CONNECTION_TYPE: CONNECTION_CLOUD, **data},
            minor_version=2,
        )
        _LOGGER.debug("Migrated %s to minor version 2", entry.title)
    return True
