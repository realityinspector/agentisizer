#!/usr/bin/env python3
"""
Check that the docs describe the code that exists.

README.md and AGENTS.md both tell people to run specific commands and open
specific files. Both drifted within days of being written — `test` was
documented before it existed, hints named an `agentisizer` binary that is
only on PATH after a pip install, and the file-drop example used a directory
nothing created. Every one of those was a dead end in the first five minutes.

So this runs with the tests. Prose is not checked and cannot be; commands and
paths are, and those are what a new reader types first.

    ./run-agentisizer.sh test        (included)
    python tools/check_docs.py       (alone)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "AGENTS.md")

# Names used illustratively rather than as real paths.
PLACEHOLDERS = {"agentisizer/sources/yours.py"}


def real_subcommands() -> set[str]:
    """Ask the parser itself, so this cannot drift from the CLI."""
    sys.path.insert(0, str(ROOT))
    from agentisizer.cli import build_parser

    for action in build_parser()._actions:
        if action.choices and "start" in action.choices:
            return set(action.choices)
    raise SystemExit("could not find the subcommand list on the parser")


def check() -> list[str]:
    valid = real_subcommands()
    problems: list[str] = []

    for name in DOCS:
        doc = ROOT / name
        if not doc.exists():
            problems.append(f"{name}: missing entirely")
            continue
        text = doc.read_text()

        def line_of(pos: int) -> int:
            return text[:pos].count("\n") + 1

        # 1. every wrapper invocation names a subcommand that exists
        for m in re.finditer(r"run-agentisizer\.sh\s+([a-z][a-z-]*)", text):
            cmd = m.group(1)
            if cmd not in valid and cmd != "help":
                problems.append(f"{name}:{line_of(m.start())}: "
                                f"'{cmd}' is not a subcommand {sorted(valid)}")

        # 2. no bare `agentisizer <cmd>` — there is no such binary unless the
        #    reader pip-installed the package, which nothing tells them to do
        for m in re.finditer(r"(?<!run-)(?<![\w./-])agentisizer\s+("
                             + "|".join(sorted(valid)) + r")\b", text):
            problems.append(f"{name}:{line_of(m.start())}: bare "
                            f"'agentisizer {m.group(1)}' is not on PATH")

        # 3. repo paths in backticks exist
        for m in re.finditer(r"`((?:agentisizer|engine|docs|tests|tools)/[\w./]+)`", text):
            path = m.group(1)
            if path not in PLACEHOLDERS and not (ROOT / path).exists():
                problems.append(f"{name}:{line_of(m.start())}: no such file {path}")

        # 4. embedded images and links resolve
        for m in re.finditer(r'(?:src="|\]\()([\w./-]+\.(?:svg|png|mp3|md|wav))', text):
            target = m.group(1)
            if target.startswith("http"):
                continue
            if not (ROOT / target).exists():
                problems.append(f"{name}:{line_of(m.start())}: broken link {target}")

    return problems


def main() -> int:
    problems = check()
    if problems:
        print(f"{len(problems)} documentation problem(s):")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print(f"✓ every command and path in {', '.join(DOCS)} resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
