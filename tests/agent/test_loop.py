import pytest

from arrow_assistant.agent.activity import ActivityMonitor
from arrow_assistant.agent.approval import APPROVE, SKIP, STOP
from arrow_assistant.agent.loop import AgentLoop, LoopConfig
from arrow_assistant.agent.panic import PanicStop, PanicSwitch
from arrow_assistant.agent.planner import PlannerError, QuotaExhausted, Verdict
from arrow_assistant.agent.risk import Assessment
from arrow_assistant.agent.state import StateStore
from arrow_assistant.agent.ui import RecordingUI

from .fakes import FakePlanner, FakeScreen, RecExecutor, ScriptApprover, el

CLICK = '{"action":"click","x":%d,"y":%d,"label":"%s"}'
DONE = '{"action":"done","message":"ok na"}'


def mk(planner, screen=None, approver=None, executor=None, risk=None, cfg=None,
       activity=None, panic=None, store=None, ui=None):
    cfg = cfg or LoopConfig(settle_s=0)
    loop = AgentLoop(screen or FakeScreen([10, 200, 10, 200, 10, 200, 10, 200, 10, 200]),
                     executor or RecExecutor(), planner,
                     approver or ScriptApprover([]), ui=ui or RecordingUI(),
                     panic=panic or PanicSwitch(), activity=activity,
                     risk=risk or (lambda c: Assessment()), store=store,
                     config=cfg, sleep=lambda s: None)
    return loop


def test_happy_path_click_then_done():
    ex = RecExecutor()
    p = FakePlanner([CLICK % (100, 50, "Save"), DONE])
    ui = RecordingUI()
    r = mk(p, executor=ex, ui=ui, cfg=LoopConfig(settle_s=0, mode="auto")).run("save it")
    assert r.status == "done" and r.steps == 2 and r.message == "ok na"
    # 1600x900 monitor is scaled to 1568 wide: (100,50) maps back to ~ (102,51)
    assert ex.calls == [("click", (102, 51), None)]
    assert ui.events[-1] == ("finished", "done", "ok na")


def test_history_is_fed_back_to_the_model_with_outcome():
    p = FakePlanner([CLICK % (10, 10, "a"), DONE])
    mk(p, cfg=LoopConfig(settle_s=0, mode="auto")).run("t")
    hist, _ = p.seen[1]
    assert hist[0]["step"] == 1 and "screen changed" in hist[0]["outcome"]


def test_step_confirm_asks_for_every_physical_action_but_not_scroll():
    ap = ScriptApprover([APPROVE, APPROVE])
    p = FakePlanner([CLICK % (1, 1, "x"), '{"action":"scroll","amount":3}',
                     '{"action":"type","text":"hi"}', DONE])
    r = mk(p, approver=ap).run("t")
    assert r.status == "done"
    assert [q.summary.split()[0] for q in ap.requests] == ["click", "type"]


def test_skip_does_not_execute_and_is_reported_to_model():
    ex = RecExecutor()
    ap = ScriptApprover([SKIP])
    p = FakePlanner([CLICK % (1, 1, "x"), DONE])
    r = mk(p, executor=ex, approver=ap).run("t")
    assert ex.calls == [] and r.status == "done"
    assert "skipped" in p.seen[1][0][0]["outcome"]


def test_repeated_skips_hand_back_to_user():
    ap = ScriptApprover([SKIP] * 4)
    p = FakePlanner([CLICK % (i, i, "x") for i in range(1, 6)])
    r = mk(p, approver=ap).run("t")
    assert r.status == "needs_user" and r.resumable


def test_stop_at_approval_raises_stopped_and_acts_on_nothing():
    ex = RecExecutor()
    r = mk(FakePlanner([CLICK % (1, 1, "x")]), executor=ex,
           approver=ScriptApprover([STOP])).run("t")
    assert r.status == "stopped" and ex.calls == []


def test_step_cap_and_resume_extends():
    p = FakePlanner([CLICK % (i, i, "x") for i in range(1, 20)])
    store_cfg = LoopConfig(settle_s=0, mode="auto", max_steps=3)
    loop = mk(p, cfg=store_cfg, screen=FakeScreen([10, 200] * 20))
    r = loop.run("t")
    assert r.status == "step_limit" and r.steps == 3 and r.resumable
    state = type("S", (), {})  # resume via saved state
    from arrow_assistant.agent.state import TaskState
    st = TaskState(task="t", step=3, max_steps=3)
    p2 = FakePlanner([DONE])
    r2 = mk(p2, cfg=store_cfg).run("t", resume=st)
    assert r2.status == "done" and st.max_steps == 6


def test_stuck_when_same_action_repeats():
    p = FakePlanner([CLICK % (5, 5, "x")] * 5)
    r = mk(p, cfg=LoopConfig(settle_s=0, mode="auto"),
           screen=FakeScreen([10, 200] * 20)).run("t")
    assert r.status == "stuck"


def test_no_effect_streak_stops_pointer_actions():
    p = FakePlanner([CLICK % (i * 10, 5, "x") for i in range(1, 8)])
    r = mk(p, cfg=LoopConfig(settle_s=0, mode="auto"),
           screen=FakeScreen([100])).run("t")      # screen never changes
    assert r.status == "stuck" and r.steps == 3
    assert "Walang nagbabago" in r.message


def test_typing_without_visible_change_is_not_stuck():
    p = FakePlanner(['{"action":"type","text":"a%d"}' % i for i in range(5)] + [DONE])
    r = mk(p, cfg=LoopConfig(settle_s=0, mode="auto"), screen=FakeScreen([100])).run("t")
    assert r.status == "done"


def test_done_verification_rejects_then_accepts():
    p = FakePlanner([DONE, CLICK % (5, 5, "x"), DONE],
                    verdicts=[Verdict(False, "file not saved")])
    r = mk(p, cfg=LoopConfig(settle_s=0, mode="auto")).run("t")
    assert r.status == "done" and r.steps == 3
    assert "verification failed" in p.seen[1][0][0]["outcome"]


def test_ask_user_and_fail_terminate():
    r = mk(FakePlanner(['{"action":"ask_user","message":"password?"}'])).run("t")
    assert r.status == "needs_user" and r.message == "password?"
    r = mk(FakePlanner(['{"action":"fail","message":"cannot"}'])).run("t")
    assert r.status == "failed"


def test_blocked_action_never_executes_and_two_blocks_stop():
    ex = RecExecutor()
    block = lambda c: Assessment("block", ("password field",))
    p = FakePlanner([CLICK % (1, 1, "a"), CLICK % (2, 2, "b")])
    r = mk(p, executor=ex, risk=block).run("t")
    assert r.status == "blocked" and ex.calls == []


def test_confirm_level_asks_even_in_auto_mode():
    ap = ScriptApprover([SKIP])
    ex = RecExecutor()
    risky = lambda c: Assessment("confirm", ("sends a message",))
    p = FakePlanner([CLICK % (1, 1, "Send"), DONE])
    r = mk(p, executor=ex, approver=ap, risk=risky,
           cfg=LoopConfig(settle_s=0, mode="auto")).run("t")
    assert ap.requests[0].risky and ap.requests[0].reasons == ("sends a message",)
    assert ex.calls == [] and r.status == "done"


def test_task_mode_requires_plan_approval_once():
    ap = ScriptApprover([APPROVE])
    p = FakePlanner([CLICK % (1, 1, "x"), DONE], plan=["do x"])
    r = mk(p, approver=ap, cfg=LoopConfig(settle_s=0, mode="task")).run("t")
    assert r.status == "done"
    assert [q.kind for q in ap.requests] == ["plan"] and ap.requests[0].plan == ("do x",)
    r = mk(FakePlanner([DONE], plan=["x"]), approver=ScriptApprover([SKIP]),
           cfg=LoopConfig(settle_s=0, mode="task")).run("t")
    assert r.status == "stopped"


def test_stale_screen_after_approval_replans_instead_of_clicking():
    ex = RecExecutor()
    # screen level changes drastically between observe and post-approval observe
    scr = FakeScreen([10, 250, 250, 250, 250, 250])
    p = FakePlanner([CLICK % (1, 1, "x"), DONE])
    r = mk(p, screen=scr, executor=ex, approver=ScriptApprover([APPROVE])).run("t")
    assert ex.calls == [] and "screen changed" in p.seen[1][0][0]["outcome"]


def test_unknown_element_is_reported_not_clicked():
    ex = RecExecutor()
    p = FakePlanner(['{"action":"click","element":99}', DONE])
    r = mk(p, executor=ex, cfg=LoopConfig(settle_s=0, mode="auto")).run("t")
    assert ex.calls == [] and "no such element" in p.seen[1][0][0]["outcome"]


def test_element_click_uses_element_center_and_name_as_label():
    ex = RecExecutor()
    scr = FakeScreen([10, 200, 10, 200], elements=[el(3, "Save", (200, 100, 300, 140))])
    p = FakePlanner(['{"action":"click","element":3,"label":"lol"}', DONE])
    seen = []
    mk(p, screen=scr, executor=ex, risk=lambda c: (seen.append(c.label) or Assessment()),
       cfg=LoopConfig(settle_s=0, mode="auto")).run("t")
    assert ex.calls[0][1] == (250, 120) and seen == ["Save"]


def test_pixel_click_label_comes_from_element_under_point():
    scr = FakeScreen([10, 200, 10, 200], elements=[el(1, "Delete all", (0, 0, 400, 400))])
    seen = []
    p = FakePlanner([CLICK % (10, 10, "Harmless"), DONE])
    mk(p, screen=scr, risk=lambda c: (seen.append(c.label) or Assessment()),
       cfg=LoopConfig(settle_s=0, mode="auto")).run("t")
    assert seen == ["Delete all"]      # UIA name beats the model's own label


def test_clicking_the_agents_own_hud_is_refused():
    ex = RecExecutor()
    ui = RecordingUI()
    ui.rects = [(0, 0, 500, 500)]
    p = FakePlanner([CLICK % (10, 10, "Approve"), CLICK % (20, 20, "Approve")])
    r = mk(p, executor=ex, ui=ui, cfg=LoopConfig(settle_s=0, mode="auto", max_invalid=2)).run("t")
    assert ex.calls == [] and r.status == "failed"


def test_hud_is_masked_in_every_observation():
    ui = RecordingUI()
    ui.rects = [(1, 2, 3, 4)]
    scr = FakeScreen([10, 200, 10])
    mk(FakePlanner([CLICK % (900, 800, "x"), DONE]), screen=scr, ui=ui,
       cfg=LoopConfig(settle_s=0, mode="auto")).run("t")
    assert scr.observed_masks and all(m == [(1, 2, 3, 4)] for m in scr.observed_masks)


def test_panic_before_and_during_run():
    panic = PanicSwitch()
    panic.stop("hotkey")
    ex = RecExecutor()
    r = mk(FakePlanner([CLICK % (1, 1, "x")]), executor=ex, panic=panic).run("t")
    assert r.status == "stopped" and ex.calls == []

    panic2 = PanicSwitch()
    ex2 = RecExecutor(on_perform=lambda a: panic2.stop("hit panic after first click"))
    p = FakePlanner([CLICK % (1, 1, "x"), CLICK % (2, 2, "y")])
    r = mk(p, executor=ex2, panic=panic2, screen=FakeScreen([100]), approver=ScriptApprover([APPROVE])).run("t")
    assert r.status == "stopped" and len(ex2.calls) == 1


def test_user_grabbing_mouse_pauses_and_resume_replans():
    pos = [(500, 500)]
    act = ActivityMonitor(lambda: pos[0])
    act.expect((500, 500))
    pos[0] = (900, 100)          # user moved
    ap = ScriptApprover([APPROVE])
    ex = RecExecutor()
    p = FakePlanner([CLICK % (1, 1, "x"), DONE])
    r = mk(p, executor=ex, approver=ap, activity=act,
           cfg=LoopConfig(settle_s=0, mode="auto")).run("t")
    assert "Resume" in ap.requests[0].summary
    assert r.status == "done"    # resumed, then proceeded


def test_user_grabbing_mouse_and_declining_resume_stops():
    pos = [(0, 0)]
    act = ActivityMonitor(lambda: pos[0])
    act.expect((500, 500))
    ex = RecExecutor()
    r = mk(FakePlanner([CLICK % (1, 1, "x")]), executor=ex, approver=ScriptApprover([SKIP]),
           activity=act, cfg=LoopConfig(settle_s=0, mode="auto")).run("t")
    assert r.status == "stopped" and ex.calls == []


def test_quota_saves_resumable_state(tmp_path):
    store = StateStore(tmp_path)
    p = FakePlanner([CLICK % (1, 1, "x"), QuotaExhausted(300, "gemini 429")])
    r = mk(p, store=store, cfg=LoopConfig(settle_s=0, mode="auto"),
           screen=FakeScreen([10, 200, 10])).run("rename the file")
    assert r.status == "quota" and r.resumable and "resume" in r.message.lower()
    saved = store.load()
    assert saved.task == "rename the file" and saved.step == 1 and saved.history
    # completes, state cleared
    r2 = mk(FakePlanner([DONE]), store=store, cfg=LoopConfig(settle_s=0, mode="auto")).run("x", resume=saved)
    assert r2.status == "done" and store.load() is None


def test_planner_error_and_internal_error_do_not_hang_ui():
    ui = RecordingUI()
    r = mk(FakePlanner([PlannerError("bad json")]), ui=ui).run("t")
    assert r.status == "failed" and ui.events[-1][0] == "finished"
    ui2 = RecordingUI()
    r = mk(FakePlanner([RuntimeError("kaboom")]), ui=ui2).run("t")
    assert r.status == "failed" and "kaboom" in r.message and ui2.events[-1][0] == "finished"


def test_preview_cleared_before_each_observation():
    ui = RecordingUI()
    mk(FakePlanner([DONE]), ui=ui).run("t")
    assert ui.events[0] == ("preview", None, "")
