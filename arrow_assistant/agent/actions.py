"""The action vocabulary and a strict parser for model output.

The model must answer with exactly ONE JSON object per step. Anything
else is rejected here, before it can get near the executor.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

POINTER_KINDS = ("click", "double_click", "right_click")
PHYSICAL_KINDS = POINTER_KINDS + ("type", "key", "drag", "open_app")
PASSIVE_KINDS = ("scroll", "wait")
TERMINAL_KINDS = ("done", "ask_user", "fail")
ALL_KINDS = frozenset(PHYSICAL_KINDS + PASSIVE_KINDS + TERMINAL_KINDS)

MAX_TYPE_CHARS = 500
MAX_SCROLL = 20
MAX_WAIT_S = 10.0
MIN_WAIT_S = 0.2

_KEY_ALIASES = {
    "control": "ctrl", "return": "enter", "escape": "esc", "del": "delete",
    "windows": "win", "cmd": "win", "super": "win", "option": "alt",
    "pgdn": "pagedown", "pgup": "pageup", "spacebar": "space",
    " ": "space", "arrowup": "up", "arrowdown": "down",
    "arrowleft": "left", "arrowright": "right",
}


class ActionParseError(ValueError):
    """Model output was not one valid action."""


@dataclass(frozen=True)
class Action:
    kind: str
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    element: int | None = None
    text: str | None = None
    keys: tuple[str, ...] = ()
    amount: int = 0           # scroll clicks, positive = down
    seconds: float = 0.0
    app: str | None = None
    message: str | None = None
    thought: str = ""
    label: str = ""           # what the model says it is acting on
    risk_hint: str = "low"    # model's own opinion: low | medium | high
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def is_physical(self) -> bool:
        return self.kind in PHYSICAL_KINDS

    @property
    def is_terminal(self) -> bool:
        return self.kind in TERMINAL_KINDS

    @property
    def has_target(self) -> bool:
        return self.element is not None or (self.x is not None and self.y is not None)

    def describe(self) -> str:
        """Short human-readable summary for the HUD, log, and approval."""
        tgt = f" '{self.label}'" if self.label else ""
        if self.kind in POINTER_KINDS:
            return f"{self.kind.replace('_', ' ')}{tgt}"
        if self.kind == "type":
            shown = (self.text or "")
            shown = shown if len(shown) <= 60 else shown[:57] + "..."
            return f"type \"{shown}\"{tgt}"
        if self.kind == "key":
            return "press " + "+".join(self.keys)
        if self.kind == "scroll":
            return f"scroll {'down' if self.amount > 0 else 'up'} {abs(self.amount)}"
        if self.kind == "drag":
            return f"drag{tgt}"
        if self.kind == "wait":
            return f"wait {self.seconds:g}s"
        if self.kind == "open_app":
            return f"open app '{self.app}'"
        return f"{self.kind}: {self.message or ''}".strip()

    def signature(self) -> tuple:
        """Identity used for repeat/stuck detection."""
        return (self.kind, self.element, self.x, self.y, self.text,
                self.keys, self.amount, self.app)


def extract_json(raw: str) -> dict:
    """Pull the first balanced JSON object out of model text."""
    if not isinstance(raw, str) or not raw.strip():
        raise ActionParseError("empty model output")
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    start = text.find("{")
    if start < 0:
        raise ActionParseError("no JSON object in model output")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise ActionParseError(f"invalid JSON: {exc}") from exc
                if not isinstance(obj, dict):
                    raise ActionParseError("JSON is not an object")
                return obj
    raise ActionParseError("unterminated JSON object")


def normalize_key(name: str) -> str:
    k = str(name).strip().lower()
    return _KEY_ALIASES.get(k, k)


def _int(obj: dict, name: str) -> int | None:
    v = obj.get(name)
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ActionParseError(f"{name} must be a number")
    try:
        return int(round(float(v)))
    except (TypeError, ValueError) as exc:
        raise ActionParseError(f"{name} must be a number") from exc


def parse_action(raw: str) -> Action:
    obj = extract_json(raw)
    # Some models nest the action: {"thought":..., "action": {...}}
    inner = obj.get("action")
    if isinstance(inner, dict):
        obj = {**{k: v for k, v in obj.items() if k != "action"}, **inner}
        kind = obj.get("kind") or obj.get("type")
    else:
        kind = inner if isinstance(inner, str) else obj.get("kind")
    if not isinstance(kind, str):
        raise ActionParseError("missing action kind")
    kind = kind.strip().lower().replace(" ", "_").replace("-", "_")
    kind = {"press": "key", "hotkey": "key", "write": "type", "input": "type",
            "launch": "open_app", "open": "open_app", "finish": "done",
            "complete": "done", "doubleclick": "double_click",
            "rightclick": "right_click"}.get(kind, kind)
    if kind not in ALL_KINDS:
        raise ActionParseError(f"unknown action kind: {kind!r}")

    thought = str(obj.get("thought") or "")[:400]
    label = str(obj.get("label") or obj.get("target") or "")[:120]
    hint = str(obj.get("risk") or obj.get("risk_hint") or "low").lower()
    if hint not in ("low", "medium", "high"):
        hint = "low"
    common = dict(kind=kind, thought=thought, label=label, risk_hint=hint)

    if kind in POINTER_KINDS or kind == "drag":
        element = _int(obj, "element")
        x, y = _int(obj, "x"), _int(obj, "y")
        if element is None and (x is None or y is None):
            raise ActionParseError(f"{kind} needs element or x,y")
        x2 = y2 = None
        if kind == "drag":
            x2, y2 = _int(obj, "x2"), _int(obj, "y2")
            if x2 is None or y2 is None:
                raise ActionParseError("drag needs x2,y2")
        return Action(x=x, y=y, x2=x2, y2=y2, element=element, **common)

    if kind == "type":
        text = obj.get("text")
        if not isinstance(text, str) or not text:
            raise ActionParseError("type needs non-empty text")
        if len(text) > MAX_TYPE_CHARS:
            raise ActionParseError(f"text longer than {MAX_TYPE_CHARS} chars")
        return Action(text=text, element=_int(obj, "element"), **common)

    if kind == "key":
        keys = obj.get("keys", obj.get("key"))
        if isinstance(keys, str):
            keys = [k for k in re.split(r"[+\s,]+", keys) if k]
        if not isinstance(keys, list) or not keys or len(keys) > 4:
            raise ActionParseError("key needs 1-4 key names")
        norm = tuple(normalize_key(k) for k in keys)
        if any(not k or len(k) > 12 for k in norm):
            raise ActionParseError("bad key name")
        return Action(keys=norm, **common)

    if kind == "scroll":
        amount = _int(obj, "amount")
        if amount is None:
            d = str(obj.get("direction", "down")).lower()
            amount = -5 if d == "up" else 5
        if amount == 0:
            raise ActionParseError("scroll amount is 0")
        amount = max(-MAX_SCROLL, min(MAX_SCROLL, amount))
        return Action(amount=amount, x=_int(obj, "x"), y=_int(obj, "y"), **common)

    if kind == "wait":
        try:
            secs = float(obj.get("seconds", 1.0))
        except (TypeError, ValueError) as exc:
            raise ActionParseError("wait seconds must be a number") from exc
        return Action(seconds=max(MIN_WAIT_S, min(MAX_WAIT_S, secs)), **common)

    if kind == "open_app":
        app = obj.get("app") or obj.get("name") or obj.get("text")
        if not isinstance(app, str) or not app.strip() or len(app) > 60:
            raise ActionParseError("open_app needs an app name")
        return Action(app=app.strip(), **common)

    # done / ask_user / fail
    msg = obj.get("message") or obj.get("reason") or obj.get("text") or ""
    if kind in ("ask_user", "fail") and not str(msg).strip():
        raise ActionParseError(f"{kind} needs a message")
    return Action(message=str(msg)[:600], **common)
