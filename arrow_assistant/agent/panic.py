"""Emergency stop and pause. Checked before EVERY physical action.

stop  : kill the task now (panic hotkey, tray Stop, HUD Stop, failsafe).
pause : the user grabbed the mouse; wait until they press Resume.
"""
from __future__ import annotations

import threading


class PanicStop(Exception):
    """Raised inside the executor/loop when the user hit stop."""


class PanicSwitch:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._running = threading.Event()
        self._running.set()
        self.reason = ""

    # -- stop ----------------------------------------------------------------
    def stop(self, reason: str = "stopped by user") -> None:
        self.reason = reason
        self._stop.set()
        self._running.set()  # wake anything waiting on a pause

    @property
    def is_stopped(self) -> bool:
        return self._stop.is_set()

    def check(self) -> None:
        if self._stop.is_set():
            raise PanicStop(self.reason)

    # -- pause ---------------------------------------------------------------
    def pause(self, reason: str = "paused") -> None:
        if not self._stop.is_set():
            self.reason = reason
            self._running.clear()

    def resume(self) -> None:
        self._running.set()

    @property
    def is_paused(self) -> bool:
        return not self._running.is_set() and not self._stop.is_set()

    def wait_if_paused(self, timeout: float | None = None) -> bool:
        """Block while paused. True = free to continue, False = stopped/timed out."""
        if self._stop.is_set():
            return False
        ok = self._running.wait(timeout)
        return ok and not self._stop.is_set()

    def reset(self) -> None:
        self._stop.clear()
        self._running.set()
        self.reason = ""
