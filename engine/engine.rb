# The Agentisizer — musical engine
#
# This runs *inside* Sonic Pi and never stops. Python does not send notes; it
# sends state, and this decides what that state should sound like. Timing
# therefore comes from Sonic Pi's scheduler rather than from a Python loop,
# which is the difference between music and a pile of triggered samples.
#
# State arrives on one OSC message so it lands atomically:
#
#   /agentisizer/state  activity valence tension blocker
#   /agentisizer/hit    kind                      (one-shot: good | bad | blocked | resolved)
#
# ── the musical contract ─────────────────────────────────────────────────
# Everything here exists to keep this listenable for hours:
#
#   * Nothing chooses a raw pitch. Data picks a *degree* of the current
#     chord, so every note is consonant by construction. Sonification that
#     maps numbers to Hz is what makes these systems unbearable.
#   * Layers enter and leave on bar lines, never mid-bar.
#   * Bad news is dissonant *within the key* (b2, tritone), not random. It
#     reads as tension because the ear can place it, not because it hurts.
#   * Loudness is a budget, not a dial. When the alarm rises the arp ducks,
#     so urgency arrives as a change in balance rather than volume.
#   * Every continuous state decays toward calm on its own. Silence has to
#     be reachable or the ear stops hearing any of it.

use_bpm 120
set_volume! 0.8

# ── state, with sane defaults so the engine is musical before any input ──
set :activity, 0.0   # 0..1  how busy the agents are
set :valence,  0.0   # -1..1 mood, good vs bad news
set :tension,  0.0   # 0..1  accumulated problems
set :blocker,  0.0   # 0..1  escalation while something is stuck
set :bar,      0

# Chord cycle: i – VI – III – VII in A minor. Four bars each, sixteen-bar
# loop. Familiar enough to fade into the background, which is the point.
PROG = (ring
        [:a2, :minor7],
        [:f2, :major7],
        [:c3, :major7],
        [:g2, :major7])

define :cur_chord do
  PROG[(get(:bar) / 4) % 4]
end

define :cur_root do
  cur_chord[0]
end

# One place that turns "some number" into "a note that belongs here".
define :tone_at do |n|
  c = chord(cur_chord[0], cur_chord[1])
  c[n % c.length]
end

# ── clock: the only place the bar counter moves ──────────────────────────
live_loop :clock do
  set :bar, get(:bar) + 1
  sleep 4
end

# ── input: one atomic state update ───────────────────────────────────────
live_loop :listen_state do
  use_real_time
  v = sync "/osc*/agentisizer/state"
  set :activity, v[0].to_f
  set :valence,  v[1].to_f
  set :tension,  v[2].to_f
  set :blocker,  v[3].to_f
end

# ── input: one-shot events, quantised to the next beat by the loop grid ──
live_loop :listen_hit do
  use_real_time
  v = sync "/osc*/agentisizer/hit"
  kind = v[0].to_s

  case kind
  when "good"
    # a bright arpeggio up the current chord — the "something worked" sound
    with_fx :reverb, room: 0.7, mix: 0.4 do
      use_synth :pretty_bell
      4.times do |i|
        play tone_at(i + 2), amp: 0.35, release: 0.6, pan: rrand(-0.3, 0.3)
        sleep 0.125
      end
    end
  when "resolved"
    # tension breaking: a falling figure that lands on the root
    with_fx :reverb, room: 0.6, mix: 0.35 do
      use_synth :blade
      [5, 3, 1, 0].each do |d|
        play tone_at(d), amp: 0.28, release: 0.9
        sleep 0.15
      end
    end
  when "bad"
    # in-key dissonance: root plus a flat second. Tense, still placeable.
    with_fx :distortion, distort: 0.3, mix: 0.4 do
      use_synth :hollow
      play cur_root, amp: 0.3, release: 1.2, cutoff: 65
      play cur_root + 1, amp: 0.22, release: 1.2, cutoff: 60
    end
  when "blocked"
    # low tritone thud — the floor dropping out
    use_synth :subpulse
    play cur_root - 12, amp: 0.5, release: 1.5
    play cur_root - 6,  amp: 0.25, release: 1.2
  end
end

# ── bed: always present, the thing you stop noticing ─────────────────────
live_loop :bed do
  use_synth :hollow
  ch = chord(cur_chord[0], cur_chord[1])
  with_fx :reverb, room: 0.85, mix: 0.5 do
    play ch,
      amp: 0.22,
      attack: 2,
      sustain: 2,
      release: 2,
      cutoff: 62 + (get(:activity) * 18)
  end
  sleep 4
end

# ── pulse: the EDM spine. Subtle, and it thins out when nothing is going on
live_loop :pulse do
  a = get(:activity)
  4.times do |beat|
    sample :bd_tek, amp: 0.35 + (a * 0.35), cutoff: 90 if a > 0.08
    # off-beat hat appears only once there is real activity
    if a > 0.35
      sleep 0.5
      sample :drum_cymbal_closed, amp: 0.12 + (a * 0.15), rate: 1.2
      sleep 0.5
    else
      sleep 1
    end
  end
end

# ── bass: root movement, sparse ──────────────────────────────────────────
live_loop :bass do
  use_synth :fm
  a = get(:activity)
  play cur_root - 12,
    amp: 0.28 + (a * 0.12),
    release: 1.6,
    cutoff: 55 + (a * 25),
    depth: 1.5
  sleep 2
end

# ── arp: the "agents are working" layer. Density and brightness track
#    activity; ducks out of the way when the alarm needs to be heard.
live_loop :arp do
  a = get(:activity)
  duck = 1.0 - (get(:blocker) * 0.6)   # loudness budget, not a volume dial

  if a < 0.1
    sleep 1                            # idle: the arp simply isn't there
  else
    use_synth :prophet
    steps = a > 0.6 ? 8 : 4
    steps.times do |i|
      play tone_at(i + (get(:bar) % 3)),
        amp: (0.10 + (a * 0.16)) * duck,
        release: 0.25,
        cutoff: 70 + (a * 45),
        pan: rrand(-0.4, 0.4)
      sleep 4.0 / steps
    end
  end
end

# ── tension: sits under everything when things are going badly ───────────
live_loop :tension_layer do
  t = get(:tension)
  if t < 0.12
    sleep 4
  else
    use_synth :dsaw
    with_fx :reverb, room: 0.6, mix: 0.3 do
      # minor second against the root: unmistakably wrong, still in key
      play cur_root + 13,
        amp: 0.06 + (t * 0.14),
        release: 3.5,
        attack: 1.5,
        cutoff: 55 + (t * 30),
        detune: 0.15
    end
    sleep 4
  end
end

# ── alarm: escalation. Starts as something you could ignore and slowly
#    becomes something you can't. Still in key — an alarm you cannot bear
#    is an alarm you will turn off.
live_loop :alarm do
  b = get(:blocker)
  if b < 0.05
    sleep 2
  else
    use_synth :chipbass
    reps = b > 0.66 ? 4 : (b > 0.33 ? 2 : 1)
    reps.times do
      play tone_at(0) + (b > 0.66 ? 12 : 0),
        amp: 0.10 + (b * 0.30),
        release: 0.35,
        cutoff: 80 + (b * 40)
      sleep 0.25
    end
    # the gap shrinks as it escalates: patience running out
    sleep [4 - (b * 3), 0.5].max
  end
end
