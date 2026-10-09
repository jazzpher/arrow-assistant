"""Sentence splitting for streaming LLM output.

As the model streams tokens, we flush each finished sentence to the TTS
provider the moment a ., !, or ? boundary lands, so the user starts hearing
the answer while the rest is still generating.
"""
from __future__ import annotations

import re

_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    """Split text into sentences on . ! ? boundaries. Never returns empties."""
    return [s for s in _BOUNDARY.split(text.strip()) if s]


class SentenceStreamer:
    """Incrementally consumes streamed chunks and yields complete sentences.

    Usage:
        streamer = SentenceStreamer()
        for chunk in model_stream:
            for sentence in streamer.feed(chunk):
                speak(sentence)
        for sentence in streamer.flush():
            speak(sentence)
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> list[str]:
        self._buf += chunk
        out: list[str] = []
        # A sentence is complete when a boundary char is followed by
        # whitespace + a new sentence start (or more text).
        while True:
            m = re.search(r"[.!?](?=\s+\S)", self._buf)
            if not m:
                break
            end = m.end()
            sentence = self._buf[:end].strip()
            if sentence:
                out.append(sentence)
            self._buf = self._buf[end:].lstrip()
        return out

    def flush(self) -> list[str]:
        tail = self._buf.strip()
        self._buf = ""
        return [tail] if tail else []
