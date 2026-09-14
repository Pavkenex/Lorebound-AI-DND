/** The story game's AI connection — one gate, one vocabulary (P12).
 *
 * The main game narrates with the player's own model, exactly like the
 * chronicle pilot: with nothing connected there is no turn, and a failed
 * model call reads as a failure, never as fiction. Everything the adventure
 * page believes about that lives here, so the wording is testable without a
 * DOM (`node --test lib/ai.test.ts`).
 */
import type { AiSettingsDoc } from "./api.ts";
import { CONNECT_NOTICE, providerConnected } from "./engine.ts";

export { CONNECT_NOTICE };

/** A model is connected (the doc's own flag; tolerant of an older server). */
export function aiConnected(doc?: AiSettingsDoc | null): boolean {
  if (!doc) return false;
  if (doc.connected === true) return true;
  return providerConnected(doc.active_provider);
}

/** "your endpoint (story-model)" — whose model, and which one. */
export function aiConnectionLine(doc?: AiSettingsDoc | null): string {
  if (!doc || !aiConnected(doc)) return "";
  const model = (doc.active_model || "").trim();
  const who = doc.active_source === "env" ? "the environment's model" : "your endpoint";
  return model ? `${who} (${model})` : who;
}

/** Why nothing is connected, in the player's words. */
export function aiNotConnectedReason(doc?: AiSettingsDoc | null): string {
  switch ((doc?.active_reason || "").trim()) {
    case "stub":
      return "this account is still set to the built-in storyteller, which no longer narrates.";
    case "incomplete":
      return "the saved endpoint is incomplete — a base URL and a model are both required.";
    case "misconfigured":
      return "the server's own AI configuration is incomplete.";
    case "unsupported":
      return "the saved provider is not one this app can use.";
    default:
      return "no endpoint is saved yet.";
  }
}

export interface AiPlayGate {
  blocked: boolean;
  kind: "connect" | "demo" | "offline" | null;
  notice: string | null;
}

/** May the chronicle take a turn? (P12)
 *
 * - signed out: the sample tale is a demo — readable, not playable;
 * - signed in but the backend did not answer: offline, with a retry;
 * - signed in with nothing connected (or the backend refused a turn with
 *   `connect_your_ai`): the connect notice.
 *
 * Browsing is never gated: the feed, the panels and the screens stay open.
 */
export function aiPlayGate(opts: {
  signedIn: boolean;
  live: boolean;
  doc?: AiSettingsDoc | null;
  connectNeeded?: boolean;
}): AiPlayGate {
  if (!opts.signedIn) {
    return {
      blocked: true,
      kind: "demo",
      notice: "Sample tale — a demo, not your own. Sign in and connect your AI to play.",
    };
  }
  if (!opts.live) {
    return {
      blocked: true,
      kind: "offline",
      notice: "The chronicler is out of reach — the backend did not answer. Retry once it is up.",
    };
  }
  if (opts.connectNeeded === true || (opts.doc != null && !aiConnected(opts.doc))) {
    return { blocked: true, kind: "connect", notice: CONNECT_NOTICE };
  }
  return { blocked: false, kind: null, notice: null };
}

/** The line for a failed turn — never a fabricated beat.
 *
 * The connect case belongs to the gate (the page switches to it rather than
 * showing an error); everything else says what happened, in the backend's own
 * (scrubbed) words when it has any.
 */
export function actFailureText(code?: string | null, message?: string | null): string {
  const kind = (code || "").trim();
  const msg = (message || "").trim();
  switch (kind) {
    case "connect_your_ai":
      return CONNECT_NOTICE;
    case "provider_failed":
    case "turn_failed":
      return msg
        ? `the model call failed — ${msg}. Retry, or check Settings → Tale-spinner (AI).`
        : "the model call failed. Retry, or check Settings → Tale-spinner (AI).";
    case "check_expired":
      return "The board has moved since the check was called — call it again.";
    case "unauthorized":
      return "Sign in first — your tale belongs to your account.";
    case "unreachable":
      return "The chronicler did not answer — the backend may be asleep or unreachable.";
    default:
      return msg || "The sending failed before the chronicler could answer.";
  }
}
