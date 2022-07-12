"""Scinan thermostat integration."""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL, CONF_TOKEN,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .data_coordinator import ScinanDataUpdateCoordinator
from .scinan import ScinanApi, ScinanAuthFailed

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.CLIMATE]


async def _async_update_listener(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Handle options update."""
    username = entry.data[CONF_USERNAME]
    update_interval = entry.options.get(CONF_SCAN_INTERVAL)
    scinan_data_coordinator: ScinanDataUpdateCoordinator = (
        hass.data[DOMAIN][username]
    )

    if (
        scinan_data_coordinator is not None
        and isinstance(update_interval, int)
    ):
        await scinan_data_coordinator.set_update_interval(update_interval)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Saswell thermostat."""
    hass.data.setdefault(DOMAIN, {})

    username = entry.data[CONF_USERNAME]
    update_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        DEFAULT_UPDATE_INTERVAL
    )
    scinan_api = ScinanApi(
        username,
        entry.data[CONF_PASSWORD],
        token=entry.data[CONF_TOKEN],
        web_session=async_get_clientsession(hass),
    )

    try:
        await scinan_api.authenticate()
    except ScinanAuthFailed as err:
        raise ConfigEntryAuthFailed(
            f"Authenticate failed for {username}",
        ) from err
    except asyncio.TimeoutError as ex:
        raise ConfigEntryNotReady(
            f"Authenticate timed out for {username}",
        ) from ex

    scinan_data_coordinator = ScinanDataUpdateCoordinator(
        hass,
        scinan_api,
        update_interval=update_interval,
    )
    hass.data[DOMAIN][username] = scinan_data_coordinator
    await scinan_data_coordinator.async_config_entry_first_refresh()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
