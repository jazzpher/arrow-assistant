"""Parsing the model's [POINT:x,y:label] and drawing instructions.

The vision model is asked to end its answer with one or more tags like
    [POINT:812,340:Export button]
giving the virtual-desktop pixel coordinates of the thing to click.
This module extracts those tags and returns the clean spoken text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

POINT_RE = re.compile(r"\[POINT:\s*(-?\d+)\s*,\s*(-?\d+)\s*:\s*([^\]\n]{1,80})\]")

MAX_POINTS = 3  # never clutter the screen with more arrows than this


@dataclass(frozen=True)
class Point:
    x: int
    y: int
    label: str


def parse_points(text: str) -> tuple[str, list[Point]]:
    """Split model output into (spoken_text, points). Tags are removed."""
    points = [Point(int(m.group(1)), int(m.group(2)), m.group(3).strip())
              for m in POINT_RE.finditer(text)]
    spoken = POINT_RE.sub("", text)
    spoken = re.sub(r"\n{3,}", "\n\n", spoken).strip()
    return spoken, points[:MAX_POINTS]


def route_points(points: list[Point], monitors: list[dict]) -> dict[int, list[Point]]:
    """Group points by index of the monitor that contains them.

    Pure function (unit-tested): monitors are mss-style dicts with
    left/top/width/height in virtual-desktop pixels.
    """
    routed: dict[int, list[Point]] = {}
    for pt in points:
        for i, m in enumerate(monitors):
            if (m["left"] <= pt.x < m["left"] + m["width"]
                    and m["top"] <= pt.y < m["top"] + m["height"]):
                routed.setdefault(i, []).append(pt)
                break
        else:
            routed.setdefault(0, []).append(pt)  # clamp to first monitor
    return routed


