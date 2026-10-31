"""Philips Hue light show, driven by what the speaker plays.

While the background sound plays, the lamps drift slowly between spooky
colours. The scare sound and the voice take over: the lamps follow the
loudness of the sound (worked out from the file, so nothing is analysed
live), with white flashes on loud parts in the scary mode. When the sound
stops (mode Av, or the lights switched off on the web page) the lamps go
back to how they were.

Uses the bridge's local REST API (v1) on the LAN. The bridge takes about 10
commands a second, so one lamp is updated per 0.1 s tick. Nothing here can
block or break a scare: the speaker only sets fields, and the network work
happens in this module's own thread.

Command line (from the app folder):
  .venv/bin/python -m scare.lights pair [bridge-ip]   press the bridge button first
  .venv/bin/python -m scare.lights list               lamps and their ids
  .venv/bin/python -m scare.lights test               a short show on the lamps
"""
import json
import logging
import random
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

WINDOW = 0.05       # loudness resolution, seconds
TICK = 0.1          # one lamp command per tick
FLASH_GAP = 0.34    # at most ~3 flashes a second per lamp (photosensitivity guideline)

# Colours as (hue 0-65535, saturation 0-254)
RED = (0, 254)
ORANGE = (5500, 254)
AMBER = (8500, 254)
GREEN = (25500, 230)
PURPLE = (48500, 254)
VIOLET = (52000, 220)
WHITE = (8400, 0)

STYLES = {
    "scary": {"ambient": [ORANGE, RED, PURPLE, AMBER], "ambient_bri": (40, 150),
              "voice": RED, "scare": [RED], "flash": True},
    # Small children: soft colours, never flashes
    "kids": {"ambient": [PURPLE, GREEN, ORANGE, VIOLET], "ambient_bri": (90, 170),
             "voice": GREEN, "scare": [PURPLE, GREEN], "flash": False},
}
GENTLE = "kids"  # style for modes without their own: fail towards the gentle one

# The bridge has a self-signed certificate
_SSL = ssl._create_unverified_context()


def bridge_url(bridge):
    return bridge if "://" in bridge else f"https://{bridge}"


def request(url, method="GET", body=None, timeout=2):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
        out = json.load(r)
    if isinstance(out, list) and out and isinstance(out[0], dict) and "error" in out[0]:
        raise RuntimeError(out[0]["error"].get("description", "bridge error"))
    return out


def envelope(path):
    """Loudness of a sound file every WINDOW seconds, 0-1 (1 = its loud parts),
    or None if the file can't be read (e.g. mp3, which sox here can't decode)."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".wav":
            with wave.open(str(path)) as w:
                if w.getsampwidth() != 2:
                    return None
                rate, channels = w.getframerate(), w.getnchannels()
                step = max(1, int(rate * WINDOW))
                levels = []
                while True:  # a second at a time, so long files don't fill the memory
                    chunk = np.frombuffer(w.readframes(step * 20), "<i2")
                    if not chunk.size:
                        break
                    mono = chunk[:chunk.size // channels * channels].reshape(-1, channels).mean(axis=1)
                    for i in range(0, mono.size, step):
                        part = mono[i:i + step]
                        levels.append(float(np.sqrt(np.mean(part * part))))
        else:
            rate = 8000
            raw = subprocess.run(["sox", str(path), "-t", "raw", "-r", str(rate), "-c", "1",
                                  "-b", "16", "-e", "signed", "-"],
                                 capture_output=True, timeout=30, check=True).stdout
            mono = np.frombuffer(raw, "<i2").astype(np.float32)
            step = int(rate * WINDOW)
            levels = [float(np.sqrt(np.mean(mono[i:i + step] ** 2))) for i in range(0, mono.size, step)]
    except (OSError, EOFError, wave.Error, subprocess.SubprocessError, ValueError):
        return None
    if not levels:
        return None
    env = np.array(levels)
    loud = np.percentile(env, 95)
    if loud <= 0:
        return [0.0] * len(levels)
    return np.clip(env / loud, 0, 1).tolist()


class Lights:
    def __init__(self, cfg, send=None):
        self.bridge = str(cfg.get("bridge", "")).strip()
        self.key = str(cfg.get("key", "")).strip()
        self.ids = [str(i) for i in cfg.get("lights", [])]
        self.brightness = min(max(float(cfg.get("brightness", 1.0)), 0.05), 1.0)
        self.delay = cfg.get("delay_ms", 200) / 1000
        self.configured = bool(self.bridge and self.key and self.ids)
        self.active = False     # set by the controller: lights on and mode not Av
        self.error = None
        self._send = send or self._http
        self._music = None      # mode of the background sound, None = not playing
        self._sound = None      # (kind, path, mode, started) of a scare sound or voice
        self._envelopes = {}    # (path, mtime) -> envelope
        self._saved = None      # lamp states from before the show, restored after
        self._next_drift = {}
        self._last_flash = {}
        self._turn = 0

    # --- Called by the speaker (no network, never blocks) ----------------------

    def music(self, mode):
        """The background sound of `mode` is playing, or None when it stopped."""
        self._music = mode

    def playing(self, kind, path=None, mode=None):
        """kind "scare" or "voice" started playing `path`, or None when done."""
        self._sound = (kind, Path(path), mode, time.monotonic()) if kind else None

    @property
    def showing(self):
        return self.active and self.configured and bool(self._sound or self._music)

    # --- The show ----------------------------------------------------------------

    def envelope(self, path):
        try:
            key = (path, path.stat().st_mtime)
        except OSError:
            return None
        if key not in self._envelopes:
            if len(self._envelopes) > 200:
                self._envelopes.clear()
            self._envelopes[key] = envelope(path)
        return self._envelopes[key]

    def _state(self, colour, bri, transition):
        hue, sat = colour
        bri = max(1, min(254, round(bri * self.brightness)))
        return {"on": True, "hue": hue, "sat": sat, "bri": bri, "transitiontime": transition}

    def frame(self, now):
        """The next command: (lamp id, state) or None. One lamp per call, in turn."""
        if not self.showing:
            return None
        lamp = self.ids[self._turn % len(self.ids)]
        self._turn += 1
        if self._sound:
            kind, path, mode, started = self._sound
            style = STYLES.get(mode, STYLES[GENTLE])
            env = self.envelope(path)
            i = int((now - started - self.delay) / WINDOW)
            if env is None:
                level = random.uniform(0.3, 1.0)  # unknown loudness: flicker
            else:
                level = env[i] if 0 <= i < len(env) else 0.0
            if kind == "voice":
                return lamp, self._state(style["voice"], 15 + level * 239, 1)
            if style["flash"] and level > 0.6 and now - self._last_flash.get(lamp, -1e9) > FLASH_GAP:
                self._last_flash[lamp] = now
                return lamp, self._state(WHITE, 254, 0)
            return lamp, self._state(random.choice(style["scare"]), 5 + level * 220, 1)
        # Background sound: a new colour every few seconds, fading slowly
        if now < self._next_drift.get(lamp, 0):
            return None
        self._next_drift[lamp] = now + random.uniform(2.0, 4.0)
        style = STYLES.get(self._music, STYLES[GENTLE])
        lo, hi = style["ambient_bri"]
        return lamp, self._state(random.choice(style["ambient"]), random.uniform(lo, hi), 20)

    # --- Bridge ----------------------------------------------------------------

    def _http(self, method, path, body=None):
        return request(f"{bridge_url(self.bridge)}/api/{self.key}/{path}", method, body)

    def _save(self):
        saved = {}
        for lamp in self.ids:
            s = self._send("GET", f"lights/{lamp}")["state"]
            keep = {"on": s.get("on", True)}
            if "bri" in s:
                keep["bri"] = s["bri"]
            mode = s.get("colormode")
            if mode == "hs":
                keep.update(hue=s["hue"], sat=s["sat"])
            elif mode in ("xy", "ct") and mode in s:
                keep[mode] = s[mode]
            saved[lamp] = keep
        return saved

    def _restore(self):
        for lamp, state in self._saved.items():
            self._send("PUT", f"lights/{lamp}/state", {**state, "transitiontime": 10})

    def step(self, now):
        """One tick of the show thread: save, drive or restore the lamps."""
        if self.showing:
            if self._saved is None:
                self._saved = self._save()
                log.info("Light show on")
            cmd = self.frame(now)
            if cmd:
                lamp, state = cmd
                self._send("PUT", f"lights/{lamp}/state", state)
        elif self._saved is not None:
            self._restore()
            self._saved = None
            log.info("Light show off, lamps restored")

    def start(self):
        if not self.configured:
            return

        def loop():
            while True:
                t = time.monotonic()
                try:
                    self.step(t)
                    if self.error:
                        log.info("Hue bridge reachable again")
                    self.error = None
                except Exception as e:
                    if self.error is None:
                        log.warning("Hue lights failed: %s", e)
                    self.error = str(e) or type(e).__name__
                    time.sleep(5)
                time.sleep(max(0.0, TICK - (time.monotonic() - t)))
        threading.Thread(target=loop, daemon=True).start()


# --- Command line ----------------------------------------------------------------

def _config():
    import tomllib
    root = Path(__file__).resolve().parent.parent
    cfg = {}
    for name in ("config.toml", "config.local.toml"):
        p = root / name
        if p.exists():
            with open(p, "rb") as f:
                cfg.update(tomllib.load(f).get("lights", {}))
    return cfg


def _pair(bridge=None):
    if not bridge:
        found = request("https://discovery.meethue.com/", timeout=10)
        if not found:
            sys.exit("No Hue Bridge found. Give its IP address: python -m scare.lights pair 192.168.1.x")
        bridge = found[0]["internalipaddress"]
        print(f"Found bridge at {bridge}")
    print("Press the round button on the Hue Bridge now (waiting 60 s)...")
    for _ in range(60):
        try:
            out = request(f"{bridge_url(bridge)}/api", "POST", {"devicetype": "halloween#pi"})
            key = out[0]["success"]["username"]
            break
        except RuntimeError:  # "link button not pressed"
            time.sleep(1)
    else:
        sys.exit("Timed out: the button wasn't pressed.")
    print("\nPaired. Put this in config.local.toml (then pick lamps with: python -m scare.lights list):\n")
    print(f'[lights]\nbridge = "{bridge}"\nkey = "{key}"\nlights = []')


def main(argv):
    logging.basicConfig(level=logging.INFO)
    cmd = argv[0] if argv else ""
    if cmd == "pair":
        return _pair(argv[1] if len(argv) > 1 else None)
    cfg = _config()
    lights = Lights(cfg)
    if not (lights.bridge and lights.key):
        sys.exit("Not paired yet: run python -m scare.lights pair")
    if cmd == "list":
        for lamp, info in lights._send("GET", "lights").items():
            mark = "*" if lamp in lights.ids else " "
            reach = "" if info["state"].get("reachable", True) else " (unreachable)"
            print(f"{mark} {lamp:>3}  {info['name']}  [{info.get('type', '')}]{reach}")
        print("\n* = used by the show (lights = [...] in config.local.toml)")
    elif cmd == "test":
        if not lights.ids:
            sys.exit("No lamps chosen: set lights = [...] in config.local.toml")
        lights.active = True
        lights.start()
        print("Background show (8 s)...")
        lights.music("scary")
        time.sleep(8)
        root = Path(__file__).resolve().parent.parent
        sounds = sorted((root / "modes" / "scary" / "scare").glob("*.wav"))
        if sounds:
            print(f"Scare light for {sounds[0].name} (no sound)...")
            lights.playing("scare", sounds[0], "scary")
            time.sleep(4)
        lights.playing(None)
        lights.music(None)
        time.sleep(1.5)
        print("Restored")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
