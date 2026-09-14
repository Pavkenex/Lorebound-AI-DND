"""R10 probe meta-check: the probes must FAIL on a mutated engine.

A probe harness that only ever passes proves nothing. This script copies
``src/`` into a scratch tree, applies ONE targeted string mutation per case to
the mechanism a probe claims to cover, and runs
``evals/artifacts/r10_probe_mechanisms.py`` against the mutated copy — each case
must exit 1 with a ``RESULT: FAIL``. A clean (unmutated) copy is run first as
the control and must exit 0.

Run it from ``engine/``::

    .venv/bin/python evals/artifacts/r10_probe_mutations.py

Exit 0 = control passed and every mutation was detected. Scratch trees are
removed; nothing in the repo is modified.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
ENGINE = HERE.parents[2]  # engine/
PROBE = HERE.parent / "r10_probe_mechanisms.py"

# (label, file under engine/, anchor, replacement) — one mechanism each.
MUTATIONS: list[tuple[str, str, str, str]] = [
    ("resolve: margin 1-2 no longer a cost band", "src/engine/resolve.py",
     "elif margin <= 2:", "elif margin == 2 and False:"),
    ("memory: mood never decays (factor frozen)", "src/engine/memory.py",
     "factor = 0.5 ** (age / half_life)", "factor = 1.0"),
    ("memory: ledger decay factor removed", "src/engine/memory.py",
     "return math.exp(-rate * age)", "return 1.0"),
    ("validate: currency conservation removed", "src/engine/validate.py",
     "if balance + amount < 0:", "if balance + amount < -10_000:"),
    ("pipeline: Pass D ignores dead-NPC speech", "src/engine/pipeline.py",
     "problems.extend(self._dead_npc_speech(narration, dialogue or []))",
     "problems.extend([])"),
]


def run_probe(root: Path) -> subprocess.CompletedProcess:
    probe = root / "engine" / "evals" / "artifacts" / PROBE.name
    return subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True, text=True, cwd=str(root / "engine"), timeout=600,
    )


def stage(tmp: Path, name: str) -> Path:
    root = tmp / name
    shutil.copytree(ENGINE / "src", root / "engine" / "src")
    artifacts = root / "engine" / "evals" / "artifacts"
    artifacts.mkdir(parents=True)
    shutil.copy2(PROBE, artifacts / PROBE.name)
    return root


def failures_of(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.startswith("  FAIL")]


def main() -> int:
    if not PROBE.is_file():
        print(f"probe script not found next to this file: {PROBE}")
        return 1
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="r10-mutations-") as tmpdir:
        tmp = Path(tmpdir)

        control = run_probe(stage(tmp, "control"))
        if control.returncode == 0 and "RESULT: OK" in control.stdout:
            print("  ok   control (unmutated copy): probe exits 0")
        else:
            problems.append("control")
            print(f"  FAIL control exited {control.returncode}\n{control.stdout[-800:]}")

        for index, (label, relpath, anchor, replacement) in enumerate(MUTATIONS, start=1):
            root = stage(tmp, f"m{index}")
            target = root / "engine" / relpath
            text = target.read_text()
            if anchor not in text:
                problems.append(label)
                print(f"  FAIL {label}: mutation anchor not found ({anchor!r})")
                continue
            target.write_text(text.replace(anchor, replacement, 1))
            result = run_probe(root)
            detected = result.returncode == 1 and "RESULT: FAIL" in result.stdout
            detail = failures_of(result.stdout)[:2] or [f"exit {result.returncode}"]
            if detected:
                print(f"  ok   {label}: probe exits 1 -> {'; '.join(detail)}")
            else:
                problems.append(label)
                tail = re.sub(r"\s+", " ", (result.stdout or result.stderr))[-300:]
                print(f"  FAIL {label}: mutation NOT detected -> {tail}")

    print()
    if problems:
        print(f"RESULT: FAIL — undetected mutation(s): {problems}")
        return 1
    print(f"RESULT: OK — control clean, all {len(MUTATIONS)} mutations detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
