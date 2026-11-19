"""Upgrade shield.

Runs on HA versions whose entity registry schema is still 1.23 (2026.10 and
earlier). It makes HA itself save the entity registry as schema 1.24, which is
the schema 2026.11 migrates *to*. When 2026.11 starts it finds nothing to
migrate, so the stored entity names are left exactly as the user typed them.

The 1.24 schema adds one field per entity, ``compat_name``. The shield fills
it with the entity's current name, which is also what 2026.11's own migration
does for names it keeps, so the friendly_name attribute stays the same even
without the core patch.

HA 2026.10 reads a 1.24 file fine: a newer minor version runs its migration
function, every "older than" step is skipped, and the data loads unchanged.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

import orjson

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import REGISTRY_MINOR_LEGACY, REGISTRY_MINOR_NEW_NAMING

_LOGGER = logging.getLogger(__name__)

_ARMED_FLAG = "_legacy_entity_naming_shield"


class ShieldState(StrEnum):
    """Result of arming the shield."""

    ARMED = "armed"  # Running pre-2026.11, registry will be saved as 1.24
    NOT_NEEDED = "not_needed"  # Running 2026.11+, migration already decided
    FAILED = "failed"  # Pre-2026.11 but internals not as expected


def _fragment_to_dict(fragment: Any) -> dict[str, Any]:
    """Turn a cached json fragment (or a dict) back into a dict."""
    if isinstance(fragment, dict):
        return dict(fragment)
    return orjson.loads(orjson.dumps(fragment))


@callback
def async_arm_shield(hass: HomeAssistant) -> ShieldState:
    """Arm the upgrade shield if this HA version needs it."""
    code_minor = getattr(er, "STORAGE_VERSION_MINOR", None)
    if code_minor is None or er.STORAGE_VERSION_MAJOR != 1:
        return ShieldState.FAILED
    if code_minor >= REGISTRY_MINOR_NEW_NAMING:
        return ShieldState.NOT_NEEDED
    if code_minor != REGISTRY_MINOR_LEGACY:
        _LOGGER.warning(
            "Entity registry schema 1.%s is older than expected; shield not armed",
            code_minor,
        )
        return ShieldState.FAILED

    registry = er.async_get(hass)
    if getattr(registry, _ARMED_FLAG, False):
        return ShieldState.ARMED

    store = getattr(registry, "_store", None)
    original_data_to_save = getattr(registry, "_data_to_save", None)
    if store is None or original_data_to_save is None or not hasattr(
        store, "minor_version"
    ):
        _LOGGER.error("Entity registry internals changed; upgrade shield not armed")
        return ShieldState.FAILED

    def _data_to_save() -> dict[str, Any]:
        data = original_data_to_save()
        entities = []
        for fragment in data["entities"]:
            entity = _fragment_to_dict(fragment)
            entity.setdefault("compat_name", entity.get("name"))
            entities.append(entity)
        data["entities"] = entities
        return data

    store.minor_version = REGISTRY_MINOR_NEW_NAMING
    registry._data_to_save = _data_to_save  # noqa: SLF001
    setattr(registry, _ARMED_FLAG, True)
    # Write the file now so it is already 1.24 if HA is updated straight away.
    registry.async_schedule_save()
    _LOGGER.info(
        "Upgrade shield armed: entity registry is saved as schema 1.%s, so the "
        "2026.11 entity name migration will not run",
        REGISTRY_MINOR_NEW_NAMING,
    )
    return ShieldState.ARMED


@callback
def async_disarm_shield(hass: HomeAssistant) -> None:
    """Disarm the shield (integration removed). Next save writes 1.23 again."""
    registry = er.async_get(hass)
    if not getattr(registry, _ARMED_FLAG, False):
        return
    registry._store.minor_version = er.STORAGE_VERSION_MINOR  # noqa: SLF001
    del registry._data_to_save  # noqa: SLF001  (falls back to the class method)
    setattr(registry, _ARMED_FLAG, False)
    registry.async_schedule_save()
    _LOGGER.warning(
        "Upgrade shield disarmed: updating to 2026.11 will now migrate entity names"
    )
