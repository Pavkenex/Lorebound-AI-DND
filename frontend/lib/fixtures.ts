// Fixture data — every screen renders from these until backends land.
export interface FeedEvent {
  id: string;
  kind: "narration" | "dialogue" | "dice" | "lead" | "system";
  text?: string;
  speaker?: string;
  roll?: { label: string; dice: string; total: number; detail?: string };
  lead?: string;
}

export const fixtures = {
  character: {
    name: "Kaelis Thorn",
    epithet: "the Lantern-Bearer",
    portraitHue: 36,
    background:
      "Raised by the lamplighters of Ravenford after the caravan fire took her parents, Kaelis learned to read roads by smell and strangers by their boots. She carries her mother's unlit lantern — a vow, not a tool.",
    level: 4,
    xp: 62,
    attributes: { Might: 12, Finesse: 15, Wits: 14, Resolve: 13, Presence: 11 },
    hp: { cur: 26, max: 32 },
    stamina: { cur: 9, max: 12 },
    resolve: { cur: 4, max: 6 },
    conditions: ["Weary", "Lantern-oath"],
    traits: ["Night-eyed", "Soft-footed", "Keeps every promise twice"],
    drives: ["Find the missing caravan", "Light the monastery beacon again"],
    relationships: [
      { name: "Marla Voss", note: "Innkeeper. Owes Kaelis a debt she will not name." },
      { name: "Brother Anselm", note: "Monastery archivist. Trades secrets for lamp oil." },
    ],
    equipment: ["Alder shortbow", "Lantern (unlit)", "Silvered knife", "Climber's cord"],
    achievements: ["Lit the Ford beacon (Day 3)", "Spared the smuggler's boy (Day 9)"],
  },
  gameState: {
    location: "The Lantern Inn, Ravenford",
    time: "Day 12 · 21:34 · rain",
    npcs: [
      { name: "Marla Voss", note: "wiping a cup, watching the door" },
      { name: "Hooded Carter", note: "mud to the knees, won't give a name" },
    ],
    leads: ["Missing Travelers", "The Monastery Lights"],
    interactables: ["hearth", "notice board", "cellar door", "Marla", "carter"],
    party: [
      { name: "Kaelis", hp: "26/32" },
      { name: "Bram", hp: "30/30" },
    ],
    feed: [
      { id: "e1", kind: "narration", text: "Rain needles the shutters of the Lantern Inn. The hearth throws long shadows across Marla's notice board, where one parchment hangs newer than the rest." } as FeedEvent,
      { id: "e2", kind: "dialogue", speaker: "Marla Voss", text: "You're the lamplighter's girl. Then you'll want to see this — a wagon came back without its driver last night." } as FeedEvent,
      { id: "e3", kind: "dice", roll: { label: "Wits check — notice the seal", dice: "d20+2", total: 17, detail: "Merchant Guild wax, cracked" } } as FeedEvent,
      { id: "e4", kind: "lead", lead: "Missing Caravan — a guild wagon returned driverless" } as FeedEvent,
    ] as FeedEvent[],
  },
  skills: {
    list: [
      { name: "Lockpicking", tier: "Apprentice", xp: 70, recent: ["Merchant's chest +14", "Cellar latch +6"], trainers: ["Old Fen (Ravenford)"], practice: "Pick the inn's practice lock" },
      { name: "Woodcraft", tier: "Journeyman", xp: 45, recent: ["Trail signs +10"], trainers: ["Bram"], practice: "Read the north road" },
      { name: "Lore", tier: "Novice", xp: 20, recent: ["Monastery archive +8"], trainers: ["Brother Anselm"], practice: "Study guild seals" },
    ],
    notes: ["Lockpicking improved", "Marla's trust increased"],
  },
  journal: {
    nodes: [
      { id: "caravan", label: "Missing Caravan", x: 80, y: 60, state: "confirmed" },
      { id: "wagon", label: "Destroyed Wagon", x: 260, y: 40, state: "confirmed" },
      { id: "powder", label: "Silver Powder", x: 430, y: 70, state: "confirmed" },
      { id: "guild", label: "Merchant Guild", x: 300, y: 170, state: "rumour" },
      { id: "smugglers", label: "Smugglers", x: 130, y: 220, state: "rumour" },
      { id: "monastery", label: "Old Monastery", x: 480, y: 200, state: "new" },
    ],
    edges: [["caravan", "wagon"], ["wagon", "powder"], ["powder", "guild"], ["guild", "smugglers"], ["powder", "monastery"]],
    detail: "Guild wax on a wrecked wagon. Silver powder in the cracks — monastery business, or smugglers wearing monks' colours?",
  },
  mapInfo: {
    places: [
      { name: "Ravenford", x: 140, y: 200, known: true, danger: 0, note: "Lantern Inn · guild post" },
      { name: "Old Monastery", x: 380, y: 110, known: true, danger: 2, note: "Lights seen — unconfirmed" },
      { name: "Wreck Hollow", x: 260, y: 260, known: true, danger: 3, note: "Destroyed wagon found here" },
      { name: "Greyfen", x: 470, y: 260, known: false, danger: 0, note: "Rumoured smuggler landing" },
    ],
    roads: [[0, 2], [2, 1], [2, 3]],
    rumours: ["Carter swears the monastery bells rang at midnight.", "Guild denies the wagon was theirs."],
    incidents: ["Active: missing travelers on the north road"],
  },
  inventory: [
    { name: "Alder shortbow", kind: "Weapon", note: "familiar grip, worn string" },
    { name: "Lantern (unlit)", kind: "Keepsake", note: "mother's — a vow, not a tool" },
    { name: "Silvered knife", kind: "Weapon", note: "cold against unnatural things" },
    { name: "64 guilders", kind: "Coin", note: "enough for a week, barely" },
  ],
  companions: [
    { name: "Bram Holloway", role: "Woodsman", note: "Laughs at danger, cries at songs. Bleeding loyalty since Day 2.", hue: 120 },
    { name: "Sister Pell", role: "Runaway novice", note: "Knows the monastery's back stair. Won't say how.", hue: 280 },
  ],
  fallbackNarration(text: string) {
    const t = text.toLowerCase();
    if (t.includes("marla") || t.includes("talk") || t.includes("ask"))
      return "Marla leans close, voice dropping under the rain. \"Ask at the wreck in Hollow Road — but go lamplit. Things walk it that fear the flame.\"";
    if (t.includes("board") || t.includes("notice") || t.includes("read"))
      return "The newest parchment is a guild notice: a wagon overdue from Greyfen. The wax seal is cracked — pressed in haste, or opened and resealed.";
    return "The inn holds its breath. Rain, hearth-crackle — and somewhere upstairs, a floorboard sighs. (The world remembers.)";
  },
};

export type Fixtures = typeof fixtures;
