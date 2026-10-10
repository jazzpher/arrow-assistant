import json

from arrow_assistant.agent import bench
from arrow_assistant.agent.actions import parse_action
from arrow_assistant.agent.planner import ProviderChain
from arrow_assistant.config import LLMProvider

from .fakes import ScriptedPost, gemini_ok

GEM = LLMProvider("gemini", "K", "m")


def test_suite_shape():
    suite = bench.synthetic_suite()
    assert len(suite) >= 10
    ids = [s.id for s in suite]
    assert len(set(ids)) == len(ids)
    for s in suite:
        assert s.image.size == (bench.W, bench.H)
        assert s.expect.get("kinds")


def test_check_click_near_and_far():
    ok, f = bench.check({"kinds": ["click"], "near": [100, 100, 40]},
                        parse_action('{"action":"click","x":110,"y":95}'))
    assert ok and not f
    ok, f = bench.check({"kinds": ["click"], "near": [100, 100, 40]},
                        parse_action('{"action":"click","x":300,"y":95}'))
    assert not ok and "too far" in f[0]


def test_check_kind_text_keys_risk_element_and_injection():
    assert not bench.check({"kinds": ["type"]}, parse_action('{"action":"click","x":1,"y":1}'))[0]
    assert bench.check({"kinds": ["type"], "text_contains": "Cebu"},
                       parse_action('{"action":"type","text":"cebu weather"}'))[0]
    assert not bench.check({"kinds": ["key"], "keys_include": ["ctrl", "a"]},
                           parse_action('{"action":"key","keys":["ctrl","c"]}'))[0]
    assert not bench.check({"kinds": ["click"], "risk": "high"},
                           parse_action('{"action":"click","x":1,"y":1,"risk":"low"}'))[0]
    assert bench.check({"kinds": ["click"], "element": 3},
                       parse_action('{"action":"click","element":3}'))[0]
    assert not bench.check({"kinds": ["click"], "element": 3},
                           parse_action('{"action":"click","element":2}'))[0]
    ok, f = bench.check({"kinds": ["click", "scroll"], "not_near": [500, 300, 80]},
                        parse_action('{"action":"click","x":510,"y":310}'))
    assert not ok and "injected" in f[0]


def _perfect_chain(suite):
    """Chain whose model answers every scenario correctly."""
    answers = []
    for s in suite:
        e = s.expect
        k = e["kinds"][0]
        if k == "click" and "element" in e:
            a = {"action": "click", "element": e["element"]}
        elif k == "click":
            a = {"action": "click", "x": e["near"][0], "y": e["near"][1],
                 "risk": e.get("risk", "low")}
        elif k == "type":
            a = {"action": "type", "text": e["text_contains"]}
        elif k == "key":
            a = {"action": "key", "keys": e["keys_include"]}
        elif k == "done":
            a = {"action": "done", "message": "ok"}
        else:
            a = {"action": k, "message": "need user"} if k in ("ask_user", "fail") \
                else {"action": "scroll", "amount": 5}
        answers.append(("generativelanguage", gemini_ok(json.dumps(a))))
    return ProviderChain([GEM], post=ScriptedPost(answers))


def test_run_bench_perfect_model_passes_everything():
    suite = bench.synthetic_suite()
    report = bench.run_bench({"gemini": _perfect_chain(suite)}, suite)
    s = report["summary"]["gemini"]
    assert s["pass"] == s["cases"] == len(suite)
    assert s["verdict"] == "good enough to drive the agent"
    assert "PASS" in bench.to_markdown(report)


def test_run_bench_bad_model_and_errors():
    suite = bench.synthetic_suite()[:3]
    bad = ProviderChain([GEM], post=ScriptedPost(
        [("generativelanguage", gemini_ok("not json"))] * 3))
    report = bench.run_bench({"gemini": bad}, suite)
    s = report["summary"]["gemini"]
    assert s["pass"] == 0 and s["valid"] == 0
    assert s["verdict"] == "too weak for the agent"

    from .fakes import FakeResp
    quota = ProviderChain([GEM], post=ScriptedPost(
        [("generativelanguage", FakeResp(429))] * 3))
    s = bench.run_bench({"gemini": quota}, suite)["summary"]["gemini"]
    assert s["errors"] == 3 and s["verdict"].startswith("unusable")


def test_delay_between_calls():
    sleeps = []
    suite = bench.synthetic_suite()[:2]
    bench.run_bench({"g": _perfect_chain(suite)}, suite, delay_s=4, sleep=sleeps.append)
    assert sleeps == [4, 4]
