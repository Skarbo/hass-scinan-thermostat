"""Scinan API exceptions."""


class ScinanAuthFailed(Exception):
    """Scinan authentication error"""


class ScinanInvalidTokenError(Exception):
    """Scinan token error"""


class ScinanResponseError(Exception):
    """Scinan response error"""
