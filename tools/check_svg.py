#!/usr/bin/env python3
"""
Check the diagrams draw inside their own canvas.

Written because previewing an SVG turned out to be a bad way to verify one.
`qlmanage` showed a diagram with its right edge apparently cut when the file
was fine — that was preview scaling — and rendered a genuinely broken one
without complaint. Both mistakes, in opposite directions, in the same sitting.

Comparing element extents against the viewBox found the real overflows and
cleared the false alarm, so that is what runs now. Text width is estimated
from character count, generously, so this warns before a renderer with wider
metrics would actually clip.

    ./run-agentisizer.sh test     (included)
    python tools/check_svg.py     (alone)
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = "{http://www.w3.org/2000/svg}"

# Generous: real glyphs average nearer 0.5em, so this errs toward complaining.
CHAR_WIDTH_EM = 0.60


def _text_extent(el, x: float) -> tuple[float, float]:
    size = float(el.get("font-size", 13))
    width = len("".join(el.itertext())) * size * CHAR_WIDTH_EM
    anchor = el.get("text-anchor", "start")
    if anchor == "middle":
        return x - width / 2, x + width / 2
    if anchor == "end":
        return x - width, x
    return x, x + width


def check_file(path: Path) -> list[str]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return [f"{path.name}: not valid XML — {e}"]

    box = root.get("viewBox")
    if not box:
        return [f"{path.name}: no viewBox, so it cannot scale"]
    _, _, W, H = (float(v) for v in box.split())

    problems: list[str] = []
    for el in root.iter():
        tag = el.tag.replace(NS, "")
        try:
            if tag == "text":
                x, y = float(el.get("x", 0)), float(el.get("y", 0))
                x0, x1 = _text_extent(el, x)
                label = "".join(el.itertext())[:32]
                if x0 < 0 or x1 > W:
                    problems.append(
                        f"{path.name}: text {label!r} spans {x0:.0f}..{x1:.0f}, canvas is {W:.0f}")
                if y > H or y < 0:
                    problems.append(f"{path.name}: text {label!r} at y={y:.0f}, canvas is {H:.0f}")
            elif tag == "rect":
                x, y = float(el.get("x", 0)), float(el.get("y", 0))
                w, h = float(el.get("width", 0)), float(el.get("height", 0))
                if x < 0 or y < 0 or x + w > W or y + h > H:
                    problems.append(
                        f"{path.name}: rect {x:.0f},{y:.0f} {w:.0f}x{h:.0f} leaves the canvas")
            elif tag == "circle":
                cx, cy = float(el.get("cx", 0)), float(el.get("cy", 0))
                r = float(el.get("r", 0))
                if cx - r < 0 or cy - r < 0 or cx + r > W or cy + r > H:
                    problems.append(f"{path.name}: circle at {cx:.0f},{cy:.0f} r{r:.0f} leaves the canvas")
            elif tag == "line":
                for ax, ay in (("x1", "y1"), ("x2", "y2")):
                    x, y = float(el.get(ax, 0)), float(el.get(ay, 0))
                    if not (0 <= x <= W and 0 <= y <= H):
                        problems.append(f"{path.name}: line endpoint {x:.0f},{y:.0f} outside the canvas")
        except ValueError:
            continue          # a unit-bearing attribute; not our business
    return problems


def check() -> list[str]:
    svgs = sorted((ROOT / "docs").glob("*.svg"))
    if not svgs:
        return ["docs/ contains no SVGs — did they move?"]
    return [p for f in svgs for p in check_file(f)]


def main() -> int:
    problems = check()
    if problems:
        print(f"{len(problems)} drawing problem(s):")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    count = len(list((ROOT / "docs").glob("*.svg")))
    print(f"✓ all {count} diagrams draw inside their canvas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
