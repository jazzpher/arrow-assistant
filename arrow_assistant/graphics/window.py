"""The on-screen graphics window: a small always-on-top card showing the
current scene. Drag the title bar to move it; Esc or the X closes it.
3D scenes: drag the canvas to rotate, wheel to zoom, double-click to
toggle auto-rotate, hover a part to highlight and name it.

3D renders on the GPU (OpenGL 3.3 via moderngl) when available and on the
QPainter CPU renderer otherwise; the title bar always shows which mode is
live (GPU / CPU) so the fallback is never silent. Only one card exists;
a new scene replaces the old.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication, QWidget

from . import render
from .spec import Scene

CARD_BODY_H_OFFSET = 36                # body starts below the title bar

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
        self._gl_view = None           # GLSceneView when GPU mode is live
        self._gl_broken = False        # GL context creation already failed
        self._mode = "cpu"             # what the user is actually seeing
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
        self._setup_body(scene)
        self._place_bottom_right()
        self.show()
        self.raise_()
        self.update()

    def _setup_body(self, scene: Scene) -> None:
        """3D scenes get the GPU canvas when OpenGL 3.3 is there; anything
        else (2D, or a failed GL probe) uses the QPainter body."""
        if scene.kind != "3d" or self._gl_broken:
            self._drop_gl_view()
            self._mode = "cpu"
            return
        if not self._probe_gl_support():
            self._gl_broken = True
            self._mode = "cpu"
            return
        if self._gl_view is None:
            try:
                from .glwindow import GLSceneView
                self._gl_view = GLSceneView(self)
                self._gl_view.setGeometry(
                    0, CARD_BODY_H_OFFSET, CARD_W, CARD_H - CARD_BODY_H_OFFSET)
                self._gl_view.failed.connect(self._on_gl_failed)
            except Exception:                      # noqa: BLE001
                self._gl_broken = True
                self._gl_view = None
                self._mode = "cpu"
                return
        self._mode = "gpu"
        self._gl_view.set_scene(scene)
        self._gl_view.show()

    def _probe_gl_support(self) -> bool:
        """Cheap up-front check: can this machine give us a standalone
        OpenGL 3.3 context for the GPU canvas?"""
        try:
            from .glrender import create_standalone_context
            create_standalone_context().release()
            return True
        except Exception:                              # noqa: BLE001
            return False

    def _drop_gl_view(self) -> None:
        if self._gl_view is not None:
            self._gl_view.hide()
            self._gl_view.deleteLater()
            self._gl_view = None

    def _on_gl_failed(self) -> None:
        """initializeGL could not get a 3.3 context: switch to CPU and
        repaint; the badge makes the downgrade visible."""
        self._gl_broken = True
        self._drop_gl_view()
        self._mode = "cpu"
        self.update()

    def renderer_mode(self) -> str:
        return self._mode

    def _toggle_autorotate(self) -> None:
        if self._scene and self._scene.kind == "3d":
            self._autorotate = not self._autorotate
            if self._autorotate:
                self._spin.start()
            else:
                self._spin.stop()

    def current_scene(self) -> Scene | None:
        return self._scene

    def _place_bottom_right(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - CARD_W - 24, geo.bottom() - CARD_H - 24)

    # -- painting --------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(0, TITLE_H, self.width(), self.height() - TITLE_H)
        p.save()
        p.translate(0, TITLE_H)
        if self._gl_view is not None and self._scene is not None \
                and self._scene.kind == "3d":
            pass                                     # GPU child owns the body
        elif self._scene is not None:
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
        # renderer mode badge (never a silent fallback)
        if self._scene is not None and self._scene.kind == "3d":
            badge = "GPU" if self._mode == "gpu" else "CPU"
            p.setPen(QColor("#4ade80") if self._mode == "gpu"
                     else QColor("#fbbf24"))
            bf = p.font()
            bf.setBold(False)
            bf.setPointSize(max(7, bf.pointSize() - 3))
            p.setFont(bf)
            p.drawText(QRectF(self.width() - TITLE_H - 44, 0, 40, TITLE_H),
                       int(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter
                           | Qt.TextFlag.TextSingleLine), badge)
        # close button
        p.setPen(QColor("#94a3b8"))
        p.drawText(QRectF(self.width() - TITLE_H, 0, TITLE_H, TITLE_H),
                   int(Qt.AlignmentFlag.AlignCenter), "✕")
        p.end()

    # -- interaction -----------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if e.position().x() >= self.width() - TITLE_H and e.position().y() <= TITLE_H:
            self.close_card()
            return
        self._drag = e.globalPosition().toPoint()
        self._drag_origin_title = e.position().y() <= TITLE_H
        if (not self._drag_origin_title and self._scene
                and self._scene.kind == "3d" and self._gl_view is None):
            self._spin.stop()            # user takes control of rotation

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            return
        delta = e.globalPosition().toPoint() - self._drag
        self._drag = e.globalPosition().toPoint()
        if self._drag_origin_title:
            self.move(self.pos() + delta)
        elif self._scene and self._scene.kind == "3d" \
                and self._gl_view is None:
            self._view["yaw"] += delta.x() * 0.5
            self._view["pitch"] = max(-89.0, min(89.0,
                                                 self._view["pitch"] + delta.y() * 0.5))
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag = None
        self._drag_origin_title = False

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if self._gl_view is None:
            self._toggle_autorotate()

    def wheelEvent(self, e: QWheelEvent) -> None:
        if self._scene and self._scene.kind == "3d" and self._gl_view is None:
            step = -e.angleDelta().y() / 120.0
            base = self._view["distance"] or 1.0
            self._view["distance"] = max(1.0, base * (1 + step * 0.1))
            self.update()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close_card()

    def _tick(self) -> None:
        self._view["yaw"] += AUTOROTATE_DEG_PER_TICK
        if self._gl_view is not None:
            self._gl_view._render_frame()
        else:
            self.update()

    def close_card(self) -> None:
        self._spin.stop()
        self.hide()
