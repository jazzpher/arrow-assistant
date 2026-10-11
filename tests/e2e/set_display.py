"""Set the CI runner's display to a given resolution and scaling (Windows).

    python tests/e2e/set_display.py --width 1920 --height 1080 --scale 150 --out e2e-out

Resolution: ChangeDisplaySettingsExW (live, no re-login).
Scaling:    DisplayConfigSetDeviceInfo with the (undocumented but stable since
            Windows 10 1703) DPI-scale device-info types -3/-4 that the Settings
            app uses; it applies live to processes started afterwards. Registry
            LogPixels would need a re-login, which a runner cannot do.

Writes <out>/display_setup.json and, when the real scaling could not be
applied, appends QT_SCALE_FACTOR=<scale/100> to $GITHUB_ENV so the e2e
still exercises the overlay's devicePixelRatio path (recorded as simulated).
Never fails the job: the e2e's display_config check judges the outcome.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

DPI_VALS = [100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500]


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD),
    ]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", ctypes.c_uint32),
                ("modeInfoIdx", ctypes.c_uint32), ("statusFlags", ctypes.c_uint32)]


class PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", SOURCE_INFO), ("targetInfo", ctypes.c_byte * 48),
                ("flags", ctypes.c_uint32)]


class HEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int32), ("size", ctypes.c_uint32),
                ("adapterId", LUID), ("id", ctypes.c_uint32)]


class DPI_GET(ctypes.Structure):
    _fields_ = [("header", HEADER), ("minScaleRel", ctypes.c_int32),
                ("curScaleRel", ctypes.c_int32), ("maxScaleRel", ctypes.c_int32)]


class DPI_SET(ctypes.Structure):
    _fields_ = [("header", HEADER), ("scaleRel", ctypes.c_int32)]


user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def current_mode() -> dict:
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))   # ENUM_CURRENT_SETTINGS
    return {"w": dm.dmPelsWidth, "h": dm.dmPelsHeight, "bpp": dm.dmBitsPerPel,
            "hz": dm.dmDisplayFrequency}


def list_modes() -> list[str]:
    out, i = set(), 0
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    while user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
        out.add(f"{dm.dmPelsWidth}x{dm.dmPelsHeight}")
        i += 1
    return sorted(out, key=lambda s: tuple(int(x) for x in s.split("x")))


def set_resolution(w: int, h: int) -> dict:
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))
    dm.dmPelsWidth, dm.dmPelsHeight = w, h
    dm.dmFields = 0x00080000 | 0x00100000          # DM_PELSWIDTH | DM_PELSHEIGHT
    CDS_UPDATEREGISTRY, CDS_TEST = 0x1, 0x2
    test = user32.ChangeDisplaySettingsExW(None, ctypes.byref(dm), None, CDS_TEST, None)
    rc = user32.ChangeDisplaySettingsExW(None, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None)
    return {"test_rc": test, "rc": rc}   # 0 = DISP_CHANGE_SUCCESSFUL


def _sources() -> list[SOURCE_INFO]:
    npaths, nmodes = ctypes.c_uint32(), ctypes.c_uint32()
    QDC_ONLY_ACTIVE_PATHS = 2
    rc = user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(npaths),
                                            ctypes.byref(nmodes))
    if rc:
        raise OSError(f"GetDisplayConfigBufferSizes rc={rc}")
    paths = (PATH_INFO * npaths.value)()
    modes = (ctypes.c_byte * (64 * max(1, nmodes.value)))()
    rc = user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(npaths), paths,
                                   ctypes.byref(nmodes), modes, None)
    if rc:
        raise OSError(f"QueryDisplayConfig rc={rc}")
    return [paths[i].sourceInfo for i in range(npaths.value)]


def get_scale(src: SOURCE_INFO) -> dict:
    g = DPI_GET()
    g.header.type, g.header.size = -3, ctypes.sizeof(DPI_GET)
    g.header.adapterId, g.header.id = src.adapterId, src.id
    rc = user32.DisplayConfigGetDeviceInfo(ctypes.byref(g.header))
    if rc:
        raise OSError(f"DisplayConfigGetDeviceInfo rc={rc}")
    rec = abs(g.minScaleRel)                      # recommended = index 0 relative
    cur = rec + g.curScaleRel
    mx = rec + g.maxScaleRel
    return {"recommended": DPI_VALS[rec], "current": DPI_VALS[min(cur, len(DPI_VALS) - 1)],
            "max": DPI_VALS[min(mx, len(DPI_VALS) - 1)], "_rec_idx": rec,
            "raw": [g.minScaleRel, g.curScaleRel, g.maxScaleRel]}


def set_scale(src: SOURCE_INFO, percent: int) -> dict:
    info = get_scale(src)
    target = DPI_VALS.index(percent) - info["_rec_idx"]
    s = DPI_SET()
    s.header.type, s.header.size = -4, ctypes.sizeof(DPI_SET)
    s.header.adapterId, s.header.id = src.adapterId, src.id
    s.scaleRel = target
    rc = user32.DisplayConfigSetDeviceInfo(ctypes.byref(s.header))
    return {"rc": rc, "scaleRel": target, "before": {k: v for k, v in info.items() if k[0] != "_"}}


def primary_dpi() -> list[int]:
    """[dpiX, dpiY] of the primary monitor (MDT_EFFECTIVE_DPI)."""
    u, sh = ctypes.windll.user32, ctypes.windll.shcore
    u.MonitorFromPoint.restype = ctypes.c_void_p
    u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    sh.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    hmon = u.MonitorFromPoint(wintypes.POINT(0, 0), 1)   # MONITOR_DEFAULTTOPRIMARY
    x, y = ctypes.c_uint(), ctypes.c_uint()
    sh.GetDpiForMonitor(hmon, 0, ctypes.byref(x), ctypes.byref(y))
    return [x.value, y.value]


def monitor_dpi() -> list[int]:
    """Effective DPI of the primary monitor as a per-monitor-aware process sees it."""
    return primary_dpi()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--scale", type=int, default=0)
    ap.add_argument("--out", default="e2e-out")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rep: dict = {"requested": {"w": a.width, "h": a.height, "scale": a.scale}}
    if sys.platform != "win32":
        print("not Windows; nothing to do")
        return 0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        pass
    rep["before"] = {"mode": current_mode(), "dpi": monitor_dpi()}
    try:
        rep["modes_available"] = list_modes()
    except Exception as exc:  # noqa: BLE001
        rep["modes_available"] = f"error {exc}"
    if a.width and a.height:
        try:
            rep["set_resolution"] = set_resolution(a.width, a.height)
        except Exception as exc:  # noqa: BLE001
            rep["set_resolution"] = f"error {type(exc).__name__}: {exc}"
        time.sleep(2)
    if a.scale:
        try:
            srcs = _sources()
            rep["sources"] = len(srcs)
            rep["set_scale"] = [set_scale(s, a.scale) for s in srcs]
            time.sleep(3)
            rep["scale_after"] = [{k: v for k, v in get_scale(s).items() if k[0] != "_"}
                                  for s in srcs]
        except Exception as exc:  # noqa: BLE001
            rep["set_scale"] = f"error {type(exc).__name__}: {exc}"
    rep["after"] = {"mode": current_mode(), "dpi": monitor_dpi()}
    real_scale = round(rep["after"]["dpi"][0] / 96 * 100)
    rep["real_scale_percent"] = real_scale
    rep["simulated"] = False
    if a.scale and real_scale != a.scale:
        rep["simulated"] = True
        ge = os.environ.get("GITHUB_ENV")
        if ge:
            with open(ge, "a", encoding="utf-8") as f:
                f.write(f"QT_SCALE_FACTOR={a.scale / 100}\n")
    (out / "display_setup.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps(rep, indent=2))
    print(f"::notice title=display setup::requested {a.width}x{a.height}@{a.scale}% -> "
          f"{rep['after']['mode']['w']}x{rep['after']['mode']['h']} dpi={rep['after']['dpi']} "
          f"({real_scale}%) simulated={rep['simulated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
