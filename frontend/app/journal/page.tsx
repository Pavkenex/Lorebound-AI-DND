"use client";
// Journal screen (t_d5b6fce3): tabs + clickable SVG lead-graph. Fixture fallback.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { fixtures } from "../../lib/fixtures";
import { useStore } from "../../lib/store";

type J = typeof fixtures.journal;
type Node = J["nodes"][number];

const TABS = ["Story Leads", "People", "Places", "Factions", "Lore", "Chronicle"] as const;

const PEOPLE = [
  { name: "Marla Voss", note: "Innkeeper of the Lantern. Owes Kaelis a debt she will not name. Watches the door." },
  { name: "Brother Anselm", note: "Monastery archivist. Trades secrets for lamp oil." },
  { name: "Hooded Carter", note: "Mud to the knees, won't give a name. Drove the wagon's road — or claims he didn't." },
  { name: "Old Fen", note: "Lockpick tutor. Counts every lesson twice." },
];
const PLACES = [
  { name: "Ravenford", note: "Lantern Inn · guild post. Home lamplight." },
  { name: "Old Monastery", note: "Lights seen, unconfirmed. Back stair known to Sister Pell." },
  { name: "Wreck Hollow", note: "Destroyed wagon found here. Silver powder in the cracks." },
  { name: "Greyfen", note: "Rumoured smuggler landing. No confirmed map." },
];
const FACTIONS = [
  { name: "Merchant Guild", note: "Denies the wagon was theirs. Their wax says otherwise." },
  { name: "Smugglers", note: "Greyfen landings, monks' colours. Unconfirmed." },
  { name: "Monastery Order", note: "Bells at midnight — heard by one carter, denied by every monk." },
];
const LORE = [
  { name: "The Lantern-oath", note: "Kaelis carries her mother's unlit lantern. Lit only when the missing are found — or avenged." },
  { name: "Silver Powder", note: "Monastery business, alchemist's leavings, or something walked-in from elsewhere. Burns blue." },
];
const CHRONICLE = [
  { name: "Day 3", note: "Lit the Ford beacon." },
  { name: "Day 9", note: "Spared the smuggler's boy." },
  { name: "Day 12", note: "A guild wagon returned driverless. Marla showed the seal." },
];

const STATE_C = (s: string) =>
  s === "confirmed" ? "#e8c766" : s === "rumour" ? "#9a8a68" : "#8e2f2f";

export default function JournalPage() {
  const { content, addCost } = useStore();
  const [j, setJ] = useState<J>(fixtures.journal);
  const [live, setLive] = useState(false);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Story Leads");
  const [sel, setSel] = useState<Node | null>(null);

  useEffect(() => {
    api.journal(content).then((r) => { setJ(r.data as J); setLive(!r.fromFixture); addCost(r.cost); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const byId = new Map(j.nodes.map((x) => [x.id, x]));

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: 16 }}>
      <h1>Journal</h1>
      <p className="sys">{live ? "Kept current by the chronicler." : "Local pages (backend unreachable)."}</p>
      <div className="tabs" role="tablist" aria-label="Journal sections">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab${tab === t ? " active" : ""}`} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {tab === "Story Leads" && (
        <div className="grid2" style={{ marginTop: 12 }}>
          <div className="parchment card">
            <h2>Thread of the missing caravan</h2>
            <svg viewBox="0 0 560 270" role="img" aria-label="Lead graph: Missing Caravan to Destroyed Wagon to Silver Powder to Merchant Guild, branching to Smugglers and Old Monastery" style={{ width: "100%", height: "auto" }}>
              {j.edges.map(([a, b]) => {
                const A = byId.get(a as string); const B = byId.get(b as string);
                if (!A || !B) return null;
                return <line key={`${a}-${b}`} x1={A.x} y1={A.y} x2={B.x} y2={B.y} stroke="#7a5c3e" strokeWidth="2" strokeDasharray={A.state === "rumour" || B.state === "rumour" ? "5 4" : undefined} />;
              })}
              {j.nodes.map((x) => (
                <g key={x.id} onClick={() => setSel(x)} style={{ cursor: "pointer" }} role="button" tabIndex={0}
                   aria-label={`${x.label}, ${x.state}`}
                   onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setSel(x); }}>
                  <circle cx={x.x} cy={x.y} r={sel?.id === x.id ? 26 : 20} fill="#1d1712" stroke={STATE_C(x.state)} strokeWidth="2.5" />
                  <text x={x.x} y={x.y - 28} textAnchor="middle" fill="#e8dcc3" fontSize="12" fontFamily="Georgia,serif">{x.label}</text>
                  <text x={x.x} y={x.y + 5} textAnchor="middle" fill={STATE_C(x.state)} fontSize="11">{x.state === "confirmed" ? "◆" : x.state === "rumour" ? "◇" : "✦"}</text>
                </g>
              ))}
            </svg>
            <p className="sys">◆ confirmed · ◇ rumour · ✦ new — solid threads are walked, dashed ones guessed.</p>
          </div>
          <div className="parchment card">
            <h2>{sel ? sel.label : "The thread so far"}</h2>
            <p>{sel ? detailFor(sel.id, j.detail) : j.detail}</p>
            {sel && <p><span className="tag gold">{sel.state}</span></p>}
            {!sel && (
              <ul>{j.nodes.map((x) => <li key={x.id}><button className="entity" onClick={() => setSel(x)}>{x.label}</button> <span className="sys">· {x.state}</span></li>)}</ul>
            )}
            {sel && <button className="btn btn-ghost" onClick={() => setSel(null)}>Back to the thread</button>}
          </div>
        </div>
      )}

      {tab !== "Story Leads" && (
        <div className="grid2" style={{ marginTop: 12 }}>
          {(tab === "People" ? PEOPLE : tab === "Places" ? PLACES : tab === "Factions" ? FACTIONS : tab === "Lore" ? LORE : CHRONICLE).map((x) => (
            <div key={x.name} className="parchment card">
              <h3 style={{ marginTop: 0 }}>{x.name}</h3>
              <p style={{ marginBottom: 0 }}>{x.note}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function detailFor(id: string, fallback: string): string {
  const D: Record<string, string> = {
    caravan: "Three wagons left Greyfen under guild charter. Two arrived. The third's driver never came home — but the wagon did.",
    wagon: "Found in Wreck Hollow, axle split, canvas slashed from inside. Guild wax on the strongbox, cracked — pressed in haste, or opened and resealed.",
    powder: "Silver powder ground into the floorboards. Burns blue. The monastery denies all knowledge; the smugglers suddenly want to talk.",
    guild: "The Merchant Guild denies the wagon was theirs. Their seal says otherwise. Someone inside is lying, or someone outside is printing seals.",
    smugglers: "Greyfen landings by moonless water. They wear monks' colours when it suits them — or so one frightened carter swears.",
    monastery: "The old house above the treeline. Bells at midnight, lights where no lamp should be. Sister Pell knows the back stair and won't say how.",
  };
  return D[id] ?? fallback;
}
