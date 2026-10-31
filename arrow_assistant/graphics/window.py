"""The on-screen graphics window: a small always-on-top card showing the
current scene. Drag the title bar to move it; Esc or the X closes it.
3D scenes: drag the canvas to rotate, wheel to zoom, double-click to
toggle auto-rotate. Only one card exists; a new scene replaces the old.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication, QWidget

from . import render
from .spec import Scene

CARD_W, CARD_H = 560, 460
TITLE_H = 36
AUTOROTATE_DEG_PER_TICK = 0.9
TICK_MS = 33                            # ~30 fps


class GraphicsWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._scene: Scene | None = None
        self._view = dict(render.DEFAULT_VIEW)
        self._autorotate = True
        self._drag: QPoint | None = None
        self._drag_origin_title = False
        self._spin = QTimer(self)
        self._spin.setInterval(TICK_MS)
        self._spin.timeout.connect(self._tick)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFixedSize(CARD_W, CARD_H)
        self.setWindowTitle("Arrow graphics")

    # -- scene management ----------------------------------------------------
    def show_scene(self, scene: Scene) -> None:
        """GUI thread: display a new or updated scene."""
        self._scene = scene
        self._view = dict(render.DEFAULT_VIEW)
        self._autorotate = scene.autorotate and scene.kind == "3d"
        if self._autorotate:
            self._spin.start()
        else:
            self._spin.stop()
        self._place_bottom_right()
        self.show()
        self.raise_()
        self.update()

    def current_scene(self) -> Scene | None:
        return self._scene

    def _place_bottom_right(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - CARD_W - 24, geo.bottom() - CARD_H - 24)

    # -- painting --------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(0, TITLE_H, self.width(), self.height() - TITLE_H)
        p.save()
        p.translate(0, TITLE_H)
        if self._scene is not None:
            render.render(p, self._scene, body.width(), body.height(),
                          self._view)
        else:
            p.fillRect(QRectF(0, 0, body.width(), body.height()),
                       QColor("#0f172a"))
        p.restore()
        # title bar
        p.fillRect(QRectF(0, 0, self.width(), TITLE_H), QColor("#1e293b"))
        p.setPen(QColor("#e2e8f0"))
        f = p.font()
        f.setBold(True)
        p.setFont(f)
        title = self._scene.title if self._scene else ""
        p.drawText(QRectF(12, 0, self.width() - 96, TITLE_H),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                       | Qt.TextFlag.TextSingleLine), title)
        caption = self._scene.caption if self._scene else ""
        if caption:
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor("#94a3b8"))
            p.drawText(QRectF(12, 0, self.width() - 96, TITLE_H + 26),
                       int(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft
                           | Qt.TextFlag.TextSingleLine), caption)
        # close button
        p.setPen(QColor("#94a3b8"))
        p.drawText(QRectF(self.width() - TITLE_H, 0, TITLE_H, TITLE_H),
                   int(Qt.AlignmentFlag.AlignCenter), "✕")
        p.end()

    # -- interaction -----------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if e.position().x() >= self.width() - TITLE_H and e.position().y() <= TITLE_H:
            self.close_card()
            return
        self._drag = e.globalPosition().toPoint()
        self._drag_origin_title = e.position().y() <= TITLE_H
        if not self._drag_origin_title and self._scene \
                and self._scene.kind == "3d":
            self._spin.stop()            # user takes control of rotation

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag is None:
            return
        delta = e.globalPosition().toPoint() - self._drag
        self._drag = e.globalPosition().toPoint()
        if self._drag_origin_title:
            self.move(self.pos() + delta)
        elif self._scene and self._scene.kind == "3d":
            self._view["yaw"] += delta.x() * 0.5
            self._view["pitch"] = max(-89.0, min(89.0,
                                                 self._view["pitch"] + delta.y() * 0.5))
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._drag = None
        self._drag_origin_title = False

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._scene and self._scene.kind == "3d":
            self._autorotate = not self._autorotate
            if self._autorotate:
                self._spin.start()
            else:
                self._spin.stop()

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        if self._scene and self._scene.kind == "3d":
            step = -e.angleDelta().y() / 120.0
            base = self._view["distance"] or 1.0
            self._view["distance"] = max(1.0, base * (1 + step * 0.1))
            self.update()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.close_card()

    def _tick(self) -> None:
        self._view["yaw"] += AUTOROTATE_DEG_PER_TICK
        self.update()

    def close_card(self) -> None:
        self._spin.stop()
        self.hide()
