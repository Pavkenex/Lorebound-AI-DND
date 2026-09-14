"""``python -m evals`` — run the eval suites and exit 0/1 (spec §9).

From the ``engine/`` directory::

    python -m evals                     # core scenario suite (default)
    python -m evals --suite e2e         # pipeline-level e2e suites
    python -m evals --suite all         # both
    python -m evals --list              # names + descriptions only
    python -m evals --json              # machine-readable report
    python -m evals --db-dir /tmp/evals # keep the throwaway DBs for inspection
    python -m evals --scenario evals/scenarios/conservation.json
"""
from __future__ import annotations

import argparse
import json
import sys

from .e2e import load_e2e_scenarios, run_e2e_suite
from .harness import load_scenarios, run_scenarios

_SUITES = ("core", "e2e", "all")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Lorebound engine eval harness — core + e2e suites (spec §9).",
    )
    parser.add_argument("--suite", choices=_SUITES, default="core",
                        help="which suite(s) to run (default: core)")
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


def _list(args: argparse.Namespace) -> int:
    if args.suite in ("core", "all"):
        for scenario in load_scenarios(args.scenario or None, root=args.root):
            description = f" — {scenario.description}" if scenario.description else ""
            print(f"{scenario.name}{description}")
    if args.suite in ("e2e", "all"):
        for scenario in load_e2e_scenarios(args.scenario or None, root=args.root):
            sessions = ", ".join(spec["name"] for spec in scenario.sessions)
            print(f"{scenario.name}{' — ' + scenario.description if scenario.description else ''}"
                  f"  [e2e: {sessions}]")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the suite(s); returns the process exit code (0 = everything passed)."""
    args = _parser().parse_args(argv)
    reports = []
    try:
        if args.list_only:
            return _list(args)
        if args.suite in ("core", "all"):
            reports.append(run_scenarios(args.scenario or None, root=args.root,
                                         seed=args.seed, db_dir=args.db_dir))
        if args.suite in ("e2e", "all"):
            reports.append(run_e2e_suite(args.scenario or None, root=args.root,
                                         seed=args.seed, db_dir=args.db_dir))
    except ValueError as exc:
        print(f"evals: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        payloads = [report.to_dict() for report in reports]
        payload = payloads[0] if len(payloads) == 1 else {
            "ok": all(report.ok for report in reports),
            "suites": {report.kind: payload
                       for report, payload in zip(reports, payloads, strict=True)},
        }
        print(json.dumps(payload, indent=2))
    else:
        print("\n\n".join(report.render() for report in reports))
    return 0 if all(report.ok for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
