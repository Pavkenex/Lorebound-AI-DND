// Shared setting types (imported by api.ts to avoid a store cycle).
export interface ContentPrefs {
  violence: "off" | "low" | "standard";
  horror: "off" | "low" | "standard";
  romance: "off" | "low" | "standard";
  language: "clean" | "mild";
  /** Explicit adult-content gate. Default OFF. Sent as prefs.nsfw + X-Content-Prefs header. */
  nsfw: boolean;
}
export const defaultPrefs: ContentPrefs = {
  violence: "low",
  horror: "low",
  romance: "off",
  language: "clean",
  nsfw: false,
};
