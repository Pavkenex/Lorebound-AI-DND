"""Pipeline-level end-to-end eval suites (spec §9) — R8.

Where the core suite (``evals/scenarios/*.py``, run by ``harness.run_scenarios``)
drives the state layer through the *step* DSL, the e2e suite drives the **real
turn pipeline** (``engine.pipeline.Orchestrator.take_turn``: intent → resolve →
context assembly → Pass B narrator → Pass C validator → memory → Pass D
consistency) with a *deterministic fake narrator per profile*, then asserts on
the turn results, the persisted state, the assembled prompts and the telemetry.

Files in this directory (``evals/scenarios/e2e/*.py``) defining ``E2E = {...}``
are e2e scenarios; ``harness.run_all()`` never discovers them (it globs only
``evals/scenarios/*.json|*.py``) so the core suite stays core-only. Run them
with ``python -m evals --suite e2e`` (or ``run_e2e()`` / ``run_e2e_suite()``).

Narrator profiles (one per session) and transports
--------------------------------------------------
Every session replays a **fixed reply script** through one profile; the profile
is the *transport* the reply travels over, so the same envelope content can be
exercised as a native tool call, as degraded JSON-in-text, and as the malformed
shapes real models produce (spec §7):

===============  ==============  =====================  ===========================
``via``          stub            native/tools           degraded (no tools)
===============  ==============  =====================  ===========================
``auto``         payload dict    tool call, dict args   strict JSON envelope text
``native-args``  —               tool call, dict args   strict JSON envelope text
``args-json``    —               tool call, JSON string strict JSON envelope text
``args-fenced``  —               tool call, fenced JSON fenced JSON text
``args-junk``    —               tool call + junk prose junk-prose text
``text``         —               raw text               raw text
``text-fenced``  —               fenced JSON text       fenced JSON text
``text-trailing-comma``          text + trailing comma  text + trailing comma
``prose``        —               narration only         narration only (regenerates)
===============  ==============  =====================  ===========================

* ``stub`` — ``play.StubNarrator``: no provider layer at all; ``envelope``
  dicts go through ``proposals_from_payload`` (so deltas are still decoded by
  the real codec). Only ``auto`` is accepted for this profile.
* ``native`` — ``LiveNarrator`` + a scripted adapter whose caps say
  ``native_tools=True``: replies arrive as ``propose_state_deltas`` tool calls.
* ``flaky`` — same capability as ``native``, but the script deliberately uses
  malformed-but-recoverable transports (fenced/junk args, fenced/no-tool-call
  text, trailing commas) so the recovery paths are exercised end to end.
* ``degraded`` — ``native_tools=False``: the JSON envelope travels as message
  text; one deliberately unparseable ``prose`` reply proves the bounded
  regenerate-on-parse-failure round (and the profiles' counters record it).

Reply consumption is exact: a turn's script must be consumed completely (both
by the pipeline and by the narrator-internal regeneration), otherwise the turn
FAILS — an unused reply means the scenario no longer tests what it claims.
``prose`` on a *native* profile is narration-only (one reply); on the
*degraded* profile it fails to parse and consumes the next scripted reply as
its regeneration — each session therefore scripts its own reply list.

Scenario DSL (``E2E`` mapping in ``evals/scenarios/e2e/*.py``)
--------------------------------------------------------------
``{"name", "description", "world", "options", "matrix", "sessions", "checks"}``

* ``world`` — fixture rows, same shape/validation as the core suite.
* ``options`` — ``seed``, ``rng`` (exact dice), ``turn``,
  ``context_window`` (fed to the orchestrator; the budget controller honours it).
* ``sessions`` — ``[{"name", "profile", "model", "expect_stats", "turns"}]``;
  each session gets its own throwaway SQLite DB, runs its turns in order, and
  the state left behind is compared when ``matrix`` is present.
* ``turns`` — ``[{"input", "turn", "replies", "steps", "expect", "checks", "bait"}]``:
  ``steps`` run first through the *same* step DSL as the core suite (plus
  ``update`` by ``ref`` — the game's own ruling — and a ``memory`` step that
  records/confirms an NPC memory through the public memory API), then the turn
  runs through ``take_turn``. ``expect`` is declarative (see
  ``_EXPECTATIONS``); ``checks`` are expressions over the e2e namespace
  (``sessions``/``turns``/``record``/``profiles``/``matrix``/``catch_rate``/
  ``memory_entry``/``memory_score``/``retrieve``/``rank_of``/``stats`` on top of
  the core namespace: ``store``/``state``/``npc``/``refs``/``facts``/...).
* ``bait`` — marks a turn as a contradiction case; the runner requires it to be
  *detected* (Pass D), *re-generated*, and *shipped clean*, and folds it into
  the scenario's ``catch_rate``.
* ``checks`` — scenario-level expressions, evaluated after every session ran.
* ``matrix`` — ``{"sessions": [...], "tables": [...]}``: the listed sessions
  must leave byte-identical state (and identical turn/prompt digests), which is
  the cross-transport ("same world, same script, three models") assertion.

Snapshots are JSON-safe and carry per-turn narration/dialogue/system lines,
outcome + report summaries, consistency handling, prompt accounting and the
final state digest, so a failure is diagnosable from ``--json`` output alone.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.config import EngineConfig
from engine.models import (
    ChatResponse,
    NPCMemoryEntry,
    ProposalSet,
    ProviderCaps,
    ProviderConfig,
    ToolCall,
    from_row,
)
from engine.pipeline import LiveNarrator, Orchestrator
from engine.play import StubNarrator
from engine.providers.jsonproto import PROPOSE_DELTAS_TOOL_NAME
from engine.store import Store
from engine.validate import Validator

from .harness import (
    _CHECK_BUILTINS,
    DEFAULT_SEED,
    EvalReport,
    EvalResult,
    EvalSession,
    Scenario,
    _append_step_telemetry,
    _insert_row,
    _make_rng,
    _namespace,
    _run_step,
    _same,
    _ScenarioError,
    _slug,
    _validate_check,
    _validate_step,
    _validate_world,
    _verdict_counts,
)

E2E_SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios" / "e2e"

PROFILES = ("stub", "native", "flaky", "degraded")
_TRANSPORTS = (
    "auto", "native-args", "args-json", "args-fenced", "args-junk",
    "text", "text-fenced", "text-trailing-comma", "prose",
)
# ``via`` -> (uses_tool_call, text_style) per profile family. ``None`` means the
# variant is not available for that profile (validated at load time).
_TEXT_STYLES = ("strict", "fenced", "junk", "trailing-comma", "prose")
#: ``via`` -> what the adapter actually replies, per profile family. "live" is a
#: native-tools provider; "degraded" has no tools, so the tool-only ``args-*``
#: variants degrade to the matching JSON *text* shape (same payload, text
#: channel) — a tool-less model cannot emit a tool call, so the harness sends
#: what such a model would send.
_TRANSPORT: dict[str, dict[str, Any]] = {
    "auto": {"stub": ("payload", None), "live": (True, "strict"), "degraded": (False, "strict")},
    "native-args": {"live": (True, "strict"), "degraded": (False, "strict")},
    "args-json": {"live": (True, "strict"), "degraded": (False, "strict")},
    "args-fenced": {"live": (True, "fenced"), "degraded": (False, "fenced")},
    "args-junk": {"live": (True, "junk"), "degraded": (False, "junk")},
    "text": {"live": (False, "strict"), "degraded": (False, "strict")},
    "text-fenced": {"live": (False, "fenced"), "degraded": (False, "fenced")},
    "text-trailing-comma": {"live": (False, "trailing-comma"),
                            "degraded": (False, "trailing-comma")},
    "prose": {"live": (False, "prose"), "degraded": (False, "prose")},
}
_SCENARIO_KEYS = ("name", "description", "world", "options", "matrix", "sessions", "checks")
_SESSION_KEYS = ("name", "profile", "model", "turns", "expect_stats")
_TURN_KEYS = ("input", "turn", "replies", "steps", "expect", "checks", "bait")
_REPLY_KEYS = ("envelope", "text", "via", "note")
_OPTION_KEYS = ("seed", "rng", "turn", "context_window")
_MATRIX_KEYS = ("sessions", "tables", "compare_prompts")
_DIGEST_TABLES = (
    "world", "characters", "npcs", "leads", "world_facts", "relationship_ledger",
    "moods", "chronicle", "npc_memory", "saga_levels",
)
_EXPECTATIONS = (
    "calls", "requests", "bait_detected", "bait_details_contain", "regenerations",
    "handling", "final_clean", "narration_contains", "narration_lacks",
    "system_contains", "prompt_contains", "prompt_lacks", "prompt_order", "deltas",
    "dialogue_count", "rejected_note_contains", "outcome", "note_contains",
)
_MEMORY_ACTIONS = ("add", "reinforce")


# --------------------------------------------------------------------------- #
# Scenario model + loading
# --------------------------------------------------------------------------- #

@dataclass
class E2EScenario:
    """One pipeline-level scenario: N scripted sessions + cross-session checks."""

    name: str = ""
    description: str = ""
    world: dict | None = None
    options: dict = field(default_factory=dict)
    sessions: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    matrix: dict | None = None
    source: str = ""

    @classmethod
    def from_mapping(cls, mapping: Any, *, source: str = "") -> E2EScenario:
        if not isinstance(mapping, dict):
            raise ValueError(f"{source}: e2e scenario must be a mapping, got {type(mapping).__name__}")
        unknown = [key for key in mapping if key not in _SCENARIO_KEYS]
        if unknown:
            raise ValueError(
                f"{source}: unknown scenario key(s) {sorted(unknown)}; "
                f"allowed: {list(_SCENARIO_KEYS)}"
            )
        name = str(mapping.get("name") or "").strip()
        if not name:
            raise ValueError(f"{source}: scenario needs a non-empty 'name'")
        world = mapping.get("world")
        if world is not None:
            _validate_world(world, where=f"{source}: world")
        options = mapping.get("options") or {}
        if not isinstance(options, dict):
            raise ValueError(f"{source}: 'options' must be a mapping")
        unknown_options = [key for key in options if key not in _OPTION_KEYS]
        if unknown_options:
            raise ValueError(
                f"{source}: unknown option(s) {sorted(unknown_options)}; "
                f"allowed: {list(_OPTION_KEYS)}"
            )
        if "rng" in options and not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in options["rng"]
        ):
            raise ValueError(f"{source}: option 'rng' must be a list of integers")
        checks = mapping.get("checks") or []
        if not isinstance(checks, list):
            raise ValueError(f"{source}: 'checks' must be a list")
        for index, check in enumerate(checks, start=1):
            _validate_check(check, where=f"{source}: check {index}")

        sessions = mapping.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            raise ValueError(f"{source}: 'sessions' must be a non-empty list")
        seen: set[str] = set()
        for index, session in enumerate(sessions, start=1):
            where = f"{source}: session {index}"
            _validate_session(session, where=where)
            if session["name"] in seen:
                raise ValueError(f"{where}: duplicate session name {session['name']!r}")
            seen.add(session["name"])

        matrix = mapping.get("matrix")
        if matrix is not None:
            _validate_matrix(matrix, sessions, where=f"{source}: matrix")

        return cls(
            name=name,
            description=str(mapping.get("description") or ""),
            world=world,
            options=dict(options),
            sessions=list(sessions),
            checks=list(checks),
            matrix=dict(matrix) if matrix is not None else None,
            source=source,
        )

    def session(self, name: str) -> dict:
        for session in self.sessions:
            if session["name"] == name:
                return session
        raise ValueError(f"{self.source}: unknown session {name!r}")


def _validate_session(session: Any, *, where: str) -> None:
    if not isinstance(session, dict):
        raise ValueError(f"{where}: session must be a mapping")
    unknown = [key for key in session if key not in _SESSION_KEYS]
    if unknown:
        raise ValueError(
            f"{where}: unknown session key(s) {sorted(unknown)}; "
            f"allowed: {list(_SESSION_KEYS)}"
        )
    name = str(session.get("name") or "").strip()
    if not name:
        raise ValueError(f"{where}: session needs a non-empty 'name'")
    profile = str(session.get("profile") or "stub").strip()
    if profile not in PROFILES:
        raise ValueError(f"{where}: unknown profile {profile!r}; known: {list(PROFILES)}")
    turns = session.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"{where}: session needs a non-empty 'turns' list")
    for index, turn in enumerate(turns, start=1):
        _validate_turn(turn, profile=profile, where=f"{where}: turn {index}")
    stats = session.get("expect_stats")
    if stats is not None:
        if not isinstance(stats, dict) or not stats:
            raise ValueError(f"{where}: 'expect_stats' must be a non-empty mapping")
        unknown_stats = [key for key in stats if key not in _STATS_KEYS]
        if unknown_stats:
            raise ValueError(
                f"{where}: unknown expect_stats key(s) {sorted(unknown_stats)}; "
                f"known: {list(_STATS_KEYS)}"
            )


_STATS_KEYS = ("kind", "calls", "requests", "regenerations", "parse_failures", "repairs",
               "native_tools", "script_remaining", "remaining")


def _validate_turn(turn: Any, *, profile: str, where: str) -> None:
    if not isinstance(turn, dict):
        raise ValueError(f"{where}: turn must be a mapping")
    unknown = [key for key in turn if key not in _TURN_KEYS]
    if unknown:
        raise ValueError(
            f"{where}: unknown turn key(s) {sorted(unknown)}; allowed: {list(_TURN_KEYS)}"
        )
    if not str(turn.get("input") or "").strip():
        raise ValueError(f"{where}: turn needs a non-empty 'input'")
    if "turn" in turn and not isinstance(turn["turn"], int):
        raise ValueError(f"{where}: 'turn' must be an integer")
    if "bait" in turn and not isinstance(turn["bait"], bool):
        raise ValueError(f"{where}: 'bait' must be a boolean")
    replies = turn.get("replies")
    if not isinstance(replies, list) or not replies:
        raise ValueError(f"{where}: turn needs a non-empty 'replies' list")
    for index, reply in enumerate(replies, start=1):
        _validate_reply(reply, profile=profile, where=f"{where}: reply {index}")
    for index, step in enumerate(turn.get("steps") or [], start=1):
        _validate_e2e_step(step, where=f"{where}: step {index}")
    expect = turn.get("expect")
    if expect is not None:
        if not isinstance(expect, dict) or not expect:
            raise ValueError(f"{where}: 'expect' must be a non-empty mapping")
        unknown_expect = [key for key in expect if key not in _EXPECTATIONS]
        if unknown_expect:
            raise ValueError(
                f"{where}: unknown expectation key(s) {sorted(unknown_expect)}; "
                f"allowed: {list(_EXPECTATIONS)}"
            )
    for index, check in enumerate(turn.get("checks") or [], start=1):
        _validate_check(check, where=f"{where}: check {index}")


def _validate_reply(reply: Any, *, profile: str, where: str) -> None:
    if not isinstance(reply, dict):
        raise ValueError(f"{where}: reply must be a mapping")
    unknown = [key for key in reply if key not in _REPLY_KEYS]
    if unknown:
        raise ValueError(
            f"{where}: unknown reply key(s) {sorted(unknown)}; allowed: {list(_REPLY_KEYS)}"
        )
    body = [key for key in ("envelope", "text") if key in reply]
    if len(body) != 1:
        raise ValueError(f"{where}: exactly one of 'envelope' or 'text' is required")
    if body[0] == "envelope" and not isinstance(reply["envelope"], dict):
        raise ValueError(f"{where}: 'envelope' must be a mapping")
    if body[0] == "text" and not str(reply["text"] or "").strip():
        raise ValueError(f"{where}: 'text' must be a non-empty string")
    via = str(reply.get("via") or "auto").strip()
    if via not in _TRANSPORTS:
        raise ValueError(f"{where}: unknown transport {via!r}; known: {list(_TRANSPORTS)}")
    if profile == "stub" and via != "auto":
        raise ValueError(
            f"{where}: the 'stub' profile has no transport variants (got via={via!r}); "
            "use via='auto' and script the envelope directly"
        )
    family = "degraded" if profile == "degraded" else "live"
    if family not in _TRANSPORT[via] and profile != "stub":
        raise ValueError(f"{where}: transport {via!r} is not available for profile {profile!r}")


def _validate_e2e_step(step: Any, *, where: str) -> None:
    if not isinstance(step, dict):
        raise ValueError(f"{where}: step must be a mapping")
    if "memory" in step:
        unknown = [key for key in step
                   if key not in ("expect", "memory", "turn", "note")]
        if unknown:
            raise ValueError(
                f"{where}: unknown step key(s) {sorted(unknown)}; "
                "memory steps allow ['expect', 'memory', 'note', 'turn']"
            )
        expect = step.get("expect") or {}
        if not isinstance(expect, dict):
            raise ValueError(f"{where}: 'expect' must be a mapping")
        unknown_expect = [key for key in expect if key != "matched"]
        if unknown_expect:
            raise ValueError(
                f"{where}: unknown memory expect key(s) {sorted(unknown_expect)}; "
                "allowed: ['matched']"
            )
        spec = step["memory"]
        if not isinstance(spec, dict):
            raise ValueError(f"{where}: 'memory' must be a mapping")
        action = str(spec.get("action") or "").strip()
        if action not in _MEMORY_ACTIONS:
            raise ValueError(f"{where}: memory action must be one of {list(_MEMORY_ACTIONS)}")
        if not str(spec.get("statement") or "").strip():
            raise ValueError(f"{where}: memory step needs a 'statement'")
        if not str(spec.get("npc_id") or "").strip():
            raise ValueError(f"{where}: memory step needs an 'npc_id'")
        if "turn" in step and not isinstance(step["turn"], int):
            raise ValueError(f"{where}: 'turn' must be an integer")
        return
    if "update" in step and isinstance(step["update"], dict) and "ref" in step["update"]:
        update = step["update"]
        if "table" not in update or "data" not in update:
            raise ValueError(f"{where}: update by ref needs 'table' and 'data'")
        translated = dict(step)
        translated["update"] = dict(update, id=0)  # placeholder: the ref resolves at run time
        translated["update"].pop("ref")
        _validate_step(translated, where=where)
        return
    _validate_step(step, where=where)


def _validate_matrix(matrix: Any, sessions: list, *, where: str) -> None:
    if not isinstance(matrix, dict):
        raise ValueError(f"{where}: must be a mapping")
    unknown = [key for key in matrix if key not in _MATRIX_KEYS]
    if unknown:
        raise ValueError(
            f"{where}: unknown key(s) {sorted(unknown)}; allowed: {list(_MATRIX_KEYS)}"
        )
    names = [str(name) for name in matrix.get("sessions") or [s["name"] for s in sessions]]
    known = {session["name"] for session in sessions}
    missing = [name for name in names if name not in known]
    if missing:
        raise ValueError(f"{where}: unknown session(s) {missing}; known: {sorted(known)}")
    if len(names) < 2:
        raise ValueError(f"{where}: a matrix needs at least two sessions to compare")
    tables = matrix.get("tables")
    if tables is not None:
        if not isinstance(tables, list) or not tables:
            raise ValueError(f"{where}: 'tables' must be a non-empty list")
        unknown_tables = [name for name in tables if name not in _DIGEST_TABLES]
        if unknown_tables:
            raise ValueError(
                f"{where}: unknown table(s) {sorted(unknown_tables)}; "
                f"comparable: {list(_DIGEST_TABLES)}"
            )


def _e2e_scenario_files(paths: list | None, root: str | None) -> list[Path]:
    from .harness import _scenario_files  # local: keeps the import block flat

    directory = Path(root) if root is not None else E2E_SCENARIOS_DIR
    return _scenario_files(paths, str(directory))


def _read_e2e_mapping(path: Path) -> Any:
    if path.suffix == ".json":
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON ({exc})") from exc
    if path.suffix == ".py":
        name = f"_evals_e2e_{path.stem}_{abs(id(path))}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise ValueError(f"{path}: cannot be imported as a python module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        mapping = getattr(module, "E2E", None)
        if mapping is None:
            raise ValueError(f"{path}: module does not define E2E")
        return mapping
    raise ValueError(f"{path}: unsupported scenario file type {path.suffix!r} (use .json or .py)")


def load_e2e_scenarios(paths: list | None = None, *, root: str | None = None,
                       ) -> list[E2EScenario]:
    """Load + validate every e2e scenario file (explicit ``paths``, else ``root``)."""
    scenarios = [
        E2EScenario.from_mapping(_read_e2e_mapping(path), source=str(path))
        for path in _e2e_scenario_files(paths, root)
    ]
    seen: dict[str, str] = {}
    for scenario in scenarios:
        if scenario.name in seen:
            raise ValueError(
                f"duplicate scenario name {scenario.name!r}: "
                f"{seen[scenario.name]} and {scenario.source}"
            )
        seen[scenario.name] = scenario.source
    return scenarios


# --------------------------------------------------------------------------- #
# Narrator profiles (scripted transports)
# --------------------------------------------------------------------------- #

def _envelope_payload(envelope: Mapping[str, Any]) -> dict:
    """Normalize an envelope mapping into the wire payload shape."""
    payload: dict[str, Any] = {"narration": str(envelope.get("narration") or "")}
    dialogue = envelope.get("npc_dialogue") or []
    if dialogue:
        payload["npc_dialogue"] = [dict(entry) for entry in dialogue]
    deltas = envelope.get("deltas") or []
    if deltas:
        payload["deltas"] = [dict(delta) for delta in deltas]
    return payload


def _render_text(style: str, envelope: Mapping[str, Any] | None, text: str) -> str:
    """Render a reply body as the text a model would actually send."""
    if style == "prose":
        return text or str((envelope or {}).get("narration") or "")
    payload = _envelope_payload(envelope or {})
    body = json.dumps(payload, ensure_ascii=False)
    if style == "strict":
        return body
    if style == "fenced":
        return f"Here is the turn, as agreed:\n\n```json\n{body}\n```"
    if style == "junk":
        return f"{body}\n\n(Note: I kept the tally consistent with canon.)"
    if style == "trailing-comma":
        # ``,"}`` becomes `,}`: invalid JSON that the conservative trailing-comma
        # repair is expected to recover (spec §7's degradation chain).
        head, _, tail = body.rpartition("}")
        return f"{head},}}"
    raise ValueError(f"unknown text style {style!r}")  # pragma: no cover - validated


def _plan_reply(profile: str, reply: Mapping[str, Any], index: int,
                ) -> dict:
    """Resolve one reply spec into the concrete object the profile replays."""
    via = str(reply.get("via") or "auto").strip()
    bare = reply.get("envelope")
    envelope = dict(bare) if isinstance(bare, Mapping) else None
    text = str(reply.get("text") or "")
    note = str(reply.get("note") or "")
    if profile == "stub":
        if envelope is not None:
            return {"kind": "stub", "item": envelope, "via": via, "note": note}
        return {"kind": "stub", "item": ProposalSet(narration=text), "via": via, "note": note}
    family = "degraded" if profile == "degraded" else "live"
    uses_tool, style = _TRANSPORT[via][family]
    body = _render_text(style, envelope, text)
    if uses_tool:
        arguments: Any = _envelope_payload(envelope or {})
        if style == "fenced":
            arguments = f"```json\n{json.dumps(arguments, ensure_ascii=False)}\n```"
        elif style == "junk":
            arguments = (f"{json.dumps(arguments, ensure_ascii=False)}\n"
                         "(Note: tally consistent with canon.)")
        elif style == "prose":
            arguments = ""
        return {
            "kind": "tool", "arguments": arguments, "text": "",
            "call_id": f"call-{index}", "via": via, "note": note,
        }
    narration_fallback = str((envelope or {}).get("narration") or "")
    return {"kind": "text", "text": body, "narration_fallback": narration_fallback,
            "via": via, "note": note}


class ScriptedAdapter:
    """Deterministic in-process BYOK adapter replaying one session's replies."""

    def __init__(self, profile: str, replies: list, *, native_tools: bool,
                 model: str) -> None:
        self.profile = profile
        self.name = f"fake-{profile}"
        self.model = model
        self.native_tools = bool(native_tools)
        self.script = list(replies)
        self.requests: list[dict] = []
        self.notes: list[str] = []

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(native_tools=self.native_tools, streaming=False,
                            json_mode=not self.native_tools, context_window=None,
                            probed_at=0)

    def complete(self, request: Any) -> ChatResponse:
        messages = list(getattr(request, "messages", []) or [])
        self.requests.append({
            "model": getattr(request, "model", ""),
            "tools": len(getattr(request, "tools", None) or []),
            "messages": len(messages),
            "last_role": getattr(messages[-1], "role", "") if messages else "",
            "retry": any("contradicted established canon" in str(getattr(m, "content", ""))
                         for m in messages),
        })
        if not self.script:
            raise _ScenarioError(
                f"narrator script exhausted after {len(self.requests)} call(s): "
                "the session scripted fewer replies than the pipeline asked for"
            )
        plan = self.script.pop(0)
        self.notes.append(str(plan.get("note") or ""))
        if plan["kind"] == "tool":
            arguments = plan["arguments"]
            return ChatResponse(
                text=str(plan.get("text") or ""),
                tool_calls=[ToolCall(id=str(plan.get("call_id") or "call"),
                                     name=PROPOSE_DELTAS_TOOL_NAME,
                                     arguments=arguments)],
                usage={"prompt_tokens": 0, "completion_tokens": 0},
                provider=self.name, model=self.model,
            )
        return ChatResponse(
            text=str(plan.get("text") or ""), tool_calls=[],
            usage={"prompt_tokens": 0, "completion_tokens": 0},
            provider=self.name, model=self.model,
        )

    def remaining(self) -> int:
        return len(self.script)


class RecordingNarrator:
    """``NarratorRunner`` wrapper that records every (prompt, reply) pair."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[dict] = []

    def narrate(self, *, prompt: Any, turn: int,
                retry_note: str | None = None) -> ProposalSet:
        reply = self.inner.narrate(prompt=prompt, turn=turn, retry_note=retry_note)
        self.calls.append({"turn": turn, "retry": retry_note is not None,
                           "prompt": prompt, "reply": reply,
                           "notes": list(getattr(self.inner, "last_notes", []) or [])})
        return reply

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _build_session_narrator(profile: str, reply_specs: list, *, model: str,
                            ) -> tuple[Any, ScriptedAdapter | None, list]:
    """Return ``(wrapped narrator, adapter or None, plan notes)``."""
    plans = [_plan_reply(profile, reply, index)
             for index, reply in enumerate(reply_specs, start=1)]
    if profile == "stub":
        inner: Any = StubNarrator(script=[plan["item"] for plan in plans])
        return RecordingNarrator(inner), None, plans
    adapter = ScriptedAdapter(profile, plans, native_tools=(profile != "degraded"),
                              model=model)
    provider = ProviderConfig(name=adapter.name, model=model, api_mode="openai")
    inner = LiveNarrator(adapter, provider, EngineConfig())
    return RecordingNarrator(inner), adapter, plans


# --------------------------------------------------------------------------- #
# Running one scenario
# --------------------------------------------------------------------------- #

@dataclass
class E2ESession(EvalSession):
    """One scripted session inside an e2e scenario (its own DB + narrator)."""

    name: str = ""
    profile: str = "stub"
    model: str = ""
    orchestrator: Any = None
    narrator: Any = None
    adapter: Any = None
    turns: list = field(default_factory=list)   # turn records, in order
    record: dict = field(default_factory=dict)  # the JSON-safe session record


def _turn_record_common(index: int, turn: int, player_input: str,
                        steps: list) -> dict:
    return {
        "index": index,
        "turn": turn,
        "input": player_input,
        "steps": steps,
        "calls": [],
        "narration": "",
        "dialogue": [],
        "system_lines": [],
        "outcome": {},
        "report": {"accepted": 0, "clamped": 0, "rejected": 0, "rejected_notes": []},
        "telemetry": {},
        "prompt": {},
        "consistency": {},
        "bait": None,
        "checks": [],
        "expect_failures": [],
        "failures": [],
        "ok": True,
    }


def _prompt_record(prompt: Any) -> dict:
    text = str(getattr(prompt, "text", "") or "")
    accounting = getattr(prompt, "accounting", None) or {}
    sections = getattr(prompt, "sections", None) or []
    return {
        "chars": len(text),
        "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(),  # noqa: S324 - not security
        "text": text,
        "section_bodies": {str(name): str(body) for name, body in sections},
        "sections": dict(accounting.get("sections") or {}),
        "budget": dict(accounting.get("budget") or {}),
        "allocations": dict(accounting.get("allocations") or {}),
        "compensation": dict(accounting.get("compensation") or {}),
        "dropped": list(accounting.get("dropped") or []),
    }


def _report_record(report: Any) -> dict:
    return {
        "accepted": len(report.accepted),
        "clamped": len(report.clamped),
        "rejected": len(report.rejected),
        "rejected_notes": [str(verdict.note) for verdict in report.rejected],
        "accepted_kinds": [str(getattr(verdict.delta, "kind", "")) for verdict in report.accepted],
    }


def _outcome_record(outcome: Any) -> dict:
    check = getattr(outcome, "check", None)
    return {
        "turn": getattr(outcome, "turn", None),
        "kind": getattr(outcome, "kind", None),
        "label": getattr(outcome, "label", ""),
        "verdict_line": getattr(outcome, "verdict_line", ""),
        "notes": list(getattr(outcome, "notes", []) or []),
        "band": getattr(check, "band", None),
        "roll": getattr(check, "roll", None),
        "total": getattr(check, "total", None),
        "dc": getattr(getattr(check, "request", None), "dc", None),
        "skill": getattr(getattr(check, "request", None), "skill", ""),
    }


def _memory_step(session: E2ESession, step: dict, index: int) -> dict:
    """``memory`` step: the game confirming/recording an NPC memory (public API)."""
    spec = dict(step["memory"])
    turn = int(step.get("turn", session.turn))
    action = str(spec["action"])
    npc_id = str(spec["npc_id"])
    statement = str(spec["statement"])
    bundle = session.orchestrator.memory_bundle()
    record: dict = {
        "index": index,
        "kind": "memory",
        "turn": turn,
        "note": str(step.get("note") or ""),
        "failures": [],
        "verdicts": [],
        "outcome": None,
        "report": None,
        "action": action,
        "npc_id": npc_id,
        "statement": statement,
        "matched": 0,
        "row_id": None,
    }
    session.steps.append(record)
    try:
        if action == "add":
            record["row_id"] = bundle.npc_memory.add(
                npc_id=npc_id, turn=turn, statement=statement,
                type=str(spec.get("type") or "observed"),
                sentiment=float(spec.get("sentiment") or 0.0),
            )
        else:
            record["matched"] = bundle.npc_memory.reinforce(
                npc_id=npc_id, statement=statement, turn=turn,
            )
            expect = step.get("expect") or {}
            if expect.get("matched") is not None and \
                    int(expect["matched"]) != int(record["matched"]):
                record["failures"].append(
                    f"step {index} (memory): expected {expect['matched']} match(es), "
                    f"got {record['matched']}"
                )
                session.failures.extend(record["failures"])
    except _ScenarioError:
        raise
    except Exception as exc:  # noqa: BLE001 - scenario-level failure, not a crash
        raise _ScenarioError(f"step {index} (memory) raised {type(exc).__name__}: {exc}") from exc
    finally:
        if turn >= session.turn:
            session.turn = turn + 1
    return record


def _run_e2e_step(session: E2ESession, step: dict, index: int) -> None:
    if "memory" in step:
        _memory_step(session, step, index)
        _append_step_telemetry(session, index, step)
        return
    translated = dict(step)
    update = translated.get("update")
    if isinstance(update, dict) and "ref" in update:
        update = dict(update)
        ref = str(update.pop("ref"))
        if ref not in session.refs:
            raise _ScenarioError(
                f"step {index} (update): unknown ref {ref!r}; known: {sorted(session.refs)}"
            )
        update["id"] = session.refs[ref]
        translated["update"] = update
    _run_step(session, translated, index)
    _append_step_telemetry(session, index, step)


def _run_e2e_session(scenario: E2EScenario, spec: dict, *, run_seed: Any,
                     directory: Path, context_window: int | None,
                     default_turn: int) -> tuple[E2ESession, list]:
    effective_seed = scenario.options.get("seed", run_seed)
    db_path = directory / f"{_slug(scenario.name)}-{_slug(spec['name'])}.sqlite"
    profile = str(spec.get("profile") or "stub")
    model = str(spec.get("model") or f"{profile}-model")
    sub_scenario = Scenario(
        name=f"{scenario.name} [{spec['name']}]",
        description=scenario.description,
        world=scenario.world,
        turns=[str(turn["input"]) for turn in spec["turns"]],
        checks=[],
        options=dict(scenario.options),
        source=scenario.source,
    )
    session = E2ESession(
        scenario=sub_scenario, seed=effective_seed, db_path=str(db_path),
        name=str(spec["name"]), profile=profile, model=model,
    )
    session.turn = int(scenario.options.get("turn", default_turn))
    telemetry: list = []
    session.telemetry = telemetry

    store = Store(db_path)
    session.store = store
    session.config = EngineConfig()
    session.validator = Validator(store, session.config)
    session.rng = _make_rng(scenario.options, effective_seed)

    all_replies = [reply for turn in spec["turns"] for reply in turn["replies"]]
    narrator, adapter, _plans = _build_session_narrator(profile, all_replies, model=model)
    session.narrator = narrator
    session.adapter = adapter
    session.orchestrator = Orchestrator(
        store, session.config, narrator, rng=session.rng,
        context_window=context_window,
    )
    try:
        for table, entries in (scenario.world or {}).items():
            for entry in entries:
                _insert_row(store, session, table, _prepare_row(table, entry))
        for index, turn_spec in enumerate(spec["turns"], start=1):
            turn = int(turn_spec.get("turn", session.turn))
            if turn >= session.turn:
                session.turn = turn
            steps = _run_steps(session, turn_spec.get("steps") or [])
            calls_before = len(narrator.calls)
            requests_before = (len(session.adapter.requests)
                               if session.adapter is not None else 0)
            record = _turn_record_common(index, turn, str(turn_spec["input"]), steps)
            session.turns.append(record)
            _run_pipeline_turn(session, record, turn_spec, turn)
            record["calls"] = [
                {"index": number + 1, "turn": call["turn"], "retry": call["retry"],
                 "notes": call["notes"]}
                for number, call in enumerate(narrator.calls[calls_before:])
            ]
            # Model round trips: a live narrator may call the adapter more than
            # once per narrate() (bounded regenerate-on-parse-failure).
            record["requests"] = (
                len(session.adapter.requests) - requests_before
                if session.adapter is not None else len(record["calls"])
            )
            prompt = (narrator.calls[calls_before]["prompt"]
                      if len(narrator.calls) > calls_before else None)
            record["prompt"] = _prompt_record(prompt) if prompt is not None else {}
            record["bait"] = _bait_scan(session, record, turn_spec, calls_before)
            _check_turn_expectations(session, record, turn_spec)
            for check_index, check in enumerate(turn_spec.get("checks") or [], start=1):
                _eval_e2e_check(
                    session, check, f"turn {index} check {check_index}", record,
                    namespace=_e2e_namespace(session, turn_index=index, record=record),
                )
            record["ok"] = not record["failures"]
            telemetry.append({
                "scenario": scenario.name, "session": session.name,
                "turn": turn, "kind": "turn",
                "problems": record["consistency"].get("problems"),
                "regenerations": record["consistency"].get("regenerations"),
                "rejected": record["report"].get("rejected"),
                "ok": record["ok"], "note": str(turn_spec.get("input") or "")[:60],
            })
            if turn >= session.turn:
                session.turn = turn + 1
        stats = _narrator_stats(session)
        unused = _unused_replies(adapter, all_replies, stats)
        if unused:
            session.failures.append(
                f"session {session.name}: {unused} scripted reply/replies were never "
                "consumed — the script no longer matches the pipeline (fix the turn "
                "scripts so every reply is used)"
            )
        session.record = _session_record(session, stats=stats)
        _check_session_stats(session, spec, stats)
        if session.failures:
            session.record["failures"] = list(session.failures)
    except _ScenarioError as exc:
        session.failures.append(str(exc))
        session.record = _session_record(session, stats=_narrator_stats(session))
        session.record["failures"] = list(session.failures)
    return session, telemetry


def _prepare_row(table: str, entry: Any) -> Any:
    """Fixture niceties that keep e2e worlds honest.

    ``npc_memory`` rows carry their decay rate: the game writes it from the
    entry's ``type`` (``NPCMemoryStore.add``). Fixtures may omit it and get the
    same config-derived rate instead of the column default (0 = never decays).
    """
    if table != "npc_memory" or not isinstance(entry, dict) or "decay_rate" in entry:
        return entry
    row = dict(entry)
    rates = EngineConfig().salience.decay_rates
    fallback = getattr(memory_module(), "DEFAULT_DECAY_RATE", 0.0)
    row["decay_rate"] = float(rates.get(str(row.get("type") or "").lower(), fallback))
    return row


def memory_module() -> Any:
    from engine import memory  # local: keeps the import block flat

    return memory


def _narrator_stats(session: E2ESession) -> dict:
    stats: dict = {}
    getter = getattr(session.narrator, "stats", None)
    if callable(getter):
        stats = dict(getter())
    if session.adapter is not None:
        stats["requests"] = len(session.adapter.requests)
        stats["remaining"] = session.adapter.remaining()
    return stats


def _run_steps(session: E2ESession, steps: list) -> list:
    records: list[dict] = []
    for index, step in enumerate(steps, start=1):
        _run_e2e_step(session, step, index)
        records.append(_step_record(session.steps[-1]))
    return records


def _step_record(step: dict) -> dict:
    return {
        "index": step.get("index"),
        "kind": step.get("kind"),
        "turn": step.get("turn"),
        "note": step.get("note"),
        "ok": not step.get("failures"),
        "failures": list(step.get("failures") or []),
        "verdicts": [_verdict_snapshot(verdict) for verdict in step.get("verdicts") or []],
        "outcome": _outcome_record(step["outcome"]) if step.get("outcome") else None,
        "action": step.get("action"),
        "npc_id": step.get("npc_id"),
        "statement": step.get("statement"),
        "matched": step.get("matched"),
        "row_id": step.get("row_id"),
    }


def _verdict_snapshot(verdict: Any) -> dict:
    return {"kind": verdict.kind, "note": verdict.note, "clamped_to": verdict.clamped_to}


def _run_pipeline_turn(session: E2ESession, record: dict, turn_spec: dict,
                       turn: int) -> None:
    orchestrator = session.orchestrator
    player_input = str(turn_spec["input"])
    try:
        result = orchestrator.take_turn(player_input=player_input, turn=turn)
    except Exception as exc:  # noqa: BLE001 - report as a scenario failure
        record["failures"].append(
            f"turn {turn}: take_turn raised {type(exc).__name__}: {exc}"
        )
        session.failures.extend(record["failures"])
        return
    session.reports.append(result.report)
    if result.mechanics is not None:
        session.outcomes.append(result.mechanics)
    record["narration"] = str(result.narration or "")
    record["dialogue"] = [dict(entry) for entry in (result.npc_dialogue or [])
                          if isinstance(entry, Mapping)]
    record["system_lines"] = [str(line) for line in (result.system_lines or [])]
    record["outcome"] = _outcome_record(result.mechanics) if result.mechanics else {}
    record["report"] = _report_record(result.report)
    record["telemetry"] = dict(result.telemetry or {})
    record["consistency"] = dict(
        (result.telemetry or {}).get("notes", {}).get("consistency", {}) or {})
    clean, details = orchestrator.consistency_check(
        turn=turn, narration=result.narration, report=result.report,
        dialogue=result.npc_dialogue,
    )
    record["final_clean"] = bool(clean)
    record["final_details"] = [str(detail) for detail in details]


def _bait_scan(session: E2ESession, record: dict, turn_spec: dict,
               calls_before: int) -> dict | None:
    """Scan the *first* draft the narrator sent against the same turn's report."""
    if not turn_spec.get("bait"):
        return None
    calls = session.narrator.calls
    if len(calls) <= calls_before:
        return {"clean": None, "details": ["no narrator call recorded for this turn"]}
    reply = calls[calls_before]["reply"]
    if not session.reports:
        return {"clean": None, "details": ["no commit report for this turn"]}
    report = session.reports[-1]
    clean, details = session.orchestrator.consistency_check(
        turn=record["turn"], narration=str(reply.narration or ""), report=report,
        dialogue=list(reply.npc_dialogue or []),
    )
    detail_list = [str(detail) for detail in details]
    return {"clean": bool(clean), "details": detail_list,
            "kinds": _detector_kinds(detail_list)}


#: Detector families (spec §3.6 Pass D). Pass D reports *stable one-line strings*
#: (``consistency_check`` docstring); these buckets map them to the kind of
#: contradiction so a scenario can assert *which classes* were caught.
def _detector_kind(detail: Any) -> str:
    text = str(detail)
    if "speaks in this turn's dialogue" in text:
        return "dead_npc_dialogue"
    if "is dead (alive = 0)" in text or "dead NPC" in text:
        return "dead_npc_prose"
    if "antonym pair" in text:
        return "pinned_fact_antonym"
    if "numeric conflict" in text:
        return "pinned_fact_numeric"
    if "negation flip" in text:
        return "pinned_fact_negation"
    if "contradicts pinned fact" in text:
        return "pinned_fact_other"
    if "restates a delta the validator rejected" in text:
        return "rejected_restatement"
    return "other"


def _detector_kinds(details: list) -> list:
    kinds: list[str] = []
    for detail in details:
        kind = _detector_kind(detail)
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def _unused_replies(adapter: Any, replies: list, stats: Mapping[str, Any] | None = None) -> int:
    """Scripted replies nobody consumed: a silent script/pipeline drift.

    Live profiles report it through the adapter; the stub narrator reports it as
    ``script_remaining`` in ``stats()``.
    """
    if adapter is not None:
        return int(adapter.remaining())
    return int((stats or {}).get("script_remaining") or 0)


def _e2e_namespace(session: E2ESession, *, turn_index: int | None = None,
                   record: dict | None = None, extra: dict | None = None) -> dict:
    """The core check namespace plus the e2e surfaces."""
    namespace = _namespace(session)
    bundle = session.orchestrator.memory_bundle() if session.orchestrator else None

    def _entries(npc_id: str = "npc:1") -> list[NPCMemoryEntry]:
        return [from_row(NPCMemoryEntry, row)
                for row in session.store.find("npc_memory", {"npc_id": npc_id},
                                              order_by="id")]

    def memory_entry(contains: str, npc_id: str = "npc:1") -> NPCMemoryEntry | None:
        hits = [entry for entry in _entries(npc_id) if contains in entry.statement]
        return hits[0] if len(hits) == 1 else None

    def memory_store() -> Any:
        if bundle is None:
            raise AssertionError("no memory bundle for this session")
        return bundle.npc_memory

    def memory_score(contains: str, turn: int, npc_id: str = "npc:1",
                     context: list | None = None) -> float:
        entry = memory_entry(contains, npc_id)
        if entry is None:
            raise AssertionError(f"no single npc_memory statement containing {contains!r}")
        return memory_store().score(
            entry, scene_context=list(context or []), turn=int(turn))

    def retrieve(turn: int, npc_id: str = "npc:1", context: list | None = None,
                 k: int | None = None) -> list[str]:
        pairs = memory_store().retrieve(
            npc_id=npc_id, scene_context=list(context or []), turn=int(turn), k=k)
        return [entry.statement for entry, _score in pairs]

    def rank_of(contains: str, turn: int, npc_id: str = "npc:1",
                context: list | None = None) -> int | None:
        for position, statement in enumerate(retrieve(turn, npc_id, context), start=1):
            if contains in statement:
                return position
        return None

    def stats(session_name: str | None = None) -> dict:
        target = session
        if session_name is not None and extra:
            target = extra.get("session_objects", {}).get(session_name, session)
        getter = getattr(target.narrator, "stats", None)
        return dict(getter() if callable(getter) else {})

    session_records = dict((extra or {}).get("session_records") or {})

    def telemetry_rows() -> list[dict]:
        return session.store.find("telemetry", order_by="id")

    namespace.update({
        "scenario": session.scenario,
        "sessions": session_records,
        "session_record": session.record,
        "profile": session.profile,
        "model": session.model,
        "turns": list(session.turns),
        "turn_index": turn_index,
        "record": record,
        "memory_entry": memory_entry,
        "memory_score": memory_score,
        "retrieve": retrieve,
        "rank_of": rank_of,
        "stats": stats,
        "telemetry_rows": telemetry_rows,
        "profiles": {name: (record_.get("stats") or {})
                     for name, record_ in session_records.items()},
    })
    if record is not None:
        namespace["turn"] = record.get("turn")
        namespace["prompt_text"] = (record.get("prompt") or {}).get("text", "")
        namespace["telemetry"] = record.get("telemetry") or {}
    namespace.update(extra or {})
    return namespace


#: Check expressions get the core builtins plus the math a salience assertion
#: needs (the core suite stays byte-identical: no core scenario uses these).
_E2E_CHECK_BUILTINS: dict[str, Any] = {
    **_CHECK_BUILTINS,
    "exp": math.exp,
    "log": math.log,
    "log1p": math.log1p,
    "sqrt": math.sqrt,
}


def _eval_e2e_check(session: E2ESession, check: dict, label: str,
                    record: dict | None, *, namespace: dict) -> bool:
    expr = str(check.get("expr") or "").strip()
    message = str(check.get("msg") or expr)
    try:
        value = eval(expr, {"__builtins__": {}, **_E2E_CHECK_BUILTINS}, namespace)  # noqa: S307
    except Exception as exc:  # noqa: BLE001 - a check never raises
        _fail_turn(session, record, f"{label} ({message}) raised "
                                    f"{type(exc).__name__}: {exc}")
        return False
    if not value:
        _fail_turn(session, record, f"{label} failed: {message}")
        return False
    return True


def _fail_turn(session: E2ESession, record: dict | None, message: str) -> None:
    session.failures.append(message)
    if record is not None:
        record["failures"].append(message)
        record["ok"] = False


# --------------------------------------------------------------------------- #
# Expectations
# --------------------------------------------------------------------------- #

def _check_turn_expectations(session: E2ESession, record: dict, turn_spec: dict) -> None:
    expect = turn_spec.get("expect") or {}
    consistency = record["consistency"]
    telemetry = record["telemetry"] or {}
    bait = record["bait"]
    failures: list[str] = []

    def _contains(key: str, needles: list, haystack: str) -> None:
        for needle in needles or []:
            if str(needle) not in haystack:
                failures.append(f"{key}: {needle!r} not found")

    if turn_spec.get("bait"):
        caught, why = _bait_caught(record)
        if not caught:
            failures.append(f"bait turn was not handled end to end: {why}")

    if "calls" in expect and len(record["calls"]) != int(expect["calls"]):
        failures.append(
            f"expected {expect['calls']} narrator call(s), got {len(record['calls'])}")
    if "requests" in expect and int(record.get("requests") or 0) != int(expect["requests"]):
        failures.append(
            f"expected {expect['requests']} model request(s), got {record.get('requests')}")
    if "bait_detected" in expect:
        detected = bool(bait and bait.get("clean") is False)
        if detected != bool(expect["bait_detected"]):
            failures.append(
                f"expected bait_detected={expect['bait_detected']!r}, got "
                f"{detected!r} (bait scan: {bait})")
    if "bait_details_contain" in expect:
        details = " | ".join((bait or {}).get("details") or [])
        _contains("bait_details_contain", list(expect["bait_details_contain"]), details)
    if "regenerations" in expect:
        got = int(consistency.get("regenerations") or 0)
        if got != int(expect["regenerations"]):
            failures.append(f"expected {expect['regenerations']} regeneration(s), got {got}")
    if "handling" in expect:
        failures.extend(_check_handling(str(expect["handling"]), consistency))
    if "final_clean" in expect:
        if bool(record.get("final_clean")) != bool(expect["final_clean"]):
            failures.append(
                f"expected final_clean={expect['final_clean']!r}, got "
                f"{record.get('final_clean')!r} ({record.get('final_details')})")
    if "narration_contains" in expect:
        _contains("narration_contains", list(expect["narration_contains"]),
                  record.get("narration", ""))
    if "narration_lacks" in expect:
        for needle in expect["narration_lacks"] or []:
            if str(needle) in record.get("narration", ""):
                failures.append(f"narration_lacks: {needle!r} present in the narration")
    if "system_contains" in expect:
        _contains("system_contains", list(expect["system_contains"]),
                  "\n".join(record.get("system_lines") or []))
    prompt_text = (record.get("prompt") or {}).get("text", "")
    if "prompt_contains" in expect:
        _contains("prompt_contains", list(expect["prompt_contains"]), prompt_text)
    if "prompt_lacks" in expect:
        for needle in expect["prompt_lacks"] or []:
            if str(needle) in prompt_text:
                failures.append(f"prompt_lacks: {needle!r} present in the prompt")
    if "prompt_order" in expect:
        failures.extend(_check_prompt_order(list(expect["prompt_order"]), prompt_text))
    if "dialogue_count" in expect:
        count = len(record.get("dialogue") or [])
        if count != int(expect["dialogue_count"]):
            failures.append(
                f"expected dialogue_count={expect['dialogue_count']}, got {count} "
                f"({[entry.get('name') for entry in record.get('dialogue') or []]})")
    if "deltas" in expect:
        wanted = expect["deltas"]
        if not isinstance(wanted, dict):
            failures.append("deltas expectation must be a mapping")
        else:
            for key, value in wanted.items():
                got = record["report"].get(key)
                if not _same(value, got):
                    failures.append(f"expected deltas.{key}={value!r}, got {got!r}")
    if "rejected_note_contains" in expect:
        _contains("rejected_note_contains", list(expect["rejected_note_contains"]),
                  " | ".join(record["report"].get("rejected_notes") or []))
    if "outcome" in expect:
        wanted = expect["outcome"]
        if not isinstance(wanted, dict):
            failures.append("outcome expectation must be a mapping")
        else:
            for key, value in wanted.items():
                got = record["outcome"].get(key)
                if not _same(value, got):
                    failures.append(f"expected outcome.{key}={value!r}, got {got!r}")
    if "note_contains" in expect:
        _contains("note_contains", list(expect["note_contains"]),
                  "\n".join((telemetry.get("notes", {}).get("mechanics", {}) or {})
                            .get("notes", []) or []))

    if failures:
        record["expect_failures"].extend(failures)
        session.failures.extend(failures)
        record["ok"] = False


def _check_handling(handling: str, consistency: Mapping[str, Any]) -> list[str]:
    regenerations = int(consistency.get("regenerations") or 0)
    sentences = int(consistency.get("patched_sentences") or 0)
    dialogue = int(consistency.get("patched_dialogue") or 0)
    problems = int(consistency.get("problems") or 0)
    if handling == "regen":
        if regenerations != 1:
            return [f"handling 'regen': expected 1 regeneration, got {regenerations}"]
        if sentences or dialogue:
            return [f"handling 'regen': patched {sentences} sentence(s)/{dialogue} "
                    "dialogue line(s); the retry was expected to be clean"]
        if problems:
            return [f"handling 'regen': {problems} problem(s) remained after regeneration"]
        return []
    if handling == "patch":
        if regenerations != 1:
            return [f"handling 'patch': expected 1 regeneration, got {regenerations}"]
        if problems and not (sentences + dialogue):
            return ["handling 'patch': problems remained after regeneration but nothing "
                    "was patched"]
        if not (sentences + dialogue):
            return ["handling 'patch': no sentence/dialogue patch was recorded"]
        return []
    return [f"unknown handling {handling!r} (use 'regen' or 'patch')"]


def _check_prompt_order(needles: list, prompt_text: str) -> list[str]:
    position = -1
    failures: list[str] = []
    for needle in needles:
        found = prompt_text.find(str(needle), position + 1)
        if found < 0:
            failures.append(f"prompt_order: {needle!r} missing or out of order")
            continue
        position = found
    return failures


def _bait_caught(record: dict) -> tuple[bool, str]:
    bait = record.get("bait") or {}
    consistency = record.get("consistency") or {}
    if bait.get("clean") is None:
        return False, "the first draft could not be scanned"
    if bait.get("clean") is not False:
        return False, "Pass D did not flag the bait narration"
    regenerations = int(consistency.get("regenerations") or 0)
    if regenerations < 1:
        return False, "no regeneration was recorded"
    if not record.get("final_clean"):
        return False, f"the shipped narration is still contradictory: {record.get('final_details')}"
    patched = int(consistency.get("patched_sentences") or 0) + \
        int(consistency.get("patched_dialogue") or 0)
    if int(consistency.get("problems") or 0) and not patched:
        return False, "problems survived regeneration with no patch recorded"
    return True, ""


def _check_session_stats(session: E2ESession, spec: dict, stats: dict) -> None:
    wanted = spec.get("expect_stats")
    if not wanted:
        return
    failures: list[str] = []
    for key, value in wanted.items():
        got = stats.get(key)
        if not _same(value, got):
            failures.append(
                f"session {session.name}: expected narrator stat {key}={value!r}, got {got!r}")
    if failures:
        session.failures.extend(failures)
        session.record.setdefault("stats_failures", []).extend(failures)
        session.record["failures"] = list(session.failures)


def _session_record(session: E2ESession, *, stats: dict | None = None) -> dict:
    return {
        "name": session.name,
        "profile": session.profile,
        "model": session.model,
        "seed": session.seed,
        "db": session.db_path,
        "turns": [dict(record) for record in session.turns],
        "stats": dict(stats if stats is not None else _narrator_stats(session)),
        "refs": dict(session.refs),
        "steps": [dict(step) for step in session.steps],
        "verdicts": _verdict_counts(session.verdicts),
        "failures": list(session.failures),
        "final_state": {
            table: session.store.find(table, order_by="id")
            for table in _DIGEST_TABLES
            if session.store is not None
        },
        "digest": _digest(session),
    }


def _digest(session: E2ESession) -> dict:
    """Cross-session comparable digest: state + per-turn outcomes (no timestamps)."""
    state = {
        table: session.store.find(table, order_by="id") for table in _DIGEST_TABLES
    }
    return {
        "state": state,
        "turns": [
            {
                "turn": record["turn"],
                "input": record["input"],
                "outcome": record["outcome"],
                "report": {key: record["report"].get(key) for key in
                           ("accepted", "clamped", "rejected", "rejected_notes")},
                "narration": record["narration"],
                "dialogue": record["dialogue"],
                "prompt_sha1": (record.get("prompt") or {}).get("sha1"),
                "prompt_chars": (record.get("prompt") or {}).get("chars"),
                "final_clean": record.get("final_clean"),
            }
            for record in session.turns
        ],
    }


def _compare_matrix(scenario: E2EScenario, sessions: dict[str, E2ESession]) -> dict:
    matrix = scenario.matrix or {}
    names = [str(name) for name in matrix.get("sessions") or list(sessions)]
    tables = list(matrix.get("tables") or _DIGEST_TABLES)
    compare_prompts = bool(matrix.get("compare_prompts", True))
    reference = names[0]
    base = sessions[reference].record["digest"]
    diffs: list[str] = []
    for other in names[1:]:
        candidate = sessions[other].record["digest"]
        for table in tables:
            if not _same(base["state"].get(table), candidate["state"].get(table)):
                diffs.append(f"{table}: {reference} and {other} diverge")
        for key in ("turns",):
            left = base[key] if compare_prompts else \
                [{k: v for k, v in turn.items() if k not in ("prompt_sha1", "prompt_chars")}
                 for turn in base[key]]
            right = candidate[key] if compare_prompts else \
                [{k: v for k, v in turn.items() if k not in ("prompt_sha1", "prompt_chars")}
                 for turn in candidate[key]]
            if not _same(left, right):
                diffs.append(f"{key}: {reference} and {other} diverge")
                diffs.extend(_first_turn_diffs(reference, base[key], other, candidate[key],
                                               compare_prompts=compare_prompts))
    return {
        "sessions": names,
        "tables": tables,
        "compare_prompts": compare_prompts,
        "equal": not diffs,
        "diffs": diffs,
    }


def _first_turn_diffs(left_name: str, left: list, right_name: str, right: list,
                      *, compare_prompts: bool) -> list[str]:
    details: list[str] = []
    for index, (left_turn, right_turn) in enumerate(zip(left, right, strict=False), start=1):
        for key in left_turn:
            if key in ("prompt_sha1", "prompt_chars") and not compare_prompts:
                continue
            if not _same(left_turn.get(key), right_turn.get(key)):
                details.append(
                    f"  turn {index} field {key!r}: {left_name}={left_turn.get(key)!r} "
                    f"vs {right_name}={right_turn.get(key)!r}"
                )
    if len(left) != len(right):
        details.append(f"  turn count differs: {left_name}={len(left)} vs {right_name}={len(right)}")
    return details[:8]


def _catch_records(scenario: E2EScenario, sessions: dict[str, E2ESession]) -> list[dict]:
    records: list[dict] = []
    for spec in scenario.sessions:
        session = sessions[spec["name"]]
        for turn_spec, record in zip(spec["turns"], session.turns, strict=False):
            if not turn_spec.get("bait"):
                continue
            caught, why = _bait_caught(record)
            records.append({
                "session": session.name,
                "turn": record["turn"],
                "input": record["input"],
                "caught": caught,
                "why": why,
                "detectors": list((record.get("bait") or {}).get("details") or []),
                "kinds": list((record.get("bait") or {}).get("kinds") or []),
                "handling": {
                    "regenerations": record["consistency"].get("regenerations"),
                    "patched_sentences": record["consistency"].get("patched_sentences"),
                    "patched_dialogue": record["consistency"].get("patched_dialogue"),
                    "problems_after_regen": record["consistency"].get("problems"),
                },
                "shipped_clean": bool(record.get("final_clean")),
            })
    return records


def _assertion_count(scenario: E2EScenario) -> int:
    total = len(scenario.checks)
    if scenario.matrix:
        total += 1
    for spec in scenario.sessions:
        if spec.get("expect_stats"):
            total += 1
        for turn in spec["turns"]:
            total += len(turn.get("checks") or [])
            if turn.get("expect"):
                total += 1
            if turn.get("bait"):
                total += 1
    return total


def _run_e2e_scenario(scenario: E2EScenario, *, run_seed: Any, directory: Path,
                      default_turn: int = 1) -> tuple[EvalResult, list]:
    result = EvalResult(name=scenario.name)
    telemetry: list = []
    assertions = _assertion_count(scenario)
    if assertions == 0:
        result.failures.append(
            "scenario defines no assertions (turn expectations/checks, session stats, "
            "bait cases, or matrix comparisons)")
    context_window = scenario.options.get("context_window")
    sessions: dict[str, E2ESession] = {}
    try:
        for spec in scenario.sessions:
            session, session_telemetry = _run_e2e_session(
                scenario, spec, run_seed=run_seed, directory=directory,
                context_window=context_window, default_turn=default_turn,
            )
            sessions[session.name] = session
            telemetry.extend(session_telemetry)
            result.failures.extend(session.failures)
        matrix = _compare_matrix(scenario, sessions) if scenario.matrix else None
        if matrix is not None and not matrix["equal"]:
            result.failures.append(
                "matrix: scripted sessions diverge across transports — "
                + "; ".join(matrix["diffs"][:6])
            )
        catches = _catch_records(scenario, sessions)
        catch_rate = (
            sum(1 for case in catches if case["caught"]) / len(catches)
            if catches else None
        )
        record_extras = {
            "session_objects": sessions,
            "session_records": {name: session.record for name, session in sessions.items()},
            "matrix": matrix,
            "catches": catches,
            "catch_rate": catch_rate,
        }
        if sessions:
            anchor = next(iter(sessions.values()))
            namespace = _e2e_namespace(anchor, extra=record_extras)
            for index, check in enumerate(scenario.checks, start=1):
                _eval_e2e_check(anchor, check, f"check {index}", None,
                                namespace=namespace)
        result.failures = list(dict.fromkeys(result.failures))
        result.passed = not result.failures
        result.snapshot = _scenario_snapshot(
            scenario, sessions, telemetry=telemetry, assertions=assertions,
            catch_rate=catch_rate, catches=catches, matrix=matrix,
        )
    finally:
        for session in sessions.values():
            store = session.store
            if store is not None:
                try:
                    store.close()
                except Exception:  # noqa: BLE001 - best effort
                    pass
    return result, telemetry


def _scenario_snapshot(scenario: E2EScenario, sessions: dict[str, E2ESession], *,
                       telemetry: list, assertions: int, catch_rate: float | None,
                       catches: list, matrix: dict | None) -> dict:
    return {
        "kind": "e2e",
        "seed": scenario.options.get("seed"),
        "context_window": scenario.options.get("context_window"),
        "assertions": assertions,
        "sessions": {name: session.record for name, session in sessions.items()},
        "catch_rate": catch_rate,
        "catches": catches,
        "matrix": matrix,
        "telemetry_rows": len(telemetry),
    }


# --------------------------------------------------------------------------- #
# Suite entry points
# --------------------------------------------------------------------------- #

def run_e2e_suite(paths: list | None = None, *, root: str | None = None,
                  seed: Any = None, db_dir: str | None = None) -> EvalReport:
    """Run each e2e scenario in throwaway DBs; aggregate into an EvalReport.

    Shaped exactly like ``harness.run_scenarios`` (same keyword surface, same
    ``EvalReport``), but every turn runs through ``Orchestrator.take_turn``.
    """
    scenarios = load_e2e_scenarios(paths, root=root)
    run_seed = DEFAULT_SEED if seed is None else seed
    owner: tempfile.TemporaryDirectory | None = None
    if db_dir is None:
        owner = tempfile.TemporaryDirectory(prefix="lorebound-e2e-")
        directory = Path(owner.name)
    else:
        directory = Path(db_dir)
        directory.mkdir(parents=True, exist_ok=True)
    report = EvalReport(kind="e2e", seed=run_seed, db_dir=str(directory))
    try:
        for scenario in scenarios:
            result, telemetry = _run_e2e_scenario(
                scenario, run_seed=run_seed, directory=directory)
            report.results.append(result)
            report.telemetry.extend(telemetry)
    finally:
        if owner is not None:
            owner.cleanup()
    report.total = len(report.results)
    report.passed = sum(1 for result in report.results if result.passed)
    report.failed = report.total - report.passed
    return report


def describe_e2e(scenario: E2EScenario) -> str:
    """One-line scenario summary (``--list``)."""
    profiles = ", ".join(f"{spec['name']}:{spec.get('profile', 'stub')}"
                         for spec in scenario.sessions)
    turns = sum(len(spec["turns"]) for spec in scenario.sessions)
    return f"{scenario.name} — {turns} turn(s), sessions [{profiles}]"


__all__ = [
    "E2E_SCENARIOS_DIR",
    "E2EScenario",
    "PROFILES",
    "RecordingNarrator",
    "ScriptedAdapter",
    "describe_e2e",
    "load_e2e_scenarios",
    "run_e2e",
    "run_e2e_suite",
]


def run_e2e(paths: list | None = None, *, root: str | None = None,
            seed: Any = None, db_dir: str | None = None) -> EvalReport:
    """Alias of :func:`run_e2e_suite` (the ``harness.run_e2e`` entry point)."""
    return run_e2e_suite(paths, root=root, seed=seed, db_dir=db_dir)
