"""Model-space <-> screen-space coordinates.

The model sees a downscaled screenshot of ONE monitor (longest edge at
most capture.MAX_EDGE). Everything it returns is in that image's pixels.
Frame remembers the monitor and scale so a click lands on the right
physical pixel, including on secondary monitors with negative origins.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..capture import MAX_EDGE


@dataclass(frozen=True)
class Frame:
    mon: dict            # {"left","top","width","height"} in virtual-desktop px
    scale: float         # model px per screen px (<= 1.0)

    @classmethod
    def for_monitor(cls, mon: dict, max_edge: int = MAX_EDGE) -> "Frame":
        s = min(1.0, max_edge / max(mon["width"], mon["height"]))
        return cls(dict(mon), s)

    @property
    def size(self) -> tuple[int, int]:
        """Size of the image the model sees."""
        return (max(1, round(self.mon["width"] * self.scale)),
                max(1, round(self.mon["height"] * self.scale)))

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Model pixel -> virtual-desktop pixel, clamped inside the monitor."""
        w, h = self.size
        x = min(max(int(x), 0), w - 1)
        y = min(max(int(y), 0), h - 1)
        sx = self.mon["left"] + round(x / self.scale)
        sy = self.mon["top"] + round(y / self.scale)
        sx = min(max(sx, self.mon["left"]), self.mon["left"] + self.mon["width"] - 1)
        sy = min(max(sy, self.mon["top"]), self.mon["top"] + self.mon["height"] - 1)
        return sx, sy

    def to_model(self, sx: int, sy: int) -> tuple[int, int]:
        return (round((sx - self.mon["left"]) * self.scale),
                round((sy - self.mon["top"]) * self.scale))

    def contains_screen(self, sx: int, sy: int) -> bool:
        m = self.mon
        return (m["left"] <= sx < m["left"] + m["width"]
                and m["top"] <= sy < m["top"] + m["height"])
