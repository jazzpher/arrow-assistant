"""Error log file + tray notice. Under pythonw or a packaged build stderr
goes nowhere, so errors are also appended to Documents/Arrow Memory/arrow.log."""
from __future__ import annotations

import os
import sys
import time


def log_path() -> str:
    base = os.path.join(os.path.expanduser("~"), "Documents", "Arrow Memory")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        base = os.path.expanduser("~")
    return os.path.join(base, "arrow.log")


def log_error(msg: str) -> None:
    print(f"[arrow] {msg}", file=sys.stderr)
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass
