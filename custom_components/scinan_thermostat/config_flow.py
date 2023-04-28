"""Adds config flow for Scinan integration."""
import asyncio
import logging
from typing import (
    TypedDict,
    Union,
)

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_API_VERSION,
    CONF_DOMAIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import ScinanAuthFailed
from .api import (
    API_DOMAIN_SASWELL,
    API_DOMAIN_SCINAN,
    ScinanApi,
    ScinanVersion,
)
from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

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
                ): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400)),
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


class ScinanConfigFlowAuthResult(TypedDict):
    """Scinan auth result"""
    username: str
    password: str
    api_version: str
    api_domain: str
    token: Union[str, None]
    errors: dict[str, str]


class ScinanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Scinan integration."""
    VERSION = 1
    _reauth_entry: ConfigEntry = None

    async def _show_setup_form(
        self,
        form_id: str,
        *,
        errors: dict[str, str] = None,
        user_info=None,
    ) -> FlowResult:
        """Show auth form to the user."""
        _show_advanced_options = self.show_advanced_options or self._reauth_entry is not None

        if user_info is None:
            user_info = {}

        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=user_info.get(CONF_USERNAME)): str,
                vol.Required(CONF_PASSWORD, default=user_info.get(CONF_PASSWORD)): str,
            }
        )

        if _show_advanced_options:
            data_schema = data_schema.extend(
                {
                    vol.Required(
                        CONF_API_VERSION,
                        default=user_info.get(CONF_API_VERSION, str(ScinanVersion.V2)),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[str(ScinanVersion.V2), str(ScinanVersion.V1)],
                            mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Required(
                        CONF_DOMAIN,
                        default=user_info.get(CONF_DOMAIN, API_DOMAIN_SASWELL),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {'value': API_DOMAIN_SASWELL, 'label': 'Saswell'},
                                {'value': API_DOMAIN_SCINAN, 'label': 'Scinan'}
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                }
            )

        return self.async_show_form(
            step_id=form_id,
            data_schema=data_schema,
            errors=errors,
        )

    async def _validate_user(self, user_input=None) -> ScinanConfigFlowAuthResult:
        """Validate user input."""
        result: ScinanConfigFlowAuthResult = {
            'errors': {},
            'username': user_input.get(CONF_USERNAME).replace(" ", ""),
            'password': user_input.get(CONF_PASSWORD),
            'api_domain': user_input.get(CONF_DOMAIN),
            'api_version': user_input.get(CONF_API_VERSION),
            'token': None,
        }

        if result['username'] is None:
            result['errors'][CONF_USERNAME] = "missing_username"
        if result['password'] is None:
            result['errors'][CONF_PASSWORD] = "missing_password"

        email_schema = vol.Schema(vol.Email())
        try:
            email_schema(result['username'])
        except vol.Invalid:
            result['errors'][CONF_USERNAME] = "invalid_email"

        if bool(result['errors']):
            return result

        scinan_data_connection = ScinanApi(
            result['username'],
            result['password'],
            web_session=async_get_clientsession(self.hass),
            api_domain=result['api_domain'],
            api_version=result['api_version']
        )

        # noinspection PyBroadException
        try:
            await scinan_data_connection.authenticate()
        except ScinanAuthFailed as err:
            result['errors']['base'] = str(err)
            _LOGGER.warning("Authentication failed: %s", err)
            return result
        except asyncio.TimeoutError:
            result['errors']['base'] = "cannot_connect"
            _LOGGER.warning("Authentication timeout", exc_info=True)
            return result
        except Exception:  # pylint: disable=broad-except
            result['errors']['base'] = "unknown"
            _LOGGER.warning("Unexpected exception", exc_info=True)
            return result

        result['token'] = scinan_data_connection.token
        return result

    #
    # Options
    #

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ScinanOptionsFlowHandler:
        """Get the options flow for this handler."""
        return ScinanOptionsFlowHandler(config_entry)

    #
    # Auth
    #

    async def _async_step_user_or_reauth(self, user_input=None):
        """Handle the user or re-authentication step."""
        form_id = "reauth_confirm" if self._reauth_entry is not None else "user"

        if user_input is None and self._reauth_entry is not None:
            user_input = {
                CONF_USERNAME: self._reauth_entry.data.get(CONF_USERNAME),
                CONF_DOMAIN: self._reauth_entry.data.get(CONF_DOMAIN),
                CONF_API_VERSION: self._reauth_entry.data.get(CONF_API_VERSION),
            }

        if user_input is None or user_input.get(CONF_PASSWORD) is None:
            return await self._show_setup_form(form_id, user_info=user_input)

        result = await self._validate_user(user_input)

        if bool(result['errors']):
            return await self._show_setup_form(
                form_id,
                errors=result['errors'],
                user_info=user_input
            )

        entry_title = result['username']
        entry_data = {
            CONF_USERNAME: result['username'],
            CONF_PASSWORD: result['password'],
            CONF_DOMAIN: result['api_domain'],
            CONF_API_VERSION: result['api_version'],
        }

        if self._reauth_entry:
            # update existing entry
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                title=entry_title,
                data=entry_data
            )
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        # create new entry
        await self.async_set_unique_id(result['username'])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=entry_title,
            data=entry_data,
        )

    async def async_step_user(self, user_input=None):
        """Handle user step."""
        return await self._async_step_user_or_reauth(user_input)

    #
    # Reauth
    #

    async def async_step_reauth(self, _user_input=None):
        """Handle re-authentication step."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Handle re-authentication confirmation step."""
        return await self._async_step_user_or_reauth(user_input)
