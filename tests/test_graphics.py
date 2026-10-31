"""Real-time graphics engine: trigger routing, spec validation, streaming
parse, 3D math, rendering, and the app pipeline. Qt tests run offscreen.
"""
import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from arrow_assistant.config import LLMProvider  # noqa: E402
from arrow_assistant.graphics import engine, geo3d, parse, render, spec, trigger  # noqa: E402


# -- trigger -----------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "Can you draw the full jet in 3D and explain how the engines work?",
    "show me a diagram of how a tensor works",
    "can you visualize how the lift fan works?",
    "sketch the flow of this app",
    "make a chart of these numbers",
    "iguhit mo yung jet",
    "pakita mo kung paano gumagana yung engine",
    "gumawa ka ng larawan ng structure",
    "render this molecule",
    "can I get a 3d view of the engine",
])
def test_trigger_positive(q):
    assert trigger.wants_graphics(q)


@pytest.mark.parametrize("q", [
    "where is the save button?",
    "what does this setting do?",
    "saan yung File menu?",
    "how do I export to PDF?",
    "ituro mo yung settings",
    "",
    "   ",
])
def test_trigger_negative(q):
    assert not trigger.wants_graphics(q)


# -- spec validation -----------------------------------------------------------

def test_spec_defaults_and_colors():
    s = spec.from_dict({"kind": "2d", "title": "t", "items": [
        {"type": "rect", "x": 1, "y": 2, "w": 3, "h": 4, "fill": "blue"},
        {"type": "text", "x": 0, "y": 0, "text": "hi"},
    ]})
    assert s.kind == "2d"
    assert s.items[0].data["fill"] == "#3b82f6"
    assert s.items[1].data["color"] == spec.DEFAULT_TEXT
    assert s.background == spec.DEFAULT_BG
    assert s.dropped == 0


def test_spec_short_hex_expanded():
    s = spec.from_dict({"items": [
        {"type": "rect", "x": 0, "y": 0, "w": 1, "h": 1, "fill": "#abc"}]})
    assert s.items[0].data["fill"] == "#aabbcc"


def test_spec_drops_garbage_without_raising():
    s = spec.from_dict({"kind": "3d", "items": [
        {"type": "box"},                      # ok: defaults
        {"type": "warp-drive"},               # unknown kind
        "not-a-dict",
        {"type": "label", "at": [0, 0]},      # bad vec
        {"type": "arrow3d", "from": [0, 0, 0], "to": [0, 0, 0]},  # degenerate
        {"type": "text"},                     # no text
    ]})
    assert len(s.items) == 1
    assert s.dropped == 5


def test_spec_never_raises_on_junk():
    for junk in (None, 42, "scene", [], {"items": "x"}, {"items": [None]}):
        s = spec.from_dict(junk)
        assert isinstance(s, spec.Scene)


def test_spec_bounds_counts_and_values():
    s = spec.from_dict({"items": [
        {"type": "rect", "x": i, "y": 0, "w": 1, "h": 1}
        for i in range(spec.MAX_ITEMS * 3)]})
    assert len(s.items) == spec.MAX_ITEMS
    assert s.dropped == spec.MAX_ITEMS * 2
    big = spec.from_dict({"kind": "3d", "items": [
        {"type": "sphere", "radius": 1e9, "center": [1e9, 0, 0],
         "segments": 5000, "label": "x" * 1000}]})
    d = big.items[0].data
    assert d["radius"] <= spec.WORLD_LIMIT
    assert all(abs(c) <= spec.WORLD_LIMIT for c in d["center"])
    assert d["segments"] <= spec.MAX_MESH_SEGMENTS
    assert len(d["label"]) <= spec.MAX_TEXT_CHARS


def test_spec_nan_and_inf_become_defaults():
    s = spec.from_dict({"kind": "3d", "items": [
        {"type": "sphere", "radius": float("nan"), "center": [float("inf"), 0, 0]}]})
    assert s.items[0].data["radius"] == 1.0
    assert s.items[0].data["center"][0] == 0.0


def test_spec_infers_3d_from_items():
    s = spec.from_dict({"items": [{"type": "box"}]})
    assert s.kind == "3d"


def test_spec_roundtrip_json():
    import json
    s = spec.from_dict({"kind": "3d", "title": "jet", "items": [{"type": "box"}]})
    s2 = spec.from_dict(json.loads(json.dumps(s.to_dict())))
    assert s2.kind == "3d" and len(s2.items) == 1 and s2.title == "jet"


# -- parse ---------------------------------------------------------------------

def test_scene_filter_streams_speech_before_marker():
    f = parse.SceneFilter()
    out = ""
    for chunk in ["Here is the jet", ". It has one engine", ". [SCE",
                  "NE] {\"kind\": \"3d\", \"items\": []}"]:
        out += f.feed(chunk)
    out += f.flush()
    assert out == "Here is the jet. It has one engine. "
    assert f.spec_text().startswith(parse.MARKER)


def test_scene_filter_no_marker_flushes_all():
    f = parse.SceneFilter()
    out = f.feed("just speech, no scene")
    out += f.flush()
    assert out == "just speech, no scene"
    assert f.spec_text() == ""


def test_extract_spec_handles_fences_and_prose():
    raw = 'Spoken part. [SCENE]\n```json\n{"kind": "2d", "items": ' \
          '[{"type": "rect", "label": "a } brace", "x":0,"y":0,"w":1,"h":1}]}\n```\nDone.'
    d = parse.extract_spec(raw)
    assert d["items"][0]["label"] == "a } brace"


def test_extract_spec_truncated_returns_none():
    assert parse.extract_spec('[SCENE] {"kind": "2d", "items": [{"type":') is None


def test_extract_spec_requires_items_list():
    assert parse.extract_spec('[SCENE] {"kind": "2d"}') is None
    assert parse.extract_spec('no json here') is None


# -- 3D math ---------------------------------------------------------------------

def _assert_closed_outward(verts, faces):
    idx = {i for f in faces for i in f}
    assert idx == set(range(len(verts)))
    for f in faces:
        n = geo3d.face_normal(verts, f)
        centroid = geo3d.norm(geo3d.sub(verts[f[0]], (0, 0, 0)))
        assert geo3d.dot(n, centroid) > 0


@pytest.mark.parametrize("gen", [geo3d.box, geo3d.cylinder, geo3d.cone,
                                 geo3d.sphere])
def test_primitives_closed_and_outward(gen):
    verts, faces = gen()
    _assert_closed_outward(verts, faces)


def test_orient_along_spans_start_to_end():
    verts, _ = geo3d.cylinder(8)
    placed = geo3d.orient_along(verts, (0, 0, 0), (2, 0, 0))
    xs = [p[0] for p in placed]
    assert min(xs) >= -0.51 and max(xs) <= 2.51
    mid = sorted(xs)[len(xs) // 2]
    assert 0.0 <= mid <= 2.0
    down = geo3d.orient_along(verts, (0, 0, 0), (0, -3, 0))
    ys = [p[1] for p in down]
    assert min(ys) >= -3.51 and max(ys) <= 0.51


def test_project_depth_ordering():
    near = geo3d.sub((0, 0, 0), (0, 0, 0))
    a = render.project(near, 0, 0, 10, 100, 100, 50)
    b = render.project((0, 0, 2), 0, 0, 10, 100, 100, 50)
    assert a[2] < b[2]                      # farther world z -> larger depth


def test_build_meshes_kinds_and_labels():
    s = spec.from_dict({"kind": "3d", "items": [
        {"type": "box", "size": [1, 1, 1], "label": "hull"},
        {"type": "cylinder", "axis": "x"},
        {"type": "cone", "axis": "z"},
        {"type": "sphere"},
        {"type": "arrow3d", "from": [0, 0, 0], "to": [0, 2, 0]},
        {"type": "label", "at": [0, 3, 0], "text": "top"},
    ]})
    meshes, labels = render.build_meshes(s)
    assert len(meshes) == 6                 # box, cyl, cone, sphere, shaft+head
    assert any(lb["text"] == "top" for lb in labels)
    assert any(lb["text"] == "hull" for lb in labels)
    assert render.scene_radius(meshes, labels) >= 1.0


# -- rendering (offscreen Qt) ------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _drawn_fraction(scene, w=560, h=424, view=None):
    from PyQt6.QtGui import QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(img)
    render.render(p, scene, w, h, view)
    p.end()
    total = drawn = 0
    for x in range(0, w, 5):
        for y in range(0, h, 5):
            total += 1
            if img.pixelColor(x, y).name() != scene.background:
                drawn += 1
    return drawn / total


def test_render_2d_draws_content(qapp):
    s = spec.from_dict({"kind": "2d", "items": [
        {"type": "rect", "x": 100, "y": 100, "w": 300, "h": 200,
         "fill": "#2563eb", "label": "box"},
        {"type": "line", "x1": 100, "y1": 500, "x2": 800, "y2": 500,
         "arrow": True},
        {"type": "text", "x": 500, "y": 800, "text": "hello"},
    ]})
    frac = _drawn_fraction(s)
    assert 0.02 < frac < 0.95


def test_render_3d_draws_and_view_changes_image(qapp):
    s = spec.from_dict({"kind": "3d", "items": [
        {"type": "cylinder", "height": 4, "rotate": [0, 0, 90]},
        {"type": "box", "size": [5, 0.2, 1.5]},
        {"type": "sphere", "center": [1, -0.5, 0], "radius": 0.4},
        {"type": "arrow3d", "from": [1, -2, 0], "to": [1, -0.9, 0]},
        {"type": "label", "at": [0, 1.5, 0], "text": "jet"},
    ]})
    from PyQt6.QtGui import QImage, QPainter

    def snap(view):
        img = QImage(400, 300, QImage.Format.Format_RGB32)
        p = QPainter(img)
        render.render(p, s, 400, 300, view)
        p.end()
        return img
    a = snap({"yaw": 0.0, "pitch": -18.0})
    b = snap({"yaw": 120.0, "pitch": -18.0})
    assert _drawn_fraction(s, 560, 424) > 0.03
    assert a != b                           # rotation actually re-renders


def test_render_empty_scene_is_just_background(qapp):
    s = spec.from_dict({"kind": "2d", "title": "nothing"})
    assert _drawn_fraction(s) == pytest.approx(0.0, abs=0.01)


def test_window_show_scene(qapp):
    from arrow_assistant.graphics.window import GraphicsWindow
    w = GraphicsWindow()
    s = spec.from_dict({"kind": "2d", "title": "t", "items": [
        {"type": "rect", "x": 0, "y": 0, "w": 100, "h": 100}]})
    w.show_scene(s)
    assert w.isVisible()
    assert w.current_scene() is s
    grab = w.grab()
    assert not grab.isNull()
    w.close_card()
    assert not w.isVisible()


def test_window_3d_interaction_state(qapp):
    from arrow_assistant.graphics.window import GraphicsWindow
    w = GraphicsWindow()
    s = spec.from_dict({"kind": "3d", "items": [{"type": "box"}]})
    w.show_scene(s)
    assert w._spin.isActive()               # autorotate on for 3D
    yaw0 = w._view["yaw"]
    w._tick()
    assert w._view["yaw"] > yaw0
    w.close_card()
    assert not w._spin.isActive()


# -- engine ---------------------------------------------------------------------

class _FakeStream:
    """Monkeypatched ai.stream_parts: yields canned chunks, records args."""
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def __call__(self, provider, parts, timeout=60, system=None,
                 max_tokens=400):
        self.calls.append({"parts": parts, "system": system,
                           "max_tokens": max_tokens})
        yield from self.chunks


def _provider():
    return LLMProvider("gemini", "key", "gemini-3.5-flash")


def test_engine_run_speech_then_scene(monkeypatch):
    fake = _FakeStream(["Here is the jet. ", "[SCENE] ",
                        '{"kind": "3d", "title": "Jet", "items": '
                        '[{"type": "box"}]}'])
    monkeypatch.setattr(engine.ai, "stream_parts", fake)
    said: list[str] = []
    res = engine.run(_provider(), "draw the jet in 3d", "app.exe", "", None,
                     None, said.append, threading.Event())
    assert "".join(said) == "Here is the jet. "
    assert res.scene is not None and res.scene.kind == "3d"
    assert res.scene.title == "Jet"
    call = fake.calls[0]
    assert call["system"] == engine.GRAPHICS_SYSTEM_PROMPT
    assert call["max_tokens"] == engine.SCENE_MAX_TOKENS


def test_engine_run_without_scene_still_speaks(monkeypatch):
    fake = _FakeStream(["I cannot draw that."])
    monkeypatch.setattr(engine.ai, "stream_parts", fake)
    said: list[str] = []
    res = engine.run(_provider(), "draw a black hole", "app.exe", "", None,
                     None, said.append, threading.Event())
    assert res.scene is None
    assert "cannot draw" in "".join(said)


def test_engine_malformed_scene_is_dropped_not_fatal(monkeypatch):
    fake = _FakeStream(["ok. [SCENE] {\"kind\": \"3d\", \"items\": [{\"type\""])
    monkeypatch.setattr(engine.ai, "stream_parts", fake)
    res = engine.run(_provider(), "draw", "app.exe", "", None, None,
                     lambda t: None, threading.Event())
    assert res.scene is None


def test_engine_empty_scene_object_is_not_a_visual(monkeypatch):
    fake = _FakeStream(["[SCENE] {\"kind\": \"2d\", \"items\": []}"])
    monkeypatch.setattr(engine.ai, "stream_parts", fake)
    res = engine.run(_provider(), "draw", "app.exe", "", None, None,
                     lambda t: None, threading.Event())
    assert res.scene is None


def test_engine_followup_carries_previous_scene(monkeypatch):
    prev = spec.from_dict({"kind": "3d", "title": "jet",
                           "items": [{"type": "box"}]})
    fake = _FakeStream(["Updated. [SCENE] {\"kind\": \"3d\", \"items\": "
                        "[{\"type\": \"box\"}, {\"type\": \"sphere\"}]}"])
    monkeypatch.setattr(engine.ai, "stream_parts", fake)
    res = engine.run(_provider(), "now add the engine", "app.exe", "", None,
                     prev, lambda t: None, threading.Event())
    assert res.scene is not None and len(res.scene.items) == 2
    text_part = fake.calls[0]["parts"][0]["text"]
    assert "currently on screen" in text_part
    assert "jet" in text_part


def test_engine_cancel_stops_stream(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    fake = _FakeStream(["Hello. [SCENE] {\"kind\": \"2d\", \"items\": []}"])
    monkeypatch.setattr(engine.ai, "stream_parts", fake)
    res = engine.run(_provider(), "draw", "app.exe", "", None, None,
                     lambda t: None, cancel)
    assert res.raw == ""


# -- app pipeline routing ---------------------------------------------------------

def _fake_app(monkeypatch, app_mod):
    from PIL import Image

    said: list[str] = []

    class Sp:
        def start(self): pass
        def say(self, s): said.append(s)
        def finish(self): pass
        def stop(self): pass
        def wait(self): pass

    class Mem:
        def __init__(self): self.rec = None
        def recall(self, app): return ""
        def record(self, *a): self.rec = a

    class Sig:
        def __init__(self, sink=None): self.sink = sink
        def emit(self, v=None):
            if self.sink is not None:
                self.sink.append(v)

    monkeypatch.setattr(app_mod, "Speaker", Sp)
    monkeypatch.setattr(app_mod, "transcribe", lambda *a: "draw a box in 3d")
    monkeypatch.setattr(app_mod.config, "get_key", lambda n: None)
    monkeypatch.setattr(app_mod.capture, "foreground_app", lambda: "excel.exe")
    mon = {"left": 0, "top": 0, "width": 1000, "height": 500}
    monkeypatch.setattr(app_mod.capture, "primary_shot",
                        lambda: (mon, Image.new("RGB", (1000, 500))))
    monkeypatch.setattr(app_mod.kb, "lookup", lambda app: None)
    a = app_mod.ArrowApp.__new__(app_mod.ArrowApp)
    a.provider = object()
    a.stt_provider = "groq"
    a.memory = Mem()
    a.show_scene = Sig()
    a.status = a.error = Sig()
    a._speaker = None
    a._gfx_scene = None
    return a, said, Mem


def test_app_routes_graphics_question_to_engine(monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from arrow_assistant import app as app_mod
    a, said, _ = _fake_app(monkeypatch, app_mod)
    scenes = []
    a.show_scene = type("Sig", (), {"emit": lambda self, v: scenes.append(v)})()

    scene = spec.from_dict({"kind": "3d", "title": "box",
                            "items": [{"type": "box"}]})

    def fake_run(provider, question, app, mem, image_b64, prev, say, cancel):
        say("Here is a box. ")
        return engine.GraphicsResult("Here is a box. ", scene, "raw")

    monkeypatch.setattr(app_mod.gfx_engine, "run", fake_run)
    monkeypatch.setattr(app_mod.ai, "stream_answer",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("teach path must not run")))
    a._pipeline(b"WAV", threading.Event())
    assert scenes and scenes[0].title == "box"
    assert a._gfx_scene is scene            # follow-ups build on it
    assert said == ["Here is a box."]       # spoken via sentence streamer
    assert a.memory.rec[1] == "draw a box in 3d"


def test_app_plain_question_stays_on_teach_path(monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from arrow_assistant import app as app_mod
    a, said, _ = _fake_app(monkeypatch, app_mod)
    monkeypatch.setattr(app_mod, "transcribe", lambda *a: "saan yung save?")
    monkeypatch.setattr(app_mod.gfx_engine, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("graphics path must not run")))
    monkeypatch.setattr(app_mod.ai, "stream_answer",
                        lambda *a, **k: iter(["Nasa ribbon. "
                                              "[POINT:10,20:Save]"]))
    a.show_points = type("Sig", (), {"emit": lambda self, v: None})()
    a._pipeline(b"WAV", threading.Event())
    assert said == ["Nasa ribbon."]


def test_app_graphics_failure_degrades_to_speech(monkeypatch):
    """A broken engine must not break teach: speech already played, no
    scene shown, error reported, no exception escapes."""
    pytest.importorskip("PyQt6.QtWidgets")
    from arrow_assistant import app as app_mod
    a, said, _ = _fake_app(monkeypatch, app_mod)
    errors = []
    a.error = type("Sig", (), {"emit": lambda self, v: errors.append(v)})()

    def boom(*a, **k):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(app_mod.gfx_engine, "run", boom)
    a._pipeline(b"WAV", threading.Event())
    assert errors and "graphics error" in errors[0]
    assert a._gfx_scene is None
