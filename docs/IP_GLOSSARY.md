# Original IP Glossary (Track: Original IP guidelines, GDD §125)

Every rules term, creature, place, and item in the build traces to an
in-house source. We are inspired by tabletop RPGs in general; we reproduce
no proprietary setting, monster, spell list, class, or text.

## Rule: in-house first

- If a term is not on the approved list below, it does not ship. Add it here
  first, with a one-line definition, before using it in content or code.
- Never copy distinctive proprietary names, stat blocks, spell descriptions,
  or setting text from any published game. Generic fantasy words (sword,
  inn, guild, forest) are fine; distinctive combinations are not.
- When in doubt, rename toward Ravenford: plain words in new arrangements.

## Approved terms (all in-house)

### Skills
| Term | Meaning |
|---|---|
| Persuasion | Win people over with honest talk, bargaining, pleas |
| Intimidation | Frighten or pressure someone into backing down or talking |
| Investigation | Search, spot clues, piece together what happened |
| Stealth | Move unseen, take small items, follow unnoticed |
| Swordsmanship | Fight with blades, from brawls to duels |

### World
| Term | Meaning |
|---|---|
| Ravenford | Rain-slick market town where the slice and MVP are set |
| Lantern Inn | Roadhouse inn run by Marla; the social hub |
| Greywood Forest | Pine forest between the road and the monastery |
| Old Monastery | Half-ruined house of the Order of the Quiet Bell |
| Northern Road | Muddy trade road; site of the ambush encounter |
| Ashen Merchant Guild | Trading faction buying silver above market rate |
| Order of the Quiet Bell | Indebted monastic order hiding tunnel smuggling |
| Ravenford Watch | Undermanned town guard seeking the missing travelers |
| Quiet Bell | The monastery's cracked, un-rung bell; order's emblem |
| Silver rings / silver | Plain currency; never a licensed coin name |

### Systems
| Term | Meaning |
|---|---|
| Story lead | A tracked mystery with stages and clues (not a "quest log" clone) |
| Lead screen | UI listing leads, stages, and found/total clues |
| Pack screen | UI listing carried items, silver, and skill ratings |
| Memory thread | An NPC's remembered facts about the player |

## Never use (proprietary or licensed — listed here only as bans, not content)

Beholders, mind flayers, yuan-ti, slaadi, gith, Vecna, Mordenkainen,
Faerûn, Waterdeep, Eberron, Dragonlance, drow-by-name lore, named spells or
named magic items from any published system, and any copied stat block or
flavor text. This list names them solely to forbid them.

## Provenance check

`backend/tests/test_slice_*.py` and `test_playtest.py` assert that content
terms come from this glossary's families and that no banned string appears
in `backend/app/content/**` or `docs/**`.
