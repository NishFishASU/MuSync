import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import { apiBase, piFetch, type QueueItem } from "../lib/pi.js";

export default defineTool({
  description: prompt`
    List the MuSync hub song queue (local + Spotify + YouTube items).
    Use before picking an existing item or confirming what queue_play added.
  `,
  effect: "read",
  async execute(_input, ctx) {
    if (ctx.session.purpose === "eval") {
      return {
        mocked: true,
        base: apiBase(),
        items: [] as QueueItem[],
      };
    }
    const result = await piFetch<QueueItem[]>("/api/queue");
    if (!result.ok) {
      return {
        ok: false,
        base: apiBase(),
        status: result.status,
        error: result.error,
        items: [] as QueueItem[],
      };
    }
    return {
      ok: true,
      base: apiBase(),
      items: result.data,
      count: result.data.length,
    };
  },
});
