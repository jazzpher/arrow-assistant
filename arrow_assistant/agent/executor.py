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

# Processes that own the Start menu / Start search box (Windows 10 and 11).
START_HOSTS = frozenset({
    "startmenuexperiencehost.exe",   # Start menu (10 + 11)
    "searchhost.exe",                # Start search (11)
    "searchapp.exe",                 # Start search (10, 20H1+)
    "searchui.exe",                  # Start search (older 10)
})
START_WAIT_S = 2.0


class ActionAborted(RuntimeError):
    """An action stopped half-way on purpose, before anything risky was
    sent (e.g. the Start menu never opened, so the app name and Enter were
    NOT typed into whatever window had focus)."""


def _default_foreground() -> str:
    from .. import capture
    return capture.foreground_app()


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
                 sleep: Callable[[float], None] = time.sleep,
                 foreground: Callable[[], str] | None = None):
        self._b = backend
        self._panic = panic
        self._sleep = sleep
        self._fg = foreground or _default_foreground

    def _foreground(self) -> str:
        try:
            return (self._fg() or "").lower()
        except Exception:  # noqa: BLE001
            return ""

    def _wait_for_start(self, timeout_s: float = START_WAIT_S) -> bool:
        waited = 0.0
        while True:
            if self._foreground() in START_HOSTS:
                return True
            if waited >= timeout_s:
                return False
            self._sleep(0.2)
            waited += 0.2
            self._panic.check()

    def _open_app(self, app: str) -> None:
        """Win -> type name -> Enter, but ONLY into the Start menu.

        Without these checks a Start menu that did not open (or was already
        open and got toggled shut by Win) means the app name and Enter go to
        the focused window - in a chat app that sends a message.
        """
        if self._foreground() not in START_HOSTS:
            self._b.hotkey("win")
            if not self._wait_for_start():
                raise ActionAborted(
                    f"Start menu did not open (focused: {self._foreground() or 'unknown'}); "
                    "typed nothing")
        self._panic.check()
        self._b.type_text(app)
        self._sleep(0.9)
        self._panic.check()
        if self._foreground() not in START_HOSTS:
            raise ActionAborted(
                f"Start menu lost focus while typing (focused: {self._foreground() or 'unknown'}); "
                "did not press Enter")
        self._b.hotkey("enter")

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
            self._open_app(action.app or "")
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
