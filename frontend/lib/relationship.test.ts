/**
 * Unit tests for the relationship meter helpers (t_a9cd7ec2, systems slice 2).
 *
 * The band ladder must mirror the backend's `attitude_band` exactly — a meter
 * value and its band word can never disagree between the two halves.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { attitudeBand, attitudeBar, attitudeBarClass, clampAttitude, formatAttitude } from "./relationship.ts";

test("band thresholds mirror the backend ladder", () => {
  assert.equal(attitudeBand(-100), "Hostile");
  assert.equal(attitudeBand(-60), "Hostile");
  assert.equal(attitudeBand(-59), "Wary");
  assert.equal(attitudeBand(-20), "Wary");
  assert.equal(attitudeBand(-19), "Neutral");
  assert.equal(attitudeBand(0), "Neutral");
  assert.equal(attitudeBand(19), "Neutral");
  assert.equal(attitudeBand(20), "Warm");
  assert.equal(attitudeBand(59), "Warm");
  assert.equal(attitudeBand(60), "Bonded");
  assert.equal(attitudeBand(100), "Bonded");
  // Out-of-range input clamps before banding, never invents a band.
  assert.equal(attitudeBand(-140), "Hostile");
  assert.equal(attitudeBand(140), "Bonded");
});

test("clampAttitude keeps values on the meter and survives junk input", () => {
  assert.equal(clampAttitude(-100), -100);
  assert.equal(clampAttitude(100), 100);
  assert.equal(clampAttitude(-101), -100);
  assert.equal(clampAttitude(101), 100);
  assert.equal(clampAttitude(Number.NaN), 0);
  assert.equal(clampAttitude(12.6), 13);
});

test("formatAttitude shows the exact number, signed", () => {
  assert.equal(formatAttitude(34), "+34");
  assert.equal(formatAttitude(0), "0");
  assert.equal(formatAttitude(-25), "\u221225");
});

test("attitudeBar grows out from the centre, both directions", () => {
  assert.deepEqual(attitudeBar(0), { left: 50, width: 0 });
  assert.deepEqual(attitudeBar(100), { left: 50, width: 50 });
  assert.deepEqual(attitudeBar(-100), { left: 0, width: 50 });
  assert.deepEqual(attitudeBar(-25), { left: 37.5, width: 12.5 });
  assert.deepEqual(attitudeBar(60), { left: 50, width: 30 });
  // Geometry never escapes the track.
  for (const v of [-100, -60, -1, 0, 1, 20, 100]) {
    const { left, width } = attitudeBar(v);
    assert.ok(left >= 0 && width >= 0 && left + width <= 100, `bar(${v}) out of track`);
  }
});

test("band classes name the CSS modifiers", () => {
  assert.equal(attitudeBarClass(-80), "bar-att-hostile");
  assert.equal(attitudeBarClass(35), "bar-att-warm");
  assert.equal(attitudeBarClass(0), "bar-att-neutral");
});
