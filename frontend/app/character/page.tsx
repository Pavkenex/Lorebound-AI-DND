"use client";
// Character screen (t_fc29e1c4): reads as a person, not a stat block. Fixture fallback.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { fixtures } from "../../lib/fixtures";
import { useStore } from "../../lib/store";
import { Portrait } from "../../components/art";

type C = typeof fixtures.character;

export default function CharacterPage() {
  const { content, addCost } = useStore();
  const [c, setC] = useState<C>(fixtures.character);
  const [live, setLive] = useState(false);

  useEffect(() => {
    api.character(content).then((r) => { setC(r.data); setLive(!r.fromFixture); addCost(r.cost); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 16 }}>
      <p className="sys">The person of the chronicle{live ? "" : " · local pages (backend unreachable)"} <span>· <a href="/create">Forge anew</a></span></p>
      <header className="parchment card" style={{ display: "flex", gap: 18, alignItems: "center" }}>
        <Portrait name={c.name} hue={c.portraitHue} size={96} />
        <div>
          <h1 style={{ margin: 0 }}>{c.name}</h1>
          <p style={{ margin: "2px 0" }}><em>{c.epithet}</em> · Level {c.level} <span className="sys">· {c.xp}% of the way to the next turning</span></p>
        </div>
      </header>

      <section className="parchment card" style={{ marginTop: 12 }} aria-label="Background">
        <h2>How they came to the road</h2>
        <p style={{ fontStyle: "italic" }}>{c.background}</p>
      </section>

      <div className="grid2" style={{ marginTop: 12 }}>
        <section className="parchment card" aria-label="Health">
          <h2>Body &amp; spirit</h2>
          <dl className="kv">
            <dt>Health</dt><dd>{c.hp.cur} of {c.hp.max}</dd>
            <dt>Stamina</dt><dd>{c.stamina.cur} of {c.stamina.max}</dd>
            <dt>Resolve</dt><dd>{c.resolve.cur} of {c.resolve.max}</dd>
          </dl>
          <div className="bar bar-hp" role="img" aria-label={`Health ${c.hp.cur} of ${c.hp.max}`}><i style={{ width: `${(c.hp.cur / c.hp.max) * 100}%` }} /></div>
          <h3>Burdens carried</h3>
          <p>{c.conditions.map((x) => <span className="tag red" key={x}>{x}</span>)}</p>
        </section>
        <section className="parchment card" aria-label="Attributes">
          <h2>What they are like</h2>
          <dl className="kv">
            {Object.entries(c.attributes).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v} <span className="sys">{flavour(k, v as number)}</span></dd></div>
            ))}
          </dl>
          <h3>Habits of the heart</h3>
          <ul>{c.traits.map((t) => <li key={t}>{t}</li>)}</ul>
        </section>
      </div>

      <div className="grid2" style={{ marginTop: 12 }}>
        <section className="parchment card" aria-label="Drives">
          <h2>What pulls them forward</h2>
          <ul>{c.drives.map((d) => <li key={d}>{d}</li>)}</ul>
          <h2>What they have done</h2>
          <ul>{c.achievements.map((a) => <li key={a}>{a}</li>)}</ul>
        </section>
        <section className="parchment card" aria-label="Relationships and equipment">
          <h2>Bound to</h2>
          <ul>{c.relationships.map((r) => <li key={r.name}><strong>{r.name}</strong> <span className="sys">— {r.note}</span></li>)}</ul>
          <h2>Carried close</h2>
          <ul>{c.equipment.map((e) => <li key={e}>{e}</li>)}</ul>
        </section>
      </div>
    </div>
  );
}

function flavour(attr: string, v: number): string {
  if (v >= 15) return attr === "Finesse" ? "— moves like lamplight" : "— remarkable";
  if (v >= 13) return "— steady";
  if (v >= 11) return "— enough, most days";
  return "— her weak side";
}
