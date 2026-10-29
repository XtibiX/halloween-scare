import json
import logging
import threading
import time
import tomllib
from pathlib import Path

from .audio import Speaker
from .motion import MotionDetector
from .web import create_app

log = logging.getLogger("halloween")

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT / "settings.json"

# Settings the web page may change: name -> (type, min, max)
TUNABLE = {
    "armed": (bool, None, None),
    "mode": (str, None, None),
    "pixel_threshold": (int, 5, 100),
    "area_percent": (float, 0.1, 50.0),
    "cooldown_seconds": (int, 0, 600),
    "volume": (float, 0.0, 1.5),
    "ambient_volume": (float, 0.0, 1.0),
}


class Controller:
    def __init__(self):
        self.root = ROOT
        with open(ROOT / "config.toml", "rb") as f:
            self.cfg = tomllib.load(f)
        m = self.cfg["motion"]
        self.settings = {
            "armed": m["start_armed"],
            "mode": self.cfg["modes"].get("default", "scary"),
            "pixel_threshold": m["pixel_threshold"],
            "area_percent": m["area_percent"],
            "cooldown_seconds": m["cooldown_seconds"],
            "volume": self.cfg["speaker"]["volume"],
            "ambient_volume": self.cfg["speaker"]["ambient_volume"],
        }
        if SETTINGS_FILE.exists():
            saved = json.loads(SETTINGS_FILE.read_text())
            self.settings.update({k: v for k, v in saved.items() if k in TUNABLE})

        self.speaker = Speaker(ROOT, self.cfg)
        if self.settings["mode"] not in self.speaker.modes:
            self.settings["mode"] = self.speaker.mode
        self.detector = MotionDetector(m["pixel_threshold"], m["area_percent"], m["frames_required"])
        self._apply_settings()

        self.camera = None
        self.camera_error = None
        self.last_scare = 0.0
        self.last_scare_at = None
        self.last_scare_reason = None
        self.scare_count = 0

    def _apply_settings(self):
        self.detector.pixel_threshold = self.settings["pixel_threshold"]
        self.detector.area_percent = self.settings["area_percent"]
        self.speaker.volume = self.settings["volume"]
        self.speaker.ambient_volume = self.settings["ambient_volume"]
        self.speaker.set_mode(self.settings["mode"])

    def update_settings(self, changes):
        for name, value in changes.items():
            if name not in TUNABLE:
                raise ValueError(f"unknown setting {name!r}")
            kind, lo, hi = TUNABLE[name]
            value = kind(value)
            if name == "mode" and value not in self.speaker.modes:
                raise ValueError(f"unknown mode {value!r}")
            if lo is not None:
                value = min(max(value, lo), hi)
            self.settings[name] = value
        self._apply_settings()
        SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2))

    def scare(self, text=None, reason="motion"):
        if not self.speaker.scare(text):
            return False
        self.last_scare = time.monotonic()
        self.last_scare_at = time.strftime("%H:%M:%S")
        self.last_scare_reason = reason
        self.scare_count += 1
        log.info("Scare #%d (%s)", self.scare_count, reason)
        return True

    def status(self):
        cooldown_left = self.settings["cooldown_seconds"] - (time.monotonic() - self.last_scare)
        return {
            **self.settings,
            "modes": {k: v["label"] for k, v in self.speaker.modes.items()},
            "camera": self.camera is not None,
            "camera_error": self.camera_error,
            "motion_level": round(self.detector.level, 2),
            "speaker_connected": self.speaker.connected(),
            "speaker_mac": self.speaker.mac,
            "playing": self.speaker.busy,
            "cooldown_left": max(0, round(cooldown_left)) if self.scare_count else 0,
            "last_scare_at": self.last_scare_at,
            "last_scare_reason": self.last_scare_reason,
            "scare_count": self.scare_count,
            "scare_sounds": [p.name for p in self.speaker.scare_sounds()],
            "ambient_sounds": [p.name for p in self.speaker.ambient_sounds()],
            "messages_pending": self.speaker.pending_messages(),
        }

    def start_camera(self):
        c = self.cfg["camera"]
        try:
            from .camera import Camera
            self.camera = Camera(c["width"], c["height"], c["fps"])
            self.camera.start()
        except Exception as e:
            # Keep running without a camera so the speaker and web page still work
            self.camera = None
            self.camera_error = str(e) or type(e).__name__
            log.error("Camera unavailable: %s", self.camera_error)

    def detect_loop(self):
        while True:
            try:
                triggered = self.detector.update(self.camera.grey_frame())
            except Exception:
                log.exception("Camera frame failed")
                time.sleep(1)
                continue
            if not (triggered and self.settings["armed"]):
                continue
            if time.monotonic() - self.last_scare < self.settings["cooldown_seconds"]:
                continue
            log.info("Motion detected (%.1f%% of picture)", self.detector.level)
            self.scare(reason="motion")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no log line per request
    ctl = Controller()
    ctl.speaker.prerender()
    ctl.speaker.start_ambient()
    ctl.start_camera()
    if ctl.camera:
        threading.Thread(target=ctl.detect_loop, daemon=True).start()
    web = ctl.cfg["web"]
    log.info("Web page on http://%s:%d", web["host"], web["port"])
    create_app(ctl).run(host=web["host"], port=web["port"], threaded=True)


if __name__ == "__main__":
    main()
