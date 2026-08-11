"""
The heuristic is the floor of this system: no API key, no network, a dead
model, a slow model — it runs. So it gets tested like a feature, not like a
fallback.

Every case in MISCLASSIFIED_IN_THE_WILD is one the classifier actually got
wrong when this was first run against real agent phrasing.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentisizer.interpret import heuristic


class TestHeuristic(unittest.TestCase):
    def assert_kind(self, text, expected):
        got = heuristic(text)
        self.assertEqual(
            got.kind, expected,
            f"{text!r}\n  expected {expected}, got {got.kind} ({got.intensity:.2f})")

    # ── cases that were wrong on the first real run ──────────────────────
    def test_asking_for_a_secret_is_blocked_not_progress(self):
        self.assert_kind("I need the staging database password to continue", "blocked")
        self.assert_kind("waiting for you to add the API key", "blocked")
        self.assert_kind("this needs your approval before I can proceed", "blocked")

    def test_recovery_is_resolved_not_good(self):
        self.assert_kind("credentials received, deploy is green again", "resolved")
        self.assert_kind("the build is passing again", "resolved")
        self.assert_kind("service is back online", "resolved")

    # ── the ordinary cases ───────────────────────────────────────────────
    def test_routine_work_is_progress(self):
        for t in ["reading main.py to understand the call graph",
                  "running the test suite",
                  "looking at the database schema"]:
            self.assert_kind(t, "progress")

    def test_success(self):
        for t in ["all 240 tests passed",
                  "deployed to production successfully",
                  "the fix worked"]:
            self.assert_kind(t, "good")

    def test_failure(self):
        for t in ["TypeError in parse_headers, three tests failing",
                  "the build broke",
                  "hit an exception while parsing"]:
            self.assert_kind(t, "bad")

    def test_blocked(self):
        for t in ["blocked on the missing credential",
                  "permission denied, cannot continue",
                  "rate limit exceeded"]:
            self.assert_kind(t, "blocked")

    # ── intensity ────────────────────────────────────────────────────────
    def test_severity_words_raise_intensity(self):
        plain = heuristic("the tests failed")
        severe = heuristic("critical production outage, tests failed")
        self.assertGreater(severe.intensity, plain.intensity)

    def test_diminishers_lower_intensity(self):
        plain = heuristic("the tests failed")
        minor = heuristic("a minor cosmetic test failed")
        self.assertLess(minor.intensity, plain.intensity)

    def test_intensity_always_in_range(self):
        for t in ["critical fatal emergency production outage data loss breach",
                  "minor trivial nit typo cosmetic",
                  ""]:
            self.assertGreaterEqual(heuristic(t).intensity, 0.0)
            self.assertLessEqual(heuristic(t).intensity, 1.0)

    def test_empty_and_junk_never_raise(self):
        for t in ["", "   ", "!!!", "\n\n", "𝕘𝕒𝕣𝕓𝕒𝕘𝕖"]:
            self.assertIn(heuristic(t).kind,
                          ("progress", "good", "bad", "blocked", "resolved"))


if __name__ == "__main__":
    unittest.main()
