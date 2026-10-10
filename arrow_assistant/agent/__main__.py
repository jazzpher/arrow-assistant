"""CLI for agent mode, no voice or Qt needed.

    python -m arrow_assistant.agent "open notepad and type hello" --dry-run
    python -m arrow_assistant.agent "..." --mode step --max-steps 15
"""
from __future__ import annotations

import argparse
import sys

from .. import config, capture
from .activity import ActivityMonitor
from .actlog import ActionLog
from .approval import AutoApprover, ConsoleApprover
from .executor import make_executor
from .hotkeys import AgentHotkeys
from .loop import AgentLoop, LoopConfig
from .panic import PanicSwitch
from .planner import Planner, ProviderChain
from .risk import assess, is_sensitive_window
from .state import StateStore
from .ui import ConsoleUI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Arrow agent (console)")
    ap.add_argument("task", nargs="?", help="what to do")
    ap.add_argument("--mode", choices=config.AGENT_MODES, default=config.agent_mode())
    ap.add_argument("--max-steps", type=int, default=config.agent_max_steps())
    ap.add_argument("--dry-run", action="store_true",
                    help="plan and preview only; never touches mouse/keyboard")
    ap.add_argument("--yes", action="store_true",
                    help="with --dry-run only: auto-approve previews")
    ap.add_argument("--scope", default=config.get_key("ARROW_AGENT_SCOPE") or "",
                    help="comma list of allowed apps (exe names); other apps always ask")
    ap.add_argument("--resume", action="store_true", help="continue the saved task")
    args = ap.parse_args(argv)
    if not args.task and not args.resume:
        ap.error("give a task, or --resume")
    if args.yes and not args.dry_run:
        ap.error("--yes is only allowed together with --dry-run")
    from . import privacy
    if not args.dry_run and not privacy.ensure_console():
        return 3
    providers = config.select_agent_providers()
    if not providers:
        print("No API key. Put GEMINI_API_KEY in .env", file=sys.stderr)
        return 2
    capture.set_dpi_awareness()
    panic = PanicSwitch()
    try:
        AgentHotkeys({config.panic_hotkey(): lambda: panic.stop("panic hotkey")}).start()
    except Exception as exc:   # no keyboard backend: failsafe corner still works
        print(f"[arrow] panic hotkey unavailable ({exc}); use Ctrl+C or the screen corner")
    from .stack import AgentStack
    screen = AgentStack._real_screen()   # same UIA wiring as the tray app
    store = StateStore()
    loop = AgentLoop(
        screen, make_executor(args.dry_run, panic), Planner(ProviderChain(providers)),
        AutoApprover() if args.yes else ConsoleApprover(), ui=ConsoleUI(),
        log=ActionLog(), panic=panic, activity=ActivityMonitor(screen.cursor),
        risk=assess, sensitive=is_sensitive_window, store=store,
        config=LoopConfig(max_steps=args.max_steps, mode=args.mode,
                           scope_apps=frozenset(a.strip().lower() for a in args.scope.split(",") if a.strip())))
    resume = store.load() if args.resume else None
    if args.resume and resume is None:
        print("Nothing to resume.")
        return 1
    result = loop.run(args.task or "", resume=resume)
    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
