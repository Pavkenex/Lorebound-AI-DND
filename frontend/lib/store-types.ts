// Shared setting types (imported by api.ts to avoid a store cycle).
export interface ContentPrefs {
  violence: "off" | "low" | "standard";
  horror: "off" | "low" | "standard";
  romance: "off" | "low" | "standard";
  language: "clean" | "mild";
}
export const defaultPrefs: ContentPrefs = {
  violence: "low",
  horror: "low",
  romance: "off",
  language: "clean",
};
