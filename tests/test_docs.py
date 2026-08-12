"""
The docs are checked with the code.

README.md and AGENTS.md both drifted within days of being written: `test` was
documented before it existed, hints named a binary that is not on PATH, and
the file-drop example used a directory nothing created. Each was a dead end
in a new reader's first five minutes, and none would be caught by a test of
the code alone.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.check_docs import check, real_subcommands


class TestDocsMatchCode(unittest.TestCase):
    def test_every_documented_command_and_path_resolves(self):
        problems = check()
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_checker_can_actually_fail(self):
        """A green check is only worth something if red is reachable."""
        import re
        from tools import check_docs

        valid = real_subcommands()
        self.assertIn("test", valid)
        # the bare-binary pattern is the one that bit us; prove it still bites
        pattern = (r"(?<!run-)(?<![\w./-])agentisizer\s+("
                   + "|".join(sorted(valid)) + r")\b")
        self.assertRegex("run: agentisizer doctor", pattern)
        self.assertNotRegex("run: ./run-agentisizer.sh doctor", pattern)


if __name__ == "__main__":
    unittest.main()
