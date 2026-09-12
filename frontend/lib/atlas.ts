// Atlas manifests — single source of truth for sprite-sheet coordinates.
// Grid: portraits-atlas.png is 1024x1024, 4 cols x 3 rows, 256px cells, 0 padding.
// Order matches backend/app/content/ravenford.py NPCS + existing companions.
// The image agent generates the PNG; this file lets the game slice it without
// per-portrait HTTP requests. JSON copies live in public/atlas/ for tooling.

export interface AtlasCell {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const PORTRAIT_ATLAS_URL = "/atlas/portraits-atlas.png";
export const PORTRAIT_ATLAS_SIZE = 1024;
export const PORTRAIT_CELL = 256;

export const PORTRAIT_ATLAS: Record<string, AtlasCell> = {
  marla: { x: 0, y: 0, w: 256, h: 256 },
  borin: { x: 256, y: 0, w: 256, h: 256 },
  "sella-voss": { x: 512, y: 0, w: 256, h: 256 },
  "tomm-ash": { x: 768, y: 0, w: 256, h: 256 },
  "sergeant-dain": { x: 0, y: 256, w: 256, h: 256 },
  wren: { x: 256, y: 256, w: 256, h: 256 },
  "brother-anselm": { x: 512, y: 256, w: 256, h: 256 },
  "mother-ilde": { x: 768, y: 256, w: 256, h: 256 },
  corb: { x: 0, y: 512, w: 256, h: 256 },
  fenn: { x: 256, y: 512, w: 256, h: 256 },
  ossia: { x: 512, y: 512, w: 256, h: 256 },
  "elder-bran": { x: 768, y: 512, w: 256, h: 256 },
  // Companions (second page of a future 1024x1536 extension; kept here so
  // portraitIdForName resolves them to individual files until then).
  kaelis: { x: 0, y: 768, w: 256, h: 256 },
  bram: { x: 256, y: 768, w: 256, h: 256 },
  "sister-pell": { x: 512, y: 768, w: 256, h: 256 },
};

export const ICONS_ATLAS_URL = "/atlas/icons-atlas.png";

export const ICONS_ATLAS: Record<string, AtlasCell> = {
  "merchant-guild": { x: 0, y: 0, w: 128, h: 128 },
  "quiet-order": { x: 128, y: 0, w: 128, h: 128 },
  "town-watch": { x: 256, y: 0, w: 128, h: 128 },
};

/** Normalize a display name ("Sella Voss", "Sergeant Dain") to an atlas id. */
export function portraitIdForName(name: string): string {
  const raw = name.trim().toLowerCase();
  // Aliases keyed by first name for backend NPC ids.
  const firstNameAlias: Record<string, string> = {
    marla: "marla",
    borin: "borin",
    sella: "sella-voss",
    tomm: "tomm-ash",
    sergeant: "sergeant-dain",
    dain: "sergeant-dain",
    wren: "wren",
    brother: "brother-anselm",
    anselm: "brother-anselm",
    mother: "mother-ilde",
    ilde: "mother-ilde",
    corb: "corb",
    fenn: "fenn",
    ossia: "ossia",
    elder: "elder-bran",
    bran: "elder-bran",
    kaelis: "kaelis",
    bram: "bram",
    pell: "sister-pell",
    sister: "sister-pell",
  };
  const slug = raw
    .replace(/^sister\s+/, "sister-")
    .replace(/^sergeant\s+/, "sergeant-")
    .replace(/^brother\s+/, "brother-")
    .replace(/^mother\s+/, "mother-")
    .replace(/^elder\s+/, "elder-")
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9-]/g, "");
  if (PORTRAIT_ATLAS[slug]) return slug;
  const first = raw.split(/\s+/)[0].replace(/[^a-z]/g, "");
  return firstNameAlias[first] ?? slug;
}
