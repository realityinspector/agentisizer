"""
Guards on the engine that only fail at runtime otherwise.

Both of these cost real debugging time once. Neither is visible by reading
the Ruby, and neither shows up until Sonic Pi is actually running.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentisizer.sonicpi import ENGINE, SonicPi


SONIC_PI_LANG = Path(
    "/Applications/Sonic Pi.app/Contents/Resources/app/server/ruby/lib/sonicpi/lang")

# One UDP datagram, minus room for the OSC address, the token and padding.
# Measured: 60 KB goes through, 64 KB raises OSError [Errno 40].
DATAGRAM_BUDGET = 60_000


class TestEngineFitsOnTheWire(unittest.TestCase):
    """
    `/run-code` is one UDP datagram and there is no second chance.

    This is not hypothetical: the engine grew past the default 9216-byte send
    buffer while being commented, and loading it started raising OSError. The
    socket buffer is raised at connect time and comments are stripped before
    sending, which between them bought a lot of headroom — but headroom is
    only headroom if something watches it.
    """

    def setUp(self):
        self.stripped = SonicPi._strip_comments(ENGINE.read_text())

    def test_engine_fits_with_room_to_spare(self):
        size = len(self.stripped.encode())
        self.assertLess(
            size, DATAGRAM_BUDGET,
            f"engine is {size} bytes; /run-code cannot carry it in one datagram")
        # Fail while there is still time to think, not at the cliff edge.
        self.assertLess(
            size, DATAGRAM_BUDGET * 0.5,
            f"engine is {size} bytes, over half the {DATAGRAM_BUDGET} byte budget — "
            f"split it or move code into the repo before it stops fitting")

    def test_stripping_removes_comments_but_not_code(self):
        self.assertNotIn("# ──", self.stripped)
        for essential in ("live_loop :bed", "live_loop :alarm", "define :tone_at",
                          "sync \"/osc*/agentisizer/state\""):
            self.assertIn(essential, self.stripped, f"stripping ate {essential!r}")

    def test_stripping_leaves_inline_comments_alone(self):
        """
        Only whole-line comments are removed. Parsing trailing `#` would mean
        parsing Ruby strings, and getting that subtly wrong corrupts the
        program in a way that is very hard to see.
        """
        code = 'play 60  # a note\n# a comment line\nsleep 1\n'
        self.assertEqual(SonicPi._strip_comments(code), 'play 60  # a note\nsleep 1')


class TestNoCollisionsWithSonicPi(unittest.TestCase):
    """
    `define :degree` throws at runtime: "a function called degree is already
    part of Sonic Pi's core API". Nothing about the Ruby hints at that, and it
    only surfaces once the engine is loaded, so the whole soundtrack fails to
    start over a name.
    """

    def builtins(self) -> set[str]:
        if not SONIC_PI_LANG.is_dir():
            self.skipTest("Sonic Pi not installed here")
        names = set()
        for f in SONIC_PI_LANG.glob("*.rb"):
            names.update(re.findall(r"^\s+def ([a-z_][a-z0-9_]*)",
                                    f.read_text(errors="replace"), re.M))
        self.assertGreater(len(names), 100, "extraction looks wrong")
        return names

    def test_engine_defines_no_reserved_names(self):
        builtins = self.builtins()
        defined = re.findall(r"define :([a-z_][a-z0-9_]*)", ENGINE.read_text())
        self.assertTrue(defined, "expected the engine to define helpers")
        clashes = sorted(set(defined) & builtins)
        self.assertEqual(
            clashes, [],
            f"these collide with Sonic Pi's core API and will throw on load: {clashes}")

    def test_the_check_would_catch_the_one_that_bit_us(self):
        """A guard nobody has seen fire is not a guard."""
        self.assertIn("degree", self.builtins())


if __name__ == "__main__":
    unittest.main()
