"use client";
// Settings screen: a11y (t_4f820f0d), content prefs via api headers (t_32d887a4),
// audio toggles (t_0c977269), AI cost display (t_eff821f1), dice preview (t_ae0e86a6).
import { useState } from "react";
import { useStore } from "../../lib/store";
import { A11yControls, AudioControls, CostBadge } from "../../components/widgets";
import { Dice } from "../../components/dice3d";
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
