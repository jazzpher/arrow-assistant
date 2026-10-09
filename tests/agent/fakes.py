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


# ---- loop fakes -------------------------------------------------------------
from PIL import Image  # noqa: E402

from arrow_assistant.agent.actions import parse_action  # noqa: E402
from arrow_assistant.agent.coords import Frame  # noqa: E402
from arrow_assistant.agent.observation import Element, Observation, make_thumb  # noqa: E402
from arrow_assistant.agent.planner import Completion, Verdict  # noqa: E402


def img_of(level):
    im = Image.new("L", (160, 90), level)
    return make_thumb(im.convert("RGB"))


class FakeScreen:
    """observe() returns the next scripted screen level (sticky at the last)."""

    def __init__(self, levels=(100,), elements=None, app="notepad.exe", title="Untitled"):
        self.levels = list(levels)
        self.i = 0
        self.elements = elements or []
        self.app, self.title = app, title
        self.observed_masks = []
        self.cursor_pos = (0, 0)

    def observe(self, mask_rects=None):
        self.observed_masks.append(list(mask_rects or []))
        lvl = self.levels[min(self.i, len(self.levels) - 1)]
        self.i += 1
        return Observation(Frame.for_monitor({"left": 0, "top": 0, "width": 1600, "height": 900}),
                           self.app, self.title, "AAAA", img_of(lvl),
                           list(self.elements),
                           "\n".join(f"{e.id}: {e.role} '{e.name}'" for e in self.elements))

    def cursor(self):
        return self.cursor_pos


class FakePlanner:
    def __init__(self, items, plan=None, verdicts=None):
        self.items = list(items)
        self.plan = plan or []
        self.verdicts = list(verdicts or [])
        self.seen = []

    def make_plan(self, task, ctx):
        return list(self.plan)

    def next_action(self, task, plan, history, ctx):
        self.seen.append((list(history), ctx))
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        a = parse_action(it) if isinstance(it, str) else it
        return a, Completion("", "fake", "m", 0.0)

    def verify(self, task, history, ctx):
        return self.verdicts.pop(0) if self.verdicts else Verdict(True, "")


class RecExecutor:
    def __init__(self, panic=None, on_perform=None):
        self.calls = []
        self.on_perform = on_perform

    def perform(self, action, pt, pt2):
        self.calls.append((action.kind, pt, pt2))
        if self.on_perform:
            self.on_perform(action)
        return pt


class ScriptApprover:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.requests = []

    def request(self, req, timeout_s=0):
        self.requests.append(req)
        return self.decisions.pop(0) if self.decisions else "approve"


def el(i, name, rect=(0, 0, 100, 40), role="Button", **kw):
    return Element(i, name, role, rect, **kw)
