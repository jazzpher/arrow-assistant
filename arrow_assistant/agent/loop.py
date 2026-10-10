"""The agent loop: observe -> plan -> guard -> (approve) -> act -> verify.

Everything outside the loop is injected (screen, executor, planner,
approver, ui, risk), so the whole thing is tested with fakes and can run
dry on a machine with no display.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .actions import POINTER_KINDS, Action
from .activity import ActivityMonitor
from .actlog import ActionLog
from .approval import APPROVE, SKIP, STOP, ApprovalRequest, Approver
from .executor import ActionAborted
from .observation import Observation, changed_fraction, point_in_rects
from .panic import PanicStop, PanicSwitch
from .planner import PlanContext, PlannerError, QuotaExhausted
from .risk import Assessment, RiskContext, assess_permissive
from .state import StateStore, TaskState
from .ui import AgentUI, NullUI

STALE_FRACTION = 0.03      # screen changed this much while waiting for approval
KEYBOARD_KINDS = ("type", "key")   # go to whatever window/field has focus
CLEAR_PREVIEW_S = 0.15


@dataclass
class LoopConfig:
    max_steps: int = 25
    mode: str = "step"               # step | task | auto
    approve_timeout_s: float = 120.0
    settle_s: float = 0.8
    max_noeffect: int = 3
    max_repeat: int = 3
    max_invalid: int = 3
    max_blocked: int = 2
    max_skipped: int = 4
    use_plan: bool = True
    verify_done: bool = True
    pause_timeout_s: float = 300.0
    scope_apps: frozenset = field(default_factory=frozenset)


@dataclass
class AgentResult:
    status: str        # done|failed|stopped|step_limit|needs_user|quota|blocked|stuck
    message: str
    steps: int = 0
    resumable: bool = False


class AgentLoop:
    def __init__(self, screen, executor, planner, approver: Approver,
                 ui: AgentUI | None = None, log: ActionLog | None = None,
                 panic: PanicSwitch | None = None,
                 activity: ActivityMonitor | None = None,
                 risk: Callable[[RiskContext], Assessment] = assess_permissive,
                 sensitive: Callable[[str, str], bool] = lambda app, title: False,
                 store: StateStore | None = None,
                 config: LoopConfig | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.screen = screen
        self.executor = executor
        self.planner = planner
        self.approver = approver
        self.ui = ui or NullUI()
        self.log = log or ActionLog()
        self.panic = panic or PanicSwitch()
        self.activity = activity
        self.risk = risk
        self.sensitive = sensitive
        self.store = store
        self.cfg = config or LoopConfig()
        self._sleep = sleep
        self._focus_el = None   # element last clicked: the likely target of typing

    # -- public --------------------------------------------------------------
    def run(self, task: str, resume: TaskState | None = None) -> AgentResult:
        if resume:
            state = resume
            state.max_steps = state.step + self.cfg.max_steps
            state.status = "running"
        else:
            state = TaskState(task=task, mode=self.cfg.mode,
                              max_steps=self.cfg.max_steps)
        self.log.start(state.task, self.cfg.mode, state.max_steps)
        try:
            result = self._run(state)
        except PanicStop as exc:
            result = AgentResult("stopped", f"Stopped: {exc}", state.step)
        except QuotaExhausted as exc:
            result = AgentResult(
                "quota", "Naubusan ng free quota sa lahat ng providers. "
                f"Subukan ulit sa ~{max(1, int(exc.retry_after_s // 60))} min; "
                "naka-save yung progress, pwede i-resume.", state.step, True)
        except PlannerError as exc:
            result = AgentResult("failed", f"Hindi makagawa ng valid na action ang model: {exc}",
                                 state.step, True)
        except Exception as exc:  # noqa: BLE001 - never leave the HUD hanging
            result = AgentResult("failed", f"Internal error: {type(exc).__name__}: {exc}",
                                 state.step, True)
        state.status, state.message = result.status, result.message
        if self.store:
            if result.resumable:
                self.store.save(state)
            else:
                self.store.clear()
        self.log.finish(result.status, result.message)
        self.ui.finished(result.status, result.message)
        return result

    # -- core ------------------------------------------------------------------
    def _observe(self) -> Observation:
        self.ui.preview(None, "")
        self._sleep(CLEAR_PREVIEW_S)
        return self.screen.observe(self.ui.hud_rects())

    def _ctx(self, obs: Observation) -> PlanContext:
        return PlanContext(obs.image_b64, obs.app, obs.title, obs.frame.size,
                           obs.elements_text)

    def _run(self, state: TaskState) -> AgentResult:
        cfg = self.cfg
        self.panic.check()
        obs = self._observe()
        if self.sensitive(obs.app, obs.title):
            return self._sensitive_result(state, obs)
        if cfg.use_plan and not state.plan:
            state.plan = self.planner.make_plan(state.task, self._ctx(obs))
        if cfg.mode == "task" and not state.plan_approved:
            req = ApprovalRequest("plan", state.task, plan=tuple(state.plan),
                                  max_steps=state.max_steps)
            decision = self.approver.request(req, cfg.approve_timeout_s)
            if decision == STOP:
                raise PanicStop("stopped at plan approval")
            if decision != APPROVE:
                return AgentResult("stopped", "Hindi na-approve yung plan.", state.step)
            state.plan_approved = True
        counters = dict(noeffect=0, repeat=0, invalid=0, blocked=0, skipped=0)
        last_sig = None
        replan_note = ""

        while state.step < state.max_steps:
            self._gate_user()
            self.panic.check()
            if obs is None:
                obs = self._observe()
            if self.sensitive(obs.app, obs.title):
                return self._sensitive_result(state, obs)
            action, comp = self.planner.next_action(
                state.task, state.plan, state.history, self._ctx(obs))
            self.panic.check()
            state.step += 1
            n = state.step
            self.ui.step(n, state.max_steps, action.describe())
            prov = getattr(comp, "provider", "")

            # ---- terminal actions -------------------------------------------
            if action.kind == "done":
                if cfg.verify_done and not state.verified:
                    v = self.planner.verify(state.task, state.history, self._ctx(obs))
                    state.verified = True
                    if not v.success:
                        state.add("done (claimed)", f"verification failed: {v.reason}")
                        self.log.step(n, "done (claimed)", "-", f"not verified: {v.reason}", (), prov)
                        obs = None
                        continue
                self.log.step(n, "done", "-", action.message or "", (), prov)
                return AgentResult("done", action.message or "Tapos na.", n)
            if action.kind == "ask_user":
                self.log.step(n, "ask_user", "-", action.message or "", (), prov)
                return AgentResult("needs_user", action.message or "Kailangan ko ng tulong mo.", n, True)
            if action.kind == "fail":
                self.log.step(n, "fail", "-", action.message or "", (), prov)
                return AgentResult("failed", action.message or "Hindi ko magawa.", n)

            # ---- repeat detection ----------------------------------------------
            sig = action.signature()
            counters["repeat"] = counters["repeat"] + 1 if sig == last_sig else 1
            last_sig = sig
            if counters["repeat"] >= cfg.max_repeat:
                self.log.step(n, action.describe(), "-", "stuck: same action repeated")
                return AgentResult("stuck", "Paulit-ulit na yung action; huminto muna ako.", n, True)

            # ---- resolve targets ---------------------------------------------------
            pt, pt2, label, problem = self._resolve(action, obs)
            if problem:
                state.add(action.describe(), problem)
                self.log.step(n, action.describe(), "-", problem, (), prov)
                counters["invalid"] += 1
                if counters["invalid"] >= cfg.max_invalid:
                    return AgentResult("failed", "Paulit-ulit na invalid na target.", n, True)
                obs = None
                continue

            # ---- risk gate ------------------------------------------------------------
            target_el = obs.element_at(pt) if pt else None
            pw = bool(target_el and target_el.password)
            if action.kind == "type":
                for f in (self._focus_el, self._live_focus()):
                    if f is not None:
                        pw = pw or f.password
                        if f.name and f.name not in label:
                            label = f"{label} {f.name}".strip()
            ctx = RiskContext(action, obs.app, obs.title, label, state.task, pt,
                              cfg.scope_apps, self.ui.hud_rects(), pw)
            assess = self.risk(ctx)
            if assess.blocked:
                why = "; ".join(assess.reasons)
                state.add(action.describe(), f"BLOCKED by safety: {why}")
                self.log.step(n, action.describe(), "blocked", why, assess.reasons, prov)
                self.ui.say(f"Hindi ko gagawin: {why}")
                counters["blocked"] += 1
                if counters["blocked"] >= cfg.max_blocked:
                    return AgentResult("blocked", f"Tinanggihan ng safety: {why}", n, True)
                obs = None
                continue

            # ---- approval ------------------------------------------------------------------
            needs = assess.confirm or (action.is_physical and cfg.mode == "step")
            if needs:
                req = ApprovalRequest("action", action.describe(), assess.reasons,
                                      assess.confirm, pt, n, state.max_steps)
                decision = self.approver.request(req, cfg.approve_timeout_s)
                if decision == STOP:
                    self.log.step(n, action.describe(), "stop", "user stopped")
                    raise PanicStop("stopped at approval")
                if decision != APPROVE:
                    state.add(action.describe(), "user skipped this action")
                    self.log.step(n, action.describe(), "skipped", "", assess.reasons, prov)
                    counters["skipped"] += 1
                    if counters["skipped"] >= cfg.max_skipped:
                        return AgentResult("needs_user", "Madalas mong i-skip; huminto muna ako. "
                                           "Sabihin mo kung ano gusto mong ibahin.", n, True)
                    obs = None
                    continue
                # the screen may have changed while the human was deciding
                fresh = self._observe()
                if changed_fraction(obs.thumb, fresh.thumb) > STALE_FRACTION:
                    state.add(action.describe(), "screen changed while waiting; re-planning")
                    self.log.step(n, action.describe(), "approved", "stale screen, re-planning")
                    obs = fresh
                    continue
                if self.activity:
                    self.activity.rebase()
                decision_txt = "approved" if needs else "auto"
            else:
                decision_txt = "auto"
                if self.activity and self.activity.user_moved():
                    self._handle_takeover(state)
                    state.step -= 1
                    obs = None
                    continue
            # ---- last-moment keyboard guard ----------------------------------------------
            # Typing goes to whatever has focus NOW, which may not be what the
            # screenshot (or the approval) showed: a popup, a notification or an
            # Alt+Tab can move focus, and Tab moves it between fields.
            verdict, why = self._keyboard_guard(action, obs)
            if verdict == "block":
                state.add(action.describe(), f"BLOCKED by safety: {why}")
                self.log.step(n, action.describe(), "blocked", why, (why,), prov)
                self.ui.say(f"Hindi ko gagawin: {why}")
                counters["blocked"] += 1
                if counters["blocked"] >= cfg.max_blocked:
                    return AgentResult("blocked", f"Tinanggihan ng safety: {why}", n, True)
                obs = None
                continue
            if verdict == "replan":
                state.add(action.describe(), why)
                self.log.step(n, action.describe(), "-", why, (), prov)
                counters["invalid"] += 1
                if counters["invalid"] >= cfg.max_invalid:
                    return AgentResult("failed", "Paulit-ulit na lumilipat ang focus; huminto muna ako.",
                                       n, True)
                obs = None
                continue
            # Fake/dry-run executors need no OS callback. RealExecutor refuses
            # keyboard input without one, and checks it for every character.
            bind = getattr(self.executor, "set_keyboard_guard", None)
            if bind and action.kind in KEYBOARD_KINDS:
                initial_field = self._live_focus()

                def check_keyboard():
                    verdict, why = self._keyboard_guard(action, obs)
                    if verdict:
                        raise ActionAborted(why)
                    now = self._live_focus()
                    if initial_field != now:
                        raise ActionAborted("focused field changed; stopped remaining input")
                bind(check_keyboard)
            self.ui.preview(pt, label or action.describe())
            self.panic.check()

            # ---- act ---------------------------------------------------------------------------
            try:
                end_pt = self.executor.perform(action, pt, pt2)
            except ActionAborted as exc:
                # stopped half-way on purpose (e.g. Start menu never opened)
                state.add(action.describe(), f"aborted for safety: {exc}")
                self.log.step(n, action.describe(), decision_txt, f"aborted: {exc}", (), prov)
                self._focus_el = None
                counters["invalid"] += 1
                if counters["invalid"] >= cfg.max_invalid:
                    return AgentResult("failed", f"Hindi ko magawa nang ligtas: {exc}", n, True)
                obs = None
                continue
            if action.kind in POINTER_KINDS:
                self._focus_el = target_el
            elif action.kind in ("key", "open_app", "drag") or (
                    action.kind == "type" and "\t" in (action.text or "")):
                self._focus_el = None   # focus may have moved; do not trust the old field
            if self.activity:
                self.activity.expect(end_pt or (self.screen.cursor() if hasattr(self.screen, "cursor") else None))
            self._sleep(cfg.settle_s)
            self.panic.check()

            # ---- verify --------------------------------------------------------------------------
            new = self._observe()
            moved = changed_fraction(obs.thumb, new.thumb) > 0.0005
            outcome = "ok, screen changed" if moved else "no visible change on screen"
            if action.kind in POINTER_KINDS + ("drag", "open_app") and not moved:
                counters["noeffect"] += 1
            else:
                counters["noeffect"] = 0
            state.add(action.describe(), outcome)
            self.log.step(n, action.describe(), decision_txt, outcome, assess.reasons, prov)
            counters["invalid"] = counters["blocked"] = 0
            if self.store:
                self.store.save(state)
            if counters["noeffect"] >= cfg.max_noeffect:
                return AgentResult("stuck", "Walang nagbabago sa screen kahit ilang beses; huminto muna ako.",
                                   n, True)
            obs = new

        return AgentResult("step_limit",
                           f"Umabot na sa {state.max_steps} steps. Sabihin mo 'ituloy' para dagdagan.",
                           state.step, True)

    def _sensitive_result(self, state, obs) -> AgentResult:
        # The screenshot is never sent to a model while this window is up.
        msg = (f"Nakabukas ang sensitive na window ('{obs.title[:40] or obs.app}'). "
               "Hindi ko titingnan o gagalawin yan. Isara mo muna, tapos ituloy natin.")
        self.log.note(f"sensitive window in foreground: {obs.app} / {obs.title[:40]}")
        return AgentResult("blocked", msg, state.step, True)

    # -- helpers ------------------------------------------------------------------------
    def _resolve(self, action: Action, obs: Observation):
        """-> (pt, pt2, label, problem). Points are absolute screen pixels."""
        label = action.label
        pt = pt2 = None
        if action.element is not None:
            el = obs.element_by_id(action.element)
            if el is None:
                return None, None, label, f"no such element #{action.element}; use a number from the list or x,y"
            if not el.enabled:
                return None, None, label, f"element #{action.element} is disabled"
            pt = el.center
            label = el.name or label
        elif action.x is not None and action.y is not None:
            pt = obs.frame.to_screen(action.x, action.y)
            if action.kind in POINTER_KINDS or action.kind == "drag":
                el = obs.element_at(pt)
                if el and el.name:
                    label = el.name
        if action.kind == "drag":
            if action.x2 is None or action.y2 is None:
                return None, None, label, "drag needs an end point"
            pt2 = obs.frame.to_screen(action.x2, action.y2)
        if pt is not None and point_in_rects(pt, self.ui.hud_rects()):
            return None, None, label, "that point is Arrow's own control panel; never click it"
        if action.kind in POINTER_KINDS and pt is None:
            return None, None, label, "click has no target"
        return pt, pt2, label, ""

    def _live_focus(self):
        fn = getattr(self.screen, "focused_element", None)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:  # noqa: BLE001 - UIA hiccups must not crash the task
            return None

    def _live_foreground(self):
        fn = getattr(self.screen, "foreground", None)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None

    def _keyboard_guard(self, action: Action, obs: Observation) -> tuple[str, str]:
        """-> ("", "") to go ahead, ("replan", why) or ("block", why)."""
        if action.kind not in KEYBOARD_KINDS:
            return "", ""
        fg = self._live_foreground()
        strict = getattr(self.screen, "require_verified_focus", False) or hasattr(self.screen, "foreground")
        if getattr(self.screen, "require_verified_focus", False) and not obs.hwnd:
            return "replan", "screenshot window identity is unknown; use manual input"
        if strict and (fg is None or not fg[0] or fg[0].lower() == "unknown"
                       or (len(fg) > 2 and not fg[2])):
            return "replan", "cannot verify foreground window; use manual input"
        if fg is not None:
            app, title = (fg[0] or "").lower(), fg[1] or ""
            hwnd = fg[2] if len(fg) > 2 else 0
            if hwnd and obs.hwnd:
                # same window = same target, even if its title ticks
                # ("(2) Messenger", "*Untitled - Notepad", a playing video)
                moved = hwnd != obs.hwnd or app != (obs.app or "").lower()
            else:
                moved = app != (obs.app or "").lower() or title != (obs.title or "")
            if moved:
                return "replan", (f"focus moved to '{title[:40] or app}' before the "
                                  f"{action.kind}; re-planning instead of typing blind")
        f = self._live_focus()
        if strict and (f is None or not f.enabled or not f.role
                       or (not f.runtime_id and f.area <= 0)):
            return "replan", "cannot verify keyboard field; use manual input"
        if action.kind == "type":
            if f is not None:
                if f.password:
                    return "block", "keyboard focus is in a password field"
                live_risk = self.risk(RiskContext(action, obs.app, obs.title,
                    f.name, "", target_is_password=f.password))
                if live_risk.blocked:
                    return "block", "; ".join(live_risk.reasons)
        return "", ""

    def _gate_user(self) -> None:
        if self.activity and self.activity.user_moved():
            self._handle_takeover(None)

    def _handle_takeover(self, state) -> None:
        self.panic.pause("user took over the mouse")
        self.ui.say("Naka-pause: ginalaw mo yung mouse. Pindutin ang Approve/Resume para ituloy.")
        # HUD Approve/Skip responds to the pending request below
        from .approval import ApprovalRequest as AR
        req = AR("action", "Resume the agent? (you moved the mouse)", ("paused: mouse moved",), False)
        decision = self.approver.request(req, self.cfg.pause_timeout_s)
        if decision == STOP:
            self.panic.resume()
            raise PanicStop("stopped while paused")
        if decision != APPROVE:
            self.panic.resume()
            raise PanicStop("pause not resumed")
        self.panic.resume()
        if self.activity:
            self.activity.rebase()
