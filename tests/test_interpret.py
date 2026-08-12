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




class TestBackendChain(unittest.TestCase):
    """
    A stale API key must not shadow a working local model.

    This was real: an expired OPENROUTER_API_KEY made auto pick OpenRouter,
    fail, and fall back to keyword rules — while a perfectly good Ollama sat
    unused on the same machine.
    """

    def _auto(self, has_key: bool, has_ollama: bool):
        import os
        from unittest import mock
        from agentisizer.interpret import Interpreter
        env = {"OPENROUTER_API_KEY": "sk-test" if has_key else ""}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(Interpreter, "_ollama_alive", lambda self: has_ollama), \
             mock.patch.object(Interpreter, "_first_ollama_model", lambda self: "llama3.2:3b"):
            return Interpreter(backend="auto")

    def test_chain_prefers_openrouter_then_ollama_then_rules(self):
        i = self._auto(has_key=True, has_ollama=True)
        self.assertEqual(i.backend, "openrouter")
        self.assertEqual(i._chain, ["openrouter", "ollama", "heuristic"])

    def test_dead_key_demotes_to_the_local_model(self):
        i = self._auto(has_key=True, has_ollama=True)
        self.assertTrue(i.demote())
        self.assertEqual(i.backend, "ollama")

    def test_demotion_bottoms_out_at_the_rules(self):
        i = self._auto(has_key=True, has_ollama=True)
        i.demote(); i.demote()
        self.assertEqual(i.backend, "heuristic")
        self.assertFalse(i.demote(), "nowhere left to go")

    def test_no_ollama_means_key_then_rules(self):
        i = self._auto(has_key=True, has_ollama=False)
        self.assertEqual(i._chain, ["openrouter", "heuristic"])

    def test_nothing_configured_is_just_rules(self):
        i = self._auto(has_key=False, has_ollama=False)
        self.assertEqual(i.backend, "heuristic")
        self.assertFalse(i.demote())

if __name__ == "__main__":
    unittest.main()
