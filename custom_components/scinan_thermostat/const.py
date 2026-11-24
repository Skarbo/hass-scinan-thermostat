"""Constants for the Scinan Saswell thermostat integration."""

from typing import Final

DOMAIN: Final = "scinan_thermostat"
MANUFACTURER: Final = "Saswell"

CONF_CONNECTION_TYPE: Final = "connection_type"
CONNECTION_CLOUD: Final = "cloud"
CONNECTION_LOCAL: Final = "local"

DEFAULT_UPDATE_INTERVAL: Final = 300  # cloud polling, seconds
MIN_UPDATE_INTERVAL: Final = 60
MAX_UPDATE_INTERVAL: Final = 86400

# The cloud shows a new status about 2 s after a command (tested 2026-11-23).
CLOUD_REFRESH_DELAY: Final = 2.5

# Device type -> model family. HA shows the device card as "<model> (<model_id>)".
# The thermostat only reports its type, so the family is inferred from it.
DEVICE_MODEL: Final = "Floor heating thermostat"
DEVICE_MODEL_UNKNOWN: Final = "Wi-Fi thermostat"
DEVICE_MODEL_IDS: Final = {
    3: "T13FHL",
    6: "SAS922WHL",
    9: "SAS920FHL",
}
