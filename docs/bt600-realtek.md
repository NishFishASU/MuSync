# ASUS BT600 (RTL8761) on Raspberry Pi 5

Hardware notes for getting **ASUS USB-BT500 / BT600-class** dongles (`0b05:1d70`, Realtek **RTL8761BU**) stable enough for MuSync A2DP sinks.

**Known-good baseline on this project:** Pi 5 **onboard** Bluetooth → Qudelix-5K already plays local media through MuSync/PipeWire. Use that path to confirm software is fine; treat BT600 failures as **adapter / USB / firmware**, not the app.

## Identify the dongle

```bash
lsusb | grep -i '0b05:1d70\|asus\|realtek'
# expect something like: ID 0b05:1d70 ASUSTek ...

dmesg -T | grep -iE 'bluetooth|rtl8761|btusb|hci' | tail -n 40
```

Each plugged BT600 should appear as its own HCI adapter:

```bash
bluetoothctl list
# Controller AA:BB:… hci0 [default]
# Controller …       hci1
ls /sys/class/bluetooth
```

If `lsusb` sees `0b05:1d70` but `bluetoothctl list` does **not** show a new controller, stop pairing and fix USB/firmware first.

## Symptom: connect then drop (`reg16 -110`)

Journal / `dmesg` loops like:

```text
Bluetooth: hciX: ACL packet ...
Bluetooth: hciX: unexpected event ...
... status 0x0c / -110
```

or BlueZ disconnect shortly after `connect` with **errno -110** (timeout) are a classic **RTL8761 + Pi USB** failure mode—not a MuSync bug.

Work through the checklist below in order.

## 1. Firmware packages

On Raspberry Pi OS (Bookworm+):

```bash
sudo apt update
sudo apt install -y bluez bluez-firmware firmware-realtek \
  firmware-misc-nonfree
# Some images also need:
# sudo apt install -y linux-firmware
```

Confirm firmware blobs exist (names vary by image):

```bash
ls /lib/firmware/rtl_bt/ 2>/dev/null | grep -i 8761 || true
ls /usr/lib/firmware/rtl_bt/ 2>/dev/null | grep -i 8761 || true
```

Replug the dongle (or reboot) after installing firmware, then re-check `dmesg` and `bluetoothctl list`.

## 2. Prefer USB 2.0 over USB 3.0

Pi 5 USB 3 ports (blue) often cause RTL8761 flakiness (enumeration OK, HCI resets, A2DP drops).

- Plug BT600 into a **USB 2.0** port (black) on the Pi, **or**
- Use a **USB 2.0** powered hub / USB2-only cable path from a USB3 port.

Verify the bus after plugging:

```bash
lsusb -t
# Prefer a path under a 480M (USB2) hub, not only under 5000M/10000M.
```

## 3. Powered USB hub (required past 1–2 dongles)

Multiple BT600s on the Pi’s own ports (or an unpowered Ugreen-style dock) commonly brown-out or reset under A2DP load.

- Use a **powered** hub sized for the dongle count (aim toward 6).
- Keep **one speaker ↔ one BT600 ↔ one HCI** (`hci0`…`hci5`).
- Bring up **2 → 4 → 6**; do not start with six cold.

## 4. Reduce radio contention (optional onboard disable)

If onboard Bluetooth is only for experiments and all sinks will be BT600s, free the 2.4 GHz coexistence path:

```bash
# Raspberry Pi OS — disable onboard BT (keeps Wi-Fi unless you also disable it)
echo 'dtoverlay=disable-bt' | sudo tee -a /boot/firmware/config.txt
# Older images may use /boot/config.txt instead of /boot/firmware/config.txt
sudo reboot
```

After reboot, onboard HCI should be gone; only USB adapters remain in `bluetoothctl list`.

To re-enable later, remove that `dtoverlay=disable-bt` line and reboot.

> Note: with onboard disabled you lose the Qudelix-on-onboard demo path until you remove the overlay.

## 5. Pairing once HCI is stable

Use the project helper (one adapter at a time):

```bash
./scripts/pair_bt600.sh hci0
./scripts/pair_bt600.sh hci1
```

Or manually:

```bash
bluetoothctl list
bluetoothctl select <adapter-mac>
bluetoothctl power on
bluetoothctl scan on
# … put speaker in pair mode …
bluetoothctl pair <speaker-mac>
bluetoothctl trust <speaker-mac>
bluetoothctl connect <speaker-mac>
```

Then confirm a PipeWire / WirePlumber sink:

```bash
wpctl status | sed -n '/Sinks:/,/Sources:/p'
# look for bluez_output.AA_BB_…
```

Persist MAC ↔ `hciN` in `config/devices.yaml` (see `config/devices.pi-2sink.example.yaml`).

## Quick verify checklist

| Check | Command / expectation |
|-------|------------------------|
| USB id | `lsusb` → `0b05:1d70` |
| HCI present | `bluetoothctl list` shows one controller per dongle |
| USB speed | `lsusb -t` → under **480M** when possible |
| Firmware | `rtl_bt` 8761 blobs installed; clean `dmesg` after replug |
| Power | Powered hub for ≥2–3 dongles |
| A2DP sink | `wpctl status` shows `bluez_output.*` after connect |
| App path | Onboard → Qudelix local file still works ⇒ software OK |

## If it still loops

1. Try a **different** BT600 on the **same** USB2 port (rule out a bad stick).
2. Try the **same** BT600 on another Pi / Linux box (rule out Pi USB).
3. Capture logs while reproducing:

   ```bash
   sudo journalctl -fu bluetooth -o short-precise
   sudo dmesg -w
   ```

4. Keep MuSync on **mock** or onboard-only until HCI stays up for a full track; do not debug delay/volume while adapters reset.

**Vendor driver / dead stick:** ASUS’s Windows/Linux vendor module for this dongle has been observed **failing to build on kernel 6.18** — stick with in-tree `btusb` + `firmware-realtek` on Pi OS rather than chasing the vendor tarball. If the same stick also shows **Windows Code 31** (device failed to start) on a PC, treat it as a **bad/failing BT600** and replace it; software workarounds will not help.

## Related

- Fan-out / feeder graph: [pi-pipewire.md](./pi-pipewire.md)
- Pairing script: `scripts/pair_bt600.sh`
- Pi launcher: `scripts/run_pi.sh`
