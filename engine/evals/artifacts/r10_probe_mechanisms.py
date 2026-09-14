"""R10 clean-clone verification probes — mechanism spot checks (spec § refs).

Independent of the pytest suite: this script drives the *public* engine surface
against throwaway SQLite databases and re-derives the spec's claims from raw
rows + arithmetic (it never reads a test expectation). Run it from ``engine/``::

    .venv/bin/python evals/artifacts/r10_probe_mechanisms.py
    .venv/bin/python evals/artifacts/r10_probe_mechanisms.py --only ledger  # one mechanism

Each probe prints ``ok``/``FAIL``; the process exits 1 when anything failed.
``--only SUBSTRING`` matches the probe function name (e.g. ``probe_ledger``).
Stdlib only, no network, no keys, deterministic.

Probe map (mechanism -> probe -> spec section):

1.  store pragmas + idempotent DDL                -> §2
2.  salience top-K per present NPC, lazy recompute-> §3.1
3.  chronicle tail window + verbatim cap, FIFO    -> §3.2
4.  relationship ledger: append-only, decay math  -> §3.5
5.  mood half-life decay, ledger separation       -> §3.7
6.  contradiction scan + auto-pin heuristics      -> §3.6
7.  Pass A bands + eligibility gates (no roll)    -> §5A, §6
8.  validator conservation, clamps, rate limit    -> §6
9.  budget controller: drop invariants            -> §4
10. Pass D catch -> one regeneration -> patch     -> §5D, §8
11. lead state machine gating                     -> §3.4
12. degraded JSON-in-text protocol chain          -> §5B, §7
13. full offline loop determinism + telemetry     -> §1, §9, §10
14. decay_class derivation: validator + memory agree, tags win -> §3.5 (decision 1a)
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from engine.config import EngineConfig  # noqa: E402
from engine.fixtures import demo_world, seed_world  # noqa: E402
from engine.memory import MemoryBundle, auto_pin_worthy  # noqa: E402
from engine.models import (  # noqa: E402
    Character,
    CheckRequest,
    CommitReport,
    Delta,
    Intent,
    Lead,
    NPCMemoryEntry,
    from_row,
)
from engine.play import PlaySession, StubNarrator  # noqa: E402
from engine.providers.jsonproto import (  # noqa: E402
    parse_tolerant,
    parse_with_repair,
    proposals_from_payload,
    regenerate_note,
    should_regenerate,
)
from engine.resolve import resolve_action, resolve_check  # noqa: E402
from engine.store import Store  # noqa: E402
from engine.validate import Validator  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: object, detail: str = "") -> None:
    if ok:
        print(f"  ok   {name}")
        return
    line = f"  FAIL {name}" + (f" -- {detail}" if detail else "")
    print(line)
    FAILURES.append(name)


def close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(float(a) - float(b)) <= tol


def row_of(store: Store, table: str, where: dict | None = None) -> dict:
    """One row or a loud failure (probe assertions never silently no-op)."""
    row = store.find_one(table, where) if where else store.find_one(table, order_by="id")
    assert row is not None, f"no row in {table} for {where or 'order_by=id'}"
    return row


def fresh_store(tmp: Path, name: str) -> Store:
    store = Store(tmp / f"{name}.db")
    if store.count("world") == 0:
        seed_world(store, demo_world())
    return store


def npc_id(store: Store, name: str) -> int:
    row = row_of(store, "npcs", {"name": name})
    return int(row["id"])



class FixedRng:
    """RngSource returning a preset roll (d20 band probes)."""

    def __init__(self, value: int) -> None:
        self.value = int(value)

    def randint(self, a: int, b: int) -> int:
        return max(a, min(b, self.value))


# --------------------------------------------------------------------------- #
# 1. §2 store
# --------------------------------------------------------------------------- #

def probe_store(tmp: Path) -> None:
    print("probe 1: state store (spec §2) -- pragmas, 15 tables, idempotent DDL")
    store = fresh_store(tmp, "p1")
    journal = store.sql("PRAGMA journal_mode")[0]["journal_mode"]
    foreign_keys = store.sql("PRAGMA foreign_keys")[0]["foreign_keys"]
    check("journal_mode=wal", str(journal).lower() == "wal", f"got {journal!r}")
    check("foreign_keys=ON", int(foreign_keys) == 1, f"got {foreign_keys!r}")
    tables = store.sql(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    check("15 tables from the schema", len(tables) == 15, f"got {len(tables)}")
    before = store.count("npcs")
    store.close()
    reopened = Store(tmp / "p1.db")  # idempotent DDL: re-open must not raise/wipe
    check("re-open applies DDL idempotently", reopened.count("npcs") == before)
    reopened.close()


# --------------------------------------------------------------------------- #
# 2. §3.1 salience retrieval
# --------------------------------------------------------------------------- #

def probe_salience(tmp: Path) -> None:
    print("probe 2: NPC salience + top-K (spec §3.1)")
    store = fresh_store(tmp, "p2")
    memory = MemoryBundle.build(store)
    marla, tam = npc_id(store, "Marla Quist"), npc_id(store, "Tam")
    memory.npc_memory.add(npc_id=str(marla), turn=9,
                          statement="We agreed on the gate token by dusk.",
                          type="promise", sentiment=0.6)
    memory.npc_memory.add(npc_id=str(marla), turn=0,
                          statement="You broke your word at the well.",
                          type="grievance", sentiment=-0.9)
    memory.npc_memory.add(npc_id=str(marla), turn=0,
                          statement="The gate closes at dusk.",
                          type="factual", sentiment=0.0)
    memory.npc_memory.add(npc_id=str(tam), turn=9,
                          statement="You gave me a pretty stone from the well.",
                          type="kindness", sentiment=1.0)

    top = memory.npc_memory.retrieve(npc_id=str(marla), scene_context=[], turn=9, k=2)
    check("top-K returns exactly k", len(top) == 2, f"got {len(top)}")
    check("scoped to the requested NPC (never another NPC's entries)",
          all(entry.npc_id == f"npc:{marla}" for entry, _ in top),
          str([entry.npc_id for entry, _ in top]))
    check("newest promise outranks the stale grievance",
          top[0][0].statement == "We agreed on the gate token by dusk.")
    check("retrieve(k=0) returns nothing", memory.npc_memory.retrieve(
        npc_id=str(marla), scene_context=[], turn=9, k=0) == [])

    entries = store.find("npc_memory", order_by="id")
    scored = {
        row["statement"]: memory.npc_memory.score(
            from_row(NPCMemoryEntry, row), scene_context=[], turn=9)
        for row in entries
    }
    check("factual entry decays slower than an equally old emotional one",
          scored["The gate closes at dusk."] > scored["You broke your word at the well."],
          f"{scored}")
    entry_promise = next(
        from_row(NPCMemoryEntry, row) for row in entries
        if row["statement"] == "We agreed on the gate token by dusk.")
    later = memory.npc_memory.score(entry_promise, scene_context=[], turn=39)
    check("salience is recomputed lazily at retrieval time (older -> lower)",
          later < scored["We agreed on the gate token by dusk."],
          f"turn9={scored['We agreed on the gate token by dusk.']} turn39={later}")
    with_context = memory.npc_memory.score(
        entry_promise, scene_context=["the gate token by dusk"], turn=9)
    check("scene relevance term raises salience",
          with_context > scored["We agreed on the gate token by dusk."],
          f"with={with_context}")
    store.close()


# --------------------------------------------------------------------------- #
# 3. §3.2 chronicle
# --------------------------------------------------------------------------- #

def probe_chronicle(tmp: Path) -> None:
    print("probe 3: chronicle tail (spec §3.2)")
    store = fresh_store(tmp, "p3")
    memory = MemoryBundle.build(store)
    for turn in range(1, 16):
        memory.chronicle.append(
            turn_id=turn, actor="player", action_summary=f"action {turn}",
            mechanical_result="SUCCESS", verbatim_text=f"verbatim {turn}")
    tail = memory.chronicle.tail()
    check("tail window is 12 entries", len(tail) == 12, f"got {len(tail)}")
    check("tail is the newest window, oldest first",
          tail[0].turn_id == 4 and tail[-1].turn_id == 15,
          f"{tail[0].turn_id}..{tail[-1].turn_id}")
    verbatim = [entry.turn_id for entry in tail if entry.verbatim_text]
    check("verbatim text kept for the last 3 entries only",
          verbatim == [13, 14, 15], str(verbatim))
    check("storage is unbounded (history is not deleted)",
          store.count("chronicle") == 15, f"got {store.count('chronicle')}")
    store.close()


# --------------------------------------------------------------------------- #
# 4. §3.5 relationship ledger
# --------------------------------------------------------------------------- #

def probe_ledger(tmp: Path) -> None:
    print("probe 4: relationship ledger decay math (spec §3.5)")
    store = fresh_store(tmp, "p4")
    memory = MemoryBundle.build(store)
    marla_key = f"npc:{npc_id(store, 'Marla Quist')}"
    ledger = memory.ledger
    ledger.append_delta(npc_id=marla_key, player_id="player", turn=0, delta=-30,
                        reason="betrayal", category="trust")
    ledger.append_delta(npc_id=marla_key, player_id="player", turn=2, delta=-12,
                        reason="broken promise", category="trust")
    ledger.append_delta(npc_id=marla_key, player_id="player", turn=4, delta=-3,
                        reason="sharp words", category="affection")
    rows = store.find("relationship_ledger", order_by="id")
    check("magnitude rule tags the decay class (>=25 durable, >=10 slow, else fast)",
          [row["decay_class"] for row in rows] == ["durable", "slow", "fast"],
          str([row["decay_class"] for row in rows]))

    turn = 6
    currents = ledger.currents(npc_id=marla_key, player_id="player", turn=turn)
    anchor = 12.0  # Marla's disposition_base in the demo fixture
    rates = {"durable": 0.0, "slow": 0.01, "fast": 0.08}
    expected_total = anchor
    for row in rows:
        age = turn - int(row["turn"])
        expected_total += float(row["delta"]) * math.exp(-rates[row["decay_class"]] * age)
    expected_trust = anchor + sum(
        float(row["delta"]) * math.exp(-rates[row["decay_class"]] * (turn - int(row["turn"])))
        for row in rows if row["category"] == "trust")
    check("recomputing anchor + sum(delta*exp(-rate*age)) reproduces 'total'",
          close(currents["total"], expected_total),
          f"ledger={currents['total']} recomputed={expected_total:.6f}")
    check("per-category meter uses the same formula (anchor included)",
          close(currents["trust"], expected_trust),
          f"ledger={currents['trust']} recomputed={expected_trust:.6f}")
    check("every category + total are always present",
          set(currents) == {"trust", "affection", "respect", "fear", "debt", "total"},
          str(sorted(currents)))
    check("reading decays nothing (append-only rows untouched)",
          store.find("relationship_ledger", order_by="id") == rows)
    store.close()


# --------------------------------------------------------------------------- #
# 5. §3.7 moods
# --------------------------------------------------------------------------- #

def probe_moods(tmp: Path) -> None:
    print("probe 5: mood half-life decay + ledger separation (spec §3.7)")
    store = fresh_store(tmp, "p5")
    config = EngineConfig()
    memory = MemoryBundle.build(store, config)
    marla_key = f"npc:{npc_id(store, 'Marla Quist')}"
    memory.moods.apply(npc_id=marla_key, valence_delta=0.5, arousal_delta=0.8, turn=1)
    now = memory.moods.current(npc_id=marla_key, turn=1)
    check("spike applies on top of the personality baseline (0.15)",
          close(now.valence, 0.65) and close(now.arousal, 0.65),
          f"valence={now.valence} arousal={now.arousal}")
    half_life = config.memory.mood_half_life_turns  # 6 turns
    one_hl = memory.moods.current(npc_id=marla_key, turn=1 + int(half_life))
    two_hl = memory.moods.current(npc_id=marla_key, turn=1 + 2 * int(half_life))
    check("one half-life decays half the offset toward the baseline",
          close(one_hl.valence, 0.15 + 0.5 * 0.5), f"got {one_hl.valence}")
    check("two half-lives decay three quarters of it",
          close(two_hl.valence, 0.15 + 0.5 * 0.25), f"got {two_hl.valence}")
    check("a mood spike never writes to the relationship ledger",
          store.count("relationship_ledger") == 0)

    validator = Validator(store, config)
    verdicts = validator.validate(
        [Delta(kind="mood", target=marla_key,
               data={"valence_delta": 5.0, "arousal_delta": 0.0})], turn=1)
    check("validator clamps an over-range mood spike",
          verdicts[0].kind == "clamped", f"got {verdicts[0].kind} ({verdicts[0].note})")
    store.close()


# --------------------------------------------------------------------------- #
# 6. §3.6 facts
# --------------------------------------------------------------------------- #

def probe_facts(tmp: Path) -> None:
    print("probe 6: contradiction scan + auto-pin (spec §3.6)")
    store = fresh_store(tmp, "p6")
    config = EngineConfig()
    memory = MemoryBundle.build(store, config)
    fact_id = memory.facts.add(
        statement="The north gate is closed after dark.", turn=1,
        source="narrator", pinned=True)
    hits = memory.facts.contradiction_scan("The north gate stands open after dark.")
    check("an antonym flip against a pinned fact is flagged",
          fact_id in hits, f"hits={hits}")
    check("an unrelated statement is not flagged",
          memory.facts.contradiction_scan("The market sells salt in cones.") == [])
    validator = Validator(store, config)
    check("validator agrees with the fact store about canon conflicts",
          sorted(validator.check_contradiction("The north gate stands open after dark."))
          == sorted(hits))
    check("promises auto-pin",
          auto_pin_worthy("I promise to bring the ledger back before dusk."))
    check("ordinary statements do not auto-pin",
          not auto_pin_worthy("The market sells salt in cones."))
    store.close()


# --------------------------------------------------------------------------- #
# 7. §5A Pass A
# --------------------------------------------------------------------------- #

def probe_pass_a(tmp: Path) -> None:
    print("probe 7: Pass A bands + eligibility gates (spec §5A, §6)")
    store = fresh_store(tmp, "p7")
    band_cases = [
        (20, -50, "critical", "nat-20 is unconditional"),
        (1, 50, "critical_failure", "nat-1 is a critical failure"),
        (5, 0, "failure", "below dc"),
        (12, 0, "success_at_cost", "margin 0"),
        (13, 0, "success_at_cost", "margin 1"),
        (14, 0, "success_at_cost", "margin 2"),
        (15, 0, "success", "margin 3"),
    ]
    for roll, modifier, expected, label in band_cases:
        result = resolve_check(CheckRequest(skill="might", dc=12),
                               rng=FixedRng(roll), modifier=modifier)
        check(f"band {expected} ({label})", result.band == expected,
              f"roll={roll} mod={modifier} -> {result.band}")

    marla_id = npc_id(store, "Marla Quist")
    attack = resolve_action(
        Intent(kind="action", text="strike Marla Quist", target="Marla Quist"),
        rng=FixedRng(15), store=store, turn=1)
    check("an attack on a present, living NPC rolls a check",
          attack.kind == "attack" and attack.check is not None and attack.verdict_line,
          f"kind={attack.kind}")
    absent = resolve_action(
        Intent(kind="action", text="attack Hob Fen", target="Hob Fen"),
        rng=FixedRng(15), store=store, turn=1)
    check("an absent target is blocked with no roll",
          absent.kind == "blocked" and absent.check is None and "not present" in absent.notes[0],
          f"{absent.kind}: {absent.verdict_line}")
    store.update("npcs", marla_id, {"alive": 0})
    dead = resolve_action(
        Intent(kind="action", text="strike Marla Quist", target="Marla Quist"),
        rng=FixedRng(15), store=store, turn=2)
    check("a dead target is blocked with no roll",
          dead.kind == "blocked" and dead.check is None and "is dead" in dead.verdict_line,
          dead.verdict_line)
    meta = resolve_action(Intent(kind="meta", text="/save"), rng=FixedRng(15),
                          store=store, turn=3)
    check("meta intents never roll",
          meta.kind == "none" and meta.check is None, f"kind={meta.kind}")
    store.close()


# --------------------------------------------------------------------------- #
# 8. §6 validator
# --------------------------------------------------------------------------- #

def probe_validator(tmp: Path) -> None:
    print("probe 8: validator conservation / clamps / rate limits (spec §6)")
    store = fresh_store(tmp, "p8")
    config = EngineConfig()
    validator = Validator(store, config)
    marla_key = f"npc:{npc_id(store, 'Marla Quist')}"

    def commit(deltas: list[Delta], turn: int) -> CommitReport:
        verdicts = validator.validate(deltas, turn=turn)
        return validator.commit(verdicts, turn=turn)

    def stats() -> dict:
        return dict(from_row(Character, row_of(store, "characters")).stats or {})

    report = commit([Delta(kind="currency", target="", data={"amount": -40})], turn=1)
    balance = stats()["currency"]
    check("an overdraft is refused outright and the balance is unchanged",
          len(report.rejected) == 1 and balance == 6, f"balance={balance}")
    commit([Delta(kind="currency", target="", data={"amount": -3})], turn=2)
    balance = stats()["currency"]
    check("an affordable spend is applied", balance == 3, f"balance={balance}")

    report = commit([Delta(kind="stat", target="", data={"stat": "wits", "delta": 100})], turn=3)
    wits = stats()["wits"]
    check("stat clamps at the +30 ceiling and reports the applied value",
          wits == 30 and report.clamped and report.clamped[0].clamped_to == 27.0,
          f"wits={wits} clamped_to={report.clamped[0].clamped_to if report.clamped else None}")
    report = commit([Delta(kind="hp", target="", data={"delta": 50})], turn=4)
    hp = stats()["hp"]
    check("hp clamps at max_hp", hp == 11 and report.clamped, f"hp={hp}")

    for turn, delta in ((5, 90.0), (6, 40.0), (7, 40.0)):
        report = commit([Delta(kind="relationship", target=marla_key,
                               data={"category": "trust", "delta": delta,
                                     "reason": "probe"})], turn=turn)
    rows = store.find("relationship_ledger", {"npc_id": marla_key}, order_by="id")
    currents = MemoryBundle.build(store).ledger.currents(
        npc_id=marla_key, player_id="player", turn=7)
    rates = {"durable": 0.0, "slow": 0.01, "fast": 0.08}
    expected = 12.0 + sum(
        float(row["delta"]) * math.exp(-rates[row["decay_class"]] * (7 - int(row["turn"])))
        for row in rows)
    check("the per-event bound clamps +90 to +40, and magnitudes drive the classes",
          [row["delta"] for row in rows] == [40.0, 40.0, 8.0]
          and [row["decay_class"] for row in rows] == ["durable", "durable", "fast"],
          str([(row["delta"], row["decay_class"]) for row in rows]))
    check("the meter clamps at +100 off the stored current and reports the applied value",
          report.clamped and "±100" in report.clamped[0].note
          and report.clamped[0].clamped_to == 8.0 and close(currents["trust"], expected),
          f"meter={currents['trust']} recomputed={expected:.6f}")

    report = commit([Delta(kind="inventory_remove", target="coil of rope",
                           data={"qty": 2})], turn=8)
    check("removing more than held is refused (no partial removal)",
          report.rejected and "holds 1" in report.rejected[0].note,
          report.rejected[0].note if report.rejected else "accepted")

    statements = [
        "The tide bell rings twice at noon.",
        "A grey cat sleeps on the gatehouse steps.",
        "The cooper stocks tar and hemp rope.",
        "Rain has washed the market road flat.",
    ]
    report = commit([Delta(kind="fact", target="", data={"statement": text})
                     for text in statements], turn=9)
    check("fact rate limit: 3 new facts accepted, the 4th refused",
          len(report.accepted) == 3 and len(report.rejected) == 1
          and "rate limit" in report.rejected[0].note,
          f"accepted={len(report.accepted)} rejected={len(report.rejected)}")
    store.close()


# --------------------------------------------------------------------------- #
# 9. §4 budget controller
# --------------------------------------------------------------------------- #

def probe_budget(tmp: Path) -> None:
    print("probe 9: budget controller drop invariants (spec §4)")
    config = EngineConfig()
    config.budget.default_context_window = 1200  # 960 prompt + 240 reserve
    session = PlaySession.start(db_path=tmp / "p9.db", world=demo_world(),
                                config=config, narrator=StubNarrator())
    store, memory = session.store, session.orchestrator.memory_bundle()
    marla_key = f"npc:{npc_id(store, 'Marla Quist')}"
    for index in range(6):  # long entries so the memory group cannot fit them
        memory.npc_memory.add(
            npc_id=marla_key, turn=1,
            statement=("Marla keeps a long account of the shipment, the tally "
                       f"sticks, the rope slack, the damp sacks, entry {index}, " * 4),
            type="observed", sentiment=0.2)
    for turn in range(1, 41):  # a deep chronicle so the continuity group overflows
        memory.chronicle.append(
            turn_id=turn, actor="player",
            action_summary=f"turn {turn}: " + "the yard and the gate and the salt " * 8,
            mechanical_result="SUCCESS", verbatim_text="verbatim " * 40)

    result = session.act("I look around the yard.")
    telemetry, sections = result.telemetry, result.telemetry["sections"]
    dropped = {entry["section"] for entry in telemetry["dropped"]}
    check("mechanics are in the prompt and never dropped",
          sections.get("mechanics", 0) > 0 and "mechanics" not in dropped,
          f"sections={sections} dropped={sorted(dropped)}")
    check("pinned facts are in the prompt and never dropped",
          sections.get("pinned_facts", 0) > 0 and "pinned_facts" not in dropped)
    check("over budget pressure drops from the bottom (chronicle/npc memory)",
          {"chronicle", "npc_memory"} <= dropped, f"dropped={sorted(dropped)}")
    check("the assembled prompt stays inside the declared window",
          telemetry["prompt_tokens"] <= 960
          and telemetry["notes"]["mechanics"] is not None,
          f"used={telemetry['prompt_tokens']} of 960")
    check("telemetry records the budget allocation and the drops",
          set(telemetry["budget_alloc"]) == {"system", "scene", "memory", "continuity"}
          and bool(telemetry["dropped"]))
    session.close()


# --------------------------------------------------------------------------- #
# 10. §5D Pass D
# --------------------------------------------------------------------------- #

def probe_pass_d(tmp: Path) -> None:
    print("probe 10: Pass D catch -> regeneration -> patch (spec §5D, §8)")
    bait = {"narration": 'Marla Quist says, "You came late." The yard stays quiet.',
            "npc_dialogue": [], "deltas": []}
    clean = {"narration": "The yard keeps its distance, and the gate road stays empty.",
             "npc_dialogue": [], "deltas": []}
    quiet = {"narration": "The well rope creaks once, and nobody crosses the yard.",
             "npc_dialogue": [], "deltas": []}
    bait2 = {"narration": 'Marla Quist says, "Still late." The gate road stays empty.',
             "npc_dialogue": [], "deltas": []}
    # A regeneration consumes the next script entry: item 2 is the correction
    # round for turn 1, item 5 the (failing) correction round for turn 3.
    script = [bait, clean, quiet, bait2, bait2]
    session = PlaySession.start(db_path=tmp / "p10.db", world=demo_world(),
                                narrator=StubNarrator(script=script))
    store = session.store
    marla_id = npc_id(store, "Marla Quist")
    store.update("npcs", marla_id, {"alive": 0})

    first = session.act("I look for Marla in the yard.")
    consistency = first.telemetry["notes"]["consistency"]
    caught = [line for line in first.system_lines if line.startswith("consistency")]
    check("a dead NPC speaking is caught and regenerated once",
          consistency["regenerations"] == 1 and any("problem(s) found" in line for line in caught)
          and any("is dead" in line for line in caught),
          f"{consistency} lines={caught}")
    check("the original bait text never reaches the player",
          "says" not in first.narration and first.narration == clean["narration"],
          first.narration)

    second = session.act("I sit by the well and wait for dawn.")
    consistency = second.telemetry["notes"]["consistency"]
    check("clean prose is not regenerated",
          consistency["regenerations"] == 0 and consistency["problems"] == 0
          and second.narration == quiet["narration"],
          f"{consistency} narration={second.narration!r}")

    third = session.act("I check the wall for fever marks.")
    patched = third.telemetry["notes"]["consistency"]
    check("a bait that survives regeneration is patched (sentence dropped)",
          patched["regenerations"] == 1 and patched["patched_sentences"] == 1
          and "says" not in third.narration
          and any("sentence patch applied" in line for line in third.system_lines),
          f"{patched} narration={third.narration!r}")
    check("no mood row was ever created for the dead NPC",
          store.count("moods", {"npc_id": f"npc:{marla_id}"}) == 0)
    session.close()


# --------------------------------------------------------------------------- #
# 11. §3.4 lead gates
# --------------------------------------------------------------------------- #

def probe_lead_gates(tmp: Path) -> None:
    print("probe 11: lead state machine gating (spec §3.4)")
    store = fresh_store(tmp, "p11")
    validator = Validator(store, EngineConfig())
    verdicts = validator.validate(
        [Delta(kind="lead_transition", target="lead:3",
               data={"new_stage": "resolved", "justification": "eager"})], turn=1)
    validator.commit(verdicts, turn=1)
    stage = row_of(store, "leads", {"id": 3})["stage"]
    check("unheard -> resolved is refused and nothing is written",
          verdicts[0].kind == "rejected" and "illegal lead transition" in verdicts[0].note
          and stage == "unheard",
          f"{verdicts[0].note} stage={stage}")
    verdicts = validator.validate(
        [Delta(kind="lead_transition", target="lead:3",
               data={"new_stage": "rumored", "justification": "a rumour reached you"})], turn=2)
    validator.commit(verdicts, turn=2)
    lead = from_row(Lead, row_of(store, "leads", {"id": 3}))
    history = list(lead.stage_history or [])
    check("unheard -> rumored is legal and lands with a history entry",
          verdicts[0].kind == "accepted" and lead.stage == "rumored"
          and len(history) == 2 and history[-1]["stage"] == "rumored"
          and history[-1]["turn"] == 2,
          f"stage={lead.stage} history={history}")
    store.close()


# --------------------------------------------------------------------------- #
# 12. §5B/§7 degraded JSON protocol
# --------------------------------------------------------------------------- #

def probe_json_protocol(tmp: Path) -> None:
    print("probe 12: degraded JSON-in-text chain (spec §5B, §7)")
    payload = {
        "narration": "The gate hums against its hinges.",
        "npc_dialogue": [{"npc_id": "npc:1", "name": "Marla Quist", "text": "Hm."}],
        "deltas": [
            {"kind": "currency", "target": "", "data": {"amount": -1}},
            {"kind": "teleport", "target": "npc:1", "data": {}},
        ],
    }
    fenced = "Here is my reply:\n```json\n" + json.dumps(payload) + "\n```\nHope that helps."
    parsed = parse_tolerant(fenced)
    check("fenced JSON surrounded by prose is extracted", parsed == payload,
          f"got {parsed!r}")
    notes: list[str] = []
    proposals = proposals_from_payload(parsed or {}, notes=notes)
    check("payload decodes to one envelope (1 valid delta kept, 1 dropped with a note)",
          len(proposals.deltas) == 1 and len(proposals.npc_dialogue) == 1
          and any("teleport" in note for note in notes),
          f"deltas={len(proposals.deltas)} notes={notes}")

    repaired, note = parse_with_repair('{"narration": "x", "deltas": [],}')
    check("a trailing comma is repaired with a reported note",
          repaired == {"narration": "x", "deltas": []} and "trailing comma" in note,
          f"got {repaired!r} note={note!r}")
    broken, reason = parse_with_repair("no json in this reply at all")
    check("broken JSON is not guessed at", broken is None and bool(reason),
          f"got {broken!r}")
    check("regeneration is bounded",
          should_regenerate(None, attempts=1, max_attempts=2)
          and not should_regenerate(None, attempts=2, max_attempts=2))
    note = regenerate_note(reason or "")
    check("the regeneration note demands JSON only",
          "ONLY the JSON object" in note, note[:60])
    check("a parse that succeeds needs no regeneration",
          not should_regenerate(parsed, attempts=1, max_attempts=2))


# --------------------------------------------------------------------------- #
# 13. §1/§9/§10 full offline loop
# --------------------------------------------------------------------------- #

def probe_offline_loop(tmp: Path) -> None:
    print("probe 13: full offline loop determinism + telemetry (spec §1, §9, §10)")
    inputs = ["I greet Marla.", "I check the gate.", "I listen at the well.",
              "I wait for the salt run."]

    def run(name: str) -> dict:
        session = PlaySession.start(db_path=tmp / f"{name}.db", world=demo_world(),
                                    config=EngineConfig())
        turns = [session.act(text) for text in inputs]
        view = session.state_view()
        store = session.store
        out = {
            "narrations": [turn.narration for turn in turns],
            "verdicts": [turn.mechanics.verdict_line for turn in turns],
            "view": view,
            "telemetry": store.count("telemetry"),
            "turn_log": store.count("turn_log"),
            "chronicle": store.count("chronicle"),
        }
        session.close()
        return out

    first, second = run("p13a"), run("p13b")
    check("same seed + same inputs -> identical narration",
          first["narrations"] == second["narrations"])
    check("same seed + same inputs -> identical state view",
          json.dumps(first["view"], sort_keys=True) == json.dumps(second["view"], sort_keys=True))
    check("every turn carries a code-owned mechanics verdict line",
          all(line for line in first["verdicts"]), str(first["verdicts"]))
    check("turn log, telemetry and chronicle get one row per turn",
          first["turn_log"] == 4 and first["telemetry"] == 4 and first["chronicle"] == 4,
          f"{first['turn_log']}/{first['telemetry']}/{first['chronicle']}")
    check("the session runs with no adapter and no key (stub narrator)",
          first["view"]["turn"] == 4 and first["view"]["hp"] is not None)
    classify = PlaySession.start(db_path=tmp / "p13c.db", world=demo_world(),
                                 config=EngineConfig()).orchestrator
    check("intent classification is rules-first",
          classify.classify_intent("hello there, Marla").kind == "dialogue"
          and classify.classify_intent("/save").kind == "meta"
          and classify.classify_intent("I look around the yard again").kind in
          {"exploration", "action"},
          "dialogue/meta/exploration")


def probe_decay_class_seam(tmp: Path) -> None:
    print("probe 14: decay_class derivation — validator + memory share the magnitude rule (decision 1a)")
    store = fresh_store(tmp, "p14")
    config = EngineConfig()
    memory = MemoryBundle.build(store, config)
    marla_key = f"npc:{npc_id(store, 'Marla Quist')}"
    memory.ledger.append_delta(
        npc_id=marla_key, player_id=config.player_id, turn=0, delta=-30.0,
        reason="memory path: a 30-point betrayal", category="trust")
    validator = Validator(store, config)
    verdicts = validator.validate(
        [Delta(kind="relationship", target=marla_key,
               data={"category": "trust", "delta": -30.0,
                     "reason": "validator path: the same 30-point betrayal"})], turn=1)
    validator.commit(verdicts, turn=1)
    tagged = validator.validate(
        [Delta(kind="relationship", target=marla_key,
               data={"category": "trust", "delta": -30.0, "decay_class": "fast",
                     "reason": "validator path: same size, narrator tags it fast"})], turn=2)
    validator.commit(tagged, turn=2)
    rows = store.find("relationship_ledger", order_by="id")
    classes = [row["decay_class"] for row in rows]
    check("memory.append_delta derives 'durable' from the magnitude rule (>=25)",
          len(classes) == 3 and classes[0] == "durable", str(classes))
    check("a validator-committed delta of the same size now derives 'durable' too",
          verdicts[0].kind == "accepted" and classes[1] == "durable", str(classes))
    check("an explicit tag still wins over the magnitude rule",
          tagged[0].kind == "accepted" and classes[2] == "fast", str(classes))
    store.close()


PROBES = (
    probe_store,
    probe_salience,
    probe_chronicle,
    probe_ledger,
    probe_moods,
    probe_facts,
    probe_pass_a,
    probe_validator,
    probe_budget,
    probe_pass_d,
    probe_lead_gates,
    probe_json_protocol,
    probe_offline_loop,
    probe_decay_class_seam,
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    selector = ""
    if args:
        if len(args) == 2 and args[0] == "--only":
            selector = args[1].lower()
        else:
            print("usage: r10_probe_mechanisms.py [--only SUBSTRING]")
            return 2
    probes = [probe for probe in PROBES if selector in probe.__name__.lower()]
    if not probes:
        known = ", ".join(probe.__name__ for probe in PROBES)
        print(f"no probe matches {selector!r}; known probes: {known}")
        return 2
    with tempfile.TemporaryDirectory(prefix="r10-probes-") as tmpdir:
        tmp = Path(tmpdir)
        for probe in probes:
            probe(tmp)
    print()
    if FAILURES:
        print(f"RESULT: FAIL — {len(FAILURES)} probe check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print(f"RESULT: OK — all {len(probes)} mechanism probes passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
