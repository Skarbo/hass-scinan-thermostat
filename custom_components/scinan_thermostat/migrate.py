"""Move thermostats from a cloud entry to local entries.

Shared by the cloud entry's options ("Move to local") and the Repairs fix flow.
The new local entries use the device id as unique id, the same unique id the cloud
entity has, so Home Assistant moves the existing device and entity over: name,
area, entity id and history stay.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import logging
import re
import socket
from typing import Any

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from pyscinan import (
    ScinanConnectionError,
    ScinanDevice,
    ScinanError,
    ScinanLocalClient,
    ScinanTimeoutError,
)
import voluptuous as vol

from .const import CONF_CONNECTION_TYPE, CONNECTION_CLOUD, CONNECTION_LOCAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE_ID = "device_id"
CONF_DEVICE_TYPE = "device_type"
CONF_TITLE = "title"


_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(\.{_LABEL})*$", re.IGNORECASE)


def normalize_host(value: str | None) -> str | None:
    """Clean up what the user typed. Returns an IP/host name, or None if it isn't one.

    Removes surrounding (and pasted non-breaking) spaces, a leading http:// or https://,
    a trailing slash and a trailing :2000 (the thermostat's port).
    """
    host = (value or "").replace("\u00a0", " ").strip().lower()
    host = re.sub(r"^https?://", "", host).rstrip("/")
    host = re.sub(r":2000$", "", host)
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if re.fullmatch(r"[\d.]+", host):
        return None  # looks like an IP but isn't one, e.g. 192.168.1 or 192.168.1.300
    return host if _HOSTNAME.match(host) else None


async def async_probe_local(host: str) -> tuple[ScinanDevice | None, str | None]:
    """Connect once, read the device id/type and status, disconnect. Returns (device, error).

    Errors: unknown_host, cannot_connect (nothing answered), connection_refused (port 2000
    closed), no_answer (connected but no hello/status; pyscinan >= 0.1.1 already asks for
    the status twice), not_a_thermostat.
    """
    client = ScinanLocalClient(host, reconnect=False)
    try:
        return await client.connect(), None
    except ScinanTimeoutError as err:
        _LOGGER.warning("No answer from thermostat at %s: %s", host, err)
        return None, "no_answer"
    except ScinanConnectionError as err:
        _LOGGER.warning("Cannot connect to thermostat at %s: %s", host, err)
        cause = err.__cause__
        if isinstance(cause, socket.gaierror):
            return None, "unknown_host"
        if isinstance(cause, ConnectionRefusedError):
            return None, "connection_refused"
        return None, "cannot_connect"
    except ScinanError as err:
        _LOGGER.warning("Unexpected answer from %s: %s", host, err)
        return None, "not_a_thermostat"
    except Exception:
        _LOGGER.exception("Unexpected error connecting to %s", host)
        return None, "unknown"
    finally:
        await client.close()


def is_cloud_entry(entry: ConfigEntry) -> bool:
    """Whether the entry is a cloud account (1.x entries have no connection type)."""
    return entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_CLOUD) == CONNECTION_CLOUD


@callback
def local_device_ids(hass: HomeAssistant) -> set[str]:
    """Device ids that have their own local config entry."""
    return {
        entry.unique_id
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_LOCAL and entry.unique_id
    }


def device_id_of(device: dr.DeviceEntry) -> str | None:
    """The thermostat's device ID from a device registry entry."""
    return next((ident for domain, ident in device.identifiers if domain == DOMAIN), None)


@callback
def cloud_thermostats(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """{form label: device id} for the cloud entry's thermostats that aren't local yet.

    Uses the device registry, so it also lists thermostats the cloud no longer returns
    (for example one that dropped off the account after a Wi-Fi change), and the names
    the user gave them.
    """
    local = local_device_ids(hass)
    names: dict[str, str] = {}  # device id -> name
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        device_id = device_id_of(device)
        if device_id and device_id not in local:
            names[device_id] = device.name_by_user or device.name or device_id
    coordinator = getattr(entry, "runtime_data", None)
    for device_id, device in (coordinator.data if coordinator else {}).items():
        if device_id not in local:
            names.setdefault(device_id, device.name or device_id)

    labels: dict[str, str] = {}
    for device_id, name in sorted(names.items(), key=lambda item: item[1].lower()):
        label = name if name not in labels else f"{name} ({device_id[-6:]})"
        labels[label] = device_id
    return labels


@dataclass(slots=True)
class LocalMove:
    """A checked thermostat that will get a local entry."""
    device_id: str
    host: str
    title: str
    device_type: int | None


async def async_check_move(device_id: str, host: str) -> tuple[LocalMove | None, str | None]:
    """Probe the IP and make sure it is this thermostat. Returns (move, error)."""
    device, error = await async_probe_local(host)
    if error:
        return None, error
    assert device is not None
    if device.device_id != device_id:
        return None, "wrong_device"
    return LocalMove(device_id, host, "", device.type), None


class MoveToLocalFlowMixin:
    """Steps shared by the cloud options flow and the Repairs fix flow.

    `move_to_local` explains what happens, then `move_device` asks for one IP per
    thermostat (empty = keep it in the cloud). HA only shows translated field labels,
    so the thermostat's name goes in the step description, not in the field key.
    """

    hass: HomeAssistant
    _queue: list[tuple[str, str]]  # (name, device id)
    _index: int
    _moves: list[LocalMove]

    def _move_entry(self) -> ConfigEntry | None:
        raise NotImplementedError

    async def _async_move_done(self, moved: bool) -> Any:
        """Finish the flow (moved = at least one local entry was created)."""
        raise NotImplementedError

    async def async_step_move_to_local(self, user_input: dict[str, Any] | None = None) -> Any:
        """Explain the move and list the thermostats."""
        entry = self._move_entry()
        if entry is None:
            return self.async_abort(reason="entry_removed")  # type: ignore[attr-defined]
        if user_input is None:
            labels = cloud_thermostats(self.hass, entry)
            if not labels:
                return self.async_abort(reason="nothing_to_move")  # type: ignore[attr-defined]
            self._queue = [(name, device_id) for name, device_id in labels.items()]
            return self.async_show_form(  # type: ignore[attr-defined]
                step_id="move_to_local",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "account": entry.title,
                    "count": str(len(self._queue)),
                    "names": "\n".join(f"- {name}" for name, _ in self._queue),
                },
            )
        self._index, self._moves = 0, []
        return await self.async_step_move_device()

    async def async_step_move_device(self, user_input: dict[str, Any] | None = None) -> Any:
        """Ask for one thermostat's IP address (empty keeps it in the cloud)."""
        name, device_id = self._queue[self._index]
        errors: dict[str, str] = {}
        typed = ((user_input or {}).get(CONF_HOST) or "").strip()
        host = normalize_host(typed) if typed else ""
        if user_input is not None:
            if host is None:
                errors[CONF_HOST] = "invalid_host"
                host = typed  # show what was typed, so it can be corrected
            elif host and host in {move.host for move in self._moves}:
                errors[CONF_HOST] = "duplicate_ip"
            elif host:
                move, error = await async_check_move(device_id, host)
                if error:
                    errors[CONF_HOST] = error
                else:
                    assert move is not None
                    move.title = name
                    self._moves.append(move)
            if not errors:
                self._index += 1
                if self._index >= len(self._queue):
                    if self._moves:
                        await async_create_local_entries(self.hass, self._moves)
                    return await self._async_move_done(bool(self._moves))
                name, device_id = self._queue[self._index]
                host = ""
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="move_device",
            data_schema=vol.Schema(
                {vol.Optional(CONF_HOST, description={"suggested_value": host or None}): str}
            ),
            description_placeholders={
                "name": name,
                "device_id": device_id,
                "index": str(self._index + 1),
                "total": str(len(self._queue)),
            },
            errors=errors,
            last_step=self._index + 1 >= len(self._queue),
        )


async def async_create_local_entries(hass: HomeAssistant, moves: list[LocalMove]) -> None:
    """Create a local entry per thermostat (via the config flow's import step)."""
    for move in moves:
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={
                CONF_HOST: move.host,
                CONF_DEVICE_ID: move.device_id,
                CONF_DEVICE_TYPE: move.device_type,
                CONF_TITLE: move.title,
            },
        )
