"""Per-provider pointing adapters: model POINT coordinates -> screen pixels.

Each vision provider answers [POINT:x,y:label] in its own coordinate space:
Gemini on a 0-1000 grid over the screenshot (found by the real-Windows e2e
run), the OpenAI-compatible ones (OpenRouter, NVIDIA) in screenshot pixels.
An adapter turns a parsed Point into a virtual-desktop pixel the overlay can
draw at, after a sanity check:

- a point a little past the edge (models overshoot by a few px) is clamped
  inside the screenshot, then inside the monitor;
- a point far outside its space (more than OVERSHOOT of its extent), or one
  that isn't a finite number, is rejected: an arrow pinned to the screen edge
  points at the wrong thing, so no arrow is better than a wrong one.

ARROW_POINT_SPACE=pixels|norm1000 overrides the provider's default, for the
day a model update changes how it answers. ai.point_space reads it from here,
so the prompt and the mapping always agree.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

from .points import Point

PIXELS = "pixels"
NORM1000 = "norm1000"
SPACES = (PIXELS, NORM1000)
OVERSHOOT = 0.10          # how far past the edge still counts as "near the edge"
ENV_OVERRIDE = "ARROW_POINT_SPACE"

# provider name (config.LLMProvider.name) -> the space its POINT tags use
PROVIDER_SPACE = {"gemini": NORM1000, "openrouter": PIXELS, "nvidia": PIXELS}


@dataclass(frozen=True)
class PointAdapter:
    space: str

    def __post_init__(self) -> None:
        if self.space not in SPACES:
            raise ValueError(f"unknown point space {self.space!r}; expected one of {SPACES}")

    def extent(self, img_w: int, img_h: int) -> tuple[float, float]:
        """Width and height of the coordinate space the model answers in."""
        if self.space == NORM1000:
            return 1000.0, 1000.0
        return float(img_w), float(img_h)

    def to_image(self, x, y, img_w: int, img_h: int) -> tuple[int, int] | None:
        """Model coordinates -> pixel in the screenshot the model saw, clamped
        inside it; None when the point fails the sanity check."""
        if img_w < 1 or img_h < 1:
            return None
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        ew, eh = self.extent(img_w, img_h)
        if not (-OVERSHOOT * ew <= x <= (1 + OVERSHOOT) * ew
                and -OVERSHOOT * eh <= y <= (1 + OVERSHOOT) * eh):
            return None
        if self.space == NORM1000:
            x, y = x / 1000.0 * img_w, y / 1000.0 * img_h
        return (min(max(round(x), 0), img_w - 1),
                min(max(round(y), 0), img_h - 1))

    def to_screen(self, pt: Point, img_w: int, img_h: int, mon: dict,
                  scale: float) -> Point | None:
        """Model Point -> virtual-desktop Point inside `mon` (mss-style dict),
        where `scale` is model px per screen px (capture.scale_factor).
        None when the point fails the sanity check."""
        if not (isinstance(scale, (int, float)) and math.isfinite(scale) and scale > 0):
            return None
        px = self.to_image(pt.x, pt.y, img_w, img_h)
        if px is None:
            return None
        left, top = mon["left"], mon["top"]
        sx = min(max(left + round(px[0] / scale), left), left + mon["width"] - 1)
        sy = min(max(top + round(px[1] / scale), top), top + mon["height"] - 1)
        return Point(sx, sy, pt.label)


def space_for(provider) -> str:
    """The space this provider's POINT tags are in (env override first)."""
    forced = os.environ.get(ENV_OVERRIDE, "").strip().lower()
    if forced in SPACES:
        return forced
    return PROVIDER_SPACE.get(getattr(provider, "name", None), PIXELS)


def adapter_for(provider) -> PointAdapter:
    return PointAdapter(space_for(provider))
