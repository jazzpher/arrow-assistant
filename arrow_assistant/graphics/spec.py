"""Scene spec: the validated, renderer-ready form of the LLM's [SCENE] JSON.

The model writes loose JSON; everything here coerces types, clamps counts
and sizes, drops malformed items, and fills defaults, so a creative model
can never crash the renderer or spam the screen. from_dict() never raises
on bad content - it returns a Scene plus a count of dropped items.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_ITEMS = 60
MAX_TEXT_CHARS = 200
MAX_TITLE_CHARS = 120
MAX_POLY_POINTS = 64
CANVAS = 1000.0            # 2D coordinates are 0-1000 on both axes
WORLD_LIMIT = 20.0         # |x|,|y|,|z| of any 3D point, world units
MAX_MESH_SEGMENTS = 48
MIN_MESH_SEGMENTS = 6

DEFAULT_FILL = "#2563eb"      # blue-600, matches the arrow overlay
DEFAULT_OUTLINE = "#1e293b"
DEFAULT_TEXT = "#e2e8f0"
DEFAULT_BG = "#0f172a"

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_NAMED = {
    "red": "#ef4444", "orange": "#f97316", "yellow": "#eab308",
    "green": "#22c55e", "teal": "#14b8a6", "blue": "#3b82f6",
    "purple": "#a855f7", "pink": "#ec4899", "white": "#f8fafc",
    "black": "#0f172a", "gray": "#94a3b8", "grey": "#94a3b8",
    "brown": "#92400e", "cyan": "#06b6d4",
}


def color(value, default: str) -> str:
    """Accept #rgb/#rrggbb or a small named set; anything else -> default."""
    if not isinstance(value, str):
        return default
    v = value.strip().lower()
    if v in _NAMED:
        return _NAMED[v]
    if _HEX.match(v):
        if len(v) == 4:                       # #abc -> #aabbcc
            v = "#" + "".join(c * 2 for c in v[1:])
        return v
    return default


def num(value, default: float = 0.0, lo: float = -1e6, hi: float = 1e6) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf guard
        return default
    return max(lo, min(hi, f))


def _vec(value, n: int, default: float, lo: float, hi: float) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < n:
        return None
    return [num(v, default, lo, hi) for v in value[:n]]


def _text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


@dataclass
class Item:
    """One drawable thing. kind selects the renderer branch; the rest are
    optional and interpreted per kind (see from_dict)."""
    kind: str
    data: dict = field(default_factory=dict)


@dataclass
class Scene:
    kind: str = "2d"                  # "2d" | "3d"
    title: str = ""
    caption: str = ""
    background: str = DEFAULT_BG
    autorotate: bool = True           # 3D: gentle spin until the user drags
    items: list[Item] = field(default_factory=list)
    dropped: int = 0                  # items rejected during validation

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "title": self.title,
            "caption": self.caption,
            "background": self.background,
            "autorotate": self.autorotate,
            "items": [{"type": it.kind, **it.data} for it in self.items],
        }


_2D_KINDS = {"rect", "ellipse", "line", "poly", "text"}
_3D_KINDS = {"box", "cylinder", "sphere", "cone", "arrow3d", "label"}


def _canvas_pt(v) -> list[float] | None:
    return _vec(v, 2, 0.0, -CANVAS, 2 * CANVAS)


def _world_pt(v) -> list[float] | None:
    return _vec(v, 3, 0.0, -WORLD_LIMIT, WORLD_LIMIT)


def _validate_2d(raw: dict) -> Item | None:
    kind = raw.get("type")
    if kind in ("rect", "ellipse"):
        pos = _canvas_pt([raw.get("x"), raw.get("y")])
        size = _vec([raw.get("w"), raw.get("h")], 2, 80.0, 1.0, CANVAS)
        if pos is None or size is None:
            return None
        d = {"x": pos[0], "y": pos[1], "w": size[0], "h": size[1],
             "fill": color(raw.get("fill"), DEFAULT_FILL),
             "outline": color(raw.get("outline"), ""),
             "radius": num(raw.get("radius"), 0.0, 0.0, 200.0),
             "label": _text(raw.get("label"), MAX_TEXT_CHARS)}
        if kind == "ellipse":
            d["radius"] = 0.0
        return Item(kind, d)
    if kind == "line":
        p1 = _canvas_pt([raw.get("x1"), raw.get("y1")])
        p2 = _canvas_pt([raw.get("x2"), raw.get("y2")])
        if p1 is None or p2 is None:
            return None
        return Item("line", {"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1],
                             "color": color(raw.get("color"), DEFAULT_FILL),
                             "width": num(raw.get("width"), 3.0, 1.0, 24.0),
                             "arrow": bool(raw.get("arrow", False)),
                             "label": _text(raw.get("label"), MAX_TEXT_CHARS)})
    if kind == "poly":
        pts = raw.get("points")
        if not isinstance(pts, (list, tuple)) or not (2 <= len(pts) <= MAX_POLY_POINTS):
            return None
        out = [_canvas_pt(p) for p in pts]
        if any(p is None for p in out):
            return None
        return Item("poly", {"points": out,
                             "fill": color(raw.get("fill"), DEFAULT_FILL),
                             "outline": color(raw.get("outline"), DEFAULT_OUTLINE),
                             "closed": bool(raw.get("closed", True))})
    if kind == "text":
        pos = _canvas_pt([raw.get("x"), raw.get("y")])
        text = _text(raw.get("text"), MAX_TEXT_CHARS)
        if pos is None or not text:
            return None
        return Item("text", {"x": pos[0], "y": pos[1], "text": text,
                             "size": num(raw.get("size"), 28.0, 10.0, 120.0),
                             "color": color(raw.get("color"), DEFAULT_TEXT),
                             "align": str(raw.get("align", "center")).lower()
                             if str(raw.get("align", "")).lower() in
                             ("left", "center", "right") else "center"})
    return None


def _validate_3d(raw: dict) -> Item | None:
    kind = raw.get("type")
    if kind == "label":
        at = _world_pt(raw.get("at"))
        text = _text(raw.get("text"), MAX_TEXT_CHARS)
        if at is None or not text:
            return None
        return Item("label", {"at": at, "text": text,
                              "color": color(raw.get("color"), DEFAULT_TEXT),
                              "size": num(raw.get("size"), 26.0, 10.0, 96.0)})
    if kind == "arrow3d":
        a = _world_pt(raw.get("from"))
        b = _world_pt(raw.get("to"))
        if a is None or b is None or a == b:
            return None
        return Item("arrow3d", {"from": a, "to": b,
                                "color": color(raw.get("color"), "#f97316")})
    if kind in ("box", "cylinder", "sphere", "cone"):
        center = _world_pt(raw.get("center", [0, 0, 0])) or [0.0, 0.0, 0.0]
        d: dict = {"center": center,
                   "color": color(raw.get("color"), DEFAULT_FILL),
                   "rotate": _vec(raw.get("rotate"), 3, 0.0, -360.0, 360.0)
                   or [0.0, 0.0, 0.0],
                   "label": _text(raw.get("label"), MAX_TEXT_CHARS)}
        if kind == "box":
            d["size"] = _vec(raw.get("size"), 3, 1.0, 0.05, WORLD_LIMIT) or [1, 1, 1]
        elif kind == "sphere":
            d["radius"] = num(raw.get("radius"), 1.0, 0.05, WORLD_LIMIT)
            d["segments"] = int(num(raw.get("segments"), 20,
                                    MIN_MESH_SEGMENTS, MAX_MESH_SEGMENTS))
        else:                              # cylinder / cone
            d["radius"] = num(raw.get("radius"), 1.0, 0.05, WORLD_LIMIT)
            d["height"] = num(raw.get("height"), 2.0, 0.05, WORLD_LIMIT)
            d["axis"] = str(raw.get("axis", "y")).lower() if str(
                raw.get("axis", "")).lower() in ("x", "y", "z") else "y"
            d["segments"] = int(num(raw.get("segments"), 20,
                                    MIN_MESH_SEGMENTS, MAX_MESH_SEGMENTS))
        return Item(kind, d)
    return None


def from_dict(raw) -> Scene:
    """Validate loose model JSON into a Scene. Never raises."""
    if not isinstance(raw, dict):
        return Scene(dropped=1)
    kind = str(raw.get("kind", "")).lower()
    if kind not in ("2d", "3d"):
        items0 = raw.get("items")
        kind = "3d" if isinstance(items0, list) and any(
            isinstance(i, dict) and i.get("type") in _3D_KINDS
            for i in items0) else "2d"
    scene = Scene(
        kind=kind,
        title=_text(raw.get("title"), MAX_TITLE_CHARS),
        caption=_text(raw.get("caption"), MAX_TEXT_CHARS),
        background=color(raw.get("background"), DEFAULT_BG),
        autorotate=bool(raw.get("autorotate", True)),
    )
    items = raw.get("items")
    if not isinstance(items, list):
        return scene
    if len(items) > MAX_ITEMS * 2:               # count truncated input
        scene.dropped += len(items) - MAX_ITEMS * 2
        items = items[:MAX_ITEMS * 2]
    validator = _validate_3d if kind == "3d" else _validate_2d
    for raw_item in items:
        if len(scene.items) >= MAX_ITEMS:
            scene.dropped += 1
            continue
        if not isinstance(raw_item, dict):
            scene.dropped += 1
            continue
        # Tolerate items of the other family: validate against both.
        item = validator(raw_item)
        if item is None:
            other = _validate_2d(raw_item) if validator is _validate_3d \
                else _validate_3d(raw_item)
            item = other
        if item is None:
            scene.dropped += 1
        else:
            scene.items.append(item)
    return scene
