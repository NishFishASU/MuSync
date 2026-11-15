# MuSync queue Grokbot

Agent SDK (`@grok/agent-sdk`) bot that turns natural language into MuSync hub
**search → queue → play** for Spotify and YouTube via the Pi FastAPI.

Lives under `platforms/agent/` (same dual-target pattern as a future Uno Q
platform folder). **Queue play is in scope**; a Next.js chat UI is a follow-on.

## Prerequisites

- Node **22.13+** (never Bun)
- MuSync hub API reachable (`./scripts/run_dev.sh` or `./scripts/run_pi.sh`)
- Optional: Cursor credential for model turns (`agent-sdk login` or `CURSOR_API_KEY`)

## Env

| Variable | Default | Meaning |
| --- | --- | --- |
| `MUSYNC_API_BASE` | `http://127.0.0.1:8080` | Hub FastAPI origin (no trailing slash) |

Spotify Connect / YouTube Data API keys stay on the hub (`~/musync.env`), not in this agent.

## Tools

- `hub_health` — `GET /api/health`
- `search_streaming` — `GET /api/streaming/search`
- `queue_play` — `POST /api/streaming/queue` (activate defaults true)
- `list_queue` — `GET /api/queue`
- `now_playing` — `GET /api/now-playing`
- `set_transport` — `PUT /api/transport`

Eval sessions stub write/search tools so CI does not need a live hub.

## Local loop

```bash
cd platforms/agent
npm install
npx --yes @grok/agent-sdk@0.6.0 validate --dir .
npm run check
# Hub mock on :8080 in another terminal:
#   ./scripts/run_dev.sh
MUSYNC_API_BASE=http://127.0.0.1:8080 npx --yes @grok/agent-sdk@0.6.0 tool call hub_health --dir . --json
npx --yes @grok/agent-sdk@0.6.0 tool call search_streaming --dir . --input '{"provider":"spotify","query":"test","limit":3}' --json
npx --yes @grok/agent-sdk@0.6.0 run --dir . --message 'Play Neon Pulse by Eval Artist on Spotify'
npx --yes @grok/agent-sdk@0.6.0 eval --dir . --tag smoke
npx --yes @grok/agent-sdk@0.6.0 dev --dir .
# → http://127.0.0.1:3000/playground
```

## Follow-on (not in this scaffold)

Next.js (or similar) chat UI that posts to this agent's HTTP channel /
playground API, with hub URL config in the browser. Keep queue-play tools
stable; add UI later.

## Docs

Canonical guide: [docs/grokbot.md](../../docs/grokbot.md).
