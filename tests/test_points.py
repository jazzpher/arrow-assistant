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
