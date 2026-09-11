"use client";
// Settings screen: a11y (t_4f820f0d), content prefs via api headers (t_32d887a4),
// audio toggles (t_0c977269), AI cost display (t_eff821f1).
import { useStore } from "../../lib/store";
import { A11yControls, AudioControls, CostBadge } from "../../components/widgets";
import type { ContentPrefs } from "../../lib/store-types";

const OPTS: { key: keyof ContentPrefs; label: string; choices: string[]; help: string }[] = [
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

      <h2 style={{ marginTop: 20 }}>Sound</h2>
      <div className="parchment card">
        <AudioControls />
        <p className="sys">The chronicle is fully playable silent — ambience is garnish, never signal.</p>
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
