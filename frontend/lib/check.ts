/** Called-check display helpers (systems slice 4, docs/SYSTEMS_DESIGN.md §6).
 *
 *  A surfaced check carries more than a DC: `dc_base` and a `dc_why` trail
 *  (approach, the live mood, the relationship meter, a vow) plus a `long_odds`
 *  flag meaning only a natural 20 lands. The card renders exactly what the
 *  engine sent — the math lives server-side, and this module never re-derives
 *  it. Kept dependency-free so `node --test` can load it directly.
 */

/** The fields these helpers read; a PendingCheck is structurally compatible. */
export interface CheckView {
  label?: string;
  skill: string;
  attribute?: string | null;
  attribute_mod?: number;
  skill_mod?: number;
  dc: number;
  /** Difficulty before any shift; only sent when something moved it. */
  dc_base?: number | null;
  /** Why the DC moved, in the engine's own words, or null. */
  dc_why?: string | null;
  /** True when nothing but a natural 20 passes. */
  long_odds?: boolean;
  difficulty?: string;
}

function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

/** "Finesse +2 · trained +2 · " — the modifier trail that leads to the DC. */
export function modsLine(spec: CheckView): string {
  const parts: string[] = [];
  if (spec.attribute && spec.attribute_mod) {
    parts.push(`${spec.attribute} ${signed(spec.attribute_mod)}`);
  }
  if (spec.skill_mod) {
    parts.push(`trained ${signed(spec.skill_mod)}`);
  }
  return parts.length ? `${parts.join(" · ")} · ` : "";
}

/** The odds sentence in parts: the card bolds the DC, tests read the whole. */
export function oddsParts(spec: CheckView): { lead: string; dc: number; grade: string } {
  return {
    lead: `${spec.skill} check — ${modsLine(spec)}vs `,
    dc: spec.dc,
    grade: spec.difficulty ? ` (${spec.difficulty})` : "",
  };
}

/** The odds sentence: "Persuasion check — Wits +1 · trained +2 · vs DC 13 (Difficult)". */
export function oddsLine(spec: CheckView): string {
  const parts = oddsParts(spec);
  return `${parts.lead}DC ${parts.dc}${parts.grade}`;
}

/** The band DC a shifted check moved from, else null (nothing moved it). */
export function shiftFrom(spec: CheckView): number | null {
  const raw = spec.dc_base;
  // Absent is not zero: `Number(null)` is 0, which would invent a shift.
  if (raw === null || raw === undefined || (raw as unknown) === "") return null;
  const base = Number(raw);
  return Number.isFinite(base) && base !== spec.dc ? base : null;
}

/** "16 → 12" when the personality/role-play terms moved the DC, else null. */
export function shiftLine(spec: CheckView): string | null {
  const base = shiftFrom(spec);
  return base === null ? null : `${base} → ${spec.dc}`;
}

/** The engine's own explanation of the DC, or null when it moved nothing. */
export function whyLine(spec: CheckView): string | null {
  const why = String(spec.dc_why ?? "").trim();
  return why ? why : null;
}

/** The long-odds warning: only a critical passes this check, else null. */
export function longOddsLine(spec: CheckView): string | null {
  return spec.long_odds
    ? "Long odds — nothing but a natural 20 will land."
    : null;
}
