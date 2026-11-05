import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from arrow_assistant import overlay as ov_mod
from arrow_assistant.overlay import (
    ARROW_COLOR, BUBBLE_MAX_W, MIN_CAPTURE_EXCLUSION_BUILD, WDA_EXCLUDEFROMCAPTURE,
    ArrowOverlay, _MonitorOverlay, bubble_opacity, capture_exclusion_wanted,
    exclude_from_capture, launch_point)
from arrow_assistant.points import Point


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def mon(qapp):
    w = _MonitorOverlay(QRect(0, 0, 900, 600), 1.0)
    yield w
    w.clear_points()
    w.deleteLater()


def _render(w):
    img = QImage(w.width(), w.height(), QImage.Format.Format_ARGB32)
    img.fill(QColor(235, 238, 242))
    p = QPainter(img)
    p.drawImage(0, 0, w.grab().toImage())
    p.end()
    return img


def _is_blue(c):
    return (abs(c.red() - ARROW_COLOR.red()) < 30 and abs(c.green() - ARROW_COLOR.green()) < 30
            and abs(c.blue() - ARROW_COLOR.blue()) < 30)


def _blue_bbox(img, x0=0, y0=0, x1=None, y1=None):
    x1 = img.width() if x1 is None else x1
    y1 = img.height() if y1 is None else y1
    xs, ys = [], []
    for y in range(y0, y1, 1):
        for x in range(x0, x1, 1):
            if _is_blue(img.pixelColor(x, y)):
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _settle(w, start=0.0):
    t = start
    while w.advance(t):
        t += 16
        assert t < 5_000, "cursor never settled"
    return t


# -- existing behaviour -------------------------------------------------------
def test_overlay_constructs_per_screen(qapp):
    ov = ArrowOverlay(qapp)
    assert len(ov._overlays) == len(qapp.screens())
    for w in ov._overlays:
        w.clear_points()


def test_point_at_routes_and_clears(qapp):
    ov = ArrowOverlay(qapp)
    g = ov._overlays[0]._geo
    inside = Point(g.x() + 50, g.y() + 50, "test")
    ov.point_at([inside])
    assert ov._overlays[0]._points == [inside]
    assert ov._overlays[0].is_animating()
    assert ov._timer.isActive()             # the 6 s auto-clear is armed
    ov.clear()
    assert ov._overlays[0]._points == []
    assert ov._overlays[0]._buddies == []
    assert not ov._overlays[0].is_animating()
    assert not ov._overlays[0].isVisible()


def test_up_to_three_cursors_each_land_on_its_point(mon):
    pts = [Point(150, 120, "a"), Point(600, 140, "b"), Point(420, 470, "c")]
    mon.show_points(pts, now_ms=0, mouse=(800, 550))
    assert len(mon._buddies) == 3
    _settle(mon)
    assert [b.pos for b in mon._buddies] == [(150.0, 120.0), (600.0, 140.0), (420.0, 470.0)]
    img = _render(mon)
    for x, y in [(150, 120), (600, 140), (420, 470)]:
        bb = _blue_bbox(img, x - 6, y - 6, x + 30, y + 44)
        assert bb is not None, (x, y)
        assert -3 <= bb[0] - x <= 6 and -3 <= bb[1] - y <= 8     # tip on the pixel


def test_dpi_scaling_still_applies(qapp):
    w = _MonitorOverlay(QRect(0, 0, 900, 600), 1.5)
    w.show_points([Point(600, 300, "x")], now_ms=0, mouse=(10, 10))
    _settle(w)
    assert w._buddies[0].pos == (400.0, 200.0)
    w.clear_points()


# -- flying cursor ------------------------------------------------------------
def test_cursor_takes_off_from_mouse_and_flies_in(mon):
    mon.show_points([Point(700, 400, "Export")], now_ms=0, mouse=(100, 500))
    b = mon._buddies[0]
    assert b.pos == (100.0, 500.0)
    mon.advance(b.flight.duration_ms / 2)
    mid = b.pos
    assert 100 < mid[0] < 700                  # on its way
    assert mid[1] < 400                         # on an arc, above the straight line
    _settle(mon)
    assert b.pos == (700.0, 400.0)
    assert b.opacity == pytest.approx(1.0)


def test_mouse_on_another_monitor_takes_off_near_target():
    sx, sy = launch_point((100, 100), (-500, 300), 900, 600)
    assert (sx, sy) != (100, 100)
    assert 0 <= sx <= 900 and 0 <= sy <= 600
    assert launch_point((100, 100), (300, 200), 900, 600) == (300.0, 200.0)
    assert launch_point((100, 100), None, 900, 600) != (100, 100)


def test_extra_cursors_are_staggered(mon):
    mon.show_points([Point(200, 200, "a"), Point(500, 300, "b"), Point(700, 100, "c")],
                    now_ms=0, mouse=(450, 550))
    delays = [b.flight.delay_ms for b in mon._buddies]
    assert delays == sorted(delays) and delays[0] == 0 and delays[2] > delays[1] > 0


def test_streamed_points_keep_landed_cursors_still(mon):
    """The teach pipeline re-sends the growing list as tags stream in: a
    cursor that already landed must not fly again."""
    a = Point(200, 200, "a")
    mon.show_points([a], now_ms=0, mouse=(800, 500))
    t = _settle(mon)
    first = mon._buddies[0]
    mon.show_points([a, Point(600, 300, "b")], now_ms=t, mouse=(800, 500))
    assert mon._buddies[0] is first
    assert first.pos == (200.0, 200.0)
    mon.advance(t + 100)
    assert first.pos == (200.0, 200.0)
    assert mon._buddies[1].pos != (600.0, 300.0)   # the new one is flying


def test_new_answer_retargets_cursor_from_where_it_is(mon):
    mon.show_points([Point(200, 200, "old")], now_ms=0, mouse=(800, 500))
    t = _settle(mon)
    mon.show_points([Point(650, 420, "new")], now_ms=t, mouse=(10, 10))
    b = mon._buddies[0]
    assert b.flight.start == (200.0, 200.0)       # flies on, not from the mouse
    _settle(mon, t)
    assert b.pos == (650.0, 420.0)


def test_animation_timer_stops_when_settled(qapp, mon):
    mon.show_points([Point(300, 300, "x")])
    assert mon.is_animating()
    from PyQt6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(1800, loop.quit)
    loop.exec()
    assert not mon.is_animating()
    assert mon._buddies[0].pos == (300.0, 300.0)


# -- message bubble -----------------------------------------------------------
def test_bubble_fades_in_near_arrival():
    assert bubble_opacity(0.0) == 0.0
    assert bubble_opacity(0.5) == 0.0
    assert 0.0 < bubble_opacity(0.8) < 1.0
    assert bubble_opacity(1.0) == 1.0


def test_bubble_follows_cursor_and_stays_on_screen(mon):
    mon.show_points([Point(880, 590, "Near the corner")], now_ms=0, mouse=(100, 100))
    _settle(mon)
    b = mon._buddies[0]
    box = mon._bubble_box(b)
    assert box.left() >= 4 and box.top() >= 4
    assert box.right() <= 900 - 4 and box.bottom() <= 600 - 4
    assert box.right() <= 880 and box.bottom() <= 590    # flipped up/left of the tip


def test_long_label_wraps_into_a_taller_bubble(mon):
    long = "Open the File menu first, then choose Save As near the bottom of the list"
    mon.show_points([Point(100, 100, long), Point(400, 100, "Short")], now_ms=0, mouse=(0, 0))
    (lw, lh), (sw, sh) = (b.bubble_size for b in mon._buddies)
    assert lw <= BUBBLE_MAX_W
    assert lh > sh * 1.5


def test_bubble_is_painted_with_its_cursor(mon):
    mon.show_points([Point(300, 200, "Click here")], now_ms=0, mouse=(50, 500))
    _settle(mon)
    img = _render(mon)
    box = mon._bubble_box(mon._buddies[0])
    c = img.pixelColor(int(box.left()) + 3, int(box.center().y()))
    assert c.red() < 60 and c.green() < 60 and c.blue() < 80   # dark bubble body


def test_no_label_no_bubble(mon):
    mon.show_points([Point(300, 200, "")], now_ms=0, mouse=(50, 500))
    assert mon._buddies[0].bubble_size == (0.0, 0.0)
    _settle(mon)
    _render(mon)   # paints without error


# -- hidden from screen captures ---------------------------------------------
class _FakeUser32:
    def __init__(self, result=1, exc=None):
        self.calls, self.result, self.exc = [], result, exc

    def SetWindowDisplayAffinity(self, hwnd, affinity):
        self.calls.append((hwnd, affinity))
        if self.exc:
            raise self.exc
        return self.result


def test_exclude_from_capture_sets_wda_excludefromcapture():
    u = _FakeUser32()
    assert exclude_from_capture(1234, user32=u, build=22631) is True
    assert u.calls == [(1234, WDA_EXCLUDEFROMCAPTURE)]
    assert WDA_EXCLUDEFROMCAPTURE == 0x11


def test_exclude_from_capture_skips_old_windows():
    # before Windows 10 2004 the flag would turn the whole overlay black
    u = _FakeUser32()
    assert exclude_from_capture(1, user32=u, build=MIN_CAPTURE_EXCLUSION_BUILD - 1) is False
    assert u.calls == []


def test_exclude_from_capture_never_raises():
    assert exclude_from_capture(1, user32=_FakeUser32(result=0), build=22631) is False
    assert exclude_from_capture(1, user32=_FakeUser32(exc=OSError("denied")),
                                build=22631) is False
    assert exclude_from_capture(1, user32=SimpleNamespace(), build=22631) is False


def test_exclude_from_capture_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(ov_mod.sys, "platform", "linux")
    assert exclude_from_capture(1) is False


def test_capture_exclusion_opt_out_env():
    assert capture_exclusion_wanted({}) is True
    assert capture_exclusion_wanted({"ARROW_OVERLAY_IN_CAPTURE": "1"}) is False
    assert capture_exclusion_wanted({"ARROW_OVERLAY_IN_CAPTURE": "true"}) is False
    assert capture_exclusion_wanted({"ARROW_OVERLAY_IN_CAPTURE": "0"}) is True
