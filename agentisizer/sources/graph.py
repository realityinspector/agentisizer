"""
Graph source: drive the soundtrack from a coordinator's live agent graph.

Built for Atlas (Sean's coordination agent, which renders an isometric map of
the ecosystem at localhost:4600), but the shape is general — any coordinator
that can answer with counts by status will work.

    GET <url>
    {"working": 1, "blocked": 0, "failed": 0, "idle": 7,
     "blocked_for": 0.0,
     "agents": {"atlas": "working", "baton-agent": "idle", ...}}

── why this source is different ─────────────────────────────────────────
Every other input is an event stream, so the conductor has to *infer* how
busy things are by counting how often messages arrive. A coordinator does not
have that problem: it knows there are four agents working and one blocked. It
holds the number the conductor has been estimating.

So this source does two things rather than one:

  * **Transitions become events.** An agent entering `blocked` emits a
    blocked event; leaving it emits `resolved`. These go through the normal
    path, so they inherit the accent spacing and the alarm escalation — the
    listenability rules apply to a map exactly as they do to a chat.
  * **Levels are reported directly.** The working fraction is handed to the
    conductor as an authoritative reading rather than inferred from event
    rate, because inferring a number somebody already knows is just a way of
    getting it wrong more slowly.

The seam survives: this still only produces Events plus one observation, and
still knows nothing about notes, keys or volume.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from typing import Any

from ..events import Event


DEFAULT_URL = "http://127.0.0.1:4600/status"

# Statuses we understand. Anything else is treated as idle rather than
# guessed at — a coordinator adding a new state should not start making noise
# until somebody decides what it means.
ACTIVE = "working"
STOPPED = "blocked"
BROKEN = "failed"


def activity_from(working: int, total: int) -> float:
    """
    How busy the fleet sounds.

    Neither pure fraction nor pure count works alone. Fraction alone makes one
    agent grinding away in a twenty-node graph almost silent, which is wrong —
    something *is* happening. Count alone makes a two-agent setup permanently
    quiet. So take whichever reads louder: the share of the fleet that is
    active, or the absolute number saturating at five.
    """
    if total <= 0:
        return 0.0
    return min(1.0, max(working / total, working / 5.0))


class GraphSource:
    """Polls a coordinator's status endpoint and reports what changed."""

    def __init__(self, emit, conductor=None, url: str = DEFAULT_URL,
                 interval: float = 2.0, timeout: float = 3.0):
        self.emit = emit
        self.conductor = conductor      # for the authoritative activity reading
        self.url = url
        self.interval = interval
        self.timeout = timeout

        self._seen: dict[str, str] = {}   # agent -> last status we reported on
        self._first_poll = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None

    # ── talking to the coordinator ───────────────────────────────────────
    def fetch(self) -> dict[str, Any] | None:
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
            self.last_error = None
            return data if isinstance(data, dict) else None
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None

    # ── turning a snapshot into events ───────────────────────────────────
    def diff(self, agents: dict[str, str]) -> list[Event]:
        """
        Compare against what we last saw and describe the changes.

        On the very first poll we do not announce every agent — a coordinator
        with nine idle nodes should not open with nine events. We do announce
        anything already blocked or failed, because those are the states a
        human needs to know about immediately, and staying quiet about a
        blocker just because we arrived late is the wrong failure.
        """
        events: list[Event] = []

        for name, status in sorted(agents.items()):
            before = self._seen.get(name)
            if before == status:
                continue

            if self._first_poll and status not in (STOPPED, BROKEN):
                self._seen[name] = status
                continue

            if status == STOPPED:
                events.append(Event(
                    text=f"{name} is blocked and needs a human",
                    source="graph", kind="blocked", intensity=0.9,
                    meta={"agent": name, "from": before}))
            elif status == BROKEN:
                events.append(Event(
                    text=f"{name} failed",
                    source="graph", kind="bad", intensity=0.8,
                    meta={"agent": name, "from": before}))
            elif before in (STOPPED, BROKEN):
                events.append(Event(
                    text=f"{name} recovered and is {status} again",
                    source="graph", kind="resolved", intensity=0.7,
                    meta={"agent": name, "from": before}))
            elif status == ACTIVE:
                events.append(Event(
                    text=f"{name} started working",
                    source="graph", kind="progress", intensity=0.4,
                    meta={"agent": name, "from": before}))
            # working → idle is deliberately silent. It could mean finished or
            # it could mean died, and claiming success for a node that quietly
            # stopped is worse than saying nothing. The activity level falling
            # already carries it.

            self._seen[name] = status

        self._first_poll = False
        return events

    def poll_once(self) -> int:
        """One fetch, one diff, one activity reading. Returns events emitted."""
        data = self.fetch()
        if data is None:
            return 0

        agents = data.get("agents") or {}
        if isinstance(agents, dict):
            for event in self.diff(agents):
                self.emit(event)

        if self.conductor is not None:
            working = int(data.get(ACTIVE, 0) or 0)
            total = sum(int(data.get(k, 0) or 0)
                        for k in (ACTIVE, STOPPED, BROKEN, "idle"))
            self.conductor.observe_activity(activity_from(working, total))

        return len(agents)

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        def loop():
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception:
                    pass          # a coordinator restarting must not stop the music
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, name="graph", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self.conductor is not None:
            self.conductor.observe_activity(None)   # stop asserting a level

    def describe(self) -> str:
        state = f"unreachable ({self.last_error})" if self.last_error else "polling"
        return f"agent graph at {self.url} — {state}"
