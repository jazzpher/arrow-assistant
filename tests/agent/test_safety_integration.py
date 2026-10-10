"""Loop + real risk gate together: the guarantees that matter."""
from arrow_assistant.agent.approval import APPROVE, SKIP
from arrow_assistant.agent.loop import AgentLoop, LoopConfig
from arrow_assistant.agent.panic import PanicSwitch
from arrow_assistant.agent.risk import assess, is_sensitive_window
from arrow_assistant.agent.ui import RecordingUI

from .fakes import FakePlanner, FakeScreen, RecExecutor, ScriptApprover, el

CLICK = '{"action":"click","x":%d,"y":%d,"label":"%s"}'
DONE = '{"action":"done","message":"ok"}'


def loop(planner, screen, approver=None, executor=None, mode="auto", scope=frozenset()):
    return AgentLoop(screen, executor or RecExecutor(), planner, approver or ScriptApprover([]),
                     ui=RecordingUI(), panic=PanicSwitch(), risk=assess,
                     sensitive=is_sensitive_window,
                     config=LoopConfig(settle_s=0, mode=mode, scope_apps=scope),
                     sleep=lambda s: None)


def test_password_manager_screenshot_never_reaches_the_model():
    planner = FakePlanner([CLICK % (1, 1, "x")])
    ex = RecExecutor()
    r = loop(planner, FakeScreen([100], app="keepassxc.exe", title="Vault"), executor=ex).run("t")
    assert r.status == "blocked" and planner.seen == [] and ex.calls == []


def test_secret_page_title_stops_before_planning():
    planner = FakePlanner([DONE])
    r = loop(planner, FakeScreen([100], app="chrome.exe", title="Enter your one-time code")).run("t")
    assert r.status == "blocked" and planner.seen == []


def test_injected_send_button_in_auto_mode_still_asks_and_skip_means_not_sent():
    ex = RecExecutor()
    scr = FakeScreen([10, 200, 10, 200], elements=[el(1, "Send to all contacts", (0, 0, 800, 800))],
                     app="chrome.exe", title="News - Chrome")
    ap = ScriptApprover([SKIP])
    r = loop(FakePlanner([CLICK % (50, 50, "Read more"), DONE]), scr, ap, ex).run("scroll the news")
    assert ex.calls == [] and ap.requests[0].risky and "Send" in ap.requests[0].reasons[0]


def test_password_typing_blocked_end_to_end():
    ex = RecExecutor()
    scr = FakeScreen([10, 200, 10, 200], elements=[el(7, "Password", (0, 0, 800, 800), password=True)],
                     app="chrome.exe", title="Sign in")
    p = FakePlanner([CLICK % (10, 10, "box"), '{"action":"type","text":"hunter2"}',
                     '{"action":"type","text":"hunter2"}'])
    ap = ScriptApprover([APPROVE] * 5)
    r = loop(p, scr, ap, ex, mode="step").run("log in")
    types = [c for c in ex.calls if c[0] == "type"]
    assert types == []
    assert r.status in ("blocked", "stuck", "failed")


def test_scope_apps_confirm_when_task_drifts_to_another_app():
    ap = ScriptApprover([SKIP])
    ex = RecExecutor()
    scr = FakeScreen([10, 200, 10, 200], app="chrome.exe", title="Some page")
    r = loop(FakePlanner([CLICK % (5, 5, "OK"), DONE]), scr, ap, ex,
             scope=frozenset({"excel.exe"})).run("fix sheet")
    assert ex.calls == [] and "outside" in ap.requests[0].reasons[0]


def test_every_physical_action_is_gated_in_step_mode_and_in_order():
    ap = ScriptApprover([APPROVE, APPROVE, APPROVE])
    ex = RecExecutor()
    scr = FakeScreen([10, 10, 200, 200, 10, 10, 200, 200, 10, 10, 200, 200],
                     app="notepad.exe", title="Untitled - Notepad")
    p = FakePlanner([CLICK % (5, 5, "Text area"), '{"action":"type","text":"hello"}',
                     '{"action":"key","keys":["ctrl","s"]}', DONE])
    r = loop(p, scr, ap, ex, mode="step").run("write hello and save")
    assert r.status == "done" and len(ap.requests) == 3
    assert [c[0] for c in ex.calls] == ["click", "type", "key"]
