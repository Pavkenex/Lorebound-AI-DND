// The story game's AI gate (P12) — pure logic, no DOM needed.
// Run with: npm test   (node --test lib/*.test.ts, types stripped)
import assert from "node:assert/strict";
import test from "node:test";

import {
  CONNECT_NOTICE,
  actFailureText,
  aiConnected,
  aiConnectionLine,
  aiNotConnectedReason,
  aiPlayGate,
} from "./ai.ts";
import type { AiSettingsDoc } from "./api.ts";

function doc(over: Partial<AiSettingsDoc> = {}): AiSettingsDoc {
  return {
    provider: "openai-compatible",
    configured: true,
    base_url: "https://api.example.com/v1",
    model: "story-model",
    has_key: true,
    timeout_s: 30,
    connected: true,
    active_provider: "openai-compatible",
    active_source: "settings",
    active_model: "story-model",
    active_base_url: "https://api.example.com/v1",
    active_reason: "",
    env_provider: "",
    providers: ["openai-compatible"],
    ...over,
  };
}

const disconnected = (reason: string): AiSettingsDoc =>
  doc({
    provider: null,
    configured: false,
    base_url: "",
    model: "",
    has_key: false,
    connected: false,
    active_provider: "",
    active_source: "default",
    active_model: "",
    active_base_url: "",
    active_reason: reason,
  });

test("aiConnected trusts the server's flag, and older servers' names", () => {
  assert.equal(aiConnected(doc()), true);
  assert.equal(aiConnected(disconnected("unset")), false);
  // No doc at all: not connected (never assume).
  assert.equal(aiConnected(null), false);
  assert.equal(aiConnected(undefined), false);
  // Tolerant fallback for a server that predates the `connected` flag.
  const old = doc({ connected: undefined as unknown as boolean, active_provider: "stub" });
  assert.equal(aiConnected(old), false);
  const oldOpen = doc({ connected: undefined as unknown as boolean, active_provider: "openai-compatible" });
  assert.equal(aiConnected(oldOpen), true);
});

test("the connection line names the model and its source", () => {
  assert.equal(aiConnectionLine(doc()), "your endpoint (story-model)");
  assert.equal(
    aiConnectionLine(doc({ active_source: "env", active_model: "env-model" })),
    "the environment's model (env-model)",
  );
  assert.equal(aiConnectionLine(doc({ active_model: "" })), "your endpoint");
  assert.equal(aiConnectionLine(disconnected("unset")), "");
  assert.equal(aiConnectionLine(null), "");
});

test("each not-connected reason reads as a sentence", () => {
  assert.match(aiNotConnectedReason(disconnected("unset")), /no endpoint is saved/);
  assert.match(aiNotConnectedReason(disconnected("stub")), /built-in storyteller/);
  assert.match(aiNotConnectedReason(disconnected("incomplete")), /incomplete/);
  assert.match(aiNotConnectedReason(disconnected("misconfigured")), /server's own AI configuration/);
  assert.match(aiNotConnectedReason(disconnected("unsupported")), /not one this app can use/);
  // Unknown/absent reason still says something honest.
  assert.match(aiNotConnectedReason(disconnected("")), /no endpoint is saved/);
  assert.match(aiNotConnectedReason(null), /no endpoint is saved/);
});

test("signed out: the sample pages are readable, never playable", () => {
  const gate = aiPlayGate({ signedIn: false, live: false, doc: null });
  assert.equal(gate.blocked, true);
  assert.equal(gate.kind, "demo");
  assert.match(gate.notice ?? "", /demo/);
});

test("signed in with the backend silent: offline, not 'connect your AI'", () => {
  const gate = aiPlayGate({ signedIn: true, live: false, doc: doc() });
  assert.equal(gate.blocked, true);
  assert.equal(gate.kind, "offline");
});

test("signed in and connected: play is open", () => {
  const gate = aiPlayGate({ signedIn: true, live: true, doc: doc() });
  assert.deepEqual(gate, { blocked: false, kind: null, notice: null });
});

test("signed in with nothing connected: the connect notice (P11 wording)", () => {
  const gate = aiPlayGate({ signedIn: true, live: true, doc: disconnected("unset") });
  assert.equal(gate.blocked, true);
  assert.equal(gate.kind, "connect");
  assert.equal(gate.notice, CONNECT_NOTICE);
});

test("a server refusal (connect_your_ai) closes the gate even if the doc looked fine", () => {
  const gate = aiPlayGate({ signedIn: true, live: true, doc: doc(), connectNeeded: true });
  assert.equal(gate.blocked, true);
  assert.equal(gate.kind, "connect");
});

test("an unfetched doc does not block play by itself (the server is the authority)", () => {
  const gate = aiPlayGate({ signedIn: true, live: true, doc: null });
  assert.equal(gate.blocked, false);
});

test("failure text: the backend's words, never a fabricated beat", () => {
  assert.equal(actFailureText("connect_your_ai"), CONNECT_NOTICE);
  const failed = actFailureText("provider_failed", "chat-completions request failed: HTTP 500");
  assert.match(failed, /HTTP 500/);
  assert.match(failed, /Tale-spinner \(AI\)/);
  assert.match(actFailureText("turn_failed", ""), /model call failed/);
  assert.match(actFailureText("check_expired", ""), /board has moved/);
  assert.match(actFailureText("unauthorized", ""), /Sign in/);
  assert.match(actFailureText("unreachable", ""), /backend may be asleep/);
  // Unknown code with a message: pass the message through; without one: generic.
  assert.match(actFailureText("weird_code", "something broke"), /something broke/);
  assert.match(actFailureText("", ""), /sending failed/);
  assert.match(actFailureText(null, null), /sending failed/);
});
