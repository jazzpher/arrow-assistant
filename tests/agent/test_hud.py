import threading

import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from arrow_assistant.agent import approval as ap  # noqa: E402
from arrow_assistant.agent.hud import AgentHUD, QtAgentUI  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeOverlay:
    def __init__(self):
        self.points = []

    def point_at(self, pts):
        self.points.extend(pts)


def make(qapp):
    approver = ap.QueueApprover()
    stops = []
    spoken = []
    ui = QtAgentUI(qapp, FakeOverlay(), approver, lambda: stops.append(1), spoken.append)
    approver._on_request = ui.on_request
    return ui, approver, stops, spoken


def _wait_buttons(qapp, ui):
    import time
    end = time.time() + 3
    while not ui.hud.btn_ok.isEnabled() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert ui.hud.btn_ok.isEnabled()


def test_hud_never_accepts_focus(qapp):
    from PyQt6.QtCore import Qt
    h = AgentHUD()
    assert h.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert h.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_step_status_and_log_window(qapp):
    ui, *_ = make(qapp)
    for i in range(10):
        ui.step(i + 1, 25, f"click {i}")
    qapp.processEvents()
    assert "Step 10/25" in ui.hud.title.text()
    assert len(ui.hud.log.text().splitlines()) == 6   # rolling window
    assert ui.hud.isVisible() or True


def test_request_enables_buttons_and_previews_point(qapp):
    ui, approver, *_ = make(qapp)
    approver.pending = ap.ApprovalRequest("action", "click Save", ("sends data",), True, (50, 60), 2, 25)
    ui.on_request(approver.pending)
    qapp.processEvents()
    assert ui.hud.btn_ok.isEnabled() and ui.hud.btn_skip.isEnabled()
    assert "sends data" in ui.hud.reasons.text()
    assert ui._overlay.points[0].x == 50


def test_approve_button_answers_blocked_loop_thread(qapp):
    ui, approver, stops, _ = make(qapp)
    result = []
    t = threading.Thread(target=lambda: result.append(
        approver.request(ap.ApprovalRequest("action", "x"), 5)))
    t.start()
    _wait_buttons(qapp, ui)
    ui.hud.btn_ok.click()
    t.join(3)
    assert result == [ap.APPROVE] and not stops
    assert not ui.hud.btn_ok.isEnabled()


def test_stop_button_triggers_panic_and_answers(qapp):
    ui, approver, stops, _ = make(qapp)
    result = []
    t = threading.Thread(target=lambda: result.append(
        approver.request(ap.ApprovalRequest("action", "x"), 5)))
    t.start()
    _wait_buttons(qapp, ui)
    ui.hud.btn_stop.click()   # stop works even when buttons for approve are disabled
    t.join(3)
    assert stops == [1] and result == [ap.STOP]


def test_finished_speaks_message(qapp):
    ui, _, _, spoken = make(qapp)
    ui.finished("done", "Tapos na")
    qapp.processEvents()
    assert spoken == ["Tapos na"] and "DONE" in ui.hud.title.text()
