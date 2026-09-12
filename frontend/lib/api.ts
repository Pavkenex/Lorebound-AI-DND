// Single API client — all backend calls go through here (Stream F contract).
// Designed against GET /health + documented JSON shapes; every call degrades
// to fixture data so the UI works before other streams land.
import { fixtures } from "./fixtures";
import type { ContentPrefs } from "./store-types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export interface CostInfo { calls: number; costUsd: number; cached: boolean }
export interface ApiResult<T> { data: T; cost: CostInfo; fromFixture: boolean }

function readCost(res: Response | null): CostInfo {
  if (!res) return { calls: 0, costUsd: 0, cached: true };
  const h = (n: string) => res.headers.get(n);
  return {
    calls: Number(h("x-ai-calls") ?? h("X-AI-Calls") ?? 0),
    costUsd: Number(h("x-ai-cost-usd") ?? h("X-AI-Cost-USD") ?? 0),
    cached: (h("x-cache") ?? "").toLowerCase() === "hit",
  };
}

function prefsHeaders(p?: ContentPrefs): Record<string, string> {
  if (!p) return {};
  return { "X-Content-Prefs": JSON.stringify(p) };
}

async function get<T>(path: string, fallback: T, prefs?: ContentPrefs): Promise<ApiResult<T>> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 6000);
    const res = await fetch(`${BASE}${path}`, { signal: ctl.signal, headers: { ...prefsHeaders(prefs) } });
    clearTimeout(t);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as T;
    return { data, cost: readCost(res), fromFixture: false };
  } catch {
    return { data: fallback, cost: { calls: 0, costUsd: 0, cached: true }, fromFixture: true };
  }
}

export interface ActResponse {
  ack: string;
  mechanics?: { label: string; roll: string; total: number; detail?: string } | null;
  narration: string;
  dialogue?: { speaker: string; line: string }[];
  newLeads?: string[];
}

/** Retry-safe submit: idempotency key per player action (t_18510814). */
export async function submitAction(
  text: string, prefs?: ContentPrefs, idempotencyKey?: string
): Promise<ApiResult<ActResponse>> {
  const key = idempotencyKey ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 25000);
    const res = await fetch(`${BASE}/act`, {
      method: "POST",
      signal: ctl.signal,
      headers: { "Content-Type": "application/json", "Idempotency-Key": key, ...prefsHeaders(prefs) },
      body: JSON.stringify({ text }),
    });
    clearTimeout(t);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as ActResponse;
    return { data, cost: readCost(res), fromFixture: false };
  } catch {
    // Fixture fallback: instant ack + canned narration so play never blocks.
    await new Promise((r) => setTimeout(r, 350));
    return {
      data: {
        ack: "The chronicler nods…",
        mechanics: null,
        narration: fixtures.fallbackNarration(text),
        dialogue: [],
        newLeads: [],
      },
      cost: { calls: 0, costUsd: 0, cached: true },
      fromFixture: true,
    };
  }
}

/** Streaming-ready narration slot: yields progressively if the backend streams,
 *  otherwise resolves whole. Caller renders chunks into the narration slot. */
export async function* streamNarration(full: string): AsyncGenerator<string> {
  const words = full.split(/(?<=\s)/);
  let acc = "";
  for (const w of words) {
    acc += w;
    yield acc;
    await new Promise((r) => setTimeout(r, 18));
  }
}

export const api = {
  health: () => get<{ status: string }>("/health", { status: "fixture" }),
  character: (prefs?: ContentPrefs) => get("/character", fixtures.character, prefs),
  gameState: (prefs?: ContentPrefs) => get("/state", fixtures.gameState, prefs),
  skills: (prefs?: ContentPrefs) => get("/skills", fixtures.skills, prefs),
  journal: (prefs?: ContentPrefs) => get("/journal", fixtures.journal, prefs),
  map: (prefs?: ContentPrefs) => get("/map", fixtures.mapInfo, prefs),
  inventory: (prefs?: ContentPrefs) => get("/inventory", fixtures.inventory, prefs),
  companions: (prefs?: ContentPrefs) => get("/companions", fixtures.companions, prefs),
};
