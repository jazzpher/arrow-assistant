"""Motion of the flying cursor buddy: pure math, no Qt (unit-tested).

The overlay's cursor does not just appear on the target. It flies there
on a gentle arc (a quadratic Bezier whose control point sits off the
straight line), arrives with a little speed left, overshoots and settles
with a damped spring. Everything is a function of elapsed time, so a late
or dropped frame never changes where the cursor ends up, and a test can
ask for any instant without a running event loop.

Units: widget-local logical pixels and milliseconds.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

FLIGHT_BASE_MS = 260.0        # flight time = base + per_px * distance, clamped
FLIGHT_MS_PER_PX = 0.35
FLIGHT_MIN_MS = 320.0
FLIGHT_MAX_MS = 700.0
ARC_RATIO = 0.22              # how far the arc bows out, as a share of the distance
ARC_MAX_PX = 160.0
ARRIVAL_SLOPE = 1.2           # eased progress' slope at arrival (0 = stop dead, no spring)
SPRING_ZETA = 0.45            # damping ratio: < 1 so it overshoots once and settles
SPRING_OMEGA = 0.022          # natural frequency, rad/ms (~3.5 Hz)
MAX_OVERSHOOT_PX = 14.0       # long flights must not fling the cursor past the target
SETTLE_PX = 0.5               # below this the spring is over and the cursor snaps home

Vec = tuple[float, float]


def flight_duration_ms(distance: float) -> float:
    """Short hops are quick, cross-screen flights take longer, both bounded."""
    return max(FLIGHT_MIN_MS, min(FLIGHT_MAX_MS, FLIGHT_BASE_MS + FLIGHT_MS_PER_PX * distance))


def arc_control(start: Vec, end: Vec) -> Vec:
    """Control point of the flight arc: the midpoint pushed sideways.

    The arc bows upward on the screen (or to the left for a straight
    vertical move), which reads as a throw rather than a slide."""
    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    if dist < 1e-6:
        return mx, my
    nx, ny = -dy / dist, dx / dist          # unit normal
    if ny > 0 or (ny == 0 and nx > 0):      # pick the side that points up / left
        nx, ny = -nx, -ny
    bow = min(ARC_MAX_PX, ARC_RATIO * dist)
    return mx + nx * bow, my + ny * bow


def bezier(p0: Vec, c: Vec, p1: Vec, u: float) -> Vec:
    a, b, d = (1 - u) ** 2, 2 * (1 - u) * u, u * u
    return a * p0[0] + b * c[0] + d * p1[0], a * p0[1] + b * c[1] + d * p1[1]


def ease(u: float, k: float = ARRIVAL_SLOPE) -> float:
    """Cubic Hermite from 0 to 1: starts at rest, arrives with slope k."""
    u = max(0.0, min(1.0, u))
    return (k - 2) * u ** 3 + (3 - k) * u ** 2


def _peak_factor(zeta: float) -> float:
    """Largest excursion of a spring launched from rest position with
    speed v is v / omega * this factor."""
    r = math.sqrt(1 - zeta * zeta)
    return math.exp(-zeta / r * math.atan2(r, zeta))


@dataclass
class Flight:
    """One cursor's trip from start to end, starting delay_ms after t=0."""
    start: Vec
    end: Vec
    delay_ms: float = 0.0
    bounds: Vec | None = None          # (w, h): keep the arc's bow on screen
    duration_ms: float = field(init=False)
    control: Vec = field(init=False)
    slope: float = field(init=False)            # this flight's arrival slope for ease()
    arrival_velocity: Vec = field(init=False)   # px/ms; the spring starts with it
    settle_ms: float = field(init=False)

    def __post_init__(self) -> None:
        dist = math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])
        self.duration_ms = flight_duration_ms(dist)
        self.control = arc_control(self.start, self.end)
        if self.bounds is not None:
            w, h = self.bounds
            self.control = (min(max(0.0, self.control[0]), w),
                            min(max(0.0, self.control[1]), h))
        # d/dt of bezier(ease(t / T, k)) at arrival = k * 2 * (end - control) / T.
        # Long flights get a smaller k, so the cursor brakes on the way in
        # instead of overshooting far, and the hand-off to the spring keeps
        # the same speed (no visible jolt).
        tx = 2 * (self.end[0] - self.control[0]) / self.duration_ms
        ty = 2 * (self.end[1] - self.control[1]) / self.duration_ms
        unit_speed = math.hypot(tx, ty)
        cap = MAX_OVERSHOOT_PX * SPRING_OMEGA / _peak_factor(SPRING_ZETA)
        self.slope = ARRIVAL_SLOPE
        if unit_speed * self.slope > cap:
            self.slope = cap / unit_speed
        vx, vy = self.slope * tx, self.slope * ty
        speed = math.hypot(vx, vy)
        self.arrival_velocity = (vx, vy)
        wd = SPRING_OMEGA * math.sqrt(1 - SPRING_ZETA ** 2)
        amp = speed / wd
        self.settle_ms = (math.log(amp / SETTLE_PX) / (SPRING_ZETA * SPRING_OMEGA)
                          if amp > SETTLE_PX else 0.0)

    @property
    def total_ms(self) -> float:
        return self.delay_ms + self.duration_ms + self.settle_ms

    def done(self, t_ms: float) -> bool:
        return t_ms >= self.total_ms - 1e-6

    def progress(self, t_ms: float) -> float:
        """0..1 share of the flight phase (the spring counts as 1)."""
        return max(0.0, min(1.0, (t_ms - self.delay_ms) / self.duration_ms))

    def position(self, t_ms: float) -> Vec:
        t = t_ms - self.delay_ms
        if t <= 0:
            return self.start
        if t < self.duration_ms:
            return bezier(self.start, self.control, self.end,
                          ease(t / self.duration_ms, self.slope))
        tau = t - self.duration_ms
        if tau >= self.settle_ms - 1e-6:     # (float slack: done() and position() agree)
            return self.end
        # under-damped spring around the target, launched with the arrival speed
        wd = SPRING_OMEGA * math.sqrt(1 - SPRING_ZETA ** 2)
        s = math.exp(-SPRING_ZETA * SPRING_OMEGA * tau) * math.sin(wd * tau) / wd
        vx, vy = self.arrival_velocity
        return self.end[0] + vx * s, self.end[1] + vy * s
