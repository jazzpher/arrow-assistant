"""One-command checkup for the PC. Run it, send back the report.

    python -m arrow_assistant.agent.selftest
    python -m arrow_assistant.agent.selftest --bench      # also scores your free models
    python -m arrow_assistant.agent.selftest --live       # tiny real task in Notepad (asks first)

It never clicks or types anything unless you pass --live and approve each step.
"""
from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path
from typing import Callable

from .. import config

Check = tuple[str, bool, str]


def run_checks(providers=None, env=None, shot_fn: Callable | None = None,
               uia_fn: Callable | None = None, ping_fn: Callable | None = None,
               plat: str | None = None) -> list[Check]:
    out: list[Check] = []
    plat = plat or sys.platform
    out.append(("Windows", plat == "win32", plat))
    out.append(("Python 3.10+", sys.version_info >= (3, 10), platform.python_version()))
    for mod in ("PyQt6", "pynput", "mss", "pyautogui", "requests", "PIL"):
        try:
            __import__(mod)
            out.append((f"import {mod}", True, "ok"))
        except Exception as exc:  # noqa: BLE001
            out.append((f"import {mod}", False, str(exc)[:80]))
    providers = config.select_agent_providers() if providers is None else providers
    out.append(("agent API key", bool(providers),
                ", ".join(p.name for p in providers) or "none: set GEMINI_API_KEY in .env"))
    if shot_fn:
        try:
            mon, img = shot_fn()
            out.append(("screenshot", True, f"{img.size[0]}x{img.size[1]} on monitor {mon.get('left')},{mon.get('top')}"))
        except Exception as exc:  # noqa: BLE001
            out.append(("screenshot", False, str(exc)[:100]))
    if uia_fn:
        try:
            n = len(uia_fn())
            out.append(("UI Automation elements in focused window", n > 0,
                        f"{n} found" + ("" if n else " (app may not expose UIA; pixel mode will be used)")))
        except Exception as exc:  # noqa: BLE001
            out.append(("UI Automation", False, str(exc)[:100]))
    if ping_fn:
        for p in providers:
            try:
                t0 = time.monotonic()
                ok, detail = ping_fn(p)
                out.append((f"{p.name} reachable", ok, f"{detail} ({time.monotonic() - t0:.1f}s)"))
            except Exception as exc:  # noqa: BLE001
                out.append((f"{p.name} reachable", False, str(exc)[:100]))
    return out


def render(checks: list[Check]) -> str:
    lines = ["# Arrow agent self-test", ""]
    for name, ok, detail in checks:
        lines.append(f"- {'PASS' if ok else 'FAIL'} {name}: {detail}")
    bad = [c for c in checks if not c[1]]
    lines += ["", f"Result: {len(checks) - len(bad)}/{len(checks)} checks passed."]
    return "\n".join(lines)


def _ping(provider):
    from .planner import ProviderChain
    chain = ProviderChain([provider], timeout=30)
    comp = chain.complete("Reply with the JSON {\"ok\": true}", [{"type": "text", "text": "ping"}], 40)
    return ("ok" in comp.text, comp.text.strip()[:40])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bench", action="store_true", help="also run the model benchmark")
    ap.add_argument("--live", action="store_true", help="tiny real task in Notepad, step-confirm")
    args = ap.parse_args(argv)
    from .. import capture
    capture.set_dpi_awareness()
    uia_fn = None
    try:
        import uiautomation  # noqa: F401
        from .coords import Frame
        from .uia import collect_foreground

        def uia_fn():
            mon, _ = capture.primary_shot()
            return collect_foreground(Frame.for_monitor(mon))
    except ImportError:
        pass
    checks = run_checks(shot_fn=capture.primary_shot, uia_fn=uia_fn, ping_fn=_ping)
    report = render(checks)
    print(report)
    out_dir = Path.home() / "Documents" / "Arrow Logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "selftest.md").write_text(report, encoding="utf-8")
    if args.bench:
        from . import bench
        bench.main(["--out", str(out_dir / "bench")])
    if args.live:
        from . import privacy
        print("\nLIVE test: screenshots of your screen go to the cloud model. "
              "Close every private window/tab first; only Notepad should be visible.")
        if not privacy.ensure_console():
            return 3
        import subprocess
        subprocess.Popen(["notepad.exe"])
        time.sleep(1.5)
        from . import __main__ as cli
        return cli.main(["open notepad is already open: click in the text area and type: hello from Arrow",
                         "--mode", "step", "--max-steps", "8", "--scope", "notepad.exe"])
    print(f"\nSaved to {out_dir / 'selftest.md'}. Send that file back.")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
