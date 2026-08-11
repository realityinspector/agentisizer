"""
Key and mode: deciding *what harmony* the mood should live in.

The first version had one key and one progression, forever. That is fine for
ninety seconds and wrong for six hours — and it wasted the most expressive
axis music has. Mood shouldn't only change how loud or busy things are. It
should change what the harmony *is*.

── the brightness ladder ────────────────────────────────────────────────
The modes of the major scale form a natural ordering. Take each one's notes
against its own root and count how many are sharpened: Lydian has the most,
Locrian the fewest. Walk down that list and each step flattens exactly one
degree, so the sound darkens by the smallest move available:

    lydian      #4          brightest, floating
    ionian                  plain major
    mixolydian  b7          major with an edge
    dorian      b3 b7       minor, hopeful
    aeolian     b3 b6 b7    natural minor — home
    phrygian    b2 b3 b6 b7 darkest usable
    (locrian)   b5 too      omitted: no perfect fifth, sounds broken

So mood maps onto a single axis that is already musical. Good news brightens
the *mode*, bad news darkens it, and neighbouring steps differ by one note —
the shift is felt without being announced.

The payoff is at the dark end. The engine voices tension as a flat second
against the root. In Aeolian that is a chromatic outsider. In Phrygian the
flat second is *diatonic* — so as things get worse, the key moves to meet the
dissonance, and the sound that was fighting the harmony becomes the harmony.
Tension resolves into character instead of just piling up.

Modulation of the tonic is separate and much rarer: only between closely
related keys, only when things are calm. Changing key during a crisis reads
as the ground moving, which is not the feeling we want.
"""

from __future__ import annotations

from dataclasses import dataclass


# Brightest first. Index is the only thing the rest of the system passes
# around; the engine holds the matching list.
MODES = ("lydian", "ionian", "mixolydian", "dorian", "aeolian", "phrygian")

# Aeolian is the reference point the ladder is measured from, but neutral
# mood deliberately rests a step brighter, in Dorian: over hours, natural
# minor reads as mournful where Dorian just reads as calm. Aeolian is where
# things go when they start going wrong.
HOME = 4          # aeolian — the reference, not the resting place
BRIGHTEST = 0
DARKEST = len(MODES) - 1

# Tonic offsets in semitones from the home key, as a slow cycle. Every one of
# these shares most of its notes with A minor, so the move lands as colour
# rather than as a jolt:
#   0  = A minor (home)      +5 = D minor (subdominant)
#   +3 = C major (relative)  +7 = E minor (dominant)
TONIC_CYCLE = (0, 5, 3, 7)


@dataclass(frozen=True)
class Harmony:
    tonic_offset: int      # semitones from the home key
    mode_index: int        # index into MODES

    @property
    def mode(self) -> str:
        return MODES[self.mode_index]

    def name(self, home_note: str = "A") -> str:
        names = ("A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#")
        root = names[self.tonic_offset % 12] if home_note == "A" else home_note
        return f"{root} {self.mode}"


def mode_for(valence: float, tension: float) -> int:
    """
    Pick a mode from mood. Bounded, monotonic, and centred on home.

    Tension is weighted harder than valence: things going wrong should pull
    toward darkness more readily than things going right pull toward light.
    A soundtrack that turns radiant the moment one test passes is a
    soundtrack nobody believes.
    """
    mood = (valence * 0.55) - (tension * 0.95)

    # Thresholds, brightest first. Deliberately asymmetric: two steps up
    # from home are available, but the top of the ladder is not — Lydian is
    # too weightless to sit under working software.
    if mood >= 0.55:
        idx = HOME - 3       # ionian
    elif mood >= 0.25:
        idx = HOME - 2       # mixolydian
    elif mood >= -0.10:
        idx = HOME - 1       # dorian
    elif mood >= -0.50:
        idx = HOME           # aeolian
    else:
        idx = DARKEST        # phrygian

    return max(BRIGHTEST + 1, min(DARKEST, idx))


def tonic_for(elapsed_seconds: float, tension: float, period: float = 480.0) -> int:
    """
    Which key we are in. Changes slowly, and never during trouble.

    Eight minutes per step by default: long enough that the ear treats it as
    the piece developing rather than as an event, which is the difference
    between a soundtrack and a slideshow.
    """
    if tension > 0.35:
        # Hold whatever we had. Modulating mid-problem sounds like the floor
        # moving, and there is already enough going on.
        step = int(elapsed_seconds // period)
        return TONIC_CYCLE[max(0, step - 1) % len(TONIC_CYCLE)]
    return TONIC_CYCLE[int(elapsed_seconds // period) % len(TONIC_CYCLE)]


def harmony_for(valence: float, tension: float, elapsed_seconds: float) -> Harmony:
    return Harmony(
        tonic_offset=tonic_for(elapsed_seconds, tension),
        mode_index=mode_for(valence, tension),
    )
