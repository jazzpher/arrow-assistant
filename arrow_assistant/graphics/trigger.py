"""Decide whether a teach-mode question is asking for a generated visual.

Keyword-based and deterministic on purpose: routing must be testable and
must never depend on the model deciding to draw on its own. English and
Tagalog/Taglish phrasings are covered, mirroring the rest of the app.
"""
from __future__ import annotations

import re

# Whole-phrase patterns matched case-insensitively against the question.
# Each is specific enough that normal teach questions ("where is the draw
# toolbar?" is fine - it names an on-screen target and still matches; that
# is acceptable: the user IS asking about drawing) route here only when a
# visual artifact is being requested.
_PATTERNS = [
    r"\bdraw\b",
    r"\bsketch\b",
    r"\bdiagram\b",
    r"\bflow\s?chart\b",
    r"\b3-?d\b",
    r"\bthree[- ]dimensional\b",
    r"\bvisuali[sz]e\b",
    r"\billustrat\w*\b",
    r"\brender\b",
    r"\bblueprint\b",
    r"\bschematic\b",
    r"\bgraph\b",
    r"\bchart\b",
    r"\bmap\s+out\b",
    r"\bshow\s+me\s+(what|how).*(look|work)",
    # Tagalog / Taglish
    r"\biguhit\b",
    r"\bguhit\b",
    r"\blarawan\b",
    r"\bdesenyo\b",
    r"\bipakita\b.*\b(kung paano|hitsura)\b",
    r"\bpakita\b.*\b(kung paano|hitsura)\b",
]

_RES = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]

_MAX_QUESTION_CHARS = 2000


def wants_graphics(question: str) -> bool:
    """True when the utterance asks Arrow to produce a visual (drawing,
    diagram, chart, or 3D view) rather than only talk/point."""
    if not question:
        return False
    text = question[:_MAX_QUESTION_CHARS]
    return any(r.search(text) for r in _RES)
