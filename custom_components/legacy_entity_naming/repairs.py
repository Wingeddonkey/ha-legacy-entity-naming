"""Repair flow: put back names an update changed."""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .const import DOMAIN, ISSUE_NAMES_CHANGED


class RestoreNamesFlow(RepairsFlow):
    """Restore the names from before this start."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        if user_input is not None:
            await self.hass.services.async_call(
                DOMAIN,
                "restore_names",
                {"snapshot_id": "previous_run"},
                blocking=True,
                return_response=True,
            )
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm")


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    if issue_id == ISSUE_NAMES_CHANGED:
        return RestoreNamesFlow()
    raise ValueError(f"Unknown issue {issue_id}")
