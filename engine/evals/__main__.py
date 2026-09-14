"""``python -m evals`` — run the core scenario suite and exit 0/1 (spec §9).

From the ``engine/`` directory::

    python -m evals                     # run every scenario in evals/scenarios/
    python -m evals --list              # names + descriptions only
    python -m evals --json              # machine-readable report
    python -m evals --db-dir /tmp/evals # keep the throwaway DBs for inspection
    python -m evals --scenario evals/scenarios/conservation.json
"""
from __future__ import annotations

import argparse
import json
import sys

from .harness import load_scenarios, run_scenarios


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Lorebound engine eval harness — core scenario suite (spec §9).",
    )
    parser.add_argument("--root", default=None,
                        help="scenario directory (default: evals/scenarios/)")
    parser.add_argument("--scenario", action="append", default=[], metavar="PATH",
                        help="scenario file or directory (repeatable)")
    parser.add_argument("--seed", type=int, default=None,
                        help="run seed for deterministic RNG (default: harness default)")
    parser.add_argument("--db-dir", default=None, metavar="DIR",
                        help="keep per-scenario sqlite DBs here instead of a tempdir")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="print the report as JSON")
    parser.add_argument("--list", action="store_true", dest="list_only",
                        help="list discovered scenarios without running them")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the suite; returns the process exit code (0 = all scenarios passed)."""
    args = _parser().parse_args(argv)
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


if __name__ == "__main__":
    raise SystemExit(main())
