"""``python -m engine`` CLI (usage in engine/README.md).

Subcommands:
  play   --db PATH [--world fixture] [--stub | --provider NAME --model M
         --base-url URL --api-key-env VAR]  : interactive play loop
  state  --db PATH                : print the state snapshot (PlaySession.state_view)
  evals  [--root DIR]             : run the eval suites and print a report

``play`` defaults to the deterministic stub narrator (no model, no key). With
``--provider`` the API key is read from the environment at runtime only — it is
never echoed, logged, or persisted. Slash commands inside the loop: ``/state``,
``/help``, ``/quit`` (``/exit``); any other ``/...`` line becomes a meta turn.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from .config import EngineConfig
from .fixtures.demo_world import load_world
from .models import ProviderConfig
from .play import PlaySession

PROG = "python -m engine"
PROVIDER_MODES = ("openai", "anthropic", "gemini", "local")


class CliError(Exception):
    """Bad invocation: usage problem the user can fix (exit code 2)."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG, description="Lorebound engine CLI (spec §1, §10.1).",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    play = subs.add_parser("play", help="play a campaign turn by turn")
    play.add_argument("--db", default=EngineConfig().db_path, metavar="PATH",
                      help="campaign sqlite file (default: engine_state.db)")
    play.add_argument("--world", default=None, metavar="FIXTURE",
                      help="world fixture: 'demo' (default), a .json/.py file")
    play.add_argument("--stub", action="store_true",
                      help="force the deterministic offline narrator (default)")
    play.add_argument("--provider", default=None, metavar="NAME",
                      help=f"live narrator api mode: {', '.join(PROVIDER_MODES)}")
    play.add_argument("--model", default=None, metavar="MODEL",
                      help="model id for the live narrator (required with --provider)")
    play.add_argument("--base-url", default="", metavar="URL",
                      help="override the provider base URL (required for local servers)")
    play.add_argument("--api-key-env", default=None, metavar="VAR",
                      help="environment variable holding the API key (never stored)")

    state = subs.add_parser("state", help="print the campaign state snapshot as JSON")
    state.add_argument("--db", default=EngineConfig().db_path, metavar="PATH",
                       help="campaign sqlite file (default: engine_state.db)")

    evals = subs.add_parser("evals", help="run the eval scenario suite")
    evals.add_argument("--root", default=None, help="scenario directory (default: evals/scenarios/)")
    evals.add_argument("--scenario", action="append", default=[], metavar="PATH",
                       help="scenario file or directory (repeatable)")
    evals.add_argument("--seed", type=int, default=None, help="run seed for deterministic RNG")
    evals.add_argument("--db-dir", default=None, metavar="DIR",
                       help="keep per-scenario sqlite DBs here instead of a tempdir")
    evals.add_argument("--json", action="store_true", dest="as_json",
                       help="print the report as JSON")
    evals.add_argument("--list", action="store_true", dest="list_only",
                       help="list discovered scenarios without running them")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "play":
            return _play(args)
        if args.command == "state":
            return _state(args)
        return _evals(args)
    except CliError as exc:
        print(f"engine: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"engine: {exc}", file=sys.stderr)
        return 2


# --------------------------------------------------------------------------- #
# play
# --------------------------------------------------------------------------- #

def _play(args: argparse.Namespace) -> int:
    world = load_world(args.world) if args.world else None
    narrator = None
    adapter = None
    provider = None
    if args.provider:
        adapter, provider = _live_provider(args)
    elif args.model or args.base_url or args.api_key_env:
        raise CliError("--model/--base-url/--api-key-env need --provider")

    session = PlaySession.start(
        db_path=args.db, world=world, narrator=narrator,
        adapter=adapter, provider=provider,
    )
    try:
        _banner(args, session, live=adapter is not None)
        while True:
            if sys.stdout.isatty():
                print("> ", end="", flush=True)
            line = sys.stdin.readline()
            if line == "":  # EOF: leave cleanly with the campaign saved
                print("(end of input — campaign saved)")
                return 0
            text = line.strip()
            if not text:
                continue
            if text in ("/quit", "/exit"):
                print("(campaign saved)")
                return 0
            if text == "/state":
                print(json.dumps(session.state_view(), indent=2))
                continue
            if text == "/help":
                _print_help()
                continue
            _render(session.act(text))
    finally:
        session.close()


def _banner(args: argparse.Namespace, session: PlaySession, *, live: bool) -> None:
    world_row = session.store.find_one("world", order_by="id") or {}
    who = f"provider '{args.provider}', model '{args.model}'" if live else "stub narrator"
    print(f"Lorebound engine — {who}")
    print(f"db: {args.db}   world: {world_row.get('seed') or '(none)'}   "
          f"turn: {session.turn}")
    print("Type an action, /state for the snapshot, /help for commands, /quit to leave.")


def _print_help() -> None:
    print("/state  print the JSON state snapshot")
    print("/help   this list")
    print("/quit   save and leave (/exit works too)")


def _render(result) -> None:
    print(f"\n[turn {result.turn}] {result.narration}")
    for line in result.npc_dialogue or []:
        entry = line if isinstance(line, dict) else {}
        name = str(entry.get("name") or entry.get("npc_id") or "?")
        print(f'  {name}: "{entry.get("text") or ""}"')
    for note in result.system_lines or []:
        print(f"  ({note})")


def _live_provider(args: argparse.Namespace) -> tuple[object, ProviderConfig]:
    """Build the adapter for --provider; the key comes from the env at runtime."""
    from .providers.registry import build_adapter

    if not args.model:
        raise CliError("--provider needs --model")
    mode = str(args.provider).strip().lower()
    if mode not in PROVIDER_MODES:
        raise CliError(
            f"unknown provider {args.provider!r}; expected one of {', '.join(PROVIDER_MODES)}"
        )
    if mode == "local" and not args.base_url:
        raise CliError("--provider local needs --base-url (e.g. http://localhost:1234/v1)")
    api_key = None
    if args.api_key_env:
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            raise CliError(
                f"environment variable {args.api_key_env!r} is unset or empty"
            )
    provider = ProviderConfig(
        name=mode, model=args.model, base_url=args.base_url or "", api_mode=mode,
    )
    try:
        adapter = build_adapter(provider, api_key=api_key)
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    return adapter, provider


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #

def _state(args: argparse.Namespace) -> int:
    if not os.path.exists(args.db):
        print(f"engine: no campaign at {args.db!r}; play one first", file=sys.stderr)
        return 1
    session = PlaySession.start(db_path=args.db, auto_seed=False)
    try:
        if session.store.count("world") == 0:
            print(f"engine: {args.db!r} holds no campaign", file=sys.stderr)
            return 1
        print(json.dumps(session.state_view(), indent=2))
        return 0
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# evals
# --------------------------------------------------------------------------- #

def _evals(args: argparse.Namespace) -> int:
    """Delegate to the eval harness (import guarded: evals/ is not a package dep)."""
    try:
        from evals.harness import load_scenarios, run_scenarios
    except ImportError as exc:
        print(
            f"engine: the eval harness is unavailable ({exc}); run the CLI from "
            "the engine/ directory (python -m engine evals) or use python -m evals",
            file=sys.stderr,
        )
        return 1
    try:
        if args.list_only:
            for scenario in load_scenarios(args.scenario or None, root=args.root):
                description = f" — {scenario.description}" if scenario.description else ""
                print(f"{scenario.name}{description}")
            return 0
        report = run_scenarios(args.scenario or None, root=args.root, seed=args.seed,
                               db_dir=args.db_dir)
    except ValueError as exc:
        print(f"evals: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), indent=2) if args.as_json else report.render())
    return 0 if report.ok else 1
