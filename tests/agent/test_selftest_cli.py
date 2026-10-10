from PIL import Image

from arrow_assistant.agent import selftest
from arrow_assistant.agent.__main__ import main as cli_main
from arrow_assistant.config import LLMProvider

GEM = LLMProvider("gemini", "K", "m")


def test_checks_report_pass_and_fail():
    checks = selftest.run_checks(
        providers=[GEM], plat="win32",
        shot_fn=lambda: ({"left": 0, "top": 0}, Image.new("RGB", (10, 10))),
        uia_fn=lambda: [1, 2, 3], ping_fn=lambda p: (True, "ok"))
    d = {n: ok for n, ok, _ in checks}
    assert d["Windows"] and d["screenshot"] and d["agent API key"] and d["gemini reachable"]
    assert "PASS UI Automation" in selftest.render(checks)


def test_checks_flag_problems_without_crashing():
    def boom():
        raise RuntimeError("no display")
    checks = selftest.run_checks(providers=[], plat="linux", shot_fn=boom, uia_fn=lambda: [])
    d = {n: (ok, det) for n, ok, det in checks}
    assert not d["Windows"][0] and not d["screenshot"][0] and not d["agent API key"][0]
    assert "GEMINI_API_KEY" in d["agent API key"][1]
    out = selftest.render(checks)
    assert "FAIL" in out and "checks passed" in out


def test_ping_failure_is_reported_not_raised():
    def bad(p):
        raise ConnectionError("offline")
    checks = selftest.run_checks(providers=[GEM], plat="win32", ping_fn=bad)
    assert ("gemini reachable", False) == next((n, ok) for n, ok, _ in checks if "reachable" in n)


def test_cli_refuses_yes_without_dry_run_and_no_key(monkeypatch, capsys):
    import pytest
    with pytest.raises(SystemExit):
        cli_main(["task", "--yes"])
    monkeypatch.setattr("arrow_assistant.config.select_agent_providers", lambda: [])
    assert cli_main(["do something"]) == 2
