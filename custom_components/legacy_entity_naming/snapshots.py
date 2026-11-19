"""Name snapshots, change detection and restore.

A snapshot is a record of every entity name *you* set (the registry ``name``
field), keyed by entity_id, plus the platform/unique_id so an entity can still
be matched if its entity_id was changed.

* A "live" snapshot follows your own edits while HA runs.
* At every start the live snapshot from the previous run is compared with the
  registry as it was loaded. Anything that differs changed while HA was
  stopped or starting, which means an update or migration did it, not you.
* The last SNAPSHOT_KEEP start snapshots are kept for restoring.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import tarfile
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, __version__ as HA_VERSION
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    SNAPSHOT_KEEP,
    SNAPSHOT_LIVE_SAVE_DELAY,
    SNAPSHOT_STORAGE_KEY,
    SNAPSHOT_STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

REGISTRY_MEMBER_SUFFIX = ".storage/core.entity_registry"


@dataclass(slots=True)
class NameChange:
    """One entity whose stored name differs from a reference."""

    entity_id: str
    current: str | None
    expected: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "current": self.current,
            "expected": self.expected,
        }


def _names_from_registry(registry: er.EntityRegistry) -> dict[str, dict[str, Any]]:
    return {
        entry.entity_id: {
            "name": entry.name,
            "platform": entry.platform,
            "unique_id": entry.unique_id,
        }
        for entry in registry.entities.values()
    }


def _names_from_registry_file(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Read names from a core.entity_registry file of any 1.x schema."""
    if data.get("key") != "core.entity_registry" or "data" not in data:
        raise HomeAssistantError("Not a core.entity_registry file")
    return {
        entity["entity_id"]: {
            "name": entity.get("name"),
            "platform": entity.get("platform"),
            "unique_id": entity.get("unique_id"),
        }
        for entity in data["data"].get("entities", [])
    }


def read_names_from_file(path: str, password: str | None = None) -> dict[str, Any]:
    """Read names from a registry file or an HA backup (.tar). Runs in executor.

    For backups the registry is read from the backup's homeassistant archive,
    decrypting it with the backup encryption key if one is given.
    """
    file = Path(path)
    if not file.is_file():
        raise HomeAssistantError(f"File not found: {path}")

    if not tarfile.is_tarfile(file):
        with file.open(encoding="utf-8") as handle:
            return _names_from_registry_file(json.load(handle))

    try:
        from securetar import SecureTarArchive, SecureTarFile  # noqa: PLC0415
    except ImportError as err:
        raise HomeAssistantError("securetar is not available") from err

    with SecureTarArchive(file, "r", password=password) as archive:
        for inner_name, gzip in (
            ("homeassistant.tar.gz", True),
            ("homeassistant.tar", False),
        ):
            try:
                inner = archive.tar.extractfile(inner_name)
            except KeyError:
                continue
            if inner is None:
                continue
            try:
                with SecureTarFile(
                    file, gzip=gzip, password=password, fileobj=inner
                ) as ha_tar:
                    for member in ha_tar:
                        if member.name.endswith(REGISTRY_MEMBER_SUFFIX):
                            handle = ha_tar.extractfile(member)
                            if handle is None:
                                break
                            return _names_from_registry_file(json.load(handle))
            except (tarfile.ReadError, OSError) as err:
                raise HomeAssistantError(
                    "Could not open the backup. If it is encrypted, pass the "
                    "backup encryption key as 'password'"
                ) from err
            raise HomeAssistantError("No entity registry found in this backup")
    raise HomeAssistantError("This backup does not contain Home Assistant data")


class SnapshotManager:
    """Keeps the name snapshots."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, SNAPSHOT_STORAGE_VERSION, SNAPSHOT_STORAGE_KEY, private=True
        )
        self._data: dict[str, Any] = {"live": None, "snapshots": []}
        self._unsubs: list[Any] = []

    async def async_load(self) -> None:
        if (stored := await self._store.async_load()) is not None:
            self._data = stored

    # ---- snapshots -----------------------------------------------------------

    def _make(self, reason: str) -> dict[str, Any]:
        return {
            "id": dt_util.utcnow().strftime("%Y%m%dT%H%M%S%fZ"),
            "created": dt_util.utcnow().isoformat(),
            "ha_version": HA_VERSION,
            "registry_schema": f"1.{er.STORAGE_VERSION_MINOR}",
            "reason": reason,
            "names": _names_from_registry(er.async_get(self.hass)),
        }

    async def async_take(self, reason: str) -> dict[str, Any]:
        snapshot = self._make(reason)
        self._data["snapshots"] = [*self._data["snapshots"], snapshot][-SNAPSHOT_KEEP:]
        await self._store.async_save(self._data)
        return snapshot

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": s["id"],
                "created": s["created"],
                "ha_version": s["ha_version"],
                "registry_schema": s.get("registry_schema"),
                "reason": s["reason"],
                "named_entities": sum(1 for v in s["names"].values() if v["name"]),
            }
            for s in reversed(self._data["snapshots"])
        ]

    def get(self, snapshot_id: str | None) -> dict[str, Any]:
        snapshots = self._data["snapshots"]
        if not snapshots and self._data["live"] is None:
            raise HomeAssistantError("No snapshots stored yet")
        if snapshot_id in (None, "", "previous_run"):
            # The live snapshot from before this start: the best reference
            # after an update renamed things.
            if self._data.get("previous_live") is not None:
                return self._data["previous_live"]
            return snapshots[-1]
        for snapshot in snapshots:
            if snapshot["id"] == snapshot_id:
                return snapshot
        raise HomeAssistantError(f"Snapshot {snapshot_id} not found")

    # ---- change detection ----------------------------------------------------

    def detect_changes_since_last_run(self) -> list[NameChange]:
        """Compare the registry as loaded now with how the last run left it."""
        previous = self._data.get("live")
        self._data["previous_live"] = previous
        if previous is None:
            return []
        return diff_names(er.async_get(self.hass), previous["names"])

    # ---- live tracking ---------------------------------------------------------

    @callback
    def async_start_live_tracking(self) -> None:
        self._data["live"] = self._make("live")
        self._store.async_delay_save(lambda: self._data, SNAPSHOT_LIVE_SAVE_DELAY)

        @callback
        def _registry_updated(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
            if event.data["action"] == "update" and "name" not in event.data.get(
                "changes", {}
            ):
                return
            self._data["live"] = self._make("live")
            self._store.async_delay_save(lambda: self._data, SNAPSHOT_LIVE_SAVE_DELAY)

        async def _stop(_: Event) -> None:
            self._data["live"] = self._make("live")
            await self._store.async_save(self._data)

        self._unsubs.append(
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_updated)
        )
        self._unsubs.append(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
        )

    @callback
    def async_stop_live_tracking(self) -> None:
        while self._unsubs:
            try:
                self._unsubs.pop()()
            except ValueError:
                pass


def diff_names(
    registry: er.EntityRegistry, reference: dict[str, dict[str, Any]]
) -> list[NameChange]:
    """List entities whose stored name differs from the reference."""
    by_unique: dict[tuple[str, str], dict[str, Any]] = {
        (ref["platform"], ref["unique_id"]): ref
        for ref in reference.values()
        if ref.get("platform") and ref.get("unique_id")
    }
    changes = []
    for entry in registry.entities.values():
        ref = reference.get(entry.entity_id) or by_unique.get(
            (entry.platform, entry.unique_id)
        )
        if ref is None:
            continue
        if (ref.get("name") or None) != (entry.name or None):
            changes.append(NameChange(entry.entity_id, entry.name, ref.get("name")))
    return changes


@callback
def async_apply_names(
    hass: HomeAssistant,
    reference: dict[str, dict[str, Any]],
    *,
    entity_ids: list[str] | None = None,
    dry_run: bool = False,
) -> list[NameChange]:
    """Write the reference names back into the entity registry."""
    registry = er.async_get(hass)
    changes = diff_names(registry, reference)
    if entity_ids:
        wanted = set(entity_ids)
        changes = [c for c in changes if c.entity_id in wanted]
    if not dry_run:
        for change in changes:
            registry.async_update_entity(change.entity_id, name=change.expected)
        _LOGGER.info("Restored %s entity names", len(changes))
    return changes
