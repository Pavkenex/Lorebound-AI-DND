// Single API client — all backend calls go through here (Stream F contract).
// Designed against GET /health + documented JSON shapes; every call degrades
// to fixture data so the UI works before other streams land.
import { fixtures } from "./fixtures";
import type { ContentPrefs } from "./store-types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export interface CostInfo { calls: number; costUsd: number; cached: boolean }
export interface ApiResult<T> { data: T; cost: CostInfo; fromFixture: boolean }

const TOKEN_KEY = "lorebound-token";
export const CAMPAIGN_KEY = "lorebound-campaign-id";
export const LAST_SAVE_KEY = "lorebound-last-save";
export const RESUME_KEY = "lorebound-resume";

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* ignore */ }
}
export function getCampaignId(): string {
  try { return localStorage.getItem(CAMPAIGN_KEY) ?? "demo-campaign"; } catch { return "demo-campaign"; }
}
export function setCampaignId(id: string) {
  try { localStorage.setItem(CAMPAIGN_KEY, id); } catch { /* ignore */ }
}

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

function authHeaders(): Record<string, string> {
  const t = typeof window === "undefined" ? null : getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function get<T>(path: string, fallback: T, prefs?: ContentPrefs): Promise<ApiResult<T>> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 6000);
    const res = await fetch(`${BASE}${path}`, {
      signal: ctl.signal,
      headers: { ...prefsHeaders(prefs), ...authHeaders() },
    });
    clearTimeout(t);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as T;
    return { data, cost: readCost(res), fromFixture: false };
  } catch {
    return { data: fallback, cost: { calls: 0, costUsd: 0, cached: true }, fromFixture: true };
  }
}

async function post<T>(path: string, body: unknown, fallback: T, prefs?: ContentPrefs): Promise<ApiResult<T>> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 12000);
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      signal: ctl.signal,
      headers: { "Content-Type": "application/json", ...prefsHeaders(prefs), ...authHeaders() },
      body: JSON.stringify(body),
    });
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
  mechanics?: {
    label: string; roll: string; total: number; detail?: string;
    /** Raw d20 value — drives the 3D die face and the critical animations (t_ae0e86a6). */
    d20?: number;
    /** Engine outcome, e.g. "Success", "CriticalFailure". */
    outcome?: string;
    dc?: number;
  } | null;
  narration: string;
  dialogue?: { speaker: string; line: string }[];
  newLeads?: string[];
}

/** Retry-safe submit: idempotency key per player action (t_18510814). */
export async function submitAction(
  text: string, prefs?: ContentPrefs, idempotencyKey?: string
): Promise<ApiResult<ActResponse>> {
  const key = idempotencyKey ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const cid = getCampaignId();
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 25000);
    const res = await fetch(`${BASE}/act`, {
      method: "POST",
      signal: ctl.signal,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": key,
        ...prefsHeaders(prefs),
        ...authHeaders(),
      },
      body: JSON.stringify({
        text,
        campaign_id: cid === "demo-campaign" ? undefined : cid,
        prefs: prefs ? { nsfw: prefs.nsfw } : undefined,
      }),
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

export interface SaveRow {
  id: string;
  slot: string;
  label: string;
  checkpoint: string;
  created_at: string;
}

/** Live game state (GET /state): the fixture shape plus live-only fields. */
export type LiveGameState = typeof fixtures.gameState & {
  character?: typeof fixtures.character;
  completed?: boolean;
  lead_stage?: string;
  clues?: string[];
};

export interface TutorialDoc {
  id: string;
  title: string;
  beats: { h: string; p: string }[];
}

export async function loginApi(email: string, password: string): Promise<{ access_token: string }> {
  const form = new URLSearchParams({ username: email, password });
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 10000);
  try {
    const res = await fetch(`${BASE}/auth/login`, {
      method: "POST",
      signal: ctl.signal,
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form.toString(),
    });
    clearTimeout(t);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as { access_token: string };
  } finally {
    clearTimeout(t);
  }
}

export async function registerApi(email: string, password: string, displayName: string): Promise<{ token: { access_token: string } }> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 10000);
  try {
    const res = await fetch(`${BASE}/auth/register`, {
      method: "POST",
      signal: ctl.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, display_name: displayName }),
    });
    clearTimeout(t);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as { token: { access_token: string } };
  } finally {
    clearTimeout(t);
  }
}

export interface CampaignRow {
  id: string;
  name: string;
  seed_key: string;
  status: string;
  created_at?: string | null;
}

/** Best-effort campaign lookup. Returns null when offline or not signed in. */
export async function listCampaigns(): Promise<CampaignRow[] | null> {
  if (!getToken()) return null;
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 8000);
  try {
    const res = await fetch(`${BASE}/campaigns`, { signal: ctl.signal, headers: authHeaders() });
    if (!res.ok) return null;
    const data = (await res.json()) as CampaignRow[];
    return Array.isArray(data) ? data : null;
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

/** Ensure the signed-in player has a campaign; returns its id or null (offline). */
export async function ensureCampaign(name?: string): Promise<string | null> {
  if (!getToken()) return null;
  try {
    const list = await listCampaigns();
    if (list === null) return null;
    const current = getCampaignId();
    const match = list.find((c) => c.id === current) ?? list[0];
    if (match) {
      setCampaignId(match.id);
      return match.id;
    }
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 8000);
    try {
      const res = await fetch(`${BASE}/campaigns`, {
        method: "POST",
        signal: ctl.signal,
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(name ? { name } : {}),
      });
      if (!res.ok) return null;
      const created = (await res.json()) as CampaignRow;
      if (!created?.id) return null;
      setCampaignId(created.id);
      return created.id;
    } finally {
      clearTimeout(t);
    }
  } catch {
    return null;
  }
}

export interface CreationOptions {
  stages: string[];
  backgrounds: { name: string; grants: Record<string, unknown> }[];
  age_ranges: string[];
  attributes: { names: string[]; min: number; max: number; default: number; pool: number };
  skills: string[];
  traits: string[];
}

export interface CreationStateDoc {
  options: CreationOptions;
  stage: number;
  complete: boolean;
  applied: boolean;
  data: Record<string, Record<string, unknown>>;
}

async function authJson<T>(path: string, init?: RequestInit): Promise<T | null> {
  if (!getToken()) return null;
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 10000);
  try {
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      signal: ctl.signal,
      headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) return { __error: (body as { detail?: string } | null)?.detail ?? `HTTP ${res.status}` } as T;
    return body as T;
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

export async function creationStateApi(): Promise<CreationStateDoc | null> {
  return authJson<CreationStateDoc>("/character/creation");
}

export async function advanceCreationApi(stage: number, payload: Record<string, unknown>):
Promise<{ ok: boolean; error?: string; state?: CreationStateDoc }> {
  const r = await authJson<CreationStateDoc & { __error?: string }>("/character/creation/advance", {
    method: "POST",
    body: JSON.stringify({ stage, payload }),
  });
  if (r === null) return { ok: false, error: "The chronicler is not reachable — try again while the backend is awake." };
  if (("__error" in r) && r.__error) return { ok: false, error: String(r.__error) };
  return { ok: true, state: r };
}

export async function commitCreationApi():
Promise<{ ok: boolean; error?: string }> {
  const r = await authJson<{ __error?: string }>("/character/creation/commit", { method: "POST", body: "{}" });
  if (r === null) return { ok: false, error: "The chronicler is not reachable — try again while the backend is awake." };
  if (r.__error) return { ok: false, error: String(r.__error) };
  return { ok: true };
}

export const api = {
  health: () => get<{ status: string }>("/health", { status: "fixture" }),
  character: (prefs?: ContentPrefs) => get("/character", fixtures.character, prefs),
  gameState: (prefs?: ContentPrefs) => get<LiveGameState>("/state", fixtures.gameState, prefs),
  skills: (prefs?: ContentPrefs) => get("/skills", fixtures.skills, prefs),
  journal: (prefs?: ContentPrefs) => get("/journal", fixtures.journal, prefs),
  map: (prefs?: ContentPrefs) => get("/map", fixtures.mapInfo, prefs),
  inventory: (prefs?: ContentPrefs) => get("/inventory", fixtures.inventory, prefs),
  companions: (prefs?: ContentPrefs) => get("/companions", fixtures.companions, prefs),
  tutorial: (prefs?: ContentPrefs) =>
    get<TutorialDoc>("/content/tutorial", fixtures.tutorial as TutorialDoc, prefs),
  listSaves: (campaignId: string, prefs?: ContentPrefs) =>
    get<SaveRow[]>(`/campaigns/${encodeURIComponent(campaignId)}/saves`, fixtures.saves as SaveRow[], prefs),
  createSave: (campaignId: string, label: string, prefs?: ContentPrefs) =>
    post<SaveRow>(
      `/campaigns/${encodeURIComponent(campaignId)}/saves`,
      { label, slot: "manual", checkpoint: "manual", prefs: prefs ? { nsfw: prefs.nsfw } : undefined },
      { id: `local-${Date.now()}`, slot: "manual", label, checkpoint: "manual", created_at: new Date().toISOString() },
      prefs
    ),
  loadSave: (saveId: string, prefs?: ContentPrefs) =>
    post<Record<string, unknown>>(
      `/saves/${encodeURIComponent(saveId)}/load`,
      {},
      { loaded: false, saveId },
      prefs
    ),
};
