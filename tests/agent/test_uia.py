from types import SimpleNamespace as NS

from PIL import Image

from arrow_assistant.agent import uia
from arrow_assistant.agent.coords import Frame
from arrow_assistant.agent.observation import Element

FRAME = Frame.for_monitor({"left": 0, "top": 0, "width": 1000, "height": 800})


def ctl(name, role, rect, children=(), enabled=True, off=False, pw=False):
    l, t, r, b = rect
    return NS(Name=name, ControlTypeName=role, BoundingRectangle=NS(left=l, top=t, right=r, bottom=b),
              IsEnabled=enabled, IsOffscreen=off, IsPassword=pw,
              GetChildren=lambda c=children: list(c))


def raw(**kw):
    base = dict(name="x", role="ButtonControl", rect=(10, 10, 110, 50), enabled=True,
                offscreen=False, password=False)
    base.update(kw)
    return base


def test_walk_collects_tree_breadth_first():
    tree = ctl("win", "WindowControl", (0, 0, 1000, 800), [
        ctl("Save", "ButtonControl", (10, 10, 60, 40), [ctl("inner", "TextControl", (12, 12, 40, 30))]),
        ctl("Name", "EditControl", (10, 60, 300, 90), pw=False)])
    out = uia.walk_controls(tree)
    assert [o["name"] for o in out] == ["win", "Save", "Name", "inner"]


def test_walk_survives_broken_nodes_and_respects_limits():
    class Bad:
        @property
        def BoundingRectangle(self):
            raise RuntimeError("COM error")
        def GetChildren(self):
            raise RuntimeError("COM error")
    assert uia.walk_controls(Bad()) == []
    wide = ctl("root", "PaneControl", (0, 0, 10, 10),
               [ctl(str(i), "ButtonControl", (0, 0, 20, 20)) for i in range(50)])
    assert len(uia.walk_controls(wide, max_nodes=10)) == 10
    t = iter(range(0, 1000))
    assert len(uia.walk_controls(wide, budget_s=3, clock=lambda: next(t))) <= 5


def test_filter_numbers_in_reading_order_and_clips_to_monitor():
    els = uia.filter_and_number([
        raw(name="B", rect=(300, 10, 400, 50)),
        raw(name="A", rect=(10, 10, 100, 50)),
        raw(name="C", rect=(10, 200, 100, 250)),
        raw(name="Edge", rect=(950, 10, 1200, 50)),
    ], FRAME)
    assert [e.name for e in els] == ["A", "B", "Edge", "C"]
    assert [e.id for e in els] == [1, 2, 3, 4]
    assert els[2].rect[2] == 1000        # clipped to the monitor


def test_filter_drops_hidden_tiny_disabledless_nameless_and_huge():
    els = uia.filter_and_number([
        raw(name="hidden", offscreen=True),
        raw(name="tiny", rect=(0, 0, 4, 4)),
        raw(name="pane", role="PaneControl"),
        raw(name="", role="TextControl"),
        raw(name="huge", rect=(0, 0, 1000, 800)),
        raw(name="doc", role="DocumentControl", rect=(0, 0, 1000, 800)),
        raw(name="ok"),
    ], FRAME)
    assert sorted(e.name for e in els) == ["doc", "ok"]


def test_filter_dedupes_overlapping_boxes_preferring_named():
    els = uia.filter_and_number([
        raw(name="", rect=(10, 10, 110, 50)),
        raw(name="Save", rect=(11, 11, 111, 51)),
    ], FRAME)
    assert [e.name for e in els] == ["Save"]


def test_filter_cap_and_flags():
    many = [raw(name=f"b{i}", rect=(i * 12, 10, i * 12 + 10, 30)) for i in range(80)]
    assert len(uia.filter_and_number(many, FRAME, max_n=60)) == 60
    e = uia.filter_and_number([raw(name="Password", role="EditControl", pw=False, password=True),
                               raw(name="Off", rect=(300, 10, 400, 50), enabled=False)], FRAME)
    assert e[0].password and not e[1].enabled
    text = uia.elements_to_text(e)
    assert "(password field)" in text and "(disabled)" in text and "Edit 'Password'" in text


def test_annotate_draws_on_a_copy_and_marks_boxes():
    img = Image.new("RGB", (1000, 800), "white")
    els = [Element(1, "Save", "Button", (100, 100, 200, 150))]
    out = uia.annotate(img, els, FRAME)
    assert out is not img and img.getpixel((100, 100)) == (255, 255, 255)
    assert out.getpixel((150, 100)) != (255, 255, 255)   # top edge of the box
    assert out.getpixel((150, 125)) == (255, 255, 255)   # interior untouched


def test_annotate_respects_scaled_frames():
    big = Frame.for_monitor({"left": 0, "top": 0, "width": 3136, "height": 1764})   # scale 0.5
    img = Image.new("RGB", big.size, "white")
    out = uia.annotate(img, [Element(1, "x", "Button", (200, 200, 400, 300))], big)
    assert out.getpixel((100, 100)) != (255, 255, 255)    # corner at (200,200) -> (100,100)


def test_element_helpers():
    e = Element(1, "OK", "Button", (0, 0, 100, 40))
    assert e.center == (50, 20) and e.contains((10, 10)) and not e.contains((200, 10))
