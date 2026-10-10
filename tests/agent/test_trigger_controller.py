import threading
import time

import pytest

from arrow_assistant.agent.controller import AgentController
from arrow_assistant.agent.hotkeys import MultiCombo
from arrow_assistant.agent.loop import AgentLoop, AgentResult, LoopConfig
from arrow_assistant.agent.panic import PanicSwitch
from arrow_assistant.agent.state import StateStore, TaskState
from arrow_assistant.agent.trigger import AgentCommand, parse_agent_request
from arrow_assistant.agent.ui import RecordingUI

from .fakes import FakePlanner, FakeScreen, RecExecutor, ScriptApprover


@pytest.mark.parametrize("text,expected", [
    ("Arrow agent, open notepad and type hello", AgentCommand("task", "open notepad and type hello")),
    ("arrow agent open chrome", AgentCommand("task", "open chrome")),
    ("Hey Arrow, agent: rename the file.", AgentCommand("task", "rename the file")),
    ("ok arrow agent mode gawin mo yung report", AgentCommand("task", "gawin mo yung report")),
    ("Arrow agent stop", AgentCommand("stop")),
    ("arrow agent, itigil mo", AgentCommand("stop")),
    ("Arrow agent ituloy", AgentCommand("continue")),
    ("arrow agent resume", AgentCommand("continue")),
])
def test_trigger_parsing(text, expected):
    assert parse_agent_request(text) == expected


@pytest.mark.parametrize("text", [
    "", "where do I click to export?", "arrow, saan yung save button", "my agent arrow",
    "agent arrow open it", "arrow agent", "arrow agent,  ",
])
def test_non_agent_speech_is_not_hijacked(text):
    assert parse_agent_request(text) is None


def test_trigger_caps_task_length():
    cmd = parse_agent_request("arrow agent " + "x" * 2000)
    assert len(cmd.task) == 600


def test_multicombo_press_release_tuples():
    ev = []
    m = MultiCombo({"ctrl+alt+a": (lambda: ev.append("down"), lambda: ev.append("up")),
                    "ctrl+alt+y": lambda: ev.append("y")})
    for t in ("ctrl", "alt", "a"):
        m.press(t)
    m.release("a")
    assert ev == ["down", "up"]


def _controller(tmp_path, planner, done=None, ui=None):
    panic = PanicSwitch()
    store = StateStore(tmp_path)

    def factory():
        return AgentLoop(FakeScreen([100]), RecExecutor(), planner, ScriptApprover([]),
                         ui=ui or RecordingUI(), panic=panic, store=store,
                         config=LoopConfig(settle_s=0, mode="auto"), sleep=lambda s: None)
    results = []
    c = AgentController(factory, panic, store, on_finish=results.append)
    return c, results, panic, store


def test_controller_runs_one_task_at_a_time(tmp_path):
    gate = threading.Event()

    class Slow(FakePlanner):
        def next_action(self, *a, **k):
            gate.wait(2)
            return super().next_action(*a, **k)

    c, results, *_ = _controller(tmp_path, Slow(['{"action":"done","message":"ok"}']))
    assert c.start("t1") is True and c.running
    assert c.start("t2") is False
    gate.set()
    c.join(3)
    assert results[0].status == "done" and not c.running
    assert c.start("t3") is True   # free again
    c.join(3)


def test_stop_from_another_thread_ends_the_task(tmp_path):
    started = threading.Event()

    class Waiting(FakePlanner):
        def next_action(self, *a, **k):
            started.set()
            time.sleep(0.1)
            return super().next_action(*a, **k)

    c, results, panic, _ = _controller(
        tmp_path, Waiting(['{"action":"click","x":1,"y":1}'] * 5))
    c.start("t")
    started.wait(2)
    c.stop()
    c.join(3)
    assert results[0].status == "stopped"


def test_resume_requires_saved_state_and_continues(tmp_path):
    c, results, _, store = _controller(tmp_path, FakePlanner(['{"action":"done","message":"ok"}']))
    assert c.start("", resume=True) is False          # nothing saved
    store.save(TaskState(task="old task", step=4, max_steps=4))
    assert c.start("", resume=True) is True
    c.join(3)
    assert results[0].status == "done" and results[0].steps == 5


def test_controller_survives_factory_crash(tmp_path):
    panic = PanicSwitch()
    results = []
    c = AgentController(lambda: (_ for _ in ()).throw(RuntimeError("no display")), panic,
                        on_finish=results.append)
    c.start("t")
    c.join(3)
    assert results[0].status == "failed" and "no display" in results[0].message
