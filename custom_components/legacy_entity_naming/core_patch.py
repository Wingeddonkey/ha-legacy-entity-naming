"""Core naming patch for HA 2026.11 (entity registry schema 1.24).

Puts back the pre-2026.11 naming rules while the toggle is on:

* An entity you named yourself is called exactly that. No device or area name
  is added in front (``friendly_name``, voice assistant names, Assist).
* Entities you never named keep the old default: device name + entity name,
  computed the same way 2026.10 did.
* Renaming a device does not write anything into its entities' names.

Everything is a thin wrapper around HA's own functions, so turning the toggle
off gives HA's stock behaviour back immediately.
"""

from __future__ import annotations

from collections.abc import Callable
import inspect
import logging
import sys
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import DATA_ENTITY_PLATFORM
from homeassistant.helpers.typing import UNDEFINED

from .const import REGISTRY_MINOR_NEW_NAMING

_LOGGER = logging.getLogger(__name__)

_PATCHED_FLAG = "__legacy_entity_naming__"

# Registry functions wrapped by this module: name -> parameters we expect.
_EXPECTED_SIGNATURES: dict[str, tuple[str, ...]] = {
    "async_get_legacy_friendly_name": ("hass", "entry", "original_name"),
    "async_get_full_entity_name": ("hass", "entry"),
}


def _params(func: Callable[..., Any]) -> tuple[str, ...]:
    return tuple(inspect.signature(func).parameters)


def check_compatible() -> str | None:
    """Return None if this HA build can be patched, else the reason it can't."""
    if getattr(er, "STORAGE_VERSION_MINOR", None) != REGISTRY_MINOR_NEW_NAMING:
        return (
            f"entity registry schema is 1.{getattr(er, 'STORAGE_VERSION_MINOR', '?')}"
            f", expected 1.{REGISTRY_MINOR_NEW_NAMING}"
        )
    for name, expected in _EXPECTED_SIGNATURES.items():
        func = getattr(er, name, None)
        if func is None:
            return f"entity_registry.{name} is missing"
        func = getattr(func, "__wrapped_original__", func)
        if _params(func)[: len(expected)] != expected:
            return f"entity_registry.{name} signature changed"
    update = getattr(er.EntityRegistry, "_async_update_entity", None)
    if update is None:
        return "EntityRegistry._async_update_entity is missing"
    update = getattr(update, "__wrapped_original__", update)
    params = _params(update)
    for needed in ("name", "compat_name"):
        if needed not in params:
            return f"EntityRegistry._async_update_entity has no '{needed}' argument"
    if not hasattr(er.EntityRegistry, "async_device_modified"):
        return "EntityRegistry.async_device_modified is missing"
    return None


class CorePatch:
    """Installs and removes the wrappers."""

    def __init__(self, hass: HomeAssistant, is_enabled: Callable[[], bool]) -> None:
        self.hass = hass
        self._is_enabled = is_enabled
        self._originals: dict[str, Any] = {}

    @property
    def installed(self) -> bool:
        return bool(self._originals)

    @callback
    def install(self) -> None:
        if self.installed:
            return
        is_enabled = self._is_enabled

        # 1. friendly_name attribute (live entities and registry-only states)
        orig_friendly = er.async_get_legacy_friendly_name

        @callback
        def async_get_legacy_friendly_name(
            hass: HomeAssistant,
            entry: er.RegistryEntry,
            original_name: Any = UNDEFINED,
        ) -> str:
            if is_enabled() and entry.name:
                return entry.name
            return orig_friendly(hass, entry, original_name)

        # 2. Computed name: voice assistants, Assist, COMPUTED_NAME aliases
        orig_full = er.async_get_full_entity_name

        @callback
        def async_get_full_entity_name(
            hass: HomeAssistant, entry: er.RegistryEntry
        ) -> str:
            if is_enabled() and entry.name:
                return entry.name
            return orig_full(hass, entry)

        # 3. Device rename must not write names into the device's entities
        orig_update = er.EntityRegistry._async_update_entity  # noqa: SLF001

        def _async_update_entity(
            registry: er.EntityRegistry, entity_id: str, **kwargs: Any
        ) -> Any:
            if (
                is_enabled()
                and sys._getframe(1).f_code.co_name  # noqa: SLF001
                == "async_device_modified"
            ):
                # Keep the user's stored name and its preserved compat name.
                kwargs.pop("name", None)
                kwargs.pop("compat_name", None)
                # Older-style entities (no has_entity_name): HA re-strips the
                # integration's name against the *new* device name. That
                # fails after a rename and the old device name would show up
                # twice ("Bar plug Smart plug 3 Voltage"). Keep the part that
                # was already stripped so it reads "Bar plug Voltage", which is
                # what 2026.10 showed, without writing a stored name.
                if (
                    "original_name_unprefixed" in kwargs
                    and kwargs["original_name_unprefixed"] is None
                    and (old := registry.entities.get(entity_id)) is not None
                    and old.original_name_unprefixed is not None
                ):
                    kwargs.pop("original_name_unprefixed")
            return orig_update(registry, entity_id, **kwargs)

        for func, orig in (
            (async_get_legacy_friendly_name, orig_friendly),
            (async_get_full_entity_name, orig_full),
            (_async_update_entity, orig_update),
        ):
            func.__wrapped_original__ = orig  # type: ignore[attr-defined]
            setattr(func, _PATCHED_FLAG, True)

        self._originals = {
            "async_get_legacy_friendly_name": orig_friendly,
            "async_get_full_entity_name": orig_full,
            "_async_update_entity": orig_update,
        }
        er.async_get_legacy_friendly_name = async_get_legacy_friendly_name
        er.async_get_full_entity_name = async_get_full_entity_name
        er.EntityRegistry._async_update_entity = _async_update_entity  # noqa: SLF001
        _LOGGER.info("Legacy entity naming patch installed")
        self.refresh_names()

    @callback
    def uninstall(self) -> None:
        if not self.installed:
            return
        er.async_get_legacy_friendly_name = self._originals[
            "async_get_legacy_friendly_name"
        ]
        er.async_get_full_entity_name = self._originals["async_get_full_entity_name"]
        er.EntityRegistry._async_update_entity = self._originals[  # noqa: SLF001
            "_async_update_entity"
        ]
        self._originals = {}
        _LOGGER.info("Legacy entity naming patch removed")
        self.refresh_names()

    @callback
    def refresh_names(self) -> None:
        """Recompute friendly_name for every live entity."""
        count = 0
        for platforms in self.hass.data.get(DATA_ENTITY_PLATFORM, {}).values():
            for platform in platforms:
                for entity in list(platform.entities.values()):
                    entity._cached_friendly_name = None  # noqa: SLF001
                    if entity.hass is not None and entity.entity_id:
                        try:
                            entity.async_write_ha_state()
                            count += 1
                        except Exception:  # noqa: BLE001
                            _LOGGER.debug(
                                "Could not refresh %s", entity.entity_id, exc_info=True
                            )
        _LOGGER.debug("Refreshed friendly names of %s entities", count)
