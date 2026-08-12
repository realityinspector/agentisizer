# AGENTS.md

Instructions for an AI agent working with The Agentisizer — getting it
running, and then reporting into it so the human you work for can hear what
you are doing without reading your transcript.

Written to be followed literally. Every command here has been run.

---

## What this is, and why you should care

The human you work for cannot read everything you do. If they could, they
would not need you. The Agentisizer turns your activity into a background
soundtrack they can leave running — so they can work on something else and
still notice, without looking, that you are making progress, that something
broke, or that you have been stuck waiting on them for five minutes.

**Your part is small: emit one short sentence when something happens.** You
never touch audio. Everything musical is decided downstream and bounded by
rules you cannot reach, so a badly worded event makes the soundtrack briefly
wrong, never unpleasant.

The thing this buys you: **a human who notices you are blocked.** That is
usually the difference between a two-minute stall and a forty-minute one.

---

## Getting it running

Requires macOS (for now), and Sonic Pi, which the setup command installs.

```bash
git clone https://github.com/realityinspector/agentisizer
cd agentisizer

./run-agentisizer.sh setup      # installs Sonic Pi via brew, launches it, waits for boot
./run-agentisizer.sh doctor     # confirm every layer
./run-agentisizer.sh demo       # 90-second tour — play this for the human once
```

`run-agentisizer.sh` builds its own virtualenv on first use (~15s) and skips
straight to the command after that. There is nothing to activate. Do not run
`python main.py` or `pip install` yourself.

Then leave it running in a background terminal:

```bash
./run-agentisizer.sh start
```

### Verifying, properly

`doctor` checks each layer separately and tells you which is broken:

```
Sonic Pi
  ✓ running — port 38893, cues 4560 (via running process)

Interpreter
  ✓ ollama:llama3.2:3b (3.2s per call)
     slower than the 2.5s ideal, but the rules answer first — most
     events are instant and never reach it
```

Note what `doctor` measures: it warms the model first and times the *second*
call, because Ollama unloads idle models and a cold call measures disk-to-GPU
load rather than classification. The same model reported 12.9s cold and 2.9s
warm.

If Sonic Pi is not running, **nothing will make sound and no error will be
raised at the point you send an event.** Check `doctor` first, always.

### Optional: a local model

Not required — the keyword rules alone score 14/20 on the benchmark. A small
model raises that to 16/20 by handling phrasing with no keywords in it.

```bash
ollama pull llama3.2:3b
./run-agentisizer.sh bench       # prove it helps before you rely on it
```

**Use a small, non-reasoning model.** A 12B reasoning model measured 27–80s
per classification — it spends its token budget thinking before answering a
one-word question. Anything past the timeout never gets used at all, and
`doctor` says so.

---

## Reporting your work

Three ways in. All equivalent; pick whichever is least friction.

### HTTP (best for hooks and scripts)

```bash
curl -s -m 1 localhost:8912/event -d '{"text": "all 240 tests passed"}' >/dev/null || true
```

Always `-m 1` and `|| true`. **Reporting must never be able to break or slow
the work it is reporting on.** If the daemon is down, your job continues.

A bare string works too:

```bash
curl -s -m 1 localhost:8912/event -d 'refactoring the parser' >/dev/null || true
```

### File drop (best when you have no HTTP client)

```bash
echo "the migration finished cleanly" > ~/.agentisizer/events/$(date +%s%N).md
```

Any language that can write a file can drive this. Use a unique filename;
files are consumed and moved to `processed/`.

With frontmatter, when you already know the classification:

```markdown
---
kind: blocked
intensity: 0.9
source: my-agent
---
I need the staging database password to continue.
```

### CLI

```bash
./run-agentisizer.sh say "deploy is green again"
```

If nothing is listening this writes to the file drop instead, so the event
survives until the daemon starts.

---

## Choosing `kind` — read this part

`kind` is optional. Leave it out and it gets classified for you, which is
usually right. Set it when you are certain, especially for `blocked`.

| kind | when | what the human hears |
| --- | --- | --- |
| `progress` | routine work; nothing decided yet | nothing directly — it raises the background density |
| `good` | something genuinely worked | a bright bell figure |
| `bad` | something failed, you are continuing | in-key dissonance underneath; the key darkens |
| `blocked` | **you have stopped and need a human** | an alarm that escalates for as long as you stay stuck |
| `resolved` | a previously bad or blocked thing is fixed | the alarm drops out, tension lifts, the key brightens |

### The one rule that matters

**`blocked` means you have stopped.** It starts an alarm that grows for three
minutes until a human responds. It is the only thing here that demands
attention rather than offering it.

Use it when: you need a credential, a decision between two paths you cannot
choose between, approval for something destructive, or an answer you cannot
derive.

Do **not** use it for: a failing test you are about to fix, a retry that might
work, or anything you are still making progress on. That is `bad`.

**Always send `resolved` when the block clears.** If you do not, the alarm
keeps escalating at a human who has already helped you, and the next thing
they do is mute the system permanently.

### Intensity

Defaults to 0.5, which is fine. Set it when the default would mislead:

- `0.1–0.3` routine progress
- `0.6` a test suite passing, a normal failure
- `0.9–1.0` production, data loss, security, an outage

---

## Rhythm: how often to report

The conductor already defends itself — event *rate* becomes a continuous
density, accents are spaced, and `progress` never makes a discrete sound. You
cannot make it ugly by sending too much. But you can make it uninformative.

**Good practice:**

- One event per meaningful step, not per tool call. "Refactoring the parser
  across nine files" beats nine "edited a file" events.
- Report outcomes, not intentions. "Tests passed" is worth more than "about to
  run tests".
- Write the sentence you would say to a colleague looking over your shoulder.
  The classifier was tuned on that register, and so was the music.
- If you go quiet for a long stretch, send an occasional `progress`. Silence
  means "idle" here, and a human hearing silence assumes you have finished.

**Avoid:** one event per file read, log lines, stack traces (send one sentence
about the failure instead), or anything longer than a sentence — only the
first ~400 characters are considered.

---

## Wiring it into a coding harness

### Claude Code

Add to `.claude/settings.json`. This reports every tool use as progress and
keeps the density honest:

```json
{
  "hooks": {
    "PostToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "curl -s -m 1 localhost:8912/event -d \"{\\\"text\\\": \\\"$CLAUDE_TOOL_NAME finished\\\", \\\"source\\\": \\\"claude-code\\\", \\\"kind\\\": \\\"progress\\\"}\" >/dev/null || true"
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "curl -s -m 1 localhost:8912/event -d '{\"text\": \"turn complete\", \"source\": \"claude-code\", \"kind\": \"good\", \"intensity\": 0.4}' >/dev/null || true"
      }]
    }]
  }
}
```

Hooks give you the texture for free. Send the *interesting* events yourself,
in your own words — the hook cannot tell that a test suite went green.

### Any other harness

Wrap your command runner:

```bash
report() { curl -s -m 1 localhost:8912/event -d "{\"text\": \"$1\"}" >/dev/null || true; }

report "running the integration suite"
if pytest -q; then report "integration suite passed"; else report "integration suite failed"; fi
```

### Several agents at once

Set `source` per agent so the human can tell them apart in the log. They share
one soundtrack — the music reflects the *aggregate*, which is the point: one
mood for the whole fleet, not five competing ones.

```bash
curl -s -m 1 localhost:8912/event \
  -d '{"text": "index rebuilt", "source": "worker-3"}' >/dev/null || true
```

---

## Reading the state back

Useful if you want to know what the human is currently hearing — for example,
to avoid piling on when things are already tense:

```bash
curl -s localhost:8912/state
```

```json
{"ok": true, "activity": 0.74, "valence": -0.21, "tension": 0.43,
 "blocker": 0.0, "events": 128, "blocked_for": 0.0, "key": "A aeolian"}
```

`blocked_for` is seconds since a block started, `0.0` if clear. If it is
climbing and you caused it, and the block is gone, send `resolved` now.

---

## Adding a new input source

The seam is `Event`. A source produces them and knows nothing about music.

1. Write `agentisizer/sources/yours.py` with `start()`, `stop()`,
   `describe()`, calling `emit(Event(...))`.
2. Add it to the `self.sources` list in `agentisizer/daemon.py`.

Copy `sources/filedrop.py`; it is about 100 lines and does nothing clever.
Do not put musical decisions in a source — the conductor owns all of them,
and that separation is why a new source is a small file.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| No sound at all | Sonic Pi not running | `./run-agentisizer.sh setup` |
| Events accepted, still silent | daemon not started | `./run-agentisizer.sh start` |
| `command not found: python` | you bypassed the wrapper | use `./run-agentisizer.sh` |
| Classification looks wrong | model slow or absent | `./run-agentisizer.sh doctor`, then `bench` |
| Alarm won't stop | a block was never resolved | send `kind: resolved` |
| Sonic Pi restarted, silence | ports and token changed | restart the daemon; it rediscovers |

Sonic Pi's own errors go to `~/.sonic-pi/log/spider.log`.

**Do not paste Python into the Sonic Pi editor window.** It runs Ruby; a
`.py` file pasted there raises a syntax error that has nothing to do with
this project.

---

## Rules for changing this project

- **Never let a model choose audio parameters.** It returns a kind and a
  number. Bounding it is what keeps this listenable.
- **Musical decisions belong in `conductor.py` or `engine.rb`**, never in a
  source or in the interpreter.
- **Run `./run-agentisizer.sh bench` after touching a prompt or a model.** The
  keyword rules are the floor; a model that cannot beat them is pure latency.
- **Timing lives in Sonic Pi.** Python sends state; it does not sequence.
  `/run-code` is used exactly twice — load and stop.
- Tests: `./run-agentisizer.sh test`. Anything about spacing, decay,
  escalation or key selection is testable without Sonic Pi running, so test
  it.
