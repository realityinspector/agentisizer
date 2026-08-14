"""
Finding and stopping a running instance.

From a real dead end: the daemon was started by another session, there was no
way to stop it from a second terminal, and starting a second one produced

    OSError: [Errno 48] Address already in use

with no indication of what to do. Both halves are covered here.
"""

import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentisizer import process


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestDetection(unittest.TestCase):
    def test_a_free_port_reads_as_not_running(self):
        port = free_port()
        self.assertFalse(process.port_busy(port))
        st = process.status(port)
        self.assertFalse(st["running"])
        self.assertFalse(st["ours"])

    def test_a_busy_port_is_detected_even_when_it_is_not_ours(self):
        """
        The case that produced the traceback: something holds the port. We
        must notice, and must not claim it as our daemon.
        """
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); s.listen(1)
            port = s.getsockname()[1]
            self.assertTrue(process.port_busy(port))
            st = process.status(port)
            self.assertTrue(st["running"], "must notice the port is taken")
            self.assertFalse(st["ours"], "must not mistake a stranger for us")

    def test_live_state_of_a_stranger_is_none(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); s.listen(1)
            self.assertIsNone(process.live_state(s.getsockname()[1]))


class TestStopping(unittest.TestCase):
    def test_stopping_nothing_is_not_an_error(self):
        ok, msg = process.stop(free_port())
        self.assertFalse(ok)
        self.assertIn("nothing", msg.lower())

    def test_stopping_a_free_port_never_finds_a_daemon_elsewhere(self):
        """
        This one bit for real. The pgrep fallback matched any running
        `agentisizer.cli start`, so asking about a free port returned the pid
        of the live daemon on another port — and this very test then killed
        it. Nothing is running on a port nothing is listening on.
        """
        self.assertIsNone(process.daemon_pid(free_port()))

    def test_stopping_a_stranger_refuses_and_says_how_to_look(self):
        """Never kill a process we cannot identify as ours."""
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); s.listen(1)
            port = s.getsockname()[1]
            original = process.daemon_pid
            process.daemon_pid = lambda p=None: None      # cannot identify it
            try:
                ok, msg = process.stop(port)
            finally:
                process.daemon_pid = original
            self.assertFalse(ok)
            self.assertIn("lsof", msg, "should tell you how to find out what it is")


class TestPidfile(unittest.TestCase):
    def test_write_then_clear_is_clean(self):
        process.write_pidfile()
        self.assertTrue(process.PIDFILE.exists())
        process.clear_pidfile()
        self.assertFalse(process.PIDFILE.exists())

    def test_clearing_a_missing_pidfile_does_not_raise(self):
        process.clear_pidfile()
        process.clear_pidfile()


if __name__ == "__main__":
    unittest.main()
