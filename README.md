# Legacy Entity Naming

Keeps Home Assistant entity names exactly as you typed them.

Home Assistant 2026.11 changed how names are built. A name is now the device name plus the entity name. On first start, the update also **rewrites the names you saved** to remove the device name from the front. As a result:

- "Coffee machine" on the device "Smart plug 3" shows as **"Smart plug 3 Coffee machine"**
- cards that show area + entity show **"Kitchen Kitchen Plug"**
- once you edit an entity, its old `friendly_name` is gone

This integration turns that off.

| Feature | What it does |
|---|---|
| **Upgrade shield** | Install on **2026.10 or earlier, before you update**. The 2026.11 migration then finds nothing to do, and your stored names are not touched. |
| **Toggle** `switch.legacy_entity_naming` | **On**: names exactly as typed, with no device or area name added in front. Applies to `friendly_name`, voice assistants and every dashboard card. **Off**: Home Assistant's own 2026.11 naming. Changes live, no restart. |
| **No rename on device rename** | Renaming a device no longer writes anything into its entities' names. |
| **No repeated parts** | A card set to show "area + entity" or "device + entity" never shows the same word twice. |
| **Name snapshots** | All entity names are saved at every start (last 10 are kept), and the live copy follows your own edits. |
| **Change alert** | If an update changes names while HA is stopped or starting, a **Repairs** alert lists them, with one-click restore. |
| **Pre-update warning** | If an HA update this version hasn't been tested against is available, a Repairs warning says to wait. |
| **Fail-safe** | On an untested HA version, the display patch turns itself off (stock naming, stored names untouched) and raises a Repairs notice. Snapshots and restore keep working. |

Tested against Home Assistant **2026.10.4** and **2026.11.2** (registry schema 1.23 → 1.24).

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add this repository, type **Integration**.
2. Install **Legacy Entity Naming** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Legacy Entity Naming.**

### Manual install

Copy `custom_components/legacy_entity_naming` into `/config/custom_components/` and restart.

## Upgrading to 2026.11: do it in this order

1. Still on 2026.10: install and add the integration (above).
2. Check that `switch.legacy_entity_naming` has the attribute `upgrade_shield: armed`.
3. Take a backup, then update Home Assistant.
4. After the update, `upgrade_shield` reads `not_needed` and `display_patch_active` reads `true`.

How the shield works: while it's armed, HA saves its entity registry already marked as the 2026.11 schema (1.24), with the one new field (`compat_name`) filled in. On first start, 2026.11 sees the file is already 1.24 and skips the name migration. HA 2026.10 reads a 1.24 file without problems.

## Already updated to 2026.11?

The migration has already removed the device name from the front of the names you saved. Restore them:

- **From a backup made before the update** (Developer tools → Actions):

  ```yaml
  action: legacy_entity_naming.restore_names
  data:
    file: /backup/<backup-file>.tar
    password: "<backup encryption key>"   # only if the backup is encrypted
    dry_run: true                          # look first, then run again with false
  ```

- **From a copy of the registry file**: `file: /config/core.entity_registry.pre2026-11`

`entity_id` can limit the restore to some entities. The action returns the list of changes.

## Actions

| Action | Purpose |
|---|---|
| `legacy_entity_naming.restore_names` | Restore names from `snapshot_id` (default: `previous_run`, the names before this start), a registry `file`, or a backup `.tar`. Supports `dry_run` and `entity_id`. |
| `legacy_entity_naming.take_snapshot` | Save a snapshot now, for example before an update. |
| `legacy_entity_naming.list_snapshots` | List snapshots and show the shield and patch status. |

## What it changes inside Home Assistant

It only wraps HA's own functions, so turning the switch off or removing the integration gives back stock behaviour.

- `entity_registry.async_get_legacy_friendly_name` / `async_get_full_entity_name`: a name you set is returned as-is.
- `EntityRegistry._async_update_entity`: when called from a device rename, it does not write a name.
- Frontend: a small module wraps `hass.formatEntityName`. Default names use the entity's `friendly_name`, and picked parts never repeat. Names you type on a card are never changed.
- Upgrade shield (2026.10 only): saves the entity registry as schema 1.24.

## Removing it

Delete the integration entry. This disarms the shield, so an update to 2026.11 will migrate names again. Remove the files and restart. Your stored names stay as they are.

## Caveats

- This patches Home Assistant internals. Every HA release has to be checked, and the package refuses to patch versions it doesn't know.
- The shield can only stop the **2026.11** migration. A different future migration can't be blocked in advance. That's why snapshots, the change alert and restore exist.
- Not affiliated with or endorsed by the Home Assistant project.
