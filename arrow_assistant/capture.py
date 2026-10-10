"""Screen capture and foreground-app detection.

mss handles multi-monitor grabs; virtual-desktop coordinates (what the
model points at) are mapped back per monitor. On Windows we ask for
per-monitor-v2 DPI awareness so pixel coordinates stay honest on
mixed-scaling setups.
"""
from __future__ import annotations

import base64
import ctypes
import io
import sys

from PIL import Image

MAX_EDGE = 1568  # model-friendly longest edge; keeps tokens + latency down


def set_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE_V2-ish
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def list_monitors() -> list[dict]:
    import mss
    with mss.mss() as sct:
        return [dict(m) for m in sct.monitors[1:]]  # [0] is the union


def capture_all_screens() -> list[tuple[dict, Image.Image]]:
    """Grab every physical monitor. Returns [(monitor, PIL image)]."""
    import mss
    shots = []
    with mss.mss() as sct:
        for mon in sct.monitors[1:]:
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            shots.append((dict(mon), img))
    return shots


def grab_monitor(mon: dict) -> Image.Image:
    import mss
    with mss.mss() as sct:
        raw = sct.grab(mon)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def pick_monitor(monitors: list[dict], x: int, y: int) -> dict:
    """The monitor containing (x, y), else the first one. Pure."""
    for mon in monitors:
        if mon["left"] <= x < mon["left"] + mon["width"] and \
           mon["top"] <= y < mon["top"] + mon["height"]:
            return mon
    return monitors[0]


def primary_shot() -> tuple[dict, Image.Image]:
    """Capture only the monitor containing the cursor (what the user sees)."""
    monitors = list_monitors()
    if len(monitors) == 1:
        mon = monitors[0]
    else:
        x, y = cursor_position()
        mon = pick_monitor(monitors, x, y)
    return mon, grab_monitor(mon)


def cursor_position() -> tuple[int, int]:
    if sys.platform == "win32":
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    return (0, 0)


def encode_for_model(img: Image.Image, max_edge: int = MAX_EDGE) -> str:
    """Resize and base64-encode a screenshot as JPEG for the vision API."""
    w, h = img.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def encoded_size(img: Image.Image, max_edge: int = MAX_EDGE) -> tuple[int, int]:
    """Pixel size of the image as the model will see it (after resize)."""
    w, h = img.size
    scale = min(1.0, max_edge / max(w, h))
    return (round(w * scale), round(h * scale)) if scale < 1.0 else (w, h)


def scale_factor(img: Image.Image, mon: dict, max_edge: int = MAX_EDGE) -> float:
    """Ratio between model coordinates and real virtual-desktop pixels."""
    return min(1.0, max_edge / max(mon["width"], mon["height"]))


def model_to_screen(mx: int, my: int, mon: dict,
                    max_edge: int = MAX_EDGE) -> tuple[int, int]:
    """Map model-space coordinates back to virtual-desktop pixels."""
    s = scale_factor(None, mon, max_edge)
    return (mon["left"] + round(mx / s), mon["top"] + round(my / s))


def foreground_app() -> str:
    """Exe name of the focused window on Windows, e.g. 'excel.exe'."""
    if sys.platform != "win32":
        return "unknown"
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hproc = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
        if not hproc:
            return "unknown"
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(512)
        ctypes.windll.kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size))
        ctypes.windll.kernel32.CloseHandle(hproc)
        import os
        return os.path.basename(buf.value).lower() or "unknown"
    except Exception:
        return "unknown"


def foreground_title() -> str:
    """Title text of the focused window (Windows), else ''."""
    if sys.platform != "win32":
        return ""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value
    except Exception:
        return ""


def foreground_rect() -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the focused window, or None."""
    if sys.platform != "win32":
        return None
    try:
        class RECT(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                        ("r", ctypes.c_long), ("b", ctypes.c_long)]
        rc = RECT()
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc)):
            return None
        return rc.l, rc.t, rc.r, rc.b
    except Exception:
        return None


def monitor_for_point(shots, x: int, y: int):
    for mon, img in shots:
        if mon["left"] <= x < mon["left"] + mon["width"] and \
           mon["top"] <= y < mon["top"] + mon["height"]:
            return mon, img
    return shots[0]
