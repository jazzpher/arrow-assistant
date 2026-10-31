"""Minimal software-3D math and primitive meshes. No numpy, no OpenGL:
vertices are (x, y, z) tuples, faces are index tuples. Y is up."""
from __future__ import annotations

import math

Vec = tuple[float, float, float]


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a: Vec) -> Vec:
    length = math.sqrt(dot(a, a))
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (a[0] / length, a[1] / length, a[2] / length)


def face_normal(verts: list[Vec], face: tuple[int, ...]) -> Vec:
    a, b, c = (verts[face[0]], verts[face[1]], verts[face[2]])
    return norm(cross(sub(b, a), sub(c, a)))


def rotate(v: Vec, rx: float, ry: float, rz: float) -> Vec:
    """Euler rotation in degrees, applied X then Y then Z."""
    x, y, z = v
    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    y, z = y * cx - z * sx, y * sx + z * cx
    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    x, z = x * cy + z * sy, -x * sy + z * cy
    cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    x, y = x * cz - y * sz, x * sz + y * cz
    return (x, y, z)


def transform(verts: list[Vec], scale: Vec = (1, 1, 1),
              rot: Vec = (0, 0, 0), move: Vec = (0, 0, 0)) -> list[Vec]:
    return [tuple(r + m for r, m in zip(rotate(
        (v[0] * scale[0], v[1] * scale[1], v[2] * scale[2]),
        rot[0], rot[1], rot[2]), move)) for v in verts]


def box() -> tuple[list[Vec], list[tuple[int, ...]]]:
    """Unit box centered at the origin, side length 1."""
    v = [(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
    # index map: i = x*4 + y*2 + z with x,y,z in {0,1}
    f = [(0, 1, 3, 2), (4, 6, 7, 5),      # x- / x+
         (0, 4, 5, 1), (2, 3, 7, 6),      # y- / y+
         (0, 2, 6, 4), (1, 5, 7, 3)]      # z- / z+
    return v, f


def cylinder(segments: int = 20) -> tuple[list[Vec], list[tuple[int, ...]]]:
    """Unit cylinder along +Y, radius 0.5, height 1, centered at origin."""
    v: list[Vec] = []
    for y in (-0.5, 0.5):
        for i in range(segments):
            a = 2 * math.pi * i / segments
            v.append((0.5 * math.cos(a), y, 0.5 * math.sin(a)))
    bot_c, top_c = len(v), len(v) + 1
    v += [(0.0, -0.5, 0.0), (0.0, 0.5, 0.0)]
    f: list[tuple[int, ...]] = []
    for i in range(segments):
        j = (i + 1) % segments
        f.append((i, segments + i, segments + j, j))          # wall
        f.append((bot_c, i, j))                               # bottom cap
        f.append((top_c, segments + j, segments + i))         # top cap
    return v, f


def cone(segments: int = 20) -> tuple[list[Vec], list[tuple[int, ...]]]:
    """Unit cone, base radius 0.5 at y=-0.5, tip at y=+0.5."""
    v: list[Vec] = []
    for i in range(segments):
        a = 2 * math.pi * i / segments
        v.append((0.5 * math.cos(a), -0.5, 0.5 * math.sin(a)))
    tip, base_c = len(v), len(v) + 1
    v += [(0.0, 0.5, 0.0), (0.0, -0.5, 0.0)]
    f: list[tuple[int, ...]] = []
    for i in range(segments):
        j = (i + 1) % segments
        f.append((i, tip, j))
        f.append((base_c, i, j))
    return v, f


def sphere(segments: int = 20) -> tuple[list[Vec], list[tuple[int, ...]]]:
    """UV sphere, radius 0.5. segments = longitude count; latitude is half."""
    rings = max(3, segments // 2)
    v: list[Vec] = [(0.0, 0.5, 0.0)]                       # north pole
    for r in range(1, rings):
        phi = math.pi * r / rings
        for i in range(segments):
            theta = 2 * math.pi * i / segments
            v.append((0.5 * math.sin(phi) * math.cos(theta),
                      0.5 * math.cos(phi),
                      0.5 * math.sin(phi) * math.sin(theta)))
    south = len(v)
    v.append((0.0, -0.5, 0.0))
    f: list[tuple[int, ...]] = []
    for i in range(segments):                              # top fan
        f.append((0, 1 + (i + 1) % segments, 1 + i))
    for r in range(rings - 2):                             # middle quads
        base = 1 + r * segments
        nxt = base + segments
        for i in range(segments):
            j = (i + 1) % segments
            f.append((base + i, base + j, nxt + j, nxt + i))
    last = 1 + (rings - 2) * segments
    for i in range(segments):                              # bottom fan
        f.append((south, last + i, last + (i + 1) % segments))
    return v, f


def orient_along(verts: list[Vec], start: Vec, end: Vec) -> list[Vec]:
    """Rotate a +Y-aligned unit shape so +Y points start->end, then scale/
    place it between the two points. Used for arrow3d shafts/heads."""
    direction = sub(end, start)
    length = math.sqrt(dot(direction, direction))
    if length == 0:
        return list(verts)
    d = norm(direction)
    up = (0.0, 1.0, 0.0)
    axis = cross(up, d)
    cos_a = max(-1.0, min(1.0, dot(up, d)))
    angle = math.degrees(math.acos(cos_a))
    out = []
    for v in verts:
        # stretch along Y, then shift so the -Y end sits on `start`
        vv = (v[0], (v[1] + 0.5) * length, v[2])
        if abs(angle) > 1e-6 and norm(axis) != (0.0, 0.0, 0.0):
            vv = _rot_axis(vv, norm(axis), angle)
        elif cos_a < 0:
            vv = (vv[0], -vv[1], vv[2])                    # straight down
        out.append((vv[0] + start[0], vv[1] + start[1], vv[2] + start[2]))
    return out


def _rot_axis(v: Vec, axis: Vec, angle_deg: float) -> Vec:
    """Rodrigues' rotation formula."""
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    k = axis
    term1 = (v[0] * c, v[1] * c, v[2] * c)
    kxv = cross(k, v)
    term2 = (kxv[0] * s, kxv[1] * s, kxv[2] * s)
    kdv = dot(k, v) * (1 - c)
    return (term1[0] + term2[0] + k[0] * kdv,
            term1[1] + term2[1] + k[1] * kdv,
            term1[2] + term2[2] + k[2] * kdv)
