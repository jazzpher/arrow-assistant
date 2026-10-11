"""System tray icon: status at a glance, settings hint, quit."""
from __future__ import annotations

from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


STATUS_COLORS = {
    "idle": QColor(37, 99, 235),        # blue
    "listening": QColor(220, 38, 38),   # red while the hotkey is held
    "thinking": QColor(234, 179, 8),    # yellow while STT/LLM runs
}


def _make_icon(status: str = "idle") -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor("transparent"))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(STATUS_COLORS.get(status, STATUS_COLORS["idle"]))
    p.setPen(QColor("white"))
    # simple arrow cursor glyph
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QPolygonF
    p.drawPolygon(QPolygonF([
        QPointF(20, 8), QPointF(20, 46), QPointF(29, 38),
        QPointF(35, 52), QPointF(41, 49), QPointF(35, 36), QPointF(46, 36),
    ]))
    p.end()
    return QIcon(pix)


class Tray:
    def __init__(self, app: QApplication, hotkey: str, on_quit, on_keys=None):
        self.tray = QSystemTrayIcon(_make_icon(), app)
        self.tray.setToolTip(f"Arrow Assistant - hold {hotkey} and ask")
        menu = QMenu()
        status = QAction(f"Hold {hotkey} and ask out loud", menu)
        status.setEnabled(False)
        menu.addAction(status)
        menu.addSeparator()
        if on_keys is not None:
            keys_action = QAction("API keys...", menu)
            keys_action.triggered.connect(on_keys)
            menu.addAction(keys_action)
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(on_quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def set_status(self, status: str) -> None:
        self.tray.setIcon(_make_icon(status))

    def notify(self, message: str) -> None:
        self.tray.showMessage("Arrow Assistant", message,
                              QSystemTrayIcon.MessageIcon.Warning, 6000)
