"""
The graph source, which is the only input that reports state rather than
events — so it has failure modes the others cannot have.

No coordinator needed: these drive `diff()` and `activity_from()` directly.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentisizer.conductor import Conductor, Tuning
from agentisizer.sources.graph import GraphSource, activity_from


class FakeSonic:
    def __init__(self):
        self.states, self.hits = [], []
    def hit(self, kind): self.hits.append(kind)
    def state(self, a, v, t, b, tonic=0, mode=4): self.states.append((a, v, t, b))


def source():
    got = []
    return GraphSource(got.append, url="http://127.0.0.1:1/none"), got


class TestActivityLevel(unittest.TestCase):
    def test_nothing_working_is_silence(self):
        self.assertEqual(activity_from(0, 8), 0.0)

    def test_a_lone_agent_in_a_big_fleet_is_still_audible(self):
        """Pure fraction would make this 0.125 — near silence while work happens."""
        self.assertGreaterEqual(activity_from(1, 20), 0.2)

    def test_a_lone_agent_in_a_small_fleet_is_not_deafening(self):
        """Pure count would make this quiet; pure fraction would make it 0.5."""
        self.assertLessEqual(activity_from(1, 2), 0.6)

    def test_a_busy_fleet_saturates(self):
        self.assertEqual(activity_from(8, 8), 1.0)
        self.assertEqual(activity_from(40, 40), 1.0)

    def test_monotonic_in_working_count(self):
        last = -1
        for w in range(0, 11):
            a = activity_from(w, 10)
            self.assertGreaterEqual(a, last)
            last = a

    def test_empty_graph_does_not_divide_by_zero(self):
        self.assertEqual(activity_from(0, 0), 0.0)

    def test_never_leaves_range(self):
        for w in (0, 1, 5, 100):
            for t in (0, 1, 8, 100):
                self.assertGreaterEqual(activity_from(w, t), 0.0)
                self.assertLessEqual(activity_from(w, t), 1.0)


class TestTransitions(unittest.TestCase):
    def test_first_poll_stays_quiet_about_ordinary_agents(self):
        """Nine idle nodes must not open with nine events."""
        g, _ = source()
        events = g.diff({"a": "idle", "b": "working", "c": "idle"})
        self.assertEqual(events, [])

    def test_but_announces_a_blocker_it_arrived_late_to(self):
        """Staying quiet about a blocker because we started late is the wrong failure."""
        g, _ = source()
        events = g.diff({"a": "idle", "b": "blocked"})
        self.assertEqual([e.kind for e in events], ["blocked"])

    def test_becoming_blocked_raises_the_alarm(self):
        g, _ = source()
        g.diff({"a": "working"})
        events = g.diff({"a": "blocked"})
        self.assertEqual([e.kind for e in events], ["blocked"])
        self.assertIn("a", events[0].text)

    def test_leaving_blocked_resolves_it(self):
        g, _ = source()
        g.diff({"a": "blocked"})
        events = g.diff({"a": "working"})
        self.assertEqual([e.kind for e in events], ["resolved"])

    def test_failure_is_bad_not_blocked(self):
        g, _ = source()
        g.diff({"a": "working"})
        self.assertEqual([e.kind for e in g.diff({"a": "failed"})], ["bad"])

    def test_working_to_idle_says_nothing(self):
        """Finished and died look identical here; claiming success would lie."""
        g, _ = source()
        g.diff({"a": "working"})
        self.assertEqual(g.diff({"a": "idle"}), [])

    def test_unchanged_status_emits_nothing_however_often_polled(self):
        g, _ = source()
        g.diff({"a": "blocked"})
        for _ in range(10):
            self.assertEqual(g.diff({"a": "blocked"}), [])

    def test_a_new_agent_appearing_at_work_is_progress(self):
        g, _ = source()
        g.diff({"a": "idle"})
        self.assertEqual([e.kind for e in g.diff({"a": "idle", "b": "working"})],
                         ["progress"])

    def test_unknown_statuses_are_not_guessed_at(self):
        g, _ = source()
        g.diff({"a": "idle"})
        self.assertEqual(g.diff({"a": "quiescent"}), [])


class TestObservedActivity(unittest.TestCase):
    """An authoritative reading beats inference, but must not outlive its source."""

    def test_reading_raises_activity_without_any_events(self):
        c = Conductor(FakeSonic())
        c._tick()
        self.assertEqual(c.sonic.states[-1][0], 0.0)
        c.observe_activity(0.6)
        c._tick()
        self.assertAlmostEqual(c.sonic.states[-1][0], 0.6, places=3)

    def test_events_can_still_push_above_the_reading(self):
        from agentisizer.events import Event
        c = Conductor(FakeSonic())
        c.observe_activity(0.3)
        for _ in range(8):
            c.submit(Event(text="x", kind="progress", intensity=1.0))
        c._tick()
        self.assertGreater(c.sonic.states[-1][0], 0.3)

    def test_a_stale_reading_is_released(self):
        """If the coordinator dies, the music must not stay pinned at its last value."""
        c = Conductor(FakeSonic(), Tuning(observed_ttl=0.05))
        c.observe_activity(0.9)
        c._tick()
        self.assertAlmostEqual(c.sonic.states[-1][0], 0.9, places=3)
        import time; time.sleep(0.1)
        c._tick()
        self.assertLess(c.sonic.states[-1][0], 0.9)

    def test_none_clears_the_reading(self):
        c = Conductor(FakeSonic())
        c.observe_activity(0.8)
        c.observe_activity(None)
        c._tick()
        self.assertEqual(c.sonic.states[-1][0], 0.0)

    def test_readings_are_clamped(self):
        c = Conductor(FakeSonic())
        for value, expected in ((5.0, 1.0), (-3.0, 0.0)):
            c.observe_activity(value)
            c._tick()
            self.assertAlmostEqual(c.sonic.states[-1][0], expected, places=3)

    def test_snapshot_exposes_it_for_the_map_to_read_back(self):
        c = Conductor(FakeSonic())
        self.assertIsNone(c.snapshot()["observed"])
        c.observe_activity(0.42)
        self.assertAlmostEqual(c.snapshot()["observed"], 0.42, places=3)


class TestResilience(unittest.TestCase):
    def test_an_unreachable_coordinator_is_survivable(self):
        g, got = source()
        self.assertIsNone(g.fetch())
        self.assertEqual(g.poll_once(), 0)
        self.assertEqual(got, [])
        self.assertIn("unreachable", g.describe())


if __name__ == "__main__":
    unittest.main()
