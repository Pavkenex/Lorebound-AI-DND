"use client";
// Skills screen (t_4b8ec292): tier + XP bar, recent learning, trainers, subtle level-up toast.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { fixtures } from "../../lib/fixtures";
import { useStore } from "../../lib/store";
import { uiBlip } from "../../lib/audio";

interface Skill { name: string; tier: string; xp: number; recent: string[]; trainers: string[]; practice: string }

const TIER_MAX: Record<string, number> = { Novice: 500, Apprentice: 1000, Journeyman: 2000, Adept: 3000, Skilled: 4000, Master: 6000 };

export default function SkillsPage() {
  const { content, addCost } = useStore();
  const [skills, setSkills] = useState<Skill[]>(fixtures.skills.list as Skill[]);
  const [notes, setNotes] = useState<string[]>(fixtures.skills.notes);
  const [live, setLive] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    api.skills(content).then((r) => {
      const d = r.data as typeof fixtures.skills;
      setSkills(d.list as Skill[]); setNotes(d.notes);
      setLive(!r.fromFixture); addCost(r.cost);
      if (!r.fromFixture && d.notes.length > 0) {
        setToast(d.notes[0]);
        uiBlip(740);
        setTimeout(() => setToast(null), 6000);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 16 }}>
      <h1>Skills</h1>
      <p className="sys">{live ? "Watched and witnessed." : "Local pages (backend unreachable)."} Growth shows here in hindsight — never as an interruption.</p>
      {toast && <div className="level-toast" role="status">✦ {toast}</div>}
      {!live && notes.length > 0 && (
        <div aria-label="Recent growth">
          {notes.map((x) => <div className="level-toast" key={x} role="status">✦ {x}</div>)}
        </div>
      )}
      <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
        {skills.map((s) => {
          const max = TIER_MAX[s.tier] ?? 2000;
          const pct = Math.min(100, (s.xp / max) * 100);
          return (
            <section key={s.name} className="parchment card" aria-label={s.name}>
              <h2 style={{ margin: "0 0 4px" }}>{s.name} <span className="sys">— {s.tier}</span></h2>
              <p style={{ margin: "2px 0 6px" }}><strong>{s.xp.toLocaleString()} / {max.toLocaleString()}</strong> <span className="sys">marks toward {nextTier(s.tier)}</span></p>
              <div className="xpbar" role="img" aria-label={`${s.name}, ${s.tier}, ${s.xp} of ${max}`}><i style={{ width: `${pct}%` }} /></div>
              <div className="grid2" style={{ marginTop: 8 }}>
                <div>
                  <h3>Recent learning</h3>
                  <ul>{s.recent.map((r) => <li key={r}>{r}</li>)}</ul>
                </div>
                <div>
                  <h3>Teachers &amp; practice</h3>
                  <p style={{ margin: "4px 0" }}>{s.trainers.map((t) => <span className="tag gold" key={t}>{t}</span>)}</p>
                  <p className="sys">Try: {s.practice}</p>
                </div>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function nextTier(t: string): string {
  const order = ["Novice", "Apprentice", "Journeyman", "Adept", "Skilled", "Master"];
  const i = order.indexOf(t);
  return i >= 0 && i < order.length - 1 ? order[i + 1] : "legend";
}
