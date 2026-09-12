"use client";
// Companions screen: cards with autonomy notes. Fixture fallback.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { fixtures } from "../../lib/fixtures";
import { useStore } from "../../lib/store";
import { Portrait } from "../../components/art";

interface C { name: string; role: string; note: string; hue: number }

const AUTONOMY: Record<string, string> = {
  "Bram Holloway": "Walks his own road: scouts ahead without being asked, shares his rations unasked, and will refuse an order that leaves the weak behind. Allow him the treeline and he doubles your eyes.",
  "Sister Pell": "Keeps her own counsel: slips away to pray at dusk, pockets small useful things, and answers monastery questions only when she chooses. Press her gently or not at all.",
};

export default function CompanionsPage() {
  const { content, addCost } = useStore();
  const [list, setList] = useState<C[]>(fixtures.companions as C[]);
  const [live, setLive] = useState(false);

  useEffect(() => {
    api.companions(content).then((r) => { setList(r.data as C[]); setLive(!r.fromFixture); addCost(r.cost); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 16 }}>
      <h1>Companions</h1>
      <p className="sys">
        {live ? "Travelling with you." : "Local pages (backend unreachable)."}
        {" "}They act on their own judgment — the notes say how, so you can plan around it.
      </p>
      {live && list.length === 0 && (
        <p className="parchment card" style={{ marginTop: 12 }}>
          You travel alone for now — no one has bound their road to yours yet. The inns
          and roads of Ravenford are full of people whose stories could still join your own.
        </p>
      )}
      <div className="grid2" style={{ marginTop: 12 }}>
        {list.map((c) => (
          <article key={c.name} className="parchment card" aria-label={c.name}>
            <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
              <Portrait name={c.name} hue={c.hue} />
              <div>
                <h2 style={{ margin: 0 }}>{c.name}</h2>
                <p className="sys" style={{ margin: 0 }}>{c.role} · <span className="tag gold">travelling</span></p>
              </div>
            </div>
            <p style={{ fontStyle: "italic" }}>{c.note}</p>
            <h3>Of their own will</h3>
            <p className="sys" style={{ color: "var(--parch-1)" }}>{AUTONOMY[c.name] ?? "New to the road — their habits are still showing."}</p>
          </article>
        ))}
      </div>
      <p className="sys" style={{ marginTop: 12 }}>An empty bedroll by the fire: there is room for one more, should the road provide.</p>
    </div>
  );
}
