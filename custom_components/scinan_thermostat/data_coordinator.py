"""Scinan data update coordinator."""
import asyncio
import logging
from datetime import timedelta
from typing import Coroutine

from aiohttp import ClientOSError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from . import ScinanAuthFailed
from .api import (
    ScinanApi,
    ScinanInvalidTokenError,
    ScinanResponseError,
)
from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class ScinanDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Scinan data."""

    def __init__(
        self,
        hass: HomeAssistant,
        scinan_api: ScinanApi,
        update_interval: int = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        """Initialize global Scinan data updater."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )
        self.scinan_api = scinan_api

    async def set_update_interval(self, update_interval: int) -> None:
        """Update update interval. Trigger refresh."""
        if self.update_interval.seconds == update_interval:
            return

        _LOGGER.debug("Update coordinator interval %s", update_interval)

        self.update_interval = timedelta(
            seconds=update_interval,
        )
        await self.async_refresh()

    async def _async_update_data(self):
        """Update devices."""

        async def _update_devices():
            devices = await self.scinan_api.update_devices()
            online_devices = [tmpDevice for tmpDevice in devices.values() if tmpDevice.online]

            if len(devices) > 0 and len(online_devices) == 0:
                _LOGGER.warning(
                    "0 of %s online devices found, check your Thermostat app",
                    len(devices)
                )

            return devices

        return await self.api_wrapper(_update_devices())

    async def api_wrapper(
        self,
        method: Coroutine,
        refresh=False,
    ):
        """
        Run API method and raise appropriate exceptions on failure.
        Refreshes data after 1 second if set to True
        """
        try:
            result = await method
            if refresh:
                # Updated Scinan result is not instant, wait before refreshing
                await asyncio.sleep(1)
                await self.async_request_refresh()
            return result
        except (ScinanInvalidTokenError, ScinanAuthFailed) as err:
            raise ConfigEntryAuthFailed from err
        except (ScinanResponseError, ClientOSError) as err:
            raise UpdateFailed from err
