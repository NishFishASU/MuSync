import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { apiBase, piFetch, type QueueItem } from "../lib/pi.js";

export default defineTool({
  description: prompt`
    Add a Spotify or YouTube search hit to the MuSync hub song queue and
    optionally activate it (feeder swap → play). Use after search_streaming.
    Pass the hit's uri plus title/artist/album/cover/duration when known.
    Default activate=true plays immediately on connected sinks.
  `,
  effect: "write",
  inputSchema: z.object({
    provider: z.enum(["spotify", "youtube"]),
    uri: z.string().min(1),
    title: z.string().optional(),
    artist: z.string().optional(),
    album: z.string().optional(),
    cover_url: z.string().optional(),
    duration_ms: z.number().int().nonnegative().optional(),
    activate: z.boolean().optional(),
  }),
  dryRunResult: (input) => ({
    dryRun: true,
    queued: false,
    provider: input.provider,
    uri: input.uri,
    activate: input.activate ?? true,
  }),
  async execute(input, ctx) {
    const activate = input.activate ?? true;
    if (ctx.session.purpose === "eval") {
      return {
        mocked: true,
        queued: true,
        activated: activate,
        item: {
          id: "eval-queue-1",
          title: input.title ?? input.uri,
          source_type: input.provider,
          locator: input.uri,
          available: true,
          artist: input.artist ?? null,
          album: input.album ?? null,
          cover_url: input.cover_url ?? null,
          duration_ms: input.duration_ms ?? null,
        } satisfies QueueItem,
        base: apiBase(),
      };
    }

    const result = await piFetch<QueueItem>("/api/streaming/queue", {
      method: "POST",
      body: JSON.stringify({
        provider: input.provider,
        uri: input.uri,
        title: input.title,
        artist: input.artist,
        album: input.album,
        cover_url: input.cover_url,
        duration_ms: input.duration_ms,
        activate,
      }),
    });

    if (!result.ok) {
      return {
        ok: false,
        queued: false,
        base: apiBase(),
        status: result.status,
        error: result.error,
      };
    }
    return {
      ok: true,
      queued: true,
      activated: activate,
      item: result.data,
      base: apiBase(),
    };
  },
});
