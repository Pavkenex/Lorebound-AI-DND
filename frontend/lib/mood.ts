/** Mood chip display helpers (systems slice 3, docs/SYSTEMS_DESIGN.md §4).
 *
 *  Kept dependency-free so `node --test` can load it directly. The word list
 *  mirrors the backend's `MOOD_VOCAB` (npc/mood.py) and the gated fallbacks
 *  mirror `MOOD_FALLBACK` — a mood word and the chip it renders must never
 *  disagree between the two halves. The backend does the gating (a gated word
 *  never leaves the server without the content settings that allow it); this
 *  module only turns the surfaced pair into what the player sees. */

/** The curated v1 vocabulary, mirroring the backend's MOOD_VOCAB. */
export const MOOD_VOCAB = [
  "neutral", "happy", "amused", "warm", "sad", "lonely", "angry", "afraid",
  "anxious", "tired", "curious", "suspicious", "grateful", "resentful",
  "proud", "flirty", "horny",
] as const;

/** Gated words and their neutral fallbacks, mirroring the backend's MOOD_FALLBACK. */
export const MOOD_FALLBACK: Record<string, string> = { flirty: "warm", horny: "amused" };

const EMOJI: Record<string, string> = {
  neutral: "😐", happy: "😄", amused: "😏", warm: "😌", sad: "😢", lonely: "😔",
  angry: "😠", afraid: "😨", anxious: "😰", tired: "🥱", curious: "🤔",
  suspicious: "🤨", grateful: "🙏", resentful: "😒", proud: "😤",
  flirty: "😉", horny: "🥵",
};

/** Emoji for a mood word; an unknown word gets a plain dot, never a wrong face. */
export function moodEmoji(mood?: string | null): string {
  const word = String(mood ?? "").trim().toLowerCase();
  return EMOJI[word] ?? "•";
}

/** What the Present card shows for one NPC's mood, or null when nothing is live.
 *
 *  A settled mood (or a missing/NaN intensity) renders no chip at all: the
 *  card only ever shows a mood the character is actually wearing. */
export function moodChip(
  mood?: string | null,
  intensity?: number | null,
): { emoji: string; word: string; intensity: number; label: string } | null {
  const word = String(mood ?? "").trim().toLowerCase();
  const level = Number(intensity);
  if (!word || word === "neutral" || !Number.isFinite(level) || level <= 0) return null;
  return {
    emoji: moodEmoji(word),
    word,
    intensity: level,
    label: `${word} (${level.toFixed(1)})`,
  };
}
