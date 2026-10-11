"""The tracker is fed by teach mode (ai.py), the agent planner, and STT."""
import io
import json
import wave

import pytest

from arrow_assistant import ai, stt, usage
from arrow_assistant.agent.planner import ProviderChain, QuotaExhausted
from arrow_assistant.config import LLMProvider

GEM = LLMProvider("gemini", "KEY-G", "gemini-3.8-flash")
NV = LLMProvider("nvidia", "KEY-N", "meta/llama", base_url="https://nv.example/v1")
PARTS = [{"type": "text", "text": "hi"}]


class StreamResp:
    def __init__(self, status=200, lines=(), text=""):
        self.status_code = status
        self._lines = list(lines)
        self.text = text

    def iter_lines(self, decode_unicode=True):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse(obj):
    return "data: " + json.dumps(obj)


def test_teach_gemini_records_usage_metadata(monkeypatch, _isolated_usage):
    lines = [_sse({"candidates": [{"content": {"parts": [{"text": "Hi "}]}}]}),
             _sse({"candidates": [{"content": {"parts": [{"text": "there"}]}}],
                   "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 12,
                                     "totalTokenCount": 950}})]
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: StreamResp(200, lines))
    out = "".join(ai.stream_answer(GEM, "q", "notepad", "", None, None))
    assert out == "Hi there"
    g = _isolated_usage.snapshot()["gemini"]
    assert g["requests"] == 1 and g["total_tokens"] == 950
    assert g["models"]["gemini-3.8-flash"]["prompt_tokens"] == 900


def test_teach_openai_compat_records_usage(monkeypatch, _isolated_usage):
    lines = [_sse({"choices": [{"delta": {"content": "ok"}}]}),
             _sse({"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 4}}),
             "data: [DONE]"]
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: StreamResp(200, lines))
    assert "".join(ai.stream_answer(NV, "q", "a", "", None, None)) == "ok"
    assert _isolated_usage.snapshot()["nvidia"]["total_tokens"] == 34


def test_teach_429_notifies_and_counts(monkeypatch, _isolated_usage):
    alerts = []
    _isolated_usage.set_notifier(lambda m, s: alerts.append(m))
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: StreamResp(
        429, text='{"error":{"status":"RESOURCE_EXHAUSTED"}}'))
    with pytest.raises(RuntimeError) as ei:
        list(ai.stream_answer(GEM, "q", "a", "", None, None))
    assert "429" in str(ei.value) and "KEY-G" not in str(ei.value)
    assert _isolated_usage.snapshot()["gemini"]["requests"] == 1
    assert len(alerts) == 1 and "Free-tier quota reached for Gemini" in alerts[0]


def test_teach_hard_limit_refuses_without_network(monkeypatch, _isolated_usage):
    monkeypatch.setenv("ARROW_DAILY_REQUEST_LIMIT_GEMINI", "1")
    _isolated_usage.record("gemini", "m")
    def no_net(*a, **k):
        raise AssertionError("network must not be called")
    monkeypatch.setattr(ai.requests, "post", no_net)
    with pytest.raises(usage.UsageLimitReached):
        list(ai.stream_answer(GEM, "q", "a", "", None, None))


class FakeResp:
    def __init__(self, status=200, data=None, text="", headers=None):
        self.status_code, self._d, self.text, self.headers = status, data, text, headers or {}

    def json(self):
        if self._d is None:
            raise ValueError
        return self._d


def test_planner_records_tokens_and_falls_through_on_hard_limit(monkeypatch, _isolated_usage):
    calls = []
    def post(url=None, headers=None, json=None, timeout=None):
        calls.append(url)
        if "generativelanguage" in url:
            return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}],
                                  "usageMetadata": {"promptTokenCount": 10,
                                                    "candidatesTokenCount": 2,
                                                    "totalTokenCount": 12}})
        return FakeResp(200, {"choices": [{"message": {"content": "{}"}}],
                              "usage": {"prompt_tokens": 5, "completion_tokens": 1}})
    chain = ProviderChain([GEM, NV], post=post)
    assert chain.complete("s", PARTS).provider == "gemini"
    assert _isolated_usage.snapshot()["gemini"]["total_tokens"] == 12
    monkeypatch.setenv("ARROW_DAILY_REQUEST_LIMIT_GEMINI", "1")
    assert chain.complete("s", PARTS).provider == "nvidia"      # gemini refused -> next
    assert sum("generativelanguage" in u for u in calls) == 1
    assert _isolated_usage.snapshot()["nvidia"]["total_tokens"] == 6
    monkeypatch.setenv("ARROW_DAILY_REQUEST_LIMIT_NVIDIA", "1")
    with pytest.raises(QuotaExhausted):
        chain.complete("s", PARTS)


def test_planner_429_counts_and_notifies(_isolated_usage):
    alerts = []
    _isolated_usage.set_notifier(lambda m, s: alerts.append(m))
    def post(url=None, **k):
        return FakeResp(429, text="RESOURCE_EXHAUSTED GenerateRequestsPerDay")
    with pytest.raises(QuotaExhausted):
        ProviderChain([GEM], post=post).complete("s", PARTS)
    assert _isolated_usage.snapshot()["gemini"]["errors_429"] == 1
    assert "midnight Pacific" in alerts[0]


def _wav(seconds=1.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\0\0" * int(seconds * rate))
    return buf.getvalue()


def test_groq_stt_counts_requests_and_audio_seconds(monkeypatch, _isolated_usage):
    class R:
        status_code = 200
        text = "hello"

        def raise_for_status(self):
            pass
    monkeypatch.setattr("requests.post", lambda *a, **k: R())
    assert stt.transcribe(_wav(1.5), "groq", "gk") == "hello"
    g = _isolated_usage.snapshot()["groq"]
    assert g["requests"] == 1 and g["audio_seconds"] == 1.5
