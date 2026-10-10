"""The agent HUD: always-on-top status box with Approve / Skip / Stop.

It never takes keyboard focus (so typing keeps going to the target app)
and is the only agent UI that is NOT click-through. While the agent is
in control it shows a red badge so the user always knows who is driving.

Threading: the agent loop runs on a worker thread and only talks to
QtAgentUI, which forwards every call through Qt signals.
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from ..points import Point
from .approval import APPROVE, SKIP, STOP, ApprovalRequest, QueueApprover

MAX_LINES = 6
HUD_MARGIN_PX = 8   # extra physical px around the HUD that the agent treats as "the HUD"


def logical_to_physical_rect(left: float, top: float, right: float, bottom: float,
                             origin_x: float, origin_y: float, dpr: float,
                             margin: int = HUD_MARGIN_PX) -> tuple[int, int, int, int]:
    """Qt widget geometry (device-independent px) -> physical desktop px.

    The agent's screenshots, UIA rects and clicks are all physical pixels,
    while Qt6 places widgets in logical pixels. Qt keeps each screen's
    native origin and scales only the offset from it, so the offset is
    multiplied by the devicePixelRatio. Without this, at 125%/150% scaling
    the HUD mask and the "never click the HUD" guard cover the wrong area.
    """
    d = dpr if dpr and dpr > 0 else 1.0

    def px(v: float, o: float) -> int:
        return round(o + (v - o) * d)

    return (px(left, origin_x) - margin, px(top, origin_y) - margin,
            px(right, origin_x) + margin, px(bottom, origin_y) + margin)


class AgentHUD(QWidget):
    decided = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(
            "AgentHUD{background:#0f172a;border:3px solid #dc2626;border-radius:10px;}"
            "QLabel{color:#e2e8f0;font:11pt 'Segoe UI';}"
            "QPushButton{font:bold 10pt 'Segoe UI';padding:6px 12px;border-radius:6px;}")
        lay = QVBoxLayout(self)
        self.badge = QLabel("ARROW AGENT IS IN CONTROL")
        self.badge.setStyleSheet("color:#fca5a5;font:bold 9pt 'Segoe UI';")
        self.title = QLabel("")
        self.title.setWordWrap(True)
        self.reasons = QLabel("")
        self.reasons.setWordWrap(True)
        self.reasons.setStyleSheet("color:#fbbf24;")
        self.log = QLabel("")
        self.log.setStyleSheet("color:#94a3b8;font:9pt 'Consolas';")
        self.log.setWordWrap(True)
        row = QHBoxLayout()
        self.btn_ok = QPushButton("Approve (Ctrl+Alt+Y)")
        self.btn_skip = QPushButton("Skip (Ctrl+Alt+N)")
        self.btn_stop = QPushButton("STOP (Ctrl+Alt+Esc)")
        self.btn_ok.setStyleSheet("background:#16a34a;color:white;")
        self.btn_skip.setStyleSheet("background:#475569;color:white;")
        self.btn_stop.setStyleSheet("background:#dc2626;color:white;")
        for b in (self.btn_ok, self.btn_skip, self.btn_stop):
            row.addWidget(b)
        for w in (self.badge, self.title, self.reasons, self.log):
            lay.addWidget(w)
        lay.addLayout(row)
        self.btn_ok.clicked.connect(lambda: self.decided.emit(APPROVE))
        self.btn_skip.clicked.connect(lambda: self.decided.emit(SKIP))
        self.btn_stop.clicked.connect(lambda: self.decided.emit(STOP))
        self._lines: list[str] = []
        self.set_waiting(False)
        self.setFixedWidth(470)

    def place(self, screen_geometry) -> None:
        self.adjustSize()
        self.move(screen_geometry.right() - self.width() - 20,
                  screen_geometry.top() + 40)

    def set_waiting(self, waiting: bool) -> None:
        self.btn_ok.setEnabled(waiting)
        self.btn_skip.setEnabled(waiting)

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_reasons(self, reasons: tuple[str, ...]) -> None:
        self.reasons.setText("\n".join(f"! {r}" for r in reasons))

    def add_line(self, text: str) -> None:
        self._lines.append(text)
        self._lines = self._lines[-MAX_LINES:]
        self.log.setText("\n".join(self._lines))

    def apply_click_through_safety(self) -> None:
        """Keep the HUD topmost without stealing focus (Windows only)."""
        if sys.platform != "win32":
            return
        import ctypes
        hwnd = int(self.winId())
        style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongPtrW(
            hwnd, -20, style | 0x08000000 | 0x00000080 | 0x00000008)


class QtAgentUI(QObject):
    """AgentUI implementation safe to call from the worker thread."""

    _status = pyqtSignal(str)
    _step = pyqtSignal(int, int, str)
    _preview = pyqtSignal(object, str)
    _say = pyqtSignal(str)
    _finished = pyqtSignal(str, str)
    _request = pyqtSignal(object)
    _hide = pyqtSignal()

    def __init__(self, app: QApplication, overlay, approver: QueueApprover,
                 on_stop, speak=None) -> None:
        super().__init__()
        self.hud = AgentHUD()
        self._overlay = overlay
        self.approver = approver
        self._on_stop = on_stop
        self._speak = speak
        primary = app.primaryScreen()
        self._screen = primary.geometry()
        self._dpr = primary.devicePixelRatio() or 1.0
        # Physical-pixel rects of the visible HUD. Written on the GUI thread,
        # read by the agent worker thread (a list swap is atomic in CPython,
        # and the worker never touches the widget itself).
        self._rects: list[tuple[int, int, int, int]] = []
        self.hud.decided.connect(self._decided)
        self._status.connect(lambda t: (self.hud.add_line(t), self._show()))
        self._step.connect(self._on_step)
        self._preview.connect(self._on_preview)
        self._say.connect(self._on_say)
        self._finished.connect(self._on_finished)
        self._request.connect(self._on_request)
        self._hide.connect(self._hide_hud)
        self._hide_timer = QTimer()
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_hud)

    def _show(self) -> None:
        self._hide_timer.stop()
        if not self.hud.isVisible():
            self.hud.show()
            self.hud.apply_click_through_safety()
        self.hud.place(self._screen)
        self._update_rects()

    def _hide_hud(self) -> None:
        self.hud.hide()
        self._rects = []

    def _update_rects(self) -> None:
        g = self.hud.frameGeometry()
        self._rects = [logical_to_physical_rect(
            g.left(), g.top(), g.left() + g.width(), g.top() + g.height(),
            self._screen.x(), self._screen.y(), self._dpr)]

    def hud_rects(self) -> list[tuple[int, int, int, int]]:
        """Screen rects (physical px) the agent must mask and never click."""
        return list(self._rects)

    # AgentUI (any thread) --------------------------------------------------
    def status(self, text): self._status.emit(text)
    def step(self, n, max_steps, text): self._step.emit(n, max_steps, text)
    def preview(self, pt, label): self._preview.emit(pt, label)
    def say(self, text): self._say.emit(text)
    def finished(self, status, message): self._finished.emit(status, message)
    def on_request(self, req: ApprovalRequest): self._request.emit(req)

    # GUI thread --------------------------------------------------------------
    def _decided(self, decision: str) -> None:
        if decision == STOP:
            self._on_stop()
        self.approver.respond(decision)
        self.hud.set_waiting(False)

    def _on_step(self, n: int, max_steps: int, text: str) -> None:
        self.hud.set_title(f"Step {n}/{max_steps}: {text}")
        self.hud.set_reasons(())
        self.hud.add_line(f"{n}. {text}")
        self._show()

    def _on_preview(self, pt, label: str) -> None:
        if pt is not None:
            self._overlay.point_at([Point(int(pt[0]), int(pt[1]), label or "here")])

    def _on_say(self, text: str) -> None:
        self.hud.add_line(text)
        self._show()
        if self._speak:
            self._speak(text)

    def _on_request(self, req: ApprovalRequest) -> None:
        head = "Approve this plan?" if req.kind == "plan" else f"Approve step {req.step}/{req.max_steps}?"
        self.hud.set_title(f"{head}  {req.summary}")
        self.hud.set_reasons(req.reasons)
        self.hud.set_waiting(True)
        self._show()
        if req.point is not None:
            self._on_preview(req.point, req.summary[:40])

    def _on_finished(self, status: str, message: str) -> None:
        self.hud.set_waiting(False)
        self.hud.set_title(f"{status.upper()}: {message}")
        self.hud.set_reasons(())
        self.hud.add_line(f"{status}: {message}")
        self._show()
        self._hide_timer.start(15_000)
        if self._speak:
            self._speak(message)
