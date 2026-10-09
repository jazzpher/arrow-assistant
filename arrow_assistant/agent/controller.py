"""Owns the agent worker thread: one task at a time, stoppable from anywhere."""
from __future__ import annotations

import threading
from typing import Callable

from .loop import AgentLoop, AgentResult
from .panic import PanicSwitch
from .state import StateStore


class AgentController:
    def __init__(self, loop_factory: Callable[[], AgentLoop], panic: PanicSwitch,
                 store: StateStore | None = None,
                 on_finish: Callable[[AgentResult], None] | None = None):
        self._factory = loop_factory
        self.panic = panic
        self.store = store
        self._on_finish = on_finish or (lambda r: None)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_result: AgentResult | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, task: str, resume: bool = False) -> bool:
        """False when a task is already running (never two at once)."""
        with self._lock:
            if self.running:
                return False
            state = None
            if resume:
                state = self.store.load() if self.store else None
                if state is None:
                    return False
                task = state.task
            self.panic.reset()
            self._thread = threading.Thread(
                target=self._work, args=(task, state), daemon=True)
            self._thread.start()
            return True

    def _work(self, task, state) -> None:
        try:
            loop = self._factory()
            result = loop.run(task, resume=state)
        except Exception as exc:  # noqa: BLE001
            result = AgentResult("failed", f"Internal error: {exc}")
        self.last_result = result
        self._on_finish(result)

    def stop(self, reason: str = "stopped by user") -> None:
        self.panic.stop(reason)

    def join(self, timeout: float | None = None) -> None:
        t = self._thread
        if t:
            t.join(timeout)
