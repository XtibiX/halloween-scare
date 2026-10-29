# Halloween Scare Pi

Motion-triggered Halloween prop. The Pi camera watches the doorway; on motion a
Bluetooth speaker plays a random scare sound, then a spoken Norwegian message.
A quiet ambient loop plays all the time. A web page on the LAN controls it.

## Hardware
- Raspberry Pi 4 (2 GB), headless, see `~/.claude/CLAUDE.md` for resource limits
- Camera Module v1.3 (OV5647) on the CSI ribbon connector
- Bluetooth speaker (MAC goes in `config.toml` under `[speaker]`)

## Stack
- `picamera2` (apt), with a 320x240 lores stream for motion and a hardware MJPEG main stream for the web
- Motion: numpy background subtraction in `scare/motion.py` (no OpenCV, to save RAM)
- Voice: Piper neural TTS (pip, in `.venv`), Norwegian voice `voices/no_NO-talesyntese-medium.onnx`,
  then sox effects. Takes several seconds per sentence on the Pi, so speech is pre-rendered
  into `cache/` on startup and when messages change. Never render during a scare if it can be avoided.
- Audio: PipeWire + WirePlumber, playback via `pw-play`. Scare and ambient sounds mix in PipeWire.
- Web: Flask on port 8000 (`scare/web.py`, `templates/index.html`)

## Layout
- `config.toml`: startup defaults. `settings.json`: values changed on the web page (overrides config)
- Modes (switched on the web page, defined under `[modes.*]` in `config.toml` with label and voice effects):
  `scary` (Skummel) and `kids` (Barnevennlig, silly not frightening, for small children)
- `modes/<mode>/messages.txt`: one Norwegian message per line
- `modes/<mode>/scare/`: played before each message. `modes/<mode>/ambient/`: background loop
- `make_sounds.sh` regenerates the starter sounds for both modes
- `cache/`: rendered speech (safe to delete)

## Setup (fresh Pi)
1. `sudo apt install -y python3-picamera2 python3-numpy python3-flask espeak-ng sox pipewire pipewire-audio wireplumber libspa-0.2-bluetooth`
2. `./setup.sh` (venv + Piper + voice download), then `./make_sounds.sh` (starter sounds)
3. `sudo loginctl enable-linger normann`
4. Bluetooth: `sudo rfkill unblock bluetooth` (blocked by default), then `bluetoothctl power on`.
   WirePlumber 0.5 disables Bluetooth audio without a local login seat (SSH/service), so
   `~/.config/wireplumber/wireplumber.conf.d/80-bluetooth-headless.conf` sets
   `monitor.bluez.seat-monitoring = disabled`. Without it: `br-connection-profile-unavailable`.
5. Pair the speaker with `bluetoothctl` (pair, trust, connect) and put its MAC in `config.toml`.
   Current speaker: MEGABOOM 3, `AA:BB:CC:DD:EE:FF`
6. `cp halloween.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now halloween`

## Running
- Manually: `.venv/bin/python -m scare.main` (from this folder)
- Service logs: `journalctl --user -u halloween -f`
- Web: `http://haloween-pi.local:8000` (hostname really is spelled `haloween-pi`)
- The app keeps running without a camera so audio and the web page can be tested alone.
