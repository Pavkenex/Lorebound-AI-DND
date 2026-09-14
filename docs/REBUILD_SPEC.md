We will initiate a rebuild of our game.
Here is the plan. You spin the workers who will do this and report to me once everything has been completed. I will most likely see it in the morning.


AI Dungeon Master System — Technical Specification
Purpose: Full architecture for a CYOA/tabletop-style narrative game engine where an
LLM (provided via user-supplied BYOK API key, targeting chat/completions-style
endpoints — OpenAI, Anthropic, Gemini, local/OpenAI-compatible servers, etc.) acts as
narrator/DM. This document is the build spec: data model, pipeline, memory system,
validation layer, and provider abstraction.

0. Design Principles (read this before building anything)
The LLM narrates. It does not own truth. All state that must be internally
consistent (HP, inventory, location, relationships, flags) lives in a structured
store the code owns. The model proposes changes via tool calls; code validates and
commits them. The model never silently "decides" game-state facts inside prose.
Never let the model adjudicate its own success. Dice/probability resolution
happens in code before the model writes prose about the outcome. The model is
told the outcome and asked to narrate it, never asked to invent whether something
worked.
Split "what happens" from "how it's written." These are different skills and
different failure modes. Conflate them and you get narrators that fudge mechanics
for drama, or mechanically-correct output that reads like a spreadsheet.
Retrieval, not accumulation. Memory stores should grow without bound; what gets
injected into a given prompt must be small, relevant, and budgeted. More stored
memory ≠ more injected context.
Lossy compaction must never be the only copy of load-bearing facts. Promises,
deaths, betrayals, and anything a human GM would never forget get pinned and
exempted from summarization.
Assume the model is unreliable at following instructions. Because the model is
BYOK, you cannot assume any particular level of instruction-following, function-
calling fidelity, or context length. Design for the weakest reasonable model in the
supported set, degrade gracefully for weaker ones, and take advantage of stronger
ones opportunistically.
Everything the model can affect must be validated before commit. Illegal state
transitions (spending gold you don't have, being in two places, reviving the dead)
are rejected by code, not prevented by prompting alone.

1. High-Level Architecture
┌─────────────┐     ┌───────────────────┐     ┌────────────────────┐
│ Player Input│────▶│ Turn Orchestrator │────▶│ Response to Player │
└─────────────┘     └───────────────────┘     └────────────────────┘
                             │  ▲
             ┌───────────────┼──┼───────────────────┐
             ▼               │  │                    ▼
    ┌─────────────────┐      │  │           ┌──────────────────┐
    │ Context Assembly │──────┘  └───────────│  Provider Adapter │
    │   / Budget       │  (assembled prompt)  │  (BYOK, multi-   │
    │   Controller     │                      │   vendor)        │
    └─────────────────┘                       └──────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────┐
    │              Memory Subsystem                │
    │  ┌───────┐ ┌────────┐ ┌───────┐ ┌─────────┐  │
    │  │ NPC   │ │Chronicle│ │ Saga  │ │ Leads / │  │
    │  │Memory │ │  Tail   │ │Digest │ │ Quests  │  │
    │  └───────┘ └────────┘ └───────┘ └─────────┘  │
    │  ┌───────────────┐ ┌────────────┐ ┌────────┐ │
    │  │ Relationships │ │ Moods/     │ │ World  │ │
    │  │   (ledger)    │ │ Personality│ │ Facts  │ │
    │  └───────────────┘ └────────────┘ └────────┘ │
    └─────────────────────────────────────────────┘
             ▲
             │ writes (validated)
    ┌─────────────────┐
│  State Validator │◀── proposed tool calls from model
    │  & Mechanics     │
    │  Resolver (dice, │
    │  checks, combat) │
    └─────────────────┘
Turn lifecycle:
Player submits input.
Orchestrator classifies intent (dialogue / action / exploration / meta-command)
— cheap, can be rules-based + small classifier, doesn't need the main model.
Mechanics Resolver runs any dice/probability/state-eligibility checks in code
for anything the intent implies (attack roll, skill check, inventory constraint).
Context Assembly Controller builds the prompt: retrieves relevant memory, applies
token budget, includes the resolved mechanical outcome as a fact the model must
narrate consistently with, not something it decides.
Provider Adapter sends to the user's configured model, requesting a tool-call
response (state deltas) plus narration — see §5.
State Validator checks proposed deltas against legality rules; rejects/clamps
illegal ones; commits legal ones to the State Store.
Consistency Check pass (cheap/fast, optionally a smaller model call) scans the
narration for contradictions with pinned facts before showing it to the player.
Memory Subsystem updates: chronicle tail appended, NPC memory candidates scored,
relationship ledger appended, mood decay applied.
Response shown to player.

2. Core State Store (Source of Truth)
This is a structured DB (Postgres/SQLite fine at small scale; document store fine too)
— not something reconstructed from prose. Suggested schema, condensed:
World {
  id, seed, calendar/time, active_scene_id
}

Location {
  id, name, description_static, connections[], flags{}, present_npc_ids[]
}

Character (player) {
  id, stats{}, inventory[{item_id, qty, flags}], position(location_id),
  status_effects[], known_facts[fact_id], journal_visible_entries[]
}

NPC {
  id, name, personality (static traits, tone, speech pattern),
  disposition_base (starting relationship anchor),
  location_id, alive: bool, schedule/routine,
  mood: { valence, arousal, decay_half_life, last_updated_turn },
  relationship: -> Relationship Ledger (see §3.5),
  memory: -> NPC Memory Store (see §3.1)
}

Lead/Quest {
  id, title, stage: enum[unheard, rumored, accepted, in_progress,
    complicated, resolved, failed, abandoned],
  stage_history[{stage, turn, trigger}],
  known_by[player_ids], related_npc_ids[], related_fact_ids[]
}

WorldFact {
  id, statement, pinned: bool, established_turn, source (npc_id | narrator | player),
  contradicts[] (fact_ids known to conflict, for validator use),
  tags[] (semantic tags for retrieval)
}

TurnLog (append-only) {
  turn_id, actor, raw_action, mechanical_resolution, narration_text,
  state_deltas_applied[], timestamp
}
All writes to NPC.mood, NPC.relationship, Lead.stage, WorldFact go through the
State Validator (§6), never directly from model output.

3. Memory Subsystem — Full Spec
Six stores + one addition (pinned facts / world facts as its own tier, split from
"established world facts" being folded into NPC memory — kept separate because facts
are global/shared, NPC memory is per-relationship).
3.1 NPC Memory (per character)
NPCMemoryEntry {
  id, npc_id, turn_established, statement,
  type: enum[factual, promise, grievance, kindness, secret_shared, observed],
  sentiment: -1.0..1.0,
  salience: float,          // computed, see below
  decay_rate: float,        // factual ≈ 0 (near-permanent); grievance/kindness decays
  reinforced_count: int,    // times referenced/confirmed again, boosts salience
  last_referenced_turn: int
}
Salience scoring (recomputed lazily at retrieval time, not stored static):
salience = w1*recency(turn) + w2*|sentiment| + w3*plot_relevance(current_scene)
           + w4*log(1 + reinforced_count) - w5*decay(turn_since_established, decay_rate)
plot_relevance = cosine similarity (embedding) between entry statement and current
scene context (active leads, current location, current dialogue topic).
Facts (type: factual) get decay_rate ≈ 0 — a fact doesn't fade just because it's
old. Emotional entries (grievance, kindness) decay unless reinforced — this
matches how people actually update feelings, and prevents "permanently furious NPC
syndrome" from a single stale event.
Retrieval per turn: for each NPC present in the scene, pull top-K (K≈5-8) entries
by salience for this NPC only, not a global top-N across all NPCs. This is the fix
for "everything gets dumped every turn" — scope by presence in scene.
3.2 Chronicle Tail (short-term continuity)
Store as structured events, not raw prose, with verbatim text retained only for
the most recent 2-3 turns:
ChronicleEntry {
  turn_id, actor, action_summary, mechanical_result, consequence_oneliner,
  verbatim_text: string | null   // populated only for last 2-3 turns
}
Window size ~12 entries (matches your instinct), FIFO. Cheap to keep structured;
verbatim only where voice/callback fidelity actually matters (immediate follow-up
dialogue), which is the expensive part, so bound it tightly.
3.3 Saga Digest — hierarchical, not single rolling summary
This is the piece I'd change most from a naive "keep re-summarizing one blob"
design, because repeated re-summarization is lossy and compounding (telephone game).
Session Summary (per ~20-40 turns) → Arc Summary (rolls up N session summaries,
triggered by narrative arc boundaries, e.g. quest resolved / act break) →
Campaign Digest (top-level, rolls up arc summaries)
All intermediate levels are archived, retrievable, never discarded. The
campaign digest is what's injected by default; arc/session summaries are pulled in
only if retrieval scoring says the current scene needs that level of detail
(e.g., player references something specific from "session 3").
Summarization pass runs on a cheap model call (doesn't need the user's primary
model / can use a fixed lightweight summarizer to keep BYOK cost down and quality
consistent regardless of what narrator model the user picked).
Pinned facts are exempt — see 3.6. The digest references them by ID rather than
re-deriving them, so a good promise/betrayal never gets smoothed away by
summarization drift.
3.4 Leads / Active Mysteries
State machine, transitions validated in code, not model-declared:
unheard → rumored → accepted → in_progress → complicated → resolved | failed | abandoned
Model proposes a transition via tool call (propose_lead_transition(lead_id, new_stage, justification)). Validator checks: is this a legal transition from
current stage (no skipping unheard→resolved), does required trigger condition hold
(e.g., "accepted" requires player to have taken an accept action, not just the NPC
mentioning it once). This stops the classic failure mode of an eager model
"solving" a mystery in one enthusiastic paragraph because it read as narratively
satisfying.
3.5 Relationships — append-only ledger, not a mutable scalar
RelationshipDelta {
  npc_id, player_id, turn, delta (-100..+100 not the value itself — the CHANGE),
  reason: string,           // "asked about the travelers", "lied about the shipment"
  category: enum[trust, affection, respect, fear, debt]
}
Current meter value = base anchor + sum of deltas with per-category decay applied
(old minor grievances fade toward baseline unless reinforced; major betrayals decay
much more slowly or not at all — decay rate itself is a tag on the delta, set by
category/magnitude at creation).
This gives you:
An audit trail (why is Marla at −40? — walk the ledger).
Natural decay without losing history.
The summarizer can generate an accurate reason-based description of relationship
state instead of inferring motive from a bare number.
3.6 Pinned Facts / World Facts
WorldFact {
  id, statement, pinned: bool, established_turn, source, tags[],
contradicts[]  // fact IDs the validator knows conflict, used to block contradictory
                 // new facts from being silently introduced
}
pinned: true facts are always eligible for injection if plot_relevance to
current scene passes a low threshold — they're cheap (short statements) and the
cost of missing one is high (continuity break the player will notice immediately).
Promises, deaths, betrayals, and explicit "the player said X and it mattered" facts
get auto-pinned. Everything else is retrievable but not force-included.
New facts proposed by the model go through the validator's contradiction check
against contradicts[] before being committed — this is your main defense against
a model asserting something that breaks established canon.
3.7 Mood — three-tier decay model (your instinct to fold into NPC data, formalized)
Personality  (static, defines baseline reaction tendencies — never changes at runtime)
    │
Relationship (durable, ledger-backed, decays slowly — §3.5)
    │
Mood         (transient: valence/arousal pair, half-life decay per turn,
              reset pressure from current scene events — e.g., an insult
              spikes anger regardless of long-term relationship)
Keeping these three explicitly separate (rather than one blended "vibe" field) is
what prevents two common failures: (a) an NPC being permanently grumpy because of
something three sessions ago (mood should have decayed, relationship shouldn't have),
and (b) an NPC's momentary anger overwriting their actual long-term trust in the
player (mood spikes, relationship doesn't move unless the event warrants a ledger
entry).

4. Context Assembly / Budget Controller
Per turn, build the prompt under a hard token budget (set relative to the user's
configured model's context window, leaving headroom for the response):
Priority order (highest first):
System/ruleset instructions — static, put first, mark for provider-side prompt
caching wherever the API supports it (huge cost/latency win, frees budget for
dynamic content).
Current scene structured state (location, present NPCs' current stats/mood,
active status effects) — small, always included.
Mechanically-resolved outcome for this turn (dice results, if any) — the model
must narrate consistent with this, never re-derive it.
Pinned facts relevant to scene (§3.6).
Retrieved NPC memory, scoped to NPCs present (§3.1), top-K by salience.
Relevant Lead state for leads touched by current scene/topic.
Chronicle tail (§3.2) — last 12 structured, last 2-3 verbatim.
Saga digest — campaign-level by default; drop in arc/session-level detail only if
retrieval scoring flags current input as referencing something specific.
If over budget: drop from the bottom of this list first. Never silently truncate
pinned facts or the mechanical outcome — those are non-negotiable; truncate saga
digest detail and lower-salience NPC memory first.
Practical numbers to start from (tune per model): reserve ~20% of context for
output, ~10-15% for system/ruleset (cacheable), remainder split roughly 40% scene
state + mechanics + pinned facts / 30% NPC memory + leads / 30% chronicle + digest —
adjust based on empirical playtesting, this isn't a law, it's a starting allocation.

5. Generation Pipeline (multi-pass)
Do not do this in one call. Minimum viable split:
Pass A — Mechanics (code, no LLM, or a tiny deterministic model at most):
Resolve dice/checks/combat math. Produces a structured outcome object.
Pass B — Narration (the user's BYOK model, the expensive/creative call):
Given assembled context + Pass A's outcome, generate: (1) prose narration, (2)
proposed state-delta tool calls (mood shift, relationship delta, new fact, lead
transition) via function calling. The model is told the mechanical outcome, not
asked to invent it.
Pass C — Validation (code):
Apply State Validator rules to every proposed delta. Reject/clamp illegal ones.
Commit legal ones.
Pass D — Consistency check (cheap/fast pass, ideally a small fast model call or
even embedding-similarity check):
Scan narration text against pinned facts + recently committed deltas for direct
contradiction ("Marla is dead" fact vs. narration having her speak). If a
contradiction is caught, either auto-regenerate Pass B with an explicit
correction note injected, or (cheaper) patch the offending sentence via a targeted
edit call.
This split means: mechanics stay fair regardless of narrator model quality, and the
creative writing stays creatively unconstrained (you're not asking the same call to
both invent good prose and police its own facts, which models are bad at
simultaneously).

6. State Validator & Mechanics Resolver
Rules live entirely in code, independent of any provider:
Range clamps (relationship −100..100, stats floors/ceilings).
Legal transition tables (Lead stage machine, §3.4).
Resource conservation (can't spend gold/items you don't have — checked against
State Store, not model's claim).
Contradiction checks against WorldFact.contradicts[].
Death/permanence rules (a dead NPC cannot be un-killed by narrative fiat; requires
an explicit, player-visible resurrection mechanic if the game supports one at all).
Rate limits on fact creation (stop a chatty model from inventing 20 new "facts"
a turn — most model output should be narration, not world-state mutation).
This is the layer that makes quality provider-agnostic: a weaker BYOK model will
propose worse or less-frequent tool calls, but it cannot corrupt state, because
the validator is the only thing with write access.

7. Provider Abstraction Layer (BYOK specifics)
Because the model is user-supplied and hits chat/completions-style endpoints:
Adapter interface normalizes: message formatting, function/tool-calling schema
differences (OpenAI-style tools, Anthropic-style tool_use, others), streaming,
and error/rate-limit handling — one internal call signature, N backend adapters.
Capability detection per key/model, done once at setup and cached:
Does it support function calling reliably? (test with a canary call)
Approximate context window (from model metadata / config, not assumed).
Cost tier, to inform how aggressively the budget controller economizes.
Graceful degradation path for models with weak/no function calling: fall back
to a constrained JSON-in-text protocol with strict parsing + regeneration-on-parse-
failure, rather than requiring native tool calls. Quality floor should never be
"the game breaks because this model doesn't do tool calls the way GPT does."
Never assume prompt caching, exact token limits, or system-prompt semantics are
identical across providers — abstract these as adapter-level config, not
hardcoded assumptions in the orchestrator.
Because narration quality varies a lot by user's chosen model, keep the
mechanically load-bearing work (Pass A, Pass C, Validator) on infrastructure you
control (fixed lightweight model or pure code) so a weak BYOK model degrades
prose quality, not game integrity.

8. Anti-Hallucination / Consistency Safeguards (summary of mechanisms above)


9. Testing & Quality Evaluation Loop
Don't ship on vibes. Build an eval harness before/alongside content:
Regression scenario suite: scripted play sessions with known expected state
outcomes (e.g., "player kills NPC in turn 5 → NPC must not speak in turn 40").
Run automatically against each pipeline change.
Contradiction injection tests: deliberately feed inputs designed to bait the
model into breaking pinned facts; measure catch rate of Pass D.
Cross-model quality matrix: run the same scripted sessions against multiple
BYOK-representative models (a strong one, a mid one, a weak/local one) to catch
degradation before users do.
Human/LLM-judge narration scoring: separate from correctness testing — score
prose quality (voice consistency, pacing, non-repetitiveness) on a sample of
sessions periodically; this is the "craft" axis that automated state-correctness
tests won't catch.
Token/cost telemetry per turn: track prompt-assembly size vs. budget target,
catch silent budget-controller regressions (e.g., digest layer creeping bigger
over time).

10. Suggested Build Order
State Store schema + Validator (no LLM yet) — get the "game" playable with
hardcoded/stubbed narration first. This is the part everything else depends on.
Provider Adapter for one reference model (pick the most capable one you'll
support) + Pass A/B/C pipeline wired end to end.
Chronicle tail + relationship ledger + mood (cheapest memory tier, immediate
payoff).
NPC memory store + salience retrieval.
Leads state machine.
Hierarchical saga digest + pinned facts.
Pass D consistency check.
Multi-provider adapter expansion + capability detection + degraded fallback path.
Eval harness (should really start earlier, in parallel with step 1 — retrofit is
painful).

This spec assumes a single-player campaign model; multiplayer (shared world state,
turn arbitration between simultaneous players) is a meaningful extension not covered
here and should be scoped separately if needed.