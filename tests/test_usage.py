import datetime as dt
import json
import threading

import pytest

from arrow_assistant import usage
from arrow_assistant.usage import UsageLimitReached, UsageTracker


class Day:
    def __init__(self, d=dt.date(2026, 10, 11)):
        self.d = d

    def __call__(self):
        return self.d


def make(tmp_path, env=None, day=None):
    alerts = []
    t = UsageTracker(path=str(tmp_path / "u.json"), today=day or Day(), env=env or {})
    t.set_notifier(lambda m, s: alerts.append((m, s)))
    return t, alerts


def test_budget_defaults_and_overrides():
    assert usage.budget("request_warn", "gemini", {}) == 50
    assert usage.budget("request_limit", "gemini", {}) == 0          # hard stop off
    assert usage.budget("token_warn", "groq", {}) == 0
    env = {"ARROW_DAILY_REQUEST_WARN": "50", "ARROW_DAILY_REQUEST_WARN_GEMINI": "10",
           "ARROW_DAILY_REQUEST_LIMIT": "off"}
    assert usage.budget("request_warn", "gemini", env) == 10
    assert usage.budget("request_warn", "nvidia", env) == 50
    assert usage.budget("request_limit", "nvidia", env) == 0
    assert usage.budget("request_warn", "nvidia", {"ARROW_DAILY_REQUEST_WARN": "junk"}) == 300


def test_counts_per_provider_and_model_persist(tmp_path):
    t, _ = make(tmp_path)
    t.record("gemini", "gemini-3.8-flash", 1000, 50, 1100)
    t.record("gemini", "gemini-3.8-flash", 10, 5)
    t.record("groq", "whisper-large-v3-turbo", audio_seconds=2.5)
    snap = t.snapshot()
    g = snap["gemini"]
    assert g["requests"] == 2 and g["total_tokens"] == 1115 and g["prompt_tokens"] == 1010
    assert g["models"]["gemini-3.8-flash"]["requests"] == 2
    assert snap["groq"]["audio_seconds"] == 2.5
    data = json.loads((tmp_path / "u.json").read_text())
    assert data["days"]["2026-10-11"]["providers"]["gemini"]["requests"] == 2
    t2 = UsageTracker(path=str(tmp_path / "u.json"), today=Day(), env={})
    assert t2.snapshot()["gemini"]["requests"] == 2


def test_new_day_resets_counts(tmp_path):
    day = Day()
    t, _ = make(tmp_path, day=day)
    t.record("gemini", "m")
    day.d = dt.date(2026, 10, 12)
    assert t.snapshot() == {}
    t.record("gemini", "m")
    assert t.snapshot()["gemini"]["requests"] == 1


def test_warn_at_80_and_100_once_each(tmp_path):
    t, alerts = make(tmp_path, env={"ARROW_DAILY_REQUEST_WARN_GEMINI": "10"})
    for _ in range(7):
        t.record("gemini", "m")
    assert alerts == []
    t.record("gemini", "m")                         # 8/10 = 80%
    assert len(alerts) == 1 and "80%" in alerts[0][0]
    assert "malapit na sa daily limit ang Gemini" in alerts[0][1]
    t.record("gemini", "m")                         # 90%: nothing new
    assert len(alerts) == 1
    t.record("gemini", "m")                         # 100%
    assert len(alerts) == 2 and "reached" in alerts[1][0]
    for _ in range(5):
        t.record("gemini", "m")
    assert len(alerts) == 2                          # never repeats
    # alerts survive a restart (no re-notification the same day)
    t2, alerts2 = make(tmp_path, env={"ARROW_DAILY_REQUEST_WARN_GEMINI": "10"})
    t2.record("gemini", "m")
    assert alerts2 == []


def test_token_budget_triggers_warning(tmp_path):
    t, alerts = make(tmp_path, env={"ARROW_DAILY_TOKEN_WARN": "1000"})
    t.record("nvidia", "m", 500, 400)               # 900/1000 = 90%
    assert len(alerts) == 1 and "tokens" in alerts[0][0]


def test_single_jump_past_both_levels_alerts_once(tmp_path):
    t, alerts = make(tmp_path, env={"ARROW_DAILY_TOKEN_WARN": "100"})
    t.record("gemini", "m", 500, 500)
    assert len(alerts) == 1 and "reached" in alerts[0][0]


def test_alerts_can_be_silenced(tmp_path):
    t, alerts = make(tmp_path, env={"ARROW_DAILY_REQUEST_WARN": "1", "ARROW_USAGE_ALERTS": "0"})
    t.record("gemini", "m")
    assert alerts == [] and t.snapshot()["gemini"]["requests"] == 1


def test_hard_limit_blocks_only_that_provider(tmp_path):
    t, alerts = make(tmp_path, env={"ARROW_DAILY_REQUEST_LIMIT_GEMINI": "2",
                                    "ARROW_DAILY_REQUEST_WARN": "0"})
    t.check_allowed("gemini")
    t.record("gemini", "m")
    t.record("gemini", "m")
    with pytest.raises(UsageLimitReached) as ei:
        t.check_allowed("gemini")
    assert "daily request limit" in str(ei.value)
    t.check_allowed("nvidia")
    with pytest.raises(UsageLimitReached):
        t.check_allowed("gemini")
    assert sum("limit reached" in m for m, _ in alerts) == 1   # told once


def test_quota_hit_notifies_once_per_day(tmp_path):
    t, alerts = make(tmp_path)
    t.quota_hit("gemini", '{"error":{"status":"RESOURCE_EXHAUSTED","message":"GenerateRequestsPerDay"}}')
    t.quota_hit("gemini", "again")
    assert len(alerts) == 1
    assert "Free-tier quota reached for Gemini" in alerts[0][0]
    assert "midnight Pacific" in alerts[0][0]
    assert t.snapshot()["gemini"]["errors_429"] == 2
    t.quota_hit("openrouter", "")
    assert len(alerts) == 2 and "resets later" in alerts[1][0]
    t.quota_hit("openrouter", "", status=402)
    assert "payment required" in alerts[2][0]


def test_io_errors_never_raise(tmp_path):
    bad = tmp_path / "file-not-dir"
    bad.write_text("x")
    t = UsageTracker(path=str(bad / "usage.json"), today=Day(), env={})
    t.record("gemini", "m", 1, 1)                   # cannot save: silently kept in memory
    assert t.snapshot()["gemini"]["requests"] == 1
    (tmp_path / "corrupt.json").write_text("{not json")
    t2 = UsageTracker(path=str(tmp_path / "corrupt.json"), today=Day(), env={})
    assert t2.snapshot() == {}
    t2.record("gemini", "m")


def test_notifier_exception_is_swallowed(tmp_path):
    t = UsageTracker(path=str(tmp_path / "u.json"), today=Day(),
                     env={"ARROW_DAILY_REQUEST_WARN": "1"})
    t.set_notifier(lambda m, s: 1 / 0)
    t.record("gemini", "m")


def test_thread_safe_counts(tmp_path):
    t, _ = make(tmp_path)
    def worker():
        for _ in range(50):
            t.record("nvidia", "m", 1, 1)
    ths = [threading.Thread(target=worker) for _ in range(8)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    assert t.snapshot()["nvidia"]["requests"] == 400
    assert t.snapshot()["nvidia"]["total_tokens"] == 800


def test_token_parsers():
    assert usage.tokens_from_gemini({"usageMetadata": {
        "promptTokenCount": 1200, "candidatesTokenCount": 40,
        "thoughtsTokenCount": 60, "totalTokenCount": 1300}}) == (1200, 100, 1300)
    assert usage.tokens_from_gemini({}) == (0, 0, 0)
    assert usage.tokens_from_openai({"usage": {"prompt_tokens": 7, "completion_tokens": 3}}) == (7, 3, 10)


def test_summary_text_lists_configured_providers(tmp_path):
    t, _ = make(tmp_path, env={"ARROW_DAILY_REQUEST_LIMIT_GEMINI": "500"})
    t.record("gemini", "gemini-3.8-flash", 100, 20)
    txt = t.summary_text(["gemini", "groq"])
    assert "Gemini: 1 requests (warn 50, limit 500)" in txt
    assert "120 tokens" in txt and "Groq: 0 requests" in txt


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._d = data

    def json(self):
        return self._d


def test_openrouter_key_warning():
    def get_with(data, status=200):
        return lambda url, headers, timeout: _Resp(status, {"data": data})
    free = {"is_free_tier": True, "usage_daily": 0}
    assert usage.openrouter_key_warning("k", "x:free", get=get_with(free)) is None
    assert "not a ':free' model" in usage.openrouter_key_warning("k", "openai/gpt-4o", get=get_with(free))
    spent = {"is_free_tier": False, "usage_daily": 0.0123}
    assert "spent $0.0123" in usage.openrouter_key_warning("k", "x:free", get=get_with(spent))
    paid = {"is_free_tier": False, "usage_daily": 0}
    assert "bought credits" in usage.openrouter_key_warning("k", "x:free", get=get_with(paid))
    assert usage.openrouter_key_warning("k", "x", get=get_with({}, 401)) is None
    def boom(*a, **k):
        raise OSError("offline")
    assert usage.openrouter_key_warning("k", "x", get=boom) is None
    assert usage.openrouter_key_warning("", "x", get=boom) is None
