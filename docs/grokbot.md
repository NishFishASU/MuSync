# MuSync queue Grokbot

Natural-language **search → queue → play** for Spotify and YouTube on the
MuSync hub, via an [Agent SDK](https://cursor.com/docs/bdk) Grokbot that calls
the Pi FastAPI.

**Code:** [`platforms/agent/`](../platforms/agent/)  
**Scope now:** queue play over playground / HTTP.  
**Follow-on:** Next.js (or similar) chat UI — not scaffolded here.

## What it does

- Understands requests like “play Neon Pulse on Spotify” or “queue that YouTube cover”.
- Calls hub endpoints:
  - `GET /api/streaming/search`
  - `POST /api/streaming/queue` (`activate: true` to play)
  - `GET /api/queue`, `GET /api/now-playing`, `PUT /api/transport`
- Leaves delay/volume, BT pairing, and Wi‑Fi join to the SvelteKit UI.

## Setup

1. Run the hub (laptop mock or Pi):

   ```bash
   ./scripts/run_dev.sh
   # or on Pi: ./scripts/run_pi.sh
   ```

2. Install and validate the agent:

   ```bash
   cd platforms/agent
   npm install
   npx --yes @grok/agent-sdk@0.6.0 validate --dir .
   npm run check
   ```

3. Point at the hub (optional):

   ```bash
   export MUSYNC_API_BASE=http://127.0.0.1:8080
   # Pi LAN example: http://192.168.1.42:8080
   ```

4. Smoke the health tool (no model key required):

   ```bash
   npx --yes @grok/agent-sdk@0.6.0 tool call hub_health --dir . --json
   ```

5. Model turn / playground (needs Cursor credential):

   ```bash
   npx --yes @grok/agent-sdk@0.6.0 login   # once
   npx --yes @grok/agent-sdk@0.6.0 run --dir . --message 'Play Neon Pulse on Spotify'
   npx --yes @grok/agent-sdk@0.6.0 eval --dir . --tag smoke
   npx --yes @grok/agent-sdk@0.6.0 dev --dir .
   # → http://127.0.0.1:3000/playground
   ```

## Hub credentials

Same as the Streaming tab:

- Spotify: Client ID/Secret + Connect OAuth + Premium + librespot — [spotify-apple.md](./spotify-apple.md)
- YouTube: `MUSYNC_YOUTUBE_API_KEY` + yt-dlp/ffmpeg — [youtube.md](./youtube.md)

The agent does **not** store those secrets; it only HTTP-calls the hub.

## Follow-on: Next UI

A thin Next (or other) chat client can sit beside this agent and POST to its
HTTP channel / playground session API, with `MUSYNC_API_BASE` still owned by
the agent process on the LAN. Keep tools stable; add the UI in a later PR.

## Deploy (optional)

Do not deploy from an agent session unless you intend to. Locally:

```bash
cd platforms/agent
npx --yes @grok/agent-sdk@0.6.0 validate --dir .
npx --yes @grok/agent-sdk@0.6.0 deploy --dir . --dry-run
# then: deploy --dir .  (save the one-time alias token)
```
