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
AUTO = "auto"
OFF = "off"  # no scares and no background sound

# Settings the web page may change: name -> (type, min, max)
TUNABLE = {
    "armed": (bool, None, None),
    "mode": (str, None, None),
    "pixel_threshold": (int, 5, 100),
    "area_percent": (float, 0.1, 50.0),
    "cooldown_seconds": (int, 0, 600),
    "volume": (float, 0.0, 1.5),
    "ambient_volume": (float, 0.0, 1.0),
    "kid_height_cm": (int, 80, 200),
    "cm_per_px": (float, 0.0, 10.0),  # Auto mode calibration, 0 = not calibrated
}


class Controller:
    def __init__(self):
        self.root = ROOT
        with open(ROOT / "config.toml", "rb") as f:
            self.cfg = tomllib.load(f)
        m = self.cfg["motion"]
        self.auto = self.cfg["auto"]
        self.settings = {
            "armed": m["start_armed"],
            "mode": self.cfg["modes"].get("default", "scary"),
            "pixel_threshold": m["pixel_threshold"],
            "area_percent": m["area_percent"],
            "cooldown_seconds": m["cooldown_seconds"],
            "volume": self.cfg["speaker"]["volume"],
            "ambient_volume": self.cfg["speaker"]["ambient_volume"],
            "kid_height_cm": self.auto["kid_height_cm"],
            "cm_per_px": 0.0,
        }
        if SETTINGS_FILE.exists():
            saved = json.loads(SETTINGS_FILE.read_text())
            self.settings.update({k: v for k, v in saved.items() if k in TUNABLE})

        self.speaker = Speaker(ROOT, self.cfg)
        if not self._valid_mode(self.settings["mode"]):
            self.settings["mode"] = self.speaker.mode
        self.detector = MotionDetector(m["pixel_threshold"], m["area_percent"], m["frames_required"])
        self._apply_settings()

        self.camera = None
        self.camera_error = None
        self.last_scare = 0.0
        self.last_scare_at = None
        self.last_scare_reason = None
        self.scare_count = 0

        self._person_detector = None
        self._person_lock = threading.Lock()
        self.people = []
        self.people_at = 0.0
        self.motion_at = 0.0
        self.last_auto = None

    def _valid_mode(self, mode):
        return mode in (AUTO, OFF) or mode in self.speaker.modes

    def _apply_settings(self):
        self.detector.pixel_threshold = self.settings["pixel_threshold"]
        self.detector.area_percent = self.settings["area_percent"]
        self.speaker.volume = self.settings["volume"]
        self.speaker.ambient_volume = self.settings["ambient_volume"]
        # In Auto the background sound comes from the kid mode, the safe choice
        mode = self.settings["mode"]
        self.speaker.set_mode(self.auto["kid_mode"] if mode in (AUTO, OFF) else mode)
        self.speaker.set_silent(mode == OFF)

    def update_settings(self, changes):
        for name, value in changes.items():
            if name not in TUNABLE:
                raise ValueError(f"unknown setting {name!r}")
            kind, lo, hi = TUNABLE[name]
            value = kind(value)
            if name == "mode" and not self._valid_mode(value):
                raise ValueError(f"unknown mode {value!r}")
            if lo is not None:
                value = min(max(value, lo), hi)
            self.settings[name] = value
        self._apply_settings()
        SETTINGS_FILE.write_text(json.dumps(self.settings, indent=2))

    # --- Scares --------------------------------------------------------------

    def pick_mode(self):
        """The mode for the next scare: the chosen one, or decided by height in Auto."""
        if self.settings["mode"] != AUTO:
            return self.settings["mode"]
        if self.camera is None:
            return self.auto["kid_mode"]
        try:
            return self.choose_auto_mode()
        except Exception:
            log.exception("Auto mode failed, using kid mode")
            return self.auto["kid_mode"]

    def scare(self, text=None, reason="motion", mode=None):
        if self.settings["mode"] == OFF or self.speaker.busy:
            return False
        mode = mode or self.pick_mode()
        if not self.speaker.scare(text, mode):
            return False
        self.last_scare = time.monotonic()
        self.last_scare_at = time.strftime("%H:%M:%S")
        self.last_scare_reason = f"{reason}, {self.speaker.modes[mode]['label']}"
        self.scare_count += 1
        log.info("Scare #%d (%s)", self.scare_count, self.last_scare_reason)
        return True

    # --- Auto mode: person detection -------------------------------------------

    def person_detector(self):
        """Loaded on first use so it costs no memory unless Auto mode is used."""
        with self._person_lock:
            if self._person_detector is None:
                from .people import PersonDetector
                log.info("Loading person detector")
                self._person_detector = PersonDetector(ROOT / self.auto["model"])
        return self._person_detector

    def look_for_people(self):
        """Detects people in a fresh frame and estimates their height."""
        rgb = self.camera.rgb_frame()
        h, w = rgb.shape[:2]
        scale = self.settings["cm_per_px"]
        people = []
        for x1, y1, x2, y2, score in self.person_detector().detect(rgb):
            px = y2 - y1
            people.append({
                "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                "px": px,
                "height_cm": round(px * scale) if scale else None,
                # Head or feet outside the picture: the height is a guess
                "cut": y1 <= 2 or y2 >= h - 2,
            })
        self.people, self.people_at = people, time.monotonic()
        return people

    def choose_auto_mode(self):
        """Looks at the visitors for a moment; kid mode if anyone is short,
        or when unsure."""
        heights = []
        end = time.monotonic() + self.auto["look_seconds"]
        while time.monotonic() < end:  # several frames: people move and hide behind each other
            heights += [p["height_cm"] for p in self.look_for_people()]
        limit = self.settings["kid_height_cm"]
        kid, adult = self.auto["kid_mode"], self.auto["adult_mode"]
        if not self.settings["cm_per_px"]:
            mode, why = kid, "not calibrated"
        elif not heights:
            mode, why = kid, "nobody seen clearly"
        else:
            shortest = min(heights)
            mode = kid if shortest < limit else adult
            why = f"shortest {shortest} cm, {len(heights)} sightings"
        self.last_auto = {"at": time.strftime("%H:%M:%S"), "mode": mode, "why": why}
        log.info("Auto: %s (%s)", self.speaker.modes[mode]["label"], why)
        return mode

    def calibrate(self, height_cm):
        """One person of known height stands where visitors stand."""
        if self.camera is None:
            raise ValueError("No camera")
        log.info("Calibrating with %d cm...", height_cm)
        samples, cut = [], 0
        end = time.monotonic() + 3
        while time.monotonic() < end:
            people = self.look_for_people()
            whole = [p["px"] for p in people if not p["cut"]]
            cut += len(people) - len(whole)
            if whole:
                samples.append(max(whole))
        if not samples:
            why = ("Your head or feet are outside the picture. Step back, or tilt the camera."
                   if cut else "Nobody seen. Stand in front of the camera, head to feet in view.")
            log.info("Calibration failed: %s", why)
            raise ValueError(why)
        px = sorted(samples)[len(samples) // 2]
        self.update_settings({"cm_per_px": height_cm / px})
        log.info("Calibrated: %d cm = %d px", height_cm, px)
        return {"px": round(px), "height_cm": height_cm}

    def people_watch_loop(self):
        """In Auto mode, keeps the people boxes on the web page fresh while
        something moves. Idle (no CPU) otherwise."""
        while True:
            time.sleep(0.5)
            if self.settings["mode"] != AUTO or time.monotonic() - self.motion_at > 2:
                continue
            try:
                self.look_for_people()
            except Exception:
                log.exception("Person detection failed")
                time.sleep(5)

    # --- Status and main loop ------------------------------------------------

    def status(self):
        cooldown_left = self.settings["cooldown_seconds"] - (time.monotonic() - self.last_scare)
        recent_people = self.people if time.monotonic() - self.people_at < 3 else []
        return {
            **self.settings,
            "modes": {**{k: v["label"] for k, v in self.speaker.modes.items()}, AUTO: "Auto", OFF: "Av"},
            "content_modes": {k: v["label"] for k, v in self.speaker.modes.items()},
            "base_mode": self.speaker.mode,
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
            "sounds": {m: {"scare": [p.name for p in self.speaker.scare_sounds(m)],
                           "ambient": [p.name for p in self.speaker.ambient_sounds(m)]}
                       for m in self.speaker.modes},
            "messages_pending": self.speaker.pending_messages(),
            "people": recent_people,
            "last_auto": self.last_auto,
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
            if self.detector.level >= self.settings["area_percent"] / 2:
                self.motion_at = time.monotonic()
            if not (triggered and self.settings["armed"]) or self.settings["mode"] == OFF:
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
        threading.Thread(target=ctl.people_watch_loop, daemon=True).start()
    web = ctl.cfg["web"]
    log.info("Web page on http://%s:%d", web["host"], web["port"])
    create_app(ctl).run(host=web["host"], port=web["port"], threaded=True)


if __name__ == "__main__":
    main()
