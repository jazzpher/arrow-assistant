"""The arrow overlay: a transparent, always-on-top, click-through layer
per monitor where a cursor buddy flies to the exact pixel the model named
and a message bubble with the label rides along with it.

One QWidget per physical screen sidesteps Qt 6's mixed-DPI gotcha (a
single overlay spanning the virtual desktop renders at the wrong size on
at least one monitor). The Win32 click-through flags must be applied
AFTER show(), OR'd in (never overwritten), and followed by
SetWindowPos(SWP_FRAMECHANGED).

Motion: each point gets a cursor that flies in on an arc and settles with
a spring (cursor_motion.Flight, pure time-based math). A QTimer repaints
only while something moves, and only the area the cursors and bubbles
cover. Plain QPainter, no OpenGL/Qt Quick.

Screen captures: on Windows 10 2004+ the overlay is excluded from screen
captures (SetWindowDisplayAffinity WDA_EXCLUDEFROMCAPTURE), so Arrow's
own screenshots for the model, screen shares and recordings never show
it. ARROW_OVERLAY_IN_CAPTURE=1 turns that off (e.g. to record a demo).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from PyQt6.QtCore import QElapsedTimer, QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QPainter,
                         QPainterPath, QPen, QPolygonF)
from PyQt6.QtWidgets import QApplication, QWidget

from .cursor_motion import Flight
from .points import Point, route_points

ARROW_COLOR = QColor(37, 99, 235)      # blue-600
ARROW_DURATION_MS = 6_000              # arrows clear themselves
FRAME_MS = 16                          # ~60 fps while a cursor is moving
STAGGER_MS = 90                        # 2nd/3rd cursor take off a beat later
BUBBLE_LAG_MS = 45                     # the bubble trails its cursor slightly
BUBBLE_MAX_W = 280                     # longer labels wrap
LAUNCH_OFFSET = (140.0, 100.0)         # take-off distance when the mouse is elsewhere

WDA_EXCLUDEFROMCAPTURE = 0x00000011
# Before Windows 10 2004 (build 19041) the same flag acts as WDA_MONITOR:
# the window turns BLACK in captures. The overlay covers the whole
# monitor, so that would black out Arrow's own screenshots. Never set it there.
MIN_CAPTURE_EXCLUSION_BUILD = 19041

# the cursor shape, tip at (0, 0), body down/right (unchanged from the static arrow)
_CURSOR_SHAPE = ((0, 0), (0, 34), (9, 26), (15, 40), (20, 37), (14, 24), (25, 24))
_CURSOR_BOUNDS = (-4, -4, 34, 48)      # x, y, w, h around the tip incl. outline + shadow


def physical_to_local(px: float, py: float, left: float, top: float,
                      dpr: float) -> tuple[float, float]:
    """Physical desktop pixel (what mss/the model use) -> widget-local
    logical pixel. Qt6 sizes widgets in device-independent pixels, so at
    125%/150% scaling the offset from the monitor origin must be divided
    by the devicePixelRatio or the arrow lands ~1.5x too far out."""
    d = dpr if dpr and dpr > 0 else 1.0
    return (px - left) / d, (py - top) / d


def clamp_label_box(bx: float, by: float, tw: float, th: float,
                    x: float, y: float, w: float, h: float) -> tuple[float, float]:
    """Keep the label box inside the widget; flip left/up of the arrow
    tip (x, y) when it would run off the right/bottom edge."""
    if bx + tw > w - 4:
        bx = max(4, x - 8 - tw)
    if by + th > h - 4:
        by = max(4, y - 8 - th)
    return bx, by


def launch_point(target: tuple[float, float], mouse: tuple[float, float] | None,
                 w: float, h: float) -> tuple[float, float]:
    """Where a new cursor takes off: from the real mouse pointer when it is
    on this monitor, else from a short way off the target, towards the
    middle of the screen, so the flight is always visible."""
    if mouse is not None and 0 <= mouse[0] < w and 0 <= mouse[1] < h:
        return float(mouse[0]), float(mouse[1])
    tx, ty = target
    sx = 1.0 if tx < w / 2 else -1.0
    sy = 1.0 if ty < h / 2 else -1.0
    x = min(max(8.0, tx + sx * LAUNCH_OFFSET[0]), max(8.0, w - 8))
    y = min(max(8.0, ty + sy * LAUNCH_OFFSET[1]), max(8.0, h - 8))
    return x, y


def bubble_opacity(progress: float) -> float:
    """The bubble fades in over the last stretch of the flight."""
    u = (progress - 0.55) / 0.45
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def capture_exclusion_wanted(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("ARROW_OVERLAY_IN_CAPTURE", "").strip().lower() not in ("1", "true", "yes")


def exclude_from_capture(hwnd: int, user32=None, build: int | None = None) -> bool:
    """Hide a top-level window from screen captures (still visible on the
    monitor). Returns True only when Windows accepted it. Never raises:
    non-Windows, Windows before 10 2004 and API failures all return False
    and leave the window as it was."""
    if user32 is None:
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            fn = user32.SetWindowDisplayAffinity
            fn.argtypes = [wintypes.HWND, wintypes.DWORD]
            fn.restype = wintypes.BOOL
        except (AttributeError, OSError):
            return False
    if build is None:
        try:
            build = sys.getwindowsversion().build
        except AttributeError:
            return False
    if build < MIN_CAPTURE_EXCLUSION_BUILD:
        return False
    fn = getattr(user32, "SetWindowDisplayAffinity", None)
    if fn is None:
        return False
    try:
        return bool(fn(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:  # noqa: BLE001 - a cosmetic nicety must never break pointing
        return False


@dataclass(eq=False)   # identity matters: two cursors can look alike
class _Buddy:
    """One flying cursor and its message bubble."""
    point: Point
    flight: Flight
    t0: float                          # overlay clock (ms) when the flight began
    bubble_size: tuple[float, float]   # (w, h) incl. padding
    pos: tuple[float, float] = field(init=False)
    bubble_at: tuple[float, float] = field(init=False)
    opacity: float = 0.0

    def __post_init__(self) -> None:
        self.pos = self.flight.start
        self.bubble_at = self.flight.start

    def advance(self, now_ms: float) -> bool:
        """Move to the instant now_ms; True while still in motion."""
        t = now_ms - self.t0
        self.pos = self.flight.position(t)
        self.bubble_at = self.flight.position(t - BUBBLE_LAG_MS)
        self.opacity = bubble_opacity(self.flight.progress(t - BUBBLE_LAG_MS))
        return not self.flight.done(t - BUBBLE_LAG_MS)


class _MonitorOverlay(QWidget):
    def __init__(self, geometry, dpr: float = 1.0):
        super().__init__()
        self._geo = geometry  # QRect of the monitor; x/y are native (physical) origin
        self._dpr = dpr or 1.0
        self._points: list[Point] = []
        self._buddies: list[_Buddy] = []
        self._font = QFont("Segoe UI", 11, QFont.Weight.Bold)
        self.capture_excluded: bool | None = None   # None until shown on Windows
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setGeometry(geometry)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._anim = QTimer(self)
        self._anim.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim.setInterval(FRAME_MS)
        self._anim.timeout.connect(self._on_frame)

    # -- public ----------------------------------------------------------------
    def show_points(self, points: list[Point], now_ms: float | None = None,
                    mouse: tuple[float, float] | None = None) -> None:
        """Fly a cursor to each point. Cursors already on a point that is
        still wanted stay put (the teach pipeline re-sends the growing list
        as each tag streams in); cursors whose point went away fly on to the
        new points; extra points take off from the mouse pointer."""
        now = self._now() if now_ms is None else now_ms
        if mouse is None:
            mouse = self._mouse_local()
        old = list(self._buddies)
        kept: dict[int, _Buddy] = {}
        for i, pt in enumerate(points):
            for b in old:
                if b.point == pt and all(b is not k for k in kept.values()):
                    kept[i] = b
                    break
        spare = [b for b in old if all(b is not k for k in kept.values())]
        buddies: list[_Buddy] = []
        launched = 0
        for i, pt in enumerate(points):
            if i in kept:
                buddies.append(kept[i])
                continue
            target = physical_to_local(pt.x, pt.y, self._geo.x(), self._geo.y(), self._dpr)
            if spare:
                start = spare.pop(0).pos      # retarget: fly on from where it is
            else:
                start = launch_point(target, mouse, self.width(), self.height())
            flight = Flight(start, target, delay_ms=STAGGER_MS * launched,
                            bounds=(float(self.width()), float(self.height())))
            launched += 1
            buddies.append(_Buddy(pt, flight, now, self._bubble_size(pt.label)))
        self._buddies = buddies
        self._points = list(points)
        self.show()
        self._apply_click_through()
        for b in self._buddies:
            b.advance(now)
        self.update()
        if self._buddies:
            self._anim.start()

    def clear_points(self) -> None:
        self._anim.stop()
        self._points = []
        self._buddies = []
        self.update()
        self.hide()

    def is_animating(self) -> bool:
        return self._anim.isActive()

    def advance(self, now_ms: float) -> bool:
        """Move every cursor to now_ms and repaint just the area involved.
        Returns True while any cursor is still moving."""
        dirty = QRect()
        moving = False
        for b in self._buddies:
            dirty = dirty.united(self._buddy_rect(b))
            moving = b.advance(now_ms) or moving
            dirty = dirty.united(self._buddy_rect(b))
        if not dirty.isNull():
            self.update(dirty)
        return moving

    # -- internals -------------------------------------------------------------
    def _now(self) -> float:
        return float(self._clock.elapsed())

    def _on_frame(self) -> None:
        if not self.advance(self._now()):
            self._anim.stop()

    def _mouse_local(self) -> tuple[float, float] | None:
        try:
            c = QCursor.pos()
        except Exception:  # noqa: BLE001 - no pointer (headless): take off near the target
            return None
        g = self.geometry()
        return float(c.x() - g.x()), float(c.y() - g.y())

    def _bubble_size(self, label: str) -> tuple[float, float]:
        if not label:
            return 0.0, 0.0
        fm = QFontMetrics(self._font)
        one_line = fm.horizontalAdvance(label)
        if one_line + 16 <= BUBBLE_MAX_W:
            return float(one_line + 16), float(fm.height() + 10)
        br = fm.boundingRect(QRect(0, 0, BUBBLE_MAX_W - 16, 10_000),
                             int(Qt.TextFlag.TextWordWrap), label)
        return float(min(BUBBLE_MAX_W, br.width() + 16)), float(br.height() + 10)

    def _bubble_box(self, b: _Buddy) -> QRectF:
        tw, th = b.bubble_size
        x, y = b.bubble_at
        bx, by = clamp_label_box(x + 28, y + 30, tw, th, x, y, self.width(), self.height())
        return QRectF(bx, by, tw, th)

    def _buddy_rect(self, b: _Buddy) -> QRect:
        x, y = b.pos
        bx, by, bw, bh = _CURSOR_BOUNDS
        r = QRectF(x + bx, y + by, bw, bh)
        if b.point.label:
            r = r.united(self._bubble_box(b).adjusted(-3, -3, 3, 3))
        return r.toAlignedRect()

    def _apply_click_through(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes
        hwnd = int(self.winId())
        GWL_EXSTYLE = -20
        WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x00000020
        WS_EX_TOPMOST, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = (
            0x00000008, 0x08000000, 0x00000080)
        style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongPtrW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
            | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        ctypes.windll.user32.SetWindowPos(
            hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
        # re-asserted on every show: Windows can drop the affinity on
        # display changes
        if capture_exclusion_wanted():
            self.capture_excluded = exclude_from_capture(hwnd)
        else:
            self.capture_excluded = False

    def paintEvent(self, event) -> None:
        if not self._buddies:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self._font)
        for b in self._buddies:
            self._draw_cursor(p, *b.pos)
        for b in self._buddies:               # bubbles above every cursor
            if b.point.label and b.opacity > 0.01:
                self._draw_bubble(p, b)
        p.end()

    def _draw_cursor(self, p: QPainter, x: float, y: float) -> None:
        # arrow cursor shape pointing at (x, y), coming from upper-left
        cursor = QPolygonF([QPointF(x + dx, y + dy) for dx, dy in _CURSOR_SHAPE])
        path = QPainterPath()
        path.addPolygon(cursor)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 55))       # soft drop shadow
        p.drawPath(path.translated(2, 3))
        p.setPen(QPen(QColor("white"), 3))
        p.setBrush(ARROW_COLOR)
        p.drawPath(path)

    def _draw_bubble(self, p: QPainter, b: _Buddy) -> None:
        box = self._bubble_box(b)
        p.save()
        p.setOpacity(b.opacity)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(QColor(15, 23, 42, 225))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor("white"))
        p.drawText(box.adjusted(8, 5, -8, -5),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                   | int(Qt.TextFlag.TextWordWrap), b.point.label)
        p.restore()


class ArrowOverlay:
    """Owns one _MonitorOverlay per physical screen."""

    def __init__(self, app: QApplication):
        self._overlays: list[_MonitorOverlay] = []
        for screen in app.screens():
            self._overlays.append(_MonitorOverlay(
                screen.geometry(), screen.devicePixelRatio()))
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clear)

    def point_at(self, points: list[Point]) -> None:
        if not points:
            return
        monitors = [{
            "left": ov._geo.x(), "top": ov._geo.y(),
            # physical size: points arrive in physical desktop pixels
            "width": round(ov._geo.width() * ov._dpr),
            "height": round(ov._geo.height() * ov._dpr),
        } for ov in self._overlays]
        routed = route_points(points, monitors)
        for i, overlay in enumerate(self._overlays):
            if i in routed:
                overlay.show_points(routed[i])
            else:
                overlay.clear_points()
        self._timer.start(ARROW_DURATION_MS)

    def clear(self) -> None:
        for overlay in self._overlays:
            overlay.clear_points()
