"""
The conductor: everything between "an event happened" and "the music changed".

This is where the project either works or becomes unbearable, so the rules
here are deliberate and they are hard rules, not preferences.

What it is defending against, concretely:

  1. Fifty events in two seconds. A naive system plays fifty sounds. This
     converts event *rate* into a continuous `activity` level and plays at
     most one accent every few seconds. Density is a texture, not a queue.
  2. State that never comes down. Everything decays toward calm, always. If
     the music can't return to quiet, the listener stops hearing any of it
     within twenty minutes and the whole thing is pointless.
  3. Alarms that arrive at full volume. A blocker ramps over minutes, so a
     thing that resolves itself in thirty seconds never becomes a siren.
  4. A language model with its hands on the volume. The supervisor may
     propose adjustments; it can only move a few parameters, inside clamps
     it cannot widen. Rules dispose.

The four numbers sent to the engine:

    activity  0..1   how busy — drives density and brightness
    valence  -1..1   mood — good news vs bad news, recent-weighted
    tension   0..1   unresolved problems — adds in-key dissonance
    blocker   0..1   escalation while something is stuck
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .events import Event
from .musical import Harmony, harmony_for


# ── tuning. These are musical decisions, so they live together, named. ───
@dataclass
class Tuning:
    tick_hz: float = 4.0             # how often state is pushed to the engine

    # Decay half-lives in seconds: how long until a value falls halfway back
    # to rest. Long enough to feel like music, short enough to notice change.
    activity_halflife: float = 25.0
    tension_halflife: float = 90.0
    valence_halflife: float = 45.0

    # An event's contribution. Small on purpose — activity should reflect a
    # sustained rate of work, not a single loud moment.
    activity_per_event: float = 0.22
    tension_per_bad: float = 0.30
    tension_relief: float = 0.55     # how much `resolved` removes

    # Blockers escalate on a clock, not on a counter. Two minutes of being
    # stuck is a different sound from ten seconds of being stuck.
    #
    # Rise slow, fall fast — and deliberately asymmetric. Patience while the
    # problem persists, but once you've fixed it the alarm has to get out of
    # the way quickly. An alarm that keeps nagging after the fix is the thing
    # that makes people mute the whole system.
    blocker_ramp_seconds: float = 180.0
    blocker_decay_seconds: float = 7.0

    # Musical spacing: the minimum gap between accents of the same kind.
    # This is the single most important listenability control in the file.
    # `None` means never accent — not "a very long gap", which would still
    # let the first one through.
    min_gap: dict = field(default_factory=lambda: {
        "good": 3.5,
        "bad": 4.0,
        "resolved": 2.0,
        "blocked": 20.0,   # the alarm layer carries this; the hit is just onset
        "progress": None,  # progress never gets an accent. It is the texture.
    })

    # Ceilings the supervisor cannot raise.
    max_activity: float = 1.0
    max_tension: float = 0.9


def _decay(value: float, halflife: float, dt: float, floor: float = 0.0) -> float:
    if halflife <= 0:
        return floor
    return floor + (value - floor) * (0.5 ** (dt / halflife))


class Conductor:
    """
    Holds musical state, applies the rules, drives the engine on a tick.

    Thread-safe: sources call `submit()` from wherever they like.
    """

    def __init__(self, sonic, tuning: Tuning | None = None, on_change=None):
        self.sonic = sonic
        self.t = tuning or Tuning()
        self.on_change = on_change          # optional callback for the UI

        self.activity = 0.0
        self.valence = 0.0
        self.tension = 0.0
        self.blocker = 0.0

        self._blocked_since: float | None = None
        self._last_hit: dict[str, float] = {}
        self._recent: deque[tuple[float, Event]] = deque(maxlen=200)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tick = time.time()
        self._started = time.time()
        self.events_seen = 0
        self.harmony = harmony_for(0.0, 0.0, 0.0)

        # Bounded trims the supervisor is allowed to set (see supervisor.py).
        self.gain_trim = 1.0        # 0.7 .. 1.15
        self.density_trim = 1.0     # 0.7 .. 1.15

    # ── input ────────────────────────────────────────────────────────────
    def submit(self, event: Event) -> None:
        """Fold one event into the musical state. Called from any thread."""
        kind = event.kind or "progress"
        now = time.time()

        with self._lock:
            self.events_seen += 1
            self._recent.append((now, event))

            # Every event, whatever it is, is evidence that work is happening.
            self.activity = min(
                self.t.max_activity,
                self.activity + self.t.activity_per_event * (0.5 + event.intensity),
            )

            if kind == "good":
                self.valence = min(1.0, self.valence + 0.35 * event.intensity)
                self.tension = max(0.0, self.tension - 0.10 * event.intensity)
            elif kind == "bad":
                self.valence = max(-1.0, self.valence - 0.40 * event.intensity)
                self.tension = min(
                    self.t.max_tension,
                    self.tension + self.t.tension_per_bad * event.intensity,
                )
            elif kind == "blocked":
                self.valence = max(-1.0, self.valence - 0.5 * event.intensity)
                self.tension = min(self.t.max_tension, self.tension + 0.2)
                if self._blocked_since is None:
                    self._blocked_since = now       # start the ramp
            elif kind == "resolved":
                self.valence = min(1.0, self.valence + 0.45)
                self.tension = max(0.0, self.tension - self.t.tension_relief)
                self._blocked_since = None          # stand the alarm down

            accent = self._may_accent(kind, now)

        # Fire the accent outside the lock — never hold it across a socket.
        if accent:
            self.sonic.hit(kind)

    def _may_accent(self, kind: str, now: float) -> bool:
        """
        Musical spacing. An accent that arrives too soon after the last one
        of its kind is dropped — the continuous state already carries the
        information, and two bells on top of each other is just a clang.
        """
        gap = self.t.min_gap.get(kind, 5.0)
        if gap is None:
            return False
        last = self._last_hit.get(kind, -1e9)
        if now - last < gap:
            return False
        self._last_hit[kind] = now
        return True

    # ── the clock ────────────────────────────────────────────────────────
    def _tick(self) -> None:
        now = time.time()
        dt = now - self._last_tick
        self._last_tick = now

        with self._lock:
            self.activity = _decay(self.activity, self.t.activity_halflife, dt)
            self.tension = _decay(self.tension, self.t.tension_halflife, dt)
            self.valence = _decay(self.valence, self.t.valence_halflife, dt)

            # The blocker is the one thing that grows while you ignore it.
            if self._blocked_since is not None:
                stuck = now - self._blocked_since
                self.blocker = min(1.0, stuck / self.t.blocker_ramp_seconds)
            elif self.blocker > 0:
                self.blocker = max(0.0, self.blocker - dt / self.t.blocker_decay_seconds)

            a = min(self.t.max_activity, self.activity * self.density_trim) * self.gain_trim
            v, ten, blk = self.valence, self.tension, self.blocker
            self.harmony = harmony_for(v, ten, now - self._started)
            harmony = self.harmony

        self.sonic.state(a, v, ten, blk, harmony.tonic_offset, harmony.mode_index)
        if self.on_change:
            self.on_change(self.snapshot())

    def snapshot(self) -> dict:
        return {
            "activity": round(self.activity, 3),
            "valence": round(self.valence, 3),
            "tension": round(self.tension, 3),
            "blocker": round(self.blocker, 3),
            "events": self.events_seen,
            "blocked_for": (round(time.time() - self._blocked_since, 1)
                            if self._blocked_since else 0.0),
            "key": self.harmony.name(),
        }

    def recent_events(self, n: int = 20) -> list[Event]:
        with self._lock:
            return [e for _, e in list(self._recent)[-n:]]

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()

        def loop():
            period = 1.0 / self.t.tick_hz
            while not self._stop.is_set():
                try:
                    self._tick()
                except Exception:
                    pass          # a bad tick must never kill the music
                self._stop.wait(period)

        self._thread = threading.Thread(target=loop, name="conductor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
