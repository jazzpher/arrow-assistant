"""Real-time graphics engine: voice-asked, LLM-authored, on-the-fly visuals.

The user asks out loud for a drawing, diagram, or 3D view of whatever they
are looking at (like Clicky's "real-time graphics" demo). The LLM answers
with a short spoken explanation plus a [SCENE] JSON spec; this package
validates the spec and renders it on screen - 2D diagrams with QPainter,
interactive 3D with a small software renderer (no OpenGL dependency).
"""
