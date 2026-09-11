"use client";
// Adventure feed: narration vs NPC dialogue vs dice vs leads (t_43e37b1e).
// Dice blocks are compact + expandable; entities are clickable buttons;
// NEW LEAD arrivals render as gold notifications.
import { useState } from "react";
import type { FeedEvent } from "../lib/fixtures";
import { useStore } from "../lib/store";
import { uiBlip } from "../lib/audio";

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

function DiceBlock({ ev }: { ev: FeedEvent }) {
  const [open, setOpen] = useState(false);
  const r = ev.roll!;
  const win = r.total >= 12;
  return (
    <div>
      <button
        className="dice"
        aria-expanded={open}
        onClick={() => { uiBlip(win ? 740 : 330); setOpen((o) => !o); }}
        title="Toggle roll details"
      >
        <span className="d20" aria-hidden="true">⬢</span>
        <span><strong>{r.label}</strong> — {r.dice} = <strong>{r.total}</strong> {win ? "· success" : "· partial"}</span>
        <small>{open ? "▾" : "▸"}</small>
      </button>
      {open && r.detail && <p className="sys" style={{ margin: "0 0 8px 24px" }}>{r.detail}</p>}
      <span className="sr-only">Dice roll: {r.label}, {r.dice}, total {r.total}.</span>
    </div>
  );
}

export function Feed({ events, streaming, onInspect }: {
  events: FeedEvent[];
  streaming?: string | null;
  onInspect: (n: string) => void;
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
        if (e.kind === "dice") return <div key={e.id}><DiceBlock ev={e} /></div>;
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
