"""Scinan data update coordinator."""
import asyncio
import logging
from datetime import timedelta
from typing import Coroutine

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN
from .scinan import ScinanApi, ScinanInvalidTokenError, ScinanResponseError

_LOGGER = logging.getLogger(__name__)


class ScinanDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Scinan data."""

    def __init__(
        self,
        hass: HomeAssistant,
        scinan_api: ScinanApi
    ) -> None:
        """Initialize global Scinan data updater."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=300),
        )
        self.scinan_api = scinan_api

    async def _async_update_data(self):
        """Update devices."""
        return await self.api_wrapper(self.scinan_api.update_devices())

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
        except ScinanInvalidTokenError as err:
            raise ConfigEntryAuthFailed from err
        except ScinanResponseError as err:
            raise UpdateFailed from err
