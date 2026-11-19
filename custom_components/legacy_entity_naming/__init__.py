"""Legacy Entity Naming.

Keeps Home Assistant entity names the way you typed them:

* Upgrade shield: installed before 2026.11, it stops the 2026.11 one-time
  migration from rewriting stored names.
* Toggle (switch.legacy_entity_naming): on 2026.11+, no device or area name is
  added in front of names you set, in core (friendly_name, voice) and in the
  dashboards (frontend module).
* Safety net: name snapshots at every start, detection of names changed by an
  update, one-click restore, and a warning before untested HA updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, __version__ as HA_VERSION
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    FRONTEND_SCRIPT,
    FRONTEND_URL_BASE,
    ISSUE_NAMES_CHANGED,
    ISSUE_SHIELD_FAILED,
    ISSUE_UNSUPPORTED_VERSION,
    ISSUE_UNTESTED_UPDATE,
    SERVICE_LIST_SNAPSHOTS,
    SERVICE_RESTORE_NAMES,
    SERVICE_TAKE_SNAPSHOT,
    TESTED_HA_RELEASES,
    VERSION,
)
from .core_patch import CorePatch, check_compatible
from .shield import ShieldState, async_arm_shield, async_disarm_shield
from .snapshots import (
    NameChange,
    SnapshotManager,
    async_apply_names,
    read_names_from_file,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CORE_UPDATE_ENTITY = "update.home_assistant_core_update"


def _release(version: str | None) -> str | None:
    """'2026.11.2' -> '2026.11'."""
    if not version:
        return None
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else None


@dataclass
class LegacyNaming:
    """Runtime data."""

    hass: HomeAssistant
    snapshots: SnapshotManager
    shield: ShieldState = ShieldState.NOT_NEEDED
    patch: CorePatch | None = None
    patch_blocked_reason: str | None = None
    enabled: bool = True
    pending_changes: list[NameChange] = field(default_factory=list)
    switch_listeners: list[Any] = field(default_factory=list)

    @property
    def display_patch_active(self) -> bool:
        return self.patch is not None and self.patch.installed and self.enabled

    @callback
    def set_enabled(self, enabled: bool) -> None:
        if enabled == self.enabled:
            return
        self.enabled = enabled
        if self.patch is not None and self.patch.installed:
            self.patch.refresh_names()
        for listener in self.switch_listeners:
            listener()


type LegacyNamingConfigEntry = ConfigEntry[LegacyNaming]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services and the frontend module."""
    await _async_register_frontend(hass)
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LegacyNamingConfigEntry) -> bool:
    """Set up from the config entry."""
    snapshots = SnapshotManager(hass)
    await snapshots.async_load()
    runtime = LegacyNaming(hass=hass, snapshots=snapshots)
    entry.runtime_data = runtime

    # 1. Names changed while HA was stopped (an update or migration)?
    runtime.pending_changes = snapshots.detect_changes_since_last_run()
    await snapshots.async_take("startup")
    snapshots.async_start_live_tracking()
    _async_update_names_changed_issue(hass, runtime)

    # 2. Upgrade shield (pre-2026.11 only)
    runtime.shield = async_arm_shield(hass)
    if runtime.shield is ShieldState.FAILED:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_SHIELD_FAILED,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_SHIELD_FAILED,
            translation_placeholders={"ha_version": HA_VERSION},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_SHIELD_FAILED)

    # 3. Core naming patch (2026.11+ only)
    if runtime.shield is ShieldState.NOT_NEEDED:
        reason = check_compatible()
        if reason is None and _release(HA_VERSION) not in TESTED_HA_RELEASES:
            reason = f"Home Assistant {HA_VERSION} has not been tested yet"
        runtime.patch_blocked_reason = reason
        if reason is None:
            runtime.patch = CorePatch(hass, lambda: runtime.enabled)
            runtime.patch.install()
            ir.async_delete_issue(hass, DOMAIN, ISSUE_UNSUPPORTED_VERSION)
        else:
            _LOGGER.warning(
                "Legacy naming display patch is off: %s. Snapshots and restore "
                "keep working",
                reason,
            )
            ir.async_create_issue(
                hass,
                DOMAIN,
                ISSUE_UNSUPPORTED_VERSION,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_UNSUPPORTED_VERSION,
                translation_placeholders={"ha_version": HA_VERSION, "reason": reason},
            )

    # 4. Warn before untested HA updates
    _async_watch_core_update(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LegacyNamingConfigEntry) -> bool:
    """Unload. The shield stays armed until the integration is removed."""
    runtime = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime.snapshots.async_stop_live_tracking()
        if runtime.patch is not None:
            runtime.patch.uninstall()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Integration deleted: disarm the shield and clear our repairs."""
    async_disarm_shield(hass)
    for issue in (
        ISSUE_NAMES_CHANGED,
        ISSUE_SHIELD_FAILED,
        ISSUE_UNSUPPORTED_VERSION,
        ISSUE_UNTESTED_UPDATE,
    ):
        ir.async_delete_issue(hass, DOMAIN, issue)


# ---------------------------------------------------------------------------
# Frontend module
# ---------------------------------------------------------------------------


async def _async_register_frontend(hass: HomeAssistant) -> None:
    path = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL_BASE, str(path), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{FRONTEND_URL_BASE}/{FRONTEND_SCRIPT}?v={VERSION}")


# ---------------------------------------------------------------------------
# Repairs
# ---------------------------------------------------------------------------


@callback
def _async_update_names_changed_issue(hass: HomeAssistant, runtime: LegacyNaming) -> None:
    changes = runtime.pending_changes
    if not changes:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_NAMES_CHANGED)
        return
    sample = "\n".join(
        f"- {c.entity_id}: “{c.expected or '(no name)'}” → “{c.current or '(no name)'}”"
        for c in changes[:15]
    )
    if len(changes) > 15:
        sample += f"\n- … and {len(changes) - 15} more"
    _LOGGER.warning(
        "%s entity names changed while Home Assistant was stopped or starting", len(changes)
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_NAMES_CHANGED,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_NAMES_CHANGED,
        translation_placeholders={"count": str(len(changes)), "changes": sample},
    )


@callback
def _async_watch_core_update(hass: HomeAssistant, entry: LegacyNamingConfigEntry) -> None:
    @callback
    def _check(*_: Any) -> None:
        state = hass.states.get(CORE_UPDATE_ENTITY)
        latest = state.attributes.get("latest_version") if state else None
        installed = state.attributes.get("installed_version") if state else None
        if (
            state is None
            or state.state != "on"
            or not latest
            or latest == installed
            or _release(latest) in TESTED_HA_RELEASES
        ):
            ir.async_delete_issue(hass, DOMAIN, ISSUE_UNTESTED_UPDATE)
            return
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_UNTESTED_UPDATE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_UNTESTED_UPDATE,
            translation_placeholders={
                "latest": latest,
                "tested": ", ".join(TESTED_HA_RELEASES),
            },
        )

    entry.async_on_unload(
        async_track_state_change_event(hass, [CORE_UPDATE_ENTITY], _check)
    )
    entry.async_on_unload(async_at_started(hass, _check))


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def _runtime(hass: HomeAssistant) -> LegacyNaming:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Legacy Entity Naming is not set up")
    return entries[0].runtime_data


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    async def restore_names(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        if file := call.data.get("file"):
            reference = await hass.async_add_executor_job(
                read_names_from_file, file, call.data.get("password")
            )
            source = file
        else:
            snapshot = runtime.snapshots.get(call.data.get("snapshot_id"))
            reference = snapshot["names"]
            source = f"snapshot {snapshot['id']} ({snapshot['ha_version']})"
        changes = async_apply_names(
            hass,
            reference,
            entity_ids=call.data.get("entity_id"),
            dry_run=call.data["dry_run"],
        )
        if not call.data["dry_run"]:
            runtime.pending_changes = []
            _async_update_names_changed_issue(hass, runtime)
        return {
            "source": source,
            "dry_run": call.data["dry_run"],
            "changed": len(changes),
            "changes": [c.as_dict() for c in changes],
        }

    async def take_snapshot(call: ServiceCall) -> ServiceResponse:
        snapshot = await _runtime(hass).snapshots.async_take("manual")
        return {"id": snapshot["id"], "created": snapshot["created"]}

    async def list_snapshots(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        return {
            "shield": runtime.shield.value,
            "display_patch_active": runtime.display_patch_active,
            "display_patch_blocked_reason": runtime.patch_blocked_reason,
            "snapshots": runtime.snapshots.list(),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE_NAMES,
        restore_names,
        schema=vol.Schema(
            {
                vol.Exclusive("snapshot_id", "source"): cv.string,
                vol.Exclusive("file", "source"): cv.string,
                vol.Optional("password"): cv.string,
                vol.Optional("entity_id"): cv.entity_ids,
                vol.Optional("dry_run", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TAKE_SNAPSHOT,
        take_snapshot,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_SNAPSHOTS,
        list_snapshots,
        supports_response=SupportsResponse.ONLY,
    )
