"""Set-of-marks grounding via Windows UI Automation.

Instead of asking a weak model to guess pixels, we list the real
clickable controls of the focused window, number them, draw the numbers
on the screenshot, and let the model answer "element 14". Apps with no
UIA tree (games, some canvases) fall back to raw x,y automatically.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable

from PIL import Image, ImageDraw, ImageFont

from .coords import Frame
from .observation import Element

MAX_ELEMENTS = 60
MAX_NODES = 600
MAX_DEPTH = 14
BUDGET_S = 1.5
MIN_SIDE = 8

CLICKABLE = {
    "ButtonControl", "MenuItemControl", "TabItemControl", "CheckBoxControl",
    "RadioButtonControl", "ComboBoxControl", "EditControl", "HyperlinkControl",
    "ListItemControl", "TreeItemControl", "SplitButtonControl", "DocumentControl",
    "MenuControl", "SliderControl", "SpinnerControl", "ToolBarControl",
    "TextControl",
}
INPUT_ROLES = {"EditControl", "DocumentControl", "ComboBoxControl"}
# plain text only when it looks like a link/label worth clicking
NAMELESS_SKIP = {"TextControl", "ListItemControl", "TreeItemControl",
                 "ToolBarControl", "MenuControl"}


def _role(raw: str) -> str:
    return raw[:-7] if raw.endswith("Control") else raw


def walk_controls(root: Any, max_nodes: int = MAX_NODES, max_depth: int = MAX_DEPTH,
                  budget_s: float = BUDGET_S,
                  clock: Callable[[], float] = time.monotonic) -> list[dict]:
    """Breadth-first walk over duck-typed UIA controls -> raw dicts."""
    out: list[dict] = []
    deadline = clock() + budget_s
    queue = [(root, 0)]
    seen = 0
    while queue and seen < max_nodes and clock() < deadline:
        node, depth = queue.pop(0)
        seen += 1
        try:
            r = node.BoundingRectangle
            out.append({
                "name": (node.Name or "").strip(),
                "role": node.ControlTypeName,
                "rect": (int(r.left), int(r.top), int(r.right), int(r.bottom)),
                "enabled": bool(node.IsEnabled),
                "offscreen": bool(node.IsOffscreen),
                "password": bool(getattr(node, "IsPassword", False)),
            })
        except Exception:
            pass
        if depth < max_depth:
            try:
                for ch in node.GetChildren():
                    queue.append((ch, depth + 1))
            except Exception:
                continue
    return out


def filter_and_number(raw: Iterable[dict], frame: Frame,
                      max_n: int = MAX_ELEMENTS) -> list[Element]:
    m = frame.mon
    ml, mt, mr, mb = m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]
    mon_area = m["width"] * m["height"]
    kept: list[dict] = []
    for r in raw:
        role = r.get("role", "")
        if role not in CLICKABLE or r.get("offscreen"):
            continue
        l, t, rr, b = r["rect"]
        l, t, rr, b = max(l, ml), max(t, mt), min(rr, mr), min(b, mb)
        if rr - l < MIN_SIDE or b - t < MIN_SIDE:
            continue
        area = (rr - l) * (b - t)
        if area > 0.5 * mon_area and role not in INPUT_ROLES:
            continue
        name = r.get("name", "")
        if role in NAMELESS_SKIP and not name:
            continue
        kept.append({**r, "rect": (l, t, rr, b), "area": area})
    # de-duplicate near-identical boxes (keep the one with a name)
    kept.sort(key=lambda r: (not r["name"], r["area"]))
    unique: list[dict] = []
    for r in kept:
        if any(abs(r["rect"][i] - u["rect"][i]) <= 3 for u in unique
               for i in range(1) if all(abs(r["rect"][j] - u["rect"][j]) <= 3 for j in range(4))):
            continue
        unique.append(r)
    unique.sort(key=lambda r: (r["rect"][1] // 24, r["rect"][0]))
    return [Element(i + 1, r["name"][:60], _role(r["role"]), r["rect"],
                    r.get("enabled", True), r.get("password", False))
            for i, r in enumerate(unique[:max_n])]


def elements_to_text(elements: list[Element]) -> str:
    lines = []
    for e in elements:
        flag = "" if e.enabled else " (disabled)"
        pw = " (password field)" if e.password else ""
        lines.append(f"  {e.id}: {e.role} '{e.name}'{flag}{pw}")
    return "\n".join(lines)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def annotate(img: Image.Image, elements: list[Element], frame: Frame) -> Image.Image:
    """Draw numbered boxes on a model-space screenshot."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    f = _font(12)
    for e in elements:
        x1, y1 = frame.to_model(e.rect[0], e.rect[1])
        x2, y2 = frame.to_model(e.rect[2], e.rect[3])
        color = "#dc2626" if e.password else "#2563eb"
        d.rectangle([x1, y1, x2, y2], outline=color, width=2)
        tag = str(e.id)
        w = d.textlength(tag, font=f) + 6
        d.rectangle([x1, y1, x1 + w, y1 + 15], fill=color)
        d.text((x1 + 3, y1 + 1), tag, fill="white", font=f)
    return out


def collect_foreground(frame: Frame) -> list[Element]:
    """Real UIA collection of the focused window. Windows only."""
    import uiautomation as auto
    root = auto.GetForegroundControl()
    if root is None:
        return []
    return filter_and_number(walk_controls(root), frame)
