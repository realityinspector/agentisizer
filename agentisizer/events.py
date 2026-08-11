"""
The one shape everything upstream collapses into.

An input module's whole job is to produce Events. Claude Code hooks, a CI
webhook, a market feed, a build log — none of them get to know anything about
music, and the musical side never learns where a message came from. That
seam is the reason a new source is a small file rather than a rewrite.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


# What an event means musically. Deliberately tiny: five things the ear can
# actually tell apart in a background soundtrack. Resist adding more — a
# vocabulary the listener can't distinguish is just noise with extra steps.
KINDS = ("progress", "good", "bad", "blocked", "resolved")


@dataclass
class Event:
    """Something happened somewhere and the music might care."""

    text: str                       # what the agent said, in its own words
    source: str = "unknown"         # which input module produced this
    kind: str | None = None         # set by the interpreter if not supplied
    intensity: float = 0.5          # 0..1, how much this matters
    tags: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self):
        if self.kind is not None and self.kind not in KINDS:
            raise ValueError(f"unknown kind {self.kind!r}; expected one of {KINDS}")
        self.intensity = max(0.0, min(1.0, float(self.intensity)))
        self.text = (self.text or "").strip()

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        known = {f for f in cls.__dataclass_fields__}
        meta = {k: v for k, v in d.items() if k not in known}
        clean = {k: v for k, v in d.items() if k in known}
        ev = cls(**clean)
        ev.meta.update(meta)
        return ev

    def __str__(self) -> str:
        k = self.kind or "?"
        return f"[{self.source}/{k}] {self.text[:80]}"
