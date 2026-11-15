#!/usr/bin/env bash
# Stub: document / optionally apply Uno Q Debian headless BlueZ + PipeWire fixes.
#
# On Uno Q, SSH sessions often cannot see Bluetooth audio sinks until:
#   1) WirePlumber BlueZ seat-monitoring is disabled
#   2) lightdm greeter PipeWire units are masked (they steal the BT transport)
#   3) loginctl linger is enabled for the service user
#
# Default is dry-run. Pass --apply on the board after review.
# See docs/uno-q-hub.md — verify against your image before applying.

set -euo pipefail

APPLY=0
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=1
fi

WP_DROPIN=/etc/wireplumber/wireplumber.conf.d/80-bluez-headless.conf
USER_NAME="${MUSYNC_UNO_Q_USER:-arduino}"

cat <<EOF
MuSync Uno Q — headless BT audio configuration
  WirePlumber drop-in: ${WP_DROPIN}
  Service user:        ${USER_NAME}
  Mode:                $([[ "$APPLY" -eq 1 ]] && echo APPLY || echo DRY-RUN)
EOF

WP_BODY='wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
    monitor.bluez-midi.seat-monitoring = disabled
  }
}
'

if [[ "$APPLY" -eq 0 ]]; then
  echo
  echo "Would write:"
  echo "$WP_BODY"
  echo "Would mask lightdm user PipeWire/WirePlumber units under /var/lib/lightdm/.config/systemd/user/"
  echo "Would: loginctl enable-linger ${USER_NAME}"
  echo
  echo "Re-run with --apply on the Uno Q host after review, then reboot and:"
  echo "  bluetoothctl connect <mac> && wpctl status"
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "error: --apply requires root (sudo)" >&2
  exit 1
fi

mkdir -p /etc/wireplumber/wireplumber.conf.d
printf '%s\n' "$WP_BODY" >"$WP_DROPIN"

mkdir -p /var/lib/lightdm/.config/systemd/user
for u in pipewire.service pipewire.socket wireplumber.service \
         pipewire-pulse.service pipewire-pulse.socket; do
  ln -sf /dev/null "/var/lib/lightdm/.config/systemd/user/$u"
done
chown -R lightdm:lightdm /var/lib/lightdm/.config 2>/dev/null || true

loginctl enable-linger "$USER_NAME" || true

echo "Applied. Reboot, then pair/connect a speaker and check wpctl status."
