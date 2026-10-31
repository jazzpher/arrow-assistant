"""GPU scene renderer (OpenGL 3.3+ via moderngl).

One SceneGL draws a validated Scene: per-part Phong-shaded meshes, a floor
grid, and a color-ID picking pass for hover. The same code runs three
ways: inside the Qt widget (context attached to QOpenGLWidget), headless
on a standalone EGL context (CI gate, screenshots), and nowhere - when no
GL 3.3 context can be created the caller falls back to the CPU renderer
and says so on the card.

Shaders are static strings in this file. Model output is scene JSON only;
no shader or code ever comes from the LLM.
"""
from __future__ import annotations

import numpy as np

from . import camera, meshgen
from .spec import Scene

PHONG_VS = """#version 330
uniform mat4 u_mvp;
in vec3 in_pos;
in vec3 in_nrm;
out vec3 v_pos;
out vec3 v_nrm;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    v_pos = in_pos;
    v_nrm = in_nrm;
}
"""

PHONG_FS = """#version 330
uniform vec3 u_color;
uniform vec3 u_campos;
uniform float u_boost;
in vec3 v_pos;
in vec3 v_nrm;
out vec4 f_color;
void main() {
    vec3 n = normalize(v_nrm);
    vec3 key_dir = normalize(vec3(-0.5, 0.8, 0.6));
    vec3 fill_dir = normalize(vec3(0.6, 0.2, -0.5));
    float key = max(dot(n, key_dir), 0.0);
    float fill = max(dot(n, fill_dir), 0.0) * 0.35;
    vec3 view_dir = normalize(u_campos - v_pos);
    vec3 half_dir = normalize(key_dir + view_dir);
    float spec = pow(max(dot(n, half_dir), 0.0), 48.0) * 0.35;
    vec3 col = u_color * (0.38 + 0.62 * key + fill) + vec3(spec);
    col = mix(col, min(col * 1.6 + 0.15, 1.0), u_boost);
    f_color = vec4(col, 1.0);
}
"""

FLAT_VS = """#version 330
uniform mat4 u_mvp;
in vec3 in_pos;
void main() { gl_Position = u_mvp * vec4(in_pos, 1.0); }
"""

FLAT_FS = """#version 330
uniform vec3 u_color;
out vec4 f_color;
void main() { f_color = vec4(u_color, 1.0); }
"""

GRID_HALF = 8                          # lines from -8..8 on X and Z
GRID_COLOR = (0.24, 0.30, 0.42, 0.0)   # slate, subtle on dark background
AXIS_X = (0.55, 0.30, 0.30, 0.0)
AXIS_Z = (0.30, 0.42, 0.60, 0.0)


def _grid_lines() -> np.ndarray:
    pts = []
    for i in range(-GRID_HALF, GRID_HALF + 1):
        pts += [(i, 0.0, -GRID_HALF), (i, 0.0, GRID_HALF)]
        pts += [(-GRID_HALF, 0.0, i), (GRID_HALF, 0.0, i)]
    return np.array(pts, dtype="f4")


def _axis_lines() -> np.ndarray:
    return np.array([(-GRID_HALF, 0.01, 0), (GRID_HALF, 0.01, 0),
                     (0, 0.01, -GRID_HALF), (0, 0.01, GRID_HALF)],
                    dtype="f4")


class SceneGL:
    """Owns the GPU state for one scene inside one moderngl context."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._phong = ctx.program(vertex_shader=PHONG_VS,
                                  fragment_shader=PHONG_FS)
        self._flat = ctx.program(vertex_shader=FLAT_VS,
                                 fragment_shader=FLAT_FS)
        grid_vbo = ctx.buffer(_grid_lines().tobytes())
        axis_vbo = ctx.buffer(_axis_lines().tobytes())
        self._grid_vao = ctx.vertex_array(self._flat, grid_vbo, "in_pos")
        self._axis_vao = ctx.vertex_array(self._flat, axis_vbo, "in_pos")
        self._parts: list[dict] = []
        self._labels: list[dict] = []
        self._anchors: list[tuple[float, float, float]] = []
        self._radius = 1.0
        self._bg = (0.06, 0.09, 0.16)
        self._floor = True
        self._pick_fbo = None
        self._pick_size = (0, 0)

    # -- scene upload --------------------------------------------------------
    def set_scene(self, scene: Scene) -> None:
        for part in self._parts:
            for res in (part.get("vao"), part.get("pick_vao"),
                        part.get("vbo"), part.get("ibo")):
                if res is not None:
                    res.release()
        self._parts = []
        parts, labels, radius = meshgen.item_meshes(scene)
        self._labels = labels
        self._radius = radius
        self._floor = getattr(scene, "floor", True)
        self._bg = meshgen.hex_rgb(scene.background)
        for i, p in enumerate(parts):
            interleaved = np.concatenate([p["pos"], p["nrm"]], axis=1)
            vbo = self.ctx.buffer(interleaved.astype("f4").tobytes())
            ibo = self.ctx.buffer(p["idx"].astype("u4").tobytes())
            vao = self.ctx.vertex_array(
                self._phong, [(vbo, "3f 3f", "in_pos", "in_nrm")], ibo)
            pick_vao = self.ctx.vertex_array(
                self._flat, [(vbo, "3f 3x4", "in_pos")], ibo)
            pick_id = i + 1
            self._parts.append({
                "vao": vao, "pick_vao": pick_vao, "vbo": vbo, "ibo": ibo,
                "color": p["color"], "name": p.get("name", ""),
                "anchor": p.get("anchor"),
                "pick_rgb": ((pick_id & 255) / 255.0,
                             ((pick_id >> 8) & 255) / 255.0,
                             ((pick_id >> 16) & 255) / 255.0),
            })

    @property
    def radius(self) -> float:
        return self._radius

    @property
    def part_names(self) -> list[str]:
        return [p["name"] for p in self._parts]

    # -- matrices --------------------------------------------------------------
    def _mvp(self, view_state: dict, w: int, h: int):
        view, distance = camera.view_from(view_state, self._radius)
        proj = camera.perspective(w / max(h, 1), distance)
        mvp = (proj @ view).astype("f4")
        eye = camera.eye_position(view)
        return mvp, (float(eye[0]), float(eye[1]), float(eye[2])), view, proj

    # -- drawing ---------------------------------------------------------------
    def draw(self, view_state: dict, w: int, h: int,
             hover: int | None = None, target=None) -> None:
        """Draw into `target` (a framebuffer) or the current default."""
        mvp, eye, _, _ = self._mvp(view_state, w, h)
        fb = target if target is not None else self.ctx.fbo
        fb.use()
        self.ctx.viewport = (0, 0, w, h)
        fb.clear(*self._bg, 1.0)
        self.ctx.enable(self.ctx.DEPTH_TEST)
        self._flat["u_mvp"].write(mvp.T.tobytes())
        if self._floor:
            self._flat["u_color"] = GRID_COLOR[:3]
            self._grid_vao.render(self.ctx.LINES)
            self._flat["u_color"] = AXIS_X[:3]
            self._axis_vao.render(self.ctx.LINES, vertices=2)
            self._flat["u_color"] = AXIS_Z[:3]
            self._axis_vao.render(self.ctx.LINES, vertices=2, first=2)
        self._phong["u_mvp"].write(mvp.T.tobytes())
        self._phong["u_campos"] = eye
        for i, part in enumerate(self._parts):
            self._phong["u_color"] = part["color"]
            self._phong["u_boost"] = 1.0 if hover == i else 0.0
            part["vao"].render(self.ctx.TRIANGLES)

    def pick(self, view_state: dict, w: int, h: int,
             x: int, y: int) -> int | None:
        """Part index at widget pixel (x, y), or None on background."""
        if not self._parts:
            return None
        if self._pick_size != (w, h):
            if self._pick_fbo is not None:
                self._pick_fbo.release()
            self._pick_fbo = self.ctx.simple_framebuffer(
                (w, h), components=3)
            self._pick_size = (w, h)
        mvp, _, _, _ = self._mvp(view_state, w, h)
        self._pick_fbo.use()
        self.ctx.viewport = (0, 0, w, h)
        self._pick_fbo.clear(0.0, 0.0, 0.0, 1.0)
        self.ctx.enable(self.ctx.DEPTH_TEST)
        self._flat["u_mvp"].write(mvp.T.tobytes())
        for part in self._parts:
            self._flat["u_color"] = part["pick_rgb"]
            part["pick_vao"].render(self.ctx.TRIANGLES)
        py = max(0, min(h - 1, h - 1 - y))
        px = max(0, min(w - 1, x))
        data = self._pick_fbo.read(viewport=(px, py, 1, 1), components=3)
        pick_id = data[0] | (data[1] << 8) | (data[2] << 16)
        self.ctx.fbo.use()
        return pick_id - 1 if pick_id > 0 else None

    def label_positions(self, view_state: dict, w: int, h: int
                        ) -> list[tuple[float, float, str, str, float]]:
        """World labels + part anchors -> (x, y, text, color, size) screen
        triples for the QPainter overlay. Points behind the camera drop."""
        _, _, view, proj = self._mvp(view_state, w, h)
        items: list[tuple] = []
        anchors, texts, colors, sizes = [], [], [], []
        for lb in self._labels:
            anchors.append(lb["at"])
            texts.append(lb["text"])
            colors.append(lb.get("color", "#e2e8f0"))
            sizes.append(lb.get("size", 24))
        for part in self._parts:
            if part["anchor"] and part["name"]:
                anchors.append(part["anchor"])
                texts.append(part["name"])
                colors.append("#e2e8f0")
                sizes.append(24)
        if not anchors:
            return items
        xs, ys, depth = camera.project_points(
            np.array(anchors, dtype="f8"), view, proj, w, h)
        for (x, y, dep, text, col, size) in zip(xs, ys, depth, texts,
                                                colors, sizes):
            if np.isfinite(dep):
                items.append((float(x), float(y), text, col, float(size)))
        return items


def create_standalone_context(require: int = 330):
    """Standalone GL context with backend preference. glcontext's default
    picks X11 once Qt is loaded (fails headless); EGL works everywhere we
    test, with the default backend as the fallback (WGL on Windows)."""
    import moderngl
    errors = []
    for backend in ("egl", None):
        try:
            if backend is None:
                return moderngl.create_context(standalone=True,
                                               require=require)
            return moderngl.create_context(standalone=True,
                                           backend=backend, require=require)
        except Exception as exc:                         # noqa: BLE001
            errors.append(f"{backend or 'default'}: {exc}")
    raise RuntimeError("; ".join(errors))


# -- headless / probing ---------------------------------------------------------


def have_egl() -> tuple[bool, str]:
    """Probe a standalone EGL context (used by tests and the CI gate).
    Returns (available, renderer-name-or-error)."""
    try:
        ctx = create_standalone_context()
        name = str(ctx.info.get("GL_RENDERER", "unknown"))
        ctx.release()
        return True, name
    except Exception as exc:                             # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


class HeadlessGL:
    """A fresh standalone context + SceneGL, explicitly released. Tests
    and screenshots use one per session instead of sharing a cached
    context: with several long-lived surfaceless contexts alive,
    llvmpipe recycled object ids across them and corrupted draws."""

    def __init__(self) -> None:
        self.ctx = create_standalone_context()
        self.gfx = SceneGL(self.ctx)

    def render(self, scene: Scene, view_state: dict, w: int, h: int
               ) -> np.ndarray:
        self.gfx.set_scene(scene)
        fbo = self.ctx.simple_framebuffer((w, h), components=3)
        self.gfx.draw(view_state, w, h, target=fbo)
        data = fbo.read(components=3)
        fbo.release()
        arr = np.frombuffer(data, dtype="u1").reshape(h, w, 3)
        return np.flipud(arr).copy()             # GL origin is bottom-left

    def close(self) -> None:
        self.ctx.release()

    def __enter__(self) -> HeadlessGL:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def render_scene_array(scene: Scene, view_state: dict, w: int, h: int
                       ) -> np.ndarray:
    """Render offscreen on a fresh standalone context; returns (h, w, 3)
    uint8 RGB. Drives screenshots and the CI capability gate."""
    with HeadlessGL() as gl:
        return gl.render(scene, view_state, w, h)


def render_scene_png(scene: Scene, view_state: dict, w: int, h: int) -> bytes:
    from PIL import Image
    img = Image.fromarray(render_scene_array(scene, view_state, w, h))
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
