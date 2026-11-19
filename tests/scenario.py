"""Boot real Home Assistant on a config dir and check naming behaviour.

usage: python scenario.py <config_dir> <phase>
phases:
  seed_shielded    (2026.10) set up integration + fixtures, keep shield armed
  seed_unshielded  (2026.10) same, then disarm shield (simulates no protection)
  check_shielded   (2026.11) names must be untouched, patch + toggle + device rename
  check_unshielded (2026.11) migration ran: detection + restore from snapshot & file
"""

import asyncio
import json
import sys
from pathlib import Path

from homeassistant import bootstrap
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.runner import RuntimeConfig

DOMAIN = "legacy_entity_naming"
FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


async def boot(cfg: Path):
    hass = await bootstrap.async_setup_hass(
        RuntimeConfig(config_dir=str(cfg), skip_pip=True, safe_mode=False,
                      recovery_mode=False, debug=False, open_ui=False,
                      verbose=False, log_rotate_days=None, log_file=None,
                      log_no_color=True)
    )
    assert hass is not None, "bootstrap failed"
    await hass.async_start()
    await hass.async_block_till_done()
    return hass


def our_entry(hass):
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


async def seed(hass, shielded: bool):
    print(f"== seed on {HA_VERSION}")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    check(result["type"] == "create_entry", "config entry created")
    await hass.async_block_till_done()
    entry = our_entry(hass)
    devreg, entreg = dr.async_get(hass), er.async_get(hass)
    kitchen = ar.async_get(hass).async_create("Kitchen")

    plug = devreg.async_get_or_create(config_entry_id=entry.entry_id,
                                      identifiers={("test", "plug")}, name="Kitchen Plug")
    devreg.async_update_device(plug.id, area_id=kitchen.id)
    smart = devreg.async_get_or_create(config_entry_id=entry.entry_id,
                                       identifiers={("test", "smart3")}, name="Smart plug 3")

    def mk(uid, device, original, name, has_entity_name=True, area=None):
        e = entreg.async_get_or_create("sensor", "test", uid, config_entry=entry,
                                       device_id=device.id, original_name=original,
                                       has_entity_name=has_entity_name)
        entreg.async_update_entity(e.entity_id, name=name, area_id=area)
        return e.entity_id

    ids = {
        "prefixed": mk("power", plug, "Power", "Kitchen Plug Power"),
        "free": mk("switch", smart, "Switch", "Coffee machine"),
        "unnamed": mk("energy", smart, "Energy", None),
        "legacy_unnamed": mk("legacy", smart, "Smart plug 3 Voltage", None, has_entity_name=False),
        "own_area": mk("temp", plug, "Temperature", "Kitchen Plug Temp", area=kitchen.id),
    }
    (Path(hass.config.config_dir) / "ids.json").write_text(json.dumps(ids))
    await hass.async_block_till_done()

    sw = entreg.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_legacy_entity_naming")
    check(sw == "switch.legacy_entity_naming", f"switch entity id ({sw})")
    st = hass.states.get(sw)
    check(st is not None and st.attributes.get("upgrade_shield") == "armed",
          f"shield armed attribute ({st and st.attributes.get('upgrade_shield')})")

    if not shielded:
        from custom_components.legacy_entity_naming.shield import async_disarm_shield
        async_disarm_shield(hass)
        await hass.async_block_till_done()


def read_registry_file(cfg: Path):
    return json.loads((cfg / ".storage/core.entity_registry").read_text())


def names(hass, ids):
    entreg = er.async_get(hass)
    return {k: entreg.async_get(v).name for k, v in ids.items()}


def friendly(hass, ids):
    entreg = er.async_get(hass)
    return {k: er.async_get_legacy_friendly_name(hass, entreg.async_get(v)) for k, v in ids.items()}


async def check_shielded(hass, cfg):
    print(f"== check_shielded on {HA_VERSION}")
    ids = json.loads((cfg / "ids.json").read_text())
    n = names(hass, ids)
    check(n["prefixed"] == "Kitchen Plug Power", f"stored name not stripped ({n['prefixed']})")
    check(n["free"] == "Coffee machine", f"free name kept ({n['free']})")
    check(not ir.async_get(hass).async_get_issue(DOMAIN, "names_changed"), "no names_changed issue")

    f = friendly(hass, ids)
    print("  friendly (toggle on):", f)
    check(f["prefixed"] == "Kitchen Plug Power", "friendly prefixed = as typed")
    check(f["free"] == "Coffee machine", "friendly free = as typed (no device prefix)")
    check(f["unnamed"] == "Smart plug 3 Energy", f"unnamed keeps device+entity ({f['unnamed']})")
    check(f["legacy_unnamed"] == "Smart plug 3 Voltage", f"legacy unnamed ({f['legacy_unnamed']})")
    st = hass.states.get(ids["free"])
    check(st is not None and st.attributes.get("friendly_name") == "Coffee machine",
          f"state friendly_name ({st and st.attributes.get('friendly_name')})")
    full = er.async_get_full_entity_name(hass, er.async_get(hass).async_get(ids["free"]))
    check(full == "Coffee machine", f"computed (voice) name ({full})")

    # Live entity: rename our own switch and look at its state
    entreg = er.async_get(hass)
    entreg.async_update_entity("switch.legacy_entity_naming", name="Naming toggle")
    await hass.async_block_till_done()
    check(hass.states.get("switch.legacy_entity_naming").attributes["friendly_name"] == "Naming toggle",
          f"live entity friendly ({hass.states.get('switch.legacy_entity_naming').attributes['friendly_name']})")

    # Toggle off -> stock 2026.11 behaviour
    entreg.async_update_entity(ids["free"], name="Espresso")  # clears compat_name
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.legacy_entity_naming"}, blocking=True)
    await hass.async_block_till_done()
    f_off = friendly(hass, ids)
    print("  friendly (toggle off):", f_off)
    check(f_off["free"] == "Smart plug 3 Espresso", f"toggle off -> device prefixed ({f_off['free']})")
    sw = hass.states.get("switch.legacy_entity_naming")
    check(sw.state == "off", "switch is off")
    check(sw.attributes["friendly_name"] == "Legacy Entity Naming Naming toggle",
          f"live entity refreshed on toggle ({sw.attributes['friendly_name']})")
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.legacy_entity_naming"}, blocking=True)
    await hass.async_block_till_done()
    check(friendly(hass, ids)["free"] == "Espresso", "toggle on again -> as typed")
    check(hass.states.get("switch.legacy_entity_naming").attributes["friendly_name"] == "Naming toggle",
          "live entity refreshed on toggle back")

    # Device rename must not write names into entities
    devreg = dr.async_get(hass)
    dev = devreg.async_get(entreg.async_get(ids["free"]).device_id)
    before = names(hass, ids)
    devreg.async_update_device(dev.id, name_by_user="Bar plug")
    await hass.async_block_till_done()
    after = names(hass, ids)
    check(before == after, f"device rename left names alone ({before} -> {after})")
    f2 = friendly(hass, ids)
    check(f2["free"] == "Espresso", f"after device rename friendly ({f2['free']})")
    check(f2["unnamed"] == "Bar plug Energy", f"unnamed follows device rename ({f2['unnamed']})")
    check(f2["legacy_unnamed"] == "Bar plug Voltage",
          f"old-style entity after device rename, no repeat ({f2['legacy_unnamed']})")

    resp = await hass.services.async_call(DOMAIN, "list_snapshots", {}, blocking=True, return_response=True)
    check(resp["display_patch_active"] is True, "list_snapshots: patch active")
    check(len(resp["snapshots"]) >= 2, f"snapshots kept ({len(resp['snapshots'])})")

    # Pre-update warning
    issues = ir.async_get(hass)
    hass.states.async_set("update.home_assistant_core_update", "on",
                          {"latest_version": "2026.12.0", "installed_version": HA_VERSION})
    await hass.async_block_till_done()
    check(issues.async_get_issue(DOMAIN, "untested_update") is not None, "untested update warning raised")
    hass.states.async_set("update.home_assistant_core_update", "on",
                          {"latest_version": "2026.11.3", "installed_version": HA_VERSION})
    await hass.async_block_till_done()
    check(issues.async_get_issue(DOMAIN, "untested_update") is None, "no warning for tested release")

    # Unsupported version: display patch must switch itself off, names untouched
    import custom_components.legacy_entity_naming as pkg
    entry = our_entry(hass)
    saved = pkg.TESTED_HA_RELEASES
    pkg.TESTED_HA_RELEASES = ("2026.10",)
    try:
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        pkg.TESTED_HA_RELEASES = saved
    check(issues.async_get_issue(DOMAIN, "unsupported_version") is not None, "unsupported version repair raised")
    check(not hasattr(er.async_get_legacy_friendly_name, "__wrapped_original__"), "core functions restored to stock")
    check(names(hass, ids)["free"] == "Espresso", "stored names untouched when patch off")
    check(friendly(hass, ids)["free"] == "Bar plug Espresso", f"stock naming when off ({friendly(hass, ids)['free']})")
    sw = hass.states.get("switch.legacy_entity_naming")
    check(sw.attributes["display_patch_active"] is False, "switch reports patch inactive")
    resp = await hass.services.async_call(DOMAIN, "restore_names", {"dry_run": True, "snapshot_id": "previous_run"},
                                          blocking=True, return_response=True)
    check("changed" in resp, "restore still works while patch off")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    check(issues.async_get_issue(DOMAIN, "unsupported_version") is None, "repair cleared on supported version")
    check(friendly(hass, ids)["free"] == "Espresso", "patch back after reload")

    # Encrypted backup restore
    await test_backup_restore(hass, cfg, ids)

    # Removing the integration gives stock behaviour back, names untouched
    # (the test fixtures belong to our own config entry, so HA removes them too)
    await hass.config_entries.async_remove(our_entry(hass).entry_id)
    await hass.async_block_till_done()
    check(not hasattr(er.async_get_legacy_friendly_name, "__wrapped_original__"), "removed: core functions stock")
    check(not [i for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN], "removed: repairs cleared")


def make_backup(cfg: Path, password: str | None) -> Path:
    """Build an HA-style backup .tar containing the 2026.10 registry."""
    import io, tarfile
    from securetar import SecureTarArchive
    registry = (cfg / "registry-2026.10.json").read_bytes()
    out = cfg / f"backup-{'enc' if password else 'plain'}.tar"
    with SecureTarArchive(out, "w", password=password) as archive:
        with archive.create_tar("homeassistant.tar.gz", gzip=True) as inner:
            info = tarfile.TarInfo("data/.storage/core.entity_registry")
            info.size = len(registry)
            inner.addfile(info, io.BytesIO(registry))
    return out


async def test_backup_restore(hass, cfg, ids):
    for password in (None, "TEST-KEY-1234"):
        try:
            path = await hass.async_add_executor_job(make_backup, cfg, password)
        except Exception as err:  # noqa: BLE001
            check(False, f"could not build test backup: {err!r}")
            return
        er.async_get(hass).async_update_entity(ids["prefixed"], name="Power")
        data = {"file": str(path)}
        if password:
            data["password"] = password
        resp = await hass.services.async_call(DOMAIN, "restore_names", data, blocking=True, return_response=True)
        check(names(hass, ids)["prefixed"] == "Kitchen Plug Power",
              f"restored from {'encrypted' if password else 'plain'} backup ({resp['changed']})")
    if password:
        er.async_get(hass).async_update_entity(ids["prefixed"], name="Power")
        try:
            await hass.services.async_call(DOMAIN, "restore_names", {"file": str(path), "password": "wrong"},
                                           blocking=True, return_response=True)
            check(False, "wrong key should fail")
        except Exception as err:  # noqa: BLE001
            check("encryption key" in str(err) or "password" in str(err).lower(), f"wrong key gives clear error ({err})")


async def check_unshielded(hass, cfg):
    print(f"== check_unshielded on {HA_VERSION}")
    ids = json.loads((cfg / "ids.json").read_text())
    n = names(hass, ids)
    print("  names after migration:", n)
    check(n["prefixed"] == "Power", f"migration stripped name (control) ({n['prefixed']})")
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "names_changed")
    check(issue is not None and issue.is_fixable, "names_changed repair raised")
    if issue:
        print("  issue placeholders:", issue.translation_placeholders)

    dry = await hass.services.async_call(DOMAIN, "restore_names", {"dry_run": True}, blocking=True, return_response=True)
    check(dry["changed"] >= 1, f"dry run lists changes ({dry['changed']})")
    check(names(hass, ids)["prefixed"] == "Power", "dry run changed nothing")

    resp = await hass.services.async_call(DOMAIN, "restore_names", {}, blocking=True, return_response=True)
    print("  restore:", resp["changed"], resp["changes"])
    n2 = names(hass, ids)
    check(n2["prefixed"] == "Kitchen Plug Power", f"restored from previous_run ({n2['prefixed']})")
    check(ir.async_get(hass).async_get_issue(DOMAIN, "names_changed") is None, "issue cleared after restore")
    check(friendly(hass, ids)["prefixed"] == "Kitchen Plug Power", "friendly after restore")

    # Restore from a registry file (the 2026.10 copy)
    er.async_get(hass).async_update_entity(ids["prefixed"], name="Power")
    resp = await hass.services.async_call(
        DOMAIN, "restore_names", {"file": str(cfg / "registry-2026.10.json")},
        blocking=True, return_response=True)
    check(names(hass, ids)["prefixed"] == "Kitchen Plug Power", f"restored from file ({resp['changed']})")


async def main():
    cfg, phase = Path(sys.argv[1]), sys.argv[2]
    sys.path.insert(0, str(cfg))
    hass = await boot(cfg)
    try:
        if phase.startswith("seed"):
            await seed(hass, shielded=phase == "seed_shielded")
        elif phase == "check_shielded":
            await check_shielded(hass, cfg)
        elif phase == "check_unshielded":
            await check_unshielded(hass, cfg)
    finally:
        await hass.async_stop()
    if phase.startswith("seed"):
        data = read_registry_file(cfg)
        print(f"  registry file after stop: {data['version']}.{data['minor_version']}")
        want = 24 if phase == "seed_shielded" else 23
        check(data["minor_version"] == want, f"registry saved as 1.{want}")
        if want == 24:
            ents = {e["entity_id"]: e for e in data["data"]["entities"]}
            check(all("compat_name" in e for e in ents.values()), "compat_name written for all entities")
        (cfg / "registry-2026.10.json").write_text(json.dumps(data))
    print("RESULT:", "FAILED " + str(len(FAILS)) if FAILS else "ALL PASSED")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
