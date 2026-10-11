import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class Sig:
    def __init__(self):
        self.got = []

    def emit(self, v=None):
        self.got.append(v)


def _app():
    pytest.importorskip("PyQt6.QtWidgets")
    from arrow_assistant import app as app_mod
    return app_mod, app_mod.ArrowApp.__new__(app_mod.ArrowApp)


def test_usage_alert_goes_to_tray_and_voice_once(_isolated_usage, monkeypatch):
    app_mod, a = _app()
    a.usage_alert = Sig()
    spoken = []
    a._speak_async = spoken.append
    monkeypatch.setenv("ARROW_DAILY_REQUEST_WARN_GEMINI", "5")
    _isolated_usage.set_notifier(a._on_usage_alert)
    for _ in range(6):
        _isolated_usage.record("gemini", "m")
    assert len(a.usage_alert.got) == 2                 # 80% and 100%
    assert spoken[0] == "Heads up: malapit na sa daily limit ang Gemini."


def test_pipeline_reports_hard_limit_clearly(_isolated_usage, monkeypatch):
    app_mod, a = _app()
    from arrow_assistant.config import LLMProvider
    monkeypatch.setenv("ARROW_DAILY_REQUEST_LIMIT_GEMINI", "1")
    _isolated_usage.record("gemini", "m")
    a.provider = LLMProvider("gemini", "k", "gemini-3.8-flash")
    a.stt_provider = "local"
    a.status, a.error = Sig(), Sig()
    a._speaker = None

    class Sp:
        def start(self): pass
        def say(self, s): pass
        def finish(self): pass
        def stop(self): pass
        def wait(self): pass

    class Mem:
        def recall(self, app): return ""
        def record(self, *x): pass

    from PIL import Image
    monkeypatch.setattr(app_mod, "Speaker", Sp)
    monkeypatch.setattr(app_mod, "transcribe", lambda *x: "nasaan ang File?")
    monkeypatch.setattr(app_mod.config, "get_key", lambda n: None)
    monkeypatch.setattr(app_mod.capture, "foreground_app", lambda: "notepad.exe")
    mon = {"left": 0, "top": 0, "width": 100, "height": 100}
    monkeypatch.setattr(app_mod.capture, "primary_shot", lambda: (mon, Image.new("RGB", (100, 100))))
    monkeypatch.setattr(app_mod.kb, "lookup", lambda app: None)
    monkeypatch.setattr(app_mod, "log_error", lambda m: None)
    a.memory = Mem()
    a._pipeline(b"WAV", threading.Event())
    assert a.error.got and "daily request limit reached" in a.error.got[0]
    assert "pipeline error" not in a.error.got[0]
