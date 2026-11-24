"""Fake Saswell v2.0 cloud API (aiohttp server on localhost) for cloud client tests.

Mimics the real server: every request is a POST with query params, signed with
uppercase MD5(secret + sorted k+v + secret). Responses use result_code /
result_message / result_data like the real API.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer
from pyscinan.cloud import _APP_SECRET, sign_params

USERNAME = "user@example.com"
PASSWORD = "secret-pw"
TOKEN = "tok-1"

DEVICE_LIST = [
    {
        "type": "9",
        "devices": [
            {
                "id": "1038000000000B02",
                "title": "Thermostat_000B02",
                "company_id": "1038",
                "online": "1",
                "s00": "1764000000000,1,21.5,22.0,0,0,0,0,0,0,05,35",
            },
            {
                "id": "1038000000000C03",
                "title": "Thermostat_000C03",
                "company_id": "1038",
                "online": "0",
                "s00": "1764000000000,1,19.0,05.0,0,1,0,0,0,0,05,35",
            },
            # Not a thermostat (other company) -> skipped
            {"id": "1015000000000001", "title": "Gateway", "company_id": "1015", "online": "1"},
        ],
    },
    # Unsupported device type group -> skipped
    {"type": "99", "devices": [{"id": "10380000000000XX", "company_id": "1038"}]},
]


def ok(data: Any = None) -> dict:
    return {"result_code": "0", "result_message": "", "result_data": data}


def error(code: str, message: str = "error") -> dict:
    return {"result_code": code, "result_message": message, "result_data": None}


class FakeCloud:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.valid_tokens = {TOKEN}
        self.next_token = TOKEN
        self.device_list = DEVICE_LIST
        # Queue of canned responses per path: dict, int (HTTP status) or "hang"
        self.overrides: dict[str, list[Any]] = {}
        self.bad_signatures = 0
        self._server: TestServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return str(self._server.make_url("")).rstrip("/")

    def calls(self, path: str) -> list[dict[str, str]]:
        return [p for uri, p in self.requests if uri == path]

    def queue(self, path: str, *responses: Any) -> None:
        self.overrides.setdefault(path, []).extend(responses)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/{tail:.*}", self._handle)
        self._server = TestServer(app)
        await self._server.start_server()

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.close()

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        path = request.path
        params = dict(request.query)
        self.requests.append((path, params))

        sign = params.pop("sign", None)
        if sign != sign_params(params, _APP_SECRET):
            self.bad_signatures += 1
            return web.json_response(error("10001", "sign error"))

        if self.overrides.get(path):
            canned = self.overrides[path].pop(0)
            if canned == "hang":
                await asyncio.sleep(10)
            elif isinstance(canned, int):
                return web.Response(status=canned, text="oops")
            else:
                return web.json_response(canned)

        if path == "/v2.0/user/login":
            return web.json_response(self._login(params))
        if params.get("token") not in self.valid_tokens:
            return web.json_response(error("10003", "token expired"))
        if path == "/v2.0/device/list":
            return web.json_response(ok(self.device_list))
        if path == "/v2.0/sensor/control":
            return web.json_response(ok())
        if path == "/v2.0/device/status":
            return web.json_response(ok({"device_id": params["device_id"], "online": "1",
                                         "user_id": "1", "s00": "1,1,21.5,21.5,0,0,0,0,0,0,05,35"}))
        return web.json_response(error("404", "unknown path"))

    def _login(self, params: dict[str, str]) -> dict:
        if params.get("account") != USERNAME:
            return error("20014", "user does not exist")
        if params.get("password") != hashlib.md5(PASSWORD.encode()).hexdigest():
            return error("20007", "wrong password")
        self.valid_tokens.add(self.next_token)
        return ok({"access_token": self.next_token})
