import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import { apiBase, piFetch, type NowPlaying } from "../lib/pi.js";

export default defineTool({
  description: prompt`
    Read unified now-playing metadata from the MuSync hub (title, artist,
    transport state, active queue item). Use after queue_play or when the
    user asks what is playing.
  `,
  effect: "read",
  async execute(_input, ctx) {
    if (ctx.session.purpose === "eval") {
      return {
        mocked: true,
        base: apiBase(),
        now: {
          title: "Eval Track",
          artist: "Eval Artist",
          state: "playing",
          queue_item_id: "eval-queue-1",
          source_type: "spotify",
        } satisfies NowPlaying,
      };
    }
    const result = await piFetch<NowPlaying>("/api/now-playing");
    if (!result.ok) {
      return {
        ok: false,
        base: apiBase(),
        status: result.status,
        error: result.error,
      };
    }
    return { ok: true, base: apiBase(), now: result.data };
  },
});
