"use client";
// Pilot: /chronicle — the rebuilt engine behind NEXT_PUBLIC_ENGINE_MODE
// (docs/INTEGRATION_PLAN.md §7). Hidden + inert unless the deploy sets the flag:
// the page renders a notice, the nav keeps no link, and nothing calls /engine/*.
//
// Runtime-key rule (§4): the BYOK key is kept in THIS browser's localStorage,
// typed into a password field, sent per request as X-Provider-Key, and never
// stored server-side. The connect panel only ever PUTs non-secret prefs.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  engineApi,
  getToken,
  type EngineCampaign,
  type EngineConnection,
  type EngineProbe,
} from "../../lib/api";
import {
  CONNECT_NOTICE,
  ENGINE_DEFAULT_BASE_URL,
  ENGINE_DEFAULT_TIMEOUT_S,
  appliedContent,
  boundariesNotice,
  boundariesSummary,
  capabilityChip,
  clearEngineKey,
  connectionLabel,
  engineDegraded,
  engineModeEnabled,
  engineStateSections,
  formatWhen,
  getEngineCampaignId,
  getEngineKey,
  keyAfterConnectionSave,
  playGate,
  probeVerdictText,
  providerChoice,
  providerConnected,
  setEngineCampaignId,
  setEngineKey,
  turnEntries,
  turnSuggestions,
  type AppliedContent,
  type EngineCapability,
  type EngineProviderChoice,
  type EngineState,
  type EngineTurn,
  type ProbeVerdict,
  type TranscriptEntry,
} from "../../lib/engine";
import { useStore } from "../../lib/store";
import { uiBlip } from "../../lib/audio";

export default function ChroniclePage() {
  if (!engineModeEnabled()) return <PilotOff />;
  return <EnginePilot />;
}

/** Flag off: the route exists but is inert — no fetches, no key field, no link. */
function PilotOff() {
  return (
    <div style={{ maxWidth: 620, margin: "0 auto", padding: 24 }}>
      <h1>The chronicle pilot is off</h1>
      <p className="sys">
        This deployment has not switched the rebuilt engine on
        (<code>NEXT_PUBLIC_ENGINE_MODE</code>). Nothing else changed — the rest of
        the game is exactly as it was.
      </p>
      <p><Link className="btn btn-ghost" href="/" prefetch>Back to the menu</Link></p>
    </div>
  );
}

function SignedOutCard() {
  return (
    <div className="parchment card" style={{ maxWidth: 560 }}>
      <h2 style={{ marginTop: 0 }}>Sign in to open the pilot</h2>
      <p className="sys">
        Pilot chronicles belong to your account — the same one that keeps your
        saves. Your AI key, when you set one, stays in this browser.
      </p>
      <Link className="btn" href="/login" prefetch>Sign in</Link>
      <p className="sys" style={{ marginBottom: 0 }}>
        After signing in, open <strong>Chronicle</strong> again from the top bar.
      </p>
    </div>
  );
}

function EnginePilot() {
  // Content boundaries are read from the store, LIVE: the header itself is
  // read from the store's persisted slot at request time (lib/engine.ts), and
  // this value only decides what the boundaries line claims pre-turn.
  const { content: storedContent, hydrated } = useStore();
  // null = not yet read from localStorage (SSR render must not guess).
  const [signedIn, setSignedIn] = useState<boolean | null>(null);

  const [campaigns, setCampaigns] = useState<EngineCampaign[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [capability, setCapability] = useState<EngineCapability | null>(null);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preserved, setPreserved] = useState("");
  const [listError, setListError] = useState<string | null>(null);

  const [snapshot, setSnapshot] = useState<EngineState | null>(null);
  const [stateError, setStateError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  /** Applied-boundaries echo of the last turn (§11); null until one arrives. */
  const [applied, setApplied] = useState<AppliedContent | null>(null);
  /** The turn's own "defaults were applied" note, when it sent one. */
  const [boundariesNote, setBoundariesNote] = useState<string | null>(null);

  const [connection, setConnection] = useState<EngineConnection | null>(null);
  const [provider, setProvider] = useState<EngineProviderChoice>("openai-compatible");
  const [baseUrl, setBaseUrl] = useState(ENGINE_DEFAULT_BASE_URL);
  const [model, setModel] = useState("");
  const [timeoutS, setTimeoutS] = useState(ENGINE_DEFAULT_TIMEOUT_S);
  const [keyInput, setKeyInput] = useState("");
  const [keyStored, setKeyStored] = useState(false);
  /** A server turn answered `connect_your_ai`: the gate stays closed until a
   *  connection is saved (P11 — never a silent keyless turn). */
  const [connectNeeded, setConnectNeeded] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const [connBusy, setConnBusy] = useState(false);
  const [connStatus, setConnStatus] = useState<ProbeVerdict | null>(null);
  const [probe, setProbe] = useState<EngineProbe | null>(null);

  const seqRef = useRef(0);
  const connectRef = useRef<HTMLElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const openConnectPanel = useCallback(() => {
    setConnectOpen(true);
    // The panel may sit below the fold on a phone; bring it into view.
    window.setTimeout(() => connectRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 30);
  }, []);

  const loadState = useCallback(async (campaignId: string) => {
    setRefreshing(true); setStateError(null);
    const r = await engineApi.state(campaignId);
    setRefreshing(false);
    if (r.ok) setSnapshot(r.data as EngineState);
    else setStateError(r.error);
  }, []);

  const openCampaign = useCallback((campaignId: string) => {
    setSelectedId(campaignId);
    setEngineCampaignId(campaignId);
    setTranscript([]);
    setSuggestions([]);
    setCapability(null);
    setSnapshot(null);
    setError(null);
    setPreserved("");
    setInput("");
    setApplied(null);
    setBoundariesNote(null);
    void loadState(campaignId);
  }, [loadState]);

  const loadConnection = useCallback(async () => {
    const r = await engineApi.getConnection();
    if (!r.ok) { setConnStatus({ tone: "warn", text: r.error }); return; }
    setConnection(r.data);
    setProvider(providerChoice(r.data.provider));
    setBaseUrl((r.data.base_url ?? "").trim() || ENGINE_DEFAULT_BASE_URL);
    setModel(r.data.model ?? "");
    setTimeoutS(r.data.timeout_s || ENGINE_DEFAULT_TIMEOUT_S);
  }, []);

  const loadCampaigns = useCallback(async () => {
    const r = await engineApi.listCampaigns();
    if (!r.ok) { setListError(r.error); setCampaigns([]); return; }
    setListError(null);
    setCampaigns(r.data);
    const remembered = getEngineCampaignId();
    const match = r.data.find((c) => c.id === remembered) ?? r.data[0];
    if (match) openCampaign(match.id);
  }, [openCampaign]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setKeyStored(!!getEngineKey());
    if (!getToken()) { setSignedIn(false); return; }
    setSignedIn(true);
    void loadConnection();
    void loadCampaigns();
  }, [loadConnection, loadCampaigns]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [transcript.length, pending, error]);

  async function newChronicle() {
    setCreating(true); setListError(null);
    const r = await engineApi.createCampaign(newName.trim());
    setCreating(false);
    if (!r.ok) { setListError(r.error); return; }
    uiBlip(740);
    setNewName("");
    setCampaigns((c) => [r.data, ...(c ?? []).filter((x) => x.id !== r.data.id)]);
    openCampaign(r.data.id);
  }

  async function send(textArg?: string): Promise<void> {
    const text = (textArg ?? input).trim();
    if (!text || pending || !selectedId) return;
    const seq = seqRef.current++;
    setTranscript((t) => [...t, { id: `t${seq}:action`, kind: "action", text }]);
    setInput(""); setPending(true); setError(null); setPreserved(""); setSuggestions([]);
    uiBlip(600);
    try {
      const r = await engineApi.takeTurn(selectedId, text);
      if (!r.ok) {
        if (r.code === "connect_your_ai") {
          // Not a stumble of the tale: no model is connected. Withdraw the
          // echoed action, keep the player's words in the line, and show the
          // ONE connect notice — the same wording the gate uses (P11).
          setTranscript((t) => t.filter((e) => e.id !== `t${seq}:action`));
          setInput(text);
          setConnectNeeded(true);
          openConnectPanel();
          return;
        }
        setError(r.error);
        setPreserved(text);
        return;
      }
      const turn = r.data as EngineTurn;
      setTranscript((t) => [...t, ...turnEntries(turn, seq)]);
      setCapability(turn.capability ?? null);
      if (turn.state) setSnapshot(turn.state);
      setSuggestions(turnSuggestions(turn));
      // §11: the payload echoes the boundaries that actually governed this
      // turn (absent on a payload that predates the echo — keep the store's).
      const echo = appliedContent(turn);
      if (echo) {
        setApplied(echo);
        setBoundariesNote(boundariesNotice(turn));
      }
      setCampaigns((c) => (c ?? []).map((x) => (
        x.id === selectedId ? { ...x, last_turn_at: new Date().toISOString() } : x
      )));
    } finally {
      setPending(false);
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    void send();
  }

  /** Persist non-secret prefs; a typed key goes to this browser, never the
   *  server. An EMPTY key field keeps the stored key — Save is not a clear
   *  (the 2026-09-14 finding: a key must survive a prefs-only save). */
  async function saveConnectionPrefs(): Promise<EngineConnection | null> {
    const typed = keyInput.trim();
    const key = keyAfterConnectionSave(typed, getEngineKey());
    setEngineKey(key); // no-op when both are empty; keeps the stored key otherwise
    setKeyStored(!!key);
    if (typed) setKeyInput("");
    const r = await engineApi.saveConnection({
      provider,
      base_url: baseUrl.trim(),
      model: model.trim(),
      timeout_s: timeoutS,
    });
    if (!r.ok) { setConnStatus({ tone: "err", text: r.error }); return null; }
    setConnection(r.data);
    setConnectNeeded(false); // a saved real provider reopens the play gate
    return r.data;
  }

  async function saveConnection() {
    setConnBusy(true); setConnStatus(null);
    const saved = await saveConnectionPrefs();
    setConnBusy(false);
    if (saved) setConnStatus({ tone: "ok", text: `Saved — ${connectionLabel(saved)}.` });
  }

  async function testConnection() {
    setConnBusy(true); setConnStatus(null); setProbe(null);
    const typed = keyInput.trim();
    if (typed) { setEngineKey(typed); setKeyInput(""); setKeyStored(true); }
    // The check probes the SAVED prefs, so persist the form first and report
    // exactly what a turn would use.
    const saved = await saveConnectionPrefs();
    if (!saved) { setConnBusy(false); return; }
    const key = typed || getEngineKey();
    const r = await engineApi.probeConnection(key || null);
    setConnBusy(false);
    if (!r.ok) {
      // The same one notice for "nothing is connected" — never a raw error.
      setConnStatus(
        r.code === "connect_your_ai" ? { tone: "warn", text: CONNECT_NOTICE } : { tone: "err", text: r.error },
      );
      if (r.code === "connect_your_ai") openConnectPanel();
      return;
    }
    setProbe(r.data);
    setConnStatus(probeVerdictText(r.data));
  }

  function clearKey() {
    clearEngineKey();
    setKeyInput(""); setKeyStored(false); setProbe(null);
    setConnStatus({ tone: "ok", text: "Key forgotten in this browser — nothing was ever stored on the server." });
  }

  const chip = capabilityChip(capability);
  const degraded = engineDegraded(capability);
  // The play gate (P11): a key kept in this browser AND a real provider saved
  // on the account. Without both, the play line stays closed — the pilot is
  // never playable keyless, and never a silent stub turn.
  const gate = playGate({ keyStored, provider: connection?.provider, connectNeeded });
  const sections = engineStateSections(snapshot);
  // What governs the prose: the applied echo once a turn carries one (§11),
  // else the player's stored boundaries. Gated on `hydrated` so the first
  // client paint never claims defaults over stored values.
  const shownContent = applied?.prefs ?? (hydrated ? storedContent : null);

  return (
    <div style={{ maxWidth: 1080, margin: "0 auto", padding: 16 }}>
      <h1>Chronicle <span className="sys">· engine pilot</span></h1>
      <p className="sys">
        The rebuilt engine, running inside the chronicler. The tale is narrated
        by the model you connect — the chronicle does not write itself.
      </p>

      {signedIn === null && <p className="sys" aria-busy="true">Opening the pilot…</p>}
      {signedIn === false && <SignedOutCard />}

      {signedIn === true && (
        <div className="engine-grid">
          {/* PLAY */}
          <section aria-label="Chronicle" className="engine-transcript">
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {chip && (
                <span className={`tag verdict-chip verdict-${chip.tone}`} title={chip.title}>◈ {chip.label}</span>
              )}
              {chip === null && selectedId !== null && <span className="sys">No turn played in this session yet.</span>}
              {selectedId === null && <span className="sys">No chronicle open yet.</span>}
            </div>
            {degraded && (
              <div className="engine-banner warn" role="status">
                ⚠ Compatibility mode — your model answered without native tool calls,
                so the engine is using its JSON fallback. Turns work; the prose is plainer.
              </div>
            )}

            <div aria-live="polite" aria-label="Chronicle so far">
              {transcript.map((e) => {
                if (e.kind === "action") return <p className="sys" key={e.id}>❧ {e.text}</p>;
                if (e.kind === "narration") return <p className="narr" key={e.id}>{e.text}</p>;
                if (e.kind === "dialogue")
                  return (
                    <div className="dialogue" key={e.id}>
                      <div className="who">{e.speaker} speaks —</div>
                      <div>{e.text}</div>
                    </div>
                  );
                if (e.kind === "verdict")
                  return (
                    <p key={e.id}>
                      <span className={`tag verdict-chip verdict-${e.tone}`} title={e.label}>{e.text}</span>
                    </p>
                  );
                return <p className="sys" key={e.id}>◈ {e.text}</p>;
              })}
            </div>

            {transcript.length === 0 && !pending && (
              <p className="sys">
                {selectedId
                  ? "The parchment is blank. Say what you do — the world answers."
                  : "Start a new chronicle, or open one from the shelf beside this page."}
              </p>
            )}
            {pending && <p className="ack" role="status">The chronicler is writing…</p>}

            {error && (
              <div className="error-banner" role="alert">
                <strong>The chronicler stumbled —</strong> {error}
                {preserved && (
                  <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button className="btn" onClick={() => void send(preserved)} disabled={pending}>Try that again</button>
                    <button className="btn btn-ghost" onClick={() => { setError(null); setPreserved(""); }}>Dismiss</button>
                  </div>
                )}
              </div>
            )}
            {preserved && error && <p className="sys">Kept: “{preserved}”</p>}

            {suggestions.length > 0 && !pending && (
              <div className="suggest-row" aria-label="Suggested next moves">
                {suggestions.map((s) => (
                  <button key={s} type="button" className="suggest-chip" onClick={() => setInput(s)}>✦ {s}</button>
                ))}
              </div>
            )}

            <form onSubmit={onSubmit} style={{ marginTop: 12 }}>
              {gate.blocked && gate.notice && (
                <div className="engine-banner warn" role="status" style={{ marginBottom: 8 }}>
                  🔑 {gate.notice}{" "}
                  <button className="btn btn-ghost" type="button" onClick={openConnectPanel}>
                    Connect your AI
                  </button>
                </div>
              )}
              <label className="sr-only" htmlFor="engine-act">What do you do?</label>
              <input
                id="engine-act"
                className="input-parch"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Attempt anything — look around, ask after the road, draw your blade…"
                aria-label="action input"
                disabled={pending || gate.blocked}
                maxLength={2000}
                autoComplete="off"
              />
              <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center", flexWrap: "wrap" }}>
                <button className="btn" type="submit" disabled={pending || !input.trim() || !selectedId || gate.blocked}>
                  {pending ? "The quill moves…" : "Act ↵"}
                </button>
                <span className="sys">
                  {gate.blocked
                    ? "Browsing stays open anywhere — only the play line waits for a model."
                    : "Enter sends · suggestion chips fill the line."}
                </span>
              </div>
            </form>
            <div ref={bottomRef} />
          </section>

          {/* SIDE */}
          <aside style={{ display: "grid", gap: 12, alignContent: "start" }}>
            <section className="parchment card" aria-label="Your AI" ref={connectRef}>
              <h2 style={{ marginTop: 0 }}>Your AI</h2>
              <p className="sys" style={{ marginTop: 0 }}>
                Now: <strong>{connectionLabel(connection)}</strong>
                {keyStored ? " · a key is kept in this browser" : " · no key in this browser"}
              </p>
              {connection !== null && !providerConnected(connection.provider) && (
                <p className="sys" role="status" style={{ margin: "4px 0 0" }}>
                  ⚠ No provider is saved for your account yet — choose one below and press
                  Save. Play stays closed until a model is connected.
                </p>
              )}
              <button className="btn btn-ghost" onClick={() => (connectOpen ? setConnectOpen(false) : openConnectPanel())} aria-expanded={connectOpen}>
                {connectOpen ? "Hide the key panel" : "Connect your AI"}
              </button>

              {connectOpen && (
                <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
                  <label htmlFor="engine-provider">Provider
                    <select
                      id="engine-provider"
                      className="input-parch"
                      value={provider}
                      onChange={(e) => setProvider(e.target.value as EngineProviderChoice)}
                    >
                      <option value="openai-compatible">OpenAI-compatible endpoint</option>
                    </select>
                  </label>
                  <label htmlFor="engine-base">Base URL
                    <input
                      id="engine-base"
                      className="input-parch"
                      value={baseUrl}
                      onChange={(e) => setBaseUrl(e.target.value)}
                      placeholder={ENGINE_DEFAULT_BASE_URL}
                      autoComplete="off"
                    />
                  </label>
                  <label htmlFor="engine-model">Model
                    <input
                      id="engine-model"
                      className="input-parch"
                      value={model}
                      onChange={(e) => setModel(e.target.value)}
                      placeholder="gpt-5-mini, llama-3.3-70b, …"
                      autoComplete="off"
                    />
                  </label>
                  <label htmlFor="engine-key">API key
                    <input
                      id="engine-key"
                      className="input-parch"
                      type="password"
                      value={keyInput}
                      onChange={(e) => setKeyInput(e.target.value)}
                      autoComplete="new-password"
                      placeholder={keyStored ? "•••• kept in this browser — type to replace" : "sk-… kept in this browser, never on the server"}
                    />
                  </label>
                  <label htmlFor="engine-timeout">Timeout (seconds)
                    <input
                      id="engine-timeout"
                      className="input-parch"
                      type="number"
                      min={1}
                      max={600}
                      style={{ width: 110 }}
                      value={timeoutS}
                      onChange={(e) => setTimeoutS(Number(e.target.value))}
                    />
                  </label>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                    <button className="btn" onClick={() => void saveConnection()} disabled={connBusy}>{connBusy ? "…" : "Save"}</button>
                    <button className="btn btn-ghost" onClick={() => void testConnection()} disabled={connBusy}>Test connection</button>
                    <button className="btn btn-ghost" onClick={clearKey} disabled={!keyStored && !keyInput}>Clear key</button>
                  </div>
                  {connStatus && (
                    <p className="sys" role="status" style={{ color: connStatus.tone === "err" ? "#e09a9a" : connStatus.tone === "warn" ? "#e0b48f" : undefined }}>
                      {connStatus.text}
                    </p>
                  )}
                  <p className="sys" style={{ marginBottom: 0 }}>
                    <strong>Your key stays in this browser only.</strong> It is kept in this
                    browser&apos;s storage and sent with each turn as a request header; the
                    chronicler holds it for that one call and never writes it to the
                    database, a log, or a prompt. Provider, base URL, model and timeout are
                    saved to your account so your phone and desktop agree — the key is not.
                  </p>
                </div>
              )}
            </section>

            <section className="parchment card" aria-label="Content boundaries">
              <h2 style={{ marginTop: 0 }}>Story boundaries</h2>
              {shownContent ? (
                <p className="sys" style={{ margin: 0 }}>
                  Content boundaries: <strong>{boundariesSummary(shownContent)}</strong>
                  {applied ? " · applied to the last turn" : ""}
                  {" — "}
                  <Link href="/settings" prefetch>change in Settings</Link>
                </p>
              ) : (
                <p className="sys" style={{ margin: 0 }}>Reading your boundaries…</p>
              )}
              {boundariesNote && (
                <p className="sys" role="status" style={{ margin: "6px 0 0", color: "#e0b48f" }}>
                  ⚠ {boundariesNote}
                </p>
              )}
            </section>

            <section className="parchment card" aria-label="Pilot chronicles">
              <h2 style={{ marginTop: 0 }}>Pilot chronicles</h2>
              {campaigns === null && <p className="sys">Opening the shelf…</p>}
              {campaigns !== null && campaigns.length === 0 && (
                <p className="sys">{listError ?? "Nothing here yet — start one below."}</p>
              )}
              {campaigns !== null && campaigns.length > 0 && (
                <ul style={{ listStyle: "none", padding: 0, margin: "0 0 10px" }}>
                  {campaigns.map((c) => (
                    <li key={c.id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", margin: "6px 0" }}>
                      <button
                        className={c.id === selectedId ? "btn" : "btn btn-ghost"}
                        onClick={() => openCampaign(c.id)}
                        aria-current={c.id === selectedId ? "true" : undefined}
                      >
                        {c.name || "Untitled chronicle"}
                      </button>
                      <span className="sys">{c.world} · {formatWhen(c.last_turn_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
              {campaigns !== null && campaigns.length > 0 && listError && <p className="sys">{listError}</p>}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <input
                  className="input-parch"
                  style={{ flex: "1 1 160px" }}
                  placeholder="Name the new chronicle"
                  value={newName}
                  maxLength={200}
                  onChange={(e) => setNewName(e.target.value)}
                  aria-label="New chronicle name"
                />
                <button className="btn" onClick={() => void newChronicle()} disabled={creating}>
                  {creating ? "…" : "✦ New chronicle"}
                </button>
              </div>
            </section>

            <section className="parchment card engine-state" aria-label="State">
              <h2 style={{ marginTop: 0 }}>State</h2>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <button
                  className="btn btn-ghost"
                  onClick={() => { if (selectedId) void loadState(selectedId); }}
                  disabled={!selectedId || refreshing}
                >
                  {refreshing ? "…" : "Refresh"}
                </button>
                <span className="sys">GET /state {snapshot ? `· turn ${snapshot.turn ?? "—"}` : ""}</span>
              </div>
              {stateError && <p className="sys" style={{ color: "#e09a9a" }}>{stateError}</p>}
              {sections.map((s) => (
                <div key={s.title} style={{ marginTop: 10 }}>
                  <h3 style={{ margin: "6px 0 2px" }}>{s.title}</h3>
                  {s.note && <p className="sys" style={{ margin: "0 0 4px" }}>{s.note}</p>}
                  <dl className="kv">
                    {s.rows.map((row, i) => (
                      <div key={`${s.title}-${i}`} style={{ display: "contents" }}>
                        <dt>{row.label}</dt>
                        <dd>{row.value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </section>
          </aside>
        </div>
      )}
    </div>
  );
}
