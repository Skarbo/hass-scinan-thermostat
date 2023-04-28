"""Scinan API constants."""

API_DOMAIN_SASWELL = "http://api.saswell.com.cn"
API_DOMAIN_SCINAN = "https://api.scinan.com"
API_TIMEOUT_DEFAULT = 30

DEVICE_TEMP_MIN = 5
DEVICE_TEMP_MAX = 35
DEVICE_COMPANY_ID_THERMOSTAT = "1038"  # 1038 is a thermostat, 1015 is a gateway
DEVICE_TYPE_THERMOSTAT = 9

CODE_WRONG_PASSWORD_BLOCKED = "1"  # Incorrect password for 5 consecutive times. (1)
CODE_TOKEN_EXPECTED = "10002"  # Need token (10002)
CODE_TOKEN_EXPIRED = "10003"  # Token expired. (10003)
CODE_WRONG_PASSWORD = "20007"  # Password error. (20007)
CODE_USER_NOT_EXIST = "20014"  # User not exists. (20014)
