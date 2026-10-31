"""GL capability tests for the GPU renderer.

By default these skip when no GL 3.3 is available (developer machines
vary). In CI the pytest job runs this file with ARROW_REQUIRE_GL=1 under
Xvfb + Mesa llvmpipe, where absence of the capability FAILS the job - so
a green run proves the GPU path actually rendered pixels and the widget
lifecycle actually worked, not just the CPU fallback.
"""
import os

import numpy as np
import pytest

from arrow_assistant.graphics import glrender, spec


@pytest.fixture()
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


REQUIRED = os.environ.get("ARROW_REQUIRE_GL") == "1"


@pytest.fixture()
def headless():
    """A fresh, released standalone GL context per test: sharing one
    surfaceless llvmpipe context across many scenes corrupted draws."""
    gate(*((EGL_OK, EGL_NAME) if True else ()))
    with glrender.HeadlessGL() as gl:
        yield gl

EGL_OK, EGL_NAME = glrender.have_egl()


def gate(ok: bool, what: str) -> None:
    if ok:
        return
    if REQUIRED:
        pytest.fail(f"GL capability required but missing: {what}")
    pytest.skip(f"no GL capability: {what}")


def sample_scene() -> spec.Scene:
    return spec.from_dict({"kind": "3d", "title": "Gate", "items": [
        {"type": "box", "center": [0, 0.5, 0], "size": [2, 1, 1],
         "color": "#3b82f6", "label": "Base"},
        {"type": "sphere", "center": [0, 1.9, 0], "radius": 0.7,
         "color": "red", "label": "Top"},
        {"type": "arrow3d", "from": [2, 0.2, 0], "to": [3, 1.5, 0],
         "color": "orange"},
        {"type": "label", "at": [0, 3, 0], "text": "Gate"}]})


VIEW = {"yaw": 35.0, "pitch": -18.0, "distance": 0.0}


def test_egl_context_available():
    gate(EGL_OK, f"EGL context ({EGL_NAME})")


def test_headless_render_pixels():
    gate(EGL_OK, EGL_NAME)
    scene = sample_scene()
    arr = glrender.render_scene_array(scene, VIEW, 320, 240)
    assert arr.shape == (240, 320, 3)
    bg = np.array([int(c * 255)
                   for c in glrender.meshgen.hex_rgb(scene.background)])
    non_bg = (np.abs(arr.astype(int) - bg).sum(axis=2) > 30).sum()
    assert non_bg > 3000                       # the scene really drew
    corner = arr[2, 2].astype(int)
    assert np.abs(corner - bg).sum() <= 30     # corners stay background


def test_view_changes_image():
    gate(EGL_OK, EGL_NAME)
    scene = sample_scene()
    a = glrender.render_scene_array(scene, VIEW, 320, 240)
    b = glrender.render_scene_array(
        scene, {**VIEW, "yaw": VIEW["yaw"] + 90}, 320, 240)
    assert not np.array_equal(a, b)


def test_pick_identifies_part(headless):
    gate(EGL_OK, EGL_NAME)
    scene = spec.from_dict({"kind": "3d", "items": [
        {"type": "box", "center": [-2.5, 0.5, 0], "size": [1, 1, 1],
         "color": "red", "label": "Left"},
        {"type": "box", "center": [2.5, 0.5, 0], "size": [1, 1, 1],
         "color": "blue", "label": "Right"}]})
    gfx = headless.gfx
    gfx.set_scene(scene)
    w, h = 320, 240
    view = dict(VIEW)
    for want, center in (("Left", [-2.5, 0.5, 0]), ("Right", [2.5, 0.5, 0])):
        _, distance = glrender.camera.view_from(view, gfx.radius)
        v = glrender.camera.view_matrix(view["yaw"], view["pitch"], distance)
        p = glrender.camera.perspective(w / h, distance)
        xs, ys, _ = glrender.camera.project_points(
            np.array([center], dtype="f8"), v, p, w, h)
        idx = gfx.pick(view, w, h, int(xs[0]), int(ys[0]))
        assert idx is not None and gfx.part_names[idx] == want
    assert gfx.pick(view, w, h, 2, 2) is None  # background corner


def test_widget_lifecycle(qapp):
    """The full window path: GraphicsWindow embeds the GPU canvas, renders
    real frames, reacts to view changes, and presents labels."""
    gate(EGL_OK, EGL_NAME)
    from arrow_assistant.graphics.window import GraphicsWindow
    win = GraphicsWindow()
    win.show_scene(sample_scene())
    assert win.renderer_mode() == "gpu"
    assert win._gl_view is not None and win._gl_view._gfx is not None
    arr1 = win._gl_view.frame_array()
    assert arr1 is not None and arr1.shape[0] > 100
    bg = np.array([15, 23, 42])
    assert (np.abs(arr1.astype(int) - bg).sum(axis=2) > 30).sum() > 5000
    win._view["yaw"] += 90
    win._gl_view._render_frame()
    arr2 = win._gl_view.frame_array()
    assert not np.array_equal(arr1, arr2)
    win.close_card()


def test_widget_falls_back_to_cpu_when_gl_missing(qapp, monkeypatch):
    """When no GL context can be created the card still shows the scene
    with the CPU renderer, and the mode is honestly 'cpu'."""

    def no_gl():
        raise RuntimeError("no GL here")
    monkeypatch.setattr("arrow_assistant.graphics.glrender."
                        "create_standalone_context", no_gl)
    from arrow_assistant.graphics.window import GraphicsWindow
    win = GraphicsWindow()
    win.show_scene(sample_scene())
    assert win.renderer_mode() == "cpu"
    assert win._gl_view is None
    img = win.grab()
    assert not img.isNull() and img.width() > 0
    win.close_card()


# -- robustness: lifecycle, resources, coordinates, bounds ----------------------

def test_repeated_scene_cycles_release_gpu_buffers(headless):
    """set_scene after set_scene must release the old parts' GL resources;
    weakrefs prove the buffers are gone, not just dropped from the list."""
    import gc
    import weakref
    gate(EGL_OK, EGL_NAME)
    gfx = headless.gfx
    scene_a = spec.from_dict({"kind": "3d", "items": [
        {"type": "box", "center": [0, 0, 0], "size": [1, 1, 1]}]})
    scene_b = spec.from_dict({"kind": "3d", "items": [
        {"type": "sphere", "center": [0, 0, 0], "radius": 1},
        {"type": "torus", "center": [2, 0, 0]}]})
    gfx.set_scene(scene_a)
    refs = []
    for part in gfx._parts:
        refs += [weakref.ref(part["vao"]), weakref.ref(part["vbo"]),
                 weakref.ref(part["ibo"]), weakref.ref(part["pick_vao"])]
    del part                                    # keep only weakrefs
    gfx.set_scene(scene_b)
    gc.collect()
    assert all(r() is None for r in refs)
    assert len(gfx._parts) == 2


def test_max_items_scene_renders():
    """A spec-capped scene (MAX_ITEMS parts) stays within framebuffer and
    renderer limits."""
    gate(EGL_OK, EGL_NAME)
    items = [{"type": "box",
              "center": [-(i % 8) * 1.5 + 5, (i // 8) * 1.2, 0],
              "size": [0.8, 0.8, 0.8], "label": f"p{i}"}
             for i in range(spec.MAX_ITEMS)]
    scene = spec.from_dict({"kind": "3d", "items": items})
    assert len(scene.items) == spec.MAX_ITEMS
    arr = glrender.render_scene_array(scene, VIEW, 400, 300)
    assert arr.shape == (300, 400, 3)


def test_pick_out_of_bounds_is_safe(headless):
    """Picking outside the widget (negative / oversized coords) must clamp
    and return None, never raise or read out of the framebuffer."""
    gate(EGL_OK, EGL_NAME)
    gfx = headless.gfx
    gfx.set_scene(sample_scene())
    # clamped to the framebuffer edge: out-of-range coords behave exactly
    # like the nearest in-bounds pixel, never raising or reading out of
    # bounds
    assert gfx.pick(dict(VIEW), 320, 240, -5, -10) == \
        gfx.pick(dict(VIEW), 320, 240, 0, 0)
    assert gfx.pick(dict(VIEW), 320, 240, 10000, 10000) == \
        gfx.pick(dict(VIEW), 320, 240, 319, 239)
    assert gfx.pick(dict(VIEW), 320, 240, -1, 0) == \
        gfx.pick(dict(VIEW), 320, 240, 0, 0)
    assert gfx.pick(dict(VIEW), 320, 240, 2, 2) is None   # bg corner


def test_widget_repeated_open_close_and_scene_switch(qapp):
    gate(EGL_OK, EGL_NAME)
    from arrow_assistant.graphics.window import GraphicsWindow
    win = GraphicsWindow()
    scene2d = spec.from_dict({"kind": "2d", "title": "d", "items": [
        {"type": "rect", "x": 10, "y": 10, "w": 100, "h": 100}]})
    for _ in range(3):
        win.show_scene(sample_scene())
        assert win.renderer_mode() == "gpu"
        assert win._gl_view.frame_array() is not None
        win.show_scene(scene2d)          # 2D drops the GL child
        assert win.renderer_mode() == "cpu"
        assert win._gl_view is None
    win.show_scene(sample_scene())
    assert win.renderer_mode() == "gpu"  # GPU still recovers after cycling
    win.close_card()
    win.close_card()                     # double close is harmless
    assert not win.isVisible()


def test_widget_autorotate_timer_advances_frame(qapp):
    gate(EGL_OK, EGL_NAME)
    from arrow_assistant.graphics.window import GraphicsWindow
    win = GraphicsWindow()
    win.show_scene(sample_scene())
    arr1 = win._gl_view.frame_array()
    yaw0 = win._view["yaw"]
    win._tick()
    assert win._view["yaw"] > yaw0
    arr2 = win._gl_view.frame_array()
    assert not np.array_equal(arr1, arr2)
    win.close_card()


def test_widget_resize_renders_new_size(qapp):
    gate(EGL_OK, EGL_NAME)
    from arrow_assistant.graphics.window import GraphicsWindow
    win = GraphicsWindow()
    win.show_scene(sample_scene())
    win._gl_view.resize(320, 240)
    win._gl_view._render_frame()
    arr = win._gl_view.frame_array()
    assert arr.shape == (240, 320, 3)
    win.close_card()


def test_unsupported_platform_stays_on_cpu_consistently(qapp, monkeypatch):
    """No retry storm: once GL probing fails, every later 3D scene goes
    straight to the CPU renderer, and 2D keeps working."""
    def no_gl():
        raise RuntimeError("no GL here")
    monkeypatch.setattr("arrow_assistant.graphics.glrender."
                        "create_standalone_context", no_gl)
    from arrow_assistant.graphics.window import GraphicsWindow
    win = GraphicsWindow()
    win.show_scene(sample_scene())
    assert win.renderer_mode() == "cpu"
    win.show_scene(sample_scene())
    assert win.renderer_mode() == "cpu"
    assert win._gl_view is None
    scene2d = spec.from_dict({"kind": "2d", "title": "d", "items": [
        {"type": "rect", "x": 10, "y": 10, "w": 100, "h": 100}]})
    win.show_scene(scene2d)
    assert win.renderer_mode() == "cpu"
    win.close_card()
