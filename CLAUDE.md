# Halloween Scare Pi

Motion-triggered Halloween prop. The Pi camera watches the doorway; on motion a
Bluetooth speaker plays a random scare sound, then a spoken Norwegian message.
A quiet ambient loop plays all the time. A web page on the LAN controls it.

## Hardware
- Raspberry Pi 4 (2 GB), headless, see `~/.claude/CLAUDE.md` for resource limits
- Camera Module v1.3 (OV5647) on the CSI ribbon connector
- Bluetooth speaker (MAC goes in `config.local.toml` under `[speaker]`, not in git)

## Stack
- `picamera2` (apt), with a 320x240 lores stream for motion and a hardware MJPEG main stream for the web
- Motion: numpy background subtraction in `scare/motion.py` (no OpenCV, to save RAM)
- Voice: Piper neural TTS (pip, in `.venv`). Per-mode voice: scary = English `voices/en_GB-alan-medium.onnx`
  (English messages), kids = Norwegian `voices/no_NO-talesyntese-medium.onnx` (the default),
  then sox effects. Takes several seconds per sentence on the Pi, so speech is pre-rendered
  into `cache/` on startup and when messages change. Never render during a scare if it can be avoided.
- Audio: PipeWire + WirePlumber, playback via `pw-play`. Scare and ambient sounds mix in PipeWire.
- Web: Flask on port 8000 (`scare/web.py`, `templates/index.html`)

## Layout
- `config.toml`: startup defaults. `config.local.toml` (not in git): machine-specific overrides
  (speaker MAC), merged per section. `settings.json`: values changed on the web page (overrides config)
- Modes (switched on the web page, defined under `[modes.*]` in `config.toml` with label and voice effects):
  `scary` (Skummel) and `kids` (Barnevennlig, silly not frightening, for small children), plus
  `auto` (picks one per scare, below) and `off` (Av: no scares, no background sound)
- Auto mode (`[auto]` in config, `scare/people.py`): on each scare, YOLO26n (`models/yolo26n-416.onnx`,
  Ultralytics, AGPL, exported to ONNX at 416 px on a PC since export needs PyTorch, hosted on this repo's
  `models` release; onnxruntime, ~0.29 s/frame, loaded on first use; falls back to YOLOX-nano
  `models/yolox_nano.onnx` if missing; `people.py` recognises the layout from the output shape) looks at the visitors for 1.5 s; if anyone is shorter
  than `kid_height_cm` (130), or nobody is seen clearly, or it isn't calibrated, it plays the kid mode,
  else the scary mode. Always fail towards kid mode. Height = box height / size of 1 cm at the feet position.
  Calibrated on the web page with a person of known height at two spots (`settings["calibration"]`, [feet, size]
  each); the size grows linearly with how low the feet are in the picture (flat ground, camera above it), so
  heights are right at any distance. One spot = same scale everywhere. Feet cut off = no height.
- `modes/<mode>/messages.txt`: one message per line (scary: English, kids: Norwegian). Norwegian lines must
  not use English words like "Halloween": the Norwegian voice mispronounces them. Use "kveld"/"aften".
- `modes/<mode>/scare/`: played before each message. `modes/<mode>/ambient/`: background loop
- `make_sounds.sh` regenerates the starter sounds for both modes
- `cache/`: rendered speech (safe to delete)

## Setup (fresh Pi)
Full user-facing steps are in README.md; keep both in sync.
1. `sudo apt install -y git curl python3-venv python3-picamera2 python3-numpy python3-flask python3-pil sox pipewire pipewire-audio wireplumber libspa-0.2-bluetooth`
2. `./setup.sh` (venv + Piper + voice download), then `./make_sounds.sh` (starter sounds)
3. `sudo loginctl enable-linger normann`
4. Bluetooth: `sudo rfkill unblock bluetooth` (blocked by default), then `bluetoothctl power on`.
   WirePlumber 0.5 disables Bluetooth audio without a local login seat (SSH/service), so
   `system/80-bluetooth-headless.conf` (installed by setup.sh into `~/.config/wireplumber/wireplumber.conf.d/`) sets
   `monitor.bluez.seat-monitoring = disabled`. Without it: `br-connection-profile-unavailable`.
5. Pair the speaker with `bluetoothctl` (pair, trust, connect) and put its MAC in `config.local.toml`.
   Current speaker: MEGABOOM 3 (MAC in `config.local.toml`)
6. `cp halloween.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now halloween`

## Running
- Manually: `.venv/bin/python -m scare.main` (from this folder)
- Service: `systemctl --user restart halloween` (or stop/status). Logs: `tail -f cache/app.log` (no user journal on this Pi)
- Web: `http://haloween-pi.local:8000` (hostname really is spelled `haloween-pi`)
- The app keeps running without a camera so audio and the web page can be tested alone.
- Tests: `.venv/bin/python -m unittest` (stdlib unittest, `tests/`, a few seconds). They run the app in a
  temp folder with a fake camera and person detector; no camera, speaker or Piper needed. GitHub Actions
  runs them on every push (`.github/workflows/tests.yml`). Auto mode's kid-mode fallbacks must stay tested.
