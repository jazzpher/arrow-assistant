"""Render a validated Scene with QPainter.

2D: the 0-1000 canvas is letterboxed into the target rect.
3D: world faces are perspective-projected (camera on a sphere around the
origin: yaw/pitch/distance), sorted far-to-near (painter's algorithm), and
flat-shaded with a fixed key light. All geometry comes from spec.py, which
caps counts and sizes, so this stays cheap and bounded.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF

from . import geo3d
from .spec import CANVAS, Scene

LIGHT = geo3d.norm((-0.5, 0.8, 0.6))
AMBIENT = 0.45                          # fraction of base color always kept

DEFAULT_VIEW = {"yaw": 35.0, "pitch": -18.0, "distance": 0.0}   # 0 = auto


def _qcolor(hex_str: str, fallback: str = "#888888") -> QColor:
    c = QColor(hex_str or fallback)
    return c if c.isValid() else QColor(fallback)


def _font(size: float, bold: bool = False) -> QFont:
    f = QFont("Segoe UI", max(6, round(size)))
    f.setBold(bold)
    return f


def shade(hex_color: str, normal: geo3d.Vec) -> QColor:
    """Flat shading: base color scaled by ambient + diffuse."""
    lam = max(0.0, geo3d.dot(normal, LIGHT))
    k = AMBIENT + (1.0 - AMBIENT) * lam
    c = _qcolor(hex_color)
    return QColor(min(255, round(c.red() * k)),
                  min(255, round(c.green() * k)),
                  min(255, round(c.blue() * k)))


# -- 2D ---------------------------------------------------------------------

def canvas_rect(w: float, h: float) -> QRectF:
    """Largest centered square-ish region for the 0-1000 canvas (4:3 max)."""
    aspect = 1.0
    side = min(w, h * aspect)
    return QRectF((w - side) / 2, (h - side / aspect) / 2, side, side / aspect)


def _to_px(rect: QRectF, x: float, y: float) -> tuple[float, float]:
    return (rect.x() + x / CANVAS * rect.width(),
            rect.y() + y / CANVAS * rect.height())


def _label(p: QPainter, rect: QRectF, x: float, y: float, text: str,
           size: float, color: str, align: str = "center") -> None:
    if not text:
        return
    px, py = _to_px(rect, x, y)
    fs = max(8.0, size / CANVAS * rect.width())
    p.setFont(_font(fs))
    p.setPen(QPen(_qcolor(color, "#e2e8f0")))
    flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
    if align == "left":
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    elif align == "right":
        flags = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    p.drawText(QRectF(px - 220, py - fs, 440, 2 * fs),
               int(flags | Qt.TextFlag.TextSingleLine), text)


def _arrowhead(p: QPainter, x1: float, y1: float, x2: float, y2: float,
               color: QColor, width: float) -> None:
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    size = max(8.0, width * 4)
    px, py = -uy, ux
    tip = QPointF(x2, y2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([
        tip,
        QPointF(x2 - ux * size + px * size * 0.45, y2 - uy * size + py * size * 0.45),
        QPointF(x2 - ux * size - px * size * 0.45, y2 - uy * size - py * size * 0.45),
    ]))


def render_2d(p: QPainter, scene: Scene, w: float, h: float) -> None:
    rect = canvas_rect(w, h)
    for item in scene.items:
        d = item.data
        if item.kind in ("rect", "ellipse"):
            x, y = _to_px(rect, d["x"], d["y"])
            ww = d["w"] / CANVAS * rect.width()
            hh = d["h"] / CANVAS * rect.height()
            r = QRectF(x, y, ww, hh)
            fill = _qcolor(d["fill"])
            outline = d.get("outline") or ""
            p.setPen(QPen(_qcolor(outline), 2) if outline
                     else Qt.PenStyle.NoPen)
            p.setBrush(fill)
            if item.kind == "rect" and d.get("radius"):
                p.drawRoundedRect(r, d["radius"], d["radius"])
            elif item.kind == "rect":
                p.drawRect(r)
            else:
                p.drawEllipse(r)
            if d.get("label"):
                _label(p, rect, d["x"] + d["w"] / 2, d["y"] + d["h"] / 2,
                       d["label"], 26, "#f8fafc")
        elif item.kind == "line":
            x1, y1 = _to_px(rect, d["x1"], d["y1"])
            x2, y2 = _to_px(rect, d["x2"], d["y2"])
            c = _qcolor(d["color"])
            pen = QPen(c, d["width"])
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            if d.get("arrow"):
                _arrowhead(p, x1, y1, x2, y2, c, d["width"])
            if d.get("label"):
                _label(p, rect, (d["x1"] + d["x2"]) / 2,
                       (d["y1"] + d["y2"]) / 2 - 18, d["label"], 24, "#e2e8f0")
        elif item.kind == "poly":
            pts = QPolygonF([QPointF(*_to_px(rect, px, py))
                             for px, py in d["points"]])
            p.setPen(QPen(_qcolor(d["outline"]), 2))
            p.setBrush(_qcolor(d["fill"]))
            if d.get("closed", True):
                p.drawPolygon(pts)
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolyline(pts)
        elif item.kind == "text":
            _label(p, rect, d["x"], d["y"], d["text"], d["size"],
                   d["color"], d.get("align", "center"))


# -- 3D ---------------------------------------------------------------------

_PRIMS = {"box": geo3d.box, "cylinder": geo3d.cylinder,
          "cone": geo3d.cone, "sphere": geo3d.sphere}


def build_meshes(scene: Scene) -> tuple[list[dict], list[dict]]:
    """Scene items -> (meshes, labels); meshes carry world-space verts/faces
    and their base color. Bounded by spec caps."""
    meshes: list[dict] = []
    labels: list[dict] = []
    for item in scene.items:
        d = item.data
        if item.kind == "label":
            labels.append(d)
            continue
        if item.kind == "arrow3d":
            a, b = tuple(d["from"]), tuple(d["to"])
            dist = math.sqrt(geo3d.dot(geo3d.sub(b, a), geo3d.sub(b, a)))
            head = min(0.35, dist * 0.3)
            shaft_end = geo3d.sub(b, tuple(head * x for x in geo3d.norm(geo3d.sub(b, a))))
            sv, sf = geo3d.cylinder(10)
            sv = [(x * 0.12, y, z * 0.12) for x, y, z in sv]
            hv, hf = geo3d.cone(10)
            hv = [(x * 0.5, y * head, z * 0.5) for x, y, z in hv]
            meshes.append({"verts": geo3d.orient_along(sv, a, shaft_end),
                           "faces": sf, "color": d["color"]})
            meshes.append({"verts": geo3d.orient_along(hv, shaft_end, b),
                           "faces": hf, "color": d["color"]})
            continue
        gen = _PRIMS.get(item.kind)
        if gen is None:
            continue
        if item.kind == "box":
            verts, faces = gen()
            s = d["size"]
            verts = [(v[0] * s[0], v[1] * s[1], v[2] * s[2]) for v in verts]
        elif item.kind == "sphere":
            verts, faces = gen(d.get("segments", 20))
            r = d["radius"] * 2
            verts = [(v[0] * r, v[1] * r, v[2] * r) for v in verts]
        else:                                   # cylinder / cone
            verts, faces = gen(d.get("segments", 20))
            r, hh = d["radius"] * 2, d["height"]
            verts = [(v[0] * r, v[1] * hh, v[2] * r) for v in verts]
            if d.get("axis") == "x":
                verts = [geo3d.rotate(v, 0, 0, -90) for v in verts]
            elif d.get("axis") == "z":
                verts = [geo3d.rotate(v, 90, 0, 0) for v in verts]
        rot = d.get("rotate") or (0, 0, 0)
        if any(rot):
            verts = [geo3d.rotate(v, rot[0], rot[1], rot[2]) for v in verts]
        c = d["center"]
        verts = [(v[0] + c[0], v[1] + c[1], v[2] + c[2]) for v in verts]
        meshes.append({"verts": verts, "faces": faces, "color": d["color"]})
        if d.get("label"):
            top = max((v[1] for v in verts), default=0.0)
            labels.append({"at": [c[0], top + 0.35, c[2]],
                           "text": d["label"], "color": "#e2e8f0", "size": 24})
    return meshes, labels


def scene_radius(meshes: list[dict], labels: list[dict]) -> float:
    """Bounding radius around the origin; drives auto distance/scale."""
    r = 0.0
    for m in meshes:
        for v in m["verts"]:
            r = max(r, math.sqrt(geo3d.dot(v, v)))
    for lb in labels:
        r = max(r, math.sqrt(geo3d.dot(tuple(lb["at"]), tuple(lb["at"]))))
    return max(r, 1.0)


def project(point: geo3d.Vec, yaw: float, pitch: float, distance: float,
            cx: float, cy: float, scale: float) -> tuple[float, float, float]:
    """World -> (screen_x, screen_y, depth). Larger depth = farther away."""
    x, y, z = geo3d.rotate(point, 0, yaw, 0)
    x, y, z = geo3d.rotate((x, y, z), pitch, 0, 0)
    z += distance
    z = max(z, 0.05)
    f = distance / z
    return (cx + x * scale * f, cy - y * scale * f, z)


def spread_labels(items: list[tuple[float, float, float, float]],
                  max_rounds: int = 40) -> list[tuple[float, float, float, float]]:
    """Greedy label de-overlap. items are (cx, cy, w, h); later entries are
    shifted below/above their anchor until clear of everything placed, with
    horizontal nudges on later rounds when vertical space alone runs out."""
    placed: list[tuple[float, float, float, float]] = []
    for x, y, w_est, h_est in items:
        x0, y0 = x, y
        for attempt in range(max_rounds):
            clash = any(abs(x - px) < (w_est + pw) / 2
                        and abs(y - py) < (h_est + ph) / 2
                        for px, py, pw, ph in placed)
            if not clash:
                break
            step = (attempt // 2 + 1) * h_est
            y = y0 + step if attempt % 2 == 0 else y0 - step
            if attempt >= max_rounds // 2:       # vertical exhausted: sidestep
                direction = 1 if attempt % 4 < 2 else -1
                x = x0 + direction * ((attempt - max_rounds // 2) // 2 + 1) \
                    * w_est * 0.45
        placed.append((x, y, w_est, h_est))
    return placed


def render_3d(p: QPainter, scene: Scene, w: float, h: float,
              view: dict | None = None) -> None:
    v = dict(DEFAULT_VIEW)
    if view:
        v.update(view)
    meshes, labels = build_meshes(scene)
    radius = scene_radius(meshes, labels)
    distance = v["distance"] or radius * 3.2
    scale = min(w, h) * 0.5 / radius
    cx, cy = w / 2, h / 2
    yaw, pitch = v["yaw"], max(-89.0, min(89.0, v["pitch"]))

    polys: list[tuple[float, QPolygonF, QColor]] = []
    for m in meshes:
        proj = [project(vtx, yaw, pitch, distance, cx, cy, scale)
                for vtx in m["verts"]]
        for face in m["faces"]:
            n = geo3d.face_normal(m["verts"], face)
            cam_dir = geo3d.norm(geo3d.rotate(
                geo3d.rotate((0, 0, 1), -pitch, 0, 0), 0, -yaw, 0))
            if geo3d.dot(n, cam_dir) <= 0:
                continue                       # back face
            depth = sum(proj[i][2] for i in face) / len(face)
            poly = QPolygonF([QPointF(proj[i][0], proj[i][1]) for i in face])
            polys.append((depth, poly, shade(m["color"], n)))
    polys.sort(key=lambda t: -t[0])            # far first
    p.setPen(QPen(QColor(15, 23, 42, 90), 1))
    for _, poly, col in polys:
        p.setBrush(col)
        p.drawPolygon(poly)
    # Project first, then de-overlap: dense scenes stack labels on top of
    # each other; spread_labels shifts later ones until they are clear.
    pending = []
    for lb in labels:
        x, y, _ = project(tuple(lb["at"]), yaw, pitch, distance, cx, cy, scale)
        fs = max(10.0, min(30.0, lb.get("size", 24) * 0.55))
        w_est = max(40.0, len(lb["text"]) * fs * 0.72)
        pending.append((lb, fs, x, y, w_est, fs * 1.5))
    placed = spread_labels([(x, y, w_est, h_est)
                            for _, _, x, y, w_est, h_est in pending])
    for (lb, fs, x, _y, _w, h_est), (_px, y, _pw, _ph) in zip(pending, placed):
        p.setFont(_font(fs, bold=True))
        p.setPen(QPen(_qcolor(lb.get("color", "#e2e8f0"))))
        p.drawText(QRectF(x - 200, y - h_est / 2, 400, h_est),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
                       | Qt.TextFlag.TextSingleLine), lb["text"])


def render(p: QPainter, scene: Scene, w: float, h: float,
           view: dict | None = None) -> None:
    p.fillRect(QRectF(0, 0, w, h), _qcolor(scene.background, "#0f172a"))
    if scene.kind == "3d":
        render_3d(p, scene, w, h, view)
    else:
        render_2d(p, scene, w, h)
