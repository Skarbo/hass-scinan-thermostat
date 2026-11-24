"""Config flow: pick Cloud (account) or Local (one thermostat by IP)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_API_TOKEN,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyscinan import ScinanAuthFailed, ScinanCloudClient, ScinanError
import voluptuous as vol

from .const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)
from .migrate import (
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_TITLE,
    MoveToLocalFlowMixin,
    async_probe_local,
    normalize_host,
)

_LOGGER = logging.getLogger(__name__)

CLOUD_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)


def _local_schema(host: str | None = None) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_HOST, default=host or vol.UNDEFINED): str})


class ScinanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Scinan Saswell thermostats."""

    VERSION = 1
    MINOR_VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ScinanOptionsFlow:
        """Options for cloud entries."""
        return ScinanOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Only cloud entries have options (the polling interval)."""
        return config_entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_CLOUD) == CONNECTION_CLOUD

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose Local or Cloud."""
        return self.async_show_menu(step_id="user", menu_options=["local", "cloud"])

    # ----------------------------------------------------------------- cloud

    async def _validate_cloud(self, username: str, password: str) -> tuple[str | None, str | None]:
        """Return (token, error key)."""
        client = ScinanCloudClient(
            username, password, session=async_get_clientsession(self.hass)
        )
        try:
            return await client.authenticate(), None
        except ScinanAuthFailed as err:
            _LOGGER.warning("Cloud login failed for %s: %s", username, err)
            return None, "invalid_auth"
        except ScinanError as err:
            _LOGGER.warning("Cloud login error for %s: %s", username, err)
            return None, "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error during cloud login")
            return None, "unknown"

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Log in with a Thermostat app account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].replace(" ", "")
            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()
            token, error = await self._validate_cloud(username, user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_CONNECTION_TYPE: CONNECTION_CLOUD,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_API_TOKEN: token,
                    },
                )
        return self.async_show_form(
            step_id="cloud",
            data_schema=self.add_suggested_values_to_schema(CLOUD_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after a failed cloud login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the cloud account's current password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token, error = await self._validate_cloud(
                entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD], CONF_API_TOKEN: token},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    # ----------------------------------------------------------------- local

    def _cloud_name(self, device_id: str) -> str | None:
        """Name of the thermostat in a loaded cloud entry, if any."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED and entry.data.get(
                CONF_CONNECTION_TYPE, CONNECTION_CLOUD
            ) == CONNECTION_CLOUD:
                device = entry.runtime_data.data.get(device_id)
                if device is not None and device.name:
                    return device.name
        return None

    async def async_step_local(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add one thermostat by IP address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            device, error = (
                await async_probe_local(host) if host else (None, "invalid_host")
            )
            if error:
                errors[CONF_HOST] = error
            else:
                assert device is not None and host is not None
                await self.async_set_unique_id(device.device_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                title = self._cloud_name(device.device_id) or f"Thermostat {device.device_id[-6:]}"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_CONNECTION_TYPE: CONNECTION_LOCAL,
                        CONF_HOST: host,
                        "device_type": device.type,
                    },
                )
        return self.async_show_form(
            step_id="local",
            data_schema=_local_schema(user_input.get(CONF_HOST) if user_input else None),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Local entry created by "Move to local" (the IP is already checked)."""
        await self.async_set_unique_id(import_data[CONF_DEVICE_ID])
        self._abort_if_unique_id_configured(updates={CONF_HOST: import_data[CONF_HOST]})
        return self.async_create_entry(
            title=import_data[CONF_TITLE],
            data={
                CONF_CONNECTION_TYPE: CONNECTION_LOCAL,
                CONF_HOST: import_data[CONF_HOST],
                CONF_DEVICE_TYPE: import_data.get(CONF_DEVICE_TYPE),
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change a local thermostat's IP address (or a cloud password)."""
        entry = self._get_reconfigure_entry()
        if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_LOCAL:
            return await self.async_step_reconfigure_cloud()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            device, error = (
                await async_probe_local(host) if host else (None, "invalid_host")
            )
            if error:
                errors[CONF_HOST] = error
            else:
                assert device is not None and host is not None
                await self.async_set_unique_id(device.device_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_local_schema(
                user_input.get(CONF_HOST) if user_input else entry.data.get(CONF_HOST)
            ),
            errors=errors,
        )


    async def async_step_reconfigure_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cloud hub: update the password (the email is the account, so it stays)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token, error = await self._validate_cloud(
                entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD], CONF_API_TOKEN: token},
                )
        return self.async_show_form(
            step_id="reconfigure_cloud",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )


class ScinanOptionsFlow(MoveToLocalFlowMixin, OptionsFlow):
    """Cloud entry options: polling interval, and moving thermostats to local."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options menu."""
        return self.async_show_menu(step_id="init", menu_options=["move_to_local", "settings"])

    def _move_entry(self) -> ConfigEntry | None:
        return self.config_entry

    async def _async_move_done(self, moved: bool) -> ConfigFlowResult:
        return self.async_create_entry(data=dict(self.config_entry.options))

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the cloud update interval."""
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_UPDATE_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL)
                    )
                }
            ),
        )
