"""Scinan thermostat integration."""
import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .data_coordinator import ScinanDataUpdateCoordinator
from .scinan import ScinanApi, ScinanAuthFailed

PLATFORMS = [Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Saswell thermostat."""
    hass.data.setdefault(DOMAIN, {})

    username = entry.data[CONF_USERNAME]
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

    scinan_data_coordinator = ScinanDataUpdateCoordinator(hass, scinan_api)
    hass.data[DOMAIN][username] = scinan_data_coordinator
    await scinan_data_coordinator.async_config_entry_first_refresh()

    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
