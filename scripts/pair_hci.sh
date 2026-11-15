#!/usr/bin/env bash
# Pair one BlueZ HCI adapter (onboard or USB dongle) to one Bluetooth speaker (Pi).
# Chip-agnostic — any USB HCI that BlueZ enumerates (hciN) works the same.
# Usage:
#   ./scripts/pair_hci.sh hci0                 # scan + interactive
#   ./scripts/pair_hci.sh hci0 AA:BB:CC:DD:EE:01
#
# Note: Extra USB HCI dongles are soft-failed when unhealthy; multi-dongle
# A2DP scale-out is untested without hardware (ASUS BT600 was dead).
set -euo pipefail

HCI="${1:-}"
SPEAKER_MAC="${2:-}"

if [[ -z "$HCI" ]]; then
  echo "Usage: $0 <hciN> [speaker-mac]" >&2
  echo "Example: $0 hci0" >&2
  echo "         $0 hci0 AA:BB:CC:DD:EE:11" >&2
  exit 1
fi

if ! command -v bluetoothctl >/dev/null 2>&1; then
  echo "bluetoothctl not found — install bluez" >&2
  exit 1
fi

echo "==> Listing adapters"
bluetoothctl list || true

ADAPTER_MAC="$(bluetoothctl list 2>/dev/null | awk -v h="$HCI" 'index($0,h){print $2; exit}')"
if [[ -z "$ADAPTER_MAC" ]]; then
  echo "Could not resolve adapter MAC for $HCI. Is a USB HCI dongle plugged in / onboard up?" >&2
  echo "Check: ls /sys/class/bluetooth && bluetoothctl list" >&2
  exit 1
fi

echo "==> Using adapter $HCI ($ADAPTER_MAC)"
bluetoothctl select "$ADAPTER_MAC"
bluetoothctl power on
bluetoothctl agent on || true
bluetoothctl default-agent || true

if [[ -z "$SPEAKER_MAC" ]]; then
  echo "==> Scanning 12s — put speaker in pairing mode"
  bluetoothctl scan on &
  SCAN_PID=$!
  sleep 12
  kill "$SCAN_PID" 2>/dev/null || true
  bluetoothctl scan off || true
  echo
  bluetoothctl devices
  echo
  read -r -p "Speaker MAC: " SPEAKER_MAC
fi

SPEAKER_MAC="$(echo "$SPEAKER_MAC" | tr '[:lower:]-' '[:upper:]:')"
echo "==> Pair / trust / connect $SPEAKER_MAC on $HCI"
bluetoothctl pair "$SPEAKER_MAC" || true
bluetoothctl trust "$SPEAKER_MAC"
bluetoothctl connect "$SPEAKER_MAC"

echo
echo "Done. Speaker paired on $HCI ($SPEAKER_MAC)."
echo "No YAML edit needed — start MuSync and use Device setup in the UI:"
echo "  ./scripts/run_pi.sh"
echo "  Open http://<pi-ip>:8080 → Bluetooth search → Connect (HCI auto or pick)"
echo
echo "Verify PipeWire sink appeared:"
echo "  wpctl status | sed -n '/Sinks:/,/Sources:/p'"
