"""Flight math of the flying cursor buddy (pure, no Qt)."""
import math

import pytest

from arrow_assistant.cursor_motion import (
    ARC_MAX_PX, FLIGHT_MAX_MS, FLIGHT_MIN_MS, MAX_OVERSHOOT_PX, Flight,
    arc_control, ease, flight_duration_ms)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _samples(f, step=2.0):
    t = 0.0
    while t <= f.total_ms + step:
        yield t, f.position(t)
        t += step


@pytest.mark.parametrize("start,end", [
    ((0, 0), (0, 0)), ((10, 10), (14, 13)), ((100, 500), (700, 120)),
    ((1800, 1000), (40, 30)), ((300, 50), (300, 900)), ((0, 400), (2400, 400)),
])
def test_flight_starts_at_start_and_ends_exactly_on_target(start, end):
    f = Flight(start, end)
    assert f.position(0) == start
    assert f.position(-50) == start
    assert f.position(f.total_ms) == end
    assert f.position(f.total_ms + 10_000) == end
    assert f.done(f.total_ms) and not f.done(f.total_ms - 20)


def test_delay_holds_the_cursor_at_start():
    f = Flight((0, 0), (500, 300), delay_ms=90)
    assert f.position(89) == (0, 0)
    assert f.position(120) != (0, 0)
    assert f.total_ms == pytest.approx(90 + f.duration_ms + f.settle_ms)


def test_duration_grows_with_distance_and_is_bounded():
    assert flight_duration_ms(0) == FLIGHT_MIN_MS
    assert flight_duration_ms(10_000) == FLIGHT_MAX_MS
    assert flight_duration_ms(200) < flight_duration_ms(1000)


def test_ease_starts_at_rest_and_ends_at_one():
    assert ease(0) == 0 and ease(1) == pytest.approx(1)
    assert ease(-1) == 0 and ease(2) == pytest.approx(1)
    assert ease(0.01) < 0.001               # slow take-off, no jump
    values = [ease(i / 100) for i in range(101)]
    assert values == sorted(values)         # never runs backwards mid-flight


def test_arc_bows_upward_and_is_capped():
    c = arc_control((0, 500), (800, 500))   # horizontal: bow goes up
    assert c[1] < 500
    assert 500 - c[1] == pytest.approx(ARC_MAX_PX)
    c = arc_control((800, 500), (0, 500))   # same, either direction
    assert c[1] < 500
    c = arc_control((300, 0), (300, 100))   # vertical: bow goes left
    assert c[0] < 300
    assert arc_control((5, 5), (5, 5)) == (5, 5)


def test_flight_is_curved_not_a_straight_line():
    f = Flight((100, 600), (900, 600))
    mid = f.position(f.duration_ms / 2)
    assert mid[1] < 600 - 40                # visibly above the straight path


def test_bounds_keep_the_arc_on_screen():
    f = Flight((50, 10), (850, 10), bounds=(900, 600))
    ys = [p[1] for _t, p in _samples(f)]
    assert min(ys) >= -MAX_OVERSHOOT_PX     # no swoop above the monitor


@pytest.mark.parametrize("end", [(30, 20), (300, 200), (1200, 900), (3000, 100)])
def test_spring_overshoots_a_little_then_settles(end):
    start = (0, 0)
    f = Flight(start, end)
    L = _dist(start, end)
    ux, uy = (end[0] - start[0]) / L, (end[1] - start[1]) / L
    beyond = max(((x - end[0]) * ux + (y - end[1]) * uy) for _t, (x, y) in _samples(f))
    assert beyond <= MAX_OVERSHOOT_PX + 0.5   # never flung far past the target
    if L >= 300:
        assert beyond > 1.0                    # but there is a visible settle
    # once settled it stays put
    tail = [f.position(f.total_ms + k) for k in range(0, 200, 20)]
    assert all(p == end for p in tail)


@pytest.mark.parametrize("end", [(40, 30), (600, 400), (2400, 1300)])
def test_motion_is_continuous_frame_to_frame(end):
    """No teleports: at 60 fps every frame moves a sane amount, and the
    hand-off from the arc to the spring has no jump."""
    f = Flight((0, 0), end)
    t = 0.0
    worst = 0.0
    while t < f.total_ms:
        worst = max(worst, _dist(f.position(t), f.position(t + 16)))
        t += 4
    # the busiest 16 ms frame covers at most ~2.5x the average speed
    avg_per_frame = _dist((0, 0), end) / f.duration_ms * 16
    assert worst <= max(4.0, 2.5 * avg_per_frame)
    arrive = f.delay_ms + f.duration_ms
    assert _dist(f.position(arrive - 0.5), f.position(arrive + 0.5)) < 2.0
