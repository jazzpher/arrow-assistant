"""Risk gate. Filled in by the safety milestone; the loop only depends on
the Assessment shape, so M2 ships with a permissive default."""
from __future__ import annotations

from dataclasses import dataclass, field

from .actions import Action


@dataclass(frozen=True)
class Assessment:
    level: str = "safe"            # safe | confirm | block
    reasons: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.level == "block"

    @property
    def confirm(self) -> bool:
        return self.level == "confirm"


SAFE = Assessment()


def assess_permissive(ctx) -> Assessment:
    return SAFE


@dataclass
class RiskContext:
    action: Action
    app: str = "unknown"
    title: str = ""
    label: str = ""                # what the action targets (UIA name wins over model label)
    task: str = ""
    pt: tuple[int, int] | None = None
    scope_apps: frozenset = field(default_factory=frozenset)
    hud_rects: list = field(default_factory=list)
    target_is_password: bool = False
