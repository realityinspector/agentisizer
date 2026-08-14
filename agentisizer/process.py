"""
Knowing whether it is already running, and stopping it if so.

This exists because of a real failure: the daemon was started in one terminal,
and from another there was no way to stop it and no way to find out it was
there. Starting a second one produced a raw traceback ending in

    OSError: [Errno 48] Address already in use

which tells you nothing about what to do next, and Ctrl-C was not available
because the process belonged to a different session. Both halves of that —
not knowing, and not being able to stop — are fixed here.

The port is the source of truth rather than the pidfile. A pidfile can be
stale after a crash, but something answering on 8912 is running now, whoever
wrote what.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path


PIDFILE = Path.home() / ".agentisizer" / "daemon.pid"


def port_busy(port: int) -> bool:
    """Is anything listening? Cheap, and does not care who."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def live_state(port: int) -> dict | None:
    """Ask the running instance what it is doing, or None if that isn't one of ours."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=1.5) as r:
            data = json.loads(r.read().decode())
        return data if data.get("ok") else None
    except Exception:
        return None


def daemon_pid(port: int = 8912) -> int | None:
    """
    The pid of the daemon serving *this port*, by any means that works.

    The port check first is not a nicety. Without it the pgrep fallback
    matches any `agentisizer.cli start` anywhere, so `stop --port 8913` would
    happily kill the instance on 8912 — and it did: a test calling
    `stop(<free port>)` to assert that stopping nothing is harmless reached
    out and killed the live daemon. Nothing is running on a port nothing is
    listening on, whatever pgrep has to say about it.

    After that, the pidfile is tried first because it is exact, then the
    command line, because the daemon may have been started by another session
    or by a coordinator — the case that stranded us to begin with.
    """
    if not port_busy(port):
        return None
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)                       # exists?
        return pid
    except Exception:
        pass
    try:
        out = subprocess.run(["pgrep", "-f", "agentisizer.cli start"],
                             capture_output=True, text=True, timeout=5).stdout
        for line in out.split():
            return int(line)
    except Exception:
        pass
    # Last resort: something is on the port but we cannot name it.
    return None


def write_pidfile() -> None:
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))


def clear_pidfile() -> None:
    try:
        PIDFILE.unlink()
    except OSError:
        pass


def status(port: int = 8912) -> dict:
    """One place that answers 'is it running, and what is it doing'."""
    state = live_state(port)
    return {
        "running": state is not None or port_busy(port),
        "ours": state is not None,
        "pid": daemon_pid(port),
        "state": state,
        "port": port,
    }


def stop(port: int = 8912, timeout: float = 8.0) -> tuple[bool, str]:
    """
    Stop the daemon and silence Sonic Pi.

    Politely first: SIGTERM lets the daemon run its own shutdown, which stops
    the sources and tells Sonic Pi to stop all jobs. Only if that is ignored
    do we escalate, and then we silence Sonic Pi ourselves — a killed daemon
    leaves the engine running, and music with nothing driving it is the worst
    possible end state.
    """
    pid = daemon_pid(port)
    if pid is None and not port_busy(port):
        _silence()
        return False, "nothing was running"

    if pid is None:
        return False, f"something is on port {port} but it is not ours — check `lsof -i :{port}`"

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        clear_pidfile()
        _silence()
        return True, "it had already exited; silenced Sonic Pi"
    except PermissionError:
        return False, f"pid {pid} is not ours to stop"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.3)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            clear_pidfile()
            _silence()          # belt and braces: the engine must not outlive it
            return True, f"stopped (pid {pid})"

    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    clear_pidfile()
    _silence()
    return True, f"stopped (pid {pid}, needed SIGKILL)"


def _silence() -> None:
    """Tell Sonic Pi to stop, if it is there. Never raises."""
    try:
        from .sonicpi import SonicPi
        sonic = SonicPi.connect()
        if sonic:
            sonic.stop_recording()
            sonic.stop()
    except Exception:
        pass
