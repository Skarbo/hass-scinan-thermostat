"""Adds config flow for Scinan integration."""
import asyncio
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .scinan import ScinanApi, ScinanAuthFailed

_LOGGER = logging.getLogger(__name__)


class ScinanOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Scinan integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input=None
    ) -> FlowResult:
        """Manage Scinan options."""
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL,
                        DEFAULT_UPDATE_INTERVAL,
                    ),
                ): int,
            }
        )
        errors: dict[str, str] = {}
        interval: int = 0

        # validate input
        if user_input is not None:
            try:
                interval = int(user_input[CONF_SCAN_INTERVAL])
            except ValueError:
                errors["base"] = "invalid_interval"
            if (interval < 60) or (interval > 86400):
                errors["base"] = "invalid_interval"

        # show form
        if (user_input is None) or (bool(errors)):
            return self.async_show_form(
                step_id="init",
                data_schema=data_schema,
                errors=errors,
            )

        return self.async_create_entry(title="", data=user_input)


class ScinanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Scinan integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ScinanOptionsFlowHandler:
        """Get the options flow for this handler."""
        return ScinanOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the user step."""
        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        errors: dict[str, str] = {}

        def _show_user_form():
            return self.async_show_form(
                step_id="user",
                data_schema=data_schema,
                errors=errors,
            )

        if user_input is None:
            return _show_user_form()

        username = user_input[CONF_USERNAME].replace(" ", "")
        password = user_input[CONF_PASSWORD].replace(" ", "")

        scinan_data_connection = ScinanApi(
            username,
            password,
            web_session=async_get_clientsession(self.hass),
        )

        # noinspection PyBroadException
        try:
            await scinan_data_connection.authenticate()
        except ScinanAuthFailed:
            errors["base"] = "auth_error"
            return _show_user_form()
        except asyncio.TimeoutError:
            errors["base"] = "cannot_connect"
            _LOGGER.error("Authentication timeout", exc_info=True)
            return _show_user_form()
        except Exception:  # pylint: disable=broad-except
            errors["base"] = "unknown"
            _LOGGER.error("Unexpected exception", exc_info=True)
            return _show_user_form()

        unique_id = username
        token = scinan_data_connection.token

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=unique_id,
            data={
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
                CONF_TOKEN: token
            },
        )
