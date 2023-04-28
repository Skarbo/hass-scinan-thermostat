"""Scinan API device class"""
import datetime
from dataclasses import dataclass
from typing import Union

from .enums import (
    ScinanDeviceMode,
    ScinanDeviceSensor,
)


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
