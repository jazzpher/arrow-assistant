"""The arrow overlay: a transparent, always-on-top, click-through layer
per monitor that draws an arrow pointing at the exact pixel the model
named, with a short label.

One QWidget per physical screen sidesteps Qt 6's mixed-DPI gotcha (a
single overlay spanning the virtual desktop renders at the wrong size on
at least one monitor). The Win32 click-through flags must be applied
AFTER show(), OR'd in (never overwritten), and followed by
SetWindowPos(SWP_FRAMECHANGED).
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QApplication, QWidget

from .points import Point, route_points

ARROW_COLOR = QColor(37, 99, 235)      # blue-600
ARROW_DURATION_MS = 6_000              # arrows clear themselves


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


class _MonitorOverlay(QWidget):
    def __init__(self, geometry, dpr: float = 1.0):
        super().__init__()
        self._geo = geometry  # QRect of the monitor; x/y are native (physical) origin
        self._dpr = dpr or 1.0
        self._points: list[Point] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setGeometry(geometry)

    def show_points(self, points: list[Point]) -> None:
        self._points = points
        self.show()
        self._apply_click_through()
        self.update()

    def clear_points(self) -> None:
        self._points = []
        self.update()
        self.hide()

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

    def paintEvent(self, event) -> None:
        if not self._points:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont("Segoe UI", 11, QFont.Weight.Bold)
        p.setFont(font)
        for pt in self._points:
            lx, ly = physical_to_local(pt.x, pt.y, self._geo.x(),
                                       self._geo.y(), self._dpr)
            self._draw_arrow(p, lx, ly, pt.label)
        p.end()

    def _draw_arrow(self, p: QPainter, x: float, y: float, label: str) -> None:
        # arrow cursor shape pointing at (x, y), coming from upper-left
        cursor = QPolygonF([
            QPointF(x, y), QPointF(x, y + 34), QPointF(x + 9, y + 26),
            QPointF(x + 15, y + 40), QPointF(x + 20, y + 37),
            QPointF(x + 14, y + 24), QPointF(x + 25, y + 24),
        ])
        path = QPainterPath()
        path.addPolygon(cursor)
        p.setPen(QPen(QColor("white"), 3))
        p.setBrush(ARROW_COLOR)
        p.drawPath(path)
        if label:
            metrics = p.fontMetrics()
            tw = metrics.horizontalAdvance(label) + 16
            th = metrics.height() + 10
            bx, by = clamp_label_box(x + 28, y + 30, tw, th, x, y,
                                     self.width(), self.height())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(15, 23, 42, 220))
            p.drawRoundedRect(QRectF(bx, by, tw, th), 6, 6)
            p.setPen(QColor("white"))
            p.drawText(QPointF(bx + 8, by + th - 8), label)


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

