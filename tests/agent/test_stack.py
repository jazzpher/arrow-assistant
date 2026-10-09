import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from arrow_assistant.agent.executor import DryRunExecutor  # noqa: E402
from arrow_assistant.agent.stack import AgentStack  # noqa: E402
from arrow_assistant.config import LLMProvider  # noqa: E402

from .fakes import FakeScreen  # noqa: E402


class FakeOverlay:
    def point_at(self, pts): pass


def make(qapp, providers, tmp_path, monkeypatch):
    monkeypatch.setenv("ARROW_LOG_DIR", str(tmp_path))
    return AgentStack(qapp, FakeOverlay(), dry_run=True, providers=providers,
                      screen=FakeScreen([100]), executor_factory=DryRunExecutor)


def test_stack_without_keys_refuses_politely(tmp_path, monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    st = make(qapp, [], tmp_path, monkeypatch)
    assert not st.ready() and st.start_task("anything") is False


def test_stack_builds_loop_with_safety_wired(tmp_path, monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    st = make(qapp, [LoopProv()], tmp_path, monkeypatch)
    loop = st._make_loop()
    from arrow_assistant.agent.risk import assess, is_sensitive_window
    assert loop.risk is assess and loop.sensitive is is_sensitive_window
    assert loop.cfg.mode == "step" and loop.cfg.max_steps == 25
    assert loop.activity is not None


def test_stack_hotkey_actions_reach_the_approver(tmp_path, monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    st = make(qapp, [LoopProv()], tmp_path, monkeypatch)
    from arrow_assistant.agent.approval import ApprovalRequest
    st.approver.pending = ApprovalRequest("action", "x")
    st.approve()
    assert st.approver._q.get_nowait() == "approve"
    st.approver.pending = ApprovalRequest("action", "x")
    st.stop()
    assert st.panic.is_stopped and st.approver._q.get_nowait() == "stop"


def LoopProv():
    return LLMProvider("gemini", "K", "gemini-2.5-flash")
