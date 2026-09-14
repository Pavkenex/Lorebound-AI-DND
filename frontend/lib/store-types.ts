// Shared setting types (imported by api.ts to avoid a store cycle).

/** localStorage slot the app store persists its settings (content prefs
 *  included) to — the single source the request path reads at send time. */
export const PREFS_STORAGE = "lorebound-prefs-v1";

/** Canonical level vocabulary — must match the backend's ContentPrefs exactly. */
export const LEVELS = ["off", "reduced", "standard"] as const;
export type Level = (typeof LEVELS)[number];

/** Legacy values from the first UI build, mapped onto canonical levels. */
const LEVEL_ALIASES: Record<string, Level> = { low: "reduced", clean: "reduced", mild: "reduced" };

export interface ContentPrefs {
  violence: Level;
  horror: Level;
  romance: Level;
  language: Level;
  /** Master uncensored switch. Default OFF; when on, all content limits are
   *  lifted (per-axis caps above are superseded) and nothing fades to black.
   *  Sent as prefs.nsfw + X-Content-Prefs header with every request. */
  nsfw: boolean;
}

export const defaultPrefs: ContentPrefs = {
  violence: "reduced",
  horror: "reduced",
  romance: "off",
  language: "reduced",
  nsfw: false,
};

/** Coerce stored/legacy prefs onto the canonical shape. Unknown level strings
 *  fall back to the default for that axis; legacy low/clean/mild normalize.
 *  Accepts arbitrary stored data by design (old payloads, hand-edited storage). */
export function normalizeContentPrefs(
  raw: Partial<Record<"violence" | "horror" | "romance" | "language" | "nsfw", unknown>> | null | undefined
): ContentPrefs {
  const level = (value: unknown, fallback: Level): Level => {
    if (typeof value !== "string") return fallback;
    const mapped = LEVEL_ALIASES[value] ?? value;
    return (LEVELS as readonly string[]).includes(mapped) ? (mapped as Level) : fallback;
  };
  return {
    violence: level(raw?.violence, defaultPrefs.violence),
    horror: level(raw?.horror, defaultPrefs.horror),
    romance: level(raw?.romance, defaultPrefs.romance),
    language: level(raw?.language, defaultPrefs.language),
    nsfw: raw?.nsfw === true,
  };
}
