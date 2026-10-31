# Real-time graphics

Teach mode can now **draw on demand**. Hold `Ctrl+Alt+Space` and ask for a
visual - "draw the full jet in 3D and explain how the engines work",
"show me a diagram of how a tensor works", "iguhit mo yung flow" - and a
card appears on screen with a generated visual while Arrow talks you
through it. Inspired by Clicky's "real-time graphics" demo; this is our
own, much simpler engine.

## What it is (and is not)

- The LLM authors a small **scene spec** (JSON: boxes, cylinders, spheres,
  cones, tori, planes, arrows, labels for 3D; rects, lines, text, polys
  for 2D). Arrow validates and renders it locally. No pre-rendered
  assets, and no code, shader, URL, or file path from the model is ever
  used - the spec is plain bounded data.
- 3D renders on the **GPU** (OpenGL 3.3 via moderngl: Phong shading,
  smooth meshes, floor grid, hover picking) whenever a 3.3 context can be
  created, and on the built-in **CPU** painter otherwise. The card's
  title bar always shows which one is live (GPU / CPU badge), so a
  fallback is never silent.
- It composes simple primitives into labelled explanatory models - think
  whiteboard sketch in 3D with real lighting, not a game engine. It will
  not reproduce a photoreal F-35B; it will show you the lift fan, the
  swivel nozzle, and which way the air goes.
- 2D covers diagrams, flows, charts, and quick sketches on a 1000x1000
  canvas.

## The card

- Stays on top, bottom-right of the screen. Drag the title bar to move it,
  `Esc` or the `x` closes it.
- **3D scenes:** drag the canvas to rotate, mouse wheel to zoom,
  double-click to pause/resume the slow auto-rotation. On the GPU
  renderer, hovering a part highlights it and shows its name at the
  bottom - visual feedback only, it does not trigger an AI answer.
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
  meshgen.py   parametric meshes (box/sphere/cylinder/cone/torus/plane)
  camera.py    orbit camera matrices, projection, picking rays (numpy)
  glrender.py  GPU renderer: Phong, grid, pick pass, headless EGL path
  glwindow.py  the GPU canvas (offscreen render -> QImage presentation)
  geo3d.py     pure-python 3D math for the CPU fallback
  render.py    QPainter rendering for 2D and for 3D CPU fallback
  window.py    the on-screen card (move/close/rotate/zoom, GPU/CPU badge)
```

GPU presentation note: the GL work happens on a standalone moderngl
context rendering to an offscreen framebuffer; the resulting frame is
blitted as a QImage inside a plain QWidget. moderngl and Qt's compositor
never share a GL context (sharing one crashed with llvmpipe), and this
makes the widget path identical to the headless/CI path.

Cost honesty: presentation reads each frame back from GPU to CPU memory
(~1 MB per frame at card size) before Qt blits it, so this path is
heavier than a native swapchain. The install is tiny, but do not mistake
that for free runtime: on a real 8 GB Windows machine, frame rate and
memory under load are unmeasured, and llvmpipe numbers say nothing about
actual drivers. If the card ever feels heavy, the honest lever is
lowering the autorotate rate or the card size, not denying the cost.
Labels are placed near their part's top anchor and pushed apart when
they collide; in dense scenes a label can sit noticeably away from its
part. That is a known readability tradeoff, not a collision-free
guarantee.

Model output is treated as untrusted data: item counts, string lengths,
coordinates, and mesh detail are capped in `spec.py`, colors are
restricted to hex/named values, and nothing in a spec can name a file,
URL, or code path.

Limits: scene JSON costs more tokens than a spoken answer
(`SCENE_MAX_TOKENS` in engine.py), so very elaborate scenes can truncate
on a tight quota; a malformed or cut-off spec degrades to speech-only
(the answer is never lost).

Testing: mesh/camera math is unit-tested without GL; the GPU path has a
required CI capability gate (tests/test_graphics_gl.py runs with
ARROW_REQUIRE_GL=1 on Ubuntu+Mesa llvmpipe over EGL, failing if the GPU
pipeline cannot render or pick), and the widget lifecycle (frame
presentation, view changes, CPU fallback honesty) is covered there too.
Not covered: native Windows WGL/driver behavior, real-GPU performance,
and the native overlay interaction (always-on-top, drag, wheel) - run
tests/e2e/win_e2e.py on a real Windows machine; it records which
renderer (gpu|cpu) the machine actually got.
