# Systems Design — Memory, Relationships & Mood, Personality, Social Checks, Scene Flow

Status: DRAFT — for review, before implementation. Scope: the "living
characters + scene flow" systems discussed 2026-09-13. Coefficients named
here are tunable knobs, not contracts.

## 1. Principles

- Every character keeps their **own memory and personality**; decisions and
  dialogue flow from those, not from narrator omniscience.
- **Relationship** is a visible meter (bar + number) per character.
- **Mood** is a live state (happy / sad / neutral / horny / …) that colors
  narration and decisions.
- **No absolute walls:** every contested social attempt rolls; a hail-mary
  can land if the player plays their cards right.
- Scenes may linger, but never loop or pull the player back; when it is
  naturally time to move on, the story transitions cleanly.
- **Side stories are first-class:** characters and the world run their own
  threads; the player may pursue anything to its natural end, and the main
  quest pulls through consequence and invitation — never by locking doors.
  When a side story resolves, the story finds its way back to the main
  thread.
- **Leverage counts when it matches a need:** money, gifts, favors, or
  pressure can decide an outcome — or shortcut it outright — when they
  answer what the character actually wants.

## 2. Memory — three layers

### 2.1 World ledger (canon)
Objective truth: `world_facts` (+ existing `visibility`: hidden | party |
public) and `narrative_memories`. Facts record what happened and who did it;
visibility gates what any given actor can plausibly know. Plot-critical
facts never fade.

### 2.2 Main character's journal
What the PC experienced, learned, promised, or owes. Feeds the Journal
screen and the narrator's "what you know" context. Backed by the PC-visible
slice of `narrative_memories` + leads/clues.

### 2.3 NPC memory
Per-NPC records: `{text, kind, sentiment, salience, at_clock}` (extends
`npc_memories`). Written when events involve that NPC — witnessed, done to
them, or heard about (hearsay comes later; v1 = present/participant).
Each NPC recalls *their* slice; a character who wasn't there doesn't know.

- **Write path:** the engine tags interactions (kindness, gift, theft,
  threat, promise, promise_broken, flirt, insult, help, violence, rescue…);
  narrator proposals stay advisory and pass the same AuthorityEngine gate as
  everything else.
- **Retention:** salience + recency; per-NPC cap (≈50) with lowest-salience
  pruning; plot-tagged entries never pruned; flavor fades naturally.
- **Retrieval:** top-K (default 3) per NPC per scene by relevance
  (location/actors) + recency + salience — prompts stay small.

## 3. Relationship meter

- Value **−100…+100** per (NPC → PC), on screen as a **bar + exact number**
  plus band words: Hostile −100..−60 · Wary −59..−20 · Neutral −19..+19 ·
  Warm +20..+59 · Bonded +60..+100.
- Deltas come from tagged interactions, weighted by personality sensitivity
  (a proud character penalizes insults double; a lonely one over-weights
  attention).
- Feedback: small delta chips in the chronicle ("Marla +2") so the player
  feels the meter move.
- Used by: social DCs (see 6), narrator tone, NPC decision weights.

## 4. Mood — current emotional state

- Separate from the meter: `{mood, intensity 0..1}` per character, decaying
  toward the personality baseline as time and scenes pass.
- Vocabulary v1 (curated, extensible): neutral, happy, amused, warm, sad,
  lonely, angry, afraid, anxious, tired, curious, suspicious, grateful,
  resentful, proud, flirty, horny. NSFW-adjacent moods respect the
  campaign's content preferences — they only surface when settings allow.
- Sources: events (theft → angry; kindness → warm/happy; a hard day →
  tired) and relationship nudges.
- Effects:
  (a) **Narration** — the narrator sees "Marla — mood: amused (0.6)" and
      colors her behavior accordingly;
  (b) **Decisions** — DC/texture modifiers (e.g. angry: hard social +4;
      flirty: charm approaches −2; afraid: reassurance lands easier).

## 5. Personality

- Per character: 3–6 trait axes (pride, faith, greed, kindness, caution,
  lust, loyalty…) + soft spots (loneliness, vanity, coin, duty) + optional
  **vows/values**.
- Vows are **conditional, not universal** — only characters where it makes
  sense carry them. A vow is a strong modifier, never an absolute wall.
- Effects: scales relationship deltas, biases which mood an event produces,
  shifts social DCs per approach type. "Playing your cards right" = matching
  approach to soft spots, with information the player actually gathered.

## 6. Social checks & the hail-mary rule

- Every contested social attempt rolls: d20 + skill/approach mods vs DC.
  `DC = ask difficulty + personality/approach modifier + mood modifier −
  relationship credit` (warmth lowers it; hostility raises it).
- **The dice speak:** a nat-20 is always a **critical success** (the
  system's `Exceptional` outcome — it applies even when the DC looked out
  of reach); a nat-1 always fails critically. "Success at a cost" is
  unchanged, and is a different band: borderline rolls that just barely
  meet the DC (margin 0–2), never criticals.
- Because criticals are unconditional, every rolled contest keeps a 5%
  story-payoff window. Relationship, mood, personality and play quality
  move the odds around it — they change what a non-critical result needs,
  never whether a miracle is possible.
- **Long odds are visible:** genuinely hard attempts get a "Long odds" tag
  on the check prompt, so the player knows they are gambling.
- **Leverage is a legitimate shortcut:** when money answers the character's
  need (debts, greed, a price they name), paying can decide the outcome —
  at the extreme, skipping the roll because the price genuinely meets the
  need. Gifts, favors, and pressure work the same way, each through the
  character's personality; pressure carries its own price (relationship
  damage, backfire risk).
- **Worked example** (from the design discussion): a deeply guarded, cold
  character can still be won over — right approach + right information +
  timing + a lucky die — and if the player finds the need this character
  actually has, that leverage can be the deciding factor. Content follows
  the campaign's content settings.
- Anti-cheese: declaring success changes nothing (existing AuthorityEngine
  rule); only rolls + validated outcomes + real leverage move state.

## 7. Scene flow ("the shield")

- Scene model: `{location, goal (from active leads), beat count,
  last_progress, state: active → resolved → transitioning}` — backed by the
  existing `scenes` / `scene_events` / `player_actions` tables.
- **Micro-scenes are first-class:** a scene is finer-grained than the map.
  "The upstairs room", "the back alley", "the cellar corner" are scenes
  even though the map still reads Lantern Inn. Scenes are not limited to
  campaign locations — the map tracks travel, scenes track the moment.
  Sub-scenes arise from the fiction (an invitation, an agreement, player
  initiative) and resolve back to their parent.
- Staying in a scene a while is fine. Lingering is allowed.
- **Anti-loop:** repeat/idle actions get diminishing responses and point at
  what is still possible. A resolved scene never re-runs its opening, and
  narration never pulls the player back into a scene they've left. The
  guard never blocks a live transition — when the fiction opens a door (an
  NPC says "come upstairs"), the engine takes it.
- **Transition triggers:** (a) the player moves; (b) the scene goal
  resolves or is clearly exhausted; (c) a story beat/chapter boundary;
  (d) a narrative invitation or agreement — NPC or player proposes the
  new scene.
- **On transition:** autosave checkpoint + bridging narration + the new
  scene set (clock advances, NPC set changes). Re-entering a scene restores
  its remembered state.
- **Side stories:** the director runs threads beyond the main quest; they
  reach a natural end (resolution, refusal, a promise for later) and the
  flow then leads back to the main thread by invitation and consequence,
  not blockage.
- Deliverable: no more circling the same evening; scenes open and close the
  way they do in a story — and a bar conversation can walk upstairs.

## 8. Integration map

- **Activate existing tables:** `world_facts`, `narrative_memories`,
  `npcs` / `npc_memories` / `npc_relationships` / `npc_goals`,
  `scenes` / `scene_events` / `player_actions`.
- **New logic homes:** `modules/npc/` (personality, mood, decisions),
  `modules/memory/` (write + retrieval pipeline), `modules/story/` (scene
  director), `modules/world/` (ledger service) — the empty modules already
  reserved for this.
- **Engine touchpoints:** `play/state.py` (expose mood/relationship),
  `play/engine.py` (interaction tags, feedback events, scene hooks),
  `rules/checks.py` (social DC modifiers), `narrator/prompts.py`
  ([NPCs present] gains mood + remembered deeds; [Scene] gains goal/progress).
- **UI:** adventure NPC panel (bar + number + mood chip); per-NPC
  "what they remember about you" list; Journal screen (PC memory); delta
  chips in the feed; "Long odds" tag on check prompts.
- **Save/restore:** new fields ride the existing snapshot mechanism —
  a restore keeps memory/mood/meter as of the save.

## 9. Rollout plan (each slice ends with tests + a playable check)

1. Data + write path: tables activated, interaction tags, NPC memory
   writes; NPC card gains the memory list.
2. Relationship meter: values, deltas, bar + number + chips.
3. Mood: state, decay, narrator injection, mood chip.
4. Personality + social checks: DC modifiers, hail-mary rule, Long-odds tag.
5. Scene director: state machine, micro-scenes, anti-loop guard,
   invitation transitions, autosave.
6. Content pass: journal/NPC views polished, playtest harness updated.

## 10. Open questions (settle during build)

- Meter decay: none by default (events move it), or a slight pull toward
  Neutral per chapter? Proposal: none for v1.
- Mood vocabulary growth + which moods are gated behind content settings.
- How loudly memories surface: explicit "she remembers…" lines vs silent
  weighting in decisions. Proposal: both — explicit for big beats, silent
  otherwise.
