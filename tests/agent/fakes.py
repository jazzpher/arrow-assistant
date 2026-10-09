"""Shared fakes for agent tests (no network, no screen, no mouse)."""
from __future__ import annotations

import json


class FakeResp:
    def __init__(self, status=200, data=None, headers=None, text=""):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def gemini_ok(text):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def openai_ok(text):
    return FakeResp(200, {"choices": [{"message": {"content": text}}]})


class ScriptedPost:
    """requests.post stand-in: returns queued responses per URL substring."""

    def __init__(self, script):
        self.script = list(script)  # [(url_substr, FakeResp|Exception)]
        self.calls = []

    def __call__(self, url=None, headers=None, json=None, timeout=None, **kw):
        self.calls.append({"url": url, "headers": headers, "json": json})
        for i, (sub, resp) in enumerate(self.script):
            if sub in url:
                self.script.pop(i)
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unscripted call to {url}")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s
