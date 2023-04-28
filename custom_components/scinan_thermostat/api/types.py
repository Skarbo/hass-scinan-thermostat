"""Scinan API types"""
from typing import (
    TypedDict,
    Union,
)


class ScinanDeviceStatusType(TypedDict):
    """Scinan device status type"""
    time: int
    is_on: bool
    measure_temperature: float
    target_temperature: float
    away: bool
    mode: int
    min_temp: float
    max_temp: float


class ScinanDeviceResponseType(TypedDict):
    """Scinan device response type"""
    id: str
    title: str
    about: str
    type: int
    mstype: int
    company_id: str
    online: str
    status: str  # v1.0
    s00: str  # v2.0


class ScinanResponse(TypedDict):
    """Scinan v2.0 response type"""
    result_code: str
    result_message: str
    result_data: Union[dict, list]


class ScinanLoginResponseData(TypedDict):
    """Scinan login v2.0 response type"""
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_token_expires_in: int
    user_id: str


class ScinanDeviceListResponseData(TypedDict):
    """Scinan device list v2.0 response type"""
    devices: list[ScinanDeviceResponseType]
    type: int

# class _ScinanDeviceResponseData(TypedDict):
#     """Scinan device v2.0 response type"""
#     about: str
#     company_id: str
#     countdown: int
#     create_time: int
#     device_module: str
#     door_type: int
#     gps_name: str
#     id: str
#     mstype: int
#     online: str
#     public_type: int
#     s00: str
#     timer: int
#     title: str
#     type: int
#     update_time: int
#     user_id: int
