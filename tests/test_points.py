from arrow_assistant.points import Point, parse_points, route_points


def test_parse_single_point():
    spoken, pts = parse_points("Click Export. [POINT:812,340:Export button]")
    assert spoken == "Click Export."
    assert pts == [Point(812, 340, "Export button")]


def test_parse_multiple_and_cap():
    text = " ".join(f"[POINT:{i},{i}:b{i}]" for i in range(5))
    spoken, pts = parse_points(text)
    assert spoken == ""
    assert len(pts) == 3  # MAX_POINTS cap


def test_parse_no_tags():
    spoken, pts = parse_points("Nothing to point at here.")
    assert pts == []
    assert spoken == "Nothing to point at here."


def test_parse_malformed_ignored():
    spoken, pts = parse_points("Try this [POINT:abc] and [POINT:1,2]")
    assert pts == []
    assert "Try this" in spoken


def test_route_single_monitor():
    mons = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
    routed = route_points([Point(100, 100, "a")], mons)
    assert routed == {0: [Point(100, 100, "a")]}


def test_route_second_monitor_negative_left():
    mons = [
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": -1920, "top": 0, "width": 1920, "height": 1080},
    ]
    routed = route_points([Point(-500, 200, "x")], mons)
    assert list(routed) == [1]


def test_route_out_of_bounds_clamps_to_first():
    mons = [{"left": 0, "top": 0, "width": 800, "height": 600}]
    routed = route_points([Point(9999, 9999, "far")], mons)
    assert list(routed) == [0]


# Synthetic representative variants, not captured private model responses.
def test_decimal_and_spaced_tags():
    spoken, pts = parse_points("[ POINT : 120.4, 80.7 : File ] File menu.")
    assert pts == [Point(120, 81, "File")]
    assert spoken == "File menu."


def test_unsafe_coordinate_formats_are_not_guessed():
    for text in ["[POINT:12%,80:File]", "[POINT:NaN,80:File]", "[POINT:120,Infinity:File]"]:
        assert parse_points(text) == ("", [])


def test_variant_streamed_at_every_boundary():
    from arrow_assistant.points import SpeechFilter
    text = "[ POINT : 120.4, 80.7 : File ] This is File."
    for cut in range(1, len(text)):
        f = SpeechFilter()
        t1, p1 = f.feed(text[:cut])
        t2, p2 = f.feed(text[cut:])
        assert p1 + p2 == [Point(120, 81, "File")]
        assert (t1 + t2 + f.flush()).strip() == "This is File."


def test_filter_diagnostic_counts():
    from arrow_assistant.points import SpeechFilter
    f = SpeechFilter()
    text, pts = f.feed("[POINT:abc] [POINT:120,80:File] [ POINT : 5,6:")
    f.flush()
    assert pts == [Point(120, 80, "File")]
    assert f.invalid_tags == 1
    assert f.incomplete_tags == 1
