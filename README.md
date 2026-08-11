# The Agentisizer

**Hear what your agents are doing.**

You have coding agents working. Reading every transcript means babysitting;
ignoring them means missing the moment one gets stuck. The Agentisizer turns
that stream into a live soundtrack you can leave running in the background —
so you can do something else and still know, without looking, that work is
happening, that something went wrong, or that something has been waiting on
you for five minutes.

A modular synthesiser in the Moog sense: sources patch into a musical engine.
Claude Code agents are just the first input module. The same stack takes any
stream with semantics — APIs, market data, build pipelines, sensors.

```bash
./run-agentisizer.sh setup     # get Sonic Pi installed and running
./run-agentisizer.sh demo      # 90-second tour of every state
./run-agentisizer.sh start     # leave it running
```

## What it sounds like

A subtle four-on-the-floor bed, always there, that you stop noticing within a
minute. Then:

| What happened | What you hear |
| --- | --- |
| **nothing** | near silence — a slow pad, no pulse |
| **agents working** | the pulse and an arpeggio come in; busier work means denser and brighter |
| **good news** | a bright bell figure up the current chord |
| **bad news** | in-key dissonance settles underneath — a flat second against the root |
| **blocked** | a low pulse that gets more insistent the longer it is ignored |
| **resolved** | a falling figure lands on the root, tension drops away |

And underneath all of it, the **key itself moves with the mood**.

## Mood changes the harmony, not just the volume

The modes of the major scale form a natural brightness ordering. Each step
down flattens exactly one degree, so neighbouring modes differ by a single
note and the shift is felt without being announced:

```
lydian  ·  ionian  ·  mixolydian  ·  dorian  ·  aeolian  ·  phrygian
brightest ──────────────────────────────────────────────────► darkest
```

Good news walks up that ladder, trouble walks down it. Neutral rests in
**dorian** — over hours, natural minor reads as mournful where dorian just
reads as calm. Tension is weighted harder than valence, because a soundtrack
that turns radiant the moment one test passes is one nobody believes.

The payoff is at the dark end. Tension is voiced as a **flat second** against
the root. In dorian that note is a chromatic outsider, fighting the key. In
phrygian the flat second is *diatonic* — so as things get worse, the key
moves to meet the dissonance, and the note that was fighting the harmony
becomes the harmony. Trouble resolves into character rather than damage.

This is measurable, not just intended. Chroma analysis of a recorded mood
sweep, looking at the flat second's share of harmonic energy:

| | bright mood | dark mood |
| --- | --- | --- |
| **A♯/B♭** (♭2 — phrygian's defining note) | 0.9% | **52.8%** |
| G (♭7 — mixolydian's defining note) | 38.9% | — |
| F (♭6 — phrygian) | — | 23.0% |

A 58× change in the defining note. The mood is genuinely rewriting the
harmony.

The **key** moves too, but rarely: one step every eight minutes, only through
closely related keys (A → D → C → E), and **never while tension is high** —
modulating mid-crisis sounds like the floor moving. Python decides which key
and mode the mood calls for; the engine holds the change until a phrase
boundary, because *when* a key change lands is a timing decision and timing
lives in Sonic Pi.

`agentisizer start` prints the current key beside each event, so you can see
the harmony track the narrative:

```
progress  refactoring the parser across nine files    A dorian
bad       the fix broke authentication as well        A aeolian
blocked   need the staging database password          A phrygian
resolved  credentials received, deploy is green       A dorian
```

## The part that actually matters

Anyone can trigger a sound on an event. The hard part is building something a
person can stand to have on for six hours. Almost every rule here exists to
defend that:

**Nothing picks a raw pitch.** Data selects a *degree* of the current chord,
so every note is consonant by construction. Mapping numbers to frequencies is
what makes sonification unbearable.

**Loudness is a budget, not a dial.** When the alarm rises, the arpeggio ducks
to make room. Across the entire emotional range of the demo — idle through
full alarm — measured loudness stays inside a 5.4 dB band. Urgency arrives as
a change in *balance*, never as a volume war.

**Fifty events do not make fifty sounds.** Event *rate* becomes a continuous
`activity` level; accents are rate-limited per kind. Routine progress never
gets an accent at all — it is the texture, not an item in a queue.

**Everything decays toward calm.** If the music can't return to quiet, the ear
stops hearing any of it within twenty minutes and the whole thing is pointless.

**Alarms rise slowly and fall fast.** A blocker ramps over three minutes, so a
problem that resolves itself in thirty seconds never becomes a siren. Once you
fix it, the alarm is gone in seven. An alarm that nags after the fix is the
one you mute.

**Bad news is dissonant *within the key*.** A flat second reads as tension
because the ear can place it — not because it hurts.

## How it fits together

```
sources ──► interpreter ──► conductor ──► Sonic Pi engine
(many)      (one)           (one)          (persistent)
```

The seam that matters is between **what happened** and **what it sounds
like**. An input module produces `Event` objects and knows nothing about
music. The musical side never learns where an event came from. That is why a
new source is a small file instead of a rewrite.

- **`agentisizer/events.py`** — the one shape everything collapses into.
- **`agentisizer/interpret.py`** — natural language → one of five kinds, via
  OpenRouter, a local Ollama, or keyword rules.
- **`agentisizer/conductor.py`** — musical state, decay, spacing, escalation.
  The rules above live here.
- **`agentisizer/musical.py`** — key and mode selection: the brightness
  ladder, and the rules about when it is safe to modulate.
- **`engine/engine.rb`** — runs *inside* Sonic Pi, permanently.

### Why the engine lives in Sonic Pi

Python sends **state**, not notes. `/run-code` is used exactly twice — to load
the engine and to stop it. Everything after that is OSC cues to a program
that is already running and listening.

That means timing comes from Sonic Pi's scheduler rather than from a Python
loop with `sleep()` in it. Notes land on the beat because a real sequencer is
placing them. Sending code per note is what makes these systems sound like a
machine gun.

## Sending events

**Any language, no client library** — write a file:

```bash
echo "all tests passed" > ~/.agentisizer/events/$(date +%s).md
```

With frontmatter, if the agent already knows what it is reporting:

```markdown
---
kind: blocked
intensity: 0.9
source: deploy-bot
---
Waiting on the staging database credentials.
```

**Or HTTP**, on localhost:

```bash
curl -s localhost:8912/event -d '{"text": "TypeError in parse_headers"}'
curl -s localhost:8912/state
```

**Or the CLI:**

```bash
./run-agentisizer.sh say "deploy is green again"
```

`kind` is optional everywhere. Leave it out and the interpreter decides.

### Claude Code

Add a hook that posts tool events as they happen:

```json
{
  "hooks": {
    "PostToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "curl -s -m 1 localhost:8912/event -d \"{\\\"text\\\": \\\"$CLAUDE_TOOL_NAME finished\\\", \\\"source\\\": \\\"claude-code\\\"}\" >/dev/null || true"
      }]
    }]
  }
}
```

## The interpreter

Classification is a language judgement, so a language model makes it —
OpenRouter if `OPENROUTER_API_KEY` is set, a local Ollama if one is answering,
otherwise keyword rules.

**The model classifies. It does not compose.** It returns one of five kinds
and a number, and cannot reach the synth. Everything musical downstream is
bounded by the conductor. A bad classification makes the soundtrack briefly
wrong, never unpleasant — letting a language model drive audio parameters
directly is how you get something nobody leaves running.

The keyword fallback is not a stub; it is tested like a feature, because with
no API key it *is* the product. Every case in `tests/test_interpret.py` under
"cases that were wrong on the first real run" is one it actually got wrong
against real agent phrasing.

```bash
./run-agentisizer.sh doctor
```

`doctor` makes a real call rather than reporting configuration — a stale API
key still looks configured, and the fallback is silent by design, so this is
the only place a degraded setup surfaces.

## Requirements

- **Sonic Pi 4 or 5** — `./run-agentisizer.sh setup` will install and launch it
- **Python 3.10–3.13** — the wrapper builds its own venv; 3.14 is not usable
  yet on Homebrew (broken `ensurepip`)
- An LLM is **optional** — and if you use a local one, pick a small
  non-reasoning model. Reasoning models spend their token budget thinking
  before answering a one-word question, which is exactly the wrong trade
  here: `ollama pull llama3.2:3b` rather than a 12B thinker.

## Status

Alpha. The engine, conductor, both input modules, and the CLI work and are
verified against Sonic Pi 5.0. Rough edges:

- The interpreter's LLM path is written and exercised, but every local model
  tried so far has been too slow to use: a 12B reasoning model measured
  27–80s per classification, against a 2.5s budget. `doctor` reports latency
  and falls back to keyword rules. A small non-reasoning model should be
  fine; that combination is not yet verified end to end.
- Long-form variation is still thin. Key and mode move with mood, but there
  is one progression shape per brightness region and no section structure
  above the 16-bar phrase.
- The cohesion supervisor is currently rules-only. The hooks for an LLM to
  propose bounded adjustments are in `Conductor.gain_trim` / `density_trim`,
  but nothing drives them yet.
- Only tested on macOS.

## Licence

MIT. See `LICENSE`.
