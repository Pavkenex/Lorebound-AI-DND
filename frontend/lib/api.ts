// Single API client — all backend calls go through here (Stream F contract).
// Designed against GET /health + documented JSON shapes; every call degrades
// to fixture data so the UI works before other streams land.
import { fixtures } from "./fixtures";
import type { ContentPrefs } from "./store-types";
import { normalizeApiBase } from "./api-base";
import { engineErrorText, engineRequestHeaders, getEngineKey, contentPrefsHeader } from "./engine";
import type { EngineState, EngineTurn } from "./engine";

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

/** The boundaries header for the legacy action path (same serializer as the
 *  engine path — `contentPrefsHeader`). */
function prefsHeaders(p?: ContentPrefs): Record<string, string> {
  if (!p) return {};
  return contentPrefsHeader(p);
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

async function del<T>(path: string, fallback: T): Promise<ApiResult<T>> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 8000);
    const res = await fetch(`${BASE}${path}`, {
      method: "DELETE",
      signal: ctl.signal,
      headers: { ...authHeaders() },
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
  /** The band DC before the social terms moved it (systems slice 4, §6). */
  dc_base?: number | null;
  /** Why the DC moved — approach, live mood, relationship credit, a vow. */
  dc_why?: string | null;
  /** Only a natural 20 passes this check (nothing but a critical reaches it). */
  long_odds?: boolean;
  /** Roll-twice hint: a spent Inspiration point rides this check (advantage). */
  advantage?: boolean;
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
    /** Advantage (Inspiration): the dropped second face; d20 is the kept die. */
    d20_second?: number;
    advantage?: boolean;
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

/** What /act answered: the resolved turn, or exactly why there is no turn (P12).
 *
 * Failures carry the backend's machine code — ``connect_your_ai`` (nothing
 * connected), ``provider_failed`` / ``turn_failed`` (the model failed
 * mid-turn), ``check_expired`` (moved board), ``unreachable`` (the request
 * never landed) — plus the scrubbed message when there is one. */
export type ActOutcome =
  | { ok: true; data: ActResponse; cost: CostInfo }
  | ActFailureDoc;

/** A fresh idempotency key for one player action.
 *
 * Exported so a retry can resend the SAME key it already used: a retried
 * action is then a replay (the backend serves the stored response) rather
 * than a second application. */
export function newActionKey(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Why a turn did not happen (P12): the backend's machine code + scrubbed text. */
export type ActFailureDoc = {
  ok: false;
  code: string;
  message: string;
  status: number | null;
};

/** Read a failed /act: ``{detail: "code"}`` or ``{detail: {code, message}}``. */
async function actFailure(res: Response): Promise<ActFailureDoc> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) {
    return { ok: false, status: res.status, code: detail, message: "" };
  }
  if (detail && typeof detail === "object") {
    const d = detail as { code?: unknown; message?: unknown };
    return {
      ok: false,
      status: res.status,
      code: typeof d.code === "string" && d.code ? d.code : `http_${res.status}`,
      message: typeof d.message === "string" ? d.message : "",
    };
  }
  return { ok: false, status: res.status, code: `http_${res.status}`, message: "" };
}

/** Retry-safe submit: idempotency key per player action (t_18510814).
 *
 * P12: there is no fixture branch — a story the backend did not narrate is
 * not a story. A refusal or a failed model call comes back as
 * ``{ok: false, code, message}`` so the UI can say exactly what happened
 * (``connect_your_ai`` → nothing connected; ``provider_failed``/``turn_failed``
 * → the model failed mid-turn; ``unreachable`` → the request never landed).
 */
export async function submitAction(
  text: string, prefs?: ContentPrefs, idempotencyKey?: string
): Promise<ActOutcome> {
  const key = idempotencyKey ?? newActionKey();
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
    if (!res.ok) return actFailure(res);
    const data = (await res.json()) as ActResponse;
    return { ok: true, data, cost: readCost(res) };
  } catch {
    return { ok: false, status: null, code: "unreachable", message: "" };
  }
}

/** Write the chronicle's opening page once (§intro, P14).
 *
 *  The seed carries no prose: a new journey's first page is the model's, and
 *  the page asks for it as soon as the state says it is pending. Same failure
 *  contract as ``submitAction`` — ``connect_your_ai`` / ``provider_failed`` /
 *  ``turn_failed`` come back as ``{ok: false}``, never as cover prose. */
export async function openChronicle(
  prefs?: ContentPrefs
): Promise<{ ok: true; data: OpeningDoc; cost: CostInfo } | ActFailureDoc> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 25000);
    const res = await fetch(`${BASE}/opening`, {
      method: "POST",
      signal: ctl.signal,
      headers: {
        "Content-Type": "application/json",
        ...prefsHeaders(prefs),
        ...authHeaders(),
      },
      body: "{}",
    });
    clearTimeout(t);
    if (!res.ok) return actFailure(res);
    return { ok: true, data: (await res.json()) as OpeningDoc, cost: readCost(res) };
  } catch {
    return { ok: false, status: null, code: "unreachable", message: "" };
  }
}

/** Throw the called check's die: sends the settled face to resolve the beat.
 * A throw that fails to send is never faked into a resolution: it answers
 * ``{ok: false}`` (409 → ``check_expired``) so the UI offers a resend of the
 * SAME face, and the same action can be retried with the same key. */
export async function rollCheck(
  text: string, roll: number, token: string, prefs?: ContentPrefs, idempotencyKey?: string,
  roll2?: number
): Promise<ActOutcome> {
  const key = idempotencyKey ?? newActionKey();
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
        roll,
        roll2,
        pending_token: token,
        campaign_id: cid === "demo-campaign" ? undefined : cid,
        prefs: prefs ? { nsfw: prefs.nsfw } : undefined,
      }),
    });
    clearTimeout(t);
    if (!res.ok) return actFailure(res);
    const data = (await res.json()) as ActResponse;
    return { ok: true, data, cost: readCost(res) };
  } catch {
    return { ok: false, status: null, code: "unreachable", message: "" };
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
  /** Stable slug for the character's page (GET /npcs/{slug}); absent in demo. */
  slug?: string;
  note: string;
  /** Their strongest memories about the player (top 3), when any. */
  remembers?: string[];
  /** Live relationship meter, -100..+100 (systems slice 2). */
  attitude?: number;
  /** Band word for the meter: Hostile | Wary | Neutral | Warm | Bonded. */
  band?: string;
  /** Live mood word the character is wearing (systems slice 3, §4); already
   *  gated by the content settings this client sent. Settled reads "neutral". */
  mood?: string;
  /** Mood strength 0..1 (0 = settled back to their baseline — no chip). */
  mood_intensity?: number;
}

/** One present character's page (GET /npcs/{slug}): the panel's entry, deeper.
 *  The memories list carries more than the panel's top three. */
export interface NpcDetail {
  slug: string;
  name: string;
  note: string;
  attitude: number;
  band: string;
  mood: string;
  mood_intensity: number;
  remembers: string[];
}

/** Live game state (GET /state): the fixture shape plus live-only fields. */
export type LiveGameState = Omit<typeof fixtures.gameState, "npcs"> & {
  campaign_id?: string | null;
  character?: typeof fixtures.character;
  completed?: boolean;
  lead_stage?: string;
  clues?: string[];
  npcs: NpcEntry[];
  /** The live scene block (§7): id/label/goal of where the player stands. */
  scene?: { id: string; label: string; goal?: string; state?: string };
  /** True until the chronicle's first page has been written (P14): the seed
   *  carries no prose, so the page asks the narrator for it once (POST /opening). */
  opening_pending?: boolean;
};

/** The chronicle's opening page (POST /opening): the model's words for the
 *  road above Ravenford, written once per journey from the player's own sheet. */
export interface OpeningDoc {
  narration: string;
  dialogue: { speaker: string; line: string }[];
  suggestions: { label: string; command: string }[];
  /** Already written: this journey's page is in the chronicle. */
  already: boolean;
}

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
  /** What is stored (a legacy row may still hold "stub" — it is not connected). */
  provider: string | null;
  configured: boolean;
  base_url: string;
  model: string;
  has_key: boolean;
  timeout_s: number;
  /** The resolution /act uses (P12): is there a model to narrate with? */
  connected: boolean;
  active_provider: string;
  active_source: "settings" | "env" | "default";
  active_model: string;
  active_base_url: string;
  /** Why not connected: unset | stub | incomplete | misconfigured | unsupported. */
  active_reason: string;
  env_provider: string;
  providers: string[];
}

export interface AiSettingsPayload {
  provider: "openai-compatible";
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

/** POST /campaigns/{id}/restart result: the old run's checkpoint + fresh state. */
export interface RestartDoc {
  restarted: boolean;
  campaign_id: string;
  had_progress: boolean;
  checkpoint_save_id: string | null;
}

export const api = {
  health: () => get<{ status: string }>("/health", { status: "fixture" }),
  character: (prefs?: ContentPrefs) => get("/character", fixtures.character, prefs),
  gameState: (prefs?: ContentPrefs) => get<LiveGameState>("/state", fixtures.gameState, prefs),
  npcDetail: (slug: string, prefs?: ContentPrefs) =>
    get<NpcDetail | null>(`/npcs/${encodeURIComponent(slug)}`, null, prefs),
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
  /** Remove one save slot from the shelf (owner-scoped server-side). */
  deleteSave: (saveId: string) =>
    del<{ deleted: boolean; save_id: string; campaign_id: string }>(
      `/saves/${encodeURIComponent(saveId)}`,
      { deleted: false, save_id: saveId, campaign_id: "" }
    ),
  /** Begin a new journey: checkpoint the old run, reset state + clock. */
  restartCampaign: (campaignId: string) =>
    post<RestartDoc>(
      `/campaigns/${encodeURIComponent(campaignId)}/restart`,
      {},
      { restarted: false, campaign_id: campaignId, had_progress: false, checkpoint_save_id: null }
    ),
};

/** --- Engine pilot (phase 2) — docs/INTEGRATION_PLAN.md §5/§7 --------------
 *  Flag-gated server-side: with ENGINE_MODE off every `/engine/*` path answers
 *  404, so a flag-off backend fails here rather than anywhere else.
 *  Runtime-key rule (§4): the BYOK key is read from THIS browser per request
 *  and travels only in the X-Provider-Key header — never in a body, a stored
 *  row, or a log line. */

export interface EngineCampaign {
  id: string;
  name: string;
  world: string;
  last_turn_at: string | null;
}

export interface EngineConnection {
  provider: string | null;
  base_url: string;
  model: string;
  timeout_s: number;
  updated_at: string | null;
}

/** Non-secret prefs only: there is no key field, by design (§4). One live
 *  provider kind is offered (P11 — the keyless/stub default is gone). */
export interface EngineConnectionPayload {
  provider?: "openai-compatible";
  base_url?: string;
  model?: string;
  timeout_s?: number;
}

export interface EngineProbe {
  reachable: boolean;
  native_tools: boolean;
  detail: string;
}

/** What the pilot calls answer: the payload, or the failure with its code. */
export type EngineResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; code?: string; status?: number };

async function engineFetch<T>(
  path: string,
  init: RequestInit & { providerKey?: string | null } = {},
  timeoutMs = 12000
): Promise<EngineResult<T>> {
  if (!getToken()) {
    return { ok: false, error: engineErrorText(null, 401), code: "unauthorized", status: 401 };
  }
  const { providerKey, ...rest } = init;
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, {
      ...rest,
      signal: ctl.signal,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        // The runtime key (when one is set) and the player's content boundaries,
        // both read from THIS browser at request time (§4, §11).
        ...engineRequestHeaders(providerKey),
        ...(rest.headers ?? {}),
      },
    });
    const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
    if (!res.ok) {
      const detail = typeof body?.detail === "string" ? body.detail : "";
      return {
        ok: false,
        error: engineErrorText(detail, res.status),
        code: detail || undefined,
        status: res.status,
      };
    }
    return { ok: true, data: body as T };
  } catch {
    return {
      ok: false,
      error: "The chronicler did not answer — check that the backend is awake and reachable.",
      code: "unreachable",
    };
  } finally {
    clearTimeout(t);
  }
}

export const engineApi = {
  listCampaigns: () => engineFetch<EngineCampaign[]>("/engine/campaigns"),

  createCampaign: (name: string) =>
    engineFetch<EngineCampaign>("/engine/campaigns", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  /** One turn; a live provider needs the browser-held key (sent as a header). */
  takeTurn: (campaignId: string, text: string) =>
    engineFetch<EngineTurn>(
      `/engine/campaigns/${encodeURIComponent(campaignId)}/turns`,
      { method: "POST", body: JSON.stringify({ text }), providerKey: getEngineKey() },
      150000
    ),

  state: (campaignId: string) =>
    engineFetch<EngineState>(`/engine/campaigns/${encodeURIComponent(campaignId)}/state`),

  getConnection: () => engineFetch<EngineConnection>("/engine/connection"),

  /** Partial update: omitted fields keep their stored value. */
  saveConnection: (payload: EngineConnectionPayload) =>
    engineFetch<EngineConnection>("/engine/connection", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  /** Canary-verify the SAVED prefs. `key` (the field's current text) wins over
   *  the stored one so Test works before Save. */
  probeConnection: (key?: string | null) =>
    engineFetch<EngineProbe>(
      "/engine/connection/check",
      { method: "POST", body: "{}", providerKey: key ?? getEngineKey() },
      60000
    ),
};
