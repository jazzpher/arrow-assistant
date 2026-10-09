import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from arrow_assistant.overlay import ArrowOverlay
from arrow_assistant.points import Point


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


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
    ov.clear()
    assert ov._overlays[0]._points == []
