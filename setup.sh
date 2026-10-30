#!/bin/bash
# One-time setup that doesn't need sudo. Run the apt install from README.md first.
# Safe to run again: it skips what is already done.
set -euo pipefail
cd "$(dirname "$0")"

# Python venv with Piper TTS. system-site-packages: use apt's picamera2,
# numpy, flask and pillow instead of compiling them
if [ ! -d .venv ]; then
    python3 -m venv --system-site-packages .venv
fi
# no --upgrade: a newer Piper could change its command-line options
.venv/bin/pip install piper-tts

# Piper voices: Norwegian (kids mode, default) and British English (scary mode)
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main
mkdir -p voices
for v in no/no_NO/talesyntese/medium/no_NO-talesyntese-medium \
         en/en_GB/alan/medium/en_GB-alan-medium; do
    for ext in onnx onnx.json; do
        f=voices/$(basename "$v").$ext
        [ -s "$f" ] || curl -fL --retry 3 -o "$f" "$BASE/$v.$ext"
    done
done

# Person detectors for Auto mode: YOLO26n (~10 MB, exported from Ultralytics at
# 416 px and attached to this repo's "models" release), and YOLOX-nano (~4 MB),
# which the app falls back to if YOLO26n is missing
mkdir -p models
[ -s models/yolo26n-416.onnx ] || curl -fL --retry 3 -o models/yolo26n-416.onnx \
    https://github.com/XtibiX/halloween-scare/releases/download/models/yolo26n-416.onnx
[ -s models/yolox_nano.onnx ] || curl -fL --retry 3 -o models/yolox_nano.onnx \
    https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx

# Bluetooth audio on a headless Pi: WirePlumber otherwise only allows it for
# someone logged in at the Pi's own screen (fails with br-connection-profile-unavailable)
WP=~/.config/wireplumber/wireplumber.conf.d
if [ ! -f "$WP/80-bluetooth-headless.conf" ]; then
    mkdir -p "$WP"
    cp system/80-bluetooth-headless.conf "$WP/"
    systemctl --user restart wireplumber || true
fi

# systemd user service (start it with: systemctl --user enable --now halloween)
mkdir -p ~/.config/systemd/user cache
cp halloween.service ~/.config/systemd/user/
systemctl --user daemon-reload

echo
echo "Setup done. Next: ./make_sounds.sh, pair the speaker, then"
echo "  systemctl --user enable --now halloween"
