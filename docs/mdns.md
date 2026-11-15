# musync.local (mDNS UI advertise)

MuSync advertises **`musync.local`** on the LAN via mDNS (`zeroconf` / `_http._tcp`) so phones and laptops can open the hub UI without hunting for a Pi IP:

```text
http://musync.local:8080
```

Hostname is always the label **`musync`** (→ `musync.local`) — not `Mu.Sync` / `MuSync.local`. Soft-fails if `zeroconf` is missing or mDNS sockets fail; the API still boots.

## Spotify OAuth — not mDNS

Spotify **forbids** `localhost`, `*.local`, and plain `http://<LAN-IP>` redirect URIs
(Dashboard: **Invalid redirect URI**). Register **`127.0.0.1`** and/or **`https://`**
callbacks only:

- `http://127.0.0.1:8080/api/streaming/spotify/callback` — **Pi + laptop default** (`MUSYNC_PORT=8080`)
- `http://127.0.0.1:8081/api/streaming/spotify/callback` — laptop override when 8080 is busy
- `https://….trycloudflare.com/api/streaming/spotify/callback` — phone / off-Pi (Cloudflare Tunnel)

Pi keeps `:8080` via `run_pi.sh` / systemd. Open the UI at `http://musync.local:8080` or
`http://<Pi-IP>:8080` — those hosts are **not** Spotify OAuth redirects (use SSH `-L` or
an HTTPS tunnel; see [spotify-apple.md](spotify-apple.md)).

Details: [spotify-apple.md](./spotify-apple.md).

## Env

| Variable | Default | Meaning |
|----------|---------|---------|
| `MUSYNC_MDNS` | `1` | `0` / `off` disables advertise |
| `MUSYNC_MDNS_HOSTNAME` | `musync` | Host label (dots stripped; prefer `musync`) |
| `MUSYNC_MDNS_NAME` | `MuSync` | Zeroconf **service instance** display name only |
| `MUSYNC_PORT` | `8080` | Port published in the `_http._tcp` record |

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/mdns` | Advertise status (`hostname`, `advertising`, `ui_only`, …) |
| `GET` | `/api/wifi` | Includes `mdns_hostname` hint when known |

## Pi tip

```bash
./scripts/run_pi.sh
# UI: http://musync.local:8080  (or http://<Pi-IP>:8080)
curl -s http://127.0.0.1:8080/api/mdns
```
