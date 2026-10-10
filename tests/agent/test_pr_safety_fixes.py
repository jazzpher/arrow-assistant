"""Regression tests for the second review:

1. agent mode crashed in the tray app (QtAgentUI had no hud_rects)
2. Tab moved focus into a password field and the password guard missed it
3. open_app typed the app name + Enter into whatever window had focus
4. keyboard actions went to a window that took focus after the screenshot
"""
from __future__ import annotations

import pytest

from arrow_assistant.agent.executor import ActionAborted, RealExecutor
from arrow_assistant.agent.loop import AgentLoop, LoopConfig
from arrow_assistant.agent.panic import PanicSwitch
from arrow_assistant.agent.risk import assess, is_sensitive_window

from .fakes import FakePlanner, FakeScreen, RecExecutor, ScriptApprover, el

CFG = dict(settle_s=0, verify_done=False, use_plan=False)


class LiveScreen(FakeScreen):
    """FakeScreen plus the optional live queries WindowsScreen provides."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.focus = None
        self.fg = None          # None = report whatever observe() reports

    def focused_element(self):
        return self.focus

    def foreground(self):
        return self.fg or (self.app, self.title)


def run(screen, steps, mode="auto", executor=None, approver=None):
    ex = executor or RecExecutor()
    loop = AgentLoop(screen, ex, FakePlanner(steps), approver or ScriptApprover([]),
                     risk=assess, sensitive=is_sensitive_window,
                     config=LoopConfig(mode=mode, **CFG), sleep=lambda s: None)
    return loop.run("task"), ex, loop


LOGIN = [el(1, "Email", (100, 100, 400, 140), role="Edit"),
         el(2, "", (100, 160, 400, 200), role="Edit", password=True)]
DONE = '{"action":"done","message":"ok"}'


# -- 2. password guard follows real keyboard focus ---------------------------------
def test_tab_into_password_field_then_type_is_blocked():
    scr = LiveScreen([10, 50, 90, 130, 170], LOGIN, app="chrome.exe",
                     title="Sign in - Google Chrome")
    ex = RecExecutor()

    def move_focus(a):
        scr.focus = LOGIN[0] if a.kind == "click" else LOGIN[1] if a.kind == "key" else scr.focus
    ex.on_perform = move_focus
    res, ex, _ = run(scr, ['{"action":"click","element":1}', '{"action":"key","keys":["tab"]}',
                           '{"action":"type","text":"hunter2"}', DONE], executor=ex)
    assert [c[0] for c in ex.calls] == ["click", "key"]       # the type never ran
    assert res.status == "done"


def test_focus_moving_to_password_during_approval_is_blocked():
    """Risk gate saw a normal field; focus moved while the human decided."""
    scr = LiveScreen([10], LOGIN, app="notepad.exe", title="Untitled")

    class Approver(ScriptApprover):
        def request(self, req, timeout_s=0):
            scr.focus = LOGIN[1]            # e.g. the page autofocused its PIN box
            return super().request(req, timeout_s)
    res, ex, _ = run(scr, ['{"action":"type","text":"hello"}', DONE], mode="step",
                     approver=Approver([]))
    assert ex.calls == []


def test_stale_clicked_field_is_forgotten_after_a_key():
    """Click a password box, Tab away, type: must NOT be blocked by the old field."""
    scr = FakeScreen([10, 50, 90, 130], LOGIN, app="notepad.exe", title="Untitled")
    res, ex, loop = run(scr, ['{"action":"click","element":2}', '{"action":"key","keys":["tab"]}',
                              '{"action":"type","text":"hello"}', DONE])
    assert [c[0] for c in ex.calls] == ["click", "key", "type"]
    assert loop._focus_el is None


# -- 4. keyboard actions re-check the foreground window -----------------------------
def test_type_is_not_sent_when_another_window_took_focus():
    scr = LiveScreen([10, 50], app="notepad.exe", title="Untitled - Notepad")
    scr.fg = ("messenger.exe", "Messenger")         # popped up after the screenshot
    res, ex, _ = run(scr, ['{"action":"type","text":"hello"}', DONE])
    assert ex.calls == []
    assert res.status == "done"


def test_repeated_focus_theft_gives_up():
    scr = LiveScreen([10], app="notepad.exe", title="Untitled")
    scr.fg = ("messenger.exe", "Messenger")
    res, ex, _ = run(scr, ['{"action":"key","keys":["ctrl","s"]}',
                           '{"action":"type","text":"a"}',
                           '{"action":"key","keys":["ctrl","v"]}'])
    assert ex.calls == [] and res.status == "failed"


def test_title_change_in_the_same_window_does_not_block_typing():
    class HwndScreen(LiveScreen):
        def observe(self, mask_rects=None):
            o = super().observe(mask_rects)
            o.hwnd = 42
            return o
    scr = HwndScreen([10, 50], app="chrome.exe", title="Messenger")
    scr.fg = ("chrome.exe", "(2) Messenger", 42)        # same window, title ticked
    res, ex, _ = run(scr, ['{"action":"type","text":"hello"}', DONE])
    assert [c[0] for c in ex.calls] == ["type"]
    scr2 = HwndScreen([10, 50], app="chrome.exe", title="Messenger")
    scr2.fg = ("chrome.exe", "Messenger", 99)           # different window, same title
    res, ex, _ = run(scr2, ['{"action":"type","text":"hello"}', DONE])
    assert ex.calls == []


def test_keyboard_action_runs_when_focus_is_unchanged():
    scr = LiveScreen([10, 50], app="notepad.exe", title="Untitled - Notepad")
    res, ex, _ = run(scr, ['{"action":"type","text":"hello"}', DONE])
    assert [c[0] for c in ex.calls] == ["type"]


def test_clicks_are_not_affected_by_the_keyboard_guard():
    scr = LiveScreen([10, 50], [el(1, "Save", (0, 0, 50, 20))], app="notepad.exe")
    scr.fg = ("other.exe", "Other")
    res, ex, _ = run(scr, ['{"action":"click","element":1}', DONE])
    assert [c[0] for c in ex.calls] == ["click"]


# -- 3. open_app only types into the Start menu -----------------------------------
class Backend:
    def __init__(self): self.calls = []
    def click(self, *a): self.calls.append(("click",) + a)
    def type_text(self, t): self.calls.append(("type", t))
    def hotkey(self, *k): self.calls.append(("hotkey", k))
    def scroll(self, *a): pass
    def drag(self, *a): pass


def _open(foreground):
    from arrow_assistant.agent.actions import parse_action
    b = Backend()
    e = RealExecutor(b, PanicSwitch(), sleep=lambda s: None, foreground=lambda: foreground(b))
    return e, b, parse_action('{"action":"open_app","app":"notepad"}')


def test_open_app_types_nothing_when_start_never_opens():
    e, b, a = _open(lambda b: "messenger.exe")
    with pytest.raises(ActionAborted, match="typed nothing"):
        e.perform(a, None, None)
    assert b.calls == [("hotkey", ("win",))]        # no app name, no Enter


def test_open_app_skips_enter_when_start_loses_focus():
    def fg(b):
        typed = any(c[0] == "type" for c in b.calls)
        return "messenger.exe" if typed else ("searchhost.exe" if b.calls else "notepad.exe")
    e, b, a = _open(fg)
    with pytest.raises(ActionAborted, match="did not press Enter"):
        e.perform(a, None, None)
    assert ("hotkey", ("enter",)) not in b.calls


def test_open_app_does_not_toggle_an_already_open_start_menu():
    e, b, a = _open(lambda b: "StartMenuExperienceHost.exe")
    e.perform(a, None, None)
    assert b.calls == [("type", "notepad"), ("hotkey", ("enter",))]


def test_open_app_happy_path_waits_for_start():
    seen = {"n": 0}

    def fg(b):
        if ("hotkey", ("win",)) not in b.calls:
            return "notepad.exe"
        seen["n"] += 1
        return "searchhost.exe" if seen["n"] > 2 else "notepad.exe"   # Start is a bit slow
    e, b, a = _open(fg)
    e.perform(a, None, None)
    assert b.calls == [("hotkey", ("win",)), ("type", "notepad"), ("hotkey", ("enter",))]


def test_loop_survives_an_aborted_action_and_replans():
    class AbortingExec(RecExecutor):
        def perform(self, action, pt, pt2):
            super().perform(action, pt, pt2)
            if action.kind == "open_app":
                raise ActionAborted("Start menu did not open; typed nothing")
            return pt
    scr = FakeScreen([10, 50, 90])
    res, ex, loop = run(scr, ['{"action":"open_app","app":"notepad"}', DONE],
                        executor=AbortingExec())
    assert res.status == "done"
    hist = loop.planner.seen[-1][0]
    assert any("aborted for safety" in h["outcome"] for h in hist)


# -- 1. the real Qt UI works with the loop ------------------------------------------
def test_logical_to_physical_rect_scales_offset_from_screen_origin():
    pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
    from arrow_assistant.agent.hud import logical_to_physical_rect
    # 150% screen at origin (0,0): logical (1000,40)-(1470,240)
    assert logical_to_physical_rect(1000, 40, 1470, 240, 0, 0, 1.5, margin=0) == \
        (1500, 60, 2205, 360)
    # secondary screen with a native origin at x=-1920, 125%
    assert logical_to_physical_rect(-1820, 0, -1720, 100, -1920, 0, 1.25, margin=0) == \
        (-1795, 0, -1670, 125)
    assert logical_to_physical_rect(0, 0, 10, 10, 0, 0, 1.0, margin=8) == (-8, -8, 18, 18)


def test_qt_agent_ui_has_hud_rects_and_loop_runs(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
    from PyQt6.QtWidgets import QApplication
    from arrow_assistant.agent.executor import DryRunExecutor
    from arrow_assistant.agent.stack import AgentStack
    from arrow_assistant.config import LLMProvider

    monkeypatch.setenv("ARROW_LOG_DIR", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path))

    class Overlay:
        def point_at(self, pts): pass

    qapp = QApplication.instance() or QApplication([])
    st = AgentStack(qapp, Overlay(), dry_run=True,
                    providers=[LLMProvider("gemini", "K", "m")],
                    screen=FakeScreen([10, 50]), executor_factory=DryRunExecutor)
    assert st.ui.hud_rects() == []                 # hidden HUD masks nothing
    st.ui._show()
    rects = st.ui.hud_rects()
    assert len(rects) == 1 and rects[0][2] > rects[0][0] and rects[0][3] > rects[0][1]
    st.ui._hide_hud()
    assert st.ui.hud_rects() == []

    loop = st._make_loop()
    loop.planner = FakePlanner(['{"action":"wait","seconds":0.2}', DONE])
    loop.cfg.verify_done = False
    loop.cfg.use_plan = False
    loop._sleep = lambda s: None
    res = loop.run("anything")
    assert res.status == "done", res.message      # was: AttributeError hud_rects
