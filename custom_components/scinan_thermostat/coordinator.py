"""Data coordinators: polled for cloud, push-based for local."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyscinan import (
    DeviceMode,
    ScinanAuthFailed,
    ScinanCloudClient,
    ScinanDevice,
    ScinanError,
    ScinanInvalidTokenError,
    ScinanLocalClient,
)

from .const import CLOUD_REFRESH_DELAY, CONNECTION_CLOUD, CONNECTION_LOCAL, DOMAIN
from .migrate import local_device_ids

_LOGGER = logging.getLogger(__name__)

type ScinanConfigEntry = ConfigEntry[ScinanCoordinator]


class ScinanCoordinator(DataUpdateCoordinator[dict[str, ScinanDevice]]):
    """Common interface used by the climate entity. Data is {device_id: device}."""

    connection_type: str
    config_entry: ScinanConfigEntry

    async def async_set_temperature(self, device_id: str, temperature: float) -> None:
        """Set the target temperature."""
        raise NotImplementedError

    async def async_set_away(self, device_id: str, away: bool) -> None:
        """Turn away mode on or off."""
        raise NotImplementedError

    async def async_set_power(self, device_id: str, on: bool) -> None:
        """Turn the thermostat on or off (ignored by type 9)."""
        raise NotImplementedError

    async def async_set_mode(self, device_id: str, mode: DeviceMode) -> None:
        """Set the system mode (Comfort, Auto, Day or night)."""
        raise NotImplementedError


class ScinanCloudCoordinator(ScinanCoordinator):
    """Polls the cloud device list. Devices with a local entry are left out."""

    connection_type = CONNECTION_CLOUD

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ScinanConfigEntry,
        client: ScinanCloudClient,
        update_interval: int,
    ) -> None:
        """Poll the cloud account at the given interval."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} cloud {entry.title}",
            update_interval=timedelta(seconds=update_interval),
        )
        self.client = client
        self.account_device_ids: set[str] = set()  # every thermostat on the account

    async def _async_update_data(self) -> dict[str, ScinanDevice]:
        try:
            devices = await self.client.update_devices()
        except (ScinanAuthFailed, ScinanInvalidTokenError) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ScinanError as err:
            raise UpdateFailed(f"Cloud update failed: {err}") from err
        if devices and not any(dev.online for dev in devices.values()):
            _LOGGER.warning(
                "0 of %s devices online for %s, check the Thermostat app",
                len(devices),
                self.config_entry.title,
            )
        self.account_device_ids = set(devices)
        skip = local_device_ids(self.hass)
        return {dev_id: dev for dev_id, dev in devices.items() if dev_id not in skip}

    async def _command(self, coro: Awaitable[Any]) -> None:
        try:
            await coro
        except (ScinanAuthFailed, ScinanInvalidTokenError) as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"Cloud authentication failed: {err}") from err
        except ScinanError as err:
            raise HomeAssistantError(f"Cloud command failed: {err}") from err
        # The cloud status updates about 2 s after a command.
        await asyncio.sleep(CLOUD_REFRESH_DELAY)
        await self.async_refresh()

    async def async_set_temperature(self, device_id: str, temperature: float) -> None:
        """Set the target temperature via the cloud."""
        await self._command(self.client.set_temperature(device_id, temperature))

    async def async_set_away(self, device_id: str, away: bool) -> None:
        """Turn away mode on or off via the cloud."""
        await self._command(self.client.set_away(device_id, away))

    async def async_set_power(self, device_id: str, on: bool) -> None:
        """Turn the thermostat on or off via the cloud."""
        await self._command(self.client.set_power(device_id, on))

    async def async_set_mode(self, device_id: str, mode: DeviceMode) -> None:
        """Set the system mode via the cloud."""
        await self._command(self.client.set_mode(device_id, mode))


class ScinanLocalCoordinator(ScinanCoordinator):
    """One thermostat over local TCP. The device pushes every change, no polling."""

    connection_type = CONNECTION_LOCAL

    def __init__(
        self, hass: HomeAssistant, entry: ScinanConfigEntry, client: ScinanLocalClient
    ) -> None:
        """Listen to the local client's pushes."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} local {client.host}",
            update_interval=None,
        )
        self.client = client
        self._last_seen = self._snapshot()  # (raw status, online) last passed on to HA
        self._remove_listener = client.add_listener(self._handle_client_update)

    @property
    def device(self) -> ScinanDevice:
        """The thermostat this entry talks to."""
        assert self.client.device is not None
        return self.client.device

    @callback
    def _handle_client_update(self) -> None:
        """Status push or connection change from the client.

        The thermostat repeats its status every 15 seconds; only real changes are
        passed on to Home Assistant.
        """
        device = self.device
        seen = self._snapshot()
        if seen == self._last_seen:
            return
        if seen[1] != self._last_seen[1]:
            if device.online:
                _LOGGER.info("Thermostat %s at %s is available again", device.device_id,
                             self.client.host)
            else:
                _LOGGER.info("Thermostat %s at %s is unavailable (connection lost); "
                             "reconnecting in the background", device.device_id, self.client.host)
        self._last_seen = seen
        self.async_set_updated_data({device.device_id: device})

    def _snapshot(self) -> tuple[str | None, bool]:
        device = self.client.device
        if device is None:
            return (None, False)
        return (device.status.raw if device.status else None, device.online)

    async def _async_update_data(self) -> dict[str, ScinanDevice]:
        return {self.device.device_id: self.device}

    async def async_shutdown(self) -> None:
        """Stop listening and close the TCP connection."""
        self._remove_listener()
        await super().async_shutdown()
        await self.client.close()

    def _ensure_connected(self) -> None:
        if not self.client.connected:
            raise HomeAssistantError(
                f"Thermostat at {self.client.host} is not connected; reconnecting in the background"
            )

    async def _command(self, coro: Awaitable[Any]) -> None:
        # The device answers every accepted command with a status push, which
        # reaches the entity through the listener. Ignored commands get no reply;
        # the client then returns the unchanged status.
        try:
            await coro
        except ScinanError as err:
            raise HomeAssistantError(
                f"Command to thermostat at {self.client.host} failed: {err}"
            ) from err

    async def async_set_temperature(self, device_id: str, temperature: float) -> None:
        """Set the target temperature over the local connection."""
        self._ensure_connected()
        await self._command(self.client.set_temperature(temperature))

    async def async_set_away(self, device_id: str, away: bool) -> None:
        """Turn away mode on or off over the local connection."""
        self._ensure_connected()
        await self._command(self.client.set_away(away))

    async def async_set_power(self, device_id: str, on: bool) -> None:
        """Turn the thermostat on or off over the local connection."""
        self._ensure_connected()
        await self._command(self.client.set_power(on))

    async def async_set_mode(self, device_id: str, mode: DeviceMode) -> None:
        """Set the system mode over the local connection."""
        self._ensure_connected()
        await self._command(self.client.set_mode(mode))
