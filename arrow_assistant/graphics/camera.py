"""Orbit camera for the GL renderer: standard view/projection matrices,
world->screen projection for label placement, and screen->world rays for
picking math. Pure numpy; the same functions drive the widget, the
headless renderer and the tests, so what CI verifies is what ships.

View convention matches the CPU renderer: the world is rotated by yaw
around Y then pitch around X, and the camera sits at (0,0,-distance)
looking down +Z. Y is up on screen.
"""
from __future__ import annotations

import math

import numpy as np

FOV_Y_DEG = 45.0
NEAR = 0.05
AUTO_DISTANCE_FACTOR = 3.2            # same as the CPU renderer


def view_matrix(yaw: float, pitch: float, distance: float) -> np.ndarray:
    """World -> view. yaw about Y, pitch about X, then push +Z by distance."""
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    v = np.eye(4)
    v[:3, :3] = rx @ ry
    v[:3, 3] = (0.0, 0.0, distance)
    return v


def perspective(aspect: float, distance: float,
                fov_y_deg: float = FOV_Y_DEG) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_y_deg) / 2.0)
    far = max(NEAR + 1.0, distance * 4.0)
    # +Z-forward convention (camera looks down +Z): w_clip = z_view, with
    # near -> ndc.z -1 and far -> ndc.z +1.
    p = np.zeros((4, 4))
    p[0, 0] = f / max(aspect, 1e-6)
    p[1, 1] = f
    p[2, 2] = (far + NEAR) / (far - NEAR)
    p[2, 3] = -2 * far * NEAR / (far - NEAR)
    p[3, 2] = 1.0
    return p


def auto_distance(radius: float) -> float:
    return max(radius, 1.0) * AUTO_DISTANCE_FACTOR


def eye_position(view: np.ndarray) -> np.ndarray:
    """Camera world position from a view matrix."""
    return np.linalg.inv(view) @ np.array([0.0, 0.0, 0.0, 1.0])


def project_points(points: np.ndarray, view: np.ndarray, proj: np.ndarray,
                   w: float, h: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """World (n,3) -> (xs, ys, depths). Points behind the near plane get
    depth=inf and clamped coords so labels never explode."""
    pts = np.asarray(points, dtype="f8").reshape(-1, 3)
    hom = np.concatenate([pts, np.ones((len(pts), 1))], axis=1)
    clip = (proj @ view @ hom.T).T
    wclip = clip[:, 3]
    safe = np.where(np.abs(wclip) < 1e-9, 1e-9, wclip)
    ndc = clip[:, :3] / safe[:, None]
    behind = wclip < 1e-6
    xs = np.clip((ndc[:, 0] + 1.0) * 0.5 * w, -w, 2 * w)
    ys = np.clip((1.0 - ndc[:, 1]) * 0.5 * h, -h, 2 * h)
    depth = np.where(behind, np.inf, wclip)
    return xs, ys, depth


def screen_ray(x: float, y: float, w: float, h: float,
               view: np.ndarray, proj: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pixel (x,y), origin top-left -> (origin, direction) world ray."""
    ndc = np.array([2.0 * x / w - 1.0, 1.0 - 2.0 * y / h, -1.0, 1.0])
    inv = np.linalg.inv(proj @ view)
    p = inv @ ndc
    p = p[:3] / p[3]
    origin = (np.linalg.inv(view) @ np.array([0, 0, 0, 1.0]))[:3]
    direction = p - origin
    norm = float(np.linalg.norm(direction))
    if norm < 1e-12:
        return origin, np.array([0.0, 0.0, 1.0])
    return origin, direction / norm


def view_from(state: dict, radius: float) -> tuple[np.ndarray, float]:
    """Shared {yaw, pitch, distance} dict -> (view matrix, distance)."""
    distance = state.get("distance") or auto_distance(radius)
    pitch = max(-89.0, min(89.0, state.get("pitch", -18.0)))
    return view_matrix(state.get("yaw", 35.0), pitch, distance), distance
