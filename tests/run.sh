#!/usr/bin/env bash
# Runs the upgrade scenarios in WSL against real HA 2026.10.4 -> 2026.11.2
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$1"   # path to custom_components/legacy_entity_naming
WORK=~/lenha/work
rm -rf "$WORK"; mkdir -p "$WORK"
rc=0
for kind in shielded unshielded; do
  cfg="$WORK/$kind"; mkdir -p "$cfg/custom_components"
  cp -r "$SRC" "$cfg/custom_components/"
  printf 'homeassistant:\n  name: test\n  time_zone: UTC\n  country: GB\n  unit_system: metric\nhttp:\n  server_port: 18124\nfrontend:\nlogger:\n  default: warning\n  logs:\n    custom_components.legacy_entity_naming: debug\n' > "$cfg/configuration.yaml"
  echo "########## $kind: 2026.10.4"
  ~/lenha/ha-2026.10.4/bin/python "$HERE/scenario.py" "$cfg" "seed_$kind" || rc=1
  echo "########## $kind: 2026.11.2"
  ~/lenha/ha-2026.11.2/bin/python "$HERE/scenario.py" "$cfg" "check_$kind" || rc=1
  echo "--- integration log lines ($kind)"
  grep -h "legacy_entity_naming\|Migrating core.entity_registry\|ERROR" "$cfg/home-assistant.log" 2>/dev/null | grep -v "^\s*$" | tail -25
done
exit $rc
