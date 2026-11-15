import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import * as z from "zod/v4";
import { apiBase, piFetch, type Transport } from "../lib/pi.js";

export default defineTool({
  description: prompt`
    Control hub transport: play, pause, or stop the active queue item, or
    scrub position_ms. Prefer queue_play(activate=true) to start a new track.
    Use this to pause/resume or stop what is already active.
  `,
  effect: "write",
  inputSchema: z.object({
    state: z.enum(["playing", "paused", "stopped"]).optional(),
    position_ms: z.number().int().nonnegative().optional(),
    queue_item_id: z.string().optional(),
  }),
  dryRunResult: (input) => ({
    dryRun: true,
    applied: false,
    ...input,
  }),
  async execute(input, ctx) {
    if (ctx.session.purpose === "eval") {
      return {
        mocked: true,
        transport: {
          state: input.state ?? "playing",
          position_ms: input.position_ms ?? 0,
          duration_ms: 0,
          queue_item_id: input.queue_item_id ?? "eval-queue-1",
          title: "Eval Track",
          artist: "Eval Artist",
          source_type: "spotify",
        } satisfies Transport,
        base: apiBase(),
      };
    }

    const body: Record<string, unknown> = {};
    if (input.state != null) body.state = input.state;
    if (input.position_ms != null) body.position_ms = input.position_ms;
    if (input.queue_item_id != null) body.queue_item_id = input.queue_item_id;

    const result = await piFetch<Transport>("/api/transport", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    if (!result.ok) {
      return {
        ok: false,
        base: apiBase(),
        status: result.status,
        error: result.error,
      };
    }
    return { ok: true, transport: result.data, base: apiBase() };
  },
});
