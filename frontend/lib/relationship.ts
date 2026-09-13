/** Relationship meter display helpers (systems slice 2, docs/SYSTEMS_DESIGN.md §3).
 *
 *  Kept dependency-free so `node --test` can load it directly. The band
 *  thresholds mirror the backend's `attitude_band` (play/state.py) — a meter
 *  value and its band word must never disagree between the two halves. */

export const ATTITUDE_MIN = -100;
export const ATTITUDE_MAX = 100;

/** Clamp a meter value into the -100..+100 range. */
export function clampAttitude(value: number): number {
  const v = Math.round(Number.isFinite(value) ? value : 0);
  return Math.max(ATTITUDE_MIN, Math.min(ATTITUDE_MAX, v));
}

/** Band word for a meter value: Hostile .. Bonded (mirrors the backend). */
export function attitudeBand(value: number): string {
  const v = clampAttitude(value);
  if (v <= -60) return "Hostile";
  if (v <= -20) return "Wary";
  if (v <= 19) return "Neutral";
  if (v <= 59) return "Warm";
  return "Bonded";
}

/** Signed display of a meter value: "+34", "0", "−25" (typographic minus). */
export function formatAttitude(value: number): string {
  const v = clampAttitude(value);
  return v > 0 ? `+${v}` : String(v).replace("-", "\u2212");
}

/** Centre-anchored bar geometry: the fill grows out from the middle.
 *
 *  A negative value fills left of centre, a positive one right of centre, so
 *  the bar reads hostile/warm at a glance; percentages of the full track. */
export function attitudeBar(value: number): { left: number; width: number } {
  const half = clampAttitude(value) / 2; // ±100 -> ±50% of the track
  return half >= 0
    ? { left: 50, width: half }
    : { left: 50 + half, width: -half };
}

/** CSS modifier for the band colour: bar-att-hostile .. bar-att-bonded. */
export function attitudeBarClass(value: number): string {
  return `bar-att-${attitudeBand(value).toLowerCase()}`;
}
