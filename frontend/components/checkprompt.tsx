"use client";
// The called check (t_84c31095 follow-up): when the engine determines a roll
// is required it surfaces the check here and waits. The die IS the button —
// press it, it tumbles, and the face that settles is the face the engine
// resolves. No separate roll button, no free throws: when no check is called,
// this card is not on the page at all. The card also shows why the DC is what
// it is (§6): the band it moved from, the engine's own reason, and the
// long-odds warning when nothing but a critical lands.
import { Dice } from "./dice3d";
import type { PendingCheck } from "../lib/api";
import { longOddsLine, oddsParts, shiftFrom, whyLine } from "../lib/check";

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

export function CheckPrompt({ spec, phase, face, error, onThrow, onSettled, onRetry }: CheckPromptProps) {
  const hint =
    phase === "ready" ? "Press the die to throw."
    : phase === "thrown" ? "The bones are falling…"
    : phase === "sending" ? "The die settles — the chronicle reads it…"
    : "The throw did not reach the chronicle. Press to send it again.";
  const pressable = phase === "ready" || phase === "retry";
  const odds = oddsParts(spec);
  const baseDc = shiftFrom(spec);
  const why = whyLine(spec);
  const longOdds = longOddsLine(spec);
  return (
    <section className="parchment card check-prompt" aria-label="A roll is called for">
      <p className="check-call">
        ⚄ A roll is called for — <strong>{spec.label}</strong>
      </p>
      <p className="sys check-odds">
        {odds.lead}
        <strong>DC {odds.dc}</strong>
        {odds.grade}
        {baseDc !== null ? <span className="check-shift"> (base {baseDc})</span> : null}
      </p>
      {why ? <p className="sys check-why">Why: {why}</p> : null}
      {longOdds ? <p className="sys check-long-odds">⚠ {longOdds}</p> : null}
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
