"""Spoken entry into agent mode.

"Arrow agent, open notepad and type hello" -> task.
"Arrow agent stop" / "Arrow agent ituloy" -> control commands.
Speech-to-text mangles names, so a few near-misses are accepted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TRIGGER = re.compile(
    r"^\s*(?:hey|hi|ok|okay|uy|oy)?[\s,]*(?:arrow|aro|arrows|aroh)[\s,.:;-]*"
    r"(?:agent mode|agents|agent|ajent|ahente|edgent)\b[\s,.:;-]*(?P<rest>.*)$",
    re.I | re.S)
_STOP = re.compile(r"^(stop|itigil|tigil|huwag na|cancel|abort)\b", re.I)
_CONTINUE = re.compile(r"^(continue|resume|ituloy|tuloy|go on)\b", re.I)


@dataclass(frozen=True)
class AgentCommand:
    kind: str          # task | stop | continue
    task: str = ""


def parse_agent_request(text: str) -> AgentCommand | None:
    if not text:
        return None
    m = _TRIGGER.match(text.strip())
    if not m:
        return None
    rest = m.group("rest").strip().strip(".")
    if not rest:
        return None
    if _STOP.match(rest):
        return AgentCommand("stop")
    if _CONTINUE.match(rest):
        return AgentCommand("continue")
    return AgentCommand("task", rest[:600])
