/**
 * Unit tests for the mood chip helpers (t_0732dcee, systems slice 3).
 *
 * The word list and the gated fallbacks must mirror the backend's `MOOD_VOCAB`
 * / `MOOD_FALLBACK` (npc/mood.py) — a mood word and the chip it renders can
 * never disagree between the two halves.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { MOOD_FALLBACK, MOOD_VOCAB, moodChip, moodEmoji } from "./mood.ts";

test("vocabulary and gated fallbacks mirror the backend", () => {
  assert.equal(MOOD_VOCAB.length, 17);
  assert.equal(new Set(MOOD_VOCAB).size, 17);
  for (const word of [
    "neutral", "happy", "amused", "warm", "sad", "lonely", "angry", "afraid",
    "anxious", "tired", "curious", "suspicious", "grateful", "resentful",
    "proud", "flirty", "horny",
  ]) {
    assert.ok((MOOD_VOCAB as readonly string[]).includes(word), `missing ${word}`);
  }
  assert.deepEqual(MOOD_FALLBACK, { flirty: "warm", horny: "amused" });
  // A fallback target is never itself gated (gating twice changes nothing).
  for (const target of Object.values(MOOD_FALLBACK)) {
    assert.ok(!(target in MOOD_FALLBACK));
    assert.ok((MOOD_VOCAB as readonly string[]).includes(target));
  }
});

test("every vocabulary word carries an emoji; unknown words get a dot", () => {
  for (const word of MOOD_VOCAB) {
    if (word === "neutral") continue;
    assert.notEqual(moodEmoji(word), "•", `no emoji for ${word}`);
  }
  assert.equal(moodEmoji("smug"), "•");
  assert.equal(moodEmoji(undefined), "•");
  assert.equal(moodEmoji(null), "•");
  assert.equal(moodEmoji("  WARM "), moodEmoji("warm"));
});

test("a settled mood renders no chip — only a live one does", () => {
  assert.equal(moodChip(), null);
  assert.equal(moodChip("neutral", 0), null);
  assert.equal(moodChip("warm", 0), null); // settled back to the baseline
  assert.equal(moodChip("warm"), null); // no intensity at all
  assert.equal(moodChip("warm", Number.NaN), null);
  assert.equal(moodChip("", 0.6), null);
});

test("a live mood renders word + emoji + a one-decimal label", () => {
  assert.deepEqual(moodChip("warm", 0.5), {
    emoji: "😌", word: "warm", intensity: 0.5, label: "warm (0.5)",
  });
  assert.deepEqual(moodChip("ANGRY", 0.8), {
    emoji: "😠", word: "angry", intensity: 0.8, label: "angry (0.8)",
  });
  // Gated words only ever arrive already allowed by the server; the chip shows
  // the word it was handed rather than inventing its own gate.
  assert.equal(moodChip("flirty", 0.6)?.label, "flirty (0.6)");
  assert.equal(moodChip("warm", 0.4375)?.label, "warm (0.4)");
});
