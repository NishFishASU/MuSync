# Wi-Fi join (home network or phone hotspot)

Join **home Wi-Fi or a phone personal hotspot** from the MuSync web UI so
you can reach the Pi for **SPP / LAN control** without SSH. The panel is
**not hotspot-only** — any SSID NetworkManager can associate with works.

## Preferred: NetworkManager (`nmcli`)

On Raspberry Pi OS with NetworkManager:

```bash
sudo apt install -y network-manager
# Ensure NetworkManager manages the Wi-Fi interface (wlan0)
nmcli general status
```

MuSync uses `nmcli` when `MUSYNC_BACKEND=pipewire` (Pi) and `nmcli` is on
`PATH`. Override with:

| Variable | Values | Meaning |
|----------|--------|---------|
| `MUSYNC_WIFI_BACKEND` | `auto` (default), `nmcli`, `mock` | Force Wi-Fi backend |

Passwords are **never** returned by the API and are redacted from nmcli error
text before it reaches logs/clients.

### API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/wifi` | Status: SSID, IPv4 list (for SPP), interface, backend |
| `GET` | `/api/wifi/networks` | Saved profiles (no passwords) |
| `POST` | `/api/wifi/networks` | Pre-save `{ "ssid", "password?" }` |
| `POST` | `/api/wifi/connect` | Join `{ "ssid", "password?" }` |
| `DELETE` | `/api/wifi/networks/{ssid}` | Forget a saved profile |

### Laptop / cloud

`MUSYNC_BACKEND=mock` (default) selects the **mock** Wi-Fi backend: in-memory
SSIDs and a fake IP `192.168.43.10` so the UI and tests work without a radio.

## Optional fallback: balena `wifi-connect`

If NetworkManager is unavailable or you need a captive-portal style first-boot
flow, [balena wifi-connect](https://github.com/balena-io/wifi-connect) can
stand up a temporary AP for credential entry. MuSync does **not** vendor or
automate wifi-connect; treat it as an optional OS-level bootstrap:

1. Install/run wifi-connect once to get the Pi onto a network.
2. Prefer switching to NetworkManager + MuSync `/api/wifi` for day-to-day
   joins (home or hotspot) from the same UI used for sinks/SPP.

## Pi test steps

1. Flash / boot Pi with NetworkManager; confirm `nmcli device wifi` lists APs.
2. Deploy MuSync, build UI (`./scripts/build_web.sh`), run `./scripts/run_pi.sh`
   (or systemd). First bring-up can use Ethernet or a pre-saved home SSID.
3. On a phone/laptop already on the Pi’s current network, open
   `http://<pi-ip>:8080` → **Wi-Fi** panel.
4. Enter **SSID / network name** (home router **or** phone hotspot) + password.
5. **Save only** (optional) → **Join**.
6. Confirm **Pi IP** updates (home LAN, or often `192.168.43.x` / `172.20.x` on hotspots).
7. Point Simple Protocol Player (or the SPP sink) at that IP and MuSync port
   (see SPP docs when present). Re-open the UI at `http://<new-ip>:8080` if
   your browser was on the old network.
8. **Forget** / re-**Join** a saved SSID without retyping the password.

### Mock smoke (laptop)

```bash
pip install -r requirements.txt && ./scripts/build_web.sh
export PYTHONPATH=backend MUSYNC_BACKEND=mock
pytest backend/tests/test_wifi.py
./scripts/run_dev.sh
# UI → Wi-Fi: Join MuSync-Dev / add a fake SSID; IP shows 192.168.43.10
```
