import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { apiBase, piFetch, type StreamingHit, type StreamingProvider } from "../lib/pi.js";

export default defineTool({
  description: prompt`
    Search Spotify or YouTube on the MuSync hub. Returns ranked hits with
    uri/title/artist for queue_play. Spotify needs Connect auth on the hub;
    YouTube needs MUSYNC_YOUTUBE_API_KEY. Prefer limit 5–10.
  `,
  effect: "read",
  inputSchema: z.object({
    provider: z.enum(["spotify", "youtube"]),
    query: z.string().min(1),
    limit: z.number().int().min(1).max(50).optional(),
  }),
  async execute({ provider, query, limit }, ctx) {
    if (ctx.session.purpose === "eval") {
      const hit: StreamingHit = {
        provider: provider as StreamingProvider,
        id: "eval-1",
        uri: provider === "spotify" ? "spotify:track:eval1" : "https://youtu.be/eval1",
        title: query,
        artist: "Eval Artist",
        duration_ms: 180_000,
        album: "Eval Album",
        cover_url: null,
      };
      return {
        mocked: true,
        provider,
        query,
        hits: [hit],
        base: apiBase(),
      };
    }

    const params = new URLSearchParams({
      provider,
      q: query,
      limit: String(limit ?? 10),
    });
    const result = await piFetch<{
      provider: StreamingProvider;
      query: string;
      hits: StreamingHit[];
    }>(`/api/streaming/search?${params}`);

    if (!result.ok) {
      return {
        ok: false,
        base: apiBase(),
        status: result.status,
        error: result.error,
        hits: [] as StreamingHit[],
      };
    }
    return {
      ok: true,
      base: apiBase(),
      provider: result.data.provider,
      query: result.data.query,
      hits: result.data.hits,
    };
  },
});
