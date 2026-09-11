"use client";
// Map screen (t_cedef501): fantasy travel map, intentionally approximate. Fixture fallback.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { fixtures } from "../../lib/fixtures";
import { useStore } from "../../lib/store";

type M = typeof fixtures.mapInfo;

export default function MapPage() {
  const { content, addCost } = useStore();
  const [m, setM] = useState<M>(fixtures.mapInfo);
  const [live, setLive] = useState(false);
  const [sel, setSel] = useState<string | null>(null);

  useEffect(() => {
    api.map(content).then((r) => { setM(r.data as M); setLive(!r.fromFixture); addCost(r.cost); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const picked = m.places.find((p) => p.name === sel);

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: 16 }}>
      <h1>Travel Map</h1>
      <p className="sys">
        {live ? "Surveyed this season." : "Copied from a carter's memory (backend unreachable)."}
        {" "}Drawn approximate on purpose — distances are guesses, and grey places are hearsay.
      </p>
      <div className="grid2">
        <div className="parchment card">
          <svg viewBox="0 0 560 340" role="img" aria-label="Fantasy travel map of the Ravenford country" style={{ width: "100%", height: "auto" }}>
            <defs>
              <filter id="rough"><feTurbulence baseFrequency="0.012" numOctaves="2" result="n" /><feDisplacementMap in="SourceGraphic" in2="n" scale="4" /></filter>
            </defs>
            <rect width="560" height="340" fill="#171208" />
            {/* compass rose */}
            <g transform="translate(508,40)" stroke="#c9a227" fill="none" opacity="0.9">
              <circle r="18" /><path d="M0,-18 L4,0 L0,18 L-4,0 Z" fill="#c9a227" stroke="none" />
              <text y="-24" textAnchor="middle" fill="#c9a227" stroke="none" fontSize="12" fontFamily="Georgia,serif">N</text>
            </g>
            {/* hills */}
            {[[90,90],[200,140],[330,220],[450,150],[150,290]].map(([x,y],i) => (
              <path key={i} d={`M${x-26},${y} Q${x-10},${y-22} ${x},${y-8} Q${x+12},${y-24} ${x+26},${y}`} stroke="#5c6e46" fill="none" strokeWidth="2" opacity="0.7" filter="url(#rough)" />
            ))}
            {/* river */}
            <path d="M40,330 C120,260 90,200 180,170 C270,140 300,80 420,40" stroke="#4a6a8a" fill="none" strokeWidth="5" opacity="0.65" filter="url(#rough)" />
            {/* roads */}
            {m.roads.map(([a,b],i) => {
              const A = m.places[a]; const B = m.places[b];
              if (!A || !B) return null;
              const unknown = !A.known || !B.known;
              return <line key={i} x1={A.x} y1={A.y} x2={B.x} y2={B.y} stroke="#7a5c3e" strokeWidth="2.5"
                strokeDasharray={unknown ? "4 6" : "10 4"} opacity={unknown ? 0.5 : 0.9} filter="url(#rough)" />;
            })}
            {/* places */}
            {m.places.map((p) => (
              <g key={p.name} onClick={() => setSel(p.name)} style={{ cursor: "pointer" }} role="button" tabIndex={0}
                 aria-label={`${p.name}${p.known ? "" : ", unconfirmed"}${p.danger > 0 ? `, danger ${p.danger} of 3` : ""}`}
                 onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setSel(p.name); }}>
                {p.danger > 0 && <circle cx={p.x} cy={p.y} r={20 + p.danger * 4} fill="none" stroke="#8e2f2f" strokeWidth="1.5" strokeDasharray="3 4" opacity="0.8" />}
                <circle cx={p.x} cy={p.y} r={sel === p.name ? 12 : 9}
                  fill={p.known ? "#2b241a" : "none"} stroke={p.known ? "#c9a227" : "#9a8a68"}
                  strokeWidth="2.5" strokeDasharray={p.known ? undefined : "4 3"} />
                {!p.known && <text x={p.x} y={p.y + 4} textAnchor="middle" fill="#9a8a68" fontSize="12">?</text>}
                <text x={p.x} y={p.y + 26} textAnchor="middle" fill={p.known ? "#e8dcc3" : "#9a8a68"} fontSize="13" fontStyle={p.known ? "normal" : "italic"} fontFamily="Georgia,serif">{p.name}</text>
              </g>
            ))}
            <text x="16" y="322" fill="#9a8a68" fontSize="11" fontStyle="italic" fontFamily="Georgia,serif">“Here the carter’s memory fails — leagues approximate.”</text>
          </svg>
          <p className="sys">— solid ring: walked ground · dashed: guessed road · red halo: danger · ? : hearsay.</p>
        </div>
        <div>
          <div className="parchment card">
            <h2>{picked ? picked.name : "The country"}</h2>
            <p>{picked ? picked.note : "Choose a place on the map. Grey places may not exist at all."}</p>
            {picked && picked.danger > 0 && <p><span className="tag red">danger {"◆".repeat(picked.danger)}{"◇".repeat(3 - picked.danger)}</span></p>}
            {picked && <button className="btn btn-ghost" onClick={() => setSel(null)}>Fold the map</button>}
          </div>
          <div className="parchment card" style={{ marginTop: 12 }}>
            <h3>Heard on the road</h3>
            <ul>{m.rumours.map((r) => <li key={r}>{r} <span className="sys">(unconfirmed)</span></li>)}</ul>
            <h3>Trouble abroad</h3>
            <ul>{m.incidents.map((r) => <li key={r}>{r}</li>)}</ul>
          </div>
        </div>
      </div>
    </div>
  );
}
