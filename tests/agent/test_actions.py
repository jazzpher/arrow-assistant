import pytest

from arrow_assistant.agent.actions import (
    Action, ActionParseError, extract_json, parse_action)


def test_click_with_coords():
    a = parse_action('{"thought":"t","action":"click","x":10.4,"y":20,"label":"Save"}')
    assert a.kind == "click" and (a.x, a.y) == (10, 20) and a.label == "Save"
    assert a.is_physical and a.has_target


def test_click_with_element():
    a = parse_action('{"action":"click","element":14}')
    assert a.element == 14 and a.has_target


def test_fenced_and_chatty_output():
    a = parse_action('Sure!\n```json\n{"action":"done","message":"ok"}\n```\nthanks')
    assert a.kind == "done" and a.message == "ok" and a.is_terminal


def test_nested_action_object():
    a = parse_action('{"thought":"x","action":{"type":"click","x":1,"y":2}}')
    assert a.kind == "click" and (a.x, a.y) == (1, 2)


def test_braces_inside_strings():
    a = parse_action('{"action":"type","text":"a } b { c"}')
    assert a.text == "a } b { c"


def test_kind_aliases():
    assert parse_action('{"action":"press","keys":"ctrl+s"}').keys == ("ctrl", "s")
    assert parse_action('{"action":"launch","app":"notepad"}').kind == "open_app"
    assert parse_action('{"action":"finish","message":"x"}').kind == "done"
    assert parse_action('{"action":"right-click","x":1,"y":1}').kind == "right_click"


def test_key_normalization():
    a = parse_action('{"action":"key","keys":["Control","Return"]}')
    assert a.keys == ("ctrl", "enter")


@pytest.mark.parametrize("raw", [
    "", "no json here", '{"action":"fly"}', '{"x":1}',
    '{"action":"click"}', '{"action":"click","x":"abc","y":2}',
    '{"action":"click","x":true,"y":2}',
    '{"action":"type"}', '{"action":"type","text":""}',
    '{"action":"key","keys":[]}', '{"action":"key","keys":["a","b","c","d","e"]}',
    '{"action":"drag","x":1,"y":2}', '{"action":"open_app"}',
    '{"action":"ask_user"}', '{"action":"fail","message":"  "}',
    '{"action":"scroll","amount":0}', '{"action":"click","x":1', '[1,2]',
    '{"action":"wait","seconds":"soon"}',
])
def test_rejects_bad_output(raw):
    with pytest.raises(ActionParseError):
        parse_action(raw)


def test_type_length_cap():
    with pytest.raises(ActionParseError):
        parse_action('{"action":"type","text":"%s"}' % ("a" * 501))


def test_scroll_clamped_and_direction():
    assert parse_action('{"action":"scroll","amount":500}').amount == 20
    assert parse_action('{"action":"scroll","direction":"up"}').amount < 0


def test_wait_clamped():
    assert parse_action('{"action":"wait","seconds":999}').seconds == 10.0
    assert parse_action('{"action":"wait","seconds":0}').seconds == 0.2


def test_risk_hint_default_and_invalid():
    assert parse_action('{"action":"click","x":1,"y":1}').risk_hint == "low"
    assert parse_action('{"action":"click","x":1,"y":1,"risk":"HIGH"}').risk_hint == "high"
    assert parse_action('{"action":"click","x":1,"y":1,"risk":"nuclear"}').risk_hint == "low"


def test_describe_and_signature():
    a = parse_action('{"action":"type","text":"hi","label":"Search box"}')
    assert "hi" in a.describe() and "Search box" in a.describe()
    assert a.signature() == parse_action('{"action":"type","text":"hi"}').signature()
    assert Action(kind="wait", seconds=2).describe() == "wait 2s"


def test_extract_json_unterminated():
    with pytest.raises(ActionParseError):
        extract_json('{"a": {"b": 1}')
