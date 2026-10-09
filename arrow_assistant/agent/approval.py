"""Human-in-the-loop approvals.

The loop asks; a UI (HUD buttons, hotkeys, or a console) answers. An
unanswered request times out as SKIP, never as approval.
"""
from __future__ import annotations

import queue
from dataclasses import dataclass, field
from typing import Callable, Protocol

APPROVE, SKIP, STOP = "approve", "skip", "stop"
Point = tuple[int, int]


@dataclass(frozen=True)
class ApprovalRequest:
    kind: str                      # "action" | "plan"
    summary: str
    reasons: tuple[str, ...] = ()  # why this needs a human (risk reasons)
    risky: bool = False
    point: Point | None = None     # screen pixel to preview with the arrow
    step: int = 0
    max_steps: int = 0
    plan: tuple[str, ...] = field(default_factory=tuple)


class Approver(Protocol):
    def request(self, req: ApprovalRequest, timeout_s: float) -> str: ...


class QueueApprover:
    """Thread-safe approver: UI calls respond(), loop thread blocks in request()."""

    def __init__(self, on_request: Callable[[ApprovalRequest], None] | None = None):
        self._q: queue.Queue[str] = queue.Queue()
        self._on_request = on_request or (lambda req: None)
        self.pending: ApprovalRequest | None = None

    def request(self, req: ApprovalRequest, timeout_s: float = 120.0) -> str:
        self._drain()
        self.pending = req
        try:
            self._on_request(req)
            try:
                return self._q.get(timeout=timeout_s)
            except queue.Empty:
                return SKIP
        finally:
            self.pending = None

    def respond(self, decision: str) -> bool:
        """Called from UI/hotkey threads. Ignored when nothing is pending."""
        if decision not in (APPROVE, SKIP, STOP) or self.pending is None:
            return False
        self._q.put(decision)
        return True

    def _drain(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return


class ConsoleApprover:
    """For the CLI: y = approve, n/Enter = skip, s = stop."""

    def __init__(self, ask: Callable[[str], str] = input,
                 out: Callable[[str], None] = print):
        self._ask = ask
        self._out = out

    def request(self, req: ApprovalRequest, timeout_s: float = 0) -> str:
        head = "PLAN" if req.kind == "plan" else f"STEP {req.step}/{req.max_steps}"
        self._out(f"[{head}] {req.summary}")
        for r in req.reasons:
            self._out(f"   ! {r}")
        try:
            ans = self._ask("   approve? [y]es / [n]o skip / [s]top: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return STOP
        if ans in ("y", "yes"):
            return APPROVE
        if ans in ("s", "stop"):
            return STOP
        return SKIP


class AutoApprover:
    """Approves everything. Tests and explicit --yes dry runs only."""

    def request(self, req, timeout_s=0):
        return APPROVE
