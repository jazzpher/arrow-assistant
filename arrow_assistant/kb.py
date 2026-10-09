"""Knowledge base: a drop-in folder of markdown docs for apps the AI
does not already know (company-internal tools, niche engineering software).

Drop a file named after the app's exe, e.g.
    %USERPROFILE%/Documents/Arrow Wiki/granta_edupack.exe.md
and its content is injected into the prompt whenever that app is focused.
"""
from __future__ import annotations

import os

MAX_KB_CHARS = 12_000  # keep prompts bounded


def wiki_dir() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs, "Arrow Wiki")


def lookup(app_exe: str, base_dir: str | None = None) -> str | None:
    """Return the KB markdown for app_exe (e.g. 'excel.exe'), or None."""
    if not app_exe:
        return None
    folder = base_dir or wiki_dir()
    path = os.path.join(folder, f"{app_exe.lower()}.md")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(MAX_KB_CHARS)
    except OSError:
        return None
