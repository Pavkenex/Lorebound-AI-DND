"""Context assembly + budget controller (spec §4).

Contract (R5 card). Per turn, build the prompt under a hard token budget with
the spec's priority order (highest first):
  1. system/ruleset (static, cacheable; mark for provider-side caching)
  2. current scene structured state (location, present NPCs, player) — always
  3. this turn's resolved mechanical outcome — always (never dropped)
  4. pinned facts relevant to scene — never dropped once included
  5. NPC memory for NPCs present, top-K by salience
  6. relevant lead state
  7. chronicle tail (last 12 structured / last 2-3 verbatim)
  8. saga digest (campaign by default; arc/session only when relevance flags)
Over budget: drop from the bottom first; truncate digest/lower-salience memory
before anything pinned. ``accounting`` records per-section token estimates,
the budget, and exactly what was dropped (telemetry for spec §9).

Interfaces — what this module expects from its collaborators
------------------------------------------------------------
``scene`` — cheap reads supplied by the caller (the orchestrator), never
fetched here::

    {
      "ruleset": str,                      # optional static-block override
      "location": {"name", "description_static", "connections", ...} | str,
      "present_npcs": [{"id"|"npc_id", "name", "alive", "mood": {...},
                        "disposition"|"disposition_base", ...}, ...],
      "player": {"name", "stats": {...}, "inventory": [...],
                 "status_effects": [...], ...},
    }

``components`` — a ``memory.MemoryBundle`` (or a double with the same method
surface). Context calls exactly four methods:

* ``facts.pinned_facts()`` — pinned world facts. The §3.6 relevance gate
  (``config.memory.min_pin_relevance``) is applied HERE because the §4 budget
  controller owns the include/drop decision. Non-pinned facts stay retrievable
  but are not force-included (§3.6), so they are not injected by default.
* ``npc_memory.retrieve(npc_id=…, scene_context=…, turn=…, k=…)`` — for NPCs
  PRESENT in the scene only, top-K by salience (``config.memory.npc_top_k``).
* ``chronicle.tail(n=…)`` — the structured window, newest last.
* ``saga.injectable(scene_context=…, turn=…)`` — the digest level decision
  (campaign by default, arc/session when relevance flags) stays in memory.

Moods/ledger are NOT read here: the caller passes the cheap per-NPC reads
(mood, disposition) inside ``scene`` — a prompt-assembly concern, not a store
walk.

``relevant`` (optional) — caller-supplied relevance hints:

* ``"scene_context"``: list[str] (or str) appended to the derived scene
  context used for relevance scoring (pins, leads);
* ``"pinned"``: list[WorldFact] to use instead of ``facts.pinned_facts()``.

Unknown keys are ignored.
"""
from __future__ import annotations

import json
from typing import Any

from .config import EngineConfig
from .models import (
    AssembledPrompt,
    Intent,
    Lead,
    LeadStage,
    MechanicalOutcome,
    SagaRow,
    from_row,
)
from .similarity import LexicalSimilarity, Similarity, best_context_score

# Static, cacheable system/ruleset block, used when the caller supplies no
# ``scene["ruleset"]``. Deliberately short: the production text is
# ``pipeline.default_ruleset_text()`` (R7) and reaches context via ``scene``;
# this default keeps assembly usable on its own (tests, evals, stubs).
DEFAULT_RULESET_TEXT = (
    "You are the narrator of a tabletop campaign. Code owns the game state; "
    "you own the prose.\n"
    "Narrate the mechanical outcome you are given — never invent, re-roll or "
    "overrule a check, and never decide for yourself whether an action worked.\n"
    "Second person, present tense, 2-4 short paragraphs; every NPC speaks in "
    "their own voice and honors every pinned fact.\n"
    "Propose state changes only through the provided tool calls — never write "
    "state into prose the validators cannot see."
)

# Dynamic sections in spec priority order (the order they are rendered in).
_SECTION_TITLES: dict[str, str] = {
    "scene": "Scene",
    "mechanics": "Resolved outcome",
    "pinned_facts": "Pinned facts",
    "npc_memory": "NPC memory",
    "leads": "Leads",
    "chronicle": "Chronicle",
    "saga": "Saga",
}
_DYNAMIC_SECTIONS: tuple[str, ...] = tuple(_SECTION_TITLES)
# Every accounted section, system first (it is delivered via the system role).
_ACCOUNTING_SECTIONS: tuple[str, ...] = ("system", *_DYNAMIC_SECTIONS)
# Telemetry order for the dropped list: lowest priority first.
_DROP_ORDER: tuple[str, ...] = (
    "saga", "chronicle", "leads", "npc_memory", "scene", "system",
)
# A partial item shorter than this is not worth injecting — drop instead.
_MIN_PARTIAL_TOKENS = 4
_TRUNCATION_MARKER = "…"
# Keep priority of saga levels: the campaign digest outlives arc/session detail.
_SAGA_LEVEL_RANK: dict[str, int] = {"campaign": 2, "arc": 1, "session": 0}


def _pct(total: int, pct: int) -> int:
    """Floor percentage in integer math — deterministic budget arithmetic."""
    return int(total) * int(pct) // 100


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _render_inventory(items: Any) -> str:
    parts: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            item_id = str(item.get("item_id") or item.get("name") or item.get("id") or "?")
            parts.append(f"{item_id} x{item.get('qty', 1)}")
        else:
            parts.append(str(item))
    return ", ".join(parts)


def _render_statuses(statuses: Any) -> str:
    parts: list[str] = []
    for status in statuses or []:
        if isinstance(status, dict):
            parts.append(str(status.get("status") or status.get("name") or status))
        else:
            parts.append(str(status))
    return ", ".join(parts)


class ContextAssembler:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 *, sim: Similarity | None = None, components: Any = None) -> None:
        """``components`` — a ``memory.MemoryBundle`` (or a test double with the
        same method surface; memory lands in parallel with context). When None,
        built from ``store`` lazily on first use."""
        self.store = store
        self.config = config or EngineConfig()
        self.sim = sim or LexicalSimilarity()
        self.components = components

    def estimate_tokens(self, text: str) -> int:
        """Deterministic heuristic (~4 chars/token); overridable per provider."""
        return max(1, (len(text) + 3) // 4)

    def assemble(self, *, turn: int, scene: dict,
                 mechanical: MechanicalOutcome | None, intent: Intent,
                 context_window: int | None = None,
                 relevant: dict | None = None) -> AssembledPrompt:
        """Build the full prompt for Pass B. ``scene`` carries cheap reads
        (location, present_npcs, player); retrieval of memory/facts/leads and
        all budget math happen here.

        Budget model (``config.budget``, percentages of the context window):
        the window (param > provider capability row > config default) minus the
        ``output_reserve_pct`` reserve is the hard prompt budget; the static
        system block is capped at ``system_pct`` (never above
        ``system_max_pct``); the remainder splits ``group_scene_pct`` /
        ``group_memory_pct`` / ``group_continuity_pct``. The mechanical outcome
        and the pinned facts are non-negotiable: when they outgrow the scene
        group they take room from the lower groups (continuity first, then
        memory) and the scene group runs at their actual size. Groups are
        silos — slack is never moved between them; within a group the
        lowest-priority section is filled last, so it is truncated/dropped
        first. ``accounting["over_budget"]`` is set only in the degenerate case
        where the non-negotiables alone exceed the prompt budget.

        ``accounting``::

            {"budget": {"window", "output_reserve", "total", "used",
                        "remaining", "over_budget"},
             "allocations": {"system", "scene", "memory", "continuity"},
             "compensation": {"continuity", "memory"},
             "sections": {<section>: tokens, …},   # canonical order, 0 = absent
             "dropped": [{"section", "reason", "kept_tokens", "dropped_tokens"}],
             "cacheable": ["system"]}

        ``dropped`` is ordered by drop priority (saga → chronicle → leads →
        npc_memory → scene → system); identical inputs produce identical
        output, including the dropped list.
        """
        scene = scene if isinstance(scene, dict) else {}
        intent = intent or Intent()
        relevant = relevant if isinstance(relevant, dict) else {}

        window = self._resolve_window(context_window)
        plan = self._budget_plan(window)
        scene_context = self._scene_context(scene, intent, relevant)

        dropped: dict[str, dict[str, Any]] = {}

        # 1. static system/ruleset block (cacheable, never dropped).
        system_text = self._system_text(scene)
        system_total = self.estimate_tokens(system_text)
        if system_total > plan["system"]:
            system_text = self._truncate_text(system_text, plan["system"])
            self._record_drop(
                dropped, "system", "system_ceiling",
                self.estimate_tokens(system_text), system_total,
            )

        # 2/3. non-negotiables: this turn's mechanical outcome + pinned facts.
        mechanics_text = self._section_text("mechanics", self._mechanics_body(mechanical))
        pinned_items = self._pinned_items(scene_context, relevant)
        pinned_text = self._section_text(
            "pinned_facts", "\n".join(text for text, _ in pinned_items)
        )
        nonneg = self.estimate_tokens(mechanics_text) + self.estimate_tokens(pinned_text)

        # 2b. a scene-group overrun is paid for by the lower groups, bottom up.
        allocations = {name: plan[name] for name in ("system", "scene", "memory", "continuity")}
        compensation = {"continuity": 0, "memory": 0}
        over = nonneg - allocations["scene"]
        if over > 0:
            for group in ("continuity", "memory"):
                taken = min(over, allocations[group])
                allocations[group] -= taken
                compensation[group] += taken
                over -= taken
            allocations["scene"] = nonneg
        over_budget = over > 0

        # 4. scene state — whatever the scene group has left after the pins.
        scene_text, _ = self._fit_section(
            "scene", self._scene_items(scene),
            allocations["scene"] - nonneg, dropped,
        )

        # 5. memory group: NPC memory (higher) then relevant leads (lower).
        npc_text, npc_used = self._fit_section(
            "npc_memory", self._npc_items(scene, scene_context, turn),
            allocations["memory"], dropped,
        )
        leads_text, _ = self._fit_section(
            "leads", self._lead_items(scene, scene_context),
            allocations["memory"] - npc_used, dropped,
        )

        # 6. continuity group: chronicle tail (higher) then saga digest (lower).
        chronicle_text, chronicle_used = self._fit_section(
            "chronicle", self._chronicle_items(), allocations["continuity"], dropped,
        )
        saga_text, _ = self._fit_section(
            "saga", self._saga_items(scene_context, turn),
            allocations["continuity"] - chronicle_used, dropped,
        )

        parts = {
            "scene": scene_text,
            "mechanics": mechanics_text,
            "pinned_facts": pinned_text,
            "npc_memory": npc_text,
            "leads": leads_text,
            "chronicle": chronicle_text,
            "saga": saga_text,
        }
        sections = [(name, parts[name]) for name in _DYNAMIC_SECTIONS if parts[name]]
        text = "\n\n".join(section for _, section in sections)

        per_section = {"system": self.estimate_tokens(system_text)}
        for name in _DYNAMIC_SECTIONS:
            per_section[name] = self.estimate_tokens(parts[name]) if parts[name] else 0
        used = sum(per_section.values())
        accounting = {
            "budget": {
                "window": window,
                "output_reserve": plan["reserve"],
                "total": plan["prompt_budget"],
                "used": used,
                "remaining": plan["prompt_budget"] - used,
                "over_budget": over_budget,
            },
            "allocations": {name: allocations[name]
                            for name in ("system", "scene", "memory", "continuity")},
            "compensation": {name: compensation[name] for name in ("continuity", "memory")},
            "sections": {name: per_section[name] for name in _ACCOUNTING_SECTIONS},
            "dropped": [dropped[name] for name in _DROP_ORDER if name in dropped],
            "cacheable": ["system"],
        }
        return AssembledPrompt(text=text, system=system_text,
                               sections=sections, accounting=accounting)

    # -- budget ------------------------------------------------------------ #

    def _resolve_window(self, context_window: int | None) -> int:
        """Param > provider capability row > ``budget.default_context_window``."""
        supplied = context_window if isinstance(context_window, int) and context_window > 0 else None
        if supplied is not None:
            return supplied
        probed = self._provider_window()
        if probed is not None:
            return probed
        return int(self.config.budget.default_context_window)

    def _provider_window(self) -> int | None:
        """Context window from the cached capability probe (``provider_caps``,
        spec §7), when present; ``None`` otherwise (no probe, malformed caps)."""
        store = self.store
        if store is None or not callable(getattr(store, "find_one", None)):
            return None
        try:
            row = store.find_one("provider_caps", order_by="probed_at DESC")
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        if not row:
            return None
        caps = row.get("caps")
        if isinstance(caps, str):
            try:
                caps = json.loads(caps)
            except ValueError:
                return None
        if not isinstance(caps, dict):
            return None
        window = caps.get("context_window")
        if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
            return None
        return window

    def _budget_plan(self, window: int) -> dict[str, int]:
        """Split ``window`` per ``config.budget`` (spec §4 practical numbers)."""
        cfg = self.config.budget
        reserve = _pct(window, cfg.output_reserve_pct)
        prompt_budget = window - reserve
        system_budget = _pct(window, min(int(cfg.system_pct), int(cfg.system_max_pct)))
        remaining = max(0, prompt_budget - system_budget)
        return {
            "reserve": reserve,
            "prompt_budget": prompt_budget,
            "system": system_budget,
            "scene": _pct(remaining, cfg.group_scene_pct),
            "memory": _pct(remaining, cfg.group_memory_pct),
            "continuity": _pct(remaining, cfg.group_continuity_pct),
        }

    # -- fitting / truncation ---------------------------------------------- #

    @staticmethod
    def _section_text(name: str, body: str) -> str:
        """``# Title\\nbody`` — empty body means the section is absent."""
        if not body:
            return ""
        return f"# {_SECTION_TITLES[name]}\n{body}"

    def _fit_section(self, name: str, items: list[tuple[str, float]], room: int,
                     dropped: dict[str, dict[str, Any]]) -> tuple[str, int]:
        """Fit a section's items into ``room`` tokens (header included).

        Returns ``(section_text, used_tokens)``; records the section in
        ``dropped`` when anything was cut. Items are taken by keep priority
        (ties: render order), the render order is preserved in the output.
        """
        if not items:
            return "", 0
        total = self.estimate_tokens(
            self._section_text(name, "\n".join(text for text, _ in items))
        )
        header_tokens = self.estimate_tokens(f"# {_SECTION_TITLES[name]}\n")
        body_room = room - header_tokens
        selected: dict[int, str] = {}
        used_body = 0
        if body_room > 0:
            order = sorted(range(len(items)), key=lambda i: (-items[i][1], i))
            for index in order:
                text = items[index][0]
                cost = self.estimate_tokens(text)
                if used_body + cost <= body_room:
                    selected[index] = text
                    used_body += cost
                    continue
                partial = self._truncate_text(text, body_room - used_body)
                if partial:
                    selected[index] = partial
                    used_body += self.estimate_tokens(partial)
                break
        if not selected:
            self._record_drop(dropped, name, "over_budget", 0, total)
            return "", 0
        text = self._section_text(name, "\n".join(selected[i] for i in sorted(selected)))
        used = self.estimate_tokens(text)
        if used < total:
            self._record_drop(dropped, name, "over_budget", used, total)
        return text, used

    def _truncate_text(self, text: str, max_tokens: int) -> str:
        """Word-boundary truncation with a trailing marker; result ≤ budget."""
        if max_tokens <= 0:
            return ""
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        head = text[: max(1, max_chars - len(_TRUNCATION_MARKER))]
        cut = head.rsplit(" ", 1)[0].rstrip() if " " in head else head.strip()
        return f"{cut}{_TRUNCATION_MARKER}" if cut else ""

    @staticmethod
    def _record_drop(dropped: dict[str, dict[str, Any]], section: str,
                     reason: str, kept: int, total: int) -> None:
        dropped[section] = {
            "section": section,
            "reason": reason,
            "kept_tokens": kept,
            "dropped_tokens": max(0, total - kept),
        }

    # -- memory / store access --------------------------------------------- #

    def _memory(self) -> Any:
        """The ``memory.MemoryBundle`` (or double) — built lazily from the store."""
        if self.components is None:
            from .memory import MemoryBundle  # local: memory lands in parallel

            self.components = MemoryBundle.build(self.store, self.config, self.sim)
        return self.components

    def _lead_rows(self) -> list[Lead]:
        """Leads from the state store, decoded via ``models.from_row`` (the raw
        rows carry ``related_npc_ids`` / ``stage_history`` as JSON text)."""
        store = self.store
        if store is None or not callable(getattr(store, "find", None)):
            return []
        return [from_row(Lead, row) for row in store.find("leads") if isinstance(row, dict)]

    # -- section builders --------------------------------------------------- #

    def _scene_context(self, scene: dict, intent: Intent, relevant: dict) -> list[str]:
        """Ordered, de-duplicated context strings relevance scoring runs against
        (current input first — the strongest signal)."""
        parts: list[str] = []
        if intent.text and intent.text.strip():
            parts.append(intent.text.strip())
        if intent.target:
            parts.append(str(intent.target))
        location = scene.get("location")
        if isinstance(location, dict):
            for key in ("name", "description_static"):
                if location.get(key):
                    parts.append(str(location[key]))
        elif isinstance(location, str) and location.strip():
            parts.append(location.strip())
        for npc in self._present_npcs(scene):
            if npc.get("name"):
                parts.append(str(npc["name"]))
            personality = npc.get("personality")
            if isinstance(personality, dict) and personality.get("tone"):
                parts.append(str(personality["tone"]))
        extra = relevant.get("scene_context")
        if isinstance(extra, str):
            extra = [extra]
        for item in extra or []:
            if item:
                parts.append(str(item))
        seen: set[str] = set()
        out: list[str] = []
        for part in parts:
            key = part.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    def _system_text(self, scene: dict) -> str:
        ruleset = scene.get("ruleset")
        if isinstance(ruleset, str) and ruleset.strip():
            return ruleset.strip()
        return DEFAULT_RULESET_TEXT

    @staticmethod
    def _present_npcs(scene: dict) -> list[dict]:
        raw = scene.get("present_npcs") or []
        if not isinstance(raw, (list, tuple)):
            return []
        out: list[dict] = []
        for entry in raw:
            if isinstance(entry, dict):
                out.append(entry)
            elif isinstance(entry, str) and entry:
                out.append({"id": entry})
        return out

    @staticmethod
    def _npc_key(npc: dict) -> str:
        for key in ("id", "npc_id", "npcId"):
            value = npc.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _npc_line(npc: dict) -> str:
        name = str(npc.get("name") or npc.get("id") or npc.get("npc_id") or "?")
        bits: list[str] = []
        if npc.get("alive") is False:
            bits.append("dead")
        mood = npc.get("mood")
        if isinstance(mood, dict):
            bits.append(
                f"mood {_as_float(mood.get('valence')):+.2f}/"
                f"{_as_float(mood.get('arousal')):+.2f}"
            )
        elif mood is not None:
            bits.append(f"mood {mood}")
        disposition = npc.get("disposition", npc.get("disposition_base"))
        if isinstance(disposition, bool) or disposition is None:
            disposition = None
        if isinstance(disposition, (int, float)):
            bits.append(f"disposition {disposition:+.0f}")
        elif isinstance(disposition, str) and disposition:
            bits.append(f"disposition {disposition}")
        return f"- {name}" + (f" ({'; '.join(bits)})" if bits else "")

    @staticmethod
    def _player_lines(player: dict) -> list[str]:
        name = str(player.get("name") or "player")
        stats = player.get("stats")
        stats = stats if isinstance(stats, dict) else {}
        head = f"Player: {name}"
        hp, max_hp = stats.get("hp"), stats.get("max_hp")
        if hp is not None:
            head += f" — hp {hp}" + (f"/{max_hp}" if max_hp is not None else "")
        if stats.get("currency") is not None:
            head += f", currency {stats['currency']}"
        lines = [head]
        if player.get("status_effects"):
            lines.append("Status: " + _render_statuses(player["status_effects"]))
        inventory = _render_inventory(player.get("inventory"))
        if inventory:
            lines.append("Carrying: " + inventory)
        return lines

    def _scene_items(self, scene: dict) -> list[tuple[str, float]]:
        lines: list[str] = []
        location = scene.get("location")
        if isinstance(location, dict):
            name = str(location.get("name") or location.get("id") or "")
            description = str(location.get("description_static") or location.get("description") or "")
            head = f"Location: {name}" if name else "Location:"
            if description:
                head += f" — {description}"
            lines.append(head)
            connections = location.get("connections")
            if connections:
                lines.append("Exits: " + ", ".join(str(c) for c in connections))
        elif isinstance(location, str) and location.strip():
            lines.append(f"Location: {location.strip()}")
        npcs = self._present_npcs(scene)
        if npcs:
            lines.append("Present NPCs:")
            lines.extend(self._npc_line(npc) for npc in npcs)
        player = scene.get("player")
        if isinstance(player, dict) and player:
            lines.extend(self._player_lines(player))
        return [(line, float(-index)) for index, line in enumerate(lines)]

    @staticmethod
    def _delta_line(delta: Any) -> str:
        data = getattr(delta, "data", None) or {}
        payload = ", ".join(f"{key}={data[key]}" for key in sorted(data, key=str))
        line = str(getattr(delta, "kind", ""))
        target = getattr(delta, "target", "")
        if target:
            line += f" on {target}"
        if payload:
            line += f": {payload}"
        reason = getattr(delta, "reason", "")
        if reason:
            line += f" ({reason})"
        return line

    def _mechanics_body(self, mechanical: MechanicalOutcome | None) -> str:
        if mechanical is None:
            return "No mechanical resolution this turn."
        lines = [f"Outcome: {mechanical.kind}"]
        if mechanical.label:
            lines[0] += f" — {mechanical.label}"
        if mechanical.verdict_line:
            lines.append(f"Verdict: {mechanical.verdict_line}")
        check = mechanical.check
        if check is not None:
            request = check.request
            skill = request.skill if request is not None else ""
            line = f"Check: {skill} — roll {check.roll} {check.modifier:+d} = {check.total}"
            if request is not None:
                line += f" vs DC {request.dc}"
            lines.append(f"{line} → {check.band}")
        for effect in mechanical.effects or []:
            lines.append(f"- {self._delta_line(effect)}")
        for note in mechanical.notes or []:
            lines.append(f"Note: {note}")
        return "\n".join(lines)

    def _pinned_items(self, scene_context: list[str], relevant: dict) -> list[tuple[str, float]]:
        if relevant.get("pinned") is not None:
            facts = list(relevant["pinned"])
        else:
            facts = list(self._memory().facts.pinned_facts() or [])
        gate = self.config.memory.min_pin_relevance
        scored: list[tuple[float, int, str]] = []
        for fact in facts:
            statement = str(getattr(fact, "statement", "") or "")
            if not statement:
                continue
            relevance = best_context_score(statement, scene_context, self.sim)
            if relevance < gate:
                continue
            scored.append((relevance, int(getattr(fact, "id", 0) or 0), statement))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [(f"- {statement}", float(-index))
                for index, (_, _, statement) in enumerate(scored)]

    def _npc_items(self, scene: dict, scene_context: list[str],
                   turn: int) -> list[tuple[str, float]]:
        """Top-K memory per PRESENT NPC only — never a global top-N."""
        items: list[tuple[str, float]] = []
        for npc in self._present_npcs(scene):
            npc_key = self._npc_key(npc)
            if not npc_key:
                continue
            name = str(npc.get("name") or npc_key)
            retrieved = self._memory().npc_memory.retrieve(
                npc_id=npc_key, scene_context=scene_context, turn=turn,
                k=self.config.memory.npc_top_k,
            )
            ranked: list[tuple[float, int, str]] = []
            for pair in retrieved or []:
                # memory.py's contract is (entry, score) pairs; a bare entry is
                # tolerated so a stub/double cannot break assembly.
                entry, score = pair if isinstance(pair, tuple) else (pair, 0.0)
                statement = str(getattr(entry, "statement", "") or "")
                if not statement:
                    continue
                entry_type = str(getattr(entry, "type", "") or "")
                text = f"- {name}: [{entry_type}] {statement}" if entry_type else f"- {name}: {statement}"
                ranked.append((_as_float(score), int(getattr(entry, "id", 0) or 0), text))
            ranked.sort(key=lambda row: (-row[0], row[1]))
            for _, _, text in ranked:
                items.append((text, float(-len(items))))
        return items

    def _lead_items(self, scene: dict, scene_context: list[str]) -> list[tuple[str, float]]:
        present = {self._npc_key(npc) for npc in self._present_npcs(scene)} - {""}
        gate = self.config.memory.min_pin_relevance
        scored: list[tuple[float, int, Lead, set[str]]] = []
        for lead in self._lead_rows():
            stage = str(lead.stage or "")
            if stage == LeadStage.UNHEARD.value:
                continue  # the player has not heard of it — never leak it
            title = str(lead.title or "")
            if not title:
                continue
            related = {str(entry) for entry in (lead.related_npc_ids or [])} & present
            relevance = 1.0 if related else best_context_score(title, scene_context, self.sim)
            if relevance < gate:
                continue
            scored.append((relevance, int(lead.id or 0), lead, related))
        scored.sort(key=lambda item: (-item[0], item[1]))
        items: list[tuple[str, float]] = []
        for position, (_, _, lead, related) in enumerate(scored):
            text = f"- {lead.title} — stage: {lead.stage}"
            if related:
                text += f" (related: {', '.join(sorted(related))})"
            history = lead.stage_history or []
            if history and isinstance(history[-1], dict) and history[-1].get("trigger"):
                text += f"; latest: {history[-1]['trigger']}"
            items.append((text, float(-position)))
        return items

    def _chronicle_items(self) -> list[tuple[str, float]]:
        window = self.config.memory.chronicle_window
        entries = self._memory().chronicle.tail(n=window)
        items: list[tuple[str, float]] = []
        for index, entry in enumerate(entries or []):
            head = f"[turn {entry.turn_id}]"
            if entry.actor:
                head += f" {entry.actor}"
            text = f"{head}: {entry.action_summary}" if entry.action_summary else head
            if entry.mechanical_result:
                text += f" → {entry.mechanical_result}"
            if entry.consequence_oneliner:
                text += f" — {entry.consequence_oneliner}"
            if entry.verbatim_text:
                text += f'\n  "{entry.verbatim_text}"'
            # keep priority = recency (tail is oldest-first): newest survives.
            items.append((text, float(index)))
        return items

    def _saga_items(self, scene_context: list[str], turn: int) -> list[tuple[str, float]]:
        rows: list[SagaRow] = list(self._memory().saga.injectable(
            scene_context=scene_context, turn=turn,
        ) or [])
        ranked = sorted(
            enumerate(rows),
            key=lambda pair: (-_SAGA_LEVEL_RANK.get(str(pair[1].level), 0), pair[0]),
        )
        items: list[tuple[str, float]] = []
        for position, (_, row) in enumerate(ranked):
            text = f"[{row.level}] {row.text}"
            if row.references:
                text += f" (refs: {', '.join(str(ref) for ref in row.references)})"
            # campaign digest outlives arc/session detail under pressure.
            items.append((text, float(len(ranked) - position)))
        return items
