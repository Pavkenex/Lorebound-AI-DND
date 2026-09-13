"use client";
// Adventure feed: narration vs NPC dialogue vs dice vs leads (t_43e37b1e).
// Dice events render a real 3D die (t_ae0e86a6) — tumbling on fresh rolls,
// settled on history; entities are clickable buttons; NEW LEAD arrivals render
// as gold notifications.
import { useState } from "react";
import type { FeedEvent } from "../lib/fixtures";
import { useStore } from "../lib/store";
import { uiBlip } from "../lib/audio";
import { Dice } from "./dice3d";
import { rollKind } from "../lib/dice3d";

export function Entity({ name, onInspect }: { name: string; onInspect: (n: string) => void }) {
  return (
    <button
      className="entity"
      onClick={() => { uiBlip(520); onInspect(name); }}
      title={`Inspect ${name}`}
    >
      {name}
    </button>
  );
}

/** Split [[Entity]] markers into clickable buttons. */
export function RichText({ text, onInspect }: { text: string; onInspect: (n: string) => void }) {
  const parts = text.split(/(\[\[.+?\]\])/g);
  return (
    <>
      {parts.map((p, i) => {
        const m = p.match(/^\[\[(.+)\]\]$/);
        if (m) return <Entity key={i} name={m[1]} onInspect={onInspect} />;
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

const OUTCOME_TEXT: Record<string, string> = {
  exceptional: "an exceptional success",
  success: "success",
  successwithcost: "success, at a cost",
  failure: "failure",
  criticalfailure: "a critical failure",
};

/** Sentence suffix describing the roll for the human reading the chronicle. */
function rollSummary(d20: number | undefined, outcome: string | undefined, total: number): string {
  const kind = rollKind(d20 ?? null, outcome ?? null);
  if (kind === "crit-success") return "· critical success — natural 20!";
  if (kind === "crit-miss") return "· critical miss — natural 1!";
  const o = (outcome ?? "").trim().toLowerCase();
  if (OUTCOME_TEXT[o]) return `· ${OUTCOME_TEXT[o]}`;
  return total >= 12 ? "· success" : "· partial";
}

function DiceBlock({ ev, fresh }: { ev: FeedEvent; fresh?: boolean }) {
  const [open, setOpen] = useState(false);
  const r = ev.roll!;
  const kind = rollKind(r.d20 ?? null, r.outcome ?? null);
  return (
    <div className={`dice-row${kind === "crit-success" ? " crit-success" : kind === "crit-miss" ? " crit-miss" : ""}`}>
      {typeof r.d20 === "number" ? (
        <Dice d20={r.d20} outcome={r.outcome} size={120} autoPlay={fresh} />
      ) : (
        <span className="d20-legacy" aria-hidden="true">⬢</span>
      )}
      <div className="dice-body">
        <button
          className="dice-line"
          aria-expanded={open}
          onClick={() => { uiBlip(kind === "crit-success" ? 880 : kind === "crit-miss" ? 220 : 520); setOpen((o) => !o); }}
          title="Toggle roll details"
        >
          {typeof r.d20 === "number" ? (
            <>
              <strong>{r.label}</strong>
              {typeof r.dc === "number" ? <> — vs <strong>DC {r.dc}</strong></> : null}
              {" · "}{r.d20}{r.dice.replace(/^d20/, "") ? ` ${r.dice.replace(/^d20/, "")}` : ""} = <strong>{r.total}</strong> {rollSummary(r.d20, r.outcome, r.total)}
              {typeof r.d20_second === "number" ? <> · ✦ kept {r.d20} over {r.d20_second}</> : null}
            </>
          ) : (
            <><strong>{r.label}</strong> — {r.dice} = <strong>{r.total}</strong> {rollSummary(r.d20, r.outcome, r.total)}</>
          )}
          <small> {open ? "▾" : "▸"}</small>
        </button>
        {open && r.detail && <p className="sys" style={{ margin: "4px 0 2px" }}>{r.detail}</p>}
        <span className="sr-only">Dice roll: {r.label}, {r.dice}, total {r.total}{typeof r.dc === "number" ? `, difficulty ${r.dc}` : ""}.</span>
      </div>
    </div>
  );
}

export function Feed({ events, streaming, onInspect, freshDice }: {
  events: FeedEvent[];
  streaming?: string | null;
  onInspect: (n: string) => void;
  /** ids of dice events that should roll their animation on mount. */
  freshDice?: Set<string>;
}) {
  const { compactNarration } = useStore();
  return (
    <div aria-live="polite" aria-label="Story so far">
      {events.map((e) => {
        if (e.kind === "dialogue")
          return (
            <div className="dialogue" key={e.id}>
              <div className="who">{e.speaker} speaks —</div>
              <div><RichText text={e.text ?? ""} onInspect={onInspect} /></div>
            </div>
          );
        if (e.kind === "dice") return <div key={e.id}><DiceBlock ev={e} fresh={freshDice?.has(e.id)} /></div>;
        if (e.kind === "lead")
          return <div className="lead-toast" key={e.id} role="status">✦ New lead — {e.lead}</div>;
        if (e.kind === "system") return <p className="sys" key={e.id}>{e.text}</p>;
        return (
          <p className={compactNarration ? "narr narr-compact" : "narr"} key={e.id}>
            <RichText text={e.text ?? ""} onInspect={onInspect} />
          </p>
        );
      })}
      {streaming != null && (
        <p className="narr stream-caret" aria-label="Narration arriving">{streaming}</p>
      )}
    </div>
  );
}
