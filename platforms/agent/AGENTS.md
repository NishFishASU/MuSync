# musync-queue-grokbot

Agent SDK project for MuSync hub **queue search/play**. Served prompt:
`bot/instructions.md`.

## Loop

```bash
npx --yes @grok/agent-sdk@0.6.0 validate --dir .
npx --yes @grok/agent-sdk@0.6.0 info --dir . --json
npx --yes @grok/agent-sdk@0.6.0 tool call hub_health --dir . --json
npx --yes @grok/agent-sdk@0.6.0 run --dir . --message "Play Neon Pulse on Spotify"
npx --yes @grok/agent-sdk@0.6.0 eval --dir . --tag smoke
npm run check
```

Node 22.13+, never Bun. Hub URL: `MUSYNC_API_BASE` (default `http://127.0.0.1:8080`).

## Layout

- `bot/tools/` — one tool per file (`search_streaming`, `queue_play`, …)
- `bot/lib/pi.ts` — FastAPI client (not discovered)
- `evals/smoke.eval.ts` — NL play path; tools stub when `purpose === "eval"`
- Next.js chat UI is **out of scope** for this scaffold

## Do not

- Store Spotify/YouTube secrets in this package — they live on the hub.
- Point `serve`/`dev` at the MuSync repo root; serve only `platforms/agent`.
