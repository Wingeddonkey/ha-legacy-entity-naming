# Changelog

## 0.1.0

First release. Tested against Home Assistant 2026.10.4 and 2026.11.2.

- Upgrade shield: stops the 2026.11 entity name migration when installed on 2026.10.
- `switch.legacy_entity_naming`: names exactly as typed in friendly_name, voice assistants and dashboards.
- Renaming a device no longer rewrites entity names.
- Dashboard names never repeat an area or device name.
- Name snapshots, Repairs alert for names an update changed, one-click restore.
- `restore_names` from a snapshot, a registry file or a (possibly encrypted) backup.
- Warning before untested HA updates; the display patch turns itself off on unknown versions.
