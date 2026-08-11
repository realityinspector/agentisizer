"""
The daemon: sources in, one interpreter, one conductor, one engine.

The whole graph, and the reason a new input module is a small file:

    sources ──► interpret ──► conductor ──► Sonic Pi engine
    (many)      (one)         (one)          (one, persistent)

Interpretation runs on a worker thread. A slow model must never stall the
source that produced the event, and it must never stall the music — the
conductor is on its own clock and keeps playing regardless.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from .conductor import Conductor, Tuning
from .events import Event
from .interpret import Interpreter
from .sonicpi import SonicPi
from .sources.filedrop import FileDropSource
from .sources.http_api import HttpSource


class Agentisizer:
    def __init__(
        self,
        backend: str = "auto",
        model: str | None = None,
        port: int = 8912,
        drop_dir: Path | None = None,
        tuning: Tuning | None = None,
        on_event=None,
    ):
        self.sonic = SonicPi.connect()
        if self.sonic is None:
            raise RuntimeError("Sonic Pi is not running")

        self.interpreter = Interpreter(backend=backend, model=model)
        self.conductor = Conductor(self.sonic, tuning=tuning)
        self.on_event = on_event

        self._q: queue.Queue[Event] = queue.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

        self.sources = [
            FileDropSource(self.ingest, directory=drop_dir),
            HttpSource(self.ingest, port=port, snapshot=self.conductor.snapshot),
        ]

    def ingest(self, event: Event) -> None:
        """Called by sources, from their own threads. Never blocks them."""
        try:
            self._q.put_nowait(event)
        except queue.Full:
            pass          # a flood is already represented by `activity`

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if event.kind is None:
                    intent = self.interpreter.interpret(event.text)
                    event.kind = intent.kind
                    # Trust an explicit intensity from the source; otherwise
                    # take the interpreter's read.
                    if event.intensity == 0.5:
                        event.intensity = intent.intensity
                    event.meta["summary"] = intent.summary
                    event.meta["via"] = intent.via
                self.conductor.submit(event)
                if self.on_event:
                    self.on_event(event)
            except Exception:
                pass      # one bad event must not stop the stream

    def start(self) -> None:
        self.sonic.load_engine()
        time.sleep(1.5)               # let the live_loops come up
        self.conductor.start()
        for s in self.sources:
            s.start()
        self._worker = threading.Thread(target=self._work, name="interpret", daemon=True)
        self._worker.start()

    def stop(self, silence: bool = True) -> None:
        self._stop.set()
        for s in self.sources:
            s.stop()
        self.conductor.stop()
        if self._worker:
            self._worker.join(timeout=2)
        if silence:
            self.sonic.stop()

    def describe(self) -> list[str]:
        return [s.describe() for s in self.sources]
