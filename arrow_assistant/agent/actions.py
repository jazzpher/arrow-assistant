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
# Tools run in code, not on the screen (see tools.py). They never touch the
# mouse or keyboard, but write_file and powershell change the PC.
TOOL_READ_KINDS = ("web_search", "web_fetch", "read_file", "list_files")
TOOL_WRITE_KINDS = ("write_file", "powershell")
TOOL_KINDS = TOOL_READ_KINDS + TOOL_WRITE_KINDS
ALL_KINDS = frozenset(PHYSICAL_KINDS + PASSIVE_KINDS + TERMINAL_KINDS + TOOL_KINDS)

MAX_TYPE_CHARS = 500
MAX_WRITE_CHARS = 20000
MAX_COMMAND_CHARS = 2000
MAX_QUERY_CHARS = 300
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
    path: str | None = None    # read_file / write_file / list_files (workspace-relative)
    url: str | None = None     # web_fetch
    command: str | None = None # powershell
    thought: str = ""
    label: str = ""           # what the model says it is acting on
    risk_hint: str = "low"    # model's own opinion: low | medium | high
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def is_physical(self) -> bool:
        return self.kind in PHYSICAL_KINDS

    @property
    def is_tool(self) -> bool:
        return self.kind in TOOL_KINDS

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
        if self.kind == "web_search":
            return f"web search \"{_short(self.text)}\""
        if self.kind == "web_fetch":
            return f"fetch {_short(self.url, 80)}"
        if self.kind == "read_file":
            return f"read file '{self.path}'"
        if self.kind == "list_files":
            return f"list files in '{self.path or '.'}'"
        if self.kind == "write_file":
            return f"write file '{self.path}' ({len(self.text or '')} chars)"
        if self.kind == "powershell":
            return f"run PowerShell: {_short(self.command, 120)}"
        return f"{self.kind}: {self.message or ''}".strip()

    def signature(self) -> tuple:
        """Identity used for repeat/stuck detection."""
        return (self.kind, self.element, self.x, self.y, self.text,
                self.keys, self.amount, self.app, self.path, self.url, self.command)


def _short(value: str | None, n: int = 60) -> str:
    v = (value or "").replace("\n", " ")
    return v if len(v) <= n else v[:n - 3] + "..."


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
            "rightclick": "right_click", "search": "web_search",
            "fetch": "web_fetch", "browse": "web_fetch", "read": "read_file",
            "save_file": "write_file", "ls": "list_files",
            "shell": "powershell", "run_command": "powershell"}.get(kind, kind)
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

    if kind == "web_search":
        q = obj.get("query") or obj.get("text")
        if not isinstance(q, str) or not q.strip() or len(q) > MAX_QUERY_CHARS:
            raise ActionParseError("web_search needs a query")
        return Action(text=q.strip(), **common)

    if kind == "web_fetch":
        url = obj.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://", url.strip(), re.I) \
                or len(url) > 2000:
            raise ActionParseError("web_fetch needs an http(s) url")
        return Action(url=url.strip(), **common)

    if kind in ("read_file", "write_file", "list_files"):
        path = obj.get("path") or obj.get("file")
        if path is None and kind == "list_files":
            path = "."
        if not isinstance(path, str) or not path.strip() or len(path) > 260:
            raise ActionParseError(f"{kind} needs a path")
        if kind == "write_file":
            text = obj.get("content", obj.get("text"))
            if not isinstance(text, str):
                raise ActionParseError("write_file needs content")
            if len(text) > MAX_WRITE_CHARS:
                raise ActionParseError(f"content longer than {MAX_WRITE_CHARS} chars")
            return Action(path=path.strip(), text=text, **common)
        return Action(path=path.strip(), **common)

    if kind == "powershell":
        cmd = obj.get("command") or obj.get("text")
        if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > MAX_COMMAND_CHARS:
            raise ActionParseError("powershell needs a command")
        return Action(command=cmd.strip(), **common)

    # done / ask_user / fail
    msg = obj.get("message") or obj.get("reason") or obj.get("text") or ""
    if kind in ("ask_user", "fail") and not str(msg).strip():
        raise ActionParseError(f"{kind} needs a message")
    return Action(message=str(msg)[:600], **common)
