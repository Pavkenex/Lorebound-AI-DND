"use client";
// Inventory screen: quality colors, 10 equipment slots, encumbrance meter. Fixture fallback.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { fixtures } from "../../lib/fixtures";
import { useStore } from "../../lib/store";

interface Item { name: string; kind: string; note: string }

const SLOTS = ["Head", "Torso", "Hands", "Feet", "Main Hand", "Off Hand", "Ranged", "Cloak", "Trinket 1", "Trinket 2"];

function slotFor(it: Item): string | null {
  const n = it.name.toLowerCase();
  if (n.includes("bow")) return "Ranged";
  if (n.includes("knife") || n.includes("sword") || n.includes("axe")) return "Main Hand";
  if (n.includes("lantern")) return "Trinket 1";
  if (n.includes("cloak")) return "Cloak";
  return null;
}

function quality(it: Item): { q: string; cls: string } {
  if (it.kind === "Keepsake") return { q: "heirloom", cls: "q-heirloom" };
  if (it.kind === "Weapon") return { q: "fine", cls: "q-fine" };
  if (it.kind === "Coin") return { q: "common", cls: "q-common" };
  return { q: "common", cls: "q-common" };
}

function weight(it: Item): number {
  if (it.kind === "Coin") return 1;
  if (it.name.toLowerCase().includes("bow")) return 4;
  return 2;
}

export default function InventoryPage() {
  const { content, addCost } = useStore();
  const [items, setItems] = useState<Item[]>(fixtures.inventory);
  const [live, setLive] = useState(false);

  useEffect(() => {
    api.inventory(content).then((r) => { setItems(r.data as Item[]); setLive(!r.fromFixture); addCost(r.cost); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const equipped = new Map<string, Item>();
  const pack: Item[] = [];
  for (const it of items) {
    const s = slotFor(it);
    if (s && !equipped.has(s)) equipped.set(s, it);
    else pack.push(it);
  }
  const coins = items.find((i) => i.kind === "Coin");
  const carried = items.filter((i) => i.kind !== "Coin").reduce((a, i) => a + weight(i), 0);
  const CAP = 40;
  const pct = Math.min(100, (carried / CAP) * 100);
  const enc = pct > 90 ? "Overladen — checks suffer" : pct > 70 ? "Heavy — the road will notice" : "Comfortable";

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 16 }}>
      <h1>Inventory</h1>
      <p className="sys">{live ? "Counted by the quartermaster." : "Local pages (backend unreachable) — every coin accounted for."}{coins ? ` · Purse: ${coins.name}.` : ""}</p>

      <section className="parchment card" aria-label="Encumbrance">
        <h2>Bearing the road — {enc}</h2>
        <div className="enc" role="img" aria-label={`Encumbrance ${carried} of ${CAP}: ${enc}`}>
          <i style={{ width: `${pct}%` }} className={pct > 90 ? "enc-over" : pct > 70 ? "enc-heavy" : ""} />
        </div>
        <p className="sys">{carried} stone of {CAP} · weapons and keepsakes weigh; coin rides light.</p>
      </section>

      <h2 style={{ marginTop: 16 }}>Worn &amp; wielded</h2>
      <div className="slots" role="list">
        {SLOTS.map((s) => {
          const it = equipped.get(s);
          return (
            <div key={s} className="parchment card slot" role="listitem">
              <span className="sys">{s}</span>
              {it ? (
                <><strong className={quality(it).cls}>{it.name}</strong><br /><small className="sys">{quality(it).q} · {it.note}</small></>
              ) : (
                <span className="sys">— empty —</span>
              )}
            </div>
          );
        })}
      </div>

      <h2 style={{ marginTop: 16 }}>Pack</h2>
      <div className="grid2">
        {pack.length === 0 && <p className="sys">Nothing loose. A tidy traveller.</p>}
        {pack.map((it) => {
          const q = quality(it);
          return (
            <div key={it.name} className="parchment card">
              <strong className={q.cls}>{it.name}</strong> <span className={`tag ${q.cls}`}>{q.q}</span>
              <p className="sys" style={{ margin: "4px 0 0" }}>{it.kind} · {it.note} · {weight(it)} stone</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
