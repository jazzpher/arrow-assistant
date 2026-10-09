"""What the agent shows the human. The loop only talks to this protocol."""
from __future__ import annotations

from typing import Protocol

Point = tuple[int, int]


class AgentUI(Protocol):
    def status(self, text: str) -> None: ...
    def step(self, n: int, max_steps: int, text: str) -> None: ...
    def preview(self, pt: Point | None, label: str) -> None: ...
    def say(self, text: str) -> None: ...
    def finished(self, status: str, message: str) -> None: ...


class NullUI:
    def status(self, text): pass
    def step(self, n, max_steps, text): pass
    def preview(self, pt, label): pass
    def say(self, text): pass
    def finished(self, status, message): pass


class RecordingUI:
    """Keeps every call so tests can assert what the human would have seen."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def status(self, text): self.events.append(("status", text))
    def step(self, n, max_steps, text): self.events.append(("step", n, max_steps, text))
    def preview(self, pt, label): self.events.append(("preview", pt, label))
    def say(self, text): self.events.append(("say", text))
    def finished(self, status, message): self.events.append(("finished", status, message))

    def kinds(self) -> list[str]:
        return [e[0] for e in self.events]


class ConsoleUI:
    def __init__(self, out=print):
        self._out = out

    def status(self, text): self._out(f"[arrow] {text}")
    def step(self, n, max_steps, text): self._out(f"[arrow] step {n}/{max_steps}: {text}")
    def preview(self, pt, label): self._out(f"[arrow]   target {pt} {label}")
    def say(self, text): self._out(f"[arrow] {text}")
    def finished(self, status, message): self._out(f"[arrow] {status.upper()}: {message}")
