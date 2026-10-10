"""One observe-only keyboard listener for all agent hotkeys.

Same rule as teach mode: suppress=False, so typing is never blocked.
"""
from __future__ import annotations

from typing import Callable

from ..hotkey import ComboTracker, parse_hotkey, token_from_pynput


class MultiCombo:
    """Pure: dispatch press/release tokens to several combos."""

    def __init__(self, bindings: dict):
        self._trackers = []
        for combo, fn in bindings.items():
            down, up = fn if isinstance(fn, tuple) else (fn, lambda: None)
            self._trackers.append(ComboTracker(parse_hotkey(combo), down, up))

    def press(self, token: str) -> None:
        for t in self._trackers:
            t.press(token)

    def release(self, token: str) -> None:
        for t in self._trackers:
            t.release(token)


class AgentHotkeys:
    def __init__(self, bindings: dict):
        from pynput import keyboard
        self._multi = MultiCombo(bindings)
        self._listener = keyboard.Listener(
            on_press=self._p, on_release=self._r, suppress=False)

    def _p(self, key):
        tok = token_from_pynput(key)
        if tok:
            self._multi.press(tok)

    def _r(self, key):
        tok = token_from_pynput(key)
        if tok:
            self._multi.release(tok)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()
