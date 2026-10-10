"""Real screen observation on Windows (mss + Win32), behind a Protocol."""
from __future__ import annotations

from typing import Callable, Protocol

from PIL import Image, ImageDraw

from .. import capture
from .coords import Frame
from .observation import Observation, Rect, make_thumb


class Screen(Protocol):
    def observe(self, mask_rects: list[Rect] | None = None) -> Observation: ...
    def cursor(self) -> tuple[int, int]: ...
    # Optional live queries (the loop uses them when present):
    #   foreground() -> (app_exe, window_title[, hwnd])   right now, not at observe time
    #   focused_element() -> Element | None          control with keyboard focus


def mask_image(img: Image.Image, frame: Frame, rects: list[Rect]) -> None:
    """Black out screen rects (the agent's own HUD) in a model-space image."""
    d = ImageDraw.Draw(img)
    for (l, t, r, b) in rects:
        x1, y1 = frame.to_model(l, t)
        x2, y2 = frame.to_model(r, b)
        d.rectangle([x1, y1, x2, y2], fill="black")


class WindowsScreen:
    require_verified_focus = True
    def __init__(self, collect_elements: Callable | None = None,
                 focused: Callable | None = None):
        self._collect = collect_elements   # uia.collect, optional (M4)
        self._focused = focused            # uia.focused_element, optional

    def observe(self, mask_rects: list[Rect] | None = None) -> Observation:
        shots = capture.capture_all_screens()
        rect = capture.foreground_rect()
        if rect:
            cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            mon, img = capture.monitor_for_point(shots, cx, cy)
        else:
            mon, img = capture.primary_shot()
        frame = Frame.for_monitor(mon)
        if img.size != frame.size:
            img = img.resize(frame.size, Image.LANCZOS)
        if mask_rects:
            mask_image(img, frame, mask_rects)
        elements = []
        elements_text = ""
        if self._collect:
            try:
                elements = self._collect(frame) or []
            except Exception:
                elements = []
            if elements:
                from .uia import annotate, elements_to_text
                img = annotate(img, elements, frame)
                elements_text = elements_to_text(elements)
        import base64, io
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        return Observation(
            frame=frame, app=capture.foreground_app(),
            title=capture.foreground_title(),
            image_b64=base64.b64encode(buf.getvalue()).decode("ascii"),
            thumb=make_thumb(img), elements=elements,
            elements_text=elements_text, hwnd=capture.foreground_hwnd())

    def cursor(self) -> tuple[int, int]:
        return capture.cursor_position()

    def foreground(self) -> tuple[str, str, int]:
        return (capture.foreground_app(), capture.foreground_title(),
                capture.foreground_hwnd())

    def focused_element(self):
        if not self._focused:
            return None
        try:
            return self._focused()
        except Exception:
            return None
