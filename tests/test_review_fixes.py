"""Regression tests for the code-review findings (speech tags, DPI,
interrupt, press length, monitor pick, whisper cache)."""
import os
import threading

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from arrow_assistant import ai, capture, stt, tts
from arrow_assistant.points import Point, SpeechFilter, parse_points
from arrow_assistant.sentences import SentenceStreamer


# -- 1. POINT tags are never spoken ------------------------------------------
def _run(chunks):
    f, st = SpeechFilter(), SentenceStreamer()
    spoken, points = [], []
    for c in chunks:
        t, p = f.feed(c)
        points += p
        spoken += st.feed(t)
    spoken += st.feed(f.flush())
    spoken += st.flush()
    return spoken, points


def test_tag_removed_from_speech_whole_chunk():
    spoken, pts = _run(["Click the File menu. Then pick Export. "
                        "[POINT:812,340:Export button]"])
    assert spoken == ["Click the File menu.", "Then pick Export."]
    assert pts == [Point(812, 340, "Export button")]


@pytest.mark.parametrize("cut", range(1, 36))
def test_tag_split_at_every_position(cut):
    text = "Pick Export. [POINT:812,340:Export button]"
    spoken, pts = _run([text[:cut], text[cut:]])
    assert spoken == ["Pick Export."]
    assert pts == [Point(812, 340, "Export button")]


def test_unfinished_tag_dropped_and_plain_brackets_kept():
    spoken, pts = _run(["Use [Ctrl] and save. [POINT:5,6:"])
    assert spoken == ["Use [Ctrl] and save."]
    assert pts == []


def test_max_three_points():
    tags = "".join(f"[POINT:{i},{i}:a]" for i in range(5))
    _, pts = _run(["ok. " + tags])
    assert len(pts) == 3


# -- DPI mapping ---------------------------------------------------------------
def test_physical_to_local_divides_by_dpr():
    pytest.importorskip("PyQt6")
    from arrow_assistant.overlay import physical_to_local
    assert physical_to_local(1500, 900, 0, 0, 1.5) == (1000.0, 600.0)
    assert physical_to_local(2000, 100, 1920, 0, 1.0) == (80.0, 100.0)
    assert physical_to_local(10, 10, 0, 0, 0) == (10.0, 10.0)  # bad dpr -> 1


def test_label_box_stays_on_screen():
    pytest.importorskip("PyQt6")
    from arrow_assistant.overlay import clamp_label_box
    bx, by = clamp_label_box(1900, 1060, 120, 30, 1872, 1030, 1920, 1080)
    assert bx + 120 <= 1916 and by + 30 <= 1076
    assert clamp_label_box(100, 100, 120, 30, 72, 70, 1920, 1080) == (100, 100)


# -- prompt knows the screenshot size -------------------------------------------
def test_prompt_includes_screenshot_size():
    parts = ai.build_user_content("q", "a", "", None, True, "QUJD", (1568, 882))
    assert "1568x882" in parts[0]["text"]


def test_encoded_size_matches_resize():
    from PIL import Image
    img = Image.new("RGB", (3840, 2160))
    assert capture.encoded_size(img) == (1568, 882)
    assert capture.encoded_size(Image.new("RGB", (800, 600))) == (800, 600)


# -- monitor selection -----------------------------------------------------------
def test_pick_monitor():
    mons = [{"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080}]
    assert capture.pick_monitor(mons, 2000, 10) is mons[1]
    assert capture.pick_monitor(mons, 10, 10) is mons[0]
    assert capture.pick_monitor(mons, -50, 5000) is mons[0]


# -- min press duration ----------------------------------------------------------
def test_short_tap_is_dropped_and_long_press_kept():
    r = stt.MicRecorder()
    r._chunks = [np.zeros((1600, 1), dtype=np.int16)]      # 0.1 s
    assert r.stop() == b""
    r._chunks = [np.zeros((16000, 1), dtype=np.int16)]     # 1 s
    assert r.stop().startswith(b"RIFF")


# -- local whisper loaded once ---------------------------------------------------
def test_whisper_loaded_once(monkeypatch):
    import sys, types
    loads = []

    class FakeModel:
        def __init__(self, *a, **k):
            loads.append(1)

        def transcribe(self, f):
            return [types.SimpleNamespace(text=" hi ")], None

    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(stt, "_whisper_model", None)
    assert stt._transcribe_local(b"x") == "hi"
    assert stt._transcribe_local(b"x") == "hi"
    assert len(loads) == 1


# -- Speaker.stop() ends the worker even mid-wait --------------------------------
def test_speaker_stop_ends_thread(monkeypatch):
    monkeypatch.setattr(tts, "synthesize_edge", lambda t, v: b"MP3")
    release = threading.Event()
    monkeypatch.setattr(tts, "play_mp3",
                        lambda audio, stop: stop.wait(5) if not release.is_set() else None)
    s = tts.Speaker()
    s.start()
    s.say("One.")
    threading.Event().wait(0.1)
    s.stop()          # no finish() ever called
    s._thread.join(timeout=3)
    assert not s._thread.is_alive()


# -- app: new press interrupts speech; newer question is not swallowed ------------
def test_new_press_stops_speaker_and_new_question_runs():
    pytest.importorskip("PyQt6.QtWidgets")
    from arrow_assistant import app as app_mod

    a = app_mod.ArrowApp.__new__(app_mod.ArrowApp)
    stops, started = [], []

    class Sp:
        def stop(self): stops.append(1)

    class Rec:
        def start(self): pass
        def stop(self): return b"WAV"

    class Sig:
        def emit(self, *a): pass

    a._cancel = threading.Event()
    a._speaker = Sp()
    a.recorder = Rec()
    a.status = a.error = Sig()
    old = a._cancel
    a._pipeline = lambda wav, cancel: started.append((wav, cancel))
    a._ptt_down()
    assert old.is_set() and stops == [1]            # old answer cancelled + audio cut
    a._ptt_up()                                      # even though an old pipeline may be running
    threading.Event().wait(0.1)
    assert len(started) == 1 and started[0][1] is a._cancel and not a._cancel.is_set()


def test_pipeline_streams_arrow_early_and_never_speaks_tag(monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from PIL import Image
    from arrow_assistant import app as app_mod

    a = app_mod.ArrowApp.__new__(app_mod.ArrowApp)
    said, shown, order = [], [], []

    class Sp:
        def start(self): pass
        def say(self, s): said.append(s); order.append("say")
        def finish(self): pass
        def stop(self): pass
        def wait(self): pass

    class Sig:
        def __init__(self, sink=None): self.sink = sink
        def emit(self, v=None):
            if self.sink is not None:
                self.sink.append(v); order.append("arrow")

    class Mem:
        def recall(self, app): return ""
        def record(self, *a): self.rec = a

    monkeypatch.setattr(app_mod, "Speaker", Sp)
    monkeypatch.setattr(app_mod, "transcribe", lambda *a: "saan export?")
    monkeypatch.setattr(app_mod.config, "get_key", lambda n: None)
    monkeypatch.setattr(app_mod.capture, "foreground_app", lambda: "excel.exe")
    mon = {"left": 0, "top": 0, "width": 1000, "height": 500}
    monkeypatch.setattr(app_mod.capture, "primary_shot",
                        lambda: (mon, Image.new("RGB", (1000, 500))))
    monkeypatch.setattr(app_mod.kb, "lookup", lambda app: None)
    chunks = ["Click File. Then ", "pick Export. [POI", "NT:99999,20:Export]", " Done."]
    monkeypatch.setattr(app_mod.ai, "stream_answer",
                        lambda *a, **k: iter(chunks))
    a.provider = object()
    a.stt_provider = "groq"
    a.memory = Mem()
    a.show_points = Sig(shown)
    a.status = a.error = Sig()
    a._speaker = None
    a._pipeline(b"WAV", threading.Event())
    assert all("POINT" not in s and "[" not in s for s in said)
    assert said == ["Click File.", "Then pick Export.", "Done."]
    assert len(shown) == 1 and shown[0][0].label == "Export"
    assert shown[0][0].x == 999            # clamped inside the screenshot
    # the arrow is emitted mid-stream, before the last sentence is spoken
    assert order.index("arrow") < len(order) - 1
    assert "POINT" not in a.memory.rec[2]


# -- second review ---------------------------------------------------------------
def test_malformed_or_lowercase_tags_are_never_spoken():
    spoken, pts = _run(["Click Save. [POINT:10,20]", " Ok."])
    assert spoken == ["Click Save.", "Ok."] and pts == []
    spoken, pts = _run(["Click Save. [poi", "nt:10,20:Save] Ok."])
    assert spoken == ["Click Save.", "Ok."] and pts == [Point(10, 20, "Save")]
    s, p = parse_points("Try [point:1,2:x] and [POINT:abc]")
    assert "POINT" not in s.upper() and p == [Point(1, 2, "x")]


def test_superseded_pipeline_does_not_clobber_newer_speaker(monkeypatch):
    """An old answer that finishes late must not drop the reference to the
    newer answer's speaker (or the newer one can never be interrupted), and
    must not flip the tray back to idle while the newer one is thinking."""
    pytest.importorskip("PyQt6.QtWidgets")
    from PIL import Image
    from arrow_assistant import app as app_mod

    a = app_mod.ArrowApp.__new__(app_mod.ArrowApp)
    statuses = []
    gate, streaming = threading.Event(), threading.Event()

    class Sp:
        def start(self): pass
        def say(self, s): pass
        def finish(self): pass
        def stop(self): pass
        def wait(self): pass

    class Sig:
        def __init__(self, sink=None): self.sink = sink
        def emit(self, v=None):
            if self.sink is not None:
                self.sink.append(v)

    class Mem:
        def recall(self, app): return ""
        def record(self, *a): pass

    def slow_stream(*a, **k):
        streaming.set()
        gate.wait(5)            # network is slow...
        yield "Late answer."

    monkeypatch.setattr(app_mod, "Speaker", Sp)
    monkeypatch.setattr(app_mod, "transcribe", lambda *a: "q")
    monkeypatch.setattr(app_mod.config, "get_key", lambda n: None)
    monkeypatch.setattr(app_mod.capture, "foreground_app", lambda: "x.exe")
    mon = {"left": 0, "top": 0, "width": 100, "height": 100}
    monkeypatch.setattr(app_mod.capture, "primary_shot",
                        lambda: (mon, Image.new("RGB", (100, 100))))
    monkeypatch.setattr(app_mod.kb, "lookup", lambda app: None)
    monkeypatch.setattr(app_mod.ai, "stream_answer", slow_stream)
    a.provider = object()
    a.stt_provider = "groq"
    a.memory = Mem()
    a.show_points = Sig()
    a.error = Sig()
    a.status = Sig(statuses)
    a._speaker = None

    old_cancel = threading.Event()
    t = threading.Thread(target=a._pipeline, args=(b"WAV", old_cancel))
    t.start()
    assert streaming.wait(3)
    old_cancel.set()            # user asked again...
    newer = Sp()
    a._speaker = newer          # ...and the newer answer is already speaking
    gate.set()
    t.join(3)
    assert a._speaker is newer
    assert "idle" not in statuses
