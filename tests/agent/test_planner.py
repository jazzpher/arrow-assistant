import pytest
import requests

from arrow_assistant.agent.planner import (
    PlanContext, Planner, PlannerError, ProviderChain, QuotaExhausted,
    build_step_parts)
from arrow_assistant.config import LLMProvider

from .fakes import Clock, FakeResp, ScriptedPost, gemini_ok, openai_ok

GEM = LLMProvider("gemini", "KEY-G", "gemini-2.5-flash")
NV = LLMProvider("nvidia", "KEY-N", "meta/llama", base_url="https://nv.example/v1")
OR = LLMProvider("openrouter", "KEY-O", "x:free", base_url="https://or.example/api/v1")
PARTS = [{"type": "text", "text": "hi"},
         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]
CLICK = '{"action":"click","x":5,"y":6,"label":"OK"}'


def chain(providers, script, clock=None):
    post = ScriptedPost(script)
    return ProviderChain(providers, post=post, clock=clock or Clock()), post


def test_gemini_success_uses_header_key_not_url():
    c, post = chain([GEM], [("generativelanguage", gemini_ok(CLICK))])
    comp = c.complete("sys", PARTS)
    assert comp.text == CLICK and comp.provider == "gemini"
    call = post.calls[0]
    assert "KEY-G" not in call["url"]
    assert call["headers"]["x-goog-api-key"] == "KEY-G"
    body = call["json"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["contents"][0]["parts"][1]["inline_data"]["data"] == "AAAA"


def test_openai_compat_shape():
    c, post = chain([NV], [("nv.example", openai_ok(CLICK))])
    c.complete("sys", PARTS)
    call = post.calls[0]
    assert call["url"] == "https://nv.example/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer KEY-N"
    assert call["json"]["messages"][0] == {"role": "system", "content": "sys"}


def test_429_falls_over_to_next_provider_and_cools_down():
    clock = Clock()
    c, post = chain([GEM, NV], [
        ("generativelanguage", FakeResp(429, text="quota", headers={"Retry-After": "30"})),
        ("nv.example", openai_ok(CLICK)),
        ("nv.example", openai_ok(CLICK)),
    ], clock)
    assert c.complete("s", PARTS).provider == "nvidia"
    # gemini is cooling: next call goes straight to nvidia without hitting gemini
    assert c.complete("s", PARTS).provider == "nvidia"
    assert sum("generativelanguage" in x["url"] for x in post.calls) == 1
    assert c.status()["gemini"]["cooldown_s"] == 30


def test_cooldown_expires():
    clock = Clock()
    c, post = chain([GEM, NV], [
        ("generativelanguage", FakeResp(429, headers={"Retry-After": "10"})),
        ("nv.example", openai_ok(CLICK)),
        ("generativelanguage", gemini_ok(CLICK)),
    ], clock)
    c.complete("s", PARTS)
    clock.advance(11)
    assert c.complete("s", PARTS).provider == "gemini"


def test_all_exhausted_raises_with_retry_hint():
    c, _ = chain([GEM, NV], [
        ("generativelanguage", FakeResp(429, headers={"Retry-After": "40"})),
        ("nv.example", FakeResp(503)),
    ])
    with pytest.raises(QuotaExhausted) as ei:
        c.complete("s", PARTS)
    assert 25 <= ei.value.retry_after_s <= 40


def test_daily_quota_body_sets_long_cooldown():
    c, _ = chain([GEM], [("generativelanguage",
                          FakeResp(429, text="Quota exceeded: RequestsPerDay"))])
    with pytest.raises(QuotaExhausted):
        c.complete("s", PARTS)
    assert c.status()["gemini"]["cooldown_s"] >= 3000


def test_auth_error_disables_provider_and_is_not_quota():
    c, _ = chain([GEM], [("generativelanguage", FakeResp(403, text="bad key"))])
    with pytest.raises(PlannerError):
        c.complete("s", PARTS)
    assert c.status()["gemini"]["disabled"]


def test_network_error_retries_next_provider():
    c, _ = chain([GEM, NV], [
        ("generativelanguage", requests.ConnectionError("boom KEY-G")),
        ("nv.example", openai_ok(CLICK)),
    ])
    comp = c.complete("s", PARTS)
    assert comp.provider == "nvidia"
    assert "KEY-G" not in c.status()["gemini"]["last_error"]


def test_empty_and_malformed_responses_fall_through():
    c, _ = chain([GEM, NV, OR], [
        ("generativelanguage", FakeResp(200, {"candidates": []})),
        ("nv.example", FakeResp(200, {"weird": 1})),
        ("or.example", openai_ok(CLICK)),
    ])
    assert c.complete("s", PARTS).provider == "openrouter"


def test_no_providers():
    with pytest.raises(QuotaExhausted):
        ProviderChain([]).complete("s", PARTS)


def test_thought_parts_are_ignored():
    data = {"candidates": [{"content": {"parts": [
        {"text": "reasoning...", "thought": True}, {"text": CLICK}]}}]}
    c, _ = chain([GEM], [("generativelanguage", FakeResp(200, data))])
    assert c.complete("s", PARTS).text == CLICK


def test_planner_retries_once_on_invalid_json():
    c, post = chain([GEM], [
        ("generativelanguage", gemini_ok("I will click now")),
        ("generativelanguage", gemini_ok(CLICK)),
    ])
    action, comp = Planner(c).next_action("t", [], [], PlanContext("AAAA"))
    assert action.kind == "click"
    second_prompt = post.calls[1]["json"]["contents"][0]["parts"][0]["text"]
    assert "previous reply was invalid" in second_prompt


def test_planner_gives_up_after_two_invalid():
    c, _ = chain([GEM], [("generativelanguage", gemini_ok("nope")),
                         ("generativelanguage", gemini_ok("still nope"))])
    with pytest.raises(PlannerError):
        Planner(c).next_action("t", [], [], PlanContext("AAAA"))


def test_planner_propagates_quota():
    c, _ = chain([GEM], [("generativelanguage", FakeResp(429))])
    with pytest.raises(QuotaExhausted):
        Planner(c).next_action("t", [], [], PlanContext("AAAA"))


def test_make_plan_tolerant():
    c, _ = chain([GEM], [("generativelanguage", gemini_ok('{"plan":["open notepad","type hi"]}'))])
    assert Planner(c).make_plan("t", PlanContext("AAAA")) == ["open notepad", "type hi"]
    c, _ = chain([GEM], [("generativelanguage", gemini_ok("garbage"))])
    assert Planner(c).make_plan("t", PlanContext("AAAA")) == []


def test_verify_verdicts():
    c, _ = chain([GEM], [("generativelanguage", gemini_ok('{"success":false,"reason":"not saved"}'))])
    v = Planner(c).verify("t", [], PlanContext("AAAA"))
    assert not v.success and v.reason == "not saved"
    c, _ = chain([GEM], [("generativelanguage", gemini_ok("???"))])
    assert Planner(c).verify("t", [], PlanContext("AAAA")).success


def test_step_prompt_contents_and_history_window():
    hist = [{"step": i, "action": f"a{i}", "outcome": "ok"} for i in range(1, 15)]
    ctx = PlanContext("AAAA", "excel.exe", "Book1", (1280, 720), "  1: Button 'Save'")
    parts = build_step_parts("do it", ["one", "two"], hist, ctx, note="careful")
    text = parts[0]["text"]
    assert "TASK: do it" in text and "1. one | 2. two" in text
    assert "excel.exe" in text and "Book1" in text and "1280x720" in text
    assert "a14" in text and "a1 " not in text.replace("a14", "")  # only last 8
    assert "Button 'Save'" in text and "NOTE: careful" in text
    assert parts[1]["type"] == "image_url"
    no_el = build_step_parts("t", [], [], PlanContext(None))
    assert "use x,y pixel coordinates" in no_el[0]["text"] and len(no_el) == 1
