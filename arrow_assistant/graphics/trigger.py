"""Decide whether a teach-mode question is asking for a generated visual.

Two tiers, deterministic on purpose (routing must never depend on the
model): STRONG patterns always route here; WEAK visual nouns (chart,
graph, plot, visual) only route when the same utterance also asks for
something to be made or shown - so "what chart type should I use in
Excel?" or "where is the chart button?" keep the normal pointing path.
English and Tagalog/Taglish, mirroring the rest of the app.
"""
from __future__ import annotations

import re

_STRONG = [
    r"\bdraw\b",
    r"\bsketch\b",
    r"\bdiagram\b",
    r"\bflow\s?chart\b",
    r"\b3-?d\b",
    r"\bthree[- ]dimensional\b",
    r"\bvisuali[sz]e\b",
    r"\billustrat\w*\b",
    r"\bblueprint\b",
    r"\bschematic\b",
    r"\bshow\s+me\s+(what|how).*(look|work)",
    # Tagalog / Taglish
    r"\biguhit\b",
    r"\bguhit\b",
    r"\blarawan\b",
    r"\bdesenyo\b",
    r"\b(ipakita|pakita)\b.*\b(kung paano|hitsura)\b",
]

# Nouns that also appear in ordinary teach questions about on-screen UI
# ("where is the chart button", "what chart type fits this data").
# "render" is deliberately NOT a weak noun: "how do I render this video?"
# is an app question. It stays a request verb, so "render a chart" works.
_WEAK_NOUN = re.compile(
    r"\b(charts?|graphs?|plots?|visuals?)\b",
    re.IGNORECASE)

# ...so they count only when the user is asking for one to be produced.
_REQUEST = re.compile(
    r"\b(draw|make|create|show|give|generate|produce|build|plot|render|"
    r"display|visuali[sz]e|gumawa|gawin|gawan|pakita|ipakita|iguhit|guhit|"
    r"can you|could you|please|paki)\b",
    re.IGNORECASE)

_STRONG_RES = [re.compile(p, re.IGNORECASE) for p in _STRONG]

_MAX_QUESTION_CHARS = 2000


def wants_graphics(question: str) -> bool:
    """True when the utterance asks Arrow to produce a visual (drawing,
    diagram, chart, or 3D view) rather than only talk/point."""
    if not question:
        return False
    text = question[:_MAX_QUESTION_CHARS]
    if any(r.search(text) for r in _STRONG_RES):
        return True
    return bool(_WEAK_NOUN.search(text) and _REQUEST.search(text))
