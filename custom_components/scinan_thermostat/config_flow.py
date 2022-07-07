"""Adds config flow for Scinan integration."""
import asyncio
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .scinan import ScinanApi, ScinanAuthFailed

_LOGGER = logging.getLogger(__name__)


class ScinanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Scinan integration."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the user step."""
        data_schema = vol.Schema(
            {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
        )
        errors = {}

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

        try:
            await scinan_data_connection.authenticate()
        except (ScinanAuthFailed, asyncio.TimeoutError) as err:
            is_auth_error = isinstance(err, ScinanAuthFailed)
            errors["base"] = (
                "auth_error"
                if is_auth_error else
                "cannot_connect"
            )

            if not is_auth_error:
                _LOGGER.error("Authentication timeout", exc_info=True)

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
