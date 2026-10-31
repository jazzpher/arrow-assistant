"""Split a streamed graphics answer into speech + the [SCENE] JSON spec.

The model is asked to speak first, then emit a line containing exactly
[SCENE] followed by one JSON object. SceneFilter is the streaming half
(mirroring points.SpeechFilter): feed() yields speakable text while
withholding anything that could be the marker; flush() returns the rest.
extract_spec() finds the JSON object after the marker with balanced-brace
scanning, tolerating ```json fences and trailing prose.

The extracted dict is untrusted model output; only spec.from_dict() turns
it into something the renderer consumes.
"""
from __future__ import annotations

import json

MARKER = "[SCENE]"
# Longest suffix of the marker that a chunk could end mid-way through.
_HOLD = len(MARKER) - 1


class SceneFilter:
    """feed(chunk) -> speech text safe to say out loud right now."""

    def __init__(self) -> None:
        self._buf = ""
        self.seen_marker = False

    def feed(self, chunk: str) -> str:
        if self.seen_marker:
            self._buf += chunk
            return ""
        self._buf += chunk
        idx = self._buf.find(MARKER)
        if idx >= 0:
            self.seen_marker = True
            out = self._buf[:idx]
            self._buf = self._buf[idx:]
            return out
        if len(self._buf) <= _HOLD:
            return ""
        out, self._buf = self._buf[:-_HOLD], self._buf[-_HOLD:]
        return out

    def flush(self) -> str:
        """Remaining speakable text (marker-less tail), if the model never
        emitted [SCENE]. After the marker the buffer is spec, not speech."""
        if self.seen_marker:
            return ""
        out, self._buf = self._buf, ""
        return out

    def spec_text(self) -> str:
        """Everything from the marker onward (empty when no marker seen)."""
        return self._buf if self.seen_marker else ""


def _balanced_start(text: str) -> int:
    """Index of the first '{' that opens a balanced object, or -1."""
    start = text.find("{")
    return start


def extract_json_object(text: str) -> str | None:
    """Return the first balanced {...} substring (string-aware), or None."""
    start = _balanced_start(text)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None          # truncated stream: unbalanced


def extract_spec(text: str) -> dict | None:
    """Full answer text -> parsed spec dict, or None when absent/broken.

    Looks after the [SCENE] marker first; falls back to the whole text so
    a model that skipped the marker but still emitted one JSON object with
    an "items" list still produces a visual.
    """
    candidates = []
    idx = text.find(MARKER)
    if idx >= 0:
        candidates.append(text[idx + len(MARKER):])
    candidates.append(text)
    for cand in candidates:
        obj = extract_json_object(cand)
        if obj is None:
            continue
        try:
            data = json.loads(obj)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    return None
