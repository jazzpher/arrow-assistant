"""Per-app memory: Arrow remembers your recent questions in each app.

Two stores, both local, nothing leaves your machine except inside the
prompt sent to your chosen LLM provider:
  - SQLite (WAL) for structured recall
  - a human-readable markdown tail per app in Documents/Arrow Memory
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

RECALL_LIMIT = 8          # recent Q/A pairs injected into the prompt
RECALL_MAX_CHARS = 2_000  # keep prompts bounded

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app TEXT NOT NULL,
    ts REAL NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interactions_app_ts ON interactions(app, ts);
"""


def memory_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "Arrow Memory")


class Memory:
    def __init__(self, db_path: str | None = None, md_dir: str | None = None):
        self._md_dir = md_dir or memory_dir()
        os.makedirs(self._md_dir, exist_ok=True)
        self._db_path = db_path or os.path.join(self._md_dir, "memory.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def record(self, app: str, question: str, answer: str) -> None:
        app = (app or "unknown").lower()
        ts = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO interactions (app, ts, question, answer) VALUES (?,?,?,?)",
                (app, ts, question, answer),
            )
            self._conn.commit()
        md = os.path.join(self._md_dir, f"{app}.md")
        try:
            with open(md, "a", encoding="utf-8") as fh:
                fh.write(f"## {time.strftime('%Y-%m-%d %H:%M')}\n"
                         f"**You:** {question}\n\n**Arrow:** {answer}\n\n")
        except OSError:
            pass

    def recall(self, app: str, limit: int = RECALL_LIMIT) -> str:
        """Return a compact markdown tail of recent Q&A for this app."""
        app = (app or "unknown").lower()
        with self._lock:
            rows = self._conn.execute(
                "SELECT question, answer FROM interactions WHERE app=? "
                "ORDER BY ts DESC LIMIT ?",
                (app, limit),
            ).fetchall()
        if not rows:
            return ""
        rows.reverse()
        parts = [f"You: {q}\nArrow: {a}" for q, a in rows]
        out = "\n\n".join(parts)
        return out[-RECALL_MAX_CHARS:]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
