#!/usr/bin/env python3
"""
Benchmark a classifier backend against the keyword rules.

The heuristic is the floor: if a model can't beat it, it is costing latency
and adding nothing, and the honest thing is to keep the rules. This exists so
that is a measurement rather than an opinion.

    ./run-agentisizer.sh bench                # whatever backend is configured
    python tools/bench_interpreter.py --backend ollama --model llama3.2:3b
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentisizer.interpret import Interpreter, heuristic


# Real agent phrasing. The second group is the interesting one: no keyword to
# latch onto, so the rules fall through to `progress` and a model has to
# actually read the sentence to win.
CASES: list[tuple[str, str]] = [
    # keyword-bearing — the rules should get these
    ("reading main.py to understand the call graph",              "progress"),
    ("running the test suite now",                                "progress"),
    ("all 240 tests passed after the refactor",                   "good"),
    ("deployed to production successfully",                       "good"),
    ("TypeError in parse_headers, three tests failing",           "bad"),
    ("the build broke on the linter step",                        "bad"),
    ("I need the staging database password to continue",          "blocked"),
    ("permission denied, cannot continue",                        "blocked"),
    ("credentials received, deploy is green again",               "resolved"),
    ("the service is back online",                                "resolved"),

    # no keyword — the rules fall through, a model has to understand
    ("the retry finally stopped throwing",                        "resolved"),
    ("I'm going to have to ask you which schema is canonical",    "blocked"),
    ("compiling, this takes a while",                             "progress"),
    ("that last change made everything worse",                    "bad"),
    ("nothing left on the list",                                  "good"),
    ("still churning through the migration",                      "progress"),
    ("I can't tell which of these two configs you meant",         "blocked"),
    ("turns out the whole approach was wrong",                    "bad"),
    ("that did it",                                               "good"),
    ("looking at how the scheduler decides priority",             "progress"),
]


def run(classify, label: str) -> tuple[int, float, list[str]]:
    hits, total, misses = 0, 0.0, []
    for text, expect in CASES:
        start = time.time()
        got = classify(text)
        total += time.time() - start
        if got == expect:
            hits += 1
        else:
            misses.append(f"    {got:<9} ≠ {expect:<9} | {text}")
    return hits, total / len(CASES), misses


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backend", default="auto")
    p.add_argument("--model", default=None)
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--quiet", action="store_true", help="hide the misses")
    args = p.parse_args(argv)

    n = len(CASES)
    print(f"{n} cases — 10 with keywords, 10 without\n")

    hits, mean, misses = run(lambda t: heuristic(t).kind, "heuristic")
    print(f"heuristic       {hits:>2}/{n}   {mean*1000:6.2f}ms/event")
    if misses and not args.quiet:
        print("\n".join(misses))

    interp = Interpreter(backend=args.backend, model=args.model, timeout=args.timeout)
    if interp.backend == "heuristic":
        print("\nno model configured — nothing to compare against")
        return 0

    ok, detail, secs = interp.health()
    if not ok:
        print(f"\n{interp.describe()}: not working — {detail}")
        return 1

    def model_only(t):
        got = interp.classify_with_model(t)
        return got.kind if got else "progress"

    hits_m, mean_m, misses_m = run(model_only, "model")
    print(f"\n{'model only':<15} {hits_m:>2}/{n}   {mean_m:6.2f}s/event   [{interp.describe()}]")
    if misses_m and not args.quiet:
        print("\n".join(misses_m))

    interp._cache.clear()
    hits_h, mean_h, misses_h = run(lambda t: interp.interpret(t).kind, "hybrid")
    asked = sum(1 for text, _ in CASES if not heuristic(text).confident)
    print(f"\n{'hybrid':<15} {hits_h:>2}/{n}   {mean_h:6.2f}s/event   "
          f"[rules first, model on {asked}/{n}]")
    if misses_h and not args.quiet:
        print("\n".join(misses_h))

    print()
    best = max(hits, hits_m, hits_h)
    if hits_h == best and hits_h > max(hits, hits_m):
        print(f"→ hybrid wins: {hits_h}/{n} vs rules {hits}, model {hits_m}.")
        print(f"  It only pays for the model on the {asked} cases the rules shrug at.")
    elif hits_h >= hits and hits_h >= hits_m:
        print(f"→ hybrid ties the best ({hits_h}/{n}) and is cheaper than the model.")
    else:
        print(f"→ hybrid {hits_h}, rules {hits}, model {hits_m} — reconsider the ordering.")
    if mean_m > interp.SLOW_SECONDS:
        print(f"  Model alone is over the {interp.SLOW_SECONDS}s budget for live audio;")
        print(f"  the hybrid averages {mean_h:.2f}s because most events never reach it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
