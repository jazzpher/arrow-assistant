import pytest

from arrow_assistant.hotkey import ComboTracker, parse_hotkey


def test_parse_basic():
    assert parse_hotkey("ctrl+alt+space") == frozenset({"ctrl", "alt", "space"})


def test_parse_char():
    assert parse_hotkey("ctrl+shift+q") == frozenset({"ctrl", "shift", "q"})


def test_parse_aliases():
    assert parse_hotkey("control+alt+space") == frozenset({"ctrl", "alt", "space"})


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        parse_hotkey("+++")


def _tracker(events):
    return ComboTracker(parse_hotkey("ctrl+alt+space"),
                        on_press=lambda: events.append("down"),
                        on_release=lambda: events.append("up"))


def test_press_release_cycle():
    events = []
    t = _tracker(events)
    for k in ("ctrl", "alt", "space"):
        t.press(k)
    t.release("space")
    assert events == ["down", "up"]


def test_no_fire_on_partial_combo():
    events = []
    t = _tracker(events)
    t.press("ctrl")
    t.press("space")
    assert events == []


def test_release_non_combo_key_keeps_active():
    events = []
    t = _tracker(events)
    for k in ("ctrl", "alt", "space"):
        t.press(k)
    t.release("q")  # unrelated key while held
    assert events == ["down"]
    t.release("alt")
    assert events == ["down", "up"]


def test_retrigger_after_release():
    events = []
    t = _tracker(events)
    for k in ("ctrl", "alt", "space"):
        t.press(k)
    t.release("space")
    t.press("space")
    t.release("space")
    assert events == ["down", "up", "down", "up"]


def test_extra_modifier_does_not_block():
    events = []
    t = _tracker(events)
    for k in ("shift", "ctrl", "alt", "space"):
        t.press(k)  # shift held too: combo still fires
    assert events == ["down"]
