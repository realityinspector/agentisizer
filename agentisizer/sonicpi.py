"""
Talking to Sonic Pi.

Two channels, and the difference matters:

  * /run-code on the server port — used exactly twice, to load the engine and
    to stop it. Sending code continuously is what makes these systems sound
    like a machine gun; we don't.
  * OSC cues on port 4560 — used constantly. The engine is already running
    and listening, so this is just state moving. Timing stays inside Sonic
    Pi's scheduler where it belongs.

Sonic Pi 4 and 5 stopped writing the token to spider.log (the daemon hands it
to the GUI over a pipe and only logs it while shutting down), so we read it
off the running process, where spider-server.rb's own ARGV order tells us
what each number is.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

from pythonosc import osc_message_builder, udp_client


ENGINE = Path(__file__).resolve().parent.parent / "engine" / "engine.rb"


class ServerInfo(NamedTuple):
    token: int
    server_port: int      # /run-code goes here
    osc_cues_port: int    # external OSC cues go here
    source: str


def parse_ps_output(out: str) -> ServerInfo | None:
    """
    Pull ports and token out of `ps` output.

        spider-server.rb -u 38893 38894 38895 38895 4560 1917343118
                         │  │                       │    └ token      ARGV[6]
                         │  │                       └ osc_cues_port   ARGV[5]
                         │  └ server_port                             ARGV[1]
                         └ protocol                                   ARGV[0]
    """
    for line in out.splitlines():
        if "spider-server.rb" not in line:
            continue
        tail = line.split("spider-server.rb", 1)[1].split()
        if len(tail) < 7 or tail[0] not in ("-u", "-t"):
            continue
        try:
            return ServerInfo(int(tail[6]), int(tail[1]), int(tail[5]), "running process")
        except ValueError:
            continue
    return None


def _from_log() -> ServerInfo | None:
    """Sonic Pi 3 kept these in the log. Kept so old installs still work."""
    log = Path.home() / ".sonic-pi" / "log" / "spider.log"
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return None
    tok = re.search(r"Token: (-?\d+)", text)
    port = re.search(r":server_port=>(\d+)", text)
    cues = re.search(r":osc_cues_port=>(\d+)", text)
    if tok and port and cues:
        return ServerInfo(int(tok.group(1)), int(port.group(1)), int(cues.group(1)), "spider.log")
    return None


def discover() -> ServerInfo | None:
    """Find a running Sonic Pi, or None."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "args"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    return parse_ps_output(out) or _from_log()


class SonicPi:
    """A thin, honest wrapper. No magic, no reconnect logic to get wrong."""

    def __init__(self, info: ServerInfo):
        self.info = info
        self._code = udp_client.UDPClient("127.0.0.1", info.server_port)
        self._cues = udp_client.UDPClient("127.0.0.1", info.osc_cues_port)

        # macOS ships a 9216-byte UDP send buffer and the engine outgrew it
        # once already. With this bump the ceiling is the UDP datagram itself:
        # measured, 60 KB goes through and 64 KB raises OSError. Failure is
        # loud either way — sendto raises rather than dropping — and
        # tests/test_engine.py fails long before we get near it.
        try:
            import socket
            self._code._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        except Exception:
            pass

    @classmethod
    def connect(cls) -> "SonicPi | None":
        info = discover()
        return cls(info) if info else None

    def _send(self, client, address: str, args) -> None:
        msg = osc_message_builder.OscMessageBuilder(address=address)
        for a in args:
            msg.add_arg(a)
        client.send(msg.build())

    # ── the two /run-code calls ──────────────────────────────────────────
    def run_code(self, code: str) -> None:
        self._send(self._code, "/run-code", [self.info.token, code])

    @staticmethod
    def _strip_comments(code: str) -> str:
        """
        Drop comment-only and blank lines before sending.

        /run-code goes over UDP, and one datagram is all we get: the engine
        is heavily commented for people reading the repo, which pushed it to
        9.7 KB against a 9.2 KB send buffer. The comments are documentation,
        not instructions, so they don't need to be on the wire.

        Only whole-line comments are removed. Trailing `#` on a line of code
        is left alone rather than parsed — a `#` inside a Ruby string would
        make naive stripping silently corrupt the program, and 4.5 KB is
        already ample headroom.
        """
        return "\n".join(
            line for line in code.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

    def load_engine(self, path: Path | None = None) -> None:
        code = self._strip_comments((path or ENGINE).read_text())
        payload = len(code.encode())
        if payload > 60000:
            raise RuntimeError(
                f"engine is {payload} bytes and /run-code is one UDP datagram "
                f"(~64 KB ceiling). Split it, or move logic into Python.")
        self.run_code(code)

    def stop(self) -> None:
        self.run_code("stop")

    def ping(self, timeout: float = 30.0) -> bool:
        """
        Prove Sonic Pi can actually *execute*, not merely that it is running.

        The process appears in `ps` well before its OSC server will accept
        anything, so a process check alone lets `setup` announce success into
        silence — the worst possible first impression. This asks Sonic Pi to
        write a file and waits for it, which is the same thing the user is
        about to depend on.
        """
        import tempfile

        probe = Path(tempfile.gettempdir()) / f"agentisizer_ping_{os.getpid()}"
        probe.unlink(missing_ok=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.run_code(f'File.write("{probe}", "ok")')
            time.sleep(1.0)
            if probe.exists():
                probe.unlink(missing_ok=True)
                return True
        return False

    # ── the channel that actually carries the performance ────────────────
    def state(self, activity: float, valence: float, tension: float, blocker: float,
              tonic_offset: int = 0, mode_index: int = 4) -> None:
        """
        One atomic update, so the engine never sees a half-changed world.

        Harmony travels with the mood that chose it. Python decides *which*
        key and mode the mood calls for; the engine decides *when* to move
        there — it holds the change until a phrase boundary, because the
        rule that a key change lands on a phrase line is a timing rule, and
        timing lives in Sonic Pi.
        """
        self._send(self._cues, "/agentisizer/state",
                   [float(activity), float(valence), float(tension), float(blocker),
                    int(tonic_offset), int(mode_index)])

    def hit(self, kind: str) -> None:
        """A one-shot. The engine decides where in the bar it lands."""
        self._send(self._cues, "/agentisizer/hit", [str(kind)])

    # ── recording, for demos and for proving this works ──────────────────
    def start_recording(self) -> None:
        self._send(self._code, "/start-recording", [self.info.token])

    def stop_recording(self) -> None:
        # note: python-sonic's helper sends /start-recording here, which is a
        # copy-paste bug in that library. We send the real thing.
        self._send(self._code, "/stop-recording", [self.info.token])

    def save_recording(self, path: str) -> None:
        self._send(self._code, "/save-recording", [self.info.token, str(Path(path).resolve())])
