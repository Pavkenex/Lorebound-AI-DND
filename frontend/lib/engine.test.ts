// Pure pilot logic — the parts a browser is not needed for.
// Run with: npm test   (node --test lib/*.test.ts, types stripped)
import assert from "node:assert/strict";
import test from "node:test";

import {
  CONNECT_NOTICE,
  ENGINE_CAMPAIGN_STORAGE,
  ENGINE_KEY_STORAGE,
  appliedContent,
  boundariesNotice,
  boundariesSummary,
  capabilityChip,
  clearEngineKey,
  connectionLabel,
  contentPrefsHeader,
  engineDegraded,
  engineErrorText,
  engineModeEnabled,
  engineRequestHeaders,
  engineStateSections,
  formatWhen,
  getEngineCampaignId,
  getEngineKey,
  hasEngineKey,
  keyAfterConnectionSave,
  plainValue,
  playGate,
  probeVerdictText,
  providerChoice,
  providerConnected,
  providerKeyHeader,
  setEngineCampaignId,
  setEngineKey,
  storedContentPrefs,
  turnEntries,
  turnSuggestions,
  verdictChip,
  type EngineTurn,
} from "./engine.ts";
import { PREFS_STORAGE, defaultPrefs } from "./store-types.ts";

/** Minimal localStorage stand-in (node has none). */
class MemoryStorage {
  private readonly map = new Map<string, string>();
  get length(): number { return this.map.size; }
  clear(): void { this.map.clear(); }
  getItem(key: string): string | null { return this.map.has(key) ? this.map.get(key)! : null; }
  key(index: number): string | null { return [...this.map.keys()][index] ?? null; }
  removeItem(key: string): void { this.map.delete(key); }
  setItem(key: string, value: string): void { this.map.set(String(key), String(value)); }
}

type Globals = typeof globalThis & { localStorage?: Storage };

function withStorage<T>(run: (mem: MemoryStorage) => T): T {
  const g = globalThis as Globals;
  const had = Object.prototype.hasOwnProperty.call(g, "localStorage");
  const before = g.localStorage;
  const mem = new MemoryStorage();
  g.localStorage = mem as unknown as Storage;
  try {
    return run(mem);
  } finally {
    if (had) g.localStorage = before;
    else delete (g as { localStorage?: Storage }).localStorage;
  }
}

function withEnv<T>(value: string | undefined, run: () => T): T {
  const before = process.env.NEXT_PUBLIC_ENGINE_MODE;
  if (value === undefined) delete process.env.NEXT_PUBLIC_ENGINE_MODE;
  else process.env.NEXT_PUBLIC_ENGINE_MODE = value;
  try {
    return run();
  } finally {
    if (before === undefined) delete process.env.NEXT_PUBLIC_ENGINE_MODE;
    else process.env.NEXT_PUBLIC_ENGINE_MODE = before;
  }
}

const SNAPSHOT = {
  turn: 4,
  next_turn: 5,
  location: {
    id: "lantern-inn",
    name: "The Lantern Inn",
    description_static: "Hearthlight and rain at the window.",
    connections: ["road-to-ravenford"],
  },
  hp: 9,
  max_hp: 12,
  currency: 14,
  inventory: ["a rusted key", "half a loaf"],
  status_effects: ["soaked"],
  leads: [{ id: 1, title: "The missing salt shipment", stage: "accepted" }],
  present_npcs: [
    { id: "marla", name: "Marla", alive: true, disposition: 0.42 },
    { id: "ferryman", name: "The ferryman", alive: false, disposition: -0.2 },
  ],
  pinned_facts: ["Marla counts the salt twice."],
};

// --------------------------------------------------------------------------- //
// The key store — this browser only
// --------------------------------------------------------------------------- //

test("the key store keeps the key in this browser's localStorage", () => {
  withStorage((mem) => {
    assert.equal(getEngineKey(), "");
    assert.equal(hasEngineKey(), false);

    setEngineKey(" sk-live-123 ");
    assert.equal(getEngineKey(), "sk-live-123"); // trimmed on the way in
    assert.equal(mem.getItem(ENGINE_KEY_STORAGE), "sk-live-123");
    assert.equal(hasEngineKey(), true);

    setEngineKey("sk-second");
    assert.equal(getEngineKey(), "sk-second"); // one slot, newest key wins

    clearEngineKey();
    assert.equal(getEngineKey(), "");
    assert.equal(mem.getItem(ENGINE_KEY_STORAGE), null);
    assert.equal(hasEngineKey(), false);
  });
});

test("a blank key clears the slot instead of storing whitespace", () => {
  withStorage((mem) => {
    setEngineKey("sk-live");
    setEngineKey("   ");
    assert.equal(mem.getItem(ENGINE_KEY_STORAGE), null);
    assert.equal(getEngineKey(), "");

    setEngineKey("sk-again");
    setEngineKey(null);
    assert.equal(getEngineKey(), "");
  });
});

test("the key store never throws when storage is blocked", () => {
  const g = globalThis as Globals;
  const had = Object.prototype.hasOwnProperty.call(g, "localStorage");
  const before = g.localStorage;
  const hostile = {
    getItem(): string | null { throw new Error("blocked"); },
    setItem(): void { throw new Error("blocked"); },
    removeItem(): void { throw new Error("blocked"); },
  };
  g.localStorage = hostile as unknown as Storage;
  try {
    assert.equal(getEngineKey(), "");
    assert.doesNotThrow(() => setEngineKey("sk-x"));
    assert.doesNotThrow(() => clearEngineKey());
  } finally {
    if (had) g.localStorage = before;
    else delete (g as { localStorage?: Storage }).localStorage;
  }
});

test("providerKeyHeader carries the key only when one is set", () => {
  assert.deepEqual(providerKeyHeader(undefined), {});
  assert.deepEqual(providerKeyHeader(null), {});
  assert.deepEqual(providerKeyHeader(""), {});
  assert.deepEqual(providerKeyHeader("   "), {}); // whitespace = absent (backend agrees)
  assert.deepEqual(providerKeyHeader(" sk-abc "), { "X-Provider-Key": "sk-abc" });
});

test("the pilot chronicle id is remembered in this browser", () => {
  withStorage((mem) => {
    assert.equal(getEngineCampaignId(), "");
    setEngineCampaignId("11111111-2222-3333-4444-555555555555");
    assert.equal(getEngineCampaignId(), "11111111-2222-3333-4444-555555555555");
    assert.equal(mem.getItem(ENGINE_CAMPAIGN_STORAGE), "11111111-2222-3333-4444-555555555555");
    setEngineCampaignId("");
    assert.equal(getEngineCampaignId(), "");
  });
});

// --------------------------------------------------------------------------- //
// The flag + the capability chip
// --------------------------------------------------------------------------- //

test("engineModeEnabled follows NEXT_PUBLIC_ENGINE_MODE exactly", () => {
  withEnv("1", () => assert.equal(engineModeEnabled(), true));
  withEnv("0", () => assert.equal(engineModeEnabled(), false));
  withEnv("true", () => assert.equal(engineModeEnabled(), false)); // only "1" counts
  withEnv(undefined, () => assert.equal(engineModeEnabled(), false));
});

test("providerChoice maps any stored provider onto the panel's one choice", () => {
  // P11: the keyless/stub choice is gone — every stored provider (legacy
  // "stub" rows included) reads back as the one live option the panel offers.
  assert.equal(providerChoice("openai-compatible"), "openai-compatible");
  assert.equal(providerChoice("openai_compatible"), "openai-compatible");
  assert.equal(providerChoice("openai"), "openai-compatible");
  assert.equal(providerChoice("stub"), "openai-compatible");
  assert.equal(providerChoice(null), "openai-compatible");
  assert.equal(providerChoice(""), "openai-compatible");
});

test("providerConnected: unset and the retired stub default are NOT connected", () => {
  assert.equal(providerConnected("openai-compatible"), true);
  assert.equal(providerConnected("openai"), true);
  assert.equal(providerConnected("  OpenAI-Compatible "), true);
  assert.equal(providerConnected(""), false);
  assert.equal(providerConnected("   "), false);
  assert.equal(providerConnected(null), false);
  assert.equal(providerConnected(undefined), false);
  assert.equal(providerConnected("stub"), false); // the legacy default row
  assert.equal(providerConnected("STUB"), false);
});

test("capabilityChip names the model and the protocol it used", () => {
  const native = capabilityChip({ provider: "openai", model: "gpt-5-mini", mode: "live", native_tools: true, degraded: false });
  assert.equal(native?.tone, "live");
  assert.equal(native?.label, "gpt-5-mini");
  assert.match(native?.title ?? "", /native tool calls/);

  const degraded = capabilityChip({ provider: "openai", model: "llama-3.3", mode: "live", native_tools: false, degraded: true });
  assert.equal(degraded?.tone, "degraded");
  assert.match(degraded?.label ?? "", /llama-3\.3 · compatibility/);
  assert.match(degraded?.title ?? "", /JSON fallback/);

  // No model name echoed: the provider or a plain "your model" still names it.
  assert.equal(capabilityChip({ provider: "openai" })?.label, "openai");
  assert.equal(capabilityChip({})?.label, "your model");

  // Nothing played yet: no chip at all (there is no stub chip to fall back on,
  // P11 — a player turn is always a model call).
  assert.equal(capabilityChip(null), null);
  assert.equal(capabilityChip(undefined), null);
});

test("engineDegraded is about the protocol, never a provider kind", () => {
  assert.equal(engineDegraded(null), false);
  assert.equal(engineDegraded(undefined), false);
  assert.equal(engineDegraded({ mode: "live", native_tools: true, degraded: false }), false);
  assert.equal(engineDegraded({ mode: "live", native_tools: false, degraded: true }), true);
  // Either signal is enough: native_tools=false, or an explicit degraded flag.
  assert.equal(engineDegraded({ mode: "live", native_tools: false }), true);
  assert.equal(engineDegraded({ mode: "live", degraded: true }), true);
});

test("verdictChip maps engine bands onto tones", () => {
  assert.equal(verdictChip({ verdict_line: "Stealth — passed", band: "critical" })?.tone, "crit");
  assert.equal(verdictChip({ verdict_line: "x", band: "success" })?.tone, "success");
  assert.equal(verdictChip({ verdict_line: "x", band: "success_at_cost" })?.tone, "cost");
  assert.equal(verdictChip({ verdict_line: "x", band: "failure" })?.tone, "fail");
  assert.equal(verdictChip({ verdict_line: "x", band: "critical_failure" })?.tone, "miss");
  assert.equal(verdictChip({ verdict_line: "x", band: "unknown_band" })?.tone, "info");
  assert.equal(verdictChip({ verdict_line: "x", band: null })?.tone, "info");
  // The app's legacy spellings still map (a payload from either vocabulary).
  assert.equal(verdictChip({ verdict_line: "x", band: "Exceptional" })?.tone, "crit");
  assert.equal(verdictChip({ verdict_line: "x", band: "SuccessWithCost" })?.tone, "cost");
});

test("verdictChip is null when no check was called", () => {
  assert.equal(verdictChip(null), null);
  assert.equal(verdictChip(undefined), null);
  assert.equal(verdictChip({}), null);
  assert.equal(verdictChip({ kind: "none" }), null);
});

test("verdictChip falls back to the check's label when the line is empty", () => {
  const chip = verdictChip({ label: "Stealth — the storeroom strongbox", verdict_line: "", band: "success" });
  assert.equal(chip?.text, "Stealth — the storeroom strongbox");
  assert.equal(chip?.label, "Stealth — the storeroom strongbox");
  const viaSkill = verdictChip({ skill: "Perception", band: "failure" });
  assert.equal(viaSkill?.text, "Perception");
});

// --------------------------------------------------------------------------- //
// The transcript entries
// --------------------------------------------------------------------------- //

test("turnEntries renders one turn in reading order with stable ids", () => {
  const turn = {
    turn: 3,
    narration: "Rain walks the roof of the inn.  ",
    dialogue: [
      { npc_id: "marla", name: "Marla", text: "Sit, then. Dry yourself." },
      { npc_id: "ghost", name: "Ghost", text: "   " }, // blank line dropped
    ],
    mechanics: { verdict_line: "Perception check — passed · 14 vs DC 12.", label: "Perception", band: "success" },
    system_lines: [" Salt is counted twice. ", ""],
  };
  const entries = turnEntries(turn, 7, "  I listen to the rain. ");
  assert.deepEqual(entries.map((e) => e.id), [
    "t7:action",
    "t7:narr",
    "t7:d0",
    "t7:verdict",
    "t7:sys0",
  ]);
  assert.deepEqual(entries.map((e) => e.kind), ["action", "narration", "dialogue", "verdict", "system"]);
  assert.equal(entries[0].kind === "action" && entries[0].text, "I listen to the rain.");
  assert.equal(entries[1].kind === "narration" && entries[1].text, "Rain walks the roof of the inn.");
  assert.equal(entries[2].kind === "dialogue" && entries[2].speaker, "Marla");
  assert.equal(entries[3].kind === "verdict" && entries[3].tone, "success");
  assert.equal(entries[4].kind === "system" && entries[4].text, "Salt is counted twice.");
});

test("turnEntries skips what a turn did not produce", () => {
  const entries = turnEntries({ turn: 1, narration: "   " }, 2);
  assert.deepEqual(entries, []);
  const bare = turnEntries({ narration: "The road bends." }, 3);
  assert.deepEqual(bare.map((e) => e.kind), ["narration"]);
});

test("turnEntries names an unnamed speaker instead of leaving a blank bubble", () => {
  const entries = turnEntries({ dialogue: [{ text: "Hm." }] }, 1);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].kind === "dialogue" && entries[0].speaker, "Someone");
});

test("turnSuggestions drops blanks and trims chips", () => {
  assert.deepEqual(turnSuggestions({ suggestions: ["Ask Marla", "  ", " Follow the wagon "] }), ["Ask Marla", "Follow the wagon"]);
  assert.deepEqual(turnSuggestions(null), []);
});

// --------------------------------------------------------------------------- //
// The state panel
// --------------------------------------------------------------------------- //

test("engineStateSections groups a full snapshot for reading", () => {
  const sections = engineStateSections(SNAPSHOT);
  assert.deepEqual(sections.map((s) => s.title), [
    "Where you stand",
    "Carrying",
    "How you are",
    "Leads",
    "Present",
    "Pinned facts",
  ]);
  const where = sections[0];
  assert.equal(where.rows.find((r) => r.label === "Turn")?.value, "4");
  assert.equal(where.rows.find((r) => r.label === "Location")?.value, "The Lantern Inn");
  assert.equal(where.rows.find((r) => r.label === "HP")?.value, "9 / 12");
  assert.equal(where.rows.find((r) => r.label === "Currency")?.value, "14");
  assert.equal(where.note, "Hearthlight and rain at the window.");

  assert.equal(sections[1].rows[0].value, "a rusted key, half a loaf");
  assert.equal(sections[2].rows[0].value, "soaked");
  assert.deepEqual(sections[3].rows, [{ label: "The missing salt shipment", value: "accepted" }]);
  assert.equal(sections[4].rows[0].label, "Marla");
  assert.equal(sections[4].rows[0].value, "0.42");
  assert.equal(sections[4].rows[1].value, "-0.2 · dead");
  assert.deepEqual(sections[5].rows, [{ label: "#1", value: "Marla counts the salt twice." }]);
});

test("engineStateSections reads a sparse snapshot with named fallbacks", () => {
  const sections = engineStateSections({ turn: 0 });
  assert.equal(sections.length, 6); // the known shape still groups
  const where = sections[0];
  assert.equal(where.rows.find((r) => r.label === "HP")?.value, "—");
  assert.equal(where.note, undefined);
  assert.equal(sections[1].note, "Nothing carried.");
  assert.equal(sections[2].note, "Nothing wearing on you.");
  assert.equal(sections[3].note, "No leads yet.");
  assert.equal(sections[4].note, "Nobody here.");
  assert.equal(sections[5].note, "Nothing pinned yet.");
});

test("engineStateSections reads carried things from the engine's item rows", () => {
  const sections = engineStateSections({
    inventory: [
      { item_id: "coil of rope", qty: 1, flags: {} },
      { item_id: "salt token", qty: 3, flags: { stamped: "salt-gate" } },
      { item_id: "old lantern", qty: 0, flags: {} },
    ],
  });
  assert.equal(sections[1].rows[0].value, "coil of rope, salt token ×3, old lantern");
});

test("engineStateSections degrades to plain rows on a shape it does not know", () => {
  const sections = engineStateSections({ mood_ring: "warm", coats: ["rain", "wool"] });
  assert.equal(sections.length, 1);
  assert.equal(sections[0].rows[0].label, "mood_ring");
  assert.equal(sections[0].rows[1].value, "rain, wool");
  assert.match(sections[0].note ?? "", /plain/);

  const nullish = engineStateSections(null);
  assert.equal(nullish.length, 1);
  assert.equal(nullish[0].rows[0].value, "—");
  assert.match(nullish[0].note ?? "", /No snapshot/);

  const scalar = engineStateSections("the fog");
  assert.equal(scalar[0].rows[0].value, "the fog");
  assert.match(scalar[0].note ?? "", /plain/);
});

test("plainValue formats scalars, lists and odd values", () => {
  assert.equal(plainValue("  a line "), "a line");
  assert.equal(plainValue(7), "7");
  assert.equal(plainValue(true), "true");
  assert.equal(plainValue(null), "—");
  assert.equal(plainValue(undefined), "—");
  assert.equal(plainValue("   "), "—");
  assert.equal(plainValue([]), "—");
  assert.equal(plainValue(["a", "", "b"]), "a, b");
  assert.equal(plainValue({ gold: 3 }), '{"gold":3}');
});

test("connectionLabel summarises the stored prefs — and reads a dead row as none", () => {
  // P11: a connection that names no real provider IS "not connected" — the
  // legacy "stub" default and a blank row both read that way, never as a
  // playable keyless mode.
  assert.equal(connectionLabel(null), "no model connected");
  assert.equal(connectionLabel({ provider: "", model: "" }), "no model connected");
  assert.equal(connectionLabel({ provider: "stub", model: "" }), "no model connected");
  assert.equal(connectionLabel({ provider: "openai-compatible", model: "" }), "openai-compatible");
  assert.equal(connectionLabel({ provider: "openai-compatible", model: "gpt-5-mini" }), "openai-compatible · gpt-5-mini");
});

test("probeVerdictText words the connection check's verdict", () => {
  assert.equal(probeVerdictText(null).tone, "warn");
  assert.match(probeVerdictText(null).text, /No verdict yet/);

  const native = probeVerdictText({ reachable: true, native_tools: true, detail: "model reachable; native tool calls available" });
  assert.equal(native.tone, "ok");
  assert.match(native.text, /native tool calls available/);

  const degraded = probeVerdictText({ reachable: true, native_tools: false, detail: "" });
  assert.equal(degraded.tone, "warn");
  assert.match(degraded.text, /JSON fallback/);

  const down = probeVerdictText({ reachable: false, native_tools: false, detail: "provider HTTP 401" });
  assert.equal(down.tone, "err");
  assert.equal(down.text, "provider HTTP 401");
});

// --------------------------------------------------------------------------- //
// Failure wording
// --------------------------------------------------------------------------- //

test("engineErrorText maps the pilot's failure codes", () => {
  assert.match(engineErrorText("Not Found", 404), /switched off on the server/);
  // P11: the server's own refusal reads as the SAME notice the play gate shows.
  assert.equal(engineErrorText("connect_your_ai", 400), CONNECT_NOTICE);
  assert.match(engineErrorText("campaign not found", 404), /not on the shelf/);
  assert.match(engineErrorText(null, 401), /Sign in first/);
  assert.equal(engineErrorText("provider HTTP 401: invalid api key: Bearer ***", 502), "The provider answered with an error: provider HTTP 401: invalid api key: Bearer ***");
  assert.equal(engineErrorText("something else", 400), "something else");
  assert.equal(engineErrorText("", 0), "The chronicler did not answer.");
});

test("playGate: a key stored beside an unset connection is NOT playable (P11)", () => {
  // The 2026-09-14 finding: a key kept in the browser + a connection that was
  // never saved (or a legacy "stub" row) must not produce a silent stub turn.
  const storedButUnset = playGate({ keyStored: true, provider: "" });
  assert.equal(storedButUnset.blocked, true);
  assert.equal(storedButUnset.notice, CONNECT_NOTICE);

  const storedButStub = playGate({ keyStored: true, provider: "stub" });
  assert.equal(storedButStub.blocked, true);
  assert.equal(storedButStub.notice, CONNECT_NOTICE);

  // A connection that has not been read yet is not a connection either: the
  // gate stays closed until the server says a real provider is saved.
  assert.equal(playGate({ keyStored: true }).blocked, true);
  assert.equal(playGate({ keyStored: true, provider: null }).blocked, true);

  // Both halves present: play opens, and there is no notice to show.
  const open = playGate({ keyStored: true, provider: "openai-compatible" });
  assert.equal(open.blocked, false);
  assert.equal(open.notice, null);

  // A provider without a key keeps the line closed too.
  const noKey = playGate({ keyStored: false, provider: "openai-compatible" });
  assert.equal(noKey.blocked, true);
  assert.equal(noKey.notice, CONNECT_NOTICE);

  // A server turn that just answered connect_your_ai closes it even when both
  // halves look present (the notice was caused by something we cannot see).
  const refused = playGate({ keyStored: true, provider: "openai-compatible", connectNeeded: true });
  assert.equal(refused.blocked, true);
  assert.equal(refused.notice, CONNECT_NOTICE);
});

test("keyAfterConnectionSave never wipes a stored key from an empty field", () => {
  // The 2026-09-14 finding: Save with a blank key field is a prefs-only save,
  // not a clear — forgetting a key is the explicit button's job.
  assert.equal(keyAfterConnectionSave("", "sk-stored"), "sk-stored");
  assert.equal(keyAfterConnectionSave("   ", "sk-stored"), "sk-stored");
  assert.equal(keyAfterConnectionSave(null, "sk-stored"), "sk-stored");
  assert.equal(keyAfterConnectionSave(undefined, "sk-stored"), "sk-stored");
  // A typed key replaces the stored one (trimmed, like the store's own normalizer).
  assert.equal(keyAfterConnectionSave("  sk-new  ", "sk-stored"), "sk-new");
  assert.equal(keyAfterConnectionSave("sk-new", ""), "sk-new");
  // Nothing anywhere stays nothing.
  assert.equal(keyAfterConnectionSave("", ""), "");
  assert.equal(keyAfterConnectionSave("", null), "");
});

test("formatWhen stays readable for missing and broken timestamps", () => {
  assert.equal(formatWhen(null), "not played yet");
  assert.equal(formatWhen(""), "not played yet");
  assert.equal(formatWhen("not-a-date"), "not-a-date");
  assert.notEqual(formatWhen("2026-09-14T06:00:00Z"), "not played yet");
});

// --------------------------------------------------------------------------- //
// Content boundaries (§11) — the request header + the applied echo
// --------------------------------------------------------------------------- //

/** A settings payload shaped the way lib/store.tsx persists it. */
function storedPrefs(content: Record<string, unknown>): string {
  return JSON.stringify({ fontSize: 16, highContrast: false, content });
}

test("contentPrefsHeader sends the canonical five-key JSON, normalized", () => {
  assert.equal(
    contentPrefsHeader({ violence: "reduced", horror: "off", romance: "off", language: "standard", nsfw: true })[
      "X-Content-Prefs"
    ],
    JSON.stringify({ violence: "reduced", horror: "off", romance: "off", language: "standard", nsfw: true })
  );
  // legacy vocabulary normalizes on the way out — a "low" never reaches the wire
  const legacy = { violence: "low" } as unknown as Parameters<typeof contentPrefsHeader>[0];
  assert.equal(
    contentPrefsHeader(legacy)["X-Content-Prefs"],
    JSON.stringify({ ...defaultPrefs, violence: "reduced" })
  );
  // nothing given: the store's own defaults, still a full payload (not {}.)
  assert.equal(contentPrefsHeader()["X-Content-Prefs"], JSON.stringify(defaultPrefs));
});

test("storedContentPrefs reads the store's slot at call time", () => {
  withStorage((mem) => {
    assert.deepEqual(storedContentPrefs(), defaultPrefs); // fresh browser
    mem.setItem(
      PREFS_STORAGE,
      storedPrefs({ violence: "standard", horror: "off", romance: "standard", language: "off", nsfw: true })
    );
    assert.deepEqual(storedContentPrefs(), {
      violence: "standard",
      horror: "off",
      romance: "standard",
      language: "off",
      nsfw: true,
    });
    // The toggle flipped a moment ago: the very next read sees it (the value is
    // never captured once — that is the "NSFW toggle lies" failure mode).
    mem.setItem(
      PREFS_STORAGE,
      storedPrefs({ violence: "standard", horror: "off", romance: "standard", language: "off", nsfw: false })
    );
    assert.equal(storedContentPrefs().nsfw, false);
  });
});

test("storedContentPrefs degrades to defaults on partial or broken storage", () => {
  withStorage((mem) => {
    mem.setItem(PREFS_STORAGE, storedPrefs({ nsfw: true })); // partial content block
    assert.deepEqual(storedContentPrefs(), { ...defaultPrefs, nsfw: true });
    mem.setItem(PREFS_STORAGE, "{not json");
    assert.deepEqual(storedContentPrefs(), defaultPrefs);
    mem.setItem(PREFS_STORAGE, JSON.stringify({ fontSize: 18 })); // no content block at all
    assert.deepEqual(storedContentPrefs(), defaultPrefs);
  });
});

test("storedContentPrefs never throws when storage is blocked", () => {
  const g = globalThis as Globals;
  const had = Object.prototype.hasOwnProperty.call(g, "localStorage");
  const before = g.localStorage;
  const hostile = { getItem(): string | null { throw new Error("blocked"); } };
  g.localStorage = hostile as unknown as Storage;
  try {
    assert.deepEqual(storedContentPrefs(), defaultPrefs);
  } finally {
    if (had) g.localStorage = before;
    else delete (g as { localStorage?: Storage }).localStorage;
  }
});

test("engineRequestHeaders attaches the key when set and always the boundaries", () => {
  withStorage((mem) => {
    const keyed = engineRequestHeaders(" sk-live ");
    assert.deepEqual(Object.keys(keyed).sort(), ["X-Content-Prefs", "X-Provider-Key"]);
    assert.equal(keyed["X-Provider-Key"], "sk-live");
    assert.equal(keyed["X-Content-Prefs"], JSON.stringify(defaultPrefs));

    // No key set: the boundaries still ride — they are not a secret, and the
    // server applies them with or without a live model.
    mem.setItem(PREFS_STORAGE, storedPrefs({ nsfw: true }));
    const keyless = engineRequestHeaders();
    assert.deepEqual(Object.keys(keyless), ["X-Content-Prefs"]);
    assert.equal(JSON.parse(keyless["X-Content-Prefs"]).nsfw, true);
    assert.equal(JSON.parse(keyless["X-Content-Prefs"]).romance, "off");
  });
});

test("appliedContent reads the turn's echo and tolerates a payload without one", () => {
  assert.equal(appliedContent(undefined), null);
  assert.equal(appliedContent(null), null);
  assert.equal(appliedContent({} as EngineTurn), null);
  // an unrelated object under a candidate key is not the echo
  assert.equal(appliedContent({ prefs: { theme: "dark" } } as unknown as EngineTurn), null);
  assert.equal(appliedContent({ content: ["reduced"] } as unknown as EngineTurn), null);

  const canonical = appliedContent({
    content: { violence: "reduced", horror: "off", romance: "off", language: "reduced", nsfw: true },
  } as EngineTurn);
  assert.deepEqual(canonical, {
    prefs: { violence: "reduced", horror: "off", romance: "off", language: "reduced", nsfw: true },
    defaultsApplied: false,
  });
  // the echo's flag, under either spelling the router might pick
  assert.equal(appliedContent({ content: { nsfw: false, defaults_applied: true } } as EngineTurn)?.defaultsApplied, true);
  assert.equal(appliedContent({ content: { nsfw: false, defaultsApplied: true } } as EngineTurn)?.defaultsApplied, true);
  // a partial echo normalizes the axes it does not carry
  assert.deepEqual(appliedContent({ content: { nsfw: true } } as EngineTurn)?.prefs, { ...defaultPrefs, nsfw: true });
  // a renamed container is still read (P8 owns the wire shape; the UI must not break)
  assert.equal(appliedContent({ content_prefs: { nsfw: true } } as unknown as EngineTurn)?.prefs.nsfw, true);
  assert.equal(appliedContent({ applied_prefs: { nsfw: true } } as unknown as EngineTurn)?.prefs.nsfw, true);
});

test("boundariesSummary is one readable line for the block", () => {
  assert.equal(
    boundariesSummary({ violence: "standard", horror: "standard", romance: "standard", language: "standard", nsfw: false }),
    "standard · NSFW off"
  );
  assert.equal(
    boundariesSummary(defaultPrefs),
    "violence reduced · horror reduced · romance off · language reduced · NSFW off"
  );
  assert.equal(boundariesSummary({ ...defaultPrefs, nsfw: true }), "uncensored (every limit lifted) · NSFW on");
});

test("boundariesNotice surfaces the defaults-applied note and nothing else", () => {
  assert.equal(boundariesNotice({} as EngineTurn), null);
  assert.equal(boundariesNotice({ system_lines: ["The road runs north."] } as EngineTurn), null);
  // a system line that merely says "default" about something else is not a note
  assert.equal(boundariesNotice({ system_lines: ["By default, the ferry runs at dawn."] } as EngineTurn), null);
  // the backend's own note (bridge.CONTENT_DEFAULTS_NOTE — §11's fallback)
  const backendNote = "content: settings could not be read — standard boundaries applied";
  assert.equal(
    boundariesNotice({ system_lines: ["The rain keeps on.", backendNote] } as EngineTurn),
    backendNote
  );
  // a note wording that says "defaults" without the backend's exact phrase
  const note = "Content boundaries could not be read — defaults applied.";
  assert.equal(boundariesNotice({ system_lines: [note] } as EngineTurn), note);
  // a flag on the echo, without any note, still tells the player
  const flagged = { content: { nsfw: false, defaults_applied: true }, system_lines: ["The rain keeps on."] } as EngineTurn;
  assert.match(boundariesNotice(flagged) ?? "", /standard boundaries governed this turn/);
  // with both, the server's own wording wins
  const both = {
    content: { nsfw: false, defaults_applied: true },
    system_lines: ["Content prefs were malformed — defaults applied."],
  } as EngineTurn;
  assert.equal(boundariesNotice(both), "Content prefs were malformed — defaults applied.");
});
