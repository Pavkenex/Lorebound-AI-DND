"use client";
// Adventure screen: three-column layout (t_87bd1163), visual hierarchy (t_43e37b1e),
// optimistic ack + streaming + error recovery (t_18510814), tutorial (t_d2dd38a0),
// called-check throws (t_84c31095 follow-up): a surfaced check waits for the
// player's die — the prompt owns the throw, nothing rolls behind the player.
import { useEffect, useRef, useState } from "react";
import { api, aiSettingsApi, newActionKey, openChronicle, streamNarration, submitAction, rollCheck, LAST_SAVE_KEY, getToken, ensureCampaign, setCampaignId, type LiveGameState, type AiSettingsDoc, type ActResponse, type PendingCheck, type NpcEntry, type NpcDetail } from "../../lib/api";
import { aiConnected, aiConnectionLine, aiNotConnectedReason, aiPlayGate, actFailureText } from "../../lib/ai";
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
  /** Advantage's second face (Inspiration): thrown after the first settles. */
  face2: number | null;
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
  /** Failed input kept for retry — WITH its idempotency key, so the retry is a
   *  replay of the same action, not a second application of it (P12). */
  const [preserved, setPreserved] = useState<{ text: string; key: string } | null>(null);
  /** The account's AI connection (P12): what /act will narrate with, or why
   *  there is nothing to narrate with. null while unknown. */
  const [ai, setAi] = useState<AiSettingsDoc | null>(null);
  /** The backend itself refused a turn with `connect_your_ai`: the gate closes
   *  no matter what the last settings fetch said. */
  const [connectNeeded, setConnectNeeded] = useState(false);
  const [toasts, setToasts] = useState<string[]>([]);
  const [scene, setScene] = useState("tavern interior");
  const [pending, setPending] = useState<PendingThrow | null>(null);
  const [suggestions, setSuggestions] = useState<{ label: string; command: string }[]>([]);
  const [npcView, setNpcView] = useState<NpcEntry | null>(null); // the open character page
  const [npcInfo, setNpcInfo] = useState<NpcDetail | null>(null); // its deeper fetch, when live
  /** Mobile pane: one thumb-reach tab at a time (Chronicle / Status / World). */
  const [tab, setTab] = useState<"tale" | "hero" | "world">("tale");
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

  // The chronicle's first page (P14): the seed carries no prose at all, so a
  // new journey asks the narrator for its opening once the state says it is
  // pending — and only once a model is connected (the act gate already says
  // why when there is none: no model, no narration).
  const [openingAsked, setOpeningAsked] = useState(false);
  useEffect(() => {
    if (!hydrated || openingAsked || !gs.opening_pending) return;
    if (!ai?.connected) return;
    setOpeningAsked(true);
    void (async () => {
      const o = await openChronicle(content);
      if (!o.ok || o.data.already) return;
      addCost(o.cost);
      const fresh: FeedEvent[] = [];
      if (o.data.narration)
        fresh.push({ id: nid(), kind: "narration", text: o.data.narration });
      for (const d of o.data.dialogue ?? [])
        fresh.push({ id: nid(), kind: "dialogue", speaker: d.speaker, text: d.line });
      if (!fresh.length) return;
      setEvents((e) => (e.length ? [...e, ...fresh] : fresh));
      setGs((g) => ({ ...g, opening_pending: false }));
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated, ai, gs.opening_pending, openingAsked]);

  // The account's AI connection (P12): the SAME resolution /act uses, so this
  // page can say "connect your AI" before a turn is wasted — and can name the
  // model that will answer. Re-read once the live state lands.
  useEffect(() => {
    if (!hydrated || !getToken()) return;
    void aiSettingsApi().then((r) => {
      if (r.ok) setAi(r.doc);
    });
  }, [hydrated, live]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [events, streaming, ack]);

  function pushToast(lead: string) {
    setToasts((t) => [...t, lead]);
    uiBlip(880);
    setTimeout(() => setToasts((t) => t.slice(1)), 9000);
  }

  /** Fold one resolved response into the chronicle + panels (both legs share it). */
  function applyActResult(data: ActResponse, text: string, opts?: { settledDice?: boolean }) {
    setAck(data.ack);
    setSuggestions(data.suggestions ?? []);
    // Mechanics resolve fast; narration streams into its slot.
    const pendingEvents: FeedEvent[] = [];
    for (const s of data.system ?? [])
      pendingEvents.push({ id: nid(), kind: "system", text: s });
    if (data.mechanics) {
      const diceId = nid();
      // A die the player just threw in the prompt shows settled here, not re-rolled.
      if (!opts?.settledDice) freshDiceRef.current.add(diceId);
      pendingEvents.push({ id: diceId, kind: "dice", roll: { label: data.mechanics.label, dice: data.mechanics.roll, total: data.mechanics.total, detail: data.mechanics.detail, d20: data.mechanics.d20, outcome: data.mechanics.outcome, dc: data.mechanics.dc, d20_second: data.mechanics.d20_second, advantage: data.mechanics.advantage } });
    }
    let full = "";
    setStreaming("");
    void (async () => {
      for await (const chunk of streamNarration(data.narration ?? "")) {
        full = chunk;
        if (!compactNarration) setStreaming(chunk);
      }
      setStreaming(null);
      pendingEvents.push({ id: nid(), kind: "narration", text: full || (data.narration ?? "") });
      for (const d of data.dialogue ?? [])
        pendingEvents.push({ id: nid(), kind: "dialogue", speaker: d.speaker, text: d.line });
      for (const l of data.newLeads ?? []) {
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

  /** Re-read the connection: a refusal means the last fetch is stale. */
  function refreshAi() {
    if (!getToken()) return;
    void aiSettingsApi().then((r) => {
      if (r.ok) setAi(r.doc);
    });
  }

  /** A failed (or refused) turn: say what happened, keep the words and their
   *  key. Nothing is invented and nothing is lost (P12). */
  function onActFailure(code: string, message: string, text: string, key: string) {
    if (code === "connect_your_ai") {
      setConnectNeeded(true);
      refreshAi();
      setInput((cur) => cur || text);
      setPreserved({ text, key });
      setAck(null); setStreaming(null);
      return;
    }
    setError(actFailureText(code, message));
    setPreserved({ text, key });
    setAck(null); setStreaming(null);
  }

  async function runSubmit(text: string, reuseKey?: string) {
    if (busy || pending) return;
    if (gate.blocked) {
      // Defence in depth for the chip/inspiration/retry paths (P12).
      if (gate.kind === "connect") setConnectNeeded(true);
      return;
    }
    // A deliberate retry resends the SAME key (a replay); a new action gets one.
    const key = reuseKey ?? newActionKey();
    setBusy(true); setError(null);
    setSuggestions([]);
    setAck(`You steel yourself — “${text}”`);
    try {
      const r = await submitAction(text, content, key);
      if (!r.ok) {
        onActFailure(r.code, r.message, text, key);
        return;
      }
      addCost(r.cost);
      if (r.data.pending_check) {
        // The engine has called a check and is holding the beat: the die waits
        // for the player's throw. Nothing has happened yet — nothing persists.
        setPending({ text, key, spec: r.data.pending_check, face: null, face2: null, phase: "ready" });
        setAck(null);
        return;
      }
      applyActResult(r.data, text);
    } finally {
      setBusy(false);
    }
  }

  /** Send the settled face(s) back to resolve the called check. */
  async function sendThrow(p: PendingThrow) {
    if (sendingRef.current || p.face == null) return;
    sendingRef.current = true;
    setPending({ ...p, phase: "sending" });
    try {
      const r = await rollCheck(p.text, p.face, p.spec.token, content, `${p.key}-roll`, p.face2 ?? undefined);
      if (!r.ok) {
        if (r.code === "check_expired") {
          // The board moved — the throw had no target, nothing was taken.
          setPending(null);
          setError(actFailureText(r.code, r.message));
          return;
        }
        // Thrown, but the sending failed: keep the die and offer the same face again.
        setPending({ ...p, phase: "retry" });
        if (r.code === "connect_your_ai") {
          setConnectNeeded(true);
          refreshAi();
        } else {
          setError(actFailureText(r.code, r.message));
        }
        return;
      }
      addCost(r.cost);
      setPending(null);
      applyActResult(r.data, p.text, { settledDice: true });
    } finally {
      sendingRef.current = false;
    }
  }

  /** Press on the die: pick the face (the settle is physics), then send it. */
  function onThrow() {
    const p = pending;
    if (!p || p.phase !== "ready") return;
    const face = 1 + Math.floor(Math.random() * 20);
    const face2 = p.spec.advantage === true ? 1 + Math.floor(Math.random() * 20) : null;
    if (reducedMotion) {
      void sendThrow({ ...p, face, face2 });
      return;
    }
    setPending({ ...p, face, face2, phase: "thrown" });
  }

  function onSettled() {
    // Advantage: the first settle births the second die; the second settle sends both.
    if (pending?.phase === "thrown" && pending.spec.advantage === true && pending.face2 != null) {
      setPending({ ...pending, phase: "thrown2" });
      return;
    }
    if (pending?.phase === "thrown" || pending?.phase === "thrown2") void sendThrow(pending);
  }

  function onRetry() {
    if (pending?.phase === "retry") void sendThrow(pending);
  }

  // A called check must be throwable with one thumb: bring the Chronicle pane up.
  useEffect(() => { if (pending) setTab("tale"); }, [pending]);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy || pending) return;
    if (gate.blocked) {
      // No turn happens without a model (P12) — the notice above says why.
      if (gate.kind === "connect") setConnectNeeded(true);
      return;
    }
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      setError("You seem to be off the road (offline). Your words are kept below — retry when ready.");
      setPreserved({ text, key: newActionKey() });
      return;
    }
    setInput("");
    void runSubmit(text);
  }

  /** Open one present character's page; live fetches the deeper detail. */
  function openNpc(entry: NpcEntry) {
    uiBlip(560);
    setNpcView(entry);
    setNpcInfo(null);
    if (live && entry.slug) {
      void api.npcDetail(entry.slug, content).then((r) => {
        if (!r.fromFixture && r.data) setNpcInfo(r.data);
      });
    }
  }

  function inspect(name: string) {
    if (busy || pending) return;
    uiBlip(520);
    if (live) {
      // Live: inspecting is a real action — the engine decides what it reveals.
      // Proper nouns take no article: "look at Marla", not "at the Marla".
      const target = /^[A-Z]/.test(name) || name.toLowerCase().startsWith("the ")
        ? name
        : `the ${name}`;
      void runSubmit(`I look closely at ${target}`);
      return;
    }
    // Demo pages: local notes only — nothing here pretends the world answered.
    setEvents((e) => [...e, { id: nid(), kind: "system", text: `❧ (demo) ${name} — noted in your journal.` }]);
  }

  const ch = gs.character ?? fixtures.character;
  // The play gate (P12): signed out = the sample pages (read, don't play);
  // nothing connected = the connect notice; backend silent = offline.
  const signedIn = Boolean(getToken());
  const gate = aiPlayGate({ signedIn, live, doc: ai, connectNeeded });

  return (
    <div className="shell" data-tab={tab}>
      <nav className="tabbar" aria-label="Adventure panes">
        {([["tale", "❧ Chronicle"], ["hero", "♥ Status"], ["world", "◈ World"]] as const).map(([v, label]) => (
          <button
            key={v}
            type="button"
            className={`tab${tab === v ? " on" : ""}`}
            aria-pressed={tab === v}
            onClick={() => setTab(v)}
          >
            {label}
          </button>
        ))}
      </nav>
      {/* LEFT — status */}
      <aside className="left" aria-label="Party status" style={{ borderRight: "1px solid var(--line)" }}>
        <div className="parchment card" style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <Portrait name={ch.name} hue={ch.portraitHue} />
          <div>
            <h2 style={{ margin: 0 }}>{ch.name}</h2>
            <p className="sys" style={{ margin: 0 }}>{ch.epithet} · Level {ch.level}</p>
            {typeof gs.inspiration === "number" && gs.inspiration > 0 ? (
              <p className="sys" style={{ margin: "2px 0 0" }} title="Burn one for advantage on your next roll — the Act row, or say so.">
                ✦ Inspiration ×{gs.inspiration}
              </p>
            ) : null}
            {gs.inspired ? <p className="sys" style={{ margin: "2px 0 0" }}>✦ burning — the next roll throws twice</p> : null}
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
          {live && aiConnected(ai) && (
            <p className="sys" style={{ margin: "8px 0 0" }} data-ai="connected">
              ✎ Narrated by {aiConnectionLine(ai)}
            </p>
          )}
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
        {!loading && gate.blocked && (
          <div className="parchment card" role="status" data-gate={gate.kind} aria-label="Play gate">
            <p style={{ margin: 0 }}>✋ {gate.notice}</p>
            {gate.kind === "connect" && (
              <p className="sys" style={{ margin: "6px 0 0" }}>
                Nothing is connected because {aiNotConnectedReason(ai)}{" "}
                <a className="entity" href="/settings">Settings → Tale-spinner (AI)</a>
              </p>
            )}
            {gate.kind === "demo" && (
              <p className="sys" style={{ margin: "6px 0 0" }}>
                These pages are a sample — <a className="entity" href="/settings">sign in</a> to
                start a tale of your own.
              </p>
            )}
            {gate.kind === "offline" && (
              <button className="btn btn-ghost" type="button" style={{ marginTop: 6 }} onClick={() => window.location.reload()}>
                Retry
              </button>
            )}
          </div>
        )}
        {error && (
          <ErrorBanner
            message={error}
            onRetry={() => {
              // Retry resends the SAME words with the SAME idempotency key: a
              // replay, not a second application (P12).
              const p = preserved;
              setPreserved(null); setError(null);
              if (p) void runSubmit(p.text, p.key);
            }}
          />
        )}
        {preserved && error && <p className="sys">Kept: “{preserved.text}”</p>}
        <div ref={bottomRef} />
        </div>
        {pending && (
          <CheckPrompt
            spec={pending.spec}
            phase={pending.phase}
            face={pending.face}
            face2={pending.face2}
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
            placeholder={
              gs.scene?.id === "road-to-ravenford" // the prologue (§intro)
                ? "Attempt anything — look over the town, listen to the rain, head down to the Lantern…"
                : "Attempt anything — ask Marla about the wagon, inspect the seal, step into the rain…"
            }
            aria-label="action input"
            disabled={busy || !!pending || gate.blocked}
            autoComplete="off"
          />
          <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
            <button className="btn" type="submit" disabled={busy || !!pending || gate.blocked || !input.trim()}>
              {busy ? "The quill moves…" : pending ? "The die waits…" : gate.blocked ? "Not connected" : "Act ↵"}
            </button>
            {typeof gs.inspiration === "number" && gs.inspiration > 0 && !pending && !gate.blocked && (
              <button
                className="btn btn-ghost"
                type="button"
                disabled={busy}
                title="Burn one Inspiration: your next roll throws twice, keeping the higher."
                onClick={() => { if (!busy && !pending) void runSubmit("I spend my inspiration"); }}
              >
                ✦ Spend ({gs.inspiration})
              </button>
            )}
            <span className="sys">
              {gate.blocked ? "The chronicle waits for a connected model." : "No wrong verbs. No timed decisions."}
            </span>
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
                  <button className="entity" onClick={() => openNpc(n)}>{n.name}</button>
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
      {npcView && (
        <NpcSheet
          entry={npcView}
          detail={npcInfo && npcInfo.slug === npcView.slug ? npcInfo : null}
          onClose={() => setNpcView(null)}
          onLook={() => {
            const name = npcView.name;
            setNpcView(null);
            inspect(name);
          }}
        />
      )}
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

/** One present character's page: meter, mood, and what they remember about
 *  you. Opens from the Present list; `detail` enriches the panel's own entry
 *  with the deeper memory list once the live fetch lands. */
function NpcSheet({ entry, detail, onClose, onLook }: {
  entry: NpcEntry;
  detail: NpcDetail | null;
  onClose: () => void;
  onLook: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const attitude = detail?.attitude ?? entry.attitude;
  const band = detail?.band ?? entry.band;
  const mood = detail?.mood ?? entry.mood;
  const intensity = detail?.mood_intensity ?? entry.mood_intensity;
  const remembers = detail?.remembers ?? entry.remembers ?? [];
  const chip = moodChip(mood, intensity);
  // A stable hue per name keeps the procedural portrait steady between opens.
  const hue = (Array.from(entry.name).reduce((a, c) => a + c.charCodeAt(0), 0) * 37) % 360;
  return (
    <div
      className="tut-veil"
      role="dialog"
      aria-modal="true"
      aria-label={`${entry.name} — character`}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="parchment npc-sheet">
        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          <Portrait name={entry.name} hue={hue} size={72} />
          <div style={{ minWidth: 0 }}>
            <p className="sys" style={{ margin: 0 }}>CHARACTER{band ? ` · ${band}` : ""}</p>
            <h2 style={{ margin: "2px 0" }}>{entry.name}</h2>
            {entry.note && <p className="sys" style={{ margin: 0 }}>{entry.note}</p>}
          </div>
        </div>
        {typeof attitude === "number" && (
          <div style={{ marginTop: 14 }}>
            <p className="sys" style={{ margin: "0 0 2px" }}>RELATIONSHIP</p>
            <RelationshipMeter name={entry.name} attitude={attitude} band={band} />
          </div>
        )}
        <div style={{ marginTop: 10 }}>
          <p className="sys" style={{ margin: "0 0 2px" }}>MOOD</p>
          {chip ? (
            <MoodChip name={entry.name} mood={mood} intensity={intensity} />
          ) : (
            <span className="sys">settled — nothing wearing on them</span>
          )}
        </div>
        <div style={{ marginTop: 10 }}>
          <p className="sys" style={{ margin: "0 0 2px" }}>REMEMBERS ABOUT YOU</p>
          {remembers.length > 0 ? (
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {remembers.map((m) => <li key={m}>{m}</li>)}
            </ul>
          ) : (
            <span className="sys">Nothing worth carrying — yet.</span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <button className="btn" onClick={onLook}>👁 Look them over</button>
          <button className="btn btn-ghost" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
