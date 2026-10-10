"""Parsing the model's [POINT:x,y:label] and drawing instructions.

The vision model is asked to end its answer with one or more tags like
    [POINT:812,340:Export button]
giving the virtual-desktop pixel coordinates of the thing to click.
This module extracts those tags and returns the clean spoken text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

POINT_RE = re.compile(r"\[POINT:\s*(-?\d+)\s*,\s*(-?\d+)\s*:\s*([^\]\n]{1,80})\]", re.I)
# Anything that looks like a POINT tag but is not a valid one (no label,
# bad numbers, ...). It never becomes an arrow, but it must never be read
# out loud either.
JUNK_TAG_RE = re.compile(r"\[POINT:[^\]\n]{0,120}\]", re.I)

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
    spoken = JUNK_TAG_RE.sub("", POINT_RE.sub("", text))
    spoken = re.sub(r"\n{3,}", "\n\n", spoken).strip()
    return spoken, points[:MAX_POINTS]


_TAG_HEAD = "[POINT:"
_MAX_TAG_LEN = 140


class SpeechFilter:
    """Streaming filter: removes [POINT:...] tags from text on its way to
    the speaker and reports each point the moment its tag completes.

    A tag split across stream chunks (e.g. "...Export. [POI" then
    "NT:812,340:Export]") is held back until it resolves, so a half tag
    is never spoken. feed() returns (speakable_text, new_points).
    """

    def __init__(self) -> None:
        self._buf = ""
        self._count = 0

    def feed(self, chunk: str) -> tuple[str, list[Point]]:
        self._buf += chunk
        out: list[str] = []
        points: list[Point] = []
        while True:
            i = self._buf.find("[")
            if i < 0:
                out.append(self._buf)
                self._buf = ""
                break
            out.append(self._buf[:i])
            rest = self._buf[i:]
            m = POINT_RE.match(rest)
            junk = None if m else JUNK_TAG_RE.match(rest)
            if junk:                  # malformed tag: drop it, no arrow
                self._buf = rest[junk.end():]
                continue
            if m:
                if self._count < MAX_POINTS:
                    points.append(Point(int(m.group(1)), int(m.group(2)),
                                        m.group(3).strip()))
                    self._count += 1
                self._buf = rest[m.end():]
                continue
            head = rest.upper()
            could_grow = (
                _TAG_HEAD.startswith(head)
                or (head.startswith(_TAG_HEAD) and "]" not in rest
                    and "\n" not in rest and len(rest) < _MAX_TAG_LEN))
            if could_grow:
                self._buf = rest      # wait for more text
                break
            out.append("[")           # a plain bracket, not a tag
            self._buf = rest[1:]
        return "".join(out), points

    def flush(self) -> str:
        """End of stream: drop an unfinished tag, keep any other text."""
        rest, self._buf = self._buf, ""
        head = rest.upper()
        return "" if head.startswith(_TAG_HEAD) or _TAG_HEAD.startswith(head) else rest


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


