"""Detect that the human grabbed the mouse mid-task.

After each of OUR pointer moves we remember where the cursor should be.
If it is somewhere else before our next action, the user moved it, and
the agent pauses instead of fighting them.
"""
from __future__ import annotations

from typing import Callable


class ActivityMonitor:
    def __init__(self, cursor_fn: Callable[[], tuple[int, int]],
                 tolerance_px: int = 25):
        self._cursor = cursor_fn
        self._tol = tolerance_px
        self._expected: tuple[int, int] | None = None

    def expect(self, pt: tuple[int, int] | None) -> None:
        """Record where the cursor should be now (after our own move)."""
        self._expected = pt

    def rebase(self) -> None:
        """Accept the cursor's current position (after the user resumes)."""
        self._expected = self._cursor()

    def user_moved(self) -> bool:
        if self._expected is None:
            return False
        x, y = self._cursor()
        ex, ey = self._expected
        return abs(x - ex) > self._tol or abs(y - ey) > self._tol
