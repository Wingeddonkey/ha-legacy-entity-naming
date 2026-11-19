"""The legacy naming toggle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LegacyNamingConfigEntry
from .const import DOMAIN, VERSION


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LegacyNamingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([LegacyNamingSwitch(entry)])


class LegacyNamingSwitch(SwitchEntity, RestoreEntity):
    """On: names exactly as you typed them. Off: Home Assistant's new naming."""

    # Main entity of its device: named "Legacy Entity Naming", no prefixing
    _attr_has_entity_name = True
    _attr_name = None
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:form-textbox"

    def __init__(self, entry: LegacyNamingConfigEntry) -> None:
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_legacy_entity_naming"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Legacy Entity Naming",
            entry_type=DeviceEntryType.SERVICE,
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._runtime.set_enabled(last.state != "off")
        self._runtime.switch_listeners.append(self._changed)
        self.async_on_remove(lambda: self._runtime.switch_listeners.remove(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._runtime.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        runtime = self._runtime
        return {
            "upgrade_shield": runtime.shield.value,
            "display_patch_active": runtime.display_patch_active,
            "display_patch_blocked_reason": runtime.patch_blocked_reason,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._runtime.set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._runtime.set_enabled(False)
