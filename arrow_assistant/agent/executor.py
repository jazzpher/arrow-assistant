"""Perform actions on the real machine.

Executor.perform receives ABSOLUTE screen pixels (the loop already mapped
model coordinates through coords.Frame), so this layer never has to know
about screenshots. The OS calls live behind a small Backend so everything
above them is testable with a fake.
"""
from __future__ import annotations

import time
from typing import Callable, Protocol

from .actions import POINTER_KINDS, Action
from .panic import PanicStop, PanicSwitch

Point = tuple[int, int]
TYPE_CHUNK = 12


class Backend(Protocol):
    def click(self, x: int, y: int, button: str, clicks: int) -> None: ...
    def type_text(self, text: str) -> None: ...
    def hotkey(self, *keys: str) -> None: ...
    def scroll(self, amount: int, at: Point | None) -> None: ...
    def drag(self, start: Point, end: Point) -> None: ...


class Executor(Protocol):
    def perform(self, action: Action, pt: Point | None,
                pt2: Point | None) -> Point | None: ...


class PyAutoGuiBackend:
    """Real mouse/keyboard. pyautogui for the mouse, pynput for unicode typing."""

    MOVE_DURATION = 0.25   # slow enough that the user sees where it goes

    def __init__(self) -> None:
        import pyautogui
        pyautogui.FAILSAFE = True      # slam the mouse into a corner = abort
        pyautogui.PAUSE = 0.08
        self._pg = pyautogui

    def _wrap(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except self._pg.FailSafeException as exc:
            raise PanicStop("failsafe: mouse moved to a screen corner") from exc

    def click(self, x, y, button, clicks):
        self._wrap(self._pg.moveTo, x, y, duration=self.MOVE_DURATION)
        self._wrap(self._pg.click, x=x, y=y, button=button, clicks=clicks,
                   interval=0.12)

    def type_text(self, text):
        from pynput.keyboard import Controller
        Controller().type(text)

    def hotkey(self, *keys):
        self._wrap(self._pg.hotkey, *keys)

    def scroll(self, amount, at):
        if at:
            self._wrap(self._pg.moveTo, at[0], at[1], duration=0.15)
        # pyautogui: positive = up. Our amount: positive = down.
        self._wrap(self._pg.scroll, -amount * 100)

    def drag(self, start, end):
        self._wrap(self._pg.moveTo, start[0], start[1], duration=self.MOVE_DURATION)
        self._wrap(self._pg.dragTo, end[0], end[1], duration=0.5, button="left")


_BUTTONS = {"click": ("left", 1), "double_click": ("left", 2),
            "right_click": ("right", 1)}


class RealExecutor:
    def __init__(self, backend: Backend, panic: PanicSwitch,
                 sleep: Callable[[float], None] = time.sleep):
        self._b = backend
        self._panic = panic
        self._sleep = sleep

    def perform(self, action: Action, pt: Point | None,
                pt2: Point | None) -> Point | None:
        self._panic.check()
        k = action.kind
        if k in POINTER_KINDS:
            if pt is None:
                raise ValueError(f"{k} needs a target point")
            button, clicks = _BUTTONS[k]
            self._b.click(pt[0], pt[1], button, clicks)
            return pt
        if k == "type":
            text = action.text or ""
            for i in range(0, len(text), TYPE_CHUNK):
                self._panic.check()   # stop mid-sentence if the user hits panic
                self._b.type_text(text[i:i + TYPE_CHUNK])
            return None
        if k == "key":
            self._b.hotkey(*action.keys)
            return None
        if k == "scroll":
            self._b.scroll(action.amount, pt)
            return pt
        if k == "drag":
            if pt is None or pt2 is None:
                raise ValueError("drag needs two points")
            self._b.drag(pt, pt2)
            return pt2
        if k == "wait":
            self._sleep(action.seconds)
            return None
        if k == "open_app":
            self._b.hotkey("win")
            self._sleep(0.7)
            self._panic.check()
            self._b.type_text(action.app or "")
            self._sleep(0.9)
            self._panic.check()
            self._b.hotkey("enter")
            return None
        raise ValueError(f"not executable: {k}")


class DryRunExecutor:
    """Plans and previews everything, touches nothing."""

    def __init__(self) -> None:
        self.performed: list[tuple[Action, Point | None, Point | None]] = []

    def perform(self, action, pt, pt2):
        self.performed.append((action, pt, pt2))
        return pt


def make_executor(dry_run: bool, panic: PanicSwitch) -> Executor:
    if dry_run:
        return DryRunExecutor()
    return RealExecutor(PyAutoGuiBackend(), panic)
