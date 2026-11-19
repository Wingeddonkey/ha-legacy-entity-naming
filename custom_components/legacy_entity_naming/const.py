"""Constants for Legacy Entity Naming."""

from __future__ import annotations

DOMAIN = "legacy_entity_naming"
VERSION = "0.1.0"

# Entity registry schema minor versions (major stays 1).
# 23 = HA 2026.10 and earlier (names stored exactly as typed).
# 24 = HA 2026.11, whose one-time migration strips device names from stored names.
REGISTRY_MINOR_LEGACY = 23
REGISTRY_MINOR_NEW_NAMING = 24

# HA releases this package has been checked against (year.month).
TESTED_HA_RELEASES = ("2026.10", "2026.11")

FRONTEND_URL_BASE = f"/{DOMAIN}_static"
FRONTEND_SCRIPT = "legacy-entity-naming.js"

SNAPSHOT_STORAGE_KEY = f"{DOMAIN}.snapshots"
SNAPSHOT_STORAGE_VERSION = 1
SNAPSHOT_KEEP = 10
SNAPSHOT_LIVE_SAVE_DELAY = 30

SERVICE_RESTORE_NAMES = "restore_names"
SERVICE_TAKE_SNAPSHOT = "take_snapshot"
SERVICE_LIST_SNAPSHOTS = "list_snapshots"

ISSUE_UNSUPPORTED_VERSION = "unsupported_version"
ISSUE_NAMES_CHANGED = "names_changed"
ISSUE_UNTESTED_UPDATE = "untested_update"
ISSUE_SHIELD_FAILED = "shield_failed"
