import pytest

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
app_mod = pytest.importorskip("arrow_assistant.app", exc_type=ImportError)


class StubAgent:
    def __init__(self):
        self.calls = []

    def stop(self): self.calls.append("stop")
    def start_task(self, task, resume=False): self.calls.append(("task", task, resume))


def make():
    a = app_mod.ArrowApp.__new__(app_mod.ArrowApp)
    a.agent = StubAgent()
    return a


def test_phrase_routes_to_agent_task():
    a = make()
    a.handle_agent_text("Arrow agent, open notepad")
    assert a.agent.calls == [("task", "open notepad", False)]


def test_agent_hotkey_utterance_is_the_whole_task():
    a = make()
    a.handle_agent_text("open notepad and type hello")
    assert a.agent.calls == [("task", "open notepad and type hello", False)]


def test_stop_and_continue_phrases():
    a = make()
    a.handle_agent_text("arrow agent stop")
    a.handle_agent_text("arrow agent ituloy")
    assert a.agent.calls == ["stop", ("task", "", True)]


def test_empty_transcript_does_nothing():
    a = make()
    a.handle_agent_text("")
    assert a.agent.calls == []
