"""Real-Windows end-to-end checks for Arrow (run by .github/workflows/e2e-windows.yml).

    python tests/e2e/win_e2e.py --out e2e-out

NOT a pytest module (tests/e2e/conftest.py keeps pytest away from it).
Windows only; on other platforms it exits 0 with a notice. Every check
is recorded in <out>/results.json and printed as a GitHub annotation.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RESULTS: list[dict] = []
OUT = Path("e2e-out")


def _one_line(s: str) -> str:
    return str(s).replace("\r", " ").replace("\n", " | ")[:900]


def record(name: str, ok: bool | None, detail: str = "", **extra) -> None:
    status = "skip" if ok is None else ("pass" if ok else "fail")
    RESULTS.append({"check": name, "status": status, "detail": detail, **extra})
    kind = "error" if ok is False else "notice"
    print(f"::{kind} title=e2e {name} {status.upper()}::{_one_line(detail)}", flush=True)


def run_check(name: str, fn) -> object:
    t0 = time.monotonic()
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        (OUT / f"{name}.traceback.txt").write_text(tb, encoding="utf-8")
        record(name, False, f"{type(exc).__name__}: {exc} (after {time.monotonic() - t0:.1f}s)")
        return None


# ---------------------------------------------------------------------------
def launch_notepad():
    import uiautomation as auto
    proc = subprocess.Popen(["notepad.exe"])
    win = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and win is None:
        for w in auto.GetRootControl().GetChildren():
            try:
                if w.ControlTypeName == "WindowControl" and (
                        w.ClassName == "Notepad" or "Notepad" in (w.Name or "")):
                    win = w
                    break
            except Exception:
                pass
        time.sleep(0.5)
    if win is None:
        record("notepad_launch", False, "no Notepad window within 20s")
        return proc, None
    try:
        win.SetActive()
        win.SetTopmost(False)
        win.MoveWindow(40, 40, 900, 600)
        win.SetActive()
    except Exception:
        pass
    time.sleep(1.0)
    r = win.BoundingRectangle
    record("notepad_launch", True,
           f"window '{win.Name}' class={win.ClassName} rect=({r.left},{r.top},{r.right},{r.bottom})")
    return proc, win


def screenshot_primary(name: str):
    from arrow_assistant import capture
    mon, img = capture.primary_shot()
    img.save(OUT / name)
    return mon, img


def dump_elements(win):
    from arrow_assistant.agent.coords import Frame
    from arrow_assistant.agent.uia import filter_and_number, walk_controls
    from arrow_assistant import capture
    mon = capture.list_monitors()[0]
    raw = walk_controls(win, budget_s=6.0)
    frame = Frame.for_monitor(mon)
    numbered = filter_and_number(raw, frame)
    data = {
        "monitor": mon,
        "raw": [r for r in raw if r["name"] or r["role"] in ("MenuItemControl", "ButtonControl",
                                                            "DocumentControl", "EditControl")],
        "arrow_numbered": [{"id": e.id, "name": e.name, "role": e.role, "rect": list(e.rect),
                            "center": list(e.center), "enabled": e.enabled} for e in numbered],
    }
    (OUT / "notepad_elements.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    names = [e["name"] for e in data["arrow_numbered"]]
    file_el = find_el(data, "File")
    record("uia_dump", bool(file_el), f"{len(raw)} raw, {len(numbered)} numbered; "
           f"File={file_el['rect'] if file_el else None}; names={names[:25]}")
    return data


def find_el(data, name: str, roles=("MenuItem", "Button", "MenuItemControl", "ButtonControl")):
    for e in data.get("arrow_numbered", []):
        if e["name"].lower() == name.lower() and e["role"] in roles:
            return e
    for e in data.get("raw", []):
        if (e["name"] or "").lower() == name.lower() and e["role"] in roles:
            return {"name": e["name"], "role": e["role"], "rect": list(e["rect"]),
                    "center": [(e["rect"][0] + e["rect"][2]) // 2, (e["rect"][1] + e["rect"][3]) // 2]}
    return None


def check_overlay(qapp, target):
    from PyQt6.QtCore import QEventLoop, QTimer
    from arrow_assistant.overlay import ArrowOverlay
    from arrow_assistant.points import Point
    from arrow_assistant import capture
    cx, cy = target["center"]
    screens = [(s.name(), s.geometry().getRect(), s.devicePixelRatio()) for s in qapp.screens()]
    mon_before, before = screenshot_primary("overlay_before.png")
    ov = ArrowOverlay(qapp)
    ov.point_at([Point(cx, cy, "File menu")])

    def pump(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
    pump(1500)
    mon, after = screenshot_primary("overlay_arrow.png")
    ov.clear()
    pump(200)
    # pixel check: the arrow body is drawn from the tip down/right (~25x40 px)
    lx, ly = cx - mon["left"], cy - mon["top"]
    box = (max(0, lx - 2), max(0, ly - 2), min(after.width, lx + 26), min(after.height, ly + 42))
    a = after.crop(box).convert("RGB")
    b = before.crop(box).convert("RGB")
    blue = sum(1 for p in a.getdata()
               if abs(p[0] - 37) < 40 and abs(p[1] - 99) < 40 and abs(p[2] - 235) < 40)
    changed = sum(1 for p, q in zip(a.getdata(), b.getdata())
                  if sum(abs(i - j) for i, j in zip(p, q)) > 60)
    # where did the blue actually land on the whole screen? (catches offset bugs)
    full = after.convert("RGB")
    xs, ys = [], []
    W, H = full.size
    px = full.load()
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            r, g, bb = px[x, y]
            if abs(r - 37) < 30 and abs(g - 99) < 30 and abs(bb - 235) < 30:
                xs.append(x); ys.append(y)
    blue_bbox = [min(xs), min(ys), max(xs), max(ys)] if xs else None
    ok = blue > 40 and changed > 60
    record("overlay_arrow", ok,
           f"target=({cx},{cy}) blue_px_in_box={blue} changed_px={changed} "
           f"blue_bbox_on_screen={blue_bbox} screens={screens} mon={mon}",
           target=[cx, cy], blue_bbox=blue_bbox)


def check_tts():
    from arrow_assistant import tts
    text = "Kumusta! Click mo yung File menu para mag-open ng file."
    t0 = time.monotonic()
    mp3 = tts.synthesize_edge(text, tts.TAGALOG_VOICE)
    dt = time.monotonic() - t0
    (OUT / "tts_fil.mp3").write_bytes(mp3)
    import miniaudio
    dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1)
    secs = dec.num_frames / dec.sample_rate
    play = "not tried"
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        stop = threading.Event()
        th = threading.Thread(target=lambda: tts.play_mp3(mp3, stop), daemon=True)
        th.start()
        th.join(2.0)
        stop.set()
        play = f"started ok ({len(devs)} devices)"
    except Exception as exc:  # noqa: BLE001
        play = f"no playback ({type(exc).__name__}: {str(exc)[:80]})"
    ok = len(mp3) > 2000 and secs > 1.0
    record("tts_edge_miniaudio", ok,
           f"voice={tts.TAGALOG_VOICE} mp3={len(mp3)}B synth={dt:.2f}s decoded={secs:.2f}s "
           f"@{dec.sample_rate}Hz playback: {play}")


def check_app_alive():
    env = dict(os.environ)
    env["ARROW_SKIP_SETUP"] = "1"
    for k in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY"):
        env.pop(k, None)
    env["PYTHONUNBUFFERED"] = "1"
    log = open(OUT / "app_stdout_stderr.txt", "w", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "arrow_assistant"], cwd=str(ROOT), env=env,
                         stdout=log, stderr=subprocess.STDOUT)
    alive_at = None
    for i in range(20):
        time.sleep(0.5)
        if p.poll() is not None:
            break
    else:
        alive_at = 10
    rc = p.poll()
    if rc is None:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        p.wait(10)
    log.close()
    logs = []
    for src, dst in ((Path(os.environ.get("APPDATA", "")) / "Arrow" / "arrow.log", "appdata_arrow.log"),
                     (Path.home() / "Documents" / "Arrow Memory" / "arrow.log", "documents_arrow.log")):
        if src.is_file():
            (OUT / dst).write_bytes(src.read_bytes())
            logs.append(dst)
    out = (OUT / "app_stdout_stderr.txt").read_text(encoding="utf-8", errors="replace")
    ok = rc is None and "Traceback" not in out
    record("app_tray_alive", ok,
           f"alive_10s={rc is None} exit={rc} logs={logs} output_tail={out[-400:]}")


def check_agent(win, data):
    from arrow_assistant.agent.actions import Action
    from arrow_assistant.agent.approval import AutoApprover
    from arrow_assistant.agent.executor import DryRunExecutor
    from arrow_assistant.agent.loop import AgentLoop, LoopConfig
    from arrow_assistant.agent.planner import Completion, Verdict
    from arrow_assistant.agent.risk import RiskContext, assess, is_sensitive_window
    from arrow_assistant.agent.stack import AgentStack
    import re

    class ScriptedPlanner:
        def __init__(self):
            self.seen = []
            self.calls = 0

        def make_plan(self, task, ctx):
            return ["click File"]

        def next_action(self, task, plan, history, ctx):
            self.calls += 1
            self.seen.append(ctx.elements_text)
            comp = Completion("{}", "scripted", "none", 0.0)
            if self.calls == 1:
                m = re.search(r"^\s*(\d+): MenuItem 'File'", ctx.elements_text, re.M)
                if not m:
                    return Action("fail", message="File not in element list"), comp
                return Action("click", element=int(m.group(1)), label="File"), comp
            return Action("done", message="clicked File"), comp

        def verify(self, task, history, ctx):
            return Verdict(True)

    try:
        win.SetActive()
    except Exception:
        pass
    time.sleep(0.5)
    screen = AgentStack._real_screen()
    planner = ScriptedPlanner()
    exe = DryRunExecutor()
    os.environ.setdefault("ARROW_LOG_DIR", str(OUT / "agent_logs"))
    loop = AgentLoop(screen, exe, planner, AutoApprover(), risk=assess,
                     sensitive=is_sensitive_window,
                     config=LoopConfig(mode="auto", max_steps=4, use_plan=False,
                                       verify_done=False, settle_s=0.3))
    res = loop.run("click the File menu in Notepad")
    (OUT / "agent_elements_text.txt").write_text("\n---\n".join(planner.seen), encoding="utf-8")
    file_el = find_el(data, "File")
    pt = exe.performed[0][1] if exe.performed else None
    inside = bool(pt and file_el and file_el["rect"][0] <= pt[0] <= file_el["rect"][2]
                  and file_el["rect"][1] <= pt[1] <= file_el["rect"][3])
    record("agent_dry_run", res.status == "done" and inside,
           f"status={res.status} msg={res.message} steps={res.steps} click_pt={pt} "
           f"file_rect={file_el['rect'] if file_el else None} fg_app={screen.foreground()[:2]}")
    # risk gate on the real Notepad window identity
    from arrow_assistant import capture
    app, title = capture.foreground_app(), capture.foreground_title()
    cases = [
        (Action("click", element=1, label="File"), "File", False, "safe"),
        (Action("click", element=1, label="Delete"), "Delete", False, "confirm"),
        (Action("type", text="hunter2"), "Password", True, "block"),
        (Action("key", keys=("ctrl", "alt", "delete")), "", False, "block"),
    ]
    got = []
    for act, label, pw, want in cases:
        a = assess(RiskContext(act, app, title, label, "t", (10, 10), frozenset(), [], pw))
        got.append((act.describe(), a.level, want))
    ok = all(lv == want for _, lv, want in got)
    record("agent_risk_gate", ok, f"app={app} title={title!r} cases={got}")


def check_real_model(qapp, data):
    """Teach-mode path with the real model (only when GEMINI_API_KEY is set)."""
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        record("real_model_teach", None, "GEMINI_API_KEY secret not set; skipped")
        return
    from arrow_assistant import ai, capture, config, kb
    from arrow_assistant.points import parse_points
    provider = config.select_llm()
    if provider is None:
        record("real_model_teach", False, "GEMINI_API_KEY set but select_llm() returned None")
        return
    targets = [("File", "Where do I click to open a file? / Nasaan ang File menu?")]
    targets.append(("Edit", "Nasaan ang Edit menu? Where do I click to find Undo or Replace?"))
    if find_el(data, "Settings"):
        targets.append(("Settings", "Where are the Notepad settings? Nasaan ang Settings?"))
    elif find_el(data, "Format"):
        targets.append(("Format", "Where do I change the font? Nasaan ang Format menu?"))
    else:
        targets.append(("View", "Where do I turn on the status bar? Nasaan ang View menu?"))
    MARGIN = 12
    rows, hits = [], 0
    for name, question in targets:
        el = find_el(data, name)
        app = capture.foreground_app()
        mon, img = capture.primary_shot()
        image_b64 = capture.encode_for_model(img)
        s = capture.scale_factor(img, mon)
        enc_w, enc_h = capture.encoded_size(img)
        t0 = time.monotonic()
        first = None
        chunks = []
        err = ""
        try:
            for chunk in ai.stream_answer(provider, question, app, "", kb.lookup(app), image_b64,
                                          image_size=(enc_w, enc_h)):
                if first is None:
                    first = time.monotonic() - t0
                chunks.append(chunk)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
        total = time.monotonic() - t0
        text = "".join(chunks)
        spoken, pts = parse_points(text)
        mapped = []
        for p in pts:   # identical to ArrowApp._pipeline
            x = min(max(p.x, 0), enc_w - 1)
            y = min(max(p.y, 0), enc_h - 1)
            mapped.append([mon["left"] + round(x / s), mon["top"] + round(y / s), p.label])
        hit = False
        if el and mapped:
            l, t, r, b = el["rect"]
            hit = any(l - MARGIN <= m[0] <= r + MARGIN and t - MARGIN <= m[1] <= b + MARGIN
                      for m in mapped[:1])
        hits += hit
        rows.append({"target": name, "question": question, "uia_rect": el["rect"] if el else None,
                     "answer_raw": text, "spoken": spoken,
                     "points_model": [[p.x, p.y, p.label] for p in pts],
                     "points_screen": mapped, "hit": hit, "error": err,
                     "first_chunk_s": round(first, 2) if first is not None else None,
                     "total_s": round(total, 2), "image": [enc_w, enc_h], "scale": s})
        time.sleep(4)   # free-tier pacing
    (OUT / "real_model.json").write_text(json.dumps(
        {"provider": provider.name, "model": provider.model, "margin_px": MARGIN, "rows": rows},
        indent=2, ensure_ascii=False), encoding="utf-8")
    summary = "; ".join(f"{r['target']}: hit={r['hit']} pt={r['points_screen'][:1]} rect={r['uia_rect']} "
                        f"t={r['total_s']}s{' ERR ' + r['error'] if r['error'] else ''}" for r in rows)
    record("real_model_teach", hits >= 1 and not any(r["error"] for r in rows),
           f"{provider.name}/{provider.model} accuracy {hits}/{len(rows)}; {summary}",
           accuracy=f"{hits}/{len(rows)}", rows=rows)


# ---------------------------------------------------------------------------
def main() -> int:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="e2e-out")
    args = ap.parse_args()
    OUT = Path(args.out)
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        print("::notice::win_e2e: not Windows, nothing to do")
        return 0
    from PyQt6.QtWidgets import QApplication
    qapp = QApplication(sys.argv)          # same order as ArrowApp: Qt first,
    from arrow_assistant import capture    # then the DPI call
    capture.set_dpi_awareness()

    proc, win = run_check("notepad_launch", launch_notepad) or (None, None)
    shot = run_check("screenshot", lambda: screenshot_primary("screen_notepad.png"))
    if shot:
        mon, img = shot
        record("screenshot", img.size[0] > 200, f"{img.size[0]}x{img.size[1]} mon={mon} "
               f"monitors={capture.list_monitors()} fg={capture.foreground_app()} "
               f"'{capture.foreground_title()}'")
    data = run_check("uia_dump", lambda: dump_elements(win)) if win else None
    target = find_el(data, "File") if data else None
    if target:
        run_check("overlay_arrow", lambda: check_overlay(qapp, target))
    else:
        record("overlay_arrow", False, "no File menu element to aim at")
    run_check("tts_edge_miniaudio", check_tts)
    if win and data:
        run_check("agent_dry_run", lambda: check_agent(win, data))
        try:
            win.SetActive()
        except Exception:
            pass
        time.sleep(0.5)
        run_check("real_model_teach", lambda: check_real_model(qapp, data))
    run_check("app_tray_alive", check_app_alive)
    if proc:
        proc.kill()

    failed = [r for r in RESULTS if r["status"] == "fail"]
    (OUT / "results.json").write_text(json.dumps({
        "run_id": os.environ.get("GITHUB_RUN_ID"), "sha": os.environ.get("GITHUB_SHA"),
        "python": sys.version.split()[0], "passed": len([r for r in RESULTS if r["status"] == "pass"]),
        "failed": len(failed), "skipped": len([r for r in RESULTS if r["status"] == "skip"]),
        "checks": RESULTS}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"::notice title=e2e summary::{len(RESULTS) - len(failed)}/{len(RESULTS)} ok "
          f"(fails: {[r['check'] for r in failed]})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
