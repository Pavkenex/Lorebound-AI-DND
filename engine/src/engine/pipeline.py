"""Turn orchestrator + multi-pass generation (spec §1, §5).

Contract (R7 card). ``Orchestrator.take_turn`` runs the full lifecycle:
  1. classify intent (rules-first; §1)
  2. Pass A: mechanics resolution (resolve.py) — before any model call
  3. context assembly (context.py)
  4. Pass B: narrator (live adapter or stub) -> ProposalSet
  5. Pass C: validate + commit (validate.py); memory subsystem updates
     (chronicle append, mood decay, saga maybe-summarize)
  6. Pass D: consistency check; on contradiction regenerate Pass B once with an
     injected correction note, else patch the narration deterministically
  7. append TurnLog + telemetry; return TurnResult
The model never adjudicates success and never writes state directly.

Interfaces (R8 and the evals harness consume these; keep them stable)
---------------------------------------------------------------------
* ``NarratorRunner.narrate(*, prompt, turn, retry_note=None) -> ProposalSet`` —
  implemented by :class:`LiveNarrator` (a provider adapter), by
  ``play.StubNarrator``, and by fakes in tests/evals. A narrator MAY expose
  ``last_notes: list[str]`` (one-line diagnostics, surfaced in system_lines).
* ``Orchestrator(store, config=None, narrator=None, *, rng=None, sim=None,
  components=None, assembler=None, adapter=None, provider=None,
  context_window=None, effect_rules=None, ruleset=None, dc=None, now=None)``.
* ``Orchestrator.classify_intent(text) -> Intent`` — rules-first, store-aware
  for NPC targets.
* ``Orchestrator.take_turn(*, player_input, turn) -> TurnResult``.
* ``Orchestrator.consistency_check(*, turn, narration, report, dialogue=None)
  -> (clean, problems)`` — deterministic scan; ``problems`` are stable one-line
  strings (the structured detail stays private and drives the sentence patch).
* ``default_ruleset_text() -> str`` — the static system block ``context.py``
  injects when the scene carries no ruleset override.

Pass C semantics worth knowing
------------------------------
The narrator's deltas AND any ``MechanicalOutcome.effects`` (R7 wires none by
default; the resolver may attach caller-supplied ones) are validated and
committed through the same ``Validator``. When Pass D forces a regeneration,
the regenerated set is validated the same way, but deltas whose canonical
identity (kind + target + data + reason) was already applied this turn are
skipped — a correction round must not double-apply a mood spike or a spend.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .config import EngineConfig
from .context import ContextAssembler
from .memory import MemoryBundle
from .models import (
    NPC as NPCRow,
)
from .models import (
    Character as CharacterRow,
)
from .models import (
    ChatMessage,
    ChatRequest,
    CommitReport,
    Delta,
    Intent,
    MechanicalOutcome,
    ProposalSet,
    ProviderConfig,
    TelemetryRow,
    TurnLog,
    TurnResult,
    VerdictKind,
    from_row,
    to_row,
)
from .providers.jsonproto import (
    build_json_protocol_prompt,
    delta_tool_schema,
    parse_with_repair,
    proposals_from_payload,
    proposals_from_tool_calls,
    regenerate_note,
    should_regenerate,
)
from .providers.registry import probe_capabilities
from .resolve import SeededRng, resolve_action
from .similarity import LexicalSimilarity, Similarity
from .validate import Validator, conflict_reason


class NarratorRunner(Protocol):
    """Anything that turns an assembled prompt into a ProposalSet: a live
    provider adapter wrapper, the StubNarrator, or an eval fake."""

    def narrate(self, *, prompt: Any, turn: int,
                retry_note: str | None = None) -> ProposalSet:
        ...


# --------------------------------------------------------------------------- #
# Static system/ruleset block (spec §4 priority #1; cacheable)
# --------------------------------------------------------------------------- #

RULESET_TEXT = (
    "You are the narrator of a tabletop campaign. Code owns the game state; "
    "you own the prose.\n"
    "- Narration is the only thing you invent. Every mechanical outcome in this "
    "prompt is final: narrate it exactly as resolved — never re-roll it, soften "
    "it, or decide for yourself whether an action worked.\n"
    "- Second person, present tense, 2-4 short paragraphs. Write fiction, not "
    "log lines: no dice numbers, no DCs, no stat names, no rules-talk.\n"
    "- Honor every pinned fact literally. The dead stay dead, promises stay "
    "made, and no one is in two places at once.\n"
    "- Propose state changes ONLY through the propose_state_deltas tool call "
    "(or the JSON envelope when tools are unavailable) — never write state "
    "into prose the validators cannot see.\n"
    "- Failure is fail-forward: on a failure or success-at-a-cost, narrate the "
    "consequence and a way onward; never narrate a clean win the code did not "
    "award.\n"
)


def default_ruleset_text() -> str:
    """Static, cacheable system/ruleset block (spec §4 priority #1)."""
    return RULESET_TEXT


# --------------------------------------------------------------------------- #
# Intent classification (rules-first; spec §1)
# --------------------------------------------------------------------------- #

# Verb -> skill hint. The skill names mirror resolve.DEFAULT_SKILL_MODIFIERS
# where one exists; unknown names simply resolve with a 0 modifier.
ACTION_VERBS: dict[str, str] = {
    # combat
    "attack": "attack", "strike": "attack", "hit": "attack", "slash": "attack",
    "stab": "attack", "shoot": "attack", "fire": "attack", "punch": "attack",
    "kick": "attack", "kill": "attack", "fight": "attack", "swing": "attack",
    "charge": "attack", "threaten": "intimidation", "intimidate": "intimidation",
    "scare": "intimidation", "menace": "intimidation", "demand": "intimidation",
    "bully": "intimidation", "bribe": "persuasion",
    # movement / body
    "climb": "athletics", "jump": "athletics", "leap": "athletics",
    "swim": "athletics", "run": "athletics", "lift": "athletics",
    "haul": "athletics", "push": "athletics", "pull": "athletics",
    "dash": "athletics", "vault": "athletics", "wrestle": "athletics",
    # stealth
    "sneak": "stealth", "hide": "stealth", "creep": "stealth",
    "skulk": "stealth", "slip": "stealth", "steal": "stealth",
    "pickpocket": "stealth", "pocket": "stealth", "snatch": "stealth",
    # wits
    "search": "investigation", "examine": "investigation", "inspect": "investigation",
    "investigate": "investigation", "study": "investigation", "rummage": "investigation",
    "loot": "investigation", "look": "perception", "watch": "perception",
    "listen": "perception", "scan": "perception", "observe": "perception",
    "spot": "perception", "notice": "perception", "read": "insight",
    "sense": "insight", "gauge": "insight",
    # outdoors / healing / craft / arcana
    "track": "survival", "forage": "survival", "hunt": "survival",
    "camp": "survival", "navigate": "survival", "heal": "medicine",
    "bandage": "medicine", "treat": "medicine", "tend": "medicine",
    "cast": "arcana", "invoke": "arcana", "pick": "lockpicking",
    "unlock": "lockpicking", "jimmy": "lockpicking", "disarm": "lockpicking",
    "lie": "deception", "bluff": "deception", "deceive": "deception",
    "mislead": "deception", "convince": "persuasion", "persuade": "persuasion",
    "negotiate": "persuasion", "bargain": "persuasion", "charm": "persuasion",
    "plead": "persuasion", "appeal": "persuasion", "offer": "persuasion",
}

# Speech/intent markers: "ask X", "say ...", quoted speech, greetings.
_DIALOGUE_VERBS = frozenset({
    "ask", "tell", "say", "speak", "talk", "greet", "answer", "reply",
    "shout", "whisper", "call", "warn", "mutter", "explain", "thank",
    "apologize", "suggest", "propose", "request", "beg", "invite", "congratulate",
})
_QUESTION_WORDS = frozenset({
    "who", "what", "when", "where", "why", "how", "which", "whose", "whom",
    "is", "are", "was", "were", "do", "does", "did", "can", "could", "would",
    "will", "should", "shall", "may", "might", "have", "has", "had",
})
_GREETINGS = frozenset({"hello", "hi", "hey", "greetings", "good morning",
                        "good evening", "good day", "farewell", "goodbye"})
_META_PREFIX = re.compile(r"^\s*[/\\]")
_WORD_RE = re.compile(r"[a-z0-9']+")
_LEADING_FILLER = re.compile(r"^(?:\s*(?:i|i'll|i will|ill|let me|let's|lets|we|we'll)\s+)+",
                             re.IGNORECASE)
_QUOTED = re.compile(r"[\"“”‘’«»]([^\"“”‘’«»]{2,})[\"“”‘’«»]")
_ENTRY_QUOTES = ('"', "'", "“", "”", "‘", "’", "«", "»")
_TARGET_ARTICLES = frozenset({"the", "a", "an", "my", "that", "this", "his",
                              "her", "their", "your", "one"})
_NUMERIC_TARGET = re.compile(r"^(?:npc[:_-]?)?(\d+)$", re.IGNORECASE)


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


class Orchestrator:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 narrator: NarratorRunner | None = None, *,
                 rng: Any = None, sim: Similarity | None = None,
                 components: Any = None, assembler: ContextAssembler | None = None,
                 adapter: Any = None, provider: ProviderConfig | None = None,
                 context_window: int | None = None,
                 effect_rules: Sequence[Callable[[Intent, MechanicalOutcome], list[Delta]]] | None = None,
                 ruleset: str | None = None, dc: int | None = None,
                 now: Callable[[], float] | None = None) -> None:
        """``narrator`` wins over ``adapter``; with only an adapter the live
        narrator (native tools / degraded JSON, spec §7) is built lazily with
        the capability probe cached in ``provider_caps``."""
        self.store = store
        self.config = config or EngineConfig()
        self.narrator = narrator
        self.adapter = adapter
        self.provider = provider or ProviderConfig(model="")
        self.sim = sim or LexicalSimilarity()
        self.rng = rng if rng is not None else SeededRng(self.config.db_path or "lorebound")
        self.components = components
        self.assembler = assembler
        self.context_window = context_window
        self.effect_rules = effect_rules
        self.ruleset = ruleset or default_ruleset_text()
        self.dc = dc
        self.now = now or time.time
        self._validator: Validator | None = None
        self._memory: MemoryBundle | None = None

    # -- lazily built collaborators ---------------------------------------- #

    def _assembler(self) -> ContextAssembler:
        if self.assembler is None:
            self.assembler = ContextAssembler(
                self.store, self.config, sim=self.sim, components=self.memory_bundle(),
            )
        return self.assembler

    def memory_bundle(self) -> MemoryBundle:
        if self._memory is None:
            self._memory = (
                self.components if self.components is not None
                else MemoryBundle.build(self.store, self.config, self.sim)
            )
        return self._memory

    def _validator_of(self) -> Validator:
        if self._validator is None:
            self._validator = Validator(self.store, self.config, sim=self.sim)
        return self._validator

    def _narrator(self) -> NarratorRunner:
        if self.narrator is None:
            if self.adapter is None:
                raise RuntimeError(
                    "Orchestrator has no narrator: pass narrator=... (e.g. "
                    "play.StubNarrator()) or adapter=... (a provider adapter)"
                )
            self.narrator = narrator_from_adapter(
                self.adapter, self.provider, store=self.store, config=self.config,
            )
        return self.narrator

    # -- intent ------------------------------------------------------------ #

    def classify_intent(self, text: str) -> Intent:
        """dialogue | action | exploration | meta — rules-based baseline.

        Rules, in order (first match wins):

        1. a leading ``/`` (or ``\\``) is a meta command;
        2. quoted speech, a ``?``, a leading question word, a greeting, or a
           dialogue verb (ask/tell/say/talk to/...) is ``dialogue``;
        3. an action verb from :data:`ACTION_VERBS` is ``action`` with that
           verb's skill hint;
        4. everything else is ``exploration``.

        ``target`` is attached when the text names a known NPC (canonical
        ``"npc:<id>"``), a numeric ``npc:<id>`` token, or a direct object right
        after an action verb. Store reads only — no writes, no dice.
        """
        raw = str(text or "")
        stripped = raw.strip()
        if not stripped:
            return Intent(kind="exploration", text="")
        if _META_PREFIX.match(stripped):
            return Intent(kind="meta", text=stripped)

        lowered = stripped.lower()
        first_word = next(iter(_words(lowered)), "")
        target = self._target_in(stripped)

        quoted = bool(_QUOTED.search(stripped))
        question = stripped.endswith("?") or first_word in _QUESTION_WORDS
        greeting = first_word in _GREETINGS
        dialogue_verb = first_word in _DIALOGUE_VERBS or any(
            word in _DIALOGUE_VERBS for word in _words(lowered)[:3]
        )
        if quoted or question or greeting or dialogue_verb:
            return Intent(kind="dialogue", text=stripped, target=target)

        verb, skill = self._action_verb(lowered)
        if verb is not None:
            if target is None:
                target = self._direct_object(stripped, verb)
            return Intent(kind="action", text=stripped, skill=skill, target=target)

        return Intent(kind="exploration", text=stripped, target=target)

    @staticmethod
    def _action_verb(lowered: str) -> tuple[str | None, str | None]:
        """First action verb in the input (leading filler like "I"/"let me" skipped)."""
        core = _LEADING_FILLER.sub("", lowered).strip()
        for word in _words(core):
            skill = ACTION_VERBS.get(word)
            if skill is not None:
                return word, skill
        return None, None

    def _target_in(self, text: str) -> str | None:
        """Canonical ``npc:<id>`` when ``text`` names a known NPC, else None.

        Matching is by name prefix so "Marla" finds "Marla Quist"; a prefix that
        fits two NPCs equally well is ambiguous and resolves to None (better no
        target than the wrong one — resolve.py re-checks everything downstream).
        """
        for token in _WORDS_SPLIT.split(text):
            match = _NUMERIC_TARGET.match(token)
            if match:
                return f"npc:{int(match.group(1))}"
        if self.store is None or not callable(getattr(self.store, "find", None)):
            return None
        words = set(_words(text))
        best: tuple[int, str] | None = None
        ambiguous = False
        for row in self.store.find("npcs", order_by="id"):
            name_words = _words(str(row.get("name") or ""))
            if not name_words:
                continue
            matched = 0
            for word in name_words:
                if word not in words:
                    break
                matched += 1
            if not matched:
                continue
            key = (matched, f"npc:{row['id']}")
            if best is None or key[0] > best[0]:
                best, ambiguous = key, False
            elif key[0] == best[0]:
                ambiguous = True
        return None if (best is None or ambiguous) else best[1]

    @staticmethod
    def _direct_object(text: str, verb: str) -> str | None:
        """The noun right after an action verb ("attack the guard" -> "guard")."""
        words = text.split()
        for index, word in enumerate(words):
            tokens = _WORD_RE.findall(word.lower())
            if not tokens or tokens[0] != verb:
                continue
            for candidate in words[index + 1:]:
                token = _WORD_RE.findall(candidate.lower())
                if not token:
                    continue
                if token[0] in _TARGET_ARTICLES:
                    continue
                return token[0]
            return None
        return None

    # -- scene ------------------------------------------------------------- #

    def _character(self) -> CharacterRow | None:
        row = self.store.find_one("characters", order_by="id")
        return from_row(CharacterRow, row) if row else None

    def location_row(self, location_id: str) -> dict | None:
        """Location row for a ``location_id`` slug.

        The store keys rows by integer id while characters/NPCs carry a string
        ``location_id``, so a location matches when the slug equals the row id,
        the row's ``flags["slug"]``, or the slugified name (fixtures use the
        slug convention; nothing else is assumed).
        """
        text = str(location_id or "").strip()
        if not text:
            return None
        rows = self.store.find("locations", order_by="id")
        if text.isdigit():
            for row in rows:
                if str(row["id"]) == text:
                    return row
        slug = _slug(text)
        for row in rows:
            flags = row.get("flags") or {}
            if isinstance(flags, str):
                try:
                    flags = json.loads(flags)
                except ValueError:
                    flags = {}
            if str(flags.get("slug") or "") == text:
                return row
            if _slug(str(row.get("name") or "")) == slug:
                return row
        return None

    def present_npcs(self, turn: int) -> list[dict]:
        """Cheap per-NPC reads for everyone at the player's location (dead ones
        included — a body in the room is scene state, and the prompt must be
        able to say so)."""
        character = self._character()
        if character is None:
            return []
        where = str(character.location_id or "")
        memory = self.memory_bundle()
        present: list[dict] = []
        for row in self.store.find("npcs", order_by="id"):
            npc_id = int(row["id"])
            if str(row.get("location_id") or "") != where:
                continue
            key = f"npc:{npc_id}"
            mood = memory.moods.current(npc_id=key, turn=turn)
            currents = memory.ledger.currents(
                npc_id=key, player_id=str(self.config.player_id), turn=turn,
            )
            personality = from_row(NPCRow, row).personality or {}
            present.append({
                "id": key,
                "name": str(row.get("name") or key),
                "alive": bool(row.get("alive", 1)),
                "mood": {"valence": mood.valence, "arousal": mood.arousal},
                "disposition": currents.get("total", 0.0),
                "personality": personality,
            })
        return present

    def _scene(self, turn: int) -> dict:
        character = self._character()
        location_id = str(character.location_id or "") if character else ""
        row = self.location_row(location_id)
        location: dict[str, Any] = {"id": location_id}
        if row is not None:
            connections = row.get("connections") or []
            if isinstance(connections, str):
                try:
                    connections = json.loads(connections)
                except ValueError:
                    connections = []
            location.update({
                "name": str(row.get("name") or location_id),
                "description_static": str(row.get("description_static") or ""),
                "connections": list(connections),
            })
        elif location_id:
            location["name"] = location_id
        player: dict[str, Any] = {
            "name": character.name if character else "",
            "stats": character.stats if character else {},
            "inventory": character.inventory if character else [],
            "status_effects": character.status_effects if character else [],
        }
        return {
            "ruleset": self.ruleset,
            "location": location,
            "present_npcs": self.present_npcs(turn),
            "player": player,
        }

    # -- lifecycle --------------------------------------------------------- #

    def take_turn(self, *, player_input: str, turn: int) -> TurnResult:
        """Run the full Pass A-D lifecycle for one player turn (see module doc)."""
        narrator = self._narrator()
        intent = self.classify_intent(player_input)
        outcome = resolve_action(
            intent, rng=self.rng, store=self.store, turn=turn,
            dc=self.dc, effect_rules=self.effect_rules,
        )
        prompt = self._assembler().assemble(
            turn=turn, scene=self._scene(turn), mechanical=outcome,
            intent=intent, context_window=self.context_window,
        )

        system_lines: list[str] = []
        proposals = narrator.narrate(prompt=prompt, turn=turn)
        self._collect_notes(narrator, system_lines)
        report, applied_keys = self._commit_pass(proposals, outcome, turn=turn)
        narration = str(proposals.narration or "")
        dialogue = list(proposals.npc_dialogue or [])

        # memory subsystem updates (§3.2, §3.7, §3.3)
        self._memory_updates(
            turn=turn, player_input=player_input, outcome=outcome, narration=narration,
        )

        # Pass D
        problems = self._scan_consistency(
            turn=turn, narration=narration, report=report, dialogue=dialogue,
        )
        regenerations = 0
        patched = {"sentences": 0, "dialogue": 0}
        if problems:
            system_lines.append(
                f"consistency: {len(problems)} problem(s) found — regenerating once"
            )
            system_lines.extend(
                f"consistency: {problem['detail']}" for problem in problems[:3]
            )
            correction = self._correction_note(problems)
            retry = narrator.narrate(prompt=prompt, turn=turn, retry_note=correction)
            regenerations = 1
            self._collect_notes(narrator, system_lines)
            retry_report, _ = self._commit_pass(
                retry, outcome, turn=turn, seen=applied_keys,
            )
            report = _merge_reports(report, retry_report)
            narration = str(retry.narration or narration)
            dialogue = list(retry.npc_dialogue or [])
            problems = self._scan_consistency(
                turn=turn, narration=narration, report=report, dialogue=dialogue,
            )
            if not problems:
                system_lines.append("consistency: regeneration cleared the problem(s)")
            else:
                narration, dialogue, patched = self._patch(
                    narration, dialogue, problems,
                )
                sentences = patched["sentences"]
                lines = patched["dialogue"]
                system_lines.append(
                    f"consistency: sentence patch applied after regeneration "
                    f"({sentences} sentence(s), {lines} dialogue line(s) removed)"
                )
                leftover = self._scan_consistency(
                    turn=turn, narration=narration, report=report, dialogue=dialogue,
                )
                if leftover:
                    system_lines.append(
                        "consistency: unresolved problems remain: "
                        + "; ".join(problem["detail"] for problem in leftover)
                    )

        if report.rejected:
            system_lines.append(
                f"validator: {len(report.rejected)} proposed delta(s) rejected"
            )

        self._write_turn_log(
            turn=turn, player_input=player_input, outcome=outcome,
            narration=narration, report=report,
        )
        telemetry = self._write_telemetry(
            turn=turn, prompt=prompt, intent=intent, outcome=outcome,
            report=report, narration=narration, dialogue=dialogue,
            regenerations=regenerations, patched=patched, problems=len(problems),
        )
        return TurnResult(
            turn=turn, narration=narration, npc_dialogue=dialogue, mechanics=outcome,
            report=report, system_lines=system_lines, telemetry=telemetry,
        )

    # -- Pass C ------------------------------------------------------------ #

    def _commit_pass(self, proposals: ProposalSet, outcome: MechanicalOutcome,
                     *, turn: int, seen: set | None = None) -> tuple[CommitReport, set]:
        """Validate + commit one Pass B envelope (plus mechanical effects)."""
        validator = self._validator_of()
        candidates = [*(proposals.deltas or []), *(outcome.effects or [])]
        fresh = [delta for delta in candidates if _delta_key(delta) not in (seen or set())]
        verdicts = validator.validate(fresh, turn=turn)
        report = validator.commit(verdicts, turn=turn)
        keys = set(seen or set())
        keys.update(_delta_key(verdict.delta) for verdict in report.applied
                    if verdict.delta is not None)
        return report, keys

    def _memory_updates(self, *, turn: int, player_input: str,
                        outcome: MechanicalOutcome, narration: str) -> None:
        """Chronicle append + mood decay for present NPCs + saga cadence (§3)."""
        memory = self.memory_bundle()
        memory.chronicle.append(
            turn_id=turn,
            actor="player",
            action_summary=_summary(player_input, limit=200),
            mechanical_result=_mechanical_label(outcome),
            consequence_oneliner="",
            verbatim_text=_verbatim(narration),
        )
        for npc in self.present_npcs(turn):
            key = str(npc["id"])
            if self.store.find_one("moods", {"npc_id": key}) is None:
                continue  # nothing recorded yet: nothing to decay
            memory.moods.apply(
                npc_id=key, valence_delta=0.0, arousal_delta=0.0, turn=turn,
            )
        memory.saga.maybe_summarize(turn=turn)

    # -- Pass D ------------------------------------------------------------ #

    def consistency_check(self, *, turn: int, narration: str, report: CommitReport,
                          dialogue: list | None = None) -> tuple[bool, list[str]]:
        """Pass D: returns (clean, problems). Scans narration against pinned
        facts + recently committed deltas; cheap, deterministic-first.

        Problems are stable one-line strings; the structured records (with the
        sentence spans/indices the sentence patch needs) stay private —
        :meth:`_scan_consistency`. Non-clean means: a dead NPC speaks, the
        narration contradicts a pinned fact, or it restates a delta the
        validator rejected this very turn.
        """
        problems = self._scan_consistency(
            turn=turn, narration=narration, report=report, dialogue=dialogue,
        )
        return (not problems, [problem["detail"] for problem in problems])

    def _scan_consistency(self, *, turn: int, narration: str, report: CommitReport,
                          dialogue: list | None) -> list[dict]:
        problems: list[dict] = []
        problems.extend(self._dead_npc_speech(narration, dialogue or []))
        problems.extend(self._pinned_fact_conflicts(narration))
        problems.extend(self._rejected_restatements(narration, report))
        return problems

    def _dead_npc_speech(self, narration: str, dialogue: list) -> list[dict]:
        """A dead NPC may be present, remembered, mourned — never heard.

        Two deterministic tells: a dialogue entry attributed to a dead NPC, and
        a narration sentence that attributes speech to them (``"…," Marla
        says`` / ``Marla: "…"``). Negated mentions ("Marla does not answer") are
        not speech and are left alone.
        """
        problems: list[dict] = []
        for index, entry in enumerate(dialogue):
            if not isinstance(entry, Mapping):
                continue
            row = self._npc_row_for(entry.get("npc_id"), entry.get("name"))
            if row is None or row.get("alive", 1):
                continue
            name = str(row.get("name") or entry.get("npc_id") or "")
            problems.append({
                "kind": "dead_npc_speech",
                "detail": (
                    f"{name or 'a dead NPC'} is dead (alive = 0) but speaks in this "
                    f"turn's dialogue"
                ),
                "span": None,
                "dialogue_index": index,
                "npc": name,
            })
        verbs = "|".join(_SPEECH_VERBS)
        for row in self.store.find("npcs", order_by="id"):
            if row.get("alive", 1):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            escaped = re.escape(name)
            attribution = re.compile(
                rf"\b{escaped}\b\s*[:：]\s*[\"“«'‘]", re.IGNORECASE,
            )
            speaking = re.compile(
                rf"\b{escaped}\b(?P<between>[^.!?\n]{{0,60}}?)\b(?:{verbs})\b",
                re.IGNORECASE,
            )
            for paragraph, sentence, text in _iter_sentences(narration):
                speaks = attribution.search(text) or any(
                    not _NEGATION_RE.search(match.group("between"))
                    for match in speaking.finditer(text)
                )
                if not speaks:
                    continue
                problems.append({
                    "kind": "dead_npc_speech",
                    "detail": (
                        f"the narration has {name} speak, but {name} is dead "
                        f"(alive = 0)"
                    ),
                    "span": (paragraph, sentence),
                    "dialogue_index": None,
                    "npc": name,
                })
        return problems

    def _pinned_fact_conflicts(self, narration: str) -> list[dict]:
        problems: list[dict] = []
        for fact in self.memory_bundle().facts.pinned_facts():
            statement = str(getattr(fact, "statement", "") or "")
            if not statement:
                continue
            for paragraph, sentence, text in _iter_sentences(narration):
                reason = conflict_reason(
                    text, statement, pinned=True, sim=self.sim,
                )
                if not reason:
                    continue
                problems.append({
                    "kind": "pinned_fact_conflict",
                    "detail": (
                        f"the narration contradicts pinned fact #{fact.id} "
                        f"({reason})"
                    ),
                    "span": (paragraph, sentence),
                    "dialogue_index": None,
                    "npc": None,
                })
        return problems

    def _rejected_restatements(self, narration: str, report: CommitReport) -> list[dict]:
        problems: list[dict] = []
        for verdict in (report.rejected if report else []):
            if getattr(verdict, "kind", "") != VerdictKind.REJECTED.value:
                continue
            delta = getattr(verdict, "delta", None)
            if delta is None:
                continue
            kind = str(getattr(delta, "kind", ""))
            data = getattr(delta, "data", None) or {}
            for paragraph, sentence, text in _iter_sentences(narration):
                hit = _restatement(kind, data, text, self.sim)
                if not hit:
                    continue
                problems.append({
                    "kind": "rejected_restatement",
                    "detail": (
                        f"the narration restates a delta the validator rejected "
                        f"({hit})"
                    ),
                    "span": (paragraph, sentence),
                    "dialogue_index": None,
                    "npc": None,
                })
        return problems

    def _correction_note(self, problems: list[dict]) -> str:
        lines = "\n".join(f"- {problem['detail']}" for problem in problems)
        return (
            "Your previous narration contradicted established canon:\n"
            f"{lines}\n"
            "Rewrite the narration so every point above is honored, keeping the "
            "same resolved mechanical outcome and the same proposed state deltas. "
            "Do not give a dead character dialogue or action, do not restate a "
            "state change that was refused, and do not contradict a pinned fact."
        )

    def _patch(self, narration: str, dialogue: list,
               problems: list[dict]) -> tuple[str, list, dict]:
        """Deterministic last resort: drop the offending dialogue lines and
        sentences.

        Runs only after the single regeneration failed; the player must never
        be shown a narration that contradicts pinned canon. Paragraph structure
        is preserved for the sentences that survive.
        """
        drop_dialogue = {
            problem["dialogue_index"] for problem in problems
            if problem.get("dialogue_index") is not None
        }
        drop_sentences = {
            problem["span"] for problem in problems if problem.get("span")
        }
        kept_dialogue = [
            entry for index, entry in enumerate(dialogue) if index not in drop_dialogue
        ]
        paragraphs: list[str] = []
        removed_sentences = 0
        for p_index, paragraph in enumerate(_paragraphs(narration)):
            kept = [
                sentence.strip()
                for s_index, sentence in enumerate(_SENTENCE_SPLIT_RE.split(paragraph.strip()))
                if sentence.strip() and (p_index, s_index) not in drop_sentences
            ]
            removed_sentences += len(_SENTENCE_SPLIT_RE.split(paragraph.strip())) - len(kept)
            if kept:
                paragraphs.append(" ".join(kept))
        patched = "\n\n".join(paragraphs) or _PATCH_FALLBACK
        return patched, kept_dialogue, {
            "sentences": removed_sentences,
            "dialogue": len(dialogue) - len(kept_dialogue),
        }

    # -- reporting --------------------------------------------------------- #

    def _write_turn_log(self, *, turn: int, player_input: str,
                        outcome: MechanicalOutcome, narration: str,
                        report: CommitReport) -> int:
        log = TurnLog(
            turn=turn,
            actor="player",
            raw_action=str(player_input or ""),
            mechanical_resolution=_outcome_snapshot(outcome),
            narration_text=narration,
            state_deltas_applied=[
                _delta_snapshot(verdict.delta) for verdict in report.applied
                if verdict.delta is not None
            ],
            created_at=int(self.now()),
        )
        return int(self.store.insert("turn_log", to_row(log)))

    def _write_telemetry(self, *, turn: int, prompt: Any, intent: Intent,
                         outcome: MechanicalOutcome, report: CommitReport,
                         narration: str, dialogue: list, regenerations: int,
                         patched: dict, problems: int) -> dict:
        accounting = getattr(prompt, "accounting", None) or {}
        budget = accounting.get("budget") or {}
        sections = accounting.get("sections") or {}
        tokens_in = int(budget.get("used") or 0)
        tokens_out = self._assembler().estimate_tokens(
            narration + "".join(str(entry.get("text") or "") for entry in dialogue
                                if isinstance(entry, Mapping))
        )
        notes: dict[str, Any] = {
            "intent": {"kind": intent.kind, "skill": intent.skill, "target": intent.target},
            "mechanics": _outcome_snapshot(outcome),
            "deltas": {
                "accepted": len(report.accepted),
                "clamped": len(report.clamped),
                "rejected": len(report.rejected),
            },
            "consistency": {
                "problems": problems,
                "regenerations": regenerations,
                "patched_sentences": patched.get("sentences", 0),
                "patched_dialogue": patched.get("dialogue", 0),
            },
            "estimated": True,
        }
        stats = getattr(self.narrator, "stats", None)
        if callable(stats):
            notes["narrator"] = stats()
        provider, model = self._narrator_identity()
        telemetry = TelemetryRow(
            turn=turn, provider=provider, model=model,
            prompt_tokens=tokens_in, completion_tokens=tokens_out,
            budget_alloc=dict(accounting.get("allocations") or {}),
            dropped=list(accounting.get("dropped") or []),
            notes=notes,
        )
        row_id = int(self.store.insert("telemetry", to_row(telemetry)))
        return {"id": row_id, "turn": turn, "provider": provider, "model": model,
                "prompt_tokens": tokens_in, "completion_tokens": tokens_out,
                "budget_alloc": telemetry.budget_alloc, "dropped": telemetry.dropped,
                "notes": notes, "sections": dict(sections)}

    def _narrator_identity(self) -> tuple[str, str]:
        narrator = self.narrator
        provider = str(getattr(narrator, "provider_name", "") or "")
        model = str(getattr(narrator, "model_name", "") or "")
        if not provider:
            provider = "live" if self.adapter is not None else type(narrator).__name__.lower()
        if not model:
            model = str(getattr(self.provider, "model", "") or "")
        return provider, model

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _collect_notes(narrator: Any, system_lines: list[str]) -> None:
        notes = getattr(narrator, "last_notes", None)
        if notes is None:
            notes = getattr(narrator, "notes", None)
        for note in list(notes or [])[:5]:
            system_lines.append(f"narrator: {note}")

    def _npc_row_for(self, npc_id: Any, name: Any = None) -> dict | None:
        text = str(npc_id or "").strip()
        row = None
        if text:
            match = _NUMERIC_TARGET.match(text)
            if match:
                row = self.store.find_one("npcs", {"id": int(match.group(1))})
            elif self.store.find_one("npcs", {"name": text}) is not None:
                row = self.store.find_one("npcs", {"name": text})
        if row is None and name:
            row = self.store.find_one("npcs", {"name": str(name).strip()})
        return row


# --------------------------------------------------------------------------- #
# Live narrator: provider adapter behind the NarratorRunner surface (spec §7)
# --------------------------------------------------------------------------- #

class LiveNarrator:
    """NarratorRunner over a BYOK adapter: native tool calls when the model has
    them, the degraded JSON-in-text protocol otherwise (§7).

    Native path: ``complete`` with ``tools=jsonproto.delta_tool_schema()``; the
    ``propose_state_deltas`` call is decoded by ``proposals_from_tool_calls``,
    with the message text used as the narration fallback. Degraded path: the
    JSON protocol instructions are appended to the prompt and the reply goes
    through ``parse_with_repair``; an unparseable reply triggers ONE bounded
    regeneration (``should_regenerate`` / ``regenerate_note``) and then degrades
    to narration-only rather than breaking the game (quality floor).
    """

    def __init__(self, adapter: Any, provider: ProviderConfig | None = None,
                 config: EngineConfig | None = None, *, max_tokens: int = 800,
                 temperature: float = 0.8, max_parse_attempts: int = 2) -> None:
        self.adapter = adapter
        self.provider = provider or ProviderConfig(model="")
        self.config = config or EngineConfig()
        self.max_tokens = int(max_tokens)
        self.temperature = float(temperature)
        self.max_parse_attempts = max(1, int(max_parse_attempts))
        self.provider_name = str(getattr(adapter, "name", "") or "live")
        self.model_name = str(self.provider.model or "")
        self.calls = 0
        self.regenerations = 0
        self.parse_failures = 0
        self.repairs = 0
        self.last_notes: list[str] = []

    def capabilities(self) -> Any:
        getter = getattr(self.adapter, "capabilities", None)
        return getter() if callable(getter) else None

    def stats(self) -> dict:
        caps = self.capabilities()
        return {
            "calls": self.calls,
            "regenerations": self.regenerations,
            "parse_failures": self.parse_failures,
            "repairs": self.repairs,
            "native_tools": bool(getattr(caps, "native_tools", False)),
        }

    def narrate(self, *, prompt: Any, turn: int,
                retry_note: str | None = None) -> ProposalSet:
        self.last_notes = []
        native = bool(getattr(self.capabilities(), "native_tools", False))
        user_text = str(getattr(prompt, "text", "") or "")
        if not native:
            user_text = build_json_protocol_prompt(user_text)
        messages = [
            ChatMessage(role="system", content=str(getattr(prompt, "system", "") or "")),
            ChatMessage(role="user", content=user_text),
        ]
        if retry_note:
            messages.append(ChatMessage(role="user", content=str(retry_note)))
        tools = delta_tool_schema() if native else None

        attempts = 0
        while True:
            response = self.adapter.complete(ChatRequest(
                model=self.provider.model, messages=messages, tools=tools,
                max_tokens=self.max_tokens, temperature=self.temperature,
            ))
            self.calls += 1
            text = str(getattr(response, "text", "") or "")
            if native and response.tool_calls:
                notes: list[str] = []
                proposals = proposals_from_tool_calls(
                    list(response.tool_calls), narration=text, notes=notes,
                )
                if proposals.deltas or not notes:
                    self.last_notes.extend(notes)
                    return proposals
                self.last_notes.extend(notes)
                return proposals
            payload, repair_note = parse_with_repair(text)
            if payload is not None:
                if repair_note:
                    self.repairs += 1
                    self.last_notes.append(f"json: {repair_note}")
                proposals = proposals_from_payload(payload, narration=text,
                                                   notes=self.last_notes)
                return proposals
            self.parse_failures += 1
            if native:
                # Tools were offered but the model answered in prose: keep the
                # prose, propose nothing (the validator commits nothing).
                self.last_notes.append(
                    "json: no propose_state_deltas call in the reply; narration-only"
                )
                return ProposalSet(narration=text)
            attempts += 1
            if should_regenerate(None, attempts=attempts,
                                 max_attempts=self.max_parse_attempts):
                self.regenerations += 1
                self.last_notes.append(f"json: {repair_note}; regenerating")
                messages = [*messages, ChatMessage(
                    role="user", content=regenerate_note(repair_note),
                )]
                continue
            self.last_notes.append(
                f"json: {repair_note}; regeneration exhausted, using the reply as prose"
            )
            return ProposalSet(narration=text)


def narrator_from_adapter(adapter: Any, provider: ProviderConfig | None = None,
                          *, store: Any = None, config: EngineConfig | None = None,
                          probe: bool = True, **kwargs: Any) -> LiveNarrator:
    """Build the live narrator for a BYOK adapter (one setup-time probe).

    ``probe_capabilities`` is advisory and never raises; the verdict is cached
    in ``provider_caps`` when a store is given (spec §7 capability detection),
    and the same store read feeds the budget controller's context window.
    """
    provider = provider or ProviderConfig(model="")
    if probe:
        probe_capabilities(adapter, provider, store=store)
    return LiveNarrator(adapter, provider, config, **kwargs)


# --------------------------------------------------------------------------- #
# Module helpers
# --------------------------------------------------------------------------- #

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"'\u201d\u2019\u00bb)])\s+")
_WORDS_SPLIT = re.compile(r"[,\s]+")
_PATCH_FALLBACK = "The moment passes, and the world keeps the shape the code gave it."
# Speech attribution verbs used by the dead-NPC scan (Pass D). Base forms are
# listed alongside inflections so "does not answer" and "answers" both match —
# the negation guard, not a missing inflection, is what clears negated prose.
_SPEECH_VERBS = (
    "say", "says", "said", "answer", "answers", "answered",
    "reply", "replies", "replied", "whisper", "whispers", "whispered",
    "shout", "shouts", "shouted", "speak", "speaks", "spoke",
    "murmur", "murmurs", "murmured", "tell", "tells", "told",
    "greet", "greets", "greeted", "call", "calls", "called",
    "laugh", "laughs", "laughed",
)
# "Marla does not answer" is not speech — negated attributions are left alone.
_NEGATION_RE = re.compile(
    r"(?:\bnot\b|\bnever\b|\bno\b|n't|\bwithout\b|\bfail(?:s|ed)?\s+to\b)",
    re.IGNORECASE,
)
_MONEY_WORDS = (
    "gold", "coin", "coins", "silver", "copper", "pay", "pays", "paid",
    "spend", "spends", "spent", "bribe", "purse", "price", "cost", "owes",
)
_REMOVAL_WORDS = (
    "give", "gives", "gave", "hand", "hands", "handed", "offer", "offers",
    "offered", "surrender", "drop", "drops", "dropped", "lose", "loses",
    "lost", "use", "uses", "used", "spend", "spent",
)
_RESTATEMENT_SIMILARITY = 0.80
_VERBATIM_LIMIT = 2000


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _paragraphs(text: str) -> list[str]:
    return [part for part in _PARAGRAPH_SPLIT_RE.split(text or "") if part.strip()]


def _iter_sentences(narration: str) -> list[tuple[int, int, str]]:
    """``[(paragraph_index, sentence_index, sentence_text)]`` — stable spans the
    patch step can address."""
    out: list[tuple[int, int, str]] = []
    for p_index, paragraph in enumerate(_paragraphs(narration)):
        for s_index, sentence in enumerate(_SENTENCE_SPLIT_RE.split(paragraph.strip())):
            text = sentence.strip()
            if text:
                out.append((p_index, s_index, text))
    return out


def _summary(text: str, *, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: max(1, limit - 1)].rstrip() + "…"


def _verbatim(narration: str) -> str | None:
    text = str(narration or "").strip()
    if not text:
        return None
    if len(text) <= _VERBATIM_LIMIT:
        return text
    return text[: _VERBATIM_LIMIT - 1].rstrip() + "…"


def _mechanical_label(outcome: MechanicalOutcome | None) -> str:
    if outcome is None:
        return ""
    if outcome.check is not None:
        return str(outcome.check.band or outcome.kind)
    return str(outcome.kind or "")


def _outcome_snapshot(outcome: MechanicalOutcome | None) -> dict:
    if outcome is None:
        return {}
    check = outcome.check
    snapshot: dict[str, Any] = {
        "turn": outcome.turn,
        "kind": outcome.kind,
        "label": outcome.label,
        "verdict_line": outcome.verdict_line,
        "notes": list(outcome.notes or []),
    }
    if check is not None:
        request = check.request
        snapshot.update({
            "band": check.band,
            "roll": check.roll,
            "modifier": check.modifier,
            "total": check.total,
            "dc": getattr(request, "dc", None),
            "skill": getattr(request, "skill", ""),
        })
    return snapshot


def _delta_key(delta: Delta) -> tuple:
    try:
        data = json.dumps(delta.data or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        data = repr(delta.data)
    return (str(delta.kind or ""), str(delta.target or ""), data, str(delta.reason or ""))


def _delta_snapshot(delta: Delta | None) -> dict:
    if delta is None:
        return {}
    return {
        "kind": str(delta.kind or ""),
        "target": str(delta.target or ""),
        "data": dict(delta.data or {}),
        "reason": str(delta.reason or ""),
    }


def _merge_reports(first: CommitReport, second: CommitReport) -> CommitReport:
    return CommitReport(
        accepted=[*first.accepted, *second.accepted],
        clamped=[*first.clamped, *second.clamped],
        rejected=[*first.rejected, *second.rejected],
    )


def _restatement(kind: str, data: Mapping[str, Any], sentence: str,
                 sim: Similarity) -> str | None:
    """Why ``sentence`` restates a rejected delta of ``kind`` (None = no hit)."""
    lowered = sentence.lower()
    if kind == "fact":
        statement = str(data.get("statement") or "").strip()
        if statement and sim.score(sentence, statement) >= _RESTATEMENT_SIMILARITY:
            return f"rejected fact: {statement!r}"
        return None
    if kind == "currency":
        amount = data.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount >= 0:
            return None
        value = abs(amount)
        needle = f"{value:g}"
        hit = re.search(rf"\b{re.escape(needle)}\b", sentence)
        if hit and any(word in lowered for word in _MONEY_WORDS):
            if _is_negated(sentence, hit.start(), hit.end()):
                return None  # "you never pay the 500" agrees with the rejection
            return f"rejected spend of {needle} (conservation refused it)"
        return None
    if kind == "inventory_remove":
        item = str(data.get("item_id") or "").strip()
        if not item:
            return None
        tokens = _WORDS_SPLIT.split(item.lower())
        if tokens and all(token in _words(lowered) for token in tokens) and any(
            word in lowered for word in _REMOVAL_WORDS
        ):
            return f"rejected removal of {item!r} (conservation refused it)"
        return None
    return None


def _is_negated(sentence: str, start: int, end: int, *, window: int = 40) -> bool:
    """True when a negation sits right around ``sentence[start:end]``."""
    return bool(_NEGATION_RE.search(sentence[max(0, start - window):end + window]))
