"""
The menu you get when you run the wrapper with no arguments.

Written after a real dead end: the daemon was running in another session, the
only documented way to stop it was Ctrl-C in a terminal that was not available,
and trying to start a second one produced a socket traceback. Flags are fine
when you already know them; a menu is what you want when the thing is running
and you are not sure what it is doing.

Subcommands still work exactly as before — scripts and coordinators call
`start --graph …` directly, and this must never get in their way. It is only
what happens when a person types the bare command.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from . import process
from .sonicpi import discover


ROOT = Path(__file__).resolve().parent.parent
console = Console()


def run(args: list[str]) -> int:
    """Run a subcommand in this interpreter, so the venv is already right."""
    console.print(f"[dim]$ ./run-agentisizer.sh {' '.join(args)}[/]\n")
    return subprocess.call([sys.executable, "-m", "agentisizer.cli"] + args, cwd=ROOT)


def header(port: int) -> None:
    st = process.status(port)
    sonic = discover()

    if st["ours"] and st["state"]:
        s = st["state"]
        line = (f"[green]● playing[/]  {s['key']}   "
                f"activity {s['activity']:.2f}  valence {s['valence']:+.2f}  "
                f"tension {s['tension']:.2f}  blocker {s['blocker']:.2f}\n"
                f"[dim]{s['events']} events")
        if s.get("blocked_for"):
            line += f" · blocked for {s['blocked_for']:.0f}s"
        line += f" · pid {st['pid']}[/]"
    elif st["running"]:
        line = (f"[yellow]● port {port} is busy but not answering[/]\n"
                f"[dim]something else may be using it[/]")
    else:
        line = "[dim]○ not running[/]"

    line += ("\n[dim]Sonic Pi: " +
             (f"ready on {sonic.server_port}" if sonic else "not running") + "[/]")
    console.print(Panel(line, title="[bold cyan]The Agentisizer[/]", border_style="cyan"))
    return st


def menu(running: bool) -> None:
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column("key", style="bold cyan", width=3)
    t.add_column("what")
    if running:
        t.add_row("1", "Stop the soundtrack")
        t.add_row("2", "Restart it")
    else:
        t.add_row("1", "Start the soundtrack")
        t.add_row("2", "Start it following an agent graph")
    t.add_row("3", "Say something to it")
    t.add_row("4", "Demo — 90 seconds through every state")
    t.add_row("5", "Doctor — what's working")
    t.add_row("6", "Bench — score the classifier")
    t.add_row("7", "Tests")
    t.add_row("r", "Refresh")
    t.add_row("q", "Quit" + (" [dim](leaves it running)[/]" if running else ""))
    console.print(t)


DEFAULT_GRAPH = "http://localhost:4600/status"


def main(port: int = 8912) -> int:
    while True:
        console.clear()
        st = header(port)
        running = bool(st["running"])
        menu(running)

        try:
            choice = Prompt.ask(
                "\n[bold]Choose[/]",
                choices=["1", "2", "3", "4", "5", "6", "7", "r", "q"],
                default="r", show_choices=False)
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/]")
            return 0

        if choice == "q":
            console.print("[dim]bye[/]")
            return 0
        if choice == "r":
            continue

        if choice == "1":
            if running:
                ok, msg = process.stop(port)
                console.print(("[green]✓[/] " if ok else "[yellow]![/] ") + msg)
            else:
                console.print("[dim]Ctrl-C in this window stops it.[/]\n")
                run(["start"])
        elif choice == "2":
            if running:
                ok, msg = process.stop(port)
                console.print(("[green]✓[/] " if ok else "[yellow]![/] ") + msg)
                url = Prompt.ask("Graph URL [dim](blank for none)[/]", default=DEFAULT_GRAPH)
                console.print("[dim]Ctrl-C in this window stops it.[/]\n")
                run(["start"] + (["--graph", url] if url.strip() else []))
            else:
                url = Prompt.ask("Graph URL", default=DEFAULT_GRAPH)
                console.print("[dim]Ctrl-C in this window stops it.[/]\n")
                run(["start", "--graph", url])
        elif choice == "3":
            text = Prompt.ask("What happened")
            if text.strip():
                run(["say", text])
        elif choice == "4":
            run(["demo"])
        elif choice == "5":
            run(["doctor"])
        elif choice == "6":
            run(["bench"])
        elif choice == "7":
            run(["test"])

        Prompt.ask("\n[dim]enter to continue[/]", default="", show_default=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
