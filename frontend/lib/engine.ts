// Engine pilot helpers (phase 2 — docs/INTEGRATION_PLAN.md §7).
//
// Loaded directly by `node --test lib/*.test.ts` (node strips the types), so
// every piece of pilot logic the UI leans on stays testable without a DOM —
// the only import is `store-types`, plain vocabulary with no DOM or React. The
// runtime-key rule (§4) lives here too: the BYOK key is kept in THIS browser's
// localStorage, read per request, and sent only as the X-Provider-Key header —
// never stored, echoed, or logged. The content-boundaries rule (§11) rides
// beside it: the X-Content-Prefs header, read at request time from the store's
// own persisted slot.

import { PREFS_STORAGE, defaultPrefs, normalizeContentPrefs, type ContentPrefs } from "./store-types.ts";

/** localStorage slot for the engine's BYOK key — the only place it is kept. */
export const ENGINE_KEY_STORAGE = "lorebound.engine.key";
/** localStorage slot for the pilot chronicle last opened in this browser. */
export const ENGINE_CAMPAIGN_STORAGE = "lorebound.engine.campaign";

export const ENGINE_DEFAULT_BASE_URL = "https://api.openai.com/v1";
export const ENGINE_DEFAULT_TIMEOUT_S = 30;
/** Providers the connect panel offers (plan decision 3; P11: openai-compatible
 *  only — the keyless/stub default is gone, the pilot needs a real model). */
export const ENGINE_PROVIDERS = ["openai-compatible"] as const;
export type EngineProviderChoice = (typeof ENGINE_PROVIDERS)[number];

/** Longest turn the client will wait for: the server's own provider timeout
 *  can be up to 600s, and a turn is 1–2 provider calls. */
export const ENGINE_TURN_TIMEOUT_MS = 150_000;

/** True only when the deploy sets NEXT_PUBLIC_ENGINE_MODE=1 (inlined at build). */
export function engineModeEnabled(): boolean {
  return process.env.NEXT_PUBLIC_ENGINE_MODE === "1";
}

/** The stored provider as a connect-panel choice. The panel offers exactly one
 *  today; anything live — "openai" included — is the openai-compatible
 *  endpoint, and a legacy "stub" row is no connection at all (P11). */
export function providerChoice(provider?: string | null): EngineProviderChoice {
  void provider;
  return "openai-compatible";
}

/** Whether a stored provider names a real model (P11). Unset — and the retired
 *  "stub" default legacy rows carry — mean NOT connected: no keyless play. */
export function providerConnected(provider?: string | null): boolean {
  const mode = (provider ?? "").trim().toLowerCase().replace("_", "-");
  return mode !== "" && mode !== "stub";
}

/** The one notice for "no model is connected in this browser": it closes the
 *  play line, and the server's own 400 `connect_your_ai` surfaces as this SAME
 *  wording — never a raw error (§7, P11). */
export const CONNECT_NOTICE =
  "Connect your AI to play — the chronicle narrates with your model.";

export interface PlayGate {
  /** The play input and its submit are disabled. */
  blocked: boolean;
  /** The one notice a blocked player sees; null when play is open. */
  notice: string | null;
}

/** The play gate (P11): take a turn only when a key is kept in THIS browser AND
 *  the account's saved connection names a real provider. A key kept beside an
 *  unset connection must never silently write stub turns (the 2026-09-14
 *  finding). `connectNeeded` flips when the server itself answered
 *  `connect_your_ai`. Browsing is never gated — the shelf, the transcript, the
 *  state panel and the connect card stay usable. */
export function playGate(opts: {
  keyStored: boolean;
  provider?: string | null;
  connectNeeded?: boolean;
}): PlayGate {
  const blocked =
    opts.connectNeeded === true || !opts.keyStored || !providerConnected(opts.provider);
  return { blocked, notice: blocked ? CONNECT_NOTICE : null };
}

/** The key store after one Save on the connect card (2026-09-14 finding): a
 *  typed value replaces the stored key, an EMPTY field keeps it — Save is not a
 *  clear, forgetting is the explicit button's job. */
export function keyAfterConnectionSave(
  typed?: string | null,
  stored?: string | null,
): string {
  const next = normalizeKey(typed);
  return next || normalizeKey(stored);
}

// --------------------------------------------------------------------------- //
// The key store (this browser only)
// --------------------------------------------------------------------------- //

function storage(): Storage | null {
  try {
    if (typeof localStorage === "undefined") return null;
    return localStorage;
  } catch {
    return null; // storage disabled (private mode, embedded webview)
  }
}

function normalizeKey(key?: string | null): string {
  return (key ?? "").trim();
}

/** The stored key, or "" when none is kept in this browser. */
export function getEngineKey(): string {
  try {
    return storage()?.getItem(ENGINE_KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

/** Keep a key in this browser; blank/whitespace clears the slot instead. */
export function setEngineKey(key?: string | null): void {
  const value = normalizeKey(key);
  try {
    const s = storage();
    if (!s) return;
    if (value) s.setItem(ENGINE_KEY_STORAGE, value);
    else s.removeItem(ENGINE_KEY_STORAGE);
  } catch {
    /* storage full or blocked — the key simply is not kept */
  }
}

/** Forget the key in this browser (nothing exists server-side to forget). */
export function clearEngineKey(): void {
  setEngineKey("");
}

export function hasEngineKey(): boolean {
  return getEngineKey().length > 0;
}

/** The pilot chronicle last opened here, or "" when none was. */
export function getEngineCampaignId(): string {
  try {
    return storage()?.getItem(ENGINE_CAMPAIGN_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function setEngineCampaignId(campaignId?: string | null): void {
  const value = (campaignId ?? "").trim();
  try {
    const s = storage();
    if (!s) return;
    if (value) s.setItem(ENGINE_CAMPAIGN_STORAGE, value);
    else s.removeItem(ENGINE_CAMPAIGN_STORAGE);
  } catch {
    /* ignore */
  }
}

/** Header map for one engine request: the key rides only when one is set.
 *  A blank/whitespace-only key counts as absent — the backend agrees. */
export function providerKeyHeader(key?: string | null): Record<string, string> {
  const value = normalizeKey(key);
  return value ? { "X-Provider-Key": value } : {};
}

// --------------------------------------------------------------------------- //
// Content boundaries (docs/INTEGRATION_PLAN.md §11)
// --------------------------------------------------------------------------- //

/** Everything an engine request carries besides auth: the runtime key when one
 *  is set, and the player's content boundaries. Both are read at REQUEST time —
 *  never captured in a closure, which would send whichever NSFW value the
 *  toggle had when the page mounted (the store's own comment warns it lies). */
export function engineRequestHeaders(providerKey?: string | null): Record<string, string> {
  return { ...providerKeyHeader(providerKey), ...contentPrefsHeader(storedContentPrefs()) };
}

/** The `X-Content-Prefs` header for one request — the same JSON the legacy play
 *  path sends (lib/api.ts serializes through here too, one implementation).
 *  Values are normalized first, so the wire vocabulary is always canonical. */
export function contentPrefsHeader(prefs?: Partial<ContentPrefs> | null): Record<string, string> {
  return { "X-Content-Prefs": JSON.stringify(normalizeContentPrefs(prefs ?? null)) };
}

/** The player's boundaries exactly as the app store persists them, read NOW.
 *  Nothing stored yet (a fresh browser) reads as the store's own defaults — the
 *  same values Settings shows and the legacy action path sends. Malformed data
 *  degrades to those defaults, never to a crash. */
export function storedContentPrefs(): ContentPrefs {
  try {
    const raw = storage()?.getItem(PREFS_STORAGE);
    if (!raw) return normalizeContentPrefs(null);
    const parsed = JSON.parse(raw) as { content?: Partial<ContentPrefs> | null } | null;
    return normalizeContentPrefs(parsed?.content ?? null);
  } catch {
    return normalizeContentPrefs(null);
  }
}

/** The boundaries that actually governed a turn, when the payload echoes them
 *  (§11). P8 owns the server shape; this reader is deliberately tolerant — it
 *  renders both today's payloads (no echo at all) and the echoed object, and it
 *  normalizes whatever it finds through the store's own vocabulary. */
export interface AppliedContent {
  prefs: ContentPrefs;
  /** The turn fell back to the chronicler's defaults (a malformed header). */
  defaultsApplied: boolean;
}

/** Object keys the echo has been/should be sent under — `content` is canonical. */
const CONTENT_ECHO_KEYS = ["content", "content_prefs", "applied_prefs", "prefs"] as const;
const BOUNDARY_KEYS = ["nsfw", "violence", "horror", "romance", "language"] as const;

export function appliedContent(turn?: EngineTurn | null): AppliedContent | null {
  if (!turn || typeof turn !== "object") return null;
  const source = turn as Record<string, unknown>;
  for (const key of CONTENT_ECHO_KEYS) {
    const raw = source[key];
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
    const row = raw as Record<string, unknown>;
    // Only an object carrying boundary vocabulary counts as the echo — an
    // unrelated `prefs` shape must not beat the values the player can see.
    if (!BOUNDARY_KEYS.some((k) => k in row)) continue;
    return {
      prefs: normalizeContentPrefs(row),
      defaultsApplied: row.defaults_applied === true || row.defaultsApplied === true,
    };
  }
  return null;
}

/** One compact, readable line for the pilot's boundaries block. A uniform cap
 *  reads as the single word Settings uses ("standard"); mixed caps list each
 *  axis, because the player set them one by one. */
export function boundariesSummary(prefs: ContentPrefs): string {
  if (prefs.nsfw) return "uncensored (every limit lifted) · NSFW on";
  const axes = [prefs.violence, prefs.horror, prefs.romance, prefs.language];
  const caps = axes.every((level) => level === axes[0])
    ? axes[0]
    : `violence ${prefs.violence} · horror ${prefs.horror} · romance ${prefs.romance} · language ${prefs.language}`;
  return `${caps} · NSFW off`;
}

/** The "defaults were applied" note for one turn, when its payload says so.
 *  §11: an unreadable X-Content-Prefs falls back to the standard boundaries AND
 *  the turn says so in its system lines (`bridge.CONTENT_DEFAULTS_NOTE`:
 *  "content: settings could not be read — standard boundaries applied"). The
 *  note is the contract; a flag on the echo (`defaults_applied`) is honoured
 *  too if a payload ever carries one. Returns null when nothing said so. */
export function boundariesNotice(turn?: EngineTurn | null): string | null {
  const lines = (turn?.system_lines ?? [])
    .map((line) => String(line ?? "").trim())
    .filter(Boolean);
  const said = lines.find(
    (line) => /boundar|content|pref|settings/i.test(line) && /could not be read|\bdefaults?\b/i.test(line)
  );
  if (said) return said;
  if (appliedContent(turn)?.defaultsApplied === true) {
    return "Your saved content boundaries could not be read — the chronicler's standard boundaries governed this turn.";
  }
  return null;
}

// --------------------------------------------------------------------------- //
// Turn payload vocabulary (shapes from backend/app/modules/engine/bridge.py)
// --------------------------------------------------------------------------- //

export interface EngineCapability {
  provider?: string;
  model?: string;
  /** Always "live" on the player path (P11: keyless/stub turns no longer exist). */
  mode?: string;
  native_tools?: boolean;
  degraded?: boolean;
}

export interface EngineMechanics {
  kind?: string;
  label?: string;
  verdict_line?: string;
  band?: string | null;
  roll?: number | null;
  modifier?: number | null;
  total?: number | null;
  dc?: number | null;
  skill?: string;
}

export interface EngineDialogueLine {
  npc_id?: string;
  name?: string;
  text?: string;
}

export interface EngineLead {
  id?: number;
  title?: string;
  stage?: string;
}

export interface EnginePresentNpc {
  id?: string;
  name?: string;
  alive?: boolean;
  disposition?: number;
}

export interface EngineLocation {
  id?: string;
  name?: string;
  description_static?: string;
  connections?: string[];
}

export interface EngineState {
  turn?: number;
  next_turn?: number;
  location?: EngineLocation | null;
  hp?: number | null;
  max_hp?: number | null;
  currency?: unknown;
  inventory?: unknown;
  status_effects?: unknown;
  leads?: unknown;
  present_npcs?: unknown;
  pinned_facts?: unknown;
}

export interface EngineTurn {
  turn?: number;
  narration?: string;
  dialogue?: EngineDialogueLine[];
  mechanics?: EngineMechanics | null;
  suggestions?: string[];
  capability?: EngineCapability;
  state?: EngineState;
  system_lines?: string[];
  /** Applied-boundaries echo when the router sends one (§11) — read it through
   *  `appliedContent()`, which tolerates absent or renamed echo objects. */
  content?: unknown;
}

// --------------------------------------------------------------------------- //
// Capability + verdict chips
// --------------------------------------------------------------------------- //

export interface CapabilityChip {
  label: string;
  tone: "live" | "degraded";
  title: string;
}

/** A live turn that ran without native tool calls (the JSON fallback). */
export function engineDegraded(cap?: EngineCapability | null): boolean {
  if (!cap) return false;
  return cap.degraded === true || cap.native_tools === false;
}

/** The chip for one turn's capability — null when there is nothing to describe
 *  (no turn played yet). Every player turn runs on the player's own model
 *  (P11), so the chip either names it or says the engine fell back to plain
 *  JSON; a chip is never a stub. */
export function capabilityChip(cap?: EngineCapability | null): CapabilityChip | null {
  if (!cap) return null;
  const named = (cap.model ?? "").trim() || (cap.provider ?? "").trim() || "your model";
  if (engineDegraded(cap)) {
    return {
      label: `${named} · compatibility`,
      tone: "degraded",
      title: "Your model answered without native tool calls — the engine used its JSON fallback.",
    };
  }
  return {
    label: named,
    tone: "live",
    title: "Your model answered with native tool calls — the full engine protocol.",
  };
}

export type VerdictTone = "crit" | "success" | "cost" | "fail" | "miss" | "info";

export interface VerdictChip {
  /** The line the turn reported (falls back to the check's label). */
  text: string;
  /** The check's label, e.g. "Stealth — the storeroom strongbox". */
  label: string;
  tone: VerdictTone;
}

/** Engine outcome bands (engine/models.py OutcomeBand) + the app's legacy spellings. */
const BAND_TONES: Record<string, VerdictTone> = {
  critical: "crit",
  exceptional: "crit",
  critical_success: "crit",
  success: "success",
  success_at_cost: "cost",
  "success-at-cost": "cost",
  successwithcost: "cost",
  failure: "fail",
  critical_failure: "miss",
  criticalfailure: "miss",
};

/** The per-turn verdict chip; null when the turn called no check. */
export function verdictChip(mech?: EngineMechanics | null): VerdictChip | null {
  if (!mech) return null;
  const text = (mech.verdict_line ?? "").trim();
  const label = (mech.label ?? "").trim() || (mech.skill ?? "").trim();
  if (!text && !label) return null;
  const band = (mech.band ?? "").trim().toLowerCase();
  return { text: text || label, label: label || "check", tone: BAND_TONES[band] ?? "info" };
}

/** Turn text for the transcript: action, narration, dialogue, verdict, notices. */
export type TranscriptEntry =
  | { id: string; kind: "action"; text: string }
  | { id: string; kind: "narration"; text: string }
  | { id: string; kind: "dialogue"; speaker: string; text: string }
  | { id: string; kind: "verdict"; text: string; label: string; tone: VerdictTone }
  | { id: string; kind: "system"; text: string };

/** Deterministic entries for one resolved turn (`seq` names the entry ids). */
export function turnEntries(turn: EngineTurn, seq: number, action?: string): TranscriptEntry[] {
  const out: TranscriptEntry[] = [];
  const said = (action ?? "").trim();
  if (said) out.push({ id: `t${seq}:action`, kind: "action", text: said });
  const narration = (turn.narration ?? "").trim();
  if (narration) out.push({ id: `t${seq}:narr`, kind: "narration", text: narration });
  (turn.dialogue ?? []).forEach((line, i) => {
    const text = (line?.text ?? "").trim();
    if (!text) return;
    out.push({
      id: `t${seq}:d${i}`,
      kind: "dialogue",
      speaker: (line?.name ?? "").trim() || "Someone",
      text,
    });
  });
  const chip = verdictChip(turn.mechanics);
  if (chip) out.push({ id: `t${seq}:verdict`, kind: "verdict", text: chip.text, label: chip.label, tone: chip.tone });
  (turn.system_lines ?? []).forEach((line, i) => {
    const text = String(line ?? "").trim();
    if (text) out.push({ id: `t${seq}:sys${i}`, kind: "system", text });
  });
  return out;
}

/** Chip-sized next moves for the turn, blanks dropped. */
export function turnSuggestions(turn?: EngineTurn | null): string[] {
  return (turn?.suggestions ?? []).map((s) => String(s ?? "").trim()).filter(Boolean);
}

// --------------------------------------------------------------------------- //
// The state snapshot, grouped for the panel (plain fallback for odd shapes)
// --------------------------------------------------------------------------- //

/** Anything value → one readable line (arrays joined, objects JSON). */
export function plainValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value.trim() || "—";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const parts = value.map(plainValue).filter((p) => p !== "—");
    return parts.length ? parts.join(", ") : "—";
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return "—";
    }
  }
  return String(value);
}

export interface StateRow {
  label: string;
  value: string;
}

export interface StateSection {
  title: string;
  rows: StateRow[];
  note?: string;
}

const KNOWN_STATE_KEYS = [
  "turn",
  "next_turn",
  "location",
  "hp",
  "max_hp",
  "currency",
  "inventory",
  "status_effects",
  "leads",
  "present_npcs",
  "pinned_facts",
];

function asRecords(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v));
}

function asStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((v) => plainValue(v)).filter((v) => v !== "—");
}

/** One carried thing: engine inventory rows are `{item_id, qty, flags}`. */
function inventoryLine(item: unknown): string {
  if (item && typeof item === "object" && !Array.isArray(item)) {
    const row = item as Record<string, unknown>;
    const name = plainValue(row.item_id ?? row.name ?? row.id);
    const qty = row.qty ?? row.quantity;
    if (name !== "—") return typeof qty === "number" && qty > 1 ? `${name} ×${qty}` : name;
  }
  return plainValue(item);
}

function asInventory(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(inventoryLine).filter((v) => v !== "—");
}

function hpLine(hp: unknown, maxHp: unknown): string {
  const cur = plainValue(hp);
  const max = plainValue(maxHp);
  if (cur === "—" && max === "—") return "—";
  return max === "—" ? cur : `${cur} / ${max}`;
}

function dispositionLine(npc: Record<string, unknown>): string {
  const parts: string[] = [];
  if (npc.disposition !== undefined && npc.disposition !== null) parts.push(plainValue(npc.disposition));
  if (npc.alive === false) parts.push("dead");
  return parts.length ? parts.join(" · ") : "—";
}

/** The snapshot as readable sections. Unknown or partial shapes degrade to
 *  plain key/value rows — a pilot panel never crashes on a surprise payload. */
export function engineStateSections(state?: unknown): StateSection[] {
  if (state === null || state === undefined) {
    return [{ title: "State", rows: [{ label: "snapshot", value: "—" }], note: "No snapshot yet — play a turn, or press Refresh." }];
  }
  if (typeof state !== "object" || Array.isArray(state)) {
    return [{
      title: "State",
      rows: [{ label: "snapshot", value: plainValue(state) }],
      note: "Shown plain — the chronicler sent a shape this panel does not know.",
    }];
  }
  const snapshot = state as Record<string, unknown>;
  if (!KNOWN_STATE_KEYS.some((k) => k in snapshot)) {
    const rows = Object.keys(snapshot).map((k) => ({ label: k, value: plainValue(snapshot[k]) }));
    return [{
      title: "State",
      rows: rows.length ? rows : [{ label: "snapshot", value: "—" }],
      note: "Shown plain — unknown snapshot shape.",
    }];
  }

  const location = snapshot.location;
  const locationView = location && typeof location === "object" && !Array.isArray(location)
    ? (location as Record<string, unknown>)
    : null;
  const where: StateRow[] = [
    { label: "Turn", value: plainValue(snapshot.turn) },
    { label: "Location", value: locationView ? plainValue(locationView.name ?? locationView.id) : plainValue(location) },
    { label: "HP", value: hpLine(snapshot.hp, snapshot.max_hp) },
    { label: "Currency", value: plainValue(snapshot.currency) },
  ];
  const description = String(locationView?.description_static ?? "").trim();

  const inventory = asInventory(snapshot.inventory);
  const effects = asStrings(snapshot.status_effects);
  const leads = asRecords(snapshot.leads);
  const present = asRecords(snapshot.present_npcs);
  const facts = asStrings(snapshot.pinned_facts);

  return [
    {
      title: "Where you stand",
      rows: where,
      note: description ? (description.length > 240 ? `${description.slice(0, 240)}…` : description) : undefined,
    },
    {
      title: "Carrying",
      rows: [{ label: "Inventory", value: inventory.length ? inventory.join(", ") : "—" }],
      note: inventory.length ? undefined : "Nothing carried.",
    },
    {
      title: "How you are",
      rows: [{ label: "Effects", value: effects.length ? effects.join(", ") : "—" }],
      note: effects.length ? undefined : "Nothing wearing on you.",
    },
    {
      title: "Leads",
      rows: leads.map((lead) => ({
        label: plainValue(lead.title),
        value: plainValue(lead.stage),
      })),
      note: leads.length ? undefined : "No leads yet.",
    },
    {
      title: "Present",
      rows: present.map((npc) => ({
        label: plainValue(npc.name ?? npc.id),
        value: dispositionLine(npc),
      })),
      note: present.length ? undefined : "Nobody here.",
    },
    {
      title: "Pinned facts",
      rows: facts.map((fact, i) => ({ label: `#${i + 1}`, value: fact })),
      note: facts.length ? undefined : "Nothing pinned yet.",
    },
  ];
}

/** One-line summary of the stored connection (the connect panel's header). A
 *  connection that names no real provider — unset, or the retired "stub"
 *  default — reads as not connected (P11). */
export function connectionLabel(conn?: { provider?: string | null; model?: string | null } | null): string {
  const provider = (conn?.provider ?? "").trim();
  if (!providerConnected(provider)) return "no model connected";
  const model = (conn?.model ?? "").trim();
  return model ? `${provider} · ${model}` : provider;
}

export interface ProbeVerdict {
  tone: "ok" | "warn" | "err";
  text: string;
}

/** The connection check's verdict, worded for the panel. */
export function probeVerdictText(
  check?: { reachable?: boolean; native_tools?: boolean; detail?: string } | null,
): ProbeVerdict {
  const detail = (check?.detail ?? "").trim();
  if (!check) {
    return { tone: "warn", text: "No verdict yet — press Test." };
  }
  if (check.reachable === false) {
    return { tone: "err", text: detail || "The endpoint did not answer." };
  }
  if (check.native_tools === false) {
    return {
      tone: "warn",
      text: detail || "Reachable — no native tool calls; the engine will use its JSON fallback.",
    };
  }
  return { tone: "ok", text: detail || "Reachable — native tool calls available." };
}

/** Human text for a failed engine call (detail code + HTTP status). */
export function engineErrorText(code?: string | null, status?: number): string {
  const c = (code ?? "").trim();
  if (c === "Not Found" && status === 404) {
    return "The chronicle pilot is switched off on the server (ENGINE_MODE is not set).";
  }
  if (c === "connect_your_ai") {
    // The server's own refusal surfaces as the same notice the gate shows —
    // never a raw error (P11).
    return CONNECT_NOTICE;
  }
  if (c === "campaign not found" || status === 404) {
    return "That chronicle is not on the shelf — it may have been removed.";
  }
  if (status === 401 || c === "unauthorized") {
    return "Sign in first — pilot chronicles belong to your account.";
  }
  if (status === 502) {
    return c ? `The provider answered with an error: ${c}` : "The provider did not answer.";
  }
  if (c) return c;
  return "The chronicler did not answer.";
}

/** "played 3 minutes ago"-ish text for the campaign list (locale-safe). */
export function formatWhen(iso?: string | null): string {
  const raw = (iso ?? "").trim();
  if (!raw) return "not played yet";
  const when = new Date(raw);
  if (Number.isNaN(when.getTime())) return raw;
  return when.toLocaleString();
}
