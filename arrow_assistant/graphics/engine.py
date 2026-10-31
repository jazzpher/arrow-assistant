"""The graphics pipeline: question + screen context -> spoken answer plus a
validated Scene. The LLM's JSON is untrusted; spec.from_dict bounds it
(counts, sizes, coordinates, colors, text). No code is ever executed from
model output, and no URLs or files are fetched.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

from .. import ai
from ..config import LLMProvider
from . import spec
from .parse import SceneFilter, extract_spec
from .spec import Scene

# The scene JSON needs far more tokens than a spoken answer.
SCENE_MAX_TOKENS = 1400
MAX_PREV_SPEC_CHARS = 6000

GRAPHICS_SYSTEM_PROMPT = """You are Arrow, a screen-aware assistant on Windows with a real-time
graphics engine. The user asked out loud for a DRAWING, DIAGRAM, or 3D
visual about what is on their screen. You get a screenshot, their
question, and recent memory.

Reply in two parts:
1. A short spoken answer, out-loud style: 1-3 short sentences, plain
   words. This is read aloud immediately.
2. A line containing exactly [SCENE], then one JSON object describing the
   visual. The JSON is rendered by code; it is never executed.

JSON shape:
{"kind": "2d" | "3d", "title": "short title", "caption": "optional",
 "autorotate": true, "items": [...]}

2D items (coordinates on a 1000x1000 canvas, x right, y DOWN):
- {"type":"rect","x":..,"y":..,"w":..,"h":..,"fill":"#hex","radius":..,"label":".."}
- {"type":"ellipse","x":..,"y":..,"w":..,"h":..,"fill":"#hex","label":".."}
- {"type":"line","x1":..,"y1":..,"x2":..,"y2":..,"color":"#hex","width":..,"arrow":true,"label":".."}
- {"type":"poly","points":[[x,y],...],"fill":"#hex","closed":true}
- {"type":"text","x":..,"y":..,"text":"..","size":..,"color":"#hex","align":"center"}

3D items (world units, keep everything inside about -5..5, y is UP):
- {"type":"box","center":[x,y,z],"size":[w,h,d],"color":"#hex","rotate":[rx,ry,rz],"label":".."}
- {"type":"cylinder","center":..,"radius":..,"height":..,"axis":"y","color":"#hex","label":".."}
- {"type":"sphere","center":..,"radius":..,"color":"#hex","label":".."}
- {"type":"cone","center":..,"radius":..,"height":..,"axis":"y","color":"#hex"}
- {"type":"arrow3d","from":[x,y,z],"to":[x,y,z],"color":"#hex"}
- {"type":"label","at":[x,y,z],"text":".."}

Rules:
- Build the subject from simple parts: boxes, cylinders, spheres, cones,
  arrows, labels. Compose them; do not attempt one complex mesh.
- Use "3d" when the user asks for 3D, a model, or an object from all
  sides; otherwise "2d" for diagrams, flows, charts, and sketches.
- Label the important parts. 4-12 items is usually right; max 20.
- Colors as #rrggbb or plain names (red, blue, gray, ...). Distinct,
  muted colors on the dark background (#0f172a).
- When the user asks to change the current visual, return the FULL
  updated JSON, not a diff.
- Never emit [POINT:] tags here; the graphics engine replaces pointing.
"""


def build_user_content(question: str, app: str, memory: str,
                       image_b64: str | None,
                       prev_scene: Scene | None) -> list[dict]:
    ctx = [f"The focused app is: {app}", f"The user asks: {question}"]
    if memory:
        ctx.append(f"Recent history with this app:\n{memory}")
    if prev_scene is not None:
        prev = json.dumps(prev_scene.to_dict())[:MAX_PREV_SPEC_CHARS]
        ctx.append("The visual currently on screen is this scene; update "
                   f"it if the question refers to it:\n{prev}")
    parts: list[dict] = [{"type": "text", "text": "\n\n".join(ctx)}]
    if image_b64:
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
        })
    return parts


def stream_scene(provider: LLMProvider, question: str, app: str,
                 memory: str, image_b64: str | None,
                 prev_scene: Scene | None = None,
                 timeout: int = 90) -> Iterator[str]:
    """Yield the raw streamed answer (speech + [SCENE] JSON tail)."""
    parts = build_user_content(question, app, memory, image_b64, prev_scene)
    yield from ai.stream_parts(provider, parts, timeout=timeout,
                               system=GRAPHICS_SYSTEM_PROMPT,
                               max_tokens=SCENE_MAX_TOKENS)


class GraphicsResult:
    def __init__(self, speech: str, scene: Scene | None, raw: str) -> None:
        self.speech = speech
        self.scene = scene
        self.raw = raw


def run(provider: LLMProvider, question: str, app: str, memory: str,
        image_b64: str | None, prev_scene: Scene | None,
        say, cancel) -> GraphicsResult:
    """Stream one graphics answer: say(text) is called with speakable
    chunks as they arrive; returns the parsed result. When the model emits
    no usable scene, scene is None and the speech still went out."""
    filt = SceneFilter()
    raw: list[str] = []
    speech: list[str] = []
    for chunk in stream_scene(provider, question, app, memory, image_b64,
                              prev_scene):
        if cancel.is_set():
            break
        raw.append(chunk)
        text = filt.feed(chunk)
        if text:
            speech.append(text)
            say(text)
    tail = filt.flush()
    if tail:
        speech.append(tail)
        say(tail)
    full = "".join(raw)
    data = extract_spec(full)
    scene = spec.from_dict(data) if data else None
    if scene is not None and not scene.items and not scene.title:
        scene = None                    # an empty object is not a visual
    return GraphicsResult("".join(speech), scene, full)
