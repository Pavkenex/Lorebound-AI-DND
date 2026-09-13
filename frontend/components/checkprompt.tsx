"use client";
// The called check (t_84c31095 follow-up): when the engine determines a roll
// is required it surfaces the check here and waits. The die IS the button —
// press it, it tumbles, and the face that settles is the face the engine
// resolves. No separate roll button, no free throws: when no check is called,
// this card is not on the page at all.
import { Dice } from "./dice3d";
import type { PendingCheck } from "../lib/api";

export type CheckPhase = "ready" | "thrown" | "sending" | "retry";

export interface CheckPromptProps {
  spec: PendingCheck;
  phase: CheckPhase;
  /** The settled (or settling) face once the player has pressed. */
  face: number | null;
  /** Inline notice, e.g. why a resend is needed. */
  error?: string | null;
  onThrow: () => void;
  onSettled: () => void;
  onRetry: () => void;
}

/** "Finesse +2 · trained +2 · " — the modifier trail that leads to the DC. */
function modsLine(spec: PendingCheck): string {
  const parts: string[] = [];
  if (spec.attribute && spec.attribute_mod) {
    parts.push(`${spec.attribute} ${spec.attribute_mod > 0 ? "+" : ""}${spec.attribute_mod}`);
  }
  if (spec.skill_mod) {
    parts.push(`trained ${spec.skill_mod > 0 ? "+" : ""}${spec.skill_mod}`);
  }
  return parts.length ? `${parts.join(" · ")} · ` : "";
}

export function CheckPrompt({ spec, phase, face, error, onThrow, onSettled, onRetry }: CheckPromptProps) {
  const hint =
    phase === "ready" ? "Press the die to throw."
    : phase === "thrown" ? "The bones are falling…"
    : phase === "sending" ? "The die settles — the chronicle reads it…"
    : "The throw did not reach the chronicle. Press to send it again.";
  const pressable = phase === "ready" || phase === "retry";
  return (
    <section className="parchment card check-prompt" aria-label="A roll is called for">
      <p className="check-call">
        ⚄ A roll is called for — <strong>{spec.label}</strong>
      </p>
      <p className="sys check-odds">
        {spec.skill} check — {modsLine(spec)}vs <strong>DC {spec.dc}</strong>
        {spec.difficulty ? ` (${spec.difficulty})` : ""}
      </p>
      <button
        type="button"
        className={`check-throw${pressable ? "" : " settled"}`}
        onClick={pressable ? (phase === "retry" ? onRetry : onThrow) : undefined}
        disabled={!pressable}
        aria-label={
          phase === "ready" ? "Throw the die"
          : phase === "retry" ? "Send the throw again"
          : "The die is settling"
        }
      >
        <Dice
          key={`${face ?? 20}-${phase}`}
          d20={face ?? 20}
          outcome={null}
          size={116}
          autoPlay={phase === "thrown"}
          onSettled={onSettled}
          interactive={false}
        />
      </button>
      <p className="sys check-hint" role="status">{hint}</p>
      {error ? <p className="sys check-hint" role="alert">{error}</p> : null}
    </section>
  );
}
