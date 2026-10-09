"""Global push-to-talk hotkey.

The pynput listener runs with suppress=False (observe-only): it sees the
keys, but Windows still delivers them to the focused app. suppress=True
would install a low-level hook that eats every keystroke system-wide -
never do that. This is why Ctrl+Alt+Space is the default: it clashes
with almost nothing (only Claude Desktop's default; unbind it there).

The combo state machine (ComboTracker) is pure and unit-tested; the
pynput listener is a thin adapter over it.
"""
from __future__ import annotations

from typing import Callable

_MODS = {"ctrl", "control", "alt", "shift", "cmd", "win", "option", "super"}
_CANON = {"control": "ctrl", "option": "alt", "win": "cmd", "super": "cmd"}


def parse_hotkey(combo: str) -> frozenset[str]:
    """'ctrl+alt+space' -> frozenset({'ctrl', 'alt', 'space'})."""
    parts = set()
    for token in combo.lower().split("+"):
        token = token.strip()
        if not token:
            continue
        parts.add(_CANON.get(token, token))
    if not parts:
        raise ValueError(f"empty hotkey: {combo!r}")
    return frozenset(parts)


class ComboTracker:
    """Pure press/release state machine for a key combo."""

    def __init__(self, combo: frozenset[str], on_press: Callable[[], None],
                 on_release: Callable[[], None]):
        self._combo = combo
        self._on_press = on_press
        self._on_release = on_release
        self._pressed: set[str] = set()
        self._active = False

    def press(self, token: str) -> None:
        self._pressed.add(token)
        if not self._active and self._combo <= self._pressed:
            self._active = True
            self._on_press()

    def release(self, token: str) -> None:
        self._pressed.discard(token)
        if self._active and token in self._combo:
            self._active = False
            self._on_release()


def token_from_pynput(key) -> str | None:
    """Map a pynput key event to a canonical token. Adapter-only."""
    from pynput import keyboard

    if isinstance(key, keyboard.Key):
        name = str(key)[4:]  # strip 'Key.'
        for side in ("_l", "_r", "_gr"):
            if name.endswith(side):
                name = name[: -len(side)]
                break
        return _CANON.get(name, name)
    return token_from_char_vk(getattr(key, "char", None), getattr(key, "vk", None))


def token_from_char_vk(char: str | None, vk: int | None) -> str | None:
    """Letter/digit token from a key event.

    With Ctrl held, Windows reports letters as control characters
    (Ctrl+A -> '\\x01'), so fall back to the virtual-key code.
    """
    if char and ord(char[0]) >= 32:
        c = char.lower()
        return _CANON.get(c, c)
    if vk is not None:
        if 65 <= vk <= 90 or 48 <= vk <= 57:
            return chr(vk).lower()
    return None


class HotkeyListener:
    """Observe-only push-to-talk listener backed by pynput."""

    def __init__(self, combo: str, on_press: Callable[[], None],
                 on_release: Callable[[], None]):
        from pynput import keyboard  # lazy: needs the OS keyboard backend

        self._tracker = ComboTracker(parse_hotkey(combo), on_press, on_release)
        self._listener = keyboard.Listener(
            on_press=self._handle_press, on_release=self._handle_release,
            suppress=False,  # observe-only: never blocks the user's typing
        )

    def _handle_press(self, key) -> None:
        token = token_from_pynput(key)
        if token:
            self._tracker.press(token)

    def _handle_release(self, key) -> None:
        token = token_from_pynput(key)
        if token:
            self._tracker.release(token)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()
