# Multiplayer Co-op Spike — Design Only (GDD §90)

Status: **design spike, not building.** Multiplayer does not begin until the
single-player experience is stable (vertical slice green + playtest go).

## Shape under consideration

- 2–4 players, one shared world and party; drop-in / drop-out with the
  world remembering who did what (per-player memory threads).

## Open questions

1. **Turn order**: free roleplay with a soft spotlight timer vs. strict
   initiative outside combat? How to keep one player from hogging Marla.
2. **Simultaneous dialogue**: two players talk to the same NPC at once —
   merge, queue, or interrupt? Who does the NPC answer first?
3. **Party disagreement**: one player steals while another negotiates —
   resolve as opposed checks; the world remembers both, NPC reacts to each.
4. **AI context size**: per-player memory threads retrieved separately, then
   fused; cost scales ~linearly — needs the caching work in Risk #4 first.
5. **Session persistence**: party snapshot + per-player deltas; rejoining
   player gets a "while you were away" summary from the journal.

## Gate

No multiplayer code until: slice playtest is a go, risks #2/#3/#4 mitigations
are in, and single-player session persistence ships.
