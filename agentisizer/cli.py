"""
Command line for The Agentisizer.

    agentisizer start          run the soundtrack until you stop it
    agentisizer say "..."      send one event to a running instance
    agentisizer demo           90-second tour of every musical state
    agentisizer doctor         what's working, what isn't
    agentisizer setup          get Sonic Pi installed and running
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from .conductor import Tuning
from .daemon import Agentisizer
from .events import Event
from .interpret import Interpreter, heuristic
from .sonicpi import SonicPi, discover
from .sources.filedrop import DEFAULT_DIR


try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
except ImportError:                       # rich is nice, not required
    console = None


def out(msg: str = "") -> None:
    if console:
        console.print(msg)
    else:
        print(msg.replace("[/]", "").replace("[bold]", "").replace("[dim]", ""))


KIND_STYLE = {
    "good": "[green]",
    "bad": "[red]",
    "blocked": "[bold red]",
    "resolved": "[cyan]",
    "progress": "[dim]",
}


# ── start ────────────────────────────────────────────────────────────────
def cmd_start(args) -> int:
    def show(event: Event):
        style = KIND_STYLE.get(event.kind or "progress", "")
        via = event.meta.get("via", "")
        out(f"{style}{(event.kind or '?'):<9}[/] {event.text[:70]} [dim]{via}[/]")

    try:
        app = Agentisizer(
            backend=args.backend,
            model=args.model,
            port=args.port,
            drop_dir=Path(args.drop_dir) if args.drop_dir else None,
            on_event=show,
        )
    except RuntimeError as e:
        out(f"[red]✗[/] {e}")
        out("  Run [bold]agentisizer setup[/] if you don't have Sonic Pi yet.")
        return 1

    out("[bold cyan]The Agentisizer[/] — listening")
    for line in app.describe():
        out(f"  [dim]·[/] {line}")
    out(f"  [dim]·[/] interpreter: {app.interpreter.describe()}")
    out("  [dim]Ctrl-C to stop[/]\n")

    app.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        out("\n[dim]stopping…[/]")
        app.stop()
    return 0


# ── say ──────────────────────────────────────────────────────────────────
def cmd_say(args) -> int:
    payload = {"text": " ".join(args.text), "source": args.source}
    if args.kind:
        payload["kind"] = args.kind
    if args.intensity is not None:
        payload["intensity"] = args.intensity

    req = urllib.request.Request(
        f"http://127.0.0.1:{args.port}/event",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            json.loads(r.read().decode())
        out(f"[green]✓[/] sent")
        return 0
    except Exception:
        # No daemon listening — fall back to the file drop, which a later
        # `start` will pick up. Losing an event to a typo'd port is worse
        # than delivering it late.
        DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
        path = DEFAULT_DIR / f"{int(time.time()*1000)}.json"
        path.write_text(json.dumps(payload))
        out(f"[yellow]![/] nothing listening on :{args.port} — dropped to {path.name}")
        return 0


# ── demo ─────────────────────────────────────────────────────────────────
DEMO = [
    (0,  "idle",           None,                                            None),
    (6,  "work starts",    "reading main.py and mapping the call graph",     "progress"),
    (4,  "more work",      "running the test suite",                         "progress"),
    (4,  "busy",           "refactoring the parser across nine files",       "progress"),
    (6,  "good news",      "all 240 tests passed",                           "good"),
    (6,  "a problem",      "TypeError in parse_headers, three tests failing", "bad"),
    (8,  "worse",          "the fix broke authentication as well",           "bad"),
    (8,  "BLOCKED",        "need the staging database password to continue",  "blocked"),
    (14, "alarm rising",   None,                                             None),
    (8,  "resolved",       "credentials received, deploy is green",          "resolved"),
    (8,  "calm returns",   None,                                             None),
]


def cmd_demo(args) -> int:
    sonic = SonicPi.connect()
    if sonic is None:
        out("[red]✗[/] Sonic Pi is not running. Try [bold]agentisizer setup[/].")
        return 1

    from .conductor import Conductor

    # A real blocker ramps over three minutes, which is right for daily use
    # and useless in a 90-second demo — you'd never hear the alarm. Compress
    # it so the escalation is audible, and say so rather than quietly
    # demoing something other than the default.
    demo_tuning = Tuning(blocker_ramp_seconds=22.0)

    conductor = Conductor(sonic, tuning=demo_tuning)
    interp = Interpreter(backend=args.backend, model=args.model)
    healthy, detail = interp.health()
    brain = detail if healthy else f"heuristic ({detail})"
    out(f"[bold cyan]The Agentisizer[/] — demo  [dim]({brain})[/]")
    out("[dim]blocker ramp compressed to 22s so the alarm is audible; "
        "the default is 180s[/]\n")

    sonic.load_engine()
    time.sleep(2)
    conductor.start()

    if args.record:
        sonic.stop_recording()
        sonic.start_recording()

    try:
        for secs, label, text, kind in DEMO:
            snap = conductor.snapshot()
            out(f"[dim]{label:<14}[/] act {snap['activity']:.2f}  "
                f"val {snap['valence']:+.2f}  ten {snap['tension']:.2f}  "
                f"blk {snap['blocker']:.2f}")
            if text:
                ev = Event(text=text, source="demo", kind=kind)
                if kind is None:
                    ev.kind = interp.interpret(text).kind
                conductor.submit(ev)
            time.sleep(secs)
    except KeyboardInterrupt:
        pass

    conductor.stop()
    if args.record:
        time.sleep(1)
        sonic.stop_recording()
        sonic.save_recording(args.record)
        out(f"\n[green]✓[/] recorded to {args.record}")
    sonic.stop()
    out("\n[dim]done[/]")
    return 0


# ── doctor ───────────────────────────────────────────────────────────────
def cmd_doctor(args) -> int:
    ok = True
    out("[bold]Sonic Pi[/]")
    info = discover()
    if info:
        out(f"  [green]✓[/] running — port {info.server_port}, cues {info.osc_cues_port} "
            f"[dim](via {info.source})[/]")
    else:
        ok = False
        out("  [red]✗[/] not running")
        out("     [dim]open the app, or run: agentisizer setup[/]")

    out("\n[bold]Interpreter[/]")
    interp = Interpreter(backend=args.backend, model=args.model)
    healthy, detail = interp.health()
    if healthy:
        out(f"  [green]✓[/] {detail}")
    elif interp.backend == "heuristic":
        out("  [yellow]![/] no model configured — using keyword rules")
        out("     [dim]set OPENROUTER_API_KEY, or run a local ollama[/]")
    else:
        # Configured but not working. Say so loudly: the fallback is silent
        # on purpose, so this is the only place it surfaces.
        out(f"  [yellow]![/] {interp.describe()} configured but [bold]not working[/]")
        out(f"     [dim]{detail}[/]")
        out("     [dim]falling back to keyword rules — the soundtrack still runs[/]")
    probe = interp.interpret("all tests passed after the fix")
    out(f"  [dim]probe → {probe.kind} ({probe.intensity:.2f}) via {probe.via}[/]")

    out("\n[bold]Inputs[/]")
    out(f"  [dim]·[/] file drop: {DEFAULT_DIR}")
    out(f"  [dim]·[/] http: 127.0.0.1:{args.port}")

    out("")
    out("[green]✓ ready[/]  try: [bold]agentisizer demo[/]" if ok
        else "[yellow]! see above[/]")
    return 0 if ok else 1


# ── setup ────────────────────────────────────────────────────────────────
def cmd_setup(args) -> int:
    import shutil
    import subprocess

    app = Path("/Applications/Sonic Pi.app")
    out("[bold cyan]Getting Sonic Pi ready[/]\n")

    if app.exists():
        out("[green]✓[/] Sonic Pi is installed")
    else:
        out("[yellow]![/] Sonic Pi is not installed")
        if shutil.which("brew"):
            out("  Install it with:\n    [bold]brew install --cask sonic-pi[/]")
            if args.yes or input("\n  Run that now? [y/N] ").strip().lower() == "y":
                subprocess.call(["brew", "install", "--cask", "sonic-pi"])
        else:
            out("  Download it from [bold]https://sonic-pi.net/[/]")
        if not app.exists():
            return 1

    if discover():
        out("[green]✓[/] Sonic Pi is running")
    else:
        out("[yellow]![/] Sonic Pi is installed but not running — opening it")
        subprocess.call(["open", "-a", "Sonic Pi"])
        out("  [dim]waiting for it to boot…[/]")
        for _ in range(40):
            time.sleep(1.5)
            if discover():
                break
        if discover():
            out("[green]✓[/] booted")
        else:
            out("[red]✗[/] still not reachable — open it by hand and re-run doctor")
            return 1

    out("\n[green]✓ ready[/]  try: [bold]agentisizer demo[/]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentisizer", description="Hear what your agents are doing.")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "openrouter", "ollama", "heuristic"],
                   help="which brain classifies messages (default: auto)")
    p.add_argument("--model", default=None, help="model name for the chosen backend")
    p.add_argument("--port", type=int, default=8912, help="localhost HTTP port")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("start", help="run the soundtrack")
    s.add_argument("--drop-dir", default=None, help="directory to watch for event files")

    s = sub.add_parser("say", help="send one event")
    s.add_argument("text", nargs="+")
    s.add_argument("--kind", choices=["progress", "good", "bad", "blocked", "resolved"])
    s.add_argument("--intensity", type=float, default=None)
    s.add_argument("--source", default="cli")

    s = sub.add_parser("demo", help="90-second tour of every musical state")
    s.add_argument("--record", metavar="FILE", help="save the demo to a WAV")

    sub.add_parser("doctor", help="check the setup")
    s = sub.add_parser("setup", help="install and launch Sonic Pi")
    s.add_argument("--yes", action="store_true", help="don't ask before installing")

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    return {
        "start": cmd_start,
        "say": cmd_say,
        "demo": cmd_demo,
        "doctor": cmd_doctor,
        "setup": cmd_setup,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
