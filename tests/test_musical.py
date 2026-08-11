"""
Key and mode selection.

Verified against recorded audio once, by chroma analysis: at a bright mood
the flat second was 0.9% of harmonic energy, at a dark mood 52.8%. These
tests hold the logic that produced that to the same shape, cheaply, on every
run.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentisizer.musical import (
    DARKEST, MODES, TONIC_CYCLE, harmony_for, mode_for, tonic_for,
)


class TestBrightnessLadder(unittest.TestCase):
    def test_modes_are_ordered_bright_to_dark(self):
        self.assertEqual(MODES[0], "lydian")
        self.assertEqual(MODES[-1], "phrygian")

    def test_locrian_is_excluded(self):
        # No perfect fifth — it reads as broken rather than dark.
        self.assertNotIn("locrian", MODES)

    def test_good_news_brightens_bad_news_darkens(self):
        happy = mode_for(0.9, 0.0)
        neutral = mode_for(0.0, 0.0)
        sad = mode_for(-0.9, 0.9)
        self.assertLess(happy, neutral, "good news should move toward the bright end")
        self.assertGreater(sad, neutral, "bad news should move toward the dark end")

    def test_monotonic_in_valence(self):
        """More good news must never darken the mode."""
        last = None
        for v in [x / 10 for x in range(-10, 11)]:
            idx = mode_for(v, 0.0)
            if last is not None:
                self.assertLessEqual(idx, last, f"went darker at valence {v}")
            last = idx

    def test_monotonic_in_tension(self):
        """More trouble must never brighten the mode."""
        last = None
        for t in [x / 10 for x in range(0, 11)]:
            idx = mode_for(0.0, t)
            if last is not None:
                self.assertGreaterEqual(idx, last, f"went brighter at tension {t}")
            last = idx

    def test_tension_outweighs_valence(self):
        """
        Good news alongside real trouble should not read as sunny. A
        soundtrack that turns radiant while things are on fire is one nobody
        believes.
        """
        self.assertGreater(mode_for(0.5, 0.6), mode_for(0.5, 0.0))

    def test_crisis_reaches_phrygian(self):
        """The dark end has to be reachable, or the ladder is decorative."""
        self.assertEqual(mode_for(-1.0, 1.0), DARKEST)
        self.assertEqual(MODES[DARKEST], "phrygian")

    def test_never_leaves_the_usable_range(self):
        for v in [x / 5 for x in range(-5, 6)]:
            for t in [x / 5 for x in range(0, 6)]:
                idx = mode_for(v, t)
                self.assertGreaterEqual(idx, 1, "lydian is too weightless to use")
                self.assertLessEqual(idx, DARKEST)


class TestModulation(unittest.TestCase):
    def test_key_changes_slowly(self):
        """Within one period the key must not move."""
        self.assertEqual(tonic_for(0, 0.0), tonic_for(479, 0.0))
        self.assertNotEqual(tonic_for(0, 0.0), tonic_for(481, 0.0))

    def test_key_cycles_through_related_keys(self):
        seen = {tonic_for(i * 480 + 1, 0.0) for i in range(len(TONIC_CYCLE))}
        self.assertEqual(seen, set(TONIC_CYCLE))

    def test_no_modulation_during_trouble(self):
        """Changing key mid-crisis sounds like the floor moving."""
        calm = tonic_for(481, 0.0)
        troubled = tonic_for(481, 0.8)
        self.assertNotEqual(calm, troubled)
        self.assertEqual(troubled, tonic_for(1, 0.0), "should hold the previous key")

    def test_related_keys_only(self):
        """Every step shares most of its notes with home."""
        for offset in TONIC_CYCLE:
            self.assertIn(offset, (0, 3, 5, 7))


class TestHarmony(unittest.TestCase):
    def test_names_are_readable(self):
        self.assertEqual(harmony_for(0.0, 0.0, 0).name(), "A dorian")
        self.assertEqual(harmony_for(-1.0, 1.0, 0).name(), "A phrygian")

    def test_neutral_rests_in_dorian(self):
        """Over hours natural minor reads as mournful; dorian reads as calm."""
        self.assertEqual(harmony_for(0.0, 0.0, 0).mode, "dorian")

    def test_indices_are_valid(self):
        for v in (-1.0, 0.0, 1.0):
            for t in (0.0, 0.5, 1.0):
                h = harmony_for(v, t, 0)
                self.assertIn(h.mode, MODES)
                self.assertIn(h.tonic_offset, TONIC_CYCLE)


if __name__ == "__main__":
    unittest.main()
