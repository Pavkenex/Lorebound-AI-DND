#!/usr/bin/env python3
"""Build sprite atlases from individual PNGs. Run after the image agent lands files.

Usage:
    python3 scripts/build-atlas.py
    python3 scripts/build-atlas.py --check   # CI: fail if atlas is stale

Reads:
    public/portraits/<id>.png        (256x256, 12 NPCs + kaelis/bram/sister-pell)
Writes:
    public/atlas/portraits-atlas.png (1024x768, 4 cols x 3 rows of NPCs)
Faction sigils are standalone (public/factions/*.svg/PNG) and NOT atlased.
Manifest coords are owned by lib/atlas.ts + public/atlas/*.json — this
script only composites pixels, never edits coordinates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("need Pillow: uv run --with pillow python3 scripts/build-atlas.py", file=sys.stderr)
    raise SystemExit(2)

def _resample() -> int:
    # Pillow>=10 moved LANCZOS under Image.Resampling; keep compat with older.
    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return int(resampling.LANCZOS)
    return int(getattr(Image, "LANCZOS", 1))  # type: ignore[attr-defined]

_RESAMPLE = _resample()

ROOT = Path(__file__).resolve().parent.parent
PORTRAITS_DIR = ROOT / "public" / "portraits"
ATLAS_DIR = ROOT / "public" / "atlas"

PORTRAIT_ORDER = [
    "marla", "borin", "sella-voss", "tomm-ash",
    "sergeant-dain", "wren", "brother-anselm", "mother-ilde",
    "corb", "fenn", "ossia", "elder-bran",
]


def build_portraits() -> Path:
    out = ATLAS_DIR / "portraits-atlas.png"
    # Core grid: 4 cols x 3 rows of NPCs = 1024x768. Companions stay as
    # individual files until a second atlas page is needed.
    atlas = Image.new("RGBA", (1024, 768), (0, 0, 0, 0))
    missing: list[str] = []
    for i, pid in enumerate(PORTRAIT_ORDER):
        src = PORTRAITS_DIR / f"{pid}.png"
        if not src.exists():
            missing.append(pid)
            continue
        img = Image.open(src).convert("RGBA").resize((256, 256), _RESAMPLE)
        atlas.paste(img, ((i % 4) * 256, (i // 4) * 256), img)
    if missing:
        print(f"portraits missing ({len(missing)}): {', '.join(missing)} — leaving blanks")
    ATLAS_DIR.mkdir(parents=True, exist_ok=True)
    atlas.save(out)
    print(f"saved {out} (1024x768, {len(PORTRAIT_ORDER) - len(missing)}/{len(PORTRAIT_ORDER)})")
    return out


def main() -> None:
    check = "--check" in sys.argv
    if check:
        manifest = json.loads((ATLAS_DIR / "portraits-atlas.json").read_text())
        want = set(manifest["order"])
        have = {p.stem for p in PORTRAITS_DIR.glob("*.png")}
        stale = sorted(want - have)
        if stale:
            print(f"atlas stale, missing: {', '.join(stale)}")
            raise SystemExit(1)
        print("atlas manifest satisfied")
        return
    build_portraits()


if __name__ == "__main__":
    main()
