"""
The conductor's rules are the difference between a soundtrack and an alarm
clock you throw across the room, so they get tested directly.

No Sonic Pi needed — a fake engine records what it was told.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentisizer.conductor import Conductor, Tuning, _decay
from agentisizer.events import Event


class FakeSonic:
    def __init__(self):
        self.hits = []
        self.states = []

    def hit(self, kind):
        self.hits.append((time.time(), kind))

    def state(self, a, v, t, b, tonic=0, mode=4):
        self.states.append((a, v, t, b, tonic, mode))


def ev(text="x", kind="progress", intensity=0.5):
    return Event(text=text, kind=kind, intensity=intensity)


class TestSpacing(unittest.TestCase):
    """The flood problem: many events must not become many sounds."""

    def test_a_burst_of_events_produces_one_accent(self):
        sonic = FakeSonic()
        c = Conductor(sonic)
        for _ in range(50):
            c.submit(ev(kind="good"))
        good = [h for h in sonic.hits if h[1] == "good"]
        self.assertEqual(len(good), 1, "50 good events should accent once, not 50 times")

    def test_progress_never_accents(self):
        """Routine work is the texture. If it dinged, it would be unbearable."""
        sonic = FakeSonic()
        c = Conductor(sonic)
        for _ in range(20):
            c.submit(ev(kind="progress"))
        self.assertEqual([h for h in sonic.hits if h[1] == "progress"], [])

    def test_but_the_burst_still_raises_activity(self):
        """Dropped accents must not mean dropped information."""
        sonic = FakeSonic()
        c = Conductor(sonic)
        before = c.activity
        for _ in range(10):
            c.submit(ev(kind="progress"))
        self.assertGreater(c.activity, before)

    def test_different_kinds_do_not_block_each_other(self):
        sonic = FakeSonic()
        c = Conductor(sonic)
        c.submit(ev(kind="good"))
        c.submit(ev(kind="bad"))
        self.assertEqual({h[1] for h in sonic.hits}, {"good", "bad"})


class TestDecay(unittest.TestCase):
    """Everything must be able to return to calm."""

    def test_decay_halves_at_the_halflife(self):
        self.assertAlmostEqual(_decay(1.0, 10.0, 10.0), 0.5, places=6)
        self.assertAlmostEqual(_decay(1.0, 10.0, 20.0), 0.25, places=6)

    def test_activity_falls_when_nothing_happens(self):
        sonic = FakeSonic()
        c = Conductor(sonic, Tuning(activity_halflife=0.05))
        c.submit(ev(kind="progress"))
        peak = c.activity
        time.sleep(0.2)
        c._tick()
        self.assertLess(c.activity, peak)

    def test_tension_falls_too(self):
        sonic = FakeSonic()
        c = Conductor(sonic, Tuning(tension_halflife=0.05))
        c.submit(ev(kind="bad", intensity=1.0))
        peak = c.tension
        time.sleep(0.2)
        c._tick()
        self.assertLess(c.tension, peak)


class TestBlockerEscalation(unittest.TestCase):
    """A blocker should grow on a clock, not arrive at full volume."""

    def test_blocker_starts_near_zero_and_grows(self):
        sonic = FakeSonic()
        c = Conductor(sonic, Tuning(blocker_ramp_seconds=1.0))
        c.submit(ev(kind="blocked"))
        c._tick()
        self.assertLess(c.blocker, 0.2, "a fresh blocker must not be a siren")
        time.sleep(0.6)
        c._tick()
        self.assertGreater(c.blocker, 0.4, "it should be climbing by now")

    def test_resolved_stands_the_alarm_down(self):
        sonic = FakeSonic()
        c = Conductor(sonic, Tuning(blocker_ramp_seconds=0.2, blocker_decay_seconds=0.05))
        c.submit(ev(kind="blocked"))
        time.sleep(0.3)
        c._tick()
        self.assertGreater(c.blocker, 0.5)
        c.submit(ev(kind="resolved"))
        time.sleep(0.2)
        c._tick()
        self.assertLess(c.blocker, 0.3, "resolving must actually quiet the alarm")

    def test_resolved_relieves_tension(self):
        sonic = FakeSonic()
        c = Conductor(sonic)
        c.submit(ev(kind="bad", intensity=1.0))
        peak = c.tension
        c.submit(ev(kind="resolved"))
        self.assertLess(c.tension, peak)


class TestHarmonyReachesTheEngine(unittest.TestCase):
    """Mood has to actually move the key, not just the volume."""

    def test_bad_news_darkens_the_mode_sent_to_sonic_pi(self):
        sonic = FakeSonic()
        c = Conductor(sonic)
        c._tick()
        calm_mode = sonic.states[-1][5]
        for _ in range(10):
            c.submit(ev(kind="bad", intensity=1.0))
        c._tick()
        self.assertGreater(sonic.states[-1][5], calm_mode,
                           "trouble should send a darker mode index")

    def test_key_is_reported_in_the_snapshot(self):
        sonic = FakeSonic()
        c = Conductor(sonic)
        c._tick()
        self.assertIn("dorian", c.snapshot()["key"])


class TestBounds(unittest.TestCase):
    """Nothing may leave the range the engine expects."""

    def test_state_stays_in_range_under_abuse(self):
        sonic = FakeSonic()
        c = Conductor(sonic)
        for kind in ("good", "bad", "blocked", "resolved", "progress"):
            for _ in range(200):
                c.submit(ev(kind=kind, intensity=1.0))
        c._tick()
        a, v, t, b, _, _ = sonic.states[-1]
        self.assertGreaterEqual(a, 0.0); self.assertLessEqual(a, 1.0)
        self.assertGreaterEqual(v, -1.0); self.assertLessEqual(v, 1.0)
        self.assertGreaterEqual(t, 0.0); self.assertLessEqual(t, 1.0)
        self.assertGreaterEqual(b, 0.0); self.assertLessEqual(b, 1.0)

    def test_snapshot_reports_blocked_duration(self):
        sonic = FakeSonic()
        c = Conductor(sonic)
        self.assertEqual(c.snapshot()["blocked_for"], 0.0)
        c.submit(ev(kind="blocked"))
        time.sleep(0.05)
        self.assertGreater(c.snapshot()["blocked_for"], 0.0)


if __name__ == "__main__":
    unittest.main()
