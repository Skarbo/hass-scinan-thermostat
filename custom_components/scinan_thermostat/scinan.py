"""Scinan package for communicating with Scinan supported climate devices."""
import datetime
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import TypedDict, Union

import aiohttp
import async_timeout

AUTH_URL = "https://api.scinan.com/oauth2/authorize"
LIST_URL = "https://api.scinan.com/v1.0/devices/list"
CONTROL_URL = "https://api.scinan.com/v1.0/sensors/control"
CLIENT_ID = "100002"
USER_AGENT = "Thermostat/3.1.0 (iPhone; iOS 11.3; Scale/3.00)"
REDIRECT_URI = "http://localhost.com:8080/testCallBack.action"
DEFAULT_TIMEOUT = 30
HEADERS = {'User-Agent': USER_AGENT}
COMPANY_ID_THERMOSTAT = 1038  # 1038 is a thermostat, 1015 is a gateway
NEED_TOKEN_ERROR_CODE = 10002

_LOGGER = logging.getLogger(__name__)


class ScinanAuthFailed(Exception):
    """Scinan authentication failed"""


class ScinanInvalidTokenError(Exception):
    """Scinan authentication error"""


class ScinanResponseError(Exception):
    """Scinan response error"""


class ScinanDeviceMode(Enum):
    """Scinan device mode"""
    COMFORT = 0
    AUTO = 1
    DAY_OR_NIGHT = 2

    # 3 may represent cooling, not sure...
    # 4 may also be a value, not sure what it represents

    def __str__(self):
        return str(self.value)


class ScinanDeviceSensor(Enum):
    """Scinan device sensor"""
    ON = "01"
    TARGET_TEMPERATURE = "02"
    AWAY = "03"
    MODE = "12"


class _ScinanDeviceResponseType(TypedDict):
    """Scinan device response type"""
    id: str
    title: str
    about: str
    type: int
    image: str
    mstype: int
    product_id: str
    company_id: str
    online: str
    status: str


# pylint: disable=too-many-instance-attributes
@dataclass
class ScinanDevice:
    """Scinan device type"""
    device_id: Union[str, None] = None
    name: Union[str, None] = None
    is_on: Union[bool, None] = None
    online: Union[bool, None] = None
    away: Union[bool, None] = None
    measure_temperature: Union[float, None] = None
    target_temperature: Union[float, None] = None
    mode: Union[ScinanDeviceMode, None] = None
    actual_mode: Union[int, None] = None
    type: Union[int, None] = None
    company_id: Union[str, None] = None
    last_updated: Union[datetime.datetime, None] = None

    @property
    def is_heating(self):
        """Return True if device is heating"""
        return (
            self.online
            and self.is_on
            and not self.away
            and self.measure_temperature < self.target_temperature
        )

    def update_sensor(self, sensor: ScinanDeviceSensor, value):
        """Update device sensor"""
        if sensor == ScinanDeviceSensor.ON:
            self.is_on = value
        elif sensor == ScinanDeviceSensor.MODE:
            self.mode = value
        elif sensor == ScinanDeviceSensor.AWAY:
            self.away = value
        elif sensor == ScinanDeviceSensor.TARGET_TEMPERATURE:
            self.target_temperature = value

    def get_sensor(self, sensor: ScinanDeviceSensor):
        """Get device sensor"""
        if sensor == ScinanDeviceSensor.ON:
            return self.is_on
        if sensor == ScinanDeviceSensor.MODE:
            return self.mode
        if sensor == ScinanDeviceSensor.AWAY:
            return self.away
        if sensor == ScinanDeviceSensor.TARGET_TEMPERATURE:
            return self.target_temperature
        return None


def _create_timestamp() -> str:
    """Create timestamp for Scinan request"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _update_device_values(
    device: ScinanDevice,
    device_response: _ScinanDeviceResponseType
) -> ScinanDevice:
    """Update Scinan device from response"""
    status_arr = device_response["status"].split(",")
    # status[0] = 1564834881996 => time
    # status[1] = 1 => is_on (hvac)
    # status[2] = 26.5 => measure_temperature
    # status[3] = 16.0 => target_temperature
    # status[4] = 0 => unit?
    # status[5] = 1 => away
    # status[6] = 1 => isProgram?
    # status[7] = 0 => fanMode?
    # status[8] = 0 => runMode?
    # status[9] = 1 => mode (systemMode)
    # status[10] = 05 => min temp
    # status[11] = 35 => max temp

    actual_mode = int(status_arr[9])
    try:
        mode = ScinanDeviceMode(actual_mode)
    except TypeError:
        _LOGGER.debug(
            "Unknown mode value '%s' for device '%s'",
            actual_mode,
            device_response['id'],
        )
        # Only seen unknown value when set to day_or_night
        mode = ScinanDeviceMode.DAY_OR_NIGHT

    device.device_id = device_response['id']
    device.name = device_response['title']
    device.is_on = status_arr[1] == "1"
    device.online = device_response['online'] == "1"
    device.away = status_arr[5] == "1"
    device.measure_temperature = float(status_arr[2])
    device.target_temperature = float(status_arr[3])
    device.mode = mode
    device.actual_mode = actual_mode
    device.type = device_response['type']
    device.company_id = device_response['company_id']
    device.last_updated = datetime.datetime.fromtimestamp(
        int(status_arr[0]) / 1000
    ).astimezone(datetime.timezone.utc)

    return device


class ScinanApi:
    """Class to communicate with Scinan API."""
    web_session: aiohttp.ClientSession
    devices: dict[str, ScinanDevice]

    def __init__(
        self,
        username: str,
        password: str,
        *,
        token: str = None,
        timeout: int = DEFAULT_TIMEOUT,
        web_session: aiohttp.ClientSession = None
    ):
        """Initialize the Scinan API."""
        if web_session is None:
            self.web_session = aiohttp.ClientSession()
        else:
            self.web_session = web_session

        self._token = token
        self._username = username
        self._password = password
        self._timeout = timeout
        self.devices = {}

    @property
    def token(self):
        """Scinan user token."""
        return self._token

    async def authenticate(self, re_auth=False) -> None:
        """Authenticate with Scinan and retrieve token."""
        if self._token is not None and re_auth is False:
            _LOGGER.debug("Already authenticated")
            return

        _LOGGER.debug("Authenticating (re-auth=%s)", re_auth)

        async with async_timeout.timeout(self._timeout):
            resp = await self.web_session.post(
                AUTH_URL,
                params={
                    "client_id": CLIENT_ID,
                    "passwd": self._password,
                    "response_type": "token",
                    "userId": self._username,
                    "redirect_uri": REDIRECT_URI,
                },
                headers=HEADERS,
            )
        result = await resp.text()

        match = re.search(r"token:(\w+)", result)
        if match:
            self._token = match.group(1)
            _LOGGER.debug("Token: %s", self._token)
        else:
            _LOGGER.warning("Token not found in response")
            raise ScinanAuthFailed("Authentication failed")

    async def close_connection(self):
        """Close web session connection."""
        if self.web_session is not None:
            await self.web_session.close()

    async def __request(self, url: str, params: dict, retry=False):
        """Send request to Scinan API"""
        _LOGGER.debug("Request %s %s (retry=%s)", url, params, retry)

        try:
            async with async_timeout.timeout(self._timeout):
                resp = await self.web_session.get(
                    url,
                    params={
                        **params,
                        "format": "json",
                        "timestamp": _create_timestamp(),
                        "token": self._token
                    },
                    headers=HEADERS
                )

            data = await resp.json(content_type=None)

            if "error" in data:
                error_code = data.get("error_code", "unknown")
                error_message = f"{data['error']} ({error_code})"
                _LOGGER.error(
                    "Error in request response: %s",
                    error_message,
                )
                if error_code == NEED_TOKEN_ERROR_CODE:
                    raise ScinanInvalidTokenError(
                        f"Invalid token: {error_message}",
                    )
                raise ScinanResponseError(
                    f"Error communicating with API: {error_message}",
                )

            return data
        except ScinanInvalidTokenError as err:
            if retry is True:
                raise err
            _LOGGER.debug("Invalid token, forcing re-auth")
            await self.authenticate(True)
            _LOGGER.debug("Retrying request")
            return await self.__request(url, params, True)

    async def __update_device_sensor(
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
            control_value = f"{value:.1f}"

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

        data = await self.__request(
            CONTROL_URL,
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

        if not data.get("result"):
            raise ScinanResponseError(
                f"Could not update device '{device_id}' sensor %s to %s: "
                f"response={data}",
            )

        if device_id in self.devices:
            self.devices[device_id].update_sensor(sensor, value)
        return self.devices.get(device_id)

    async def update_devices(self) -> dict[str, ScinanDevice]:
        """Update devices"""
        data: list[_ScinanDeviceResponseType] = await self.__request(
            LIST_URL,
            {}
        )

        for device_response in data:
            device_id = device_response.get("id")
            company_id = int(device_response.get("company_id"))

            if company_id != COMPANY_ID_THERMOSTAT:
                _LOGGER.debug(
                    "Device '%s' is not a thermostat: company_id=%s",
                    device_id,
                    company_id,
                )
                continue

            device = self.devices.get(device_id, ScinanDevice())

            try:
                _update_device_values(device, device_response)
            except BaseException:  # pylint: disable=broad-except
                _LOGGER.error(
                    "Error while updating device values '%s'",
                    device_id,
                    exc_info=True
                )

            self.devices[device_id] = device

        return self.devices

    async def set_home_away(self, device_id: str, away: bool) -> ScinanDevice:
        """Set device home/away"""
        return await self.__update_device_sensor(
            device_id,
            ScinanDeviceSensor.AWAY,
            away
        )

    async def set_on_off(self, device_id: str, off: bool) -> ScinanDevice:
        """Set device on/off"""
        return await self.__update_device_sensor(
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
        return await self.__update_device_sensor(
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
        return await self.__update_device_sensor(
            device_id,
            ScinanDeviceSensor.TARGET_TEMPERATURE,
            temperature
        )
