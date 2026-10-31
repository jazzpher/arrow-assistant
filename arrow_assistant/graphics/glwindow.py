"""GLSceneView: the GPU-rendered 3D canvas.

Renders the scene with moderngl on its own OpenGL 3.3 context (offscreen
FBO), then presents the frame as a QImage in an ordinary QWidget. This
keeps moderngl's context completely separate from Qt's compositor GL
context: sharing one context between moderngl VAOs and Qt's compositor
proved crash-prone, and the separate context is exactly the path the
headless renderer and the CI capability gate exercise, so tested code is
shipped code. A real GPU renders a 560px scene in well under a frame.

Drag orbits, wheel zooms, double-click toggles auto-rotate, and hovering
a part highlights it and names it (visual feedback only - no AI calls).
Labels are drawn with QPainter over the frame so text stays crisp.

If no OpenGL 3.3 context can be created, `failed` fires and the window
falls back to the CPU renderer, marked with a CPU badge.
"""
from __future__ import annotations

import logging

import numpy as np
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QWheelEvent
from PyQt6.QtWidgets import QWidget

from . import render
from .spec import Scene

log = logging.getLogger("arrow.graphics.gl")


class GLSceneView(QWidget):
    failed = pyqtSignal()

    def __init__(self, window) -> None:
        super().__init__(window)
        self._win = window                     # GraphicsWindow (view owner)
        self._scene: Scene | None = None
        self._gfx = None                       # glrender.SceneGL
        self._ctx = None
        self._fbo = None
        self._frame: QImage | None = None
        self._hover: int | None = None
        self._drag = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        try:
            from . import glrender
            self._ctx = glrender.create_standalone_context()
            self._gfx = glrender.SceneGL(self._ctx)
            log.info("graphics GPU renderer: %s",
                     self._ctx.info.get("GL_RENDERER", "unknown"))
        except Exception as exc:               # noqa: BLE001
            self._ctx = None
            self._gfx = None
            log.warning("graphics GPU unavailable, CPU fallback: %s", exc)
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self.failed.emit)

    # -- scene ---------------------------------------------------------------
    def set_scene(self, scene: Scene) -> None:
        self._scene = scene
        self._hover = None
        if self._gfx is not None:
            self._gfx.set_scene(scene)
        self._render_frame()

    def _render_frame(self) -> None:
        """Render the current scene+view to the offscreen FBO and keep the
        pixels as a QImage for paintEvent."""
        if self._gfx is None or self._scene is None:
            self._frame = None
            self.update()
            return
        dpr = self.devicePixelRatioF()
        gw = max(2, round(self.width() * dpr))
        gh = max(2, round(self.height() * dpr))
        if self._fbo is None or self._fbo.size != (gw, gh):
            if self._fbo is not None:
                self._fbo.release()
            self._fbo = self._ctx.simple_framebuffer((gw, gh), components=3)
        self._gfx.draw(self._win._view, gw, gh, hover=self._hover,
                       target=self._fbo)
        data = self._fbo.read(components=3)
        arr = np.frombuffer(data, dtype="u1").reshape(gh, gw, 3)
        arr = np.flipud(arr)
        img = QImage(arr.copy().data, gw, gh, gw * 3,
                     QImage.Format.Format_RGB888)
        img.setDevicePixelRatio(dpr)
        self._frame = img
        self.update()

    # -- painting --------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._frame is not None:
            p.drawImage(0, 0, self._frame)
        else:
            p.fillRect(self.rect(), QColor("#0f172a"))
        self._paint_overlay(p)
        p.end()

    def _paint_overlay(self, p: QPainter) -> None:
        if self._scene is None or self._gfx is None:
            return
        items = self._gfx.label_positions(self._win._view,
                                          self.width(), self.height())
        pending = []
        for x, y, text, col, size in items:
            fs = max(10.0, min(30.0, size * 0.55))
            w_est = max(40.0, len(text) * fs * 0.72)
            pending.append((x, y, text, col, fs, w_est, fs * 1.5))
        placed = render.spread_labels([(x, y, w_est, h_est)
                                       for x, y, _t, _c, _f, w_est, h_est
                                       in pending])
        for (x, _y, text, col, fs, _w, h_est), (_px, y, _pw, _ph) in zip(
                pending, placed):
            p.setFont(render._font(fs, bold=True))
            qc = QColor(col)
            p.setPen(qc if qc.isValid() else QColor("#e2e8f0"))
            p.drawText(QRectF(x - 200, y - h_est / 2, 400, h_est),
                       int(Qt.AlignmentFlag.AlignHCenter
                           | Qt.AlignmentFlag.AlignVCenter
                           | Qt.TextFlag.TextSingleLine), text)
        if self._hover is not None:
            name = self._gfx.part_names[self._hover] or "part"
            p.setFont(render._font(12))
            p.setPen(QColor("#94a3b8"))
            p.drawText(QRectF(10, self.height() - 26, self.width() - 20, 20),
                       int(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter
                           | Qt.TextFlag.TextSingleLine), name)

    # -- interaction -----------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.position()
            self._win._spin.stop()             # user takes control

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag is not None:
            delta = e.position() - self._drag
            self._drag = e.position()
            self._win._view["yaw"] += delta.x() * 0.5
            self._win._view["pitch"] = max(
                -89.0, min(89.0,
                           self._win._view["pitch"] + delta.y() * 0.5))
            self._render_frame()
        elif self._gfx is not None and self._scene is not None:
            idx = self._gfx.pick(self._win._view, self.width(),
                                 self.height(),
                                 round(e.position().x()),
                                 round(e.position().y()))
            if idx != self._hover:
                self._hover = idx
                self._render_frame()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        self._win._toggle_autorotate()

    def wheelEvent(self, e: QWheelEvent) -> None:
        step = -e.angleDelta().y() / 120.0
        base = self._win._view["distance"] or 1.0
        self._win._view["distance"] = max(1.0, base * (1 + step * 0.1))
        self._render_frame()

    # -- testing hooks -----------------------------------------------------------
    def frame_array(self) -> np.ndarray | None:
        """Latest presented frame as (h, w, 3) uint8, for tests."""
        if self._frame is None:
            return None
        img = self._frame.convertToFormat(QImage.Format.Format_RGB888)
        ptr = img.bits()
        ptr.setsize(img.sizeInBytes())
        return np.frombuffer(ptr, np.uint8).reshape(
            img.height(), img.width(), 3).copy()
