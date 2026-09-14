"use client";
// Settings screen: a11y (t_4f820f0d), content prefs via api headers (t_32d887a4),
// audio toggles (t_0c977269), AI cost display (t_eff821f1), dice preview (t_ae0e86a6),
// bring-your-own AI endpoint (t_4c575bb5).
import { useCallback, useEffect, useState } from "react";
import { useStore } from "../../lib/store";
import { A11yControls, AudioControls, CostBadge } from "../../components/widgets";
import { Dice } from "../../components/dice3d";
import {
  aiSettingsApi,
  saveAiSettingsApi,
  testAiSettingsApi,
  getToken,
  type AiSettingsDoc,
  type AiSettingsPayload,
} from "../../lib/api";
import { aiConnected, aiConnectionLine, aiNotConnectedReason } from "../../lib/ai";
import type { ContentPrefs } from "../../lib/store-types";

const OPTS: { key: Exclude<keyof ContentPrefs, "nsfw">; label: string; choices: string[]; help: string }[] = [
  { key: "violence", label: "Violence", choices: ["off", "reduced", "standard"], help: "How plainly harm is described." },
  { key: "horror", label: "Horror", choices: ["off", "reduced", "standard"], help: "Dread, gore, and things in the dark." },
  { key: "romance", label: "Romance", choices: ["off", "reduced", "standard"], help: "Tenderness on the road." },
  { key: "language", label: "Language", choices: ["off", "reduced", "standard"], help: "Oaths at the table." },
];

export default function SettingsPage() {
  const s = useStore();

  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: 16 }}>
      <h1>Settings</h1>
      <p className="sys">Kept in this browser. Content boundaries travel with every request to the chronicler.</p>

      <h2>Reading &amp; motion</h2>
      <A11yControls />

      <h2 style={{ marginTop: 20 }}>Tale-spinner (AI)</h2>
      <AiProviderPanel />

      <h2 style={{ marginTop: 20 }}>Story boundaries</h2>
      <div className="parchment card">
        {OPTS.map((o) => (
          <div key={o.key} style={{ margin: "10px 0" }}>
            <label htmlFor={`pref-${o.key}`}><strong>{o.label}</strong> <span className="sys">— {o.help}</span></label>
            <br />
            <select
              id={`pref-${o.key}`}
              className="input-parch"
              style={{ width: "auto", marginTop: 4 }}
              value={s.content[o.key]}
              onChange={(e) => s.set({ content: { ...s.content, [o.key]: e.target.value } as ContentPrefs })}
            >
              {o.choices.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        ))}
        <p className="sys">Sent with every chronicle request as story-boundary headers (see lib/api.ts) and enforced when the tale is woven — never stored on the server beyond the session.</p>
      </div>

      <h2 style={{ marginTop: 20 }}>Adult content</h2>
      <div className="parchment card">
        <label style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <input
            type="checkbox"
            checked={s.content.nsfw === true}
            onChange={(e) => s.set({ content: { ...s.content, nsfw: e.target.checked } })}
            aria-describedby="nsfw-help"
          />
          <span><strong>Enable NSFW — uncensored</strong></span>
        </label>
        <p className="sys" id="nsfw-help" style={{ margin: "8px 0 0" }}>
          When on, all content limits are lifted: graphic and explicit content, no
          fade to black. Overrides the story boundaries above while enabled. Off by
          default; travels with every chronicle request.
        </p>
      </div>

      <h2 style={{ marginTop: 20 }}>Sound</h2>
      <div className="parchment card">
        <AudioControls />
        <p className="sys">The chronicle is fully playable silent — ambience is garnish, never signal.</p>
      </div>

      <h2 style={{ marginTop: 20 }}>The dice</h2>
      <div className="parchment card">
        <p className="sys" style={{ marginTop: 0 }}>
          When a check happens in the chronicle, a real die tumbles and lands on the roll.
          Natural 20s and natural 1s get their own flourishes — try them here.
          Quiet mode (Reduce motion) shows the settled die without the tumble.
        </p>
        <DiceDemo />
      </div>

      <h2 style={{ marginTop: 20 }}>Tale-telling cost</h2>
      <div className="parchment card">
        <p><CostBadge /></p>
        <dl className="kv">
          <dt>Narration calls</dt><dd>{s.totalCalls} this session</dd>
          <dt>Estimated cost</dt><dd>${s.totalCost.toFixed(4)}</dd>
          <dt>Last answer</dt><dd>{s.lastCached ? "kept from cache or local pages — no cost" : "freshly woven — one call"}</dd>
        </dl>
        <p className="sys">One player action costs one narration call; small chores (sorting, remembering) cost none. Cached and local answers are free.</p>
      </div>

      <h2 style={{ marginTop: 20 }}>Beginning again</h2>
      <div className="parchment card">
        <button className="btn btn-ghost" onClick={() => s.set({ tutorialDone: false })}>Replay the three-step introduction</button>
      </div>
    </div>
  );
}

/** Bring-your-own AI: point the chronicle at any OpenAI-compatible endpoint.
 *  Stored per account on the server; the API key is write-only (never echoed).
 *  There is no built-in storyteller to fall back on (P12): the chronicle is
 *  playable only with a connected model, so this card states exactly which
 *  model the server will use — the SAME resolution /act uses. */
function AiProviderPanel() {
  const [doc, setDoc] = useState<AiSettingsDoc | null>(null);
  const [signedIn, setSignedIn] = useState(true);
  const [status, setStatus] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [timeoutS, setTimeoutS] = useState(30);

  const load = useCallback(async () => {
    if (!getToken()) { setSignedIn(false); return; }
    const r = await aiSettingsApi();
    if (!r.ok) { setSignedIn(false); setStatus({ kind: "err", text: r.error }); return; }
    setSignedIn(true);
    const d = r.doc;
    setDoc(d);
    setBaseUrl(d.base_url);
    setModel(d.model);
    setTimeoutS(d.timeout_s);
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function save() {
    setBusy(true); setStatus(null);
    const payload: AiSettingsPayload = {
      provider: "openai-compatible",
      base_url: baseUrl,
      model,
      timeout_s: timeoutS,
    };
    // A typed key replaces the stored one; a blank field keeps it (write-only).
    if (apiKey) payload.api_key = apiKey;
    const r = await saveAiSettingsApi(payload);
    setBusy(false);
    if (!r.ok) { setStatus({ kind: "err", text: r.error }); return; }
    setApiKey("");
    setDoc(r.doc);
    setStatus({
      kind: "ok",
      text: r.doc.connected
        ? `Saved — the chronicle will ask ${r.doc.active_model || "your endpoint"} for narration.`
        : "Saved — but the endpoint is not complete yet, so the chronicle stays closed until it is.",
    });
  }

  async function test() {
    setBusy(true); setStatus(null);
    const draft: { base_url?: string; model?: string; api_key?: string; timeout_s?: number } = {
      base_url: baseUrl || undefined,
      model: model || undefined,
      timeout_s: timeoutS,
    };
    // Blank key field: reuse the stored key when there is one, else test bare.
    if (apiKey) draft.api_key = apiKey;
    else if (!doc?.has_key) draft.api_key = "";
    const r = await testAiSettingsApi(draft);
    setBusy(false);
    if (!r.ok) { setStatus({ kind: "err", text: r.error }); return; }
    const d = r.doc;
    setStatus(d.ok
      ? { kind: "ok", text: `The endpoint answered (${d.latency_ms} ms): “${d.reply ?? ""}”` }
      : { kind: "err", text: d.error ?? "The endpoint did not answer." });
  }

  const connected = aiConnected(doc);
  const who = aiConnectionLine(doc);
  const source = doc?.active_source === "env"
    ? "from the server's environment config"
    : doc?.active_source === "settings" ? "from your settings" : "";

  return (
    <div className="parchment card">
      {!signedIn ? (
        <p className="sys" style={{ margin: 0 }}>
          Sign in to connect your AI — the chronicle narrates with a model, and this is where
          you point it at yours. Settings are stored with your account.
        </p>
      ) : (
        <>
          <p className="sys" style={{ marginTop: 0 }} role="status" data-ai="state">
            {connected ? (
              <>Right now: <strong>{who}</strong>{source ? ` — ${source}` : ""}. The chronicle is playable.</>
            ) : (
              <>Right now: <strong>nothing connected</strong> — {aiNotConnectedReason(doc)} The
              chronicle cannot take a turn until a model is connected.</>
            )}
          </p>
          <div style={{ display: "grid", gap: 8, margin: "10px 0" }}>
            <label htmlFor="ai-base-url">Base URL
              <input id="ai-base-url" className="input-parch" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" autoComplete="off" />
            </label>
            <label htmlFor="ai-model">Model
              <input id="ai-model" className="input-parch" value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-5-mini, llama-3.3-70b, …" autoComplete="off" />
            </label>
            <label htmlFor="ai-api-key">API key
              <input id="ai-api-key" className="input-parch" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="new-password"
                placeholder={doc?.has_key ? "•••• saved — leave blank to keep it" : "sk-… (leave blank for keyless local servers)"} />
            </label>
            <label htmlFor="ai-timeout">Timeout (seconds)
              <input id="ai-timeout" className="input-parch" type="number" min={1} max={120} style={{ width: 110 }} value={timeoutS} onChange={(e) => setTimeoutS(Number(e.target.value))} />
            </label>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn" onClick={() => void save()} disabled={busy || !baseUrl.trim() || !model.trim()}>{busy ? "…" : "Save"}</button>
            <button className="btn btn-ghost" onClick={() => void test()} disabled={busy || !baseUrl.trim() || !model.trim()}>Test connection</button>
            {doc?.has_key && (
              <button className="btn btn-ghost" onClick={() => { setApiKey(""); void saveWithClearKey(); }} disabled={busy}>
                Remove stored key
              </button>
            )}
            {status && (
              <span className="sys" role="status" style={{ color: status.kind === "err" ? "#e09a9a" : "var(--parch-1)" }}>
                {status.text}
              </span>
            )}
          </div>
          <p className="sys" style={{ marginBottom: 0 }}>
            The key is stored on the server for your account and never shown again; requests go
            straight from this server to your endpoint. There is no keyless mode: a saved endpoint
            that is missing its base URL or model reads as not connected, and nothing else
            narrates in its place.
          </p>
        </>
      )}
    </div>
  );

  async function saveWithClearKey() {
    setBusy(true); setStatus(null);
    const r = await saveAiSettingsApi({ provider: "openai-compatible", base_url: baseUrl, model, clear_key: true, timeout_s: timeoutS });
    setBusy(false);
    if (!r.ok) { setStatus({ kind: "err", text: r.error }); return; }
    setDoc(r.doc);
    setStatus({ kind: "ok", text: "Stored key removed — the endpoint is still connected if it needs no key." });
  }
}

/** Live preview of the 3D die: a normal roll, a natural 20, a natural 1. */
function DiceDemo() {
  const [runs, setRuns] = useState({ roll: 0, crit: 0, miss: 0 });
  const [face, setFace] = useState(14);
  const bump = (k: keyof typeof runs) => setRuns((r) => ({ ...r, [k]: r[k] + 1 }));
  return (
    <div className="dice-demo">
      <div className="dice-demo-cell">
        <Dice key={`roll-${runs.roll}`} d20={face} outcome={face === 20 ? "Exceptional" : face === 1 ? "CriticalFailure" : "Success"} size={132} autoPlay />
        <p className="sys dice-demo-note">A check — the die tumbles and lands on the roll.</p>
        <button className="btn btn-ghost" onClick={() => { setFace(1 + Math.floor(Math.random() * 20)); bump("roll"); }}>Roll a d20</button>
      </div>
      <div className="dice-demo-cell">
        <Dice key={`crit-${runs.crit}`} d20={20} outcome="Exceptional" size={132} autoPlay />
        <p className="sys dice-demo-note">Critical success — natural 20, a golden burst.</p>
        <button className="btn btn-ghost" onClick={() => bump("crit")}>Replay</button>
      </div>
      <div className="dice-demo-cell">
        <Dice key={`miss-${runs.miss}`} d20={1} outcome="CriticalFailure" size={132} autoPlay />
        <p className="sys dice-demo-note">Critical miss — natural 1, a dire slam and ash.</p>
        <button className="btn btn-ghost" onClick={() => bump("miss")}>Replay</button>
      </div>
    </div>
  );
}
