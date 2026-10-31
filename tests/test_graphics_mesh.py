"""Pure-math tests for the GPU renderer's mesh and camera layers.

These need no GL context: they pin mesh integrity (bounds, unit normals,
valid indices), transform correctness and camera projection/picking math.
"""
import math

import numpy as np
import pytest

from arrow_assistant.graphics import camera, meshgen, spec

SHAPES = [meshgen.box, meshgen.sphere, meshgen.cylinder, meshgen.cone,
          meshgen.plane]


@pytest.mark.parametrize("gen", SHAPES)
def test_mesh_integrity(gen):
    m = gen()
    assert m["pos"].dtype == np.float32 and m["nrm"].dtype == np.float32
    assert m["pos"].shape == m["nrm"].shape
    assert m["idx"].max() < len(m["pos"])
    lens = np.linalg.norm(m["nrm"], axis=1)
    assert np.abs(lens - 1.0).max() < 1e-3
    assert np.abs(m["pos"]).max() <= 1.0 + 1e-6      # unit-sized


def test_torus_integrity_and_dimensions():
    m = meshgen.torus(1.0, 0.3, 24)
    lens = np.linalg.norm(m["nrm"], axis=1)
    assert np.abs(lens - 1.0).max() < 1e-3
    assert m["idx"].max() < len(m["pos"])
    rr = np.sqrt(m["pos"][:, 0] ** 2 + m["pos"][:, 2] ** 2)   # ring radius
    assert rr.max() == pytest.approx(1.3, abs=1e-4)
    assert abs(m["pos"][:, 1]).max() == pytest.approx(0.3, abs=1e-4)


def test_transform_nonuniform_scale_keeps_unit_normals():
    m = meshgen.transform_mesh(meshgen.sphere(12), scale=(2, 1, 1))
    lens = np.linalg.norm(m["nrm"], axis=1)
    assert np.abs(lens - 1.0).max() < 1e-3
    assert abs(m["pos"][:, 0]).max() == pytest.approx(1.0, abs=1e-4)


def test_transform_rotation_moves_geometry():
    m = meshgen.transform_mesh(meshgen.cylinder(8), scale=(0.5, 2.0, 0.5),
                               rot=meshgen._euler_matrix(0, 0, -90))
    # a +Y cylinder rotated -90 about Z lies along +X
    assert np.ptp(m["pos"][:, 0]) == pytest.approx(2.0, abs=1e-4)
    assert np.ptp(m["pos"][:, 0]) > np.ptp(m["pos"][:, 1])


def test_arrow_part_spans_endpoints():
    scene = spec.from_dict({"kind": "3d", "items": [
        {"type": "arrow3d", "from": [0, 0, 0], "to": [2, 2, 0],
         "color": "orange"}]})
    parts, _, _ = meshgen.item_meshes(scene)
    assert len(parts) == 1
    lo = parts[0]["pos"].min(axis=0)
    hi = parts[0]["pos"].max(axis=0)
    assert lo[0] < 0.1 and hi[0] > 1.9 and lo[1] < 0.1 and hi[1] > 1.9


def test_item_meshes_labels_and_anchors():
    scene = spec.from_dict({"kind": "3d", "items": [
        {"type": "box", "center": [0, 0.5, 0], "size": [2, 1, 1],
         "label": "Base"},
        {"type": "label", "at": [0, 3, 0], "text": "Top"}]})
    parts, labels, radius = meshgen.item_meshes(scene)
    assert len(parts) == 1 and parts[0]["name"] == "Base"
    assert parts[0]["anchor"][1] > 1.0                 # above the box top
    assert labels[0]["text"] == "Top"
    assert radius >= math.sqrt(9.0)


def test_camera_center_and_axes():
    v = camera.view_matrix(0, 0, 10)
    p = camera.perspective(560 / 420, 10)
    xs, ys, depth = camera.project_points(
        np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]]), v, p, 560, 420)
    assert xs[0] == pytest.approx(280) and ys[0] == pytest.approx(210)
    assert depth[0] == pytest.approx(10)
    assert xs[1] > xs[0]                               # +X goes right
    assert ys[2] < ys[0]                               # +Y goes up


def test_camera_depth_ordering_and_behind():
    v = camera.view_matrix(0, 0, 10)
    p = camera.perspective(100, 10)
    _, _, depth = camera.project_points(
        np.array([[0, 0, 0], [0, 0, 5], [0, 0, -20]]), v, p, 100, 100)
    assert depth[0] < depth[1]                         # +Z is farther
    assert not np.isfinite(depth[2])                   # behind the camera


def test_screen_ray_hits_center():
    v = camera.view_matrix(35, -18, 10)
    p = camera.perspective(560 / 420, 10)
    origin, direction = camera.screen_ray(280, 210, 560, 420, v, p)
    t = -float(origin[2]) / float(direction[2])
    hit = origin + t * direction
    assert np.abs(hit).max() < 0.05                    # through world origin


def test_view_from_clamps_pitch_and_autodistance():
    v, dist = camera.view_from({"yaw": 0, "pitch": 200, "distance": 0}, 2.0)
    assert dist == pytest.approx(2.0 * camera.AUTO_DISTANCE_FACTOR)
    assert v.shape == (4, 4)


def test_spec_torus_plane_bounds():
    s = spec.from_dict({"kind": "3d", "items": [
        {"type": "torus", "radius": 999, "tube": 999, "segments": 9999},
        {"type": "plane", "size": [9999, -5]}]})
    tor = s.items[0].data
    assert tor["radius"] <= spec.WORLD_LIMIT
    assert tor["tube"] <= tor["radius"]
    assert tor["segments"] <= spec.MAX_MESH_SEGMENTS
    assert s.items[1].data["size"][0] <= spec.WORLD_LIMIT
    assert s.items[1].data["size"][1] >= 0.05
