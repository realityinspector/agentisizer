#!/usr/bin/env python3
"""
Is an accent actually audible over the mix it plays into?

Written after guessing wrong. The `bad` accent was inaudible, revoiced on
reasoning alone — octave up, plucked, "a transient carries over a sustained
bed" — and the reasoning was wrong: measured against a live mix it was still
buried. An argument about masking is not evidence about masking.

Method: record the running soundtrack, fire the accent into it, and look for
an onset in the band the accent occupies. The positive control is the point —
`good` is known to cut through, so if the harness cannot find *that* the
harness is broken, not the accent. Without a control, "not detected" and "my
detector is wrong" look identical.

── read this before trusting a verdict ──────────────────────────────────
This does not yet answer the question reliably. Three metrics were tried on
the same recordings and gave three different answers: max-vs-max said buried,
percentile-against-hops said audible at 99%, and percentile against a fair
null of equal-length windows put the *control* at 82% — below its own
threshold. The control failing is the harness saying so, and that mechanism
is the only part demonstrably working.

What it is good for today is a repeatable, honest negative: when the control
passes and the accent does not, that is real. When the control fails, the run
proves nothing and it says so rather than reporting a number.

Two things would make it trustworthy. Measuring against a *silent* baseline
rather than a live mix isolates the accent, at the cost of answering an easier
question than the one that matters. And a listener's ear remains the ground
truth — the entire project is a claim about what a person notices, which is
not obviously reducible to onset energy in a band.

Needs the daemon running, because the interesting question is audibility over
a real mix at real density. Accents are hardest to hear exactly when the fleet
is busiest, which is when they matter most.

    ./run-agentisizer.sh audible          both, with the control
    python tools/check_audible.py --kind bad
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentisizer.sonicpi import SonicPi

# Where each accent lives, so we listen where it actually is.
BANDS = {
    "good":     (600, 1800),    # pretty_bell, up top
    "resolved": (400, 1400),    # blade
    "bad":      (170, 420),     # the flat-second stab
    "blocked":  (100, 400),     # subpulse thud
}


def fire(kind: str, port: int) -> None:
    urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{port}/event",
        data=json.dumps({"text": f"audibility probe ({kind})", "kind": kind,
                         "intensity": 0.9, "source": "audible-check"}).encode(),
        headers={"Content-Type": "application/json"}), timeout=3)


def band_energy(seg, sr, lo, hi) -> float:
    """Goertzel across a band. No numpy, and exact enough for onsets."""
    total, f = 0.0, lo
    while f < hi:
        k = 2 * math.cos(2 * math.pi * f / sr)
        s1 = s2 = 0.0
        for v in seg:
            s0 = v + k * s1 - s2
            s2, s1 = s1, s0
        total += max(s1 * s1 + s2 * s2 - k * s1 * s2, 0)
        f *= 1.05
    return math.sqrt(total) / max(len(seg), 1)


def onsets(samples, sr, lo, hi, hop_s=0.05):
    hop = int(sr * hop_s)
    vals = [(i / sr, band_energy(samples[i:i + hop], sr, lo, hi))
            for i in range(0, len(samples) - hop, hop)]
    return [(t, max(0.0, v - vals[i - 1][1])) for i, (t, v) in enumerate(vals) if i]


def measure(kinds: list[str], port: int, lead: float = 5.0, gap: float = 7.0,
            reps: int = 3):
    """
    Fire each accent several times and take the median.

    One firing is not evidence. The mix is stochastic — the arp picks its own
    notes, the pulse pattern varies with activity — so a single onset can land
    on a busy moment or a gap. A first run of this detected the control
    clearly; a second, minutes later, did not. Repeating and taking the middle
    reading is the difference between a measurement and an anecdote.
    """
    sonic = SonicPi.connect()
    if sonic is None:
        print("Sonic Pi is not running"); return 1

    tmp = Path(tempfile.gettempdir()) / "agentisizer_audible.wav"
    sonic.stop_recording(); time.sleep(0.5)
    sonic.start_recording()
    start = time.time()
    at: dict[str, list[float]] = {k: [] for k in kinds}
    first = True
    for _ in range(reps):
        for kind in kinds:
            time.sleep(lead if first else gap)
            first = False
            fire(kind, port)
            at[kind].append(time.time() - start)
    print(f"  fired {reps}× each: " +
          ", ".join(f"{k} at " + "/".join(f"{t:.0f}s" for t in ts) for k, ts in at.items()))
    time.sleep(gap)
    sonic.stop_recording(); time.sleep(0.5)
    sonic.save_recording(str(tmp)); time.sleep(1.5)

    mono = tmp.with_name("agentisizer_audible_mono.wav")
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(tmp),
                    "-ac", "1", "-ar", "11025", str(mono)], check=True)
    w = wave.open(str(mono))
    sr, n = w.getframerate(), w.getnframes()
    x = list(struct.unpack(f"<{n}h", w.readframes(n)))

    print()
    results = {}
    for kind in kinds:
        lo, hi = BANDS.get(kind, (150, 2000))
        o = onsets(x, sr, lo, hi)
        peak = max(v for _, v in o) or 1.0
        windows = [(t0, t0 + 1.6) for t0 in at[kind]]
        inside = sorted(max((v for t, v in o if a <= t <= b), default=0.0)
                        for a, b in windows)
        med = inside[len(inside) // 2]
        # The null has to be built the same way as the measurement. Ranking a
        # window's *maximum* against individual 50ms hops is not a fair
        # comparison — any window maximum beats most single hops, so it would
        # call almost anything audible. So slide equivalent-length windows
        # across the rest of the recording and rank against *their* maxima.
        #
        # Comparing against the single loudest moment elsewhere is also wrong,
        # in the other direction: it punishes long recordings, since more
        # material means more chance of one big unrelated onset. That is a
        # property of the ruler, not the sound.
        span = 1.6
        elsewhere = []
        t = 0.0
        end = o[-1][0] if o else 0.0
        while t + span <= end:
            if not any(a - span < t < b for a, b in windows):
                vals = [v for tt, v in o if t <= tt <= t + span]
                if vals:
                    elsewhere.append(max(vals))
            t += span / 2
        rank = sum(1 for v in elsewhere if v < med) / max(len(elsewhere), 1)
        ok = rank >= 0.90        # louder than 90% of the ambient mix
        results[kind] = ok
        print(f"  {kind:<9} {lo}-{hi}Hz   louder than {rank*100:>4.0f}% of the mix"
              f"   {'✓ audible' if ok else '✗ buried'}")

    for f in (tmp, mono):
        f.unlink(missing_ok=True)

    if "good" in results and not results["good"]:
        print("\n  The control was not detected either — trust the harness, not the verdict.")
        return 1
    return 0 if all(results.values()) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--kind", action="append", choices=sorted(BANDS),
                   help="accent to test (repeatable); default: good then bad")
    p.add_argument("--port", type=int, default=8912)
    args = p.parse_args(argv)
    # `good` first by default: it is the control, and a run without one proves
    # nothing when the answer comes back negative.
    kinds = args.kind or ["good", "bad"]
    if "good" not in kinds:
        kinds = ["good"] + kinds
    return measure(kinds, args.port)


if __name__ == "__main__":
    sys.exit(main())
