/**
 * Thin HTTP client for the MuSync Pi (or laptop mock) FastAPI control plane.
 * Base URL: MUSYNC_API_BASE (default http://127.0.0.1:8080).
 */

export type StreamingProvider = "spotify" | "youtube";

export type StreamingHit = {
  provider: StreamingProvider;
  id: string;
  uri: string;
  title: string;
  artist: string;
  duration_ms?: number | null;
  album?: string | null;
  cover_url?: string | null;
};

export type QueueItem = {
  id: string;
  title: string;
  source_type: string;
  locator?: string | null;
  available: boolean;
  note?: string | null;
  duration_ms?: number | null;
  artist?: string | null;
  album?: string | null;
  cover_url?: string | null;
};

export type Transport = {
  state: string;
  position_ms: number;
  duration_ms: number;
  queue_item_id?: string | null;
  title?: string | null;
  artist?: string | null;
  source_type?: string | null;
};

export type NowPlaying = {
  title?: string | null;
  artist?: string | null;
  album?: string | null;
  state: string;
  queue_item_id?: string | null;
  source_type?: string | null;
  note?: string | null;
};

export function apiBase(): string {
  const raw = (process.env.MUSYNC_API_BASE ?? "http://127.0.0.1:8080").trim();
  return raw.replace(/\/+$/, "") || "http://127.0.0.1:8080";
}

async function readError(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const json = JSON.parse(text) as { detail?: unknown };
    if (typeof json.detail === "string") return json.detail;
    if (json.detail != null) return JSON.stringify(json.detail);
  } catch {
    /* plain text */
  }
  return text || `${res.status} ${res.statusText}`;
}

export async function piFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<{ ok: true; data: T } | { ok: false; status: number; error: string }> {
  const url = `${apiBase()}${path.startsWith("/") ? path : `/${path}`}`;
  try {
    const res = await fetch(url, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers ?? {}),
      },
    });
    if (!res.ok) {
      return { ok: false, status: res.status, error: await readError(res) };
    }
    if (res.status === 204) {
      return { ok: true, data: undefined as T };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      status: 0,
      error: `Cannot reach MuSync at ${apiBase()}: ${message}`,
    };
  }
}
