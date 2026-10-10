"""Additional regressions: unknown UIA, approval races, and mid-input changes."""
import pytest
from .test_pr_safety_fixes import LiveScreen, run, DONE, Backend
from .fakes import el, ScriptApprover
from arrow_assistant.agent.actions import parse_action
from arrow_assistant.agent.executor import RealExecutor, ActionAborted
from arrow_assistant.agent.panic import PanicSwitch
from arrow_assistant.agent.screen import WindowsScreen

TYPE = '{"action":"type","text":"abcdefghijklSECRET"}'

@pytest.mark.parametrize('query', ['focused_element', 'foreground'])
@pytest.mark.parametrize('failure', ['none', 'exception'])
def test_unknown_focus_cannot_type(query, failure):
    scr = LiveScreen([10])
    def bad():
        if failure == 'exception':
            raise RuntimeError('UIA failed')
        return None
    setattr(scr, query, bad)
    res, ex, _ = run(scr, [TYPE, DONE])
    assert ex.calls == []


def test_real_screen_without_uia_requires_manual_input():
    assert WindowsScreen.require_verified_focus
    assert WindowsScreen().focused_element() is None
    scr = LiveScreen([10])
    scr.require_verified_focus = True
    scr.focus = None
    res, ex, _ = run(scr, [TYPE, TYPE, TYPE])
    assert ex.calls == [] and res.status in ('failed', 'stuck')

@pytest.mark.parametrize('name', ['Verification code', 'PIN', 'Card number', 'API key'])
def test_final_live_field_risk_is_rechecked_after_approval(name):
    scr = LiveScreen([10], app='chrome.exe', title='Sign in')
    class Approver(ScriptApprover):
        def request(self, req, timeout_s=0):
            scr.focus = el(2, name, role='Edit')
            return super().request(req, timeout_s)
    _, ex, _ = run(scr, [TYPE, DONE], mode='step', approver=Approver([]))
    assert ex.calls == []

@pytest.mark.parametrize('change', ['password', 'normal_field', 'window', 'uia_failure'])
def test_focus_change_during_typing_aborts_remaining_text(change):
    scr = LiveScreen([10], app='chrome.exe', title='Sign in')
    class TypingBackend(Backend):
        def type_text(self, text):
            super().type_text(text)
            if change == 'window':
                scr.fg = ('messenger.exe', 'Messenger')
            elif change == 'uia_failure':
                scr.focus = None
            else:
                scr.focus = el(2, 'New field', role='Edit', password=change == 'password')
    b = TypingBackend()
    executor = RealExecutor(b, PanicSwitch(), sleep=lambda _: None)
    _, _, loop = run(scr, [TYPE, DONE], executor=executor)
    assert b.calls == [('type', 'a')]
    assert any('aborted for safety' in h['outcome'] for h in loop.planner.seen[-1][0])


def test_successful_guarded_typing_preserves_text():
    scr = LiveScreen([10])
    b = Backend()
    executor = RealExecutor(b, PanicSwitch(), sleep=lambda _: None)
    run(scr, [TYPE, DONE], executor=executor)
    assert ''.join(c[1] for c in b.calls) == 'abcdefghijklSECRET'


def test_executor_cannot_type_or_press_key_without_bound_guard():
    b = Backend()
    e = RealExecutor(b, PanicSwitch())
    for raw in [TYPE, '{"action":"key","keys":["enter"]}']:
        with pytest.raises(ActionAborted, match='not been verified'):
            e.perform(parse_action(raw), None, None)
    assert b.calls == []


def test_start_focus_loss_during_name_stops_remaining_characters_and_enter():
    b = Backend()
    e = RealExecutor(b, PanicSwitch(), sleep=lambda _: None,
        foreground=lambda: 'messenger.exe' if b.calls else 'searchhost.exe')
    with pytest.raises(ActionAborted):
        e.perform(parse_action('{"action":"open_app","app":"notepad"}'), None, None)
    assert b.calls == [('type', 'n')]


def test_uia_runtime_identity_detects_same_looking_field_switch():
    scr = LiveScreen([10])
    scr.focus = el(0, 'Text', role='Edit', runtime_id=(1, 2))
    class B(Backend):
        def type_text(self, text):
            super().type_text(text)
            scr.focus = el(0, 'Text', role='Edit', runtime_id=(1, 3))
    b = B()
    run(scr, [TYPE, DONE], executor=RealExecutor(b, PanicSwitch()))
    assert b.calls == [('type', 'a')]


def test_zero_hwnd_and_unknown_app_fail_closed():
    for fg in [('notepad.exe', 'Untitled', 0), ('unknown', 'Untitled', 42)]:
        scr = LiveScreen([10])
        scr.fg = fg
        _, ex, _ = run(scr, [TYPE, DONE])
        assert ex.calls == []
