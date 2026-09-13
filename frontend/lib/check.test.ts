/**
 * Unit tests for the called-check display helpers (systems slice 4, §6).
 *
 * The card shows the engine's own numbers: the DC the player must beat, the
 * base it moved from, the engine's reason, and the long-odds warning. These
 * helpers must never invent a shift the engine did not send.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  longOddsLine,
  modsLine,
  oddsLine,
  oddsParts,
  shiftFrom,
  shiftLine,
  whyLine,
} from "./check.ts";

const base = {
  label: "Persuasion — Sella Voss",
  skill: "Persuasion",
  attribute: "Presence",
  attribute_mod: -1,
  skill_mod: 0,
  dc: 16,
};

test("mods and odds read like the card's odds line", () => {
  assert.equal(modsLine(base), "Presence -1 · ");
  assert.equal(modsLine({ ...base, skill_mod: 2 }), "Presence -1 · trained +2 · ");
  assert.equal(modsLine({ skill: "General", dc: 13 }), "");
  assert.equal(oddsLine(base), "Persuasion check — Presence -1 · vs DC 16");
  assert.equal(
    oddsLine({ ...base, dc: 12, difficulty: "Difficult" }),
    "Persuasion check — Presence -1 · vs DC 12 (Difficult)",
  );
  // The card bolds the DC, so it renders the sentence in parts.
  assert.deepEqual(oddsParts({ ...base, dc: 12, difficulty: "Difficult" }), {
    lead: "Persuasion check — Presence -1 · vs ",
    dc: 12,
    grade: " (Difficult)",
  });
});

test("a shifted DC shows where it moved from", () => {
  assert.equal(shiftLine({ ...base, dc_base: 16, dc: 12 }), "16 → 12");
  assert.equal(shiftFrom({ ...base, dc_base: 16, dc: 12 }), 16);
  assert.equal(shiftFrom({ ...base, dc_base: 16, dc: 16 }), null); // nothing moved
  assert.equal(shiftFrom(base), null);                             // older saves
  assert.equal(shiftLine({ ...base, dc_base: 16, dc: 16 }), null); // nothing moved
  assert.equal(shiftLine(base), null);                            // older saves
  assert.equal(shiftLine({ ...base, dc_base: null, dc: 19 }), null);
  assert.equal(shiftLine({ ...base, dc_base: Number.NaN, dc: 19 }), null);
});

test("the why line is the engine's own words, or nothing", () => {
  const why = "coin on the bar (−2) · Sella Voss is suspicious (+4)";
  assert.equal(whyLine({ ...base, dc_why: why }), why);
  assert.equal(whyLine({ ...base, dc_why: "   " }), null);
  assert.equal(whyLine({ ...base, dc_why: null }), null);
  assert.equal(whyLine(base), null);
});

test("long odds warn that only the critical lands", () => {
  assert.match(longOddsLine({ ...base, long_odds: true }) ?? "", /natural 20/);
  assert.equal(longOddsLine({ ...base, long_odds: false }), null);
  assert.equal(longOddsLine(base), null); // absent on every other check
});
