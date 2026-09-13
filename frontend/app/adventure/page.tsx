"use client";
// Adventure screen: three-column layout (t_87bd1163), visual hierarchy (t_43e37b1e),
// optimistic ack + streaming + error recovery (t_18510814), tutorial (t_d2dd38a0),
// called-check throws (t_84c31095 follow-up): a surfaced check waits for the
// player's die — the prompt owns the throw, nothing rolls behind the player.
import { useEffect, useRef, useState } from "react";
import { api, streamNarration, submitAction, rollCheck, LAST_SAVE_KEY, getToken, ensureCampaign, setCampaignId, type LiveGameState, type ApiResult, type ActResponse, type PendingCheck } from "../../lib/api";
import { fixtures, type FeedEvent } from "../../lib/fixtures";
import { useStore } from "../../lib/store";
import { uiBlip } from "../../lib/audio";
import { Feed } from "../../components/feed";
import { SceneArt, Portrait } from "../../components/art";
import { CheckPrompt, type CheckPhase } from "../../components/checkprompt";
import { ErrorBanner, TutorialOverlay } from "../../components/widgets";
import { attitudeBand, attitudeBar, attitudeBarClass, formatAttitude } from "../../lib/relationship";
import { moodChip } from "../../lib/mood";

let n = 100;
const nid = () => `u${n++}`;

/** A called check waiting on the player's throw. */
interface PendingThrow {
  text: string;
  /** Idempotency stem: the resolve leg uses `${key}-roll`. */
  key: string;
  spec: PendingCheck;
  face: number | null;
  phase: CheckPhase;
}

export default function AdventurePage() {
  const { content, addCost, compactNarration, reducedMotion, hydrated } = useStore();
  const [loading, setLoading] = useState(true);
  const [restored, setRestored] = useState<string | null>(null);
  const [gs, setGs] = useState<LiveGameState>(fixtures.gameState);
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
  const [pending, setPending] = useState<PendingThrow | null>(null);
  const [suggestions, setSuggestions] = useState<{ label: string; command: string }[]>([]);
  const keyRef = useRef(0);
  const sendingRef = useRef(false); // one throw resolves once
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
  }, []);

  // The first fetch must carry the *stored* content prefs — wait for the
  // store to hydrate from localStorage, or the request races the hydration
  // effect and gates the state with defaults (chips faded, content leaking).
  useEffect(() => {
    if (!hydrated) return;
    async function loadState(): Promise<void> {
      const r = await api.gameState(content);
      if (r.fromFixture && getToken()) {
        // Signed in but no campaign yet (e.g. deep link): provision one, retry once.
        const cid = await ensureCampaign();
        if (cid) {
          const again = await api.gameState(content);
          if (again.data.campaign_id) setCampaignId(again.data.campaign_id);
          setGs(again.data); setEvents(again.data.feed); setLive(!again.fromFixture); addCost(again.cost);
          setLoading(false);
          return;
        }
      }
      if (r.data.campaign_id) setCampaignId(r.data.campaign_id);
      setGs(r.data); setEvents(r.data.feed); setLive(!r.fromFixture); addCost(r.cost);
      setLoading(false);
    }
    void loadState();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [events, streaming, ack]);

  function pushToast(lead: string) {
    setToasts((t) => [...t, lead]);
    uiBlip(880);
    setTimeout(() => setToasts((t) => t.slice(1)), 9000);
  }

  /** Fold one resolved response into the chronicle + panels (both legs share it). */
  function applyActResult(r: ApiResult<ActResponse>, text: string, opts?: { settledDice?: boolean }) {
    setAck(r.data.ack);
    setSuggestions(r.data.suggestions ?? []);
    // Mechanics resolve fast; narration streams into its slot.
    const pendingEvents: FeedEvent[] = [];
    for (const s of r.data.system ?? [])
      pendingEvents.push({ id: nid(), kind: "system", text: s });
    if (r.data.mechanics) {
      const diceId = nid();
      // A die the player just threw in the prompt shows settled here, not re-rolled.
      if (!opts?.settledDice) freshDiceRef.current.add(diceId);
      pendingEvents.push({ id: diceId, kind: "dice", roll: { label: r.data.mechanics.label, dice: r.data.mechanics.roll, total: r.data.mechanics.total, detail: r.data.mechanics.detail, d20: r.data.mechanics.d20, outcome: r.data.mechanics.outcome, dc: r.data.mechanics.dc } });
    }
    let full = "";
    setStreaming("");
    void (async () => {
      for await (const chunk of streamNarration(r.data.narration ?? "")) {
        full = chunk;
        if (!compactNarration) setStreaming(chunk);
      }
      setStreaming(null);
      pendingEvents.push({ id: nid(), kind: "narration", text: full || (r.data.narration ?? "") });
      for (const d of r.data.dialogue ?? [])
        pendingEvents.push({ id: nid(), kind: "dialogue", speaker: d.speaker, text: d.line });
      for (const l of r.data.newLeads ?? []) {
        pendingEvents.push({ id: nid(), kind: "lead", lead: l });
        pushToast(l);
      }
      setEvents((e) => [...e, { id: nid(), kind: "system", text: `❧ ${text}` }, ...pendingEvents]);
    })();
    // Refresh the surrounding panels (location, clock, NPCs, leads, sheet) from
    // the authoritative state — the local feed already shows what happened.
    api.gameState(content).then((r2) => {
      if (!r2.fromFixture) {
        if (r2.data.campaign_id) setCampaignId(r2.data.campaign_id);
        setGs(r2.data); addCost(r2.cost);
      }
    });
    if (text.toLowerCase().match(/road|hollow|forest|wreck|travel|leave|north/)) setScene("forest road");
    else if (text.toLowerCase().match(/monastery|chapel|beacon|monk/)) setScene("monastery");
  }

  async function runSubmit(text: string) {
    if (busy || pending) return;
    const key = `${Date.now()}-${keyRef.current++}`;
    setBusy(true); setError(null);
    setSuggestions([]);
    setAck(`You steel yourself — “${text}”`);
    try {
      const r = await submitAction(text, content, key);
      addCost(r.cost);
      if (r.data.pending_check) {
        // The engine has called a check and is holding the beat: the die waits
        // for the player's throw. Nothing has happened yet — nothing persists.
        setPending({ text, key, spec: r.data.pending_check, face: null, phase: "ready" });
        setAck(null);
        return;
      }
      applyActResult(r, text);
    } catch {
      // A killed call never loses game state: action + mechanics stand, narration retries.
      setError("The sending failed before the chronicler could answer.");
      setPreserved(text);
      setAck(null); setStreaming(null);
    } finally {
      setBusy(false);
    }
  }

  /** Send the settled face back to resolve the called check. */
  async function sendThrow(p: PendingThrow) {
    if (sendingRef.current || p.face == null) return;
    sendingRef.current = true;
    setPending({ ...p, phase: "sending" });
    try {
      const r = await rollCheck(p.text, p.face, p.spec.token, content, `${p.key}-roll`);
      if (r.conflict) {
        // check_expired: the board moved — the throw had no target, nothing was taken.
        setPending(null);
        setError("The moment moved on — that throw had no target, so nothing happened. Say it again.");
        return;
      }
      if (r.fromFixture) {
        // Thrown, but the sending failed: keep the die and offer the same face again.
        setPending({ ...p, phase: "retry" });
        return;
      }
      addCost(r.cost);
      setPending(null);
      applyActResult(r, p.text, { settledDice: true });
    } finally {
      sendingRef.current = false;
    }
  }

  /** Press on the die: pick the face (the settle is physics), then send it. */
  function onThrow() {
    const p = pending;
    if (!p || p.phase !== "ready") return;
    const face = 1 + Math.floor(Math.random() * 20);
    if (reducedMotion) {
      void sendThrow({ ...p, face });
      return;
    }
    setPending({ ...p, face, phase: "thrown" });
  }

  function onSettled() {
    if (pending?.phase === "thrown") void sendThrow(pending);
  }

  function onRetry() {
    if (pending?.phase === "retry") void sendThrow(pending);
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy || pending) return;
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      setError("You seem to be off the road (offline). Your words are kept below — retry when ready.");
      setPreserved(text);
      return;
    }
    setInput("");
    void runSubmit(text);
  }

  function inspect(name: string) {
    if (busy || pending) return;
    uiBlip(520);
    if (live) {
      // Live: inspecting is a real action — the engine decides what it reveals.
      void runSubmit(`I look closely at the ${name}`);
      return;
    }
    setEvents((e) => [...e, {
      id: nid(), kind: "system",
      text: gs.interactables.includes(name.toLowerCase()) || gs.npcs.some((x) => x.name === name) || gs.leads.includes(name)
        ? `❧ ${name} — noted in your journal.`
        : `❧ ${name} — the chronicler makes a note of it.`,
    }]);
  }

  const ch = gs.character ?? fixtures.character;

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
        {gs.completed && (
          <div className="level-toast" role="status">
            ❧ The tale of the missing travelers is told — the travelers walk free, and the chronicle marks this chapter complete. The road goes on.
          </div>
        )}
        <div className="feed-scroll">
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
        <div ref={bottomRef} />
        </div>
        {pending && (
          <CheckPrompt
            spec={pending.spec}
            phase={pending.phase}
            face={pending.face}
            onThrow={onThrow}
            onSettled={onSettled}
            onRetry={onRetry}
          />
        )}
        {!pending && suggestions.length > 0 && !busy && (
          <div className="suggest-row" aria-label="Suggested next moves">
            {suggestions.map((s) => (
              <button
                key={s.label}
                type="button"
                className="suggest-chip"
                onClick={() => { if (!busy && !pending) void runSubmit(s.command); }}
              >
                ✦ {s.label}
              </button>
            ))}
          </div>
        )}
        <form onSubmit={onSubmit} style={{ marginTop: 12 }}>
          <label className="sr-only" htmlFor="act">What do you do?</label>
          <input
            id="act"
            className="input-parch"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Attempt anything — ask Marla about the wagon, inspect the seal, step into the rain…"
            aria-label="action input"
            disabled={busy || !!pending}
            autoComplete="off"
          />
          <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
            <button className="btn" type="submit" disabled={busy || !!pending || !input.trim()}>
              {busy ? "The quill moves…" : pending ? "The die waits…" : "Act ↵"}
            </button>
            <span className="sys">No wrong verbs. No timed decisions.</span>
          </div>
        </form>
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
              <div key={n.name} style={{ margin: "6px 0" }}>
                <p style={{ margin: 0 }}>
                  <button className="entity" onClick={() => inspect(n.name)}>{n.name}</button>
                  <span className="sys"> — {n.note}</span>
                </p>
                <span className="att-row">
                  {typeof n.attitude === "number" && (
                    <RelationshipMeter name={n.name} attitude={n.attitude} band={n.band} />
                  )}
                  <MoodChip name={n.name} mood={n.mood} intensity={n.mood_intensity} />
                </span>
                {n.remembers && n.remembers.length > 0 && (
                  <span className="sys" style={{ display: "block", marginLeft: 10, opacity: 0.85 }}>
                    remembers: {n.remembers.join("; ")}
                  </span>
                )}
              </div>
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

/** Compact relationship meter for one present NPC: bar + band word + number.
 *  The backend's band word wins; the local ladder mirrors it as a fallback. */
function RelationshipMeter({ name, attitude, band }: { name: string; attitude: number; band?: string }) {
  const word = band ?? attitudeBand(attitude);
  const { left, width } = attitudeBar(attitude);
  // A 2px sliver vanishes against the track: any non-zero move keeps a floor.
  const visualWidth = width === 0 ? 0 : Math.max(width, 2.5);
  return (
    <span className="att-line" role="img" aria-label={`${name} — relationship ${word} (${attitude})`}>
      <span className={`bar bar-att ${attitudeBarClass(attitude)}`}>
        <i style={{ left: `${left}%`, width: `${visualWidth}%` }} />
      </span>
      <span className="sys">{word} {formatAttitude(attitude)}</span>
    </span>
  );
}

/** Mood chip for one present NPC: emoji + word, beside the relationship meter.
 *  A settled character (intensity 0, or nothing to show) renders no chip. */
function MoodChip({ name, mood, intensity }: { name: string; mood?: string; intensity?: number }) {
  const chip = moodChip(mood, intensity);
  if (!chip) return null;
  return (
    <span
      className={`mood-chip mood-chip-${chip.word}`}
      role="img"
      aria-label={`${name} — mood ${chip.label}`}
      title={`mood: ${chip.label}`}
    >
      <span aria-hidden="true">{chip.emoji}</span>
      <span className="mood-word">{chip.word}</span>
    </span>
  );
}
