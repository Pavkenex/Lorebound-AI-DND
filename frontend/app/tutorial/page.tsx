"use client";
// Tutorial: guided beats from GET /content/tutorial with fixture fallback.
// Beat-by-beat cards, a demo d20 check button, ends by routing into New Journey.
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, type TutorialDoc } from "../../lib/api";
import { useStore } from "../../lib/store";
import { uiBlip } from "../../lib/audio";

export default function TutorialPage() {
  const router = useRouter();
  const { content, addCost, set, tutorialDone } = useStore();
  const [doc, setDoc] = useState<TutorialDoc | null>(null);
  const [live, setLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [i, setI] = useState(0);
  const [demoRoll, setDemoRoll] = useState<number | null>(null);

  useEffect(() => {
    api.tutorial(content).then((r) => {
      setDoc(r.data); setLive(!r.fromFixture); addCost(r.cost);
      setLoading(false);
    }).catch(() => {
      setError("The tutorial pages stuck together. You can still walk straight into a New Journey.");
      setLoading(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function rollDemo() {
    const v = 1 + Math.floor(Math.random() * 20);
    setDemoRoll(v);
    uiBlip(v >= 12 ? 740 : 330);
  }

  function finish() {
    set({ tutorialDone: true });
    uiBlip(880);
    router.push("/adventure");
  }

  const beats = doc?.beats ?? [];
  const last = i >= beats.length - 1;

  return (
    <div style={{ maxWidth: 680, margin: "0 auto", padding: 16 }}>
      <p className="sys"><Link href="/">Menu</Link> · How to play</p>
      <h1>{doc?.title ?? "A Ten-Minute Beginning"}</h1>
      {!live && !loading && <p className="sys">Local pages (backend unreachable).</p>}
      {tutorialDone && <p className="sys">You have walked this road before — a refresher, then.</p>}

      {loading && (
        <div aria-label="Loading tutorial" role="status">
          <div className="skel" style={{ height: 140 }} />
          <p className="sys">Unrolling the introduction…</p>
        </div>
      )}
      {error && !loading && (
        <div className="error-banner" role="alert">
          <strong>The chronicler stumbled —</strong> {error}
          <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
            <button className="btn" onClick={() => { setError(null); setLoading(true); api.tutorial(content).then((r) => { setDoc(r.data); setLive(!r.fromFixture); setLoading(false); }); }}>Retry</button>
            <Link className="btn btn-ghost" href="/adventure" prefetch>Skip to New Journey</Link>
          </div>
        </div>
      )}
      {!loading && !error && beats.length === 0 && (
        <div className="parchment card" role="status">
          <p><strong>The introduction is blank.</strong></p>
          <p className="sys">Nothing to rehearse — walk straight in.</p>
          <Link className="btn" href="/adventure" prefetch>Begin a New Journey</Link>
        </div>
      )}
      {!loading && beats.length > 0 && (
        <section className="parchment card" aria-label={`Tutorial beat ${i + 1} of ${beats.length}`}>
          <p className="sys">BEAT {i + 1} / {beats.length}</p>
          <h2>{beats[i].h}</h2>
          <p>{beats[i].p}</p>

          {beats[i].h.toLowerCase().includes("check") && (
            <div style={{ marginTop: 12 }}>
              <p className="sys">Try it — a practice throw. No stakes, no record:</p>
              <button className="dice" onClick={rollDemo} aria-live="polite" title="Roll a practice d20">
                <span className="d20" aria-hidden="true">⬢</span>
                <span>{demoRoll === null ? "Roll a practice d20" : `d20 = ${demoRoll} — ${demoRoll >= 12 ? "success! Onward." : "partial — onward, at a cost."}`}</span>
              </button>
              <span className="sr-only">{demoRoll === null ? "No roll yet." : `Rolled ${demoRoll}.`}</span>
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }} role="group" aria-label="Tutorial steps">
            {beats.map((_, k) => (
              <button
                key={k}
                className={`btn btn-ghost${k === i ? " active-step" : ""}`}
                style={k === i ? { borderColor: "var(--gold)", color: "var(--gold-hi)" } : undefined}
                onClick={() => { uiBlip(); setI(k); }}
                aria-label={`Go to beat ${k + 1}`}
                aria-current={k === i ? "step" : undefined}
              >
                {k + 1}
              </button>
            ))}
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            {i > 0 && <button className="btn btn-ghost" onClick={() => { uiBlip(); setI(i - 1); }}>Back</button>}
            {!last && <button className="btn" onClick={() => { uiBlip(); setI(i + 1); }}>Next</button>}
            {last && <button className="btn" onClick={finish}>Begin a New Journey →</button>}
            <button className="btn btn-ghost" onClick={finish}>Skip</button>
          </div>
        </section>
      )}
    </div>
  );
}
