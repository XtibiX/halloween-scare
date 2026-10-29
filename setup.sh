#!/bin/bash
# One-time setup that doesn't need sudo: Python venv with Piper TTS and the
# Norwegian voice. Run the apt install from CLAUDE.md first.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    # system-site-packages: use apt's picamera2, numpy and flask
    python3 -m venv --system-site-packages .venv
fi
.venv/bin/pip install --upgrade piper-tts

VOICE=no_NO-talesyntese-medium
URL=https://huggingface.co/rhasspy/piper-voices/resolve/main/no/no_NO/talesyntese/medium
mkdir -p voices
for f in "$VOICE.onnx" "$VOICE.onnx.json"; do
    [ -s "voices/$f" ] || curl -fL --retry 3 -o "voices/$f" "$URL/$f"
done

# Person detector for Auto mode (YOLOX-nano, ~4 MB)
mkdir -p models
[ -s models/yolox_nano.onnx ] || curl -fL --retry 3 -o models/yolox_nano.onnx \
    https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx

mkdir -p cache
echo "Setup done. Test the voice with:"
echo "  echo 'Hei, jeg ser deg.' | .venv/bin/python -m piper -m voices/$VOICE.onnx -f cache/test.wav && pw-play cache/test.wav"
