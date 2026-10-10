"""Task state: survives quota exhaustion and restarts so work can resume."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_dir() -> Path:
    base = Path(os.environ.get("ARROW_LOG_DIR") or (Path.home() / "Documents" / "Arrow Logs"))
    return base


@dataclass
class TaskState:
    task: str
    mode: str = "step"
    max_steps: int = 25
    step: int = 0
    plan: list[str] = field(default_factory=list)
    plan_approved: bool = False
    history: list[dict] = field(default_factory=list)
    status: str = "running"
    message: str = ""
    started: float = field(default_factory=time.time)
    verified: bool = False

    def add(self, action: str, outcome: str) -> None:
        self.history.append({"step": self.step, "action": action, "outcome": outcome})
        self.history = self.history[-60:]


class StateStore:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else default_dir()
        self.path = self.dir / "state.json"

    def save(self, state: TaskState) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass   # state is a convenience; never crash the task over it

    def load(self) -> TaskState | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return TaskState(**{k: v for k, v in data.items()
                                if k in TaskState.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
