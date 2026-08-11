"""
File-drop source: an agent writes a file, the music reacts.

The lowest-friction integration there is. Any agent, in any language, that
can write a file can play into this — no client library, no HTTP, no
protocol to learn:

    echo "tests are green" > ~/.agentisizer/events/$(date +%s).md

Markdown files may carry optional YAML-ish frontmatter, which lets an agent
skip the classifier when it already knows what it's reporting:

    ---
    kind: blocked
    intensity: 0.9
    source: deploy-bot
    ---
    Waiting on the staging database credentials.

Processed files move to `processed/` rather than being deleted, so a run can
be replayed or audited afterwards.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..events import Event


DEFAULT_DIR = Path.home() / ".agentisizer" / "events"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Split optional `---` frontmatter from the body.

    Intentionally not YAML: a tiny key: value parser has no dependency and
    no surprises, and anything more elaborate is a sign the payload should
    have been JSON.
    """
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    meta: dict = {}
    for line in text[3:end].strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip("'\"")
        key = key.strip()
        if key in ("intensity",):
            try:
                meta[key] = float(value)
            except ValueError:
                pass
        elif key == "tags":
            meta[key] = [t.strip() for t in value.split(",") if t.strip()]
        else:
            meta[key] = value

    return meta, text[end + 4:].lstrip("\n")


def event_from_file(path: Path) -> Event | None:
    """Turn one dropped file into an Event, or None if it's unusable."""
    try:
        raw = path.read_text(errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None

    if path.suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        data.setdefault("source", "filedrop")
        return Event.from_dict(data)

    meta, body = parse_frontmatter(raw)
    kind = meta.pop("kind", None)
    if kind not in (None, "progress", "good", "bad", "blocked", "resolved"):
        kind = None                       # let the interpreter decide instead

    return Event(
        text=body.strip() or path.stem,
        source=meta.pop("source", "filedrop"),
        kind=kind,
        intensity=float(meta.pop("intensity", 0.5)),
        tags=meta.pop("tags", []),
        meta=meta,
    )


class FileDropSource:
    """Polls a directory. Polling, not inotify — portable and sufficient."""

    def __init__(self, emit, directory: Path | None = None, interval: float = 0.4):
        self.emit = emit
        self.dir = Path(directory or DEFAULT_DIR)
        self.processed = self.dir / "processed"
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _scan(self) -> None:
        for path in sorted(self.dir.glob("*")):
            if path.is_dir() or path.name.startswith("."):
                continue
            if path.suffix not in (".md", ".json", ".txt"):
                continue
            event = event_from_file(path)
            if event:
                self.emit(event)
            try:
                path.rename(self.processed / f"{int(time.time()*1000)}_{path.name}")
            except OSError:
                try:
                    path.unlink()
                except OSError:
                    pass

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)

        def loop():
            while not self._stop.is_set():
                try:
                    self._scan()
                except Exception:
                    pass
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, name="filedrop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def describe(self) -> str:
        return f"file drop at {self.dir}"
