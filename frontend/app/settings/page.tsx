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
} from "../../lib/api";
import type { ContentPrefs } from "../../lib/store-types";

const OPTS: { key: Exclude<keyof ContentPrefs, "nsfw">; label: string; choices: string[]; help: string }[] = [
  { key: "violence", label: "Violence", choices: ["off", "low", "standard"], help: "How plainly harm is described." },
  { key: "horror", label: "Horror", choices: ["off", "low", "standard"], help: "Dread, gore, and things in the dark." },
  { key: "romance", label: "Romance", choices: ["off", "low", "standard"], help: "Tenderness on the road." },
  { key: "language", label: "Language", choices: ["clean", "mild"], help: "Oaths at the table." },
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
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
          <input
            type="checkbox"
            checked={s.content.nsfw === true}
            onChange={(e) => {
              if (e.target.checked) {
                const ok = window.confirm(
                  "Allow explicit adult content in the chronicle?\n\nThis lifts the fade-to-black for sexual content. It stays OFF unless you opt in here, is kept only in this browser, and travels with requests as prefs.nsfw. You can turn it back off any time."
                );
                if (!ok) return;
                s.set({ content: { ...s.content, nsfw: true } });
              } else {
                s.set({ content: { ...s.content, nsfw: false } });
              }
            }}
            aria-describedby="nsfw-help"
          />
          <span>
            <strong>Allow explicit adult content (18+)</strong>
            <br />
            <span className="sys" id="nsfw-help">
              Default OFF. When off, the chronicler fades to black. When on, mature
              romantic/erotic content may be described plainly — still within
              consent and story boundaries above. Applies to new narration after
              you toggle it.
            </span>
          </span>
        </label>
        <p className="sys" style={{ marginBottom: 0 }}>
          Status: <strong>{s.content.nsfw ? "ON — explicit adult content allowed" : "OFF — fade to black"}</strong>
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
 *  Sent to the chronicler with every /act so free-text beats are narrated by
 *  your model. Default: the built-in storyteller (no AI calls, free). */
function AiProviderPanel() {
  const [doc, setDoc] = useState<AiSettingsDoc | null>(null);
  const [signedIn, setSignedIn] = useState(true);
  const [status, setStatus] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"stub" | "openai-compatible">("stub");
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
    setMode(d.provider ?? (d.env_provider === "openai-compatible" ? "openai-compatible" : "stub"));
    setBaseUrl(d.base_url);
    setModel(d.model);
    setTimeoutS(d.timeout_s);
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function save() {
    setBusy(true); setStatus(null);
    const r = await saveAiSettingsApi({
      provider: mode,
      base_url: baseUrl,
      model,
      api_key: apiKey ? apiKey : undefined,
      timeout_s: timeoutS,
    });
    setBusy(false);
    if (!r.ok) { setStatus({ kind: "err", text: r.error }); return; }
    setApiKey("");
    setDoc(r.doc);
    setStatus({
      kind: "ok",
      text: r.doc.active_provider === "stub"
        ? "Saved — the built-in storyteller writes the chronicle."
        : `Saved — the chronicle now asks ${r.doc.model || "your endpoint"} for narration.`,
    });
    setMode(r.doc.provider ?? "stub");
  }

  async function test() {
    setBusy(true); setStatus(null);
    const r = await testAiSettingsApi({
      base_url: baseUrl || undefined,
      model: model || undefined,
      // Blank field: reuse the saved key when one exists, else test without auth.
      api_key: apiKey ? apiKey : (doc?.has_key ? undefined : ""),
      timeout_s: timeoutS,
    });
    setBusy(false);
    if (!r.ok) { setStatus({ kind: "err", text: r.error }); return; }
    const d = r.doc;
    setStatus(d.ok
      ? { kind: "ok", text: `The endpoint answered (${d.latency_ms} ms): “${d.reply ?? ""}”` }
      : { kind: "err", text: d.error ?? "The endpoint did not answer." });
  }

  const activeText = doc?.active_provider === "openai-compatible"
    ? `your endpoint${doc.model ? ` (${doc.model})` : ""}`
    : "the built-in storyteller (free, no AI calls)";
  const activeSource = doc?.active_source === "env" ? "from the environment config" : doc?.active_source === "settings" ? "from your settings" : "";

  return (
    <div className="parchment card">
      {!signedIn ? (
        <p className="sys" style={{ margin: 0 }}>Sign in to add your own AI endpoint — it is stored with your account.</p>
      ) : (
        <>
          <p className="sys" style={{ marginTop: 0 }}>
            Right now: <strong>{activeText}</strong>{activeSource ? ` — ${activeSource}` : ""}. Applies to free-text actions in the chronicle.
          </p>
          <div style={{ display: "grid", gap: 8, margin: "10px 0" }}>
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="radio" name="ai-provider" checked={mode === "stub"} onChange={() => setMode("stub")} />
              Built-in storyteller (free)
            </label>
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="radio" name="ai-provider" checked={mode === "openai-compatible"} onChange={() => setMode("openai-compatible")} />
              My own OpenAI-compatible endpoint
            </label>
          </div>
          {mode === "openai-compatible" && (
            <div style={{ display: "grid", gap: 8 }}>
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
              {doc?.has_key && (
                <button className="btn btn-ghost" style={{ width: "fit-content" }} onClick={() => { setApiKey(""); void saveWithClearKey(); }} disabled={busy}>
                  Remove stored key
                </button>
              )}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn" onClick={() => void save()} disabled={busy}>{busy ? "…" : "Save"}</button>
            <button className="btn btn-ghost" onClick={() => void test()} disabled={busy || !baseUrl.trim() || !model.trim()}>Test connection</button>
            {status && (
              <span className="sys" role="status" style={{ color: status.kind === "err" ? "#e09a9a" : "var(--parch-1)" }}>
                {status.text}
              </span>
            )}
          </div>
          <p className="sys" style={{ marginBottom: 0 }}>
            The key is stored on the server for your account and never shown again; requests go straight from this server to your endpoint.
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
    setStatus({ kind: "ok", text: "Stored key removed." });
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
