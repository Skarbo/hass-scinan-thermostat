"""Repairs fix flows: move cloud thermostats to local, remove an empty cloud hub."""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .migrate import MoveToLocalFlowMixin

try:
    from homeassistant.components.repairs import RepairsFlowResult
except ImportError:  # pragma: no cover - HA before 2026.10
    RepairsFlowResult = data_entry_flow.FlowResult  # type: ignore[misc,assignment]


class MoveToLocalRepairFlow(MoveToLocalFlowMixin, RepairsFlow):
    """Same steps as the cloud hub's "Move thermostats to local control" option."""

    def __init__(self, entry_id: str) -> None:
        """Remember the cloud entry the notice belongs to."""
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Start with the move steps."""
        return await self.async_step_move_to_local()

    def _move_entry(self) -> ConfigEntry | None:
        return self.hass.config_entries.async_get_entry(self._entry_id)

    async def _async_move_done(self, moved: bool) -> RepairsFlowResult:
        if not moved:
            # Nothing entered: keep the notice (the user can still ignore it).
            return self.async_abort(reason="nothing_moved")
        return self.async_create_entry(data={})


class RemoveCloudEntryRepairFlow(RepairsFlow):
    """All thermostats of the account are local: offer to delete the cloud hub."""

    def __init__(self, entry_id: str) -> None:
        """Remember the cloud entry the notice belongs to."""
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Start with the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Delete the cloud entry when confirmed."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_create_entry(data={})
        if user_input is not None:
            await self.hass.config_entries.async_remove(entry.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"account": entry.title},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Return the fix flow for a Repairs notice."""
    entry_id = (data or {})["entry_id"]
    if issue_id.startswith("cloud_empty"):
        return RemoveCloudEntryRepairFlow(entry_id)
    return MoveToLocalRepairFlow(entry_id)
