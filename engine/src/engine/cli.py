"""``python -m engine`` CLI (usage in engine/README.md).

Subcommands (R7 implements):
  play   --db PATH [--world fixture] [--stub | --provider NAME --model M
         --base-url URL --api-key-env VAR]  : interactive play loop
  state  --db PATH                : print the state snapshot (PlaySession.state_view)
  evals  [--root DIR]             : run the eval suites and print a report
"""
from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    raise NotImplementedError("R7 card implements cli.main")
