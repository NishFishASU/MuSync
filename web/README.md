# MuSync web UI (SvelteKit)

Lean static SPA for the Pi + phone control surface. Talks to FastAPI at `/api/*`.

## Dev (hot reload)

Terminal 1 — API (mock):

```bash
./scripts/run_dev.sh
# http://127.0.0.1:8080  (API + last built UI if present)
```

Terminal 2 — Vite + SvelteKit:

```bash
cd web
npm install
npm run dev
# http://127.0.0.1:5173  (/api proxied → :8080)
```

Override the proxy target with `MUSYNC_API_PROXY=http://127.0.0.1:8080`.

## Production build

```bash
cd web
npm ci
npm run build
# → web/build/  (adapter-static SPA, fallback index.html)
```

FastAPI serves that directory (`MUSYNC_WEB_DIR`, default `web/build`). No Node on the Pi at runtime — only the built assets.

```bash
# from repo root after build
./scripts/run_dev.sh          # mock
./scripts/run_pi.sh           # PipeWire
# UI: http://<host>:8080/
```

Reverse-proxy alternative: put nginx/caddy in front, serve `web/build` as `/`, proxy `/api` to uvicorn.
