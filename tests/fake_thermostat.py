"""A fake thermostat TCP server that mimics the observed real device behaviour."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

DEVICE_ID = "1038000000000A01"


@dataclass
class FakeState:
    power: int = 1
    current: float = 22.0
    target: float = 22.0
    away: int = 0
    program: int = 0
    mode: int = 0

    schedule_target: float = 16.0

    def status(self) -> str:
        # Real type 9: away shows frost target 05.0; mode 1 (auto) runs the schedule.
        if self.away:
            target = 5.0
        elif self.mode == 1:
            target = self.schedule_target
        else:
            target = self.target
        program = 1 if self.mode == 1 else self.program
        return (f"{self.power},{self.current:.1f},{target:04.1f},0,{self.away},"
                f"{program},0,0,{self.mode},05,35")


@dataclass
class FakeThermostat:
    device_id: str = DEVICE_ID
    device_type: int = 9
    send_hello: bool = True
    hello_newline: bool = True
    answer_commands: bool = True
    ignore_status_requests: int = 0  # don't answer the next N S00 requests
    connections: int = 0
    state: FakeState = field(default_factory=FakeState)
    received: list[str] = field(default_factory=list)
    server: asyncio.Server | None = None
    writers: list[asyncio.StreamWriter] = field(default_factory=list)

    @property
    def port(self) -> int:
        assert self.server
        return self.server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def stop(self) -> None:
        await self.drop_clients()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def drop_clients(self) -> None:
        for w in self.writers:
            w.close()
        self.writers.clear()

    async def push_status(self) -> None:
        for w in self.writers:
            w.write(self._line("S00", self.state.status()))
            await w.drain()

    def _line(self, code: str, data: str, newline: bool = True) -> bytes:
        return f"/{self.device_id}/{code}/1/{data}{chr(13) + chr(10) if newline else ''}".encode()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self.writers.append(writer)
        if self.send_hello:
            writer.write(self._line("type", str(self.device_type), self.hello_newline))
            await writer.drain()
        try:
            while line := await reader.readline():
                text = line.decode().strip()
                self.received.append(text)
                parts = text.split("/")
                if len(parts) != 5:
                    continue
                code, data = parts[2], parts[4]
                if code == "S00" and self.ignore_status_requests > 0:
                    self.ignore_status_requests -= 1
                    continue
                reply = self._apply(code, data)
                if reply is not None and self.answer_commands:
                    writer.write(reply)
                    await writer.drain()
        except (ConnectionError, OSError):
            pass

    def _apply(self, code: str, data: str) -> bytes | None:
        """Mimics type 9: S01 and S07 are ignored (no reply), S12 only takes 0-2."""
        s = self.state
        type9 = self.device_type == 9
        if code == "SPOLL":
            return self._line("SPOLL", "OK")
        if code == "S01" and not type9:
            s.power = int(data)
        elif code == "S02":
            s.target = float(data)
            s.away = 0  # setting a temperature cancels away (confirmed on type 9)
        elif code == "S03":
            s.away = int(data)
        elif code == "S07" and not type9:
            s.program = int(data)
        elif code == "S12" and data in ("0", "1", "2"):
            s.mode = int(data)
        elif code != "S00":
            return None
        return self._line("S00", s.status())
