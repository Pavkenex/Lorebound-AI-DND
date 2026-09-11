"use client";
// Small shared widgets: cost badge (t_eff821f1), error banner + retry (t_18510814),
// audio + a11y controls (t_0c977269, t_4f820f0d), tutorial overlay (t_d2dd38a0).
import { useState } from "react";
import { useStore, type Ambient } from "../lib/store";
import { uiBlip } from "../lib/audio";

export function CostBadge() {
  const { totalCalls, totalCost, lastCached } = useStore();
  return (
    <span
      className="tag gold"
      title={`AI narration calls this session: ${totalCalls}. Estimated cost $${totalCost.toFixed(4)}. ${lastCached ? "Last response served from cache/fixture (no AI cost)." : "Last response used live AI narration."}`}
    >
      ◈ {totalCalls} call{totalCalls === 1 ? "" : "s"} · ${totalCost.toFixed(4)}{lastCached ? " · cached" : ""}
    </span>
  );
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="error-banner" role="alert">
      <strong>The chronicler stumbled —</strong> {message} Your words and the dice stand; nothing was lost.
      <div style={{ marginTop: 8 }}>
        <button className="btn" onClick={() => { uiBlip(440); onRetry(); }}>Retry narration</button>
      </div>
    </div>
  );
}

const AMBIENTS: Ambient[] = ["off", "tavern", "rain", "forest", "combat"];

export function AudioControls() {
  const { ambient, muted, set } = useStore();
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <label className="sys" htmlFor="ambient">Ambience</label>
      <select
        id="ambient"
        className="input-parch"
        style={{ width: "auto" }}
        value={ambient}
        onChange={(e) => { uiBlip(); set({ ambient: e.target.value as Ambient }); }}
      >
        {AMBIENTS.map((a) => <option key={a} value={a}>{a === "off" ? "Silent" : a[0].toUpperCase() + a.slice(1)}</option>)}
      </select>
      <button className="btn btn-ghost" onClick={() => set({ muted: !muted })} aria-pressed={muted}>
        {muted ? "🔇 Muted" : "🔊 Sound on"}
      </button>
    </div>
  );
}

export function A11yControls() {
  const s = useStore();
  return (
    <fieldset className="parchment card" style={{ border: "1px solid var(--line)" }}>
      <legend className="sys">Reading &amp; motion</legend>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <label>Text size
          <input
            type="range" min={14} max={22} step={1} value={s.fontSize} aria-label="Text size"
            onChange={(e) => s.set({ fontSize: Number(e.target.value) })} style={{ marginLeft: 8 }}
          /> {s.fontSize}px
        </label>
        {([
          ["highContrast", "High contrast"],
          ["reducedMotion", "Reduce motion"],
          ["hideArtwork", "Hide artwork (text-first)"],
          ["compactNarration", "Compact narration"],
        ] as const).map(([k, label]) => (
          <label key={k} style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={s[k]} onChange={(e) => s.set({ [k]: e.target.checked } as object as Parameters<typeof s.set>[0])} /> {label}
          </label>
        ))}
      </div>
      <p className="sys">Keyboard: Tab through the chronicle · 1–8 jump between pages · Enter submits. No decision is ever timed.</p>
    </fieldset>
  );
}

const STEPS = [
  { h: "Act with words", p: "Type anything — “ask Marla about the wagon”, “inspect the seal”, “draw my bow”. There are no wrong verbs. Press Enter; the world answers." },
  { h: "Checks", p: "When risk appears you'll see a compact dice seal (⬢ d20). Click it for detail. Success moves you on; partial success moves you on at a cost." },
  { h: "Journal", p: "Every discovery becomes a LEAD. Open the Journal to trace the Missing Caravan thread — caravan → wagon → silver powder → guild → monastery." },
];

export function TutorialOverlay() {
  const { tutorialDone, set } = useStore();
  const done = tutorialDone;
  if (done) return null;
  return <TutSteps onDone={() => set({ tutorialDone: true })} />;
}

function TutSteps({ onDone }: { onDone: () => void }) {
  const [i, setI] = useState(0);
  const last = i === STEPS.length - 1;
  return (
    <div className="tut-veil" role="dialog" aria-modal="true" aria-label="How to play">
      <div className="parchment tut-card">
        <p className="sys">A TEN-MINUTE BEGINNING · {i + 1} / {STEPS.length}</p>
        <h2>{STEPS[i].h}</h2>
        <p>{STEPS[i].p}</p>
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          {i > 0 && <button className="btn btn-ghost" onClick={() => setI(i - 1)}>Back</button>}
          {!last && <button className="btn" onClick={() => { uiBlip(); setI(i + 1); }}>Next</button>}
          {last && <button className="btn" onClick={() => { uiBlip(740); onDone(); }}>Begin the chronicle</button>}
          <button className="btn btn-ghost" onClick={onDone}>Skip</button>
        </div>
      </div>
    </div>
  );
}
