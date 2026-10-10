"""Audit trail: a readable markdown log of every step, per task."""
from __future__ import annotations

import re
import time
from pathlib import Path

from .state import default_dir


class ActionLog:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else default_dir()
        self.path: Path | None = None

    def start(self, task: str, mode: str, max_steps: int) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", task.lower())[:40].strip("-") or "task"
            self.path = self.dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.md"
            self._write(f"# Arrow agent task\n\n- task: {task}\n- mode: {mode}\n"
                        f"- max steps: {max_steps}\n- started: {time.strftime('%F %T')}\n\n## Steps\n")
        except OSError:
            self.path = None

    def step(self, n: int, action: str, decision: str, outcome: str,
             reasons: tuple[str, ...] = (), provider: str = "") -> None:
        extra = f" [{'; '.join(reasons)}]" if reasons else ""
        prov = f" ({provider})" if provider else ""
        self._write(f"{n}. {action}{extra} - {decision} - {outcome}{prov}\n")

    def note(self, text: str) -> None:
        self._write(f"- note: {text}\n")

    def finish(self, status: str, message: str) -> None:
        self._write(f"\n## Result\n\n- {status}: {message}\n- ended: {time.strftime('%F %T')}\n")

    def _write(self, text: str) -> None:
        if not self.path:
            return
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
