"""Per-provider pointing adapters: model POINT -> screen pixel, sanity-checked."""
from __future__ import annotations

import threading

import pytest

from arrow_assistant import ai
from arrow_assistant import point_adapter as pa
from arrow_assistant.config import LLMProvider
from arrow_assistant.points import Point, to_image_px

GEM = LLMProvider("gemini", "k", "gemini-3.8-flash")
OR = LLMProvider("openrouter", "k", "m", base_url="https://x")
NV = LLMProvider("nvidia", "k", "m", base_url="https://y")


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv(pa.ENV_OVERRIDE, raising=False)


# -- which space each provider answers in ----------------------------------------
def test_default_space_per_provider():
    assert pa.adapter_for(GEM).space == "norm1000"
    assert pa.adapter_for(OR).space == "pixels"
    assert pa.adapter_for(NV).space == "pixels"
    assert pa.adapter_for(None).space == "pixels"
    assert pa.adapter_for(object()).space == "pixels"


def test_ai_point_space_reads_the_adapter():
    for p in (GEM, OR, NV, None):
        assert ai.point_space(p) == pa.adapter_for(p).space


def test_env_override_switches_space_and_prompt(monkeypatch):
    monkeypatch.setenv(pa.ENV_OVERRIDE, "pixels")
    assert ai.point_space(GEM) == "pixels"
    assert ai.system_prompt(GEM) == ai.SYSTEM_PROMPT
    monkeypatch.setenv(pa.ENV_OVERRIDE, " NORM1000 ")
    assert ai.point_space(OR) == "norm1000"
    assert ai.system_prompt(OR) == ai.GEMINI_SYSTEM_PROMPT


def test_env_override_ignores_junk(monkeypatch):
    monkeypatch.setenv(pa.ENV_OVERRIDE, "inches")
    assert pa.adapter_for(GEM).space == "norm1000"
    assert pa.adapter_for(OR).space == "pixels"


def test_unknown_space_is_refused():
    with pytest.raises(ValueError):
        pa.PointAdapter("inches")


# -- model coords -> screenshot pixels ---------------------------------------------
def test_norm1000_maps_grid_to_image():
    a = pa.adapter_for(GEM)
    assert a.to_image(0, 0, 1568, 882) == (0, 0)
    assert a.to_image(500, 500, 1568, 882) == (784, 441)
    assert a.to_image(1000, 1000, 1568, 882) == (1567, 881)


def test_norm1000_matches_e2e_notepad():
    # real Gemini answer on a 1024x768 shot: File menu rect (48,71)-(80,90)
    x, y = pa.adapter_for(GEM).to_image(63, 103, 1024, 768)
    assert 48 <= x <= 80 and 71 <= y <= 90


def test_pixels_pass_through():
    assert pa.adapter_for(OR).to_image(812, 340, 1568, 882) == (812, 340)
    assert pa.adapter_for(NV).to_image(812, 340, 1568, 882) == (812, 340)


@pytest.mark.parametrize("prov", [GEM, OR])
def test_in_range_agrees_with_to_image_px(prov):
    a = pa.adapter_for(prov)
    for x in (0, 1, 250, 777, 999):
        for y in (0, 3, 500, 881):
            assert a.to_image(x, y, 1568, 882) == to_image_px(x, y, a.space, 1568, 882)


def test_small_overshoot_is_clamped():
    assert pa.adapter_for(OR).to_image(1600, -20, 1568, 882) == (1567, 0)
    assert pa.adapter_for(GEM).to_image(1040, -30, 1000, 500) == (999, 0)


@pytest.mark.parametrize("x,y", [(99999, 20), (20, 99999), (-500, 10), (10, -500)])
def test_absurd_points_are_rejected(x, y):
    assert pa.adapter_for(OR).to_image(x, y, 1568, 882) is None
    assert pa.adapter_for(GEM).to_image(x, y, 1568, 882) is None


def test_pixel_answer_from_gemini_is_rejected_not_misdrawn():
    # Gemini answering in screenshot pixels (say, after a model update):
    # 1400 is way past the 0-1000 grid, so no arrow instead of one at the edge
    assert pa.adapter_for(GEM).to_image(1400, 300, 1568, 882) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, "abc"])
def test_non_numbers_are_rejected(bad):
    assert pa.adapter_for(OR).to_image(bad, 10, 100, 100) is None
    assert pa.adapter_for(GEM).to_image(10, bad, 100, 100) is None


def test_empty_image_is_rejected():
    assert pa.adapter_for(OR).to_image(1, 1, 0, 10) is None
    assert pa.adapter_for(GEM).to_image(1, 1, 10, 0) is None


# -- screenshot pixels -> virtual-desktop pixels ---------------------------------
def test_to_screen_scales_back_up_to_monitor():
    # 2560x1440 monitor, the model saw it downscaled to 1568x882
    mon = {"left": 0, "top": 0, "width": 2560, "height": 1440}
    s = 1568 / 2560
    p = pa.adapter_for(GEM).to_screen(Point(500, 500, "Mid"), 1568, 882, mon, s)
    assert p == Point(1280, 720, "Mid")


def test_to_screen_on_secondary_monitor_with_negative_origin():
    mon = {"left": -1920, "top": -200, "width": 1920, "height": 1080}
    p = pa.adapter_for(OR).to_screen(Point(960, 540, "X"), 1920, 1080, mon, 1.0)
    assert p == Point(-960, 340, "X")


def test_to_screen_stays_inside_monitor():
    mon = {"left": 100, "top": 50, "width": 1000, "height": 500}
    p = pa.adapter_for(OR).to_screen(Point(1050, 520, "edge"), 1000, 500, mon, 1.0)
    assert p == Point(1099, 549, "edge")


def test_to_screen_rejects_bad_scale_and_points():
    mon = {"left": 0, "top": 0, "width": 100, "height": 100}
    a = pa.adapter_for(OR)
    assert a.to_screen(Point(10, 10, "x"), 100, 100, mon, 0) is None
    assert a.to_screen(Point(10, 10, "x"), 100, 100, mon, float("nan")) is None
    assert a.to_screen(Point(99999, 10, "x"), 100, 100, mon, 1.0) is None


def test_same_target_lands_in_same_place_for_every_provider():
    # one button at screen (1280, 720) on a 2560x1440 monitor: Gemini says it
    # on its grid, OpenRouter and NVIDIA in screenshot pixels
    mon = {"left": 0, "top": 0, "width": 2560, "height": 1440}
    s = 1568 / 2560
    g = pa.adapter_for(GEM).to_screen(Point(500, 500, "b"), 1568, 882, mon, s)
    o = pa.adapter_for(OR).to_screen(Point(784, 441, "b"), 1568, 882, mon, s)
    n = pa.adapter_for(NV).to_screen(Point(784, 441, "b"), 1568, 882, mon, s)
    assert g == o == n == Point(1280, 720, "b")


# -- the teach pipeline uses the adapter -----------------------------------------
def _run_pipeline(monkeypatch, provider, chunks, size=(1000, 500)):
    pytest.importorskip("PyQt6.QtWidgets")
    from PIL import Image
    from arrow_assistant import app as app_mod

    a = app_mod.ArrowApp.__new__(app_mod.ArrowApp)
    shown = []

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

    w, h = size
    mon = {"left": 0, "top": 0, "width": w, "height": h}
    monkeypatch.setattr(app_mod, "Speaker", Sp)
    monkeypatch.setattr(app_mod, "transcribe", lambda *a: "saan export?")
    monkeypatch.setattr(app_mod.config, "get_key", lambda n: None)
    monkeypatch.setattr(app_mod.capture, "foreground_app", lambda: "excel.exe")
    monkeypatch.setattr(app_mod.capture, "primary_shot",
                        lambda: (mon, Image.new("RGB", (w, h))))
    monkeypatch.setattr(app_mod.kb, "lookup", lambda app: None)
    monkeypatch.setattr(app_mod.ai, "stream_answer", lambda *a, **k: iter(chunks))
    diagnostics = []
    monkeypatch.setattr(app_mod, "log_diagnostic", diagnostics.append)
    a.provider = provider
    a.stt_provider = "groq"
    a.memory = Mem()
    a.show_points = Sig(shown)
    a.status = a.error = Sig()
    a._speaker = None
    a._pipeline(b"WAV", threading.Event())
    return shown, diagnostics


def test_pipeline_draws_gemini_grid_point_on_screen(monkeypatch):
    shown, diag = _run_pipeline(monkeypatch, GEM, ["Click Export. [POINT:500,500:Export]"])
    assert shown == [[Point(500, 250, "Export")]]
    assert "space=norm1000" in diag[0] and "rejected=0" in diag[0]


def test_pipeline_drops_absurd_point_and_keeps_good_one(monkeypatch):
    chunks = ["Click Export. [POINT:99999,20:Ghost]", " Then Save. [POINT:120,80:Save]"]
    shown, diag = _run_pipeline(monkeypatch, OR, chunks)
    assert shown == [[Point(120, 80, "Save")]]
    assert "parsed=1 rejected=1" in diag[0] and "space=pixels" in diag[0]


def test_pipeline_with_only_bad_points_draws_nothing(monkeypatch):
    shown, diag = _run_pipeline(monkeypatch, GEM, ["Here. [POINT:5000,5000:Nope]"])
    assert shown == []
    assert "parsed=0 rejected=1" in diag[0]
