"""Gemini 3.x request shape: thinking must not eat the answer budget, and
deprecated sampling params must not be sent. Gemini 2.x stays unchanged."""
from __future__ import annotations

import json
import os

from arrow_assistant import ai
from arrow_assistant.agent import planner
from arrow_assistant.config import LLMProvider


def test_version_detection():
    assert ai.is_gemini3_or_newer("gemini-3.8-flash")
    assert ai.is_gemini3_or_newer("models/gemini-3.5-flash-lite")
    assert ai.is_gemini3_or_newer("gemini-10-flash")
    assert not ai.is_gemini3_or_newer("gemini-2.5-flash")
    assert not ai.is_gemini3_or_newer("gemini-1.5-pro")
    assert not ai.is_gemini3_or_newer("")
    assert not ai.is_gemini3_or_newer("meta/llama-3.2-90b-vision-instruct")


def test_gemini3_config_low_thinking_headroom_no_temperature():
    gen = ai.gemini_generation_config("gemini-3.8-flash", 400, 0.4)
    assert gen["thinkingConfig"] == {"thinkingLevel": "low"}
    assert gen["maxOutputTokens"] >= 400 + 2048
    assert "temperature" not in gen
    assert "thinkingBudget" not in gen["thinkingConfig"]


def test_gemini25_config_unchanged():
    gen = ai.gemini_generation_config("gemini-2.5-flash", 400, 0.4)
    assert gen == {"maxOutputTokens": 400, "temperature": 0.4}
    gen = ai.gemini_generation_config("gemini-2.5-flash", 1024, 0.2,
                                      disable_legacy_thinking=True)
    assert gen["thinkingConfig"] == {"thinkingBudget": 0}
    assert gen["temperature"] == 0.2


class _StreamResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)


def test_teach_stream_sends_gemini3_config_and_skips_thought_parts(monkeypatch):
    seen = {}
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "plan...", "thought": True}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "[POINT:10,20:File] Click File."}]}}]},
    ]
    lines = [f"data: {json.dumps(c)}" for c in chunks]

    def fake_post(url, json=None, headers=None, **kw):
        seen["body"] = json
        return _StreamResp(lines)

    monkeypatch.setattr(ai.requests, "post", fake_post)
    p = LLMProvider("gemini", "k", "gemini-3.8-flash")
    out = "".join(ai.stream_answer(p, "q", "app", "", None, "AAAA"))
    assert out == "[POINT:10,20:File] Click File."
    gen = seen["body"]["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "temperature" not in gen


class _JsonResp:
    status_code = 200
    headers: dict = {}
    text = ""

    def json(self):
        return {"candidates": [{"content": {"parts": [
            {"text": '{"thought":"x","action":"done","message":"ok"}'}]}}]}


def test_planner_gemini3_request(monkeypatch):
    seen = {}

    def fake_post(url=None, headers=None, json=None, timeout=None):
        seen["body"] = json
        return _JsonResp()

    prov = LLMProvider("gemini", "k", "gemini-3.8-flash")
    chain = planner.ProviderChain([prov], post=fake_post)
    chain.complete("sys", [{"type": "text", "text": "hi"}], max_tokens=1024)
    gen = seen["body"]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "temperature" not in gen
    assert gen["maxOutputTokens"] > 1024


def test_env_example_does_not_pin_gemini_25():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".env.example"), encoding="utf-8") as fh:
        active = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any("gemini-2.5" in ln for ln in active)
