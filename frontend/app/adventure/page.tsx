"use client";
// Adventure screen: three-column layout (t_87bd1163), visual hierarchy (t_43e37b1e),
// optimistic ack + streaming + error recovery (t_18510814), tutorial (t_d2dd38a0).
import { useEffect, useRef, useState } from "react";
import { api, streamNarration, submitAction, LAST_SAVE_KEY } from "../../lib/api";
import { fixtures, type FeedEvent } from "../../lib/fixtures";
import { useStore } from "../../lib/store";
import { uiBlip } from "../../lib/audio";
import { Feed } from "../../components/feed";
import { SceneArt, Portrait } from "../../components/art";
import { ErrorBanner, TutorialOverlay } from "../../components/widgets";

type GS = typeof fixtures.gameState;

let n = 100;
const nid = () => `u${n++}`;

export default function AdventurePage() {
  const { content, addCost, compactNarration } = useStore();
  const [loading, setLoading] = useState(true);
  const [restored, setRestored] = useState<string | null>(null);
  const [gs, setGs] = useState<GS>(fixtures.gameState);
  const [live, setLive] = useState(false);
  const [events, setEvents] = useState<FeedEvent[]>(fixtures.gameState.feed);
  const [input, setInput] = useState("");
  const [ack, setAck] = useState<string | null>(null); // optimistic ack (t_18510814)
  const [streaming, setStreaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preserved, setPreserved] = useState(""); // failed input kept for retry
  const [toasts, setToasts] = useState<string[]>([]);
  const [scene, setScene] = useState("tavern interior");
  const keyRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  // Dice events created in this session roll their 3D animation on mount (t_ae0e86a6).
  const freshDiceRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    try {
      const raw = localStorage.getItem(LAST_SAVE_KEY);
      if (raw) {
        const s = JSON.parse(raw) as { label?: string };
        if (s?.label) setRestored(s.label);
      }
    } catch { /* no restored save */ }
    api.gameState(content).then((r) => {
      setGs(r.data); setEvents(r.data.feed); setLive(!r.fromFixture); addCost(r.cost);
      setLoading(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [events, streaming, ack]);

  function pushToast(lead: string) {
    setToasts((t) => [...t, lead]);
    uiBlip(880);
    setTimeout(() => setToasts((t) => t.slice(1)), 9000);
  }

  async function runSubmit(text: string) {
    const key = `${Date.now()}-${keyRef.current++}`;
    setBusy(true); setError(null);
    setAck(`You steel yourself — “${text}”`);
    try {
      const r = await submitAction(text, content, key);
      addCost(r.cost);
      setAck(r.data.ack);
      // Mechanics resolve fast; narration streams into its slot.
      const pending: FeedEvent[] = [];
      if (r.data.mechanics) {
        const diceId = nid();
        freshDiceRef.current.add(diceId);
        pending.push({ id: diceId, kind: "dice", roll: { label: r.data.mechanics.label, dice: r.data.mechanics.roll, total: r.data.mechanics.total, detail: r.data.mechanics.detail, d20: r.data.mechanics.d20, outcome: r.data.mechanics.outcome } });
      }
      let full = "";
      setStreaming("");
      for await (const chunk of streamNarration(r.data.narration)) {
        full = chunk;
        if (!compactNarration) setStreaming(chunk);
      }
      setStreaming(null);
      pending.push({ id: nid(), kind: "narration", text: full || r.data.narration });
      for (const d of r.data.dialogue ?? [])
        pending.push({ id: nid(), kind: "dialogue", speaker: d.speaker, text: d.line });
      for (const l of r.data.newLeads ?? []) {
        pending.push({ id: nid(), kind: "lead", lead: l });
        pushToast(l);
      }
      setEvents((e) => [...e, { id: nid(), kind: "system", text: `❧ ${text}` }, ...pending]);
      setAck(null);
      if (text.toLowerCase().match(/road|hollow|forest|wreck|travel|leave|north/)) setScene("forest road");
      else if (text.toLowerCase().match(/monastery|chapel|beacon|monk/)) setScene("monastery");
    } catch {
      // A killed call never loses game state: action + mechanics stand, narration retries.
      setError("The sending failed before the chronicler could answer.");
      setPreserved(text);
      setAck(null); setStreaming(null);
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      setError("You seem to be off the road (offline). Your words are kept below — retry when ready.");
      setPreserved(text);
      return;
    }
    setInput("");
    void runSubmit(text);
  }

  function inspect(name: string) {
    uiBlip(520);
    setEvents((e) => [...e, {
      id: nid(), kind: "system",
      text: gs.interactables.includes(name.toLowerCase()) || gs.npcs.some((x) => x.name === name) || gs.leads.includes(name)
        ? `❧ ${name} — noted in your journal.`
        : `❧ ${name} — the chronicler makes a note of it.`,
    }]);
  }

  const ch = fixtures.character;

  return (
    <div className="shell">
      {/* LEFT — status */}
      <aside className="left" aria-label="Party status" style={{ borderRight: "1px solid var(--line)" }}>
        <div className="parchment card" style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <Portrait name={ch.name} hue={ch.portraitHue} />
          <div>
            <h2 style={{ margin: 0 }}>{ch.name}</h2>
            <p className="sys" style={{ margin: 0 }}>{ch.epithet} · Level {ch.level}</p>
          </div>
        </div>
        <div className="parchment card" style={{ marginTop: 12 }}>
          <Bar label={`Health ${ch.hp.cur}/${ch.hp.max}`} pct={(ch.hp.cur / ch.hp.max) * 100} cls="bar-hp" />
          <Bar label={`Stamina ${ch.stamina.cur}/${ch.stamina.max}`} pct={(ch.stamina.cur / ch.stamina.max) * 100} cls="bar-st" />
          <Bar label={`Resolve ${ch.resolve.cur}/${ch.resolve.max}`} pct={(ch.resolve.cur / ch.resolve.max) * 100} cls="bar-rs" />
          <p style={{ margin: "8px 0 0" }}>
            {ch.conditions.map((c) => <span className="tag red" key={c}>{c}</span>)}
          </p>
          {!live && <p className="sys">Reading from local pages (backend unreachable).</p>}
        </div>
        <div className="parchment card" style={{ marginTop: 12 }}>
          <h3>Party</h3>
          {gs.party.map((p) => <p key={p.name} style={{ margin: "4px 0" }}>{p.name} <span className="sys">· {p.hp}</span></p>)}
        </div>
      </aside>

      {/* CENTER — chronicle */}
      <section aria-label="Chronicle" style={{ padding: 14, minWidth: 0 }}>
        <SceneArt scene={scene} label={scene === "tavern interior" ? "The Lantern Inn — hearthlight and rain" : scene} />
        {restored && (
          <div className="level-toast" role="status">
            ❧ Restored snapshot — {restored}
            <button className="btn btn-ghost" style={{ marginLeft: 10, padding: "2px 8px" }} onClick={() => { try { localStorage.removeItem(LAST_SAVE_KEY); } catch { /* */ } setRestored(null); }}>Dismiss</button>
          </div>
        )}
        {loading && (
          <div aria-label="Loading chronicle" role="status">
            <div className="skel" style={{ height: 120 }} />
            <div className="skel" style={{ height: 60, marginTop: 8 }} />
            <p className="sys">Unrolling the chronicle…</p>
          </div>
        )}
        {!loading && <Feed events={events} streaming={streaming} onInspect={inspect} freshDice={freshDiceRef.current} />}
        {ack && <p className="ack" role="status">{ack}</p>}
        {error && (
          <ErrorBanner
            message={error}
            onRetry={() => { const t = preserved; setPreserved(""); setError(null); if (t) void runSubmit(t); }}
          />
        )}
        {preserved && error && <p className="sys">Kept: “{preserved}”</p>}
        <form onSubmit={onSubmit} style={{ marginTop: 12 }}>
          <label className="sr-only" htmlFor="act">What do you do?</label>
          <input
            id="act"
            className="input-parch"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Attempt anything — ask Marla about the wagon, inspect the seal, step into the rain…"
            aria-label="action input"
            disabled={busy}
            autoComplete="off"
          />
          <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
            <button className="btn" type="submit" disabled={busy || !input.trim()}>
              {busy ? "The quill moves…" : "Act ↵"}
            </button>
            <span className="sys">No wrong verbs. No timed decisions.</span>
          </div>
        </form>
        <div ref={bottomRef} />
        <div className="toast-stack" aria-live="polite">
          {toasts.map((t, i) => (
            <div className="lead-toast" key={`${i}-${t}`} role="status">
              ✦ New lead — {t}
              <button className="btn btn-ghost" style={{ marginLeft: 10, padding: "2px 8px" }} onClick={() => setToasts((x) => x.filter((_, j) => j !== i))} aria-label={`Dismiss lead ${t}`}>✕</button>
            </div>
          ))}
        </div>
      </section>

      {/* RIGHT — world */}
      <aside className="right" aria-label="Surroundings" style={{ borderLeft: "1px solid var(--line)" }}>
        <div className="rgrid">
          <div className="parchment card">
            <h3>📍 {gs.location}</h3>
            <p className="sys">🕰 {gs.time}</p>
            <SaveIndicator />
          </div>
          <div className="parchment card">
            <h3>Present</h3>
            {gs.npcs.map((n) => (
              <p key={n.name} style={{ margin: "4px 0" }}>
                <button className="entity" onClick={() => inspect(n.name)}>{n.name}</button>
                <span className="sys"> — {n.note}</span>
              </p>
            ))}
          </div>
          <div className="parchment card">
            <h3>Leads</h3>
            {gs.leads.map((l) => <p key={l} style={{ margin: "4px 0" }}>✦ <button className="entity" onClick={() => inspect(l)}>{l}</button></p>)}
          </div>
          <div className="parchment card">
            <h3>At hand</h3>
            <p>{gs.interactables.map((x, i) => (
              <span key={x}><button className="entity" onClick={() => inspect(x)}>{x}</button>{i < gs.interactables.length - 1 ? " · " : ""}</span>
            ))}</p>
          </div>
        </div>
      </aside>
      <TutorialOverlay />
    </div>
  );
}

function SaveIndicator() {
  const [label, setLabel] = useState<string | null>(null);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(LAST_SAVE_KEY);
      if (raw) {
        const s = JSON.parse(raw) as { label?: string; at?: string };
        setLabel(s?.label ? `${s.label}${s.at ? ` · ${new Date(s.at).toLocaleString()}` : ""}` : null);
      }
    } catch { /* no save yet */ }
  }, []);
  return (
    <p className="sys" role="status" style={{ margin: "6px 0 0" }}>
      {label ? `◈ Saved — ${label}` : "◈ No save yet this journey"}
    </p>
  );
}

function Bar({ label, pct, cls }: { label: string; pct: number; cls: string }) {
  return (
    <div style={{ margin: "6px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.9em" }}>
        <span>{label}</span>
      </div>
      <div className={`bar ${cls}`} role="img" aria-label={label}><i style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} /></div>
    </div>
  );
}
