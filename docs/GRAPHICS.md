# Real-time graphics

Teach mode can now **draw on demand**. Hold `Ctrl+Alt+Space` and ask for a
visual - "draw the full jet in 3D and explain how the engines work",
"show me a diagram of how a tensor works", "iguhit mo yung flow" - and a
card appears on screen with a generated visual while Arrow talks you
through it. Inspired by Clicky's "real-time graphics" demo; this is our
own, much simpler engine.

## What it is (and is not)

- The LLM authors a small **scene spec** (JSON: boxes, cylinders, spheres,
  cones, arrows, labels for 3D; rects, lines, text, polys for 2D). Arrow
  validates and renders it locally. No pre-rendered assets, no code from
  the model is ever executed.
- 3D is a **software renderer** (perspective projection, painter's
  algorithm, flat shading). It composes simple primitives into labelled
  explanatory models - think whiteboard sketch in 3D, not a game engine.
  It will not reproduce a photoreal F-35B; it will show you the lift fan,
  the swivel nozzle, and which way the air goes.
- 2D covers diagrams, flows, charts, and quick sketches on a 1000x1000
  canvas.

## The card

- Stays on top, bottom-right of the screen. Drag the title bar to move it,
  `Esc` or the `x` closes it.
- **3D scenes:** drag the canvas to rotate, mouse wheel to zoom,
  double-click to pause/resume the slow auto-rotation.
- One card at a time: a new visual replaces the old one.

## Follow-ups

The current scene is sent back with your next graphics question, so
"now add the engine" or "make the fan bigger" updates the same visual
instead of starting over.

## Triggering

Routing is keyword-based (deterministic, no extra model call) and
two-tiered. Strong words always trigger it: draw, sketch, diagram,
flowchart, 3D, visualize, illustrate, blueprint, schematic, plus
Tagalog/Taglish forms (iguhit, guhit, larawan, desenyo, pakita kung
paano/hitsura). Weaker nouns - chart, graph, plot, visual - only trigger
when the same sentence asks for one to be made or shown, so "what chart
type should I use in Excel?" or "where is the chart button?" keep the
normal pointing path. A question that only asks to point or explain never
reaches the engine. The graphics call goes through the same
provider chain, usage budgets, and free-model guard as everything else.

## Internals

```
arrow_assistant/graphics/
  trigger.py   wants_graphics(question) -> bool
  engine.py    prompt + streaming; splits speech from the [SCENE] JSON
  parse.py     SceneFilter (stream) + tolerant JSON extraction
  spec.py      validation/coercion/clamping of the model's spec
  geo3d.py     pure-python 3D math + primitive meshes
  render.py    QPainter rendering for 2D and 3D
  window.py    the on-screen card (move/close/rotate/zoom)
```

Model output is treated as untrusted data: item counts, string lengths,
coordinates, and mesh detail are capped in `spec.py`, colors are
restricted to hex/named values, and nothing in a spec can name a file,
URL, or code path.

Limits: scene JSON costs more tokens than a spoken answer
(`SCENE_MAX_TOKENS` in engine.py), so very elaborate scenes can truncate
on a tight quota; a malformed or cut-off spec degrades to speech-only
(the answer is never lost). On Linux CI the renderer is tested offscreen;
the native Windows overlay interaction (always-on-top, drag, wheel) is
not covered there.
