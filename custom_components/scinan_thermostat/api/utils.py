"""Scinan API utils."""
import datetime
import hashlib
import logging
import time

from .device import ScinanDevice
from .enums import ScinanDeviceMode
from .types import (
    ScinanDeviceResponseType,
    ScinanDeviceStatusType,
)
from .const import (
    DEVICE_TEMP_MAX,
    DEVICE_TEMP_MIN,
)

_LOGGER = logging.getLogger(__name__)


def create_timestamp() -> str:
    """Create timestamp for Scinan request"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def create_md5_hash(string: str) -> str:
    """Create MD5 hash"""
    return hashlib.md5(string.encode()).hexdigest()


def _create_md5_bytes(string: str) -> bytes:
    """Create MD5 bytes"""
    return hashlib.md5(string.encode('utf-8')).digest()


def _convert_hex_string(bytes_arr: bytes) -> str:
    """Convert bytes array to hex string"""
    string_buffer = ""
    for byte in bytes_arr:
        hex_string = hex(byte & 255)[2:]
        if len(hex_string) == 1:
            string_buffer += "0"
        string_buffer += hex_string
    return string_buffer.upper()


def create_md5_signature(params: dict, app_secret: str) -> str:
    """Create MD5 signature for Scinan v2.0 request"""
    sign = app_secret
    sorted_params = dict(sorted(params.items()))  # have to be sorted

    for key, value in sorted_params.items():
        sign += key
        sign += value
    sign += app_secret

    return _convert_hex_string(_create_md5_bytes(sign))


def remove_empty_entries(params: dict) -> dict:
    """Remove empty entries from dictionary"""
    return {k: v for k, v in params.items() if v is not None}


def create_device_status(status: str) -> ScinanDeviceStatusType:
    """Create device status dict"""
    status_arr = status.split(",")
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
    return {
        'time': int(status_arr[0]),
        'is_on': status_arr[1] == "1",
        'measure_temperature': float(status_arr[2]),
        'target_temperature': float(status_arr[3]),
        'away': status_arr[5] == "1",
        'mode': int(status_arr[9]),
        'min_temp': float(status_arr[10]) if len(status_arr) > 10 else DEVICE_TEMP_MIN,
        'max_temp': float(status_arr[11]) if len(status_arr) > 11 else DEVICE_TEMP_MAX,
    }


def prepare_device_values(
    device: ScinanDevice,
    device_response: ScinanDeviceResponseType,
    device_status: ScinanDeviceStatusType
) -> ScinanDevice:
    """Update Scinan device from response"""
    try:
        mode = ScinanDeviceMode(device_status['mode'])
    except TypeError:
        _LOGGER.debug(
            "Unknown mode value '%s' for device '%s'",
            device_status['mode'],
            device_response['id'],
        )
        # Only seen unknown value when set to day_or_night
        mode = ScinanDeviceMode.DAY_OR_NIGHT

    device.device_id = device_response['id']
    device.name = device_response['title']
    device.is_on = device_status['is_on']
    device.online = device_response['online'] == "1"
    device.away = device_status['away']
    device.measure_temperature = device_status['measure_temperature']
    device.target_temperature = device_status['target_temperature']
    device.mode = mode
    device.actual_mode = device_status['mode']
    device.type = device_response['type']
    device.company_id = device_response['company_id']
    device.last_updated = datetime.datetime.fromtimestamp(
        int(device_status['time']) / 1000
    ).astimezone(datetime.timezone.utc)

    return device
