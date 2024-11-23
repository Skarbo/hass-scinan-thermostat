"""Scinan API package for communicating with Scinan supported climate devices."""
import asyncio
import base64
import logging
import re
from typing import (
    TypedDict,
    Union,
)

import aiohttp
import async_timeout
from aiohttp.client_exceptions import ClientResponse, ClientOSError

from .const import (
    API_DOMAIN_SASWELL,
    API_DOMAIN_SCINAN,
    API_TIMEOUT_DEFAULT,
    CODE_TOKEN_EXPECTED,
    CODE_TOKEN_EXPIRED,
    CODE_USER_NOT_EXIST,
    CODE_WRONG_PASSWORD,
    CODE_WRONG_PASSWORD_BLOCKED,
    DEVICE_COMPANY_ID_THERMOSTAT,
    DEVICE_TEMP_MAX,
    DEVICE_TEMP_MIN,
    DEVICE_TYPE_WHL_THERMOSTAT,
    DEVICE_TYPE_FHL_THERMOSTAT,
)
from .device import ScinanDevice
from .enums import (
    ScinanDeviceMode,
    ScinanDeviceSensor,
    ScinanVersion,
)
from .exceptions import (
    ScinanAuthFailed,
    ScinanInvalidTokenError,
    ScinanResponseError,
)
from .types import (
    ScinanDeviceResponseType,
    ScinanDeviceStatusType,
    ScinanLoginResponseData,
)
from .utils import (
    create_device_status,
    create_md5_hash,
    create_md5_signature,
    create_timestamp,
    prepare_device_values,
    remove_empty_entries,
)

_API_URIS: dict[
    ScinanVersion, TypedDict('Uri', {'auth': str, 'device_list': str, 'device_control': str})] = {
    ScinanVersion.V1: {
        'auth': '/oauth2/authorize',
        'device_list': '/v1.0/devices/list',
        'device_control': '/v1.0/sensors/control',
    },
    ScinanVersion.V2: {
        'auth': '/v2.0/user/login',
        'device_list': '/v2.0/device/list',
        'device_control': '/v2.0/sensor/control',
    },
}

_API_AUTH_PARAMS_V1 = {
    "client_id": "100002",
    "response_type": "token",
    "redirect_uri": "http://localhost.com:8080/testCallBack.action",
}
_API_AUTH_HEADERS_V1 = {
    "User-Agent": "Thermostat/3.1.0 (iPhone; iOS 11.3; Scale/3.00)",
}
_API_REQUEST_PARAMS_V1 = {
    "format": "json",
}
_API_REQUEST_PARAMS_V2 = {
    "app_key": "100027",
    "company_id": DEVICE_COMPANY_ID_THERMOSTAT,
    "imei": "357014732382494",
}

_API_APP_SECRET = base64.b64decode("RTE0OTkwQ0JGNUM3NDBEQjg1MzBBQTZCRTA0REQ5RjM=").decode("utf-8")
_LOGGER = logging.getLogger(__name__)


# pylint: disable=too-many-instance-attributes
class ScinanApi:
    """Class to communicate with Scinan API"""
    web_session: aiohttp.ClientSession
    devices: dict[str, ScinanDevice]

    def __init__(
        self,
        username: str,
        password: str,
        *,
        token: str = None,
        timeout: int = API_TIMEOUT_DEFAULT,
        web_session: aiohttp.ClientSession = None,
        api_domain: str = None,
        api_version: Union[ScinanVersion, str] = None,
    ):
        """Initialize Scinan API"""
        if api_version is None:
            api_version = ScinanVersion.V2

        if not isinstance(api_version, ScinanVersion):
            api_version = ScinanVersion.from_value(api_version)

        if api_domain is None:
            api_domain = API_DOMAIN_SCINAN if api_version == ScinanVersion.V1 else API_DOMAIN_SASWELL

        if web_session is None:
            web_session = aiohttp.ClientSession()

        _LOGGER.debug("Initializing Scinan API (version=%s, domain=%s)", api_version, api_domain)

        self.web_session = web_session
        self._token = token
        self._username = username
        self._password = password
        self._timeout = timeout
        self._api_domain = api_domain
        self._api_version = api_version
        self.devices = {}

    @property
    def token(self) -> str:
        """Scinan user token."""
        return self._token

    async def close_connection(self):
        """Close web session connection."""
        if self.web_session is not None:
            await self.web_session.close()

    #
    # AUTHENTICATE
    #

    async def authenticate(self, *, skip=False) -> str:
        """Authenticate with Scinan API and retrieve token"""
        if self.token is not None and skip is True:
            _LOGGER.debug("Already authenticated")
            return self.token

        # v1.0 authenticate
        if self._api_version == ScinanVersion.V1:
            return await self._authenticate_v1()

        # v2.0 authenticate
        _LOGGER.debug("Authenticating with v2.0: %s", self._username)

        data: ScinanLoginResponseData = await self.request(
            _API_URIS[self._api_version]['auth'], {
                "account": self._username,
                "password": create_md5_hash(self._password),
            },
            retry=False,
        )

        if data.get("access_token") is None:
            _LOGGER.warning("Token not found in response")
            raise ScinanAuthFailed("Token not found in response")

        self._token = data.get("access_token")
        _LOGGER.debug("Token: %s", self.token)
        return self.token

    async def _authenticate_v1(self) -> str:
        """Authenticate with Scinan v1.0 API and retrieve token."""
        url = f"{self._api_domain}{_API_URIS[ScinanVersion.V1]['auth']}"
        _LOGGER.debug("Authenticating with v1.0: %s (%s)", self._username, url)

        # raise ScinanInvalidTokenError("Deprecated API") from from_err

        async with async_timeout.timeout(self._timeout):
            resp = await self.web_session.post(
                url,
                params={
                    **_API_AUTH_PARAMS_V1,
                    "passwd": self._password,
                    "userId": self._username,
                },
                headers=_API_AUTH_HEADERS_V1,
            )
        result = await resp.text()

        match = re.search(r"token:(\w+)", result)
        if match:
            self._token = match.group(1)
            _LOGGER.debug("Token: %s", self._token)
        else:
            _LOGGER.warning("Token not found in v1.0 response, invalid credentials?")
            raise ScinanAuthFailed("Wrong username or password. Check your Thermostat app.")

        return self.token

    #
    # REQUEST
    #

    async def request(
        self,
        uri: str,
        params: dict,
        *,
        retry: bool = True,
        retrying: bool = False
    ) -> dict:
        """Send request to Scinan API"""
        url = f"{self._api_domain}{uri}"
        use_params = self._prepare_request_params(params)

        _LOGGER.debug("Request POST %s %s (retrying=%s)", url, use_params, retrying)

        # if self._api_version == ScinanVersion.V1:
        #     raise ScinanInvalidTokenError("Deprecated API") from from_err

        try:
            async with async_timeout.timeout(self._timeout):
                resp = await self.web_session.post(
                    url,
                    params=use_params,
                )

            if resp.status != 200:
                raise ScinanResponseError(
                    f"Request {url} failed with status {resp.status}"
                )

            data = await resp.json(content_type=None)
            return self._handle_request_response(
                resp,
                data,
            )
        except ScinanInvalidTokenError as err:
            if retry is False or retrying is True:
                raise err

            _LOGGER.debug("Invalid token, forcing re-auth")
            await asyncio.sleep(3)  # wait a bit before authenticating
            await self.authenticate()

            _LOGGER.debug("Re-authenticated, retrying request %s", url)
            await asyncio.sleep(3)  # wait a bit before retrying
            return await self.request(uri, params, retrying=True)
        except ClientOSError as err:
            if retry is False or retrying is True:
                raise err

            if err.errno == 104:
                _LOGGER.debug("Connection reset by peer, retrying request %s", url)
                await asyncio.sleep(3)  # wait a bit before retrying
                return await self.request(uri, params, retrying=True)

            raise err

    def _prepare_request_params(self, params: dict) -> dict:
        """Prepare request params"""
        # v1.0 request params
        if self._api_version == ScinanVersion.V1:
            return remove_empty_entries(
                {
                    **params,
                    **_API_REQUEST_PARAMS_V1,
                    "timestamp": create_timestamp(),
                    "token": self.token
                }
            )

        # v2.0 request params
        params_v2 = {
            **params,
            **_API_REQUEST_PARAMS_V2,
            "timestamp": create_timestamp(),
            "token": self.token
        }
        params_v2 = remove_empty_entries(params_v2)
        params_v2['sign'] = create_md5_signature(params_v2, _API_APP_SECRET)
        return params_v2

    def _handle_request_response(
        self,
        resp: ClientResponse,
        data: dict,
    ) -> Union[dict, list]:
        """Handle request response"""
        error_code = None
        error_message = None

        # look for error
        if self._api_version == ScinanVersion.V1 and "error" in data:
            # v1.0 response error
            error_code = str(data.get("error_code", "unknown"))
            error_message = data.get("error", "Error communicating with API")
        elif self._api_version != ScinanVersion.V1 and data.get("result_code") != "0":
            # v2.0 response error
            error_code = str(data.get("result_code", "unknown"))
            error_message = data.get("result_message", "Error communicating with API")

        # handle error
        if error_code is not None:
            error_str = f"{error_message} ({error_code})"
            _LOGGER.warning(
                "Error in response %s %s: %s",
                resp.method,
                f'{resp.url.scheme}://{resp.url.host}{resp.url.path}',
                error_str
            )

            if error_code in [CODE_WRONG_PASSWORD, CODE_USER_NOT_EXIST]:
                raise ScinanAuthFailed(error_str)

            if error_code in [CODE_WRONG_PASSWORD_BLOCKED]:
                raise ScinanAuthFailed(
                    f"Incorrect password for 5 consecutive times. ({error_code})"
                )

            if error_code in [CODE_TOKEN_EXPIRED, CODE_TOKEN_EXPECTED]:
                raise ScinanInvalidTokenError(error_str)

            raise ScinanResponseError(error_str)

        # return data
        if self._api_version == ScinanVersion.V1:
            return data
        return data.get("result_data")

    #
    # DEVICE LIST
    #

    async def update_devices(self) -> dict[str, ScinanDevice]:
        """Update devices"""
        data = self._handle_device_list_response(
            await self.request(
                _API_URIS[self._api_version]['device_list'],
                {}
            )
        )

        for device_response in data:
            device_id = device_response['id']
            company_id = device_response['company_id']

            if company_id != DEVICE_COMPANY_ID_THERMOSTAT:
                _LOGGER.debug(
                    "Device '%s' is not a thermostat: company_id=%s",
                    device_id,
                    company_id,
                )
                continue

            device = self.devices.get(device_id, ScinanDevice())

            try:
                prepare_device_values(
                    device,
                    device_response,
                    create_device_status(
                        device_response[
                            'status' if self._api_version == ScinanVersion.V1 else 's00']
                    )
                )
            except BaseException:  # pylint: disable=broad-except
                _LOGGER.error(
                    "Error while updating device values '%s'",
                    device_id,
                    exc_info=True
                )

            self.devices[device_id] = device

        return self.devices

    def _handle_device_list_response(self, data: Union[dict, list]) -> list[
        ScinanDeviceResponseType
    ]:
        """Handle device list response"""
        # v1.0 device list response
        if self._api_version == ScinanVersion.V1:
            return data

        # v2.0 device list response
        if isinstance(data, list) is False or len(data) == 0:
            _LOGGER.warning("Device list result data should be a list")
            raise ScinanResponseError("Invalid device list response")

        supported_devices = (DEVICE_TYPE_WHL_THERMOSTAT, DEVICE_TYPE_FHL_THERMOSTAT)
        item = next((item for item in data if item['type'] in supported_devices), None)

        if item is None:
            _LOGGER.warning("Device list result data did not contain a list of thermostats")
            return []

        return item.get('devices')

    #
    # DEVICE SENSOR
    #

    async def update_device_sensor(
        self,
        device_id: str,
        sensor: ScinanDeviceSensor,
        value: Union[bool, float, ScinanDeviceMode]
    ) -> ScinanDevice:
        """Update device sensor"""

        control_value = str(value)
        if isinstance(value, bool):
            control_value = "1" if value else "0"
        elif isinstance(value, float):
            control_value = f"{min(max(value, DEVICE_TEMP_MIN), DEVICE_TEMP_MAX):04.1f}"

        old_value = None
        if device_id in self.devices:
            old_value = str(self.devices.get(device_id).get_sensor(sensor))

        _LOGGER.debug(
            "Update device '%s' sensor %s to %s=>%s",
            device_id,
            sensor.name,
            old_value,
            control_value
        )

        data = await self.request(
            _API_URIS[self._api_version]['device_control'],
            {
                "device_id": device_id,
                "sensor_id": sensor.value,
                "control_data": '{"value": "' + control_value + '"}',
                "sensor_type": "1",
            }
        )

        _LOGGER.debug(
            "Updated device '%s' sensor %s to %s: response=%s",
            device_id,
            sensor.name,
            control_value,
            data,
        )

        self._handle_device_sensor_response(device_id, sensor.name, control_value, data)

        if device_id in self.devices:
            self.devices[device_id].update_sensor(sensor, value)
        return self.devices.get(device_id)

    def _handle_device_sensor_response(
        self,
        device_id: str,
        sensor_name: str,
        sensor_value: str,
        data: dict,
    ) -> None:
        """Handle device sensor response"""
        if self._api_version == ScinanVersion.V1:
            if not data.get("result"):
                raise ScinanResponseError(
                    f"Could not update device '{device_id}' sensor '{sensor_name}' to '{sensor_value}'"
                )

    #
    # CHANGE SENSOR METHODS
    #

    async def set_home_away(self, device_id: str, away: bool) -> ScinanDevice:
        """Set device home/away"""
        return await self.update_device_sensor(
            device_id,
            ScinanDeviceSensor.AWAY,
            away
        )

    async def set_on_off(self, device_id: str, off: bool) -> ScinanDevice:
        """Set device on/off"""
        return await self.update_device_sensor(
            device_id,
            ScinanDeviceSensor.ON,
            off
        )

    async def set_mode(
        self,
        device_id: str,
        mode: ScinanDeviceMode
    ) -> ScinanDevice:
        """Set device mode"""
        return await self.update_device_sensor(
            device_id,
            ScinanDeviceSensor.MODE,
            mode
        )

    async def set_temperature(
        self,
        device_id: str,
        temperature: float
    ) -> ScinanDevice:
        """Set device temperature"""
        return await self.update_device_sensor(
            device_id,
            ScinanDeviceSensor.TARGET_TEMPERATURE,
            temperature
        )
