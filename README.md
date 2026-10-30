# Halloween Scare Pi 🎃

A motion-triggered Halloween prop for a Raspberry Pi with a camera and a Bluetooth speaker.
When someone walks up to the door, it plays a spooky sound followed by a spoken message.
A quiet background sound loops in between. Everything is controlled from a web page on your phone.

- **Modes:**
  - 💀 *Skummel*: scary, English troll voice
  - 👻 *Barnevennlig*: kid-friendly, Norwegian friendly-ghost voice, spooky to listen to but only nice messages
  - 🎯 *Auto*: estimates visitors' height and plays kid-friendly if anyone is under 130 cm
  - ⏻ *Av*: off
- **Natural voices** with [Piper](https://github.com/OHF-Voice/piper1-gpl) neural text-to-speech, running offline on the Pi
- **Web page** with live camera view, mode buttons, sensitivity and volume sliders, message editor and sound uploads

## What you need

| | |
|---|---|
| **Raspberry Pi** | Pi 4 (2 GB is enough), with power supply and a 16 GB+ SD card. Pi 5 is untested: it has no hardware video encoder for the live view |
| **OS** | Raspberry Pi OS **64-bit**, Debian 13 "trixie" based (Lite/headless is fine) |
| **Camera** | Any Raspberry Pi camera on the ribbon connector (developed with Camera Module v1.3, OV5647) |
| **Speaker** | Any Bluetooth speaker (tested with UE MEGABOOM 3) |
| **Network** | Wi-Fi or Ethernet, so you can open the web page from a phone on the same network |
| **Light** | Some light where visitors stand: the camera can't see in the dark |

## Installation

### 1. System packages

```bash
sudo apt update
sudo apt install -y git curl python3-venv python3-picamera2 python3-numpy python3-flask python3-pil \
    sox pipewire pipewire-audio wireplumber libspa-0.2-bluetooth
```

| Package | Used for |
|---|---|
| `python3-picamera2` | Camera access (libcamera) |
| `python3-numpy`, `python3-pil` | Motion detection and image handling |
| `python3-flask` | The web page |
| `sox` | Voice effects and generating the starter sounds |
| `pipewire`, `pipewire-audio`, `wireplumber`, `libspa-0.2-bluetooth` | Audio and Bluetooth speaker playback (`pw-play`) |
| `python3-venv`, `curl`, `git` | Installing Piper and downloading voices and models |

Also let your services keep running when you log out, and start at boot:

```bash
sudo loginctl enable-linger $USER
```

### 2. Check the camera

```bash
rpicam-hello --list-cameras
```

You should see your camera listed. If it says **"No cameras available"**:
1. Power off and reseat the ribbon cable at both ends (on a Pi 4, the blue side faces the USB/Ethernet ports).
2. If it's still missing, tell the Pi which camera you have. For a Camera Module v1 (OV5647), edit
   `/boot/firmware/config.txt`: set `camera_auto_detect=0` and add `dtoverlay=ov5647` under `[all]`, then reboot.
   (Module v2: `imx219`, Module 3: `imx708`.)

### 3. Get the code and run the setup

```bash
git clone https://github.com/XtibiX/halloween-scare.git ~/halloween
cd ~/halloween
./setup.sh         # Python venv + Piper, voices (~125 MB), person detector, Bluetooth fix, service file
./make_sounds.sh   # starter sound effects for both modes
```

`setup.sh` doesn't need sudo and is safe to run again.

### 4. Pair the Bluetooth speaker

Bluetooth is often blocked on a fresh Raspberry Pi OS, so unblock it first:

```bash
sudo rfkill unblock bluetooth
bluetoothctl power on
```

Put the speaker in pairing mode, then:

```bash
bluetoothctl --timeout 25 scan on      # wait for it to finish
bluetoothctl devices                   # find your speaker's address, e.g. AA:BB:CC:DD:EE:FF
bluetoothctl pair    XX:XX:XX:XX:XX:XX
bluetoothctl trust   XX:XX:XX:XX:XX:XX
bluetoothctl connect XX:XX:XX:XX:XX:XX
wpctl status                           # the speaker should be listed under Sinks with a *
```

Put the speaker's address in a new file `config.local.toml` (not in git), so the app reconnects it
automatically before each scare:

```bash
printf '[speaker]\nmac = "XX:XX:XX:XX:XX:XX"\n' > config.local.toml
```

### 5. Start it

```bash
systemctl --user enable --now halloween
```

Open **`http://<pi-name>.local:8000`** on your phone (or `http://<pi-ip-address>:8000`; `hostname -I` shows it).

The first start renders all the messages with Piper, which takes 15 seconds per message on a Pi 4
(a few minutes in total). The page shows "Preparing … messages" until it's done.

## Using it

- **Mode buttons** at the top switch between Skummel, Barnevennlig, Auto and Av (off).
- **Armed/Disarmed** turns motion scares on or off but keeps the background sound. **Av** turns everything off.
- **Scare now** plays a scare right away; type text in the box to make it say something specific.
- **Tuning sliders:** how much movement triggers a scare, time between scares, volumes.
- **Edit content for** picks which mode's messages and sounds you're editing below.
- **Messages:** one per line. The scary mode's voice is English, the kid-friendly one is Norwegian;
  write each mode's messages in its language (the Norwegian voice mispronounces English words like "Halloween").
- **Sounds:** upload MP3, WAV, OGG or FLAC (not M4A/AAC). *Scare sounds* play before each message (keep them
  2-5 s); *background sounds* loop quietly (30 s or longer works best). Free effects: pixabay.com/sound-effects,
  freesound.org (filter on CC0).

### Auto mode (height detection)

Auto uses a small person detector (YOLO26n) to estimate how tall the visitors are, and plays the
kid-friendly mode if **anyone** is shorter than the limit (130 cm by default). When in doubt (not calibrated,
nobody seen clearly) it always picks kid-friendly.

For it to work:
1. Place the camera **1-1.5 m off the ground**, level or tilted slightly down, about **3 m** from where visitors
   stand, so a whole person fits in the picture head to feet. (A camera on the ground looking up can't measure height.)
2. Tap **🎯 Auto**, stand on the doormat, type your height (e.g. `180` or `1,80`) and tap **Calibrate**.
3. Stand 2-3 m further away (still head to feet in view) and tap **Add 2nd spot**. People further away look
   smaller; with two spots the app knows by how much, so heights are right at any distance.
4. Walk towards the camera: boxes on the video show green (adult, scary) or yellow (under the limit,
   kid-friendly), and your height should stay about the same.

Calibrate again whenever you move the camera, or after changing the person detector.

## Configuration

`config.toml` holds the defaults: speaker address, volumes, motion sensitivity, the voice and sox effects for
each mode, and Auto mode settings. Changes made on the web page are saved in `settings.json` and override
`config.toml` (delete it to go back to the defaults). `config.local.toml` holds machine-specific values like
the speaker address (and the camera colour profile for a NoIR camera) and overrides `config.toml`. Restart after editing either file:

```bash
systemctl --user restart halloween
```

## Files

```
config.toml               settings and per-mode voices
modes/scary/, modes/kids/ messages.txt, scare/ and ambient/ sounds for each mode
scare/                    the app (camera, motion, people detection, audio, web)
templates/index.html      the web page
tests/                    automatic tests (no camera or speaker needed)
setup.sh, make_sounds.sh  setup and starter-sound scripts
halloween.service         systemd user service
system/                   WirePlumber config for headless Bluetooth audio (installed by setup.sh)
```

Downloaded and generated files (`.venv/`, `voices/`, `models/`, `cache/`, all sound files) are not in git;
`setup.sh` and `make_sounds.sh` recreate them. Sounds you upload on the web page stay on your Pi only.

## Tests

```bash
.venv/bin/python -m unittest
```

They take a few seconds and need no camera, speaker or voices. GitHub runs them on every push.

## Troubleshooting

| Problem | Fix |
|---|---|
| Logs | `tail -f ~/halloween/cache/app.log` |
| App not running | `systemctl --user status halloween`, then `systemctl --user restart halloween` |
| `br-connection-profile-unavailable` when connecting the speaker | Run `./setup.sh` again (installs the WirePlumber fix), then `bluetoothctl connect ...` |
| Bluetooth `Failed to set power on` | `sudo rfkill unblock bluetooth` |
| `rpicam-hello: symbol lookup error` | Camera tools and libcamera out of sync after an update: `sudo apt full-upgrade` |
| Camera picture is pink | A NoIR camera (no infrared filter). Add `[camera]` with `tuning = "ov5647_noir.json"` to `config.local.toml` (Module v2: `imx219_noir.json`, Module 3: `imx708_noir.json`) and restart |
| Start of a sound gets cut off | Normal for Bluetooth speakers waking up; the background sound keeps the speaker awake |
| Page doesn't show changes | Close the browser tab and open the page again |
| SSH drops kill your terminal | Run long sessions inside `tmux`; the app itself runs as a service and is unaffected |
| Slow or laggy over Wi-Fi | Turn off Wi-Fi power saving: `printf '[connection]\nwifi.powersave = 2\n' \| sudo tee /etc/NetworkManager/conf.d/wifi-powersave-off.conf` and reboot |

## Credits

- Voices: [Piper](https://github.com/OHF-Voice/piper1-gpl) (GPL-3.0, run as a separate program) with the `no_NO-talesyntese-medium` and `en_GB-alan-medium` voices
- Person detection: [Ultralytics YOLO26n](https://github.com/ultralytics/ultralytics) (AGPL-3.0), exported to ONNX at 416 px;
  [YOLOX-nano](https://github.com/Megvii-BaseDetection/YOLOX) (Apache 2.0) as a fallback
- Starter sounds are generated with sox by `make_sounds.sh`. No third-party sound files are included;
  upload your own on the web page (for example from [Pixabay](https://pixabay.com/sound-effects/) or CC0 sounds on freesound.org)

## License

AGPL-3.0, see [LICENSE](LICENSE) (the YOLO26 person detector is AGPL-3.0). Downloaded voices and
the YOLOX model keep their own licenses.
