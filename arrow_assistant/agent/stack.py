"""Wires the agent stack for the Qt app (and for tests with fakes)."""
from __future__ import annotations

from .. import config
from .activity import ActivityMonitor
from .actlog import ActionLog
from .approval import APPROVE, SKIP, STOP, QueueApprover
from .controller import AgentController
from .executor import make_executor
from .loop import AgentLoop, LoopConfig
from .panic import PanicSwitch
from .planner import Planner, ProviderChain
from .risk import assess, is_sensitive_window
from .screen import WindowsScreen
from .state import StateStore


class AgentStack:
    def __init__(self, qapp, overlay, speak=None, dry_run: bool = False,
                 providers=None, screen=None, executor_factory=None):
        from .hud import QtAgentUI
        self.providers = providers if providers is not None else config.select_agent_providers()
        self.panic = PanicSwitch()
        self.approver = QueueApprover()
        self.dry_run = dry_run
        self.ui = QtAgentUI(qapp, overlay, self.approver, self.stop, speak)
        self.approver._on_request = self.ui.on_request
        self.store = StateStore()
        self.screen = screen or self._real_screen()
        self._executor_factory = executor_factory or (
            lambda: make_executor(self.dry_run, self.panic))
        self.planner = Planner(ProviderChain(self.providers))
        self.controller = AgentController(self._make_loop, self.panic, self.store,
                                          on_finish=self._finished)

    @staticmethod
    def _real_screen():
        collect = focused = None
        try:
            import uiautomation  # noqa: F401
            from .uia import collect_foreground, focused_element
            collect, focused = collect_foreground, focused_element
        except ImportError:
            pass   # no UIA: pixel coordinates only
        return WindowsScreen(collect, focused)

    def _make_loop(self) -> AgentLoop:
        scope = frozenset(a.strip().lower() for a in
                          (config.get_key("ARROW_AGENT_SCOPE") or "").split(",") if a.strip())
        cfg = LoopConfig(max_steps=config.agent_max_steps(), mode=config.agent_mode(),
                         scope_apps=scope)
        return AgentLoop(self.screen, self._executor_factory(), self.planner,
                         self.approver, ui=self.ui, log=ActionLog(), panic=self.panic,
                         activity=ActivityMonitor(self.screen.cursor), risk=assess,
                         sensitive=is_sensitive_window, store=self.store, config=cfg)

    def _finished(self, result) -> None:
        pass   # HUD already shows the result via ui.finished

    # -- user-facing controls ---------------------------------------------------
    def ready(self) -> bool:
        return bool(self.providers)

    def start_task(self, task: str, resume: bool = False) -> bool:
        if not self.ready():
            self.ui.say("Walang API key para sa agent. Ilagay ang GEMINI_API_KEY sa .env.")
            return False
        from . import privacy
        if not privacy.acknowledged():
            self.ui.say(privacy.NOTICE + " Run: python -m arrow_assistant.agent.privacy")
            return False
        ok = self.controller.start(task, resume=resume)
        if not ok:
            self.ui.say("May tumatakbo pang task (o walang naka-save na ipagpapatuloy).")
        return ok

    def stop(self) -> None:
        self.controller.stop()
        self.approver.respond(STOP)

    def approve(self) -> None:
        self.approver.respond(APPROVE)

    def skip(self) -> None:
        self.approver.respond(SKIP)
