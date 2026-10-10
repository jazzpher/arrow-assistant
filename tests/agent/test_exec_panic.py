import threading
import time

import pytest

from arrow_assistant.agent import approval as ap
from arrow_assistant.agent.actions import Action, parse_action
from arrow_assistant.agent.activity import ActivityMonitor
from arrow_assistant.agent.coords import Frame
from arrow_assistant.agent.executor import DryRunExecutor, RealExecutor
from arrow_assistant.agent.hotkeys import MultiCombo
from arrow_assistant.agent.panic import PanicStop, PanicSwitch
from arrow_assistant.hotkey import token_from_char_vk


class FakeBackend:
    def __init__(self, panic=None, stop_after_type_chunks=None):
        self.calls = []
        self._panic = panic
        self._stop_after = stop_after_type_chunks

    def click(self, x, y, button, clicks): self.calls.append(("click", x, y, button, clicks))

    def type_text(self, text):
        self.calls.append(("type", text))
        if self._stop_after and sum(c[0] == "type" for c in self.calls) >= self._stop_after:
            self._panic.stop("user panic")

    def hotkey(self, *keys): self.calls.append(("hotkey", keys))
    def scroll(self, amount, at): self.calls.append(("scroll", amount, at))
    def drag(self, s, e): self.calls.append(("drag", s, e))


def ex(panic=None, backend=None, sleeps=None):
    panic = panic or PanicSwitch()
    b = backend or FakeBackend()
    return RealExecutor(b, panic, sleep=(sleeps.append if sleeps is not None else lambda s: None)), b, panic


def A(raw):
    return parse_action(raw)


# -- coordinates ---------------------------------------------------------------
def test_frame_scales_1080p_down_and_back():
    f = Frame.for_monitor({"left": 0, "top": 0, "width": 1920, "height": 1080})
    assert f.size == (1568, 882)
    sx, sy = f.to_screen(784, 441)
    assert (sx, sy) == (960, 540)
    assert f.to_model(960, 540) == (784, 441)


def test_frame_secondary_monitor_negative_origin():
    f = Frame.for_monitor({"left": -1280, "top": 0, "width": 1280, "height": 1024})
    assert f.scale == 1.0
    assert f.to_screen(100, 50) == (-1180, 50)
    assert f.contains_screen(-1180, 50) and not f.contains_screen(10, 10)


def test_frame_clamps_out_of_range_model_coords():
    f = Frame.for_monitor({"left": 100, "top": 200, "width": 800, "height": 600})
    assert f.to_screen(-50, -50) == (100, 200)
    assert f.to_screen(99999, 99999) == (899, 799)


# -- executor -------------------------------------------------------------------
def test_click_variants():
    e, b, _ = ex()
    assert e.perform(A('{"action":"click","x":1,"y":1}'), (10, 20), None) == (10, 20)
    e.perform(A('{"action":"double_click","x":1,"y":1}'), (10, 20), None)
    e.perform(A('{"action":"right_click","x":1,"y":1}'), (10, 20), None)
    assert b.calls == [("click", 10, 20, "left", 1), ("click", 10, 20, "left", 2),
                       ("click", 10, 20, "right", 1)]


def test_click_without_point_is_an_error():
    e, _, _ = ex()
    with pytest.raises(ValueError):
        e.perform(A('{"action":"click","x":1,"y":1}'), None, None)


def test_type_is_chunked():
    e, b, _ = ex()
    e.perform(A('{"action":"type","text":"hello world, this is long"}'), None, None)
    texts = [c[1] for c in b.calls]
    assert "".join(texts) == "hello world, this is long" and len(texts) > 1


def test_panic_mid_typing_stops_further_chunks():
    panic = PanicSwitch()
    b = FakeBackend(panic, stop_after_type_chunks=2)
    e, _, _ = ex(panic, b)
    with pytest.raises(PanicStop):
        e.perform(A('{"action":"type","text":"%s"}' % ("x" * 100)), None, None)
    assert sum(c[0] == "type" for c in b.calls) == 2


def test_panic_before_action_blocks_it():
    panic = PanicSwitch()
    e, b, _ = ex(panic)
    panic.stop()
    with pytest.raises(PanicStop):
        e.perform(A('{"action":"click","x":1,"y":1}'), (1, 1), None)
    assert b.calls == []


def test_keys_scroll_drag_wait_open_app():
    sleeps = []
    e, b, _ = ex(sleeps=sleeps)
    e.perform(A('{"action":"key","keys":["ctrl","s"]}'), None, None)
    e.perform(A('{"action":"scroll","amount":3}'), (5, 5), None)
    e.perform(A('{"action":"drag","x":1,"y":1,"x2":9,"y2":9}'), (1, 1), (9, 9))
    e.perform(A('{"action":"wait","seconds":2}'), None, None)
    e.perform(A('{"action":"open_app","app":"notepad"}'), None, None)
    assert b.calls == [("hotkey", ("ctrl", "s")), ("scroll", 3, (5, 5)),
                       ("drag", (1, 1), (9, 9)), ("hotkey", ("win",)),
                       ("type", "notepad"), ("hotkey", ("enter",))]
    assert sleeps[0] == 2


def test_terminal_actions_are_not_executable():
    e, _, _ = ex()
    with pytest.raises(ValueError):
        e.perform(A('{"action":"done","message":"x"}'), None, None)


def test_dry_run_touches_nothing():
    d = DryRunExecutor()
    d.perform(A('{"action":"click","x":1,"y":1}'), (1, 1), None)
    assert len(d.performed) == 1


# -- panic switch ------------------------------------------------------------------
def test_pause_resume_and_stop_wakeup():
    p = PanicSwitch()
    p.pause("moved mouse")
    assert p.is_paused and p.reason == "moved mouse"
    assert p.wait_if_paused(0.01) is False  # timed out while still paused
    threading.Timer(0.05, p.resume).start()
    assert p.wait_if_paused(2) is True
    p.pause()
    threading.Timer(0.05, p.stop).start()
    assert p.wait_if_paused(2) is False and p.is_stopped
    p.reset()
    assert not p.is_stopped and not p.is_paused


def test_pause_does_not_override_stop():
    p = PanicSwitch()
    p.stop("x")
    p.pause("y")
    assert p.reason == "x" and not p.is_paused


# -- activity monitor ---------------------------------------------------------------
def test_activity_monitor():
    pos = [(100, 100)]
    m = ActivityMonitor(lambda: pos[0], tolerance_px=20)
    assert not m.user_moved()          # nothing expected yet
    m.expect((100, 100))
    pos[0] = (110, 95)
    assert not m.user_moved()          # within tolerance
    pos[0] = (400, 300)
    assert m.user_moved()
    m.rebase()
    assert not m.user_moved()


# -- approvals ------------------------------------------------------------------------
def test_queue_approver_roundtrip_and_ignores_stray():
    a = ap.QueueApprover()
    assert a.respond(ap.APPROVE) is False   # nothing pending
    seen = []
    a._on_request = lambda r: (seen.append(r), threading.Timer(0.02, lambda: a.respond(ap.APPROVE)).start())
    assert a.request(ap.ApprovalRequest("action", "click Save"), 2) == ap.APPROVE
    assert seen[0].summary == "click Save"


def test_queue_approver_timeout_is_skip_never_approve():
    a = ap.QueueApprover()
    t0 = time.time()
    assert a.request(ap.ApprovalRequest("action", "x"), 0.05) == ap.SKIP
    assert time.time() - t0 < 1


def test_stale_response_does_not_leak_into_next_request():
    a = ap.QueueApprover()
    a.pending = ap.ApprovalRequest("action", "old")
    a.respond(ap.APPROVE)   # late click for a request that already timed out
    a.pending = None
    assert a.request(ap.ApprovalRequest("action", "new"), 0.05) == ap.SKIP


def test_console_approver_answers():
    def mk(ans):
        return ap.ConsoleApprover(ask=lambda p: ans, out=lambda s: None)
    r = ap.ApprovalRequest("action", "x", ("danger",), True)
    assert mk("y").request(r) == ap.APPROVE
    assert mk("").request(r) == ap.SKIP
    assert mk("s").request(r) == ap.STOP

    def boom(p):
        raise EOFError
    assert ap.ConsoleApprover(ask=boom, out=lambda s: None).request(r) == ap.STOP


# -- hotkeys ------------------------------------------------------------------------------
def test_multicombo_fires_each_binding_once():
    hits = []
    m = MultiCombo({"ctrl+alt+esc": lambda: hits.append("panic"),
                    "ctrl+alt+y": lambda: hits.append("yes")})
    for t in ("ctrl", "alt", "y"):
        m.press(t)
    assert hits == ["yes"]
    m.release("y")
    m.press("esc")
    assert hits == ["yes", "panic"]


def test_ctrl_letter_control_chars_fall_back_to_vk():
    assert token_from_char_vk("\x01", 65) == "a"       # Ctrl+A on Windows
    assert token_from_char_vk(None, 89) == "y"
    assert token_from_char_vk("Y", 89) == "y"
    assert token_from_char_vk("\x01", None) is None
    assert token_from_char_vk(None, 27) is None
