import pytest

pytest.importorskip("PIL")

from arrow_assistant import capture


def test_scale_factor_no_resize_when_small():
    mon = {"left": 0, "top": 0, "width": 1000, "height": 800}
    assert capture.scale_factor(None, mon) == 1.0


def test_scale_factor_shrinks_large_monitors():
    mon = {"left": 0, "top": 0, "width": 3840, "height": 2160}
    s = capture.scale_factor(None, mon)
    assert abs(s - 1568 / 3840) < 1e-9


def test_model_to_screen_roundtrip():
    mon = {"left": 100, "top": 50, "width": 3840, "height": 2160}
    s = capture.scale_factor(None, mon)
    # model sees a 1568-wide image; a point at its center maps back to the
    # monitor center in virtual-desktop pixels
    x, y = capture.model_to_screen(784, 441, mon)
    assert abs(x - (100 + 1920)) <= 2
    assert abs(y - (50 + 1080)) <= 2


def test_model_to_screen_second_monitor_offset():
    mon = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
    x, y = capture.model_to_screen(0, 0, mon)
    assert (x, y) == (-1920, 0)
