"""
Command line for The Agentisizer.

    agentisizer start          run the soundtrack until you stop it
    agentisizer say "..."      send one event to a running instance
    agentisizer demo           90-second tour of every musical state
    agentisizer doctor         what's working, what isn't
    agentisizer setup          get Sonic Pi installed and running
    agentisizer bench          measure the classifier against the keyword rules
    agentisizer test           run the test suite
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from . import process
from .conductor import Tuning
from .daemon import Agentisizer
from .events import Event
from .interpret import Interpreter
from .sonicpi import SonicPi, discover
from .sources.filedrop import DEFAULT_DIR, ensure_dir


# How this was invoked. The wrapper sets it; falls back to the installed
# entry-point name for anyone who did pip install .
CMD = os.environ.get("AGENTISIZER_CMD", "agentisizer")


try:
    from rich.console import Console
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
    holder = {}

    def show(event: Event):
        style = KIND_STYLE.get(event.kind or "progress", "")
        key = holder["app"].conductor.snapshot()["key"] if "app" in holder else ""
        # `·` decided by the keyword rules, `~` needed the model. Shows the
        # hybrid working, and how rarely the model is actually consulted.
        mark = "·" if event.meta.get("via") == "heuristic" else "~"
        out(f"{style}{(event.kind or '?'):<9}[/] {mark} {event.text[:58]} [cyan]{key}[/]")

    st = process.status(args.port)
    if st["running"]:
        if st["ours"] and st["state"]:
            out(f"[yellow]![/] already running [dim](pid {st['pid']})[/] — "
                f"{st['state']['key']}, activity {st['state']['activity']:.2f}")
            out(f"  [bold]{CMD} stop[/] to stop it, or [bold]{CMD} restart[/]")
        else:
            out(f"[red]✗[/] port {args.port} is in use by something that isn't us")
            out(f"  [dim]check: lsof -nP -iTCP:{args.port} -sTCP:LISTEN[/]")
            out(f"  [dim]or pick another: {CMD} --port 8913 start[/]")
        return 1

    try:
        app = Agentisizer(
            backend=args.backend,
            model=args.model,
            port=args.port,
            drop_dir=Path(args.drop_dir) if args.drop_dir else None,
            graph_url=args.graph,
            on_event=show,
        )
    except RuntimeError as e:
        out(f"[red]✗[/] {e}")
        out(f"  Run [bold]{CMD} setup[/] if you don't have Sonic Pi yet.")
        return 1

    holder["app"] = app
    out("[bold cyan]The Agentisizer[/] — listening")
    process.write_pidfile()   # so another window can stop this
    app.start()          # settles the backend before we claim which one it is
    for line in app.describe():
        out(f"  [dim]·[/] {line}")
    out(f"  [dim]·[/] interpreter: {app.interpreter.describe()}")
    out("  [dim]Ctrl-C to stop[/]\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        out("\n[dim]stopping…[/]")
        app.stop()
    finally:
        process.clear_pidfile()
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
        ensure_dir()
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
        out(f"[red]✗[/] Sonic Pi is not running. Try [bold]{CMD} setup[/].")
        return 1

    from .conductor import Conductor

    # A real blocker ramps over three minutes, which is right for daily use
    # and useless in a 90-second demo — you'd never hear the alarm. Compress
    # it so the escalation is audible, and say so rather than quietly
    # demoing something other than the default.
    demo_tuning = Tuning(blocker_ramp_seconds=22.0)

    conductor = Conductor(sonic, tuning=demo_tuning)
    interp = Interpreter(backend=args.backend, model=args.model)
    healthy, detail, secs = interp.health()
    # Walk the fallback chain so a dead API key doesn't hide a working local
    # model — report what will actually be used, not the first thing tried.
    while not healthy and interp.backend != "heuristic" and interp.demote():
        out(f"  [dim]{detail.splitlines()[0][:70]} — trying {interp.backend}[/]")
        healthy, detail, secs = interp.health()
    brain = f"{detail} {secs:.2f}s" if healthy else f"heuristic ({detail})"
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
                f"blk {snap['blocker']:.2f}  [cyan]{snap['key']}[/]")
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
        out(f"     [dim]open the app, or run: {CMD} setup[/]")

    out("\n[bold]Interpreter[/]")
    interp = Interpreter(backend=args.backend, model=args.model)
    healthy, detail, secs = interp.health()
    # Walk the fallback chain so a dead API key doesn't hide a working local
    # model — report what will actually be used, not the first thing tried.
    while not healthy and interp.backend != "heuristic" and interp.demote():
        out(f"  [dim]{detail.splitlines()[0][:70]} — trying {interp.backend}[/]")
        healthy, detail, secs = interp.health()
    if healthy and secs > interp.timeout:
        # Slower than we will wait for. These calls time out and the rules
        # answer instead, so the model is contributing nothing.
        out(f"  [red]✗[/] {detail} takes [bold]{secs:.1f}s[/] — over the "
            f"{interp.timeout:.0f}s timeout, so it never gets used")
        if not re.search(r"[:\-](0\.5|1|1\.5|2|3|3\.8|4)b", interp.model, re.I):
            out("     [dim]reasoning models think before answering, which this "
                "doesn't need\n     try: ollama pull llama3.2:3b[/]")
    elif healthy and secs > interp.SLOW_SECONDS:
        # Slower than ideal but still useful, because the rules go first and
        # the model is only asked about the minority they can't place.
        out(f"  [green]✓[/] {detail} [dim]({secs:.1f}s per call)[/]")
        out(f"     [dim]slower than the {interp.SLOW_SECONDS}s ideal, but the rules "
            f"answer first — most\n     events are instant and never reach it[/]")
    elif healthy:
        out(f"  [green]✓[/] {detail} [dim]({secs:.2f}s per call)[/]")
    elif interp.backend == "heuristic":
        out("  [yellow]![/] no model configured — using keyword rules")
        out("     [dim]set OPENROUTER_API_KEY, or run a local ollama[/]")
    else:
        # Configured but not working. Say so loudly: the fallback is silent
        # on purpose, so this is the only place it surfaces.
        out(f"  [yellow]![/] {interp.describe()} configured but [bold]not working[/]")
        out(f"     [dim]{detail}[/]")
        if "Timeout" in detail:
            # Almost always a reasoning model rather than a broken one: it
            # spends its budget thinking before answering a one-word question.
            out("     [dim]that usually means the model is too slow, not broken — "
                "reasoning models\n     think before answering, which this "
                "doesn't need[/]")
            out("     [dim]try a small one: ollama pull llama3.2:3b[/]")
        out("     [dim]falling back to keyword rules — the soundtrack still runs[/]")
    probe = interp.interpret("all tests passed after the fix")
    out(f"  [dim]probe → {probe.kind} ({probe.intensity:.2f}) via {probe.via}[/]")

    out("\n[bold]Inputs[/]")
    ensure_dir()          # so the documented echo-to-a-file works right now
    out(f"  [green]✓[/] file drop ready: {DEFAULT_DIR}")
    out(f"  [dim]·[/] http: 127.0.0.1:{args.port}")

    out("")
    out(f"[green]✓ ready[/]  try: [bold]{CMD} demo[/]" if ok
        else f"[yellow]! see above[/] — then: [bold]{CMD} doctor[/]")
    return 0 if ok else 1


def cmd_restart(args) -> int:
    ok, msg = process.stop(args.port)
    out(("[green]✓[/] " if ok else "[dim]·[/] ") + msg)
    time.sleep(1.0)          # let the port come back
    return cmd_start(args)


def cmd_audible(args) -> int:
    """Measure whether accents can be heard over the live mix."""
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
    from tools.check_audible import main as audible_main
    return audible_main(["--port", str(args.port)])


def cmd_stop(args) -> int:
    ok, msg = process.stop(args.port)
    out(("[green]✓[/] " if ok else "[yellow]![/] ") + msg)
    return 0


def cmd_status(args) -> int:
    st = process.status(args.port)
    if st["ours"] and st["state"]:
        s = st["state"]
        out(f"[green]● running[/] [dim](pid {st['pid']})[/]  {s['key']}")
        out(f"  activity {s['activity']:.2f}   valence {s['valence']:+.2f}   "
            f"tension {s['tension']:.2f}   blocker {s['blocker']:.2f}")
        out(f"  [dim]{s['events']} events"
            + (f" · blocked for {s['blocked_for']:.0f}s" if s.get("blocked_for") else "")
            + "[/]")
    elif st["running"]:
        out(f"[yellow]●[/] port {args.port} busy, but not answering as us")
    else:
        out("[dim]○ not running[/]")
    return 0


def cmd_menu(args) -> int:
    from .menu import main as menu_main
    return menu_main(args.port)


def cmd_test(args) -> int:
    """Run the suite. Documented in AGENTS.md, so it has to exist here."""
    import subprocess
    root = Path(__file__).resolve().parent.parent
    return subprocess.call(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root)


def cmd_bench(args) -> int:
    """Is the model actually earning its latency? Measure, don't assume."""
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
    from tools.bench_interpreter import main as bench_main
    argv = ["--backend", args.backend]
    if args.model:
        argv += ["--model", args.model]
    return bench_main(argv) if bench_main.__code__.co_argcount else bench_main()


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

    if not discover():
        out("[yellow]![/] Sonic Pi is installed but not running — opening it")
        subprocess.call(["open", "-a", "Sonic Pi"])
        out("  [dim]waiting for it to boot…[/]")
        for _ in range(40):
            time.sleep(1.5)
            if discover():
                break

    if not discover():
        out("[red]✗[/] Sonic Pi did not start — open it by hand, then re-run")
        return 1

    # The process existing is not the same as it being able to run code, and
    # announcing success into silence is the worst possible first impression.
    out("  [dim]checking it can actually run code…[/]")
    sonic = SonicPi.connect()
    if sonic is None or not sonic.ping(timeout=45):
        out("[red]✗[/] Sonic Pi is running but not accepting code yet")
        out("  [dim]give it a moment and re-run; if it persists, check[/]")
        out("  [dim]~/.sonic-pi/log/spider.log[/]")
        return 1
    out("[green]✓[/] Sonic Pi is running and responding")

    out(f"\n[green]✓ ready[/]  try: [bold]{CMD} demo[/]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=CMD, description="Hear what your agents are doing.")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "openrouter", "ollama", "heuristic"],
                   help="which brain classifies messages (default: auto)")
    p.add_argument("--model", default=None, help="model name for the chosen backend")
    p.add_argument("--port", type=int, default=8912, help="localhost HTTP port")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("start", help="run the soundtrack")
    s.add_argument("--drop-dir", default=None, help="directory to watch for event files")
    s.add_argument("--graph", default=None, metavar="URL",
                   help="poll a coordinator's agent-graph status endpoint "
                        "(e.g. http://localhost:4600/status)")

    s = sub.add_parser("say", help="send one event")
    s.add_argument("text", nargs="+")
    s.add_argument("--kind", choices=["progress", "good", "bad", "blocked", "resolved"])
    s.add_argument("--intensity", type=float, default=None)
    s.add_argument("--source", default="cli")

    s = sub.add_parser("demo", help="90-second tour of every musical state")
    s.add_argument("--record", metavar="FILE", help="save the demo to a WAV")

    sub.add_parser("doctor", help="check the setup")
    sub.add_parser("bench", help="measure the classifier against the keyword rules")
    sub.add_parser("test", help="run the test suite")
    sub.add_parser("stop", help="stop a running soundtrack, from anywhere")
    sub.add_parser("status", help="is it running, and what is it doing")
    sub.add_parser("menu", help="interactive menu")
    sub.add_parser("audible", help="can the accents be heard over the mix?")

    p_restart = sub.add_parser("restart", help="stop it, then start it again")
    p_restart.add_argument("--graph", default=None, metavar="URL")
    p_restart.add_argument("--drop-dir", default=None)
    s = sub.add_parser("setup", help="install and launch Sonic Pi")
    s.add_argument("--yes", action="store_true", help="don't ask before installing")

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        # A person typed the bare command. Show them what is happening and
        # what they can do about it, rather than a wall of flags.
        return cmd_menu(args)
    return {
        "start": cmd_start,
        "say": cmd_say,
        "demo": cmd_demo,
        "doctor": cmd_doctor,
        "bench": cmd_bench,
        "test": cmd_test,
        "stop": cmd_stop,
        "status": cmd_status,
        "menu": cmd_menu,
        "audible": cmd_audible,
        "restart": cmd_restart,
        "setup": cmd_setup,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
