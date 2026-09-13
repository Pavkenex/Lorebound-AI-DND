// Single API client — all backend calls go through here (Stream F contract).
// Designed against GET /health + documented JSON shapes; every call degrades
// to fixture data so the UI works before other streams land.
import { fixtures } from "./fixtures";
import type { ContentPrefs } from "./store-types";
import { normalizeApiBase } from "./api-base";

/** Fallback when no explicit URL is configured: same host as the page, backend port 8001.
 *  Works for localhost dev and IP-based deploys alike; set NEXT_PUBLIC_API_URL for anything else. */
function defaultApiBase(): string {
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8001`;
  }
  return "http://localhost:8001";
}

const BASE = normalizeApiBase(process.env.NEXT_PUBLIC_API_URL) || defaultApiBase();

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

/** A surfaced check waiting on the player's throw (two-phase /act, t_84c31095-follow-up). */
export interface PendingCheck {
  /** Human label, e.g. "Stealth — the storeroom strongbox". */
  label: string;
  skill: string;
  attribute?: string | null;
  attribute_mod?: number;
  skill_mod?: number;
  total_mod?: number;
  dc: number;
  difficulty?: string;
  /** Board fingerprint from the calling leg; a moved board rejects the throw (409). */
  token: string;
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
  /** Present when the action waits on the player's throw; nothing persisted yet. */
  pending_check?: PendingCheck | null;
  narration: string | null;
  dialogue?: { speaker: string; line: string }[];
  newLeads?: string[];
  /** Quick actions proposed for the next move (model's, else scene buttons). */
  suggestions?: { label: string; command: string }[];
  /** Engine system lines for the live feed (clues found, routes opened). */
  system?: string[];
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

/** Throw the called check's die: sends the settled face to resolve the beat.
 *
 * Deliberately NOT the submitAction fallback: a throw that fails to send is
 * never faked into a resolution — it returns fromFixture so the UI offers a
 * resend of the SAME face, and a 409 (moved board) returns conflict: true. */
export async function rollCheck(
  text: string, roll: number, token: string, prefs?: ContentPrefs, idempotencyKey?: string
): Promise<ApiResult<ActResponse> & { conflict?: boolean }> {
  const key = idempotencyKey ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const cid = getCampaignId();
  const empty: ActResponse = { ack: "", mechanics: null, narration: null, dialogue: [], newLeads: [], system: [] };
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
        roll,
        pending_token: token,
        campaign_id: cid === "demo-campaign" ? undefined : cid,
        prefs: prefs ? { nsfw: prefs.nsfw } : undefined,
      }),
    });
    clearTimeout(t);
    if (res.status === 409) {
      // check_expired: the board moved between the call and the throw.
      return { data: empty, cost: { calls: 0, costUsd: 0, cached: true }, fromFixture: false, conflict: true };
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as ActResponse;
    return { data, cost: readCost(res), fromFixture: false };
  } catch {
    return { data: empty, cost: { calls: 0, costUsd: 0, cached: true }, fromFixture: true };
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

/** One NPC present at the current location (GET /state npcs[]). */
export interface NpcEntry {
  name: string;
  note: string;
  /** Their strongest memories about the player (top 3), when any. */
  remembers?: string[];
}

/** Live game state (GET /state): the fixture shape plus live-only fields. */
export type LiveGameState = Omit<typeof fixtures.gameState, "npcs"> & {
  campaign_id?: string | null;
  character?: typeof fixtures.character;
  completed?: boolean;
  lead_stage?: string;
  clues?: string[];
  npcs: NpcEntry[];
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

/** --- AI provider settings (bring your own OpenAI-compatible endpoint) --- */
export interface AiSettingsDoc {
  provider: "stub" | "openai-compatible" | null;
  configured: boolean;
  base_url: string;
  model: string;
  has_key: boolean;
  timeout_s: number;
  active_provider: string;
  active_source: "settings" | "env" | "default";
  env_provider: string;
}

export interface AiSettingsPayload {
  provider: "stub" | "openai-compatible";
  base_url?: string;
  model?: string;
  /** Omit to keep the stored key; empty string also keeps it (write-only field). */
  api_key?: string;
  clear_key?: boolean;
  timeout_s?: number;
}

export interface AiTestDoc {
  ok: boolean;
  model?: string;
  latency_ms?: number;
  reply?: string;
  error?: string;
}

export type AiResult<T> = { ok: true; doc: T } | { ok: false; error: string };

export async function aiSettingsApi(): Promise<AiResult<AiSettingsDoc>> {
  const r = await authJson<AiSettingsDoc & { __error?: string }>("/ai/settings");
  if (r === null) return { ok: false, error: "The chronicler is out of reach — sign in first." };
  if (r.__error) return { ok: false, error: String(r.__error) };
  return { ok: true, doc: r };
}

export async function saveAiSettingsApi(body: AiSettingsPayload): Promise<AiResult<AiSettingsDoc>> {
  const r = await authJson<AiSettingsDoc & { __error?: string }>("/ai/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
  if (r === null) return { ok: false, error: "The chronicler is out of reach — try again." };
  if (r.__error) return { ok: false, error: String(r.__error) };
  return { ok: true, doc: r };
}

export async function testAiSettingsApi(
  body: { base_url?: string; model?: string; api_key?: string; timeout_s?: number }
): Promise<AiResult<AiTestDoc>> {
  const r = await authJson<AiTestDoc & { __error?: string }>("/ai/settings/test", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (r === null) return { ok: false, error: "The chronicler is out of reach — try again." };
  if (r.__error) return { ok: false, error: String(r.__error) };
  return { ok: true, doc: r };
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

/** Standard prebuilt hero sheets (choose-your-hero step) + apply. */
export interface PrebuiltHeroDoc {
  id: string;
  name: string;
  class: string;
  blurb: string;
  pronouns: string;
  age_range: string;
  homeland: string;
  appearance: string;
  background: string;
  background_grants: Record<string, unknown>;
  drives: string[];
  attributes: Record<string, number>;
  skills: string[];
  traits: string[];
}

export async function listPrebuiltsApi(): Promise<PrebuiltHeroDoc[] | null> {
  const r = await authJson<PrebuiltHeroDoc[]>("/character/prebuilts");
  return Array.isArray(r) ? r : null;
}

export async function applyPrebuiltApi(id: string): Promise<{ ok: boolean; error?: string }> {
  const r = await authJson<{ __error?: string }>(
    `/character/prebuilts/${encodeURIComponent(id)}/apply`,
    { method: "POST", body: "{}" }
  );
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
