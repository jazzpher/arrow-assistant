"""What the agent sees at one instant, plus helpers to compare frames."""
from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from .coords import Frame

THUMB_SIZE = (160, 90)
PIXEL_DELTA = 10          # gray levels a pixel must change to count
Point = tuple[int, int]
Rect = tuple[int, int, int, int]   # left, top, right, bottom (screen px)


@dataclass(frozen=True)
class Element:
    id: int
    name: str
    role: str
    rect: Rect                     # screen px
    enabled: bool = True
    password: bool = False

    @property
    def center(self) -> Point:
        return ((self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2)

    @property
    def area(self) -> int:
        return max(0, self.rect[2] - self.rect[0]) * max(0, self.rect[3] - self.rect[1])

    def contains(self, pt: Point) -> bool:
        return self.rect[0] <= pt[0] <= self.rect[2] and self.rect[1] <= pt[1] <= self.rect[3]


@dataclass
class Observation:
    frame: Frame
    app: str = "unknown"
    title: str = ""
    image_b64: str | None = None
    thumb: bytes = b""
    elements: list[Element] = field(default_factory=list)
    elements_text: str = ""
    hwnd: int = 0                  # focused window at observe time (0 = unknown)

    def element_by_id(self, eid: int) -> Element | None:
        for e in self.elements:
            if e.id == eid:
                return e
        return None

    def element_at(self, pt: Point) -> Element | None:
        """Smallest listed element under a screen point."""
        hits = [e for e in self.elements if e.contains(pt)]
        return min(hits, key=lambda e: e.area) if hits else None


def make_thumb(img: Image.Image) -> bytes:
    return img.convert("L").resize(THUMB_SIZE, Image.BILINEAR).tobytes()


def changed_fraction(a: bytes, b: bytes) -> float:
    """Fraction of thumbnail pixels that changed noticeably."""
    if not a or not b or len(a) != len(b):
        return 1.0
    n = sum(1 for x, y in zip(a, b) if abs(x - y) >= PIXEL_DELTA)
    return n / len(a)


def point_in_rects(pt: Point, rects) -> bool:
    return any(r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3] for r in rects)
