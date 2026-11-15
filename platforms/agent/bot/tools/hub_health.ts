import { prompt } from "@grok/agent-sdk";
import { defineTool } from "@grok/agent-sdk/tools";
import { apiBase, piFetch } from "../lib/pi.js";

type Health = {
  ok: boolean;
  backend: string;
  detail?: string;
  spotify_ready?: boolean;
  youtube_ready?: boolean;
  sink_count?: number;
  enabled_count?: number;
};

export default defineTool({
  description: prompt`
    Check whether the MuSync hub API is reachable and which streaming
    backends are ready. Call this first when the hub may be down or
    Spotify/YouTube look unconfigured.
  `,
  effect: "read",
  async execute() {
    const result = await piFetch<Health>("/api/health");
    if (!result.ok) {
      return {
        reachable: false,
        base: apiBase(),
        status: result.status,
        error: result.error,
      };
    }
    return {
      reachable: true,
      base: apiBase(),
      ok: result.data.ok,
      backend: result.data.backend,
      detail: result.data.detail ?? "",
      spotify_ready: Boolean(result.data.spotify_ready),
      youtube_ready: Boolean(result.data.youtube_ready),
      sink_count: result.data.sink_count ?? 0,
      enabled_count: result.data.enabled_count ?? 0,
    };
  },
});
