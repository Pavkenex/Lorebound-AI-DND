# Systems playtest — slices 2–6 on the local stack

Kanban `t_c334e3af` (docs/SYSTEMS_DESIGN.md §9 slice 6). One scripted walk plus a
real-browser pass over the five systems on a local stack (FastAPI :8001 with a
fresh `/tmp` SQLite DB, Next.js dev :3001, fake narrator :9098). Raw evidence
lives in `/opt/data/kanban-evidence/`:

- `t_c334e3af-playtest-run3.txt` — the clean run (106/106 checks)
- `t_c334e3af-act-log.jsonl` — every `/act` request/response of that run
- `ui-1…ui-8-*.png` — the browser pass (see "UI evidence" below)
- `t_c334e3af-full-pytest.txt`, `t_c334e3af-ruff.txt` — the gates after the fix

Driver: `/opt/data/scratch/t_c334e3af_playtest.py` (create it fresh each run —
it registers its own account and campaign).

```
cd backend && DATABASE_URL="sqlite:////tmp/lorebound-playtest-<epoch>.db" \
    .venv/bin/python -m uvicorn app.main:app --port 8001 --log-level warning
bash /opt/data/scratch/pa-frontend.sh                 # npm run dev -- -p 3001
python3 /opt/data/scratch/pa-fake-narrator.py 9098    # the fake relay
python3 /opt/data/scratch/t_c334e3af_playtest.py      # the walk
```

## The walk and what it showed

`106/106` checks passed (exit 0). Prebuilt rogue (Wren Ashgrove, Finesse 16 →
stealth +5), authored beats where the assertion is about authored prose.

| System | Observed |
| --- | --- |
| Talk / Marla | lead `unheard → rumored → accepted`; `❖ Marla +5 — Neutral (5)` then `+10 → 15`; remembers line `asked about the travelers who never came back`; mood chip `warm 0.5`; the invitation lands as dialogue ("you may come up when I bank the fire") + a stair suggestion. |
| Steal (all four branches) | seeds 12 / 10 / 5 / 1 → Success / SuccessWithCost / Failure / CriticalFailure at DC 13; meter `15 → −10 → −40 → −45 → −80 (Hostile)`; feed deltas `❖ Marla −25 / −30 / −5 / −35`; memories per branch ("came up light", "glimpsed who did it", "caught them red-handed"); the crit-fail leaves mood `angry 0.8`. |
| Two-phase die | `/act` answers a `pending_check` box `{label "Stealth — the storeroom strongbox", Finesse +3, trained +2, dc 13, dc_base 13, dc_why null, long_odds false, token}`; **the whole `/state` payload is byte-identical while the die waits** (nothing persisted); a forged token answers `409 check_expired`; the thrown face (20) resolves as `Exceptional`, total 25, and the chronicle keeps the settled dice row; the same throw replayed after the board moved answers `409` again. |
| Long odds | going hostile raises the same ask: `Intimidation check — dc 30 (base 13)`, `long_odds true`, why `a hard word (+4) · Marla Voss is angry (+3) · Marla Voss's coldness stands against you (+10)`; the natural 20 lands it as `Exceptional` at DC 30 (unconditional crit, §6). |
| Borin / fight | talk `+5` + memory; pressure (seed 20) → `borin_down`, mood `afraid 0.7`, `−20 → −15`; the brawl rolls Swordsmanship, `Exceptional`, hp `−1`, Marla `−15 → −95`. |
| Leave / return | map move to the Northern Road, `travel` checkpoint, `▸ Scene — Northern road`; two clue beats (`❧ Clue found — Cart tracks at the crossroads…`, then the lanterns thread); Marla's return greeting quotes this run's history: "she remembers your questions about travelers; her missing silver; how you eyed her strongbox". |
| Anti-loop | after the clues, idle beats degrade: `You linger a moment; the room has nothing new to give it.` + `You have been over that ground already, and nothing here says it twice. Still open: Read the wagon ruts · Watch the treeline for lanterns…` with those moves as suggestion chips. |
| Micro-scene | `I follow Marla upstairs` → `▸ Scene — The upstairs room`, `parent lantern-inn`, location card still the inn, present card narrowed to Marla, opening prose first visit only; the goal resolves ("She came north with…"), `+5 → −90`; walking down resolves into the inn; re-entering reads as aftermath ("The upstairs room again, and quieter than memory…"), never the opening again. `scene_transition` checkpoint saved. |
| Market / resolution | Sella persuasion rolls (`dc 17`, why `easy charm (+1)`) → `investigating`; the confrontation completes the arc (`completed: true`, epilogue dialogue), the whole room moves `+35` (Marla `−55 Wary`, Borin `+5 Neutral`, Sella `+35 Warm`), and the return finds Marla `happy` with `saw the missing travelers brought home safe out of the dark`. |

### UI evidence (real browser, fake relay configured)

| Shot | What it proves |
| --- | --- |
| `ui-1-present-card.png` | Present card: meter `Neutral +15`, mood chip `warm`, remembers line; and **no die card on load** — the die only exists when a check is called. |
| `ui-2-called-check.png` | The called-check card: `⚄ A roll is called for — Stealth — the storeroom strongbox` / `Stealth check — Finesse +3 · trained +2 · vs DC 13 (Moderate)` / `Press the die to throw.` Input disabled while the die waits. |
| `ui-3-thrown-die-settled.png` | The press resolves (the UI threw its own face 3 → total 8 → failure): chronicle keeps the dice row `vs DC 13 · 3 +5 = 8 · failure ▸`, the `❖ Marla −5 — Neutral (10)` delta, and the card flips to `suspicious` + `heard a suspicious clatter by the storeroom`. |
| `ui-4-long-odds-card.png` | The hard social prompt: `Intimidation check — vs DC 30 (Moderate) (base 13)`, why-line with all three terms, and `⚠ Long odds — nothing but a natural 20 will land.` Present card reads `Hostile −95`, `angry`. |
| `ui-5-hail-mary-settled.png` | The throw settles with the dice row + narration, no stuck card. |
| `ui-6-upstairs-micro-scene.png` | `▸ Scene — The upstairs room` in the live feed, the room's opening + Marla's dialogue, present card narrowed to Marla, location still the inn (scenes are not the map). |
| `ui-7-narrator-free-text.png` | An unknown action through the fake narrator: prose, two dialogue bubbles, three suggestion chips — the model path renders in the UI. |
| `ui-8-journal.png` | Journal renders the lead thread. |

## Findings

**Fixed here (`191cbf1`)**
- Doubled article in the generic inspect line: `You give the the room the
  traveler's once-over` (the target is stored article-bearing, the template
  added its own), and the same doubling in Marla's greeting
  (`your poking around the the room`). Both add the article only when the
  target lacks one; the note tags are untouched. Run 2 shows the bug, run 3 the
  fixed line.

**Reported, no code change (outside "styling/format", or a design call)**
1. **A successful persuasion writes no memory and moves no meter.** The market
   persuade resolves as `Exceptional` (dc 17, why `easy charm (+1)`) and opens
   the solution, but Sella stays `0 / Neutral` with `remembers: None` — while
   the parallel pressure path on Borin moves `−20` and writes a memory. Either
   the persuade-success branch wants its own `_remember(...)` pair (design §2 /
   §3 symmetry) or the asymmetry is intended; needs a ruling, not a drive-by
   edit.
2. **`PlayState.note()` writes into the legacy `marla_memory` list**, which is
   (a) a `_progress_markers()` member, so any note-taking beat counts as scene
   progress, and (b) the source of Marla's greeting "inspected:" lines. Proof
   from the marker probe: the first idle look on the road moved
   `marla_memory: ('talked:travelers',) -> ('talked:travelers', 'inspected:the room')`.
   Consequences: the anti-loop's idle streak can be reset by a look (the guard
   then fires on the 5th idle beat, not the 4th), and Marla can voice an
   inspection performed elsewhere ("your poking around the window") because the
   tag list is not per-NPC. The scene-director card's decision log calls the
   note marker deliberate; the cross-NPC greeting read is the part worth a look.
3. **The generic inspect prose is inn-flavored on every map** ("…inside, only
   the fire and Marla's patience move", "the room") — visible standing on the
   northern road. Content-level; a location-aware variant is a writing call.
4. **Canned-provider prose gets padded**: with no AI provider configured the
   stub's narration is under the word target, so `render_prose` appends "The
   moment holds, and what happens next is yours to decide." — the beat closes
   twice with a "the moment…" clause. Only the canned path shows it; the
   openai-compatible/fake path does not.
5. **A failed narrator call after a resolved check leaves no trace of the
   check.** Run 2 ran with the fake relay down: the Intimidation leg (thrown
   face 20 passed in) answered `The moment hangs unfinished — the sending failed
   before the chronicler could answer. Nothing was lost; try the words again.`
   and the save carries **no dice row and no mechanics** for that check (the
   earlier Sella row survives in the same feed). The beat's own effects persist;
   the player just cannot see that a roll happened.

**Harness notes (for the skill, reported not edited)**
- The `409` guard fires only when `pending_token` is present and stale.
  Omitting it re-runs the beat normally (a re-run of an already-walked beat is
  then answered by the anti-loop's diminishing line — that is guard, not 409).
- The fake narrator must be up **before** the AI-path legs; with it down the
  pipeline degrades to "the sending failed" (finding 5) and the response carries
  no `mechanics`.
- The anti-loop is per-scene: `idle = beats − last_progress` (see `/state`'s
  `scene` block), and a note-taking beat counts as progress (finding 2) — script
  the linger as 5 idle beats, or assert "a diminishing reply appears within 5".

## Gates after the fix

```
.venv/bin/python -m pytest tests -q -rA -p no:warnings   -> 403 passed, exit 0
.venv/bin/ruff check app                                 -> All checks passed!, exit 0
npm test                                                 -> 39 pass / 0 fail
npm run typecheck                                        -> clean
```

Commit: `191cbf1 fix(play): the inspect line and Marla's greeting no longer read
'the the room'`. Not pushed (the orchestrator pushes).
