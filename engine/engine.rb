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

# Harmony. Python decides which key and mode the mood calls for; we decide
# when to move there. `want_*` is the request, the un-prefixed pair is what
# is actually sounding, and the two only meet on a phrase line.
set :want_tonic, 0
set :want_mode,  4
set :tonic,      0
set :mode_i,     4
set :last_arp,   0   # for voice leading — see nearest_tone

HOME = :a2           # everything is an offset from here

# Brightest to darkest. Each step down flattens exactly one degree, so
# neighbouring modes differ by a single note and the shift is felt rather
# than announced. Locrian is omitted: no perfect fifth, it sounds broken
# rather than dark.
MODES = (ring :lydian, :ionian, :mixolydian, :dorian, :aeolian, :phrygian)

define :cur_mode do
  MODES[get(:mode_i)]
end

define :cur_tonic do
  note(HOME) + get(:tonic)
end

# Which scale degree the current bar sits on. Two progressions, because
# i-VI-III-VII is idiomatic in the dark modes and limp in the bright ones,
# where I-vi-IV-V is what the ear expects.
define :cur_degree do
  bright = get(:mode_i) <= 2
  prog = bright ? [1, 6, 4, 5] : [1, 6, 3, 7]
  prog[(get(:bar) / 4) % 4]
end

# The chord under everything right now, built from the mode itself rather
# than from a hardcoded quality — so it is diatonic by construction whatever
# mode we have drifted into.
define :cur_chord do
  chord_degree(cur_degree, cur_tonic, cur_mode, 4)
end

define :cur_root do
  cur_chord[0]
end

# One place that turns "some number" into "a note that belongs here".
define :tone_at do |n|
  c = cur_chord
  c[n % c.length]
end

# Voice leading: of all the octaves of the chord tones available, take the
# one closest to where the line just was. Without this the arpeggio leaps
# arbitrarily and reads as data rather than as a melody.
define :nearest_tone do |n, from|
  target = tone_at(n)
  best = target
  [-24, -12, 0, 12, 24].each do |shift|
    best = target + shift if (target + shift - from).abs < (best - from).abs
  end
  best
end

# ── clock: the only place the bar counter moves, and the only place the
#    key is allowed to change. A modulation that lands mid-phrase sounds
#    like a mistake; on the phrase line it sounds like the piece developing.
live_loop :clock do
  b = get(:bar) + 1
  set :bar, b
  if b % 16 == 0
    set :tonic,  get(:want_tonic)
    set :mode_i, get(:want_mode)
  end
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
  set :want_tonic, (v[4] || 0).to_i
  set :want_mode,  (v[5] || 4).to_i
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
    # A flat second — same dissonance as before, still in key — but voiced an
    # octave above the bass and plucked rather than swelled.
    #
    # The first version played the root itself through :hollow, which put it
    # in exactly the register the bass and pad already occupy, with a soft
    # attack. Measured against a busy mix it was inaudible: low-band energy at
    # the accent was indistinguishable from the bars either side of it. An
    # alert nobody can hear is not an alert, and this is now the sound a
    # pending decision makes.
    #
    # The fix is separation, not volume — loudness is a budget. A transient is
    # what makes something audible over a sustained bed, so this plucks, and
    # the two notes are staggered so the clash is legible as an arrival rather
    # than a chord.
    with_fx :reverb, room: 0.4, mix: 0.25 do
      use_synth :pluck
      play cur_root + 12, amp: 0.55, release: 1.4
      sleep 0.09
      play cur_root + 13, amp: 0.45, release: 1.6
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
  # Voiced an octave up: the bass owns everything below this, and two
  # instruments sharing the bottom octave is the fastest way to mud.
  ch = cur_chord.map { |n| n + 12 }
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
      # Step to the chord tone nearest the last one, rather than to whatever
      # octave the chord happens to list. Small intervals read as a melody;
      # arbitrary leaps read as data.
      n = nearest_tone(i + (get(:bar) % 3), get(:last_arp))
      n = tone_at(0) + 12 if (n - note(:a4)).abs > 18   # keep it in register
      set :last_arp, n
      play n,
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
      # The flat second, an octave up. In Aeolian this is a chromatic
      # outsider; in Phrygian it is diatonic — so as things worsen and the
      # mode darkens, the key moves to meet this note and the dissonance
      # becomes character rather than damage.
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
    # tb303 rather than a chiptune blip: resonance carries urgency without
    # the ice-pick top end you cannot listen to for ten minutes. The pitch
    # stays on a chord tone, so even at full alarm it belongs to the key.
    use_synth :tb303
    reps = b > 0.66 ? 4 : (b > 0.33 ? 2 : 1)
    reps.times do
      play tone_at(0) + (b > 0.66 ? 12 : 0),
        amp: 0.09 + (b * 0.26),
        release: 0.32,
        cutoff: 70 + (b * 45),
        res: 0.7 + (b * 0.25)
      sleep 0.25
    end
    # the gap shrinks as it escalates: patience running out
    sleep [4 - (b * 3), 0.5].max
  end
end
