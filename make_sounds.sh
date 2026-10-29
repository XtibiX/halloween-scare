#!/bin/bash
# Generates starter sound effects with sox so the prop works out of the box.
# Real recorded effects sound much better: drop .wav/.ogg/.mp3 files into
# modes/<mode>/scare (played on each scare) and modes/<mode>/ambient (quiet
# background loop), or upload them from the web page.
set -euo pipefail
cd "$(dirname "$0")"
R="-r 44100 -b 16"

# --- Scary mode ---
S=modes/scary/scare
A=modes/scary/ambient
mkdir -p $S $A
sox -n $R $S/shriek.wav synth 2.2 sawtooth 500:1800 sawtooth 530:1850 remix - \
    overdrive 15 tremolo 9 60 reverb 70 fade 0.02 2.2 0.8 gain -n -2
sox -n $R $S/growl.wav synth 2.8 brownnoise gain -8 lowpass 350 overdrive 25 \
    tremolo 14 90 reverb 60 fade 0.3 2.8 0.8 gain -n -2
sox -n $R $S/creak.wav synth 3 square 40:85 gain -8 lowpass 2500 bandpass 900 500 \
    tremolo 23 90 reverb 40 fade 0.2 3 0.6 gain -n -3
sox -n $R $S/heartbeat.wav synth 0.18 sine 70:35 fade 0 0.18 0.15 pad 0 0.12 \
    repeat 1 pad 0 0.45 repeat 5 reverb 25 gain -n -2
sox -n $R $A/wind.wav synth 40 pinknoise band -n 400 300 tremolo 0.12 70 \
    reverb 50 fade 4 40 4 gain -n -8
sox -n $R $A/drone.wav synth 40 sine 55 sine 58.5 remix - tremolo 0.25 40 \
    reverb 80 fade 4 40 4 gain -n -8

# --- Kids mode: silly, not frightening ---
S=modes/kids/scare
A=modes/kids/ambient
mkdir -p $S $A
# friendly ghost "woooo"
sox -n $R $S/ghost-woo.wav synth 2.4 sine 300:520 sine 304:524 remix - \
    tremolo 5 30 reverb 50 fade 0.3 2.4 0.9 gain -n -4
# cartoon boing
sox -n $R $S/boing.wav synth 0.7 sine 120:700 tremolo 16 70 fade 0 0.7 0.5 \
    reverb 20 gain -n -4
# playful spooky organ: A minor notes rising, then the chord held. Every note
# fades in and out so there are no clicks between them.
T2=$(mktemp -d cache/organ.XXXX)
for f in 220 261.6 329.6; do
    sox -n $R $T2/n$f.wav synth 0.42 sine $f sine $((${f%.*} * 2)) remix - gain -12 \
        fade h 0.04 0.42 0.12
done
sox -n $R $T2/chord.wav synth 1.4 sine 220 sine 261.6 sine 329.6 remix - gain -14 \
    fade h 0.05 1.4 0.9
sox $T2/n220.wav $T2/n261.6.wav $T2/n329.6.wav $T2/chord.wav $S/organ.wav \
    pad 0 0.8 reverb 50 gain -n -6
rm -rf "$T2"
rm -f $A/crickets.wav $A/breeze.wav  # replaced by the two files below
T=$(mktemp -d cache/sounds.XXXX)

# Music box: playful minor-key waltz on a plucked string, note by note.
# Numbers are semitones above A5; r = rest.
i=0
for n in 0 3 7 5 3 2 0 3 7 12 7 r 8 7 5 3 2 0 -1 2 7 0 r r; do
    f=$T/note$(printf %02d $i).wav
    if [ "$n" = r ]; then
        sox -n $R -c 1 "$f" trim 0 0.36
    else
        sox -n $R "$f" synth 0.36 pluck %$((n + 12)) fade 0 0.36 0.3 lowpass 4000
    fi
    i=$((i + 1))
done
sox $T/note*.wav $T/tune.wav
sox $T/tune.wav $A/music-box.wav repeat 3 reverb 60 fade 0.5 0 2 gain -n -12

# Night forest: soft breeze, gentle cricket chirps and an owl now and then
sox -n $R $T/breeze.wav synth 40 pinknoise band -n 300 200 tremolo 0.1 50
sox -n $R $T/crickets.wav synth 0.12 sine 2400 tremolo 40 100 fade 0.01 0.12 0.03 \
    pad 0 0.55 repeat 58 lowpass 2800 gain -22
sox -n $R $T/hoot.wav synth 0.4 sine 390:340 fade 0.05 0.4 0.2
sox $T/hoot.wav $T/owl.wav pad 0 0.35 repeat 1          # "hoo... hoo"
sox $T/owl.wav $T/owl1.wav pad 8 0 gain -3
sox $T/owl.wav $T/owl2.wav pad 27 0 gain -5
sox -m $T/breeze.wav $T/crickets.wav $T/owl1.wav $T/owl2.wav $A/night-forest.wav \
    reverb 50 fade 4 40 4 gain -n -12

rm -rf "$T"

ls -l modes/*/scare modes/*/ambient
