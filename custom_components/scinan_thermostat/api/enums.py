"""Scinan API enums"""
from enum import Enum


class ScinanVersion(Enum):
    """Scinan version"""
    V1 = "v1.0"
    V2 = "v2.0"

    def __str__(self):
        return str(self.value)

    @classmethod
    def from_value(cls, value):
        """Get ScinanVersion from value"""
        for member in cls:
            if member.value == value:
                return member
        return ScinanVersion.V2


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
