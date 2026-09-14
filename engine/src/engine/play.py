"""Playable session + deterministic stub narrator (spec §1, §10.1).

The stub narrator makes the engine fully playable with NO model and no key:
it narrates from the resolved mechanical outcome + scene state via templates
(same ``ProposalSet`` envelope as live models; emits no state deltas by
default). ``PlaySession`` is the thin player-facing wrapper the CLI uses.

The stub reads the *assembled prompt* (its section bodies), never the store, so
it exercises exactly the surface a live narrator gets. It deliberately invents
no dialogue: a template cannot know who may speak, and guessing is how a dead
NPC ends up with a line. Scripted envelopes (``StubNarrator(script=[...])``)
cover the cases tests need to pin.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .fixtures.demo_world import demo_world as _demo_world
from .fixtures.demo_world import seed_world as _seed_world
from .models import (
    AssembledPrompt,
    Location,
    OutcomeBand,
    ProposalSet,
    ProviderConfig,
    TurnResult,
    from_row,
)
from .models import (
    Character as CharacterRow,
)
from .pipeline import Orchestrator, narrator_from_adapter
from .providers.jsonproto import proposals_from_payload
from .resolve import SeededRng
from .store import Store


def demo_world() -> dict:
    """Starter world for play/tests (content lives in ``fixtures/demo_world``)."""
    return _demo_world()


def seed_world(store: Any, world: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Insert a fixture world (see ``fixtures.seed_world``)."""
    return _seed_world(store, world)


# --------------------------------------------------------------------------- #
# Stub narrator
# --------------------------------------------------------------------------- #

_SECTION_RE = re.compile(r"^# (?P<title>.+)$")

# Turn-indexed phrasing so a long offline run does not read as one loop; the
# index is the turn number, so a rerun of the same campaign is word-identical.
_OPENERS = (
    "You take in {location}.",
    "You are still at {location}, and the light has not changed.",
    "{location} waits around you.",
)
_CLOSERS = (
    "The scene holds, waiting to see what you do next.",
    "Whatever comes next, the world will not move first.",
    "The moment stays open; the next move is yours.",
)

_BAND_PROSE: dict[str, str] = {
    OutcomeBand.SUCCESS.value: (
        "{label} goes through cleanly. The world answers plainly, and for this "
        "moment the odds are on your side."
    ),
    OutcomeBand.SUCCESS_AT_COST.value: (
        "{label} works — and takes its price on the way through. Whatever you "
        "won, the scene keeps a piece of it."
    ),
    OutcomeBand.FAILURE.value: (
        "{label} does not land. The moment turns against you, and the world "
        "hands back a complication instead of a result."
    ),
    OutcomeBand.CRITICAL.value: (
        "{label} lands perfectly, past every objection the moment could raise."
    ),
    OutcomeBand.CRITICAL_FAILURE.value: (
        "{label} goes wrong from the first motion, and the wreck carries "
        "further than you meant."
    ),
}


class StubNarrator:
    """NarratorRunner-compatible, deterministic, offline (used by --stub, tests, evals).

    ``script`` is an optional list of envelopes consumed one per turn — either
    ready ``ProposalSet`` objects or JSON-protocol payload mappings (decoded by
    the same codec live models use, so malformed proposals behave identically in
    both paths). When the script runs out, the template narrator takes over.
    """

    provider_name = "stub"
    model_name = "stub"

    def __init__(self, script: Iterable[Any] | None = None) -> None:
        self.script: list[Any] = list(script or [])
        self.calls: list[dict] = []
        self.last_notes: list[str] = []

    def narrate(self, *, prompt: AssembledPrompt, turn: int,
                retry_note: str | None = None) -> ProposalSet:
        self.calls.append({"turn": turn, "retry_note": retry_note, "prompt": prompt})
        self.last_notes = []
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, ProposalSet):
                return item
            return proposals_from_payload(dict(item), notes=self.last_notes)
        return ProposalSet(
            narration=self._template(prompt, retry_note, turn),
            npc_dialogue=[],
            deltas=[],
        )

    def stats(self) -> dict:
        return {"kind": "stub", "calls": len(self.calls),
                "script_remaining": len(self.script)}

    # -- template ---------------------------------------------------------- #

    def _template(self, prompt: AssembledPrompt, retry_note: str | None,
                  turn: int) -> str:
        mechanics = _section_body(prompt, "mechanics")
        outcome_kind, label = _outcome_of(mechanics)
        band = _band_of(mechanics)
        location, living, dead = _scene_of(prompt)

        if outcome_kind == "blocked":
            first = (
                "The way is barred before you even start: the scene will not "
                "allow it, and nothing you do here changes that."
            )
        elif band in _BAND_PROSE:
            first = _BAND_PROSE[band].format(label=_prose_label(label, outcome_kind))
        elif label == "dialogue":
            first = (
                "Your words go out into the scene and the moment simply holds — "
                "whatever answer comes will have to be earned."
            )
        else:
            first = (
                "Nothing in the world shifts for that; the scene simply waits "
                "for something that can be attempted."
            )

        opener_bits: list[str] = []
        if location:
            opener_bits.append(_OPENERS[(turn - 1) % len(_OPENERS)].format(location=location))
        for name in living:
            opener_bits.append(f"{name} is here with you.")
        for name in dead:
            opener_bits.append(f"{name} lies still where the scene left them.")
        opener = " ".join(opener_bits)

        if retry_note:
            # A correction round gets the corrected facts and nothing else:
            # fewer sentences means fewer chances to repeat the mistake.
            return f"{opener}\n\n{first}".strip()
        closer = _CLOSERS[(turn - 1) % len(_CLOSERS)]
        return f"{opener}\n\n{first}\n\n{closer}".strip()


def _section_body(prompt: AssembledPrompt, name: str) -> str:
    for section, body in getattr(prompt, "sections", None) or []:
        if section == name:
            return str(body)
    return ""


def _outcome_of(mechanics_body: str) -> tuple[str, str]:
    for line in mechanics_body.splitlines():
        if line.startswith("Outcome:"):
            rest = line[len("Outcome:"):].strip()
            kind, _, label = rest.partition(" — ")
            return kind.strip(), label.strip()
    return "", ""


def _band_of(mechanics_body: str) -> str:
    for line in mechanics_body.splitlines():
        if line.startswith("Check:") and "→" in line:
            return line.rsplit("→", 1)[1].strip()
    return ""


def _scene_of(prompt: AssembledPrompt) -> tuple[str, list[str], list[str]]:
    """``(location, living names, dead names)`` from the rendered scene section."""
    location = ""
    living: list[str] = []
    dead: list[str] = []
    in_npcs = False
    for line in _section_body(prompt, "scene").splitlines():
        if line.startswith("Location:"):
            location = line[len("Location:"):].split("—", 1)[0].strip()
            in_npcs = False
        elif line.strip() == "Present NPCs:":
            in_npcs = True
        elif line.startswith("Player:") or line.startswith("Carrying:") \
                or line.startswith("Status:") or line.startswith("Exits:"):
            in_npcs = False
        elif in_npcs and line.strip().startswith("- "):
            entry = line.strip()[2:]
            name = entry.split(" (", 1)[0].strip()
            if not name:
                continue
            (dead if "(dead)" in entry else living).append(name)
    return location, living, dead


def _prose_label(label: str, kind: str) -> str:
    """Narrator-friendly rendering of a mechanics label (no raw row keys)."""
    text = re.sub(r"\bnpc[:_-]?\d+\b", "your mark", str(label or "")).strip()
    text = text or {"attack": "attack", "check": "attempt"}.get(kind, "attempt")
    if not text[:1].isupper():
        return f"Your {text}"
    return text


# --------------------------------------------------------------------------- #
# Play session
# --------------------------------------------------------------------------- #

class PlaySession:
    """One campaign session bound to a SQLite DB.

    ``start()`` seeds the world (from ``world`` dict or the demo fixture) when
    the DB is empty and is idempotent on an existing campaign.
    """

    def __init__(self, *, store: Store, config: EngineConfig,
                 orchestrator: Orchestrator, turn: int) -> None:
        self.store = store
        self.config = config
        self.orchestrator = orchestrator
        self.turn = turn

    @classmethod
    def start(cls, *, db_path: str | Path, world: dict | None = None,
              config: EngineConfig | None = None,
              narrator: Any = None, rng: Any = None, adapter: Any = None,
              provider: ProviderConfig | None = None,
              content_policy: str | None = None,
              auto_seed: bool = True, probe: bool = True) -> PlaySession:
        """``content_policy`` is the code-owned content-boundary directive every
        assembled prompt carries on its system line (plan §11; empty = none)."""
        config = config or EngineConfig()
        store = Store(db_path)
        if auto_seed and store.count("world") == 0:
            _seed_world(store, demo_world() if world is None else world)
        if narrator is None:
            if adapter is not None:
                narrator = narrator_from_adapter(
                    adapter, provider, store=store, config=config, probe=probe,
                )
            else:
                narrator = StubNarrator()
        world_row = store.find_one("world", order_by="id")
        seed = (world_row or {}).get("seed") or config.db_path or "lorebound"
        orchestrator = Orchestrator(
            store, config, narrator,
            rng=rng if rng is not None else SeededRng(seed),
            adapter=adapter, provider=provider,
            context_window=config.budget.default_context_window,
            content_policy=content_policy,
        )
        return cls(store=store, config=config, orchestrator=orchestrator,
                   turn=cls._next_turn(store))

    @staticmethod
    def _next_turn(store: Store) -> int:
        """Next turn number = max(turn_log.turn) + 1 (1 on a fresh campaign)."""
        rows = store.find("turn_log", order_by="turn")
        last = int(rows[-1]["turn"]) if rows else 0
        return max(last, 0) + 1

    @property
    def narrator(self) -> Any:
        """The narrator this session drives (scriptable when it is a stub)."""
        return self.orchestrator.narrator

    def act(self, player_input: str) -> TurnResult:
        result = self.orchestrator.take_turn(player_input=player_input,
                                             turn=self.turn)
        self.turn += 1
        return result

    def state_view(self) -> dict:
        """Small JSON-able snapshot: turn, location, HP, inventory, top leads."""
        character_row = self.store.find_one("characters", order_by="id")
        character = from_row(CharacterRow, character_row) if character_row else None
        stats = dict(character.stats or {}) if character else {}
        location = self._location_view(str(character.location_id or "") if character else "")
        leads = [
            {"id": int(row["id"]), "title": str(row.get("title") or ""),
             "stage": str(row.get("stage") or "")}
            for row in self.store.find("leads", order_by="id")
        ]
        return {
            "turn": self.turn - 1,
            "next_turn": self.turn,
            "location": location,
            "hp": stats.get("hp"),
            "max_hp": stats.get("max_hp"),
            "currency": stats.get("currency"),
            "inventory": list(character.inventory or []) if character else [],
            "status_effects": list(character.status_effects or []) if character else [],
            "present_npcs": [
                {"id": npc["id"], "name": npc["name"], "alive": npc["alive"],
                 "disposition": round(float(npc.get("disposition", 0.0)), 2)}
                for npc in self.orchestrator.present_npcs(self.turn)
            ],
            "leads": leads[:5],
            "pinned_facts": [
                fact.statement
                for fact in self.orchestrator.memory_bundle().facts.pinned_facts()
            ],
        }

    def _location_view(self, location_id: str) -> dict:
        row = self.orchestrator.location_row(location_id)
        if row is None:
            return {"id": location_id, "name": location_id}
        location = from_row(Location, row)
        return {
            "id": location_id,
            "name": location.name,
            "description_static": location.description_static,
            "connections": list(location.connections or []),
        }

    def close(self) -> None:
        self.store.close()
