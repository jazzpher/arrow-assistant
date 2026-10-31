"""Parametric GPU-ready meshes from a validated Scene.

Every generator returns indexed triangles with per-vertex normals as numpy
float32/uint32 arrays, unit-sized and centered at the origin; item_meshes()
applies each item's axis alignment, Euler rotation and translation and
returns world-space parts ready to upload to the GPU. All inputs come from
spec.py, so counts and sizes are already bounded; this module is pure math
(no GL) and unit-testable headless.
"""
from __future__ import annotations

import math

import numpy as np

from .spec import Scene

Vec3 = tuple[float, float, float]


def _mesh(pos: np.ndarray, nrm: np.ndarray, idx: np.ndarray) -> dict:
    return {"pos": np.asarray(pos, dtype="f4").reshape(-1, 3),
            "nrm": np.asarray(nrm, dtype="f4").reshape(-1, 3),
            "idx": np.asarray(idx, dtype="u4").ravel()}


def _revolve(prof_r, prof_y, prof_n, segments: int) -> dict:
    """Lathe a profile around +Y. prof_* are per-ring (radius, y, normal
    2D (nr, ny)). Smooth normals, welded seam via duplicated ring."""
    pos, nrm, idx = [], [], []
    rings = len(prof_r)
    for i in range(segments + 1):                     # +1: duplicated seam
        a = 2.0 * math.pi * i / segments
        ca, sa = math.cos(a), math.sin(a)
        for r, y, (nr, ny) in zip(prof_r, prof_y, prof_n):
            pos.append((r * ca, y, r * sa))
            nrm.append((nr * ca, ny, nr * sa))
    stride = rings
    for i in range(segments):
        j = i + 1
        for k in range(rings - 1):
            a0 = i * stride + k
            b0 = j * stride + k
            idx += [a0, b0, a0 + 1, b0, b0 + 1, a0 + 1]
    return _mesh(np.array(pos), np.array(nrm), np.array(idx))


def _cap(radius: float, y: float, ny: float, segments: int) -> dict:
    """Flat disc cap facing +ny, welded-seam ring."""
    pos = [(0.0, y, 0.0)]
    nrm = [(0.0, ny, 0.0)]
    for i in range(segments + 1):
        a = 2.0 * math.pi * i / segments
        pos.append((radius * math.cos(a), y, radius * math.sin(a)))
        nrm.append((0.0, ny, 0.0))
    idx = []
    for i in range(segments):
        if ny > 0:
            idx += [0, 1 + i + 1, 1 + i]
        else:
            idx += [0, 1 + i, 1 + i + 1]
    return _mesh(np.array(pos), np.array(nrm), np.array(idx))


def _combine(*meshes: dict) -> dict:
    pos, nrm, idx, base = [], [], [], 0
    for m in meshes:
        pos.append(m["pos"])
        nrm.append(m["nrm"])
        idx.append(m["idx"] + base)
        base += len(m["pos"])
    return _mesh(np.concatenate(pos), np.concatenate(nrm), np.concatenate(idx))


def box() -> dict:
    """Unit box, side 1, centered at the origin. Flat normals (24 verts)."""
    corners = [(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5)
               for z in (-0.5, 0.5)]

    def c(xi, yi, zi):
        return corners[xi * 4 + yi * 2 + zi]

    faces = [((-1, 0, 0), [c(0, 0, 0), c(0, 0, 1), c(0, 1, 1), c(0, 1, 0)]),
             ((1, 0, 0), [c(1, 0, 0), c(1, 1, 0), c(1, 1, 1), c(1, 0, 1)]),
             ((0, -1, 0), [c(0, 0, 0), c(1, 0, 0), c(1, 0, 1), c(0, 0, 1)]),
             ((0, 1, 0), [c(0, 1, 0), c(0, 1, 1), c(1, 1, 1), c(1, 1, 0)]),
             ((0, 0, -1), [c(0, 0, 0), c(0, 1, 0), c(1, 1, 0), c(1, 0, 0)]),
             ((0, 0, 1), [c(0, 0, 1), c(1, 0, 1), c(1, 1, 1), c(0, 1, 1)])]
    pos, nrm, idx = [], [], []
    for n, quad in faces:
        base = len(pos)
        pos += quad
        nrm += [n] * 4
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return _mesh(np.array(pos), np.array(nrm), np.array(idx))


def sphere(segments: int = 20) -> dict:
    """UV sphere radius 0.5, smooth normals (= normalized position)."""
    rings = max(3, segments // 2)
    pos, idx = [(0.0, 0.5, 0.0)], []
    for r in range(1, rings):
        phi = math.pi * r / rings
        for i in range(segments):
            theta = 2.0 * math.pi * i / segments
            pos.append((0.5 * math.sin(phi) * math.cos(theta),
                        0.5 * math.cos(phi),
                        0.5 * math.sin(phi) * math.sin(theta)))
    south = len(pos)
    pos.append((0.0, -0.5, 0.0))
    for i in range(segments):                          # top fan
        idx += [0, 1 + i, 1 + (i + 1) % segments]
    for r in range(rings - 2):                         # middle quads
        base = 1 + r * segments
        nxt = base + segments
        for i in range(segments):
            j = (i + 1) % segments
            idx += [base + i, nxt + i, base + j, nxt + i, nxt + j, base + j]
    last = 1 + (rings - 2) * segments
    for i in range(segments):                          # bottom fan
        idx += [south, last + (i + 1) % segments, last + i]
    nrm = [(p[0] * 2, p[1] * 2, p[2] * 2) for p in pos]  # r=0.5 -> unit
    return _mesh(np.array(pos), np.array(nrm), np.array(idx))


def cylinder(segments: int = 20) -> dict:
    """Unit cylinder along +Y: radius 0.5, height 1, centered."""
    wall = _revolve([0.5, 0.5], [-0.5, 0.5], [(1.0, 0.0)] * 2, segments)
    return _combine(wall, _cap(0.5, -0.5, -1.0, segments),
                    _cap(0.5, 0.5, 1.0, segments))


def cone(segments: int = 20) -> dict:
    """Unit cone: base radius 0.5 at y=-0.5, tip at y=+0.5. Smooth sides."""
    # side normal for slope r=0.5,h=1 is (cos a, 0.5, sin a) normalized
    n_side = (2.0 / math.sqrt(5.0), 1.0 / math.sqrt(5.0))
    pos = [(0.0, 0.5, 0.0)]                              # tip
    nrm = [(0.0, 1.0, 0.0)]
    for i in range(segments + 1):
        a = 2.0 * math.pi * i / segments
        ca, sa = math.cos(a), math.sin(a)
        pos.append((0.5 * ca, -0.5, 0.5 * sa))
        nrm.append((n_side[0] * ca, n_side[1], n_side[0] * sa))
    idx = []
    for i in range(segments):
        idx += [0, 1 + i, 1 + i + 1]
    return _combine(_mesh(np.array(pos), np.array(nrm), np.array(idx)),
                    _cap(0.5, -0.5, -1.0, segments))


def torus(major: float = 1.0, tube: float = 0.3, segments: int = 24) -> dict:
    """Torus around +Y (hole along Y): ring radius `major`, tube `tube`."""
    tube_seg = max(6, segments // 2)
    pos, nrm, idx = [], [], []
    for i in range(segments + 1):
        u = 2.0 * math.pi * i / segments
        cu, su = math.cos(u), math.sin(u)
        for j in range(tube_seg + 1):
            v = 2.0 * math.pi * j / tube_seg
            cv, sv = math.cos(v), math.sin(v)
            pos.append(((major + tube * cv) * cu, tube * sv,
                        (major + tube * cv) * su))
            nrm.append((cv * cu, sv, cv * su))
    stride = tube_seg + 1
    for i in range(segments):
        for j in range(tube_seg):
            a0 = i * stride + j
            b0 = (i + 1) * stride + j
            idx += [a0, b0, a0 + 1, b0, b0 + 1, a0 + 1]
    return _mesh(np.array(pos), np.array(nrm), np.array(idx))


def plane() -> dict:
    """Unit quad in XZ facing +Y, side 1, centered."""
    pos = [(-0.5, 0.0, -0.5), (0.5, 0.0, -0.5),
           (0.5, 0.0, 0.5), (-0.5, 0.0, 0.5)]
    nrm = [(0.0, 1.0, 0.0)] * 4
    idx = [0, 2, 1, 0, 3, 2]                             # +Y winding
    return _mesh(np.array(pos), np.array(nrm), np.array(idx))


# -- transforms ---------------------------------------------------------------

def _euler_matrix(rx: float, ry: float, rz: float) -> np.ndarray:
    """Degrees, applied X then Y then Z (matches geo3d.rotate)."""
    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


def _axis_matrix(axis: str) -> np.ndarray:
    """Rotate a +Y-aligned shape onto the given axis."""
    if axis == "x":
        return _euler_matrix(0, 0, -90)
    if axis == "z":
        return _euler_matrix(90, 0, 0)
    return np.eye(3)


def _orient_y_to(direction: Vec3) -> np.ndarray:
    """Rotation taking +Y to `direction` (Rodrigues)."""
    d = np.array(direction, dtype="f8")
    length = float(np.linalg.norm(d))
    if length < 1e-9:
        return np.eye(3)
    d /= length
    up = np.array([0.0, 1.0, 0.0])
    axis = np.cross(up, d)
    alen = float(np.linalg.norm(axis))
    cos_a = max(-1.0, min(1.0, float(np.dot(up, d))))
    if alen < 1e-9:
        return np.diag([1.0, -1.0, 1.0]) if cos_a < 0 else np.eye(3)
    axis /= alen
    angle = math.acos(cos_a)
    kx = np.array([[0, -axis[2], axis[1]],
                   [axis[2], 0, -axis[0]],
                   [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(angle) * kx + (1 - math.cos(angle)) * (kx @ kx)


def transform_mesh(mesh: dict, scale: Vec3 = (1, 1, 1),
                   rot: np.ndarray | None = None,
                   move: Vec3 = (0, 0, 0)) -> dict:
    """Scale (per-axis), rotate (3x3), translate. Normals use the
    inverse-transpose direction (n / scale, then rotate) and are
    renormalized, so non-uniform scale stays correctly lit."""
    s = np.array(scale, dtype="f8")
    s_safe = np.where(np.abs(s) < 1e-9, 1.0, s)
    r = np.eye(3) if rot is None else rot
    pos = mesh["pos"].astype("f8") * s
    nrm = mesh["nrm"].astype("f8") / s_safe
    pos = pos @ r.T
    nrm = nrm @ r.T
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = nrm / np.where(ln < 1e-12, 1.0, ln)
    pos = pos + np.array(move, dtype="f8")
    return {"pos": pos.astype("f4"), "nrm": nrm.astype("f4"),
            "idx": mesh["idx"]}


def hex_rgb(value: str) -> tuple[float, float, float]:
    """#rrggbb -> (r, g, b) floats in 0-1."""
    try:
        h = value.lstrip("#")
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0)
    except (ValueError, IndexError, AttributeError):
        return (0.15, 0.39, 0.92)                        # default blue


def _arrow_part(a: Vec3, b: Vec3, color: tuple, name: str) -> dict | None:
    """Shaft + head merged into one part, matching the CPU proportions."""
    a_, b_ = np.array(a), np.array(b)
    dvec = b_ - a_
    dist = float(np.linalg.norm(dvec))
    if dist < 1e-6:
        return None
    head = min(0.35, dist * 0.3)
    d = dvec / dist
    shaft_end = b_ - head * d
    shaft_len = float(np.linalg.norm(shaft_end - a_))
    r_mtx = _orient_y_to(tuple(dvec))
    shaft = transform_mesh(cylinder(12), scale=(0.12, max(shaft_len, 1e-4), 0.12),
                           rot=r_mtx, move=tuple(a_ + 0.5 * shaft_len * d))
    headm = transform_mesh(cone(12), scale=(0.5, head, 0.5),
                           rot=r_mtx, move=tuple(shaft_end + 0.5 * head * d))
    m = _combine(shaft, headm)
    return {**m, "color": color, "name": name,
            "anchor": (b[0], b[1] + 0.35, b[2]) if name else None}


def item_meshes(scene: Scene) -> tuple[list[dict], list[dict], float]:
    """Scene -> (parts, labels, bounding radius).

    parts: world-space meshes with color, pick name and optional label
    anchor. labels: standalone "label" items. Bounded by spec caps.
    """
    parts: list[dict] = []
    labels: list[dict] = []
    radius = 1.0
    for item in scene.items:
        d = item.data
        if item.kind == "label":
            labels.append(d)
            radius = max(radius, float(np.linalg.norm(np.array(d["at"]))))
            continue
        if item.kind == "arrow3d":
            part = _arrow_part(tuple(d["from"]), tuple(d["to"]),
                               hex_rgb(d["color"]), d.get("label", ""))
            if part is not None:
                parts.append(part)
            continue
        gen = {"box": box, "sphere": sphere, "cylinder": cylinder,
               "cone": cone, "plane": plane}.get(item.kind)
        if item.kind == "torus":
            mesh = torus(d.get("radius", 1.0), d.get("tube", 0.3),
                         d.get("segments", 24))
            scale = (1.0, 1.0, 1.0)
        elif gen is None:
            continue
        else:
            if item.kind == "box":
                mesh, scale = box(), tuple(d.get("size", [1, 1, 1]))
            elif item.kind == "sphere":
                r = d.get("radius", 1.0) * 2
                mesh, scale = sphere(d.get("segments", 20)), (r, r, r)
            elif item.kind == "plane":
                w, dd = d.get("size", [2.0, 2.0])
                mesh, scale = plane(), (w, 1.0, dd)
            else:                                        # cylinder / cone
                r, h = d.get("radius", 1.0) * 2, d.get("height", 2.0)
                mesh = gen(d.get("segments", 20))
                scale = (r, h, r)
        rot = _axis_matrix(d.get("axis", "y")) @ _euler_matrix(
            *(d.get("rotate") or (0, 0, 0)))
        center = tuple(d.get("center", [0, 0, 0]))
        m = transform_mesh(mesh, scale=scale, rot=rot, move=center)
        top = float(m["pos"][:, 1].max()) if len(m["pos"]) else center[1]
        name = d.get("label", "")
        parts.append({**m, "color": hex_rgb(d["color"]), "name": name,
                      "anchor": (center[0], top + 0.35, center[2])
                      if name else None})
        radius = max(radius, float(np.abs(m["pos"]).max())
                     if len(m["pos"]) else radius)
    return parts, labels, radius
