"""Scinan Saswell thermostat integration."""
import asyncio
import logging

from aiohttp import ClientOSError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_API_VERSION,
    CONF_DOMAIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_API_TOKEN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ScinanApi,
    ScinanAuthFailed,
)
from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .data_coordinator import ScinanDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.CLIMATE]


async def _async_update_listener(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Handle options update."""
    username = entry.data.get(CONF_USERNAME)
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
    """Set up Scinan Saswell thermostat."""
    hass.data.setdefault(DOMAIN, {})
    username = entry.data.get(CONF_USERNAME)
    update_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        DEFAULT_UPDATE_INTERVAL
    )

    scinan_api = ScinanApi(
        username,
        entry.data.get(CONF_PASSWORD),
        token=entry.data.get(CONF_API_TOKEN),
        web_session=async_get_clientsession(hass),
        api_version=entry.data.get(CONF_API_VERSION),
        api_domain=entry.data.get(CONF_DOMAIN),
    )

    try:
        await asyncio.sleep(3)  # wait a bit
        await scinan_api.update_devices()
    except ScinanAuthFailed as err:
        raise ConfigEntryAuthFailed(
            f"Authenticate failed for {username}",
        ) from err
    except asyncio.TimeoutError as err:
        raise ConfigEntryNotReady(
            f"Authenticate timed out for {username}",
        ) from err
    except ClientOSError as err:
        raise ConfigEntryNotReady(
            f"Connection reset for {username}",
        ) from err

    scinan_data_coordinator = ScinanDataUpdateCoordinator(
        hass,
        scinan_api,
        update_interval=update_interval,
    )
    hass.data[DOMAIN][username] = scinan_data_coordinator
    await scinan_data_coordinator.async_config_entry_first_refresh()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    for platform in PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, [platform])

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    for platform in PLATFORMS:
        await hass.config_entries.async_forward_entry_unload(entry, platform)

    return True
