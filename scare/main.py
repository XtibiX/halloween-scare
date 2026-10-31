import json
import logging
import threading
import time
import tomllib
from pathlib import Path

from .audio import Speaker
from .lights import Lights
from .motion import MotionDetector
from .web import create_app

log = logging.getLogger("halloween")

ROOT = Path(__file__).resolve().parent.parent
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
    "lights": (bool, None, None),
}
# Two calibration spots must be this far apart (feet, fraction of the picture height)
MIN_SPOT_GAP = 0.08


class Controller:
    calibrate_seconds = 3

    def __init__(self, root=ROOT):
        self.root = root
        self.settings_file = root / "settings.json"
        with open(root / "config.toml", "rb") as f:
            self.cfg = tomllib.load(f)
        # Machine-specific values (speaker MAC) live in config.local.toml, not in git
        local = root / "config.local.toml"
        if local.exists():
            with open(local, "rb") as f:
                for section, values in tomllib.load(f).items():
                    self.cfg.setdefault(section, {}).update(values)
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
            "lights": self.cfg.get("lights", {}).get("start_on", True),
            # Auto mode calibration, set by calibrate(): up to two spots, each
            # [feet, size]: where the feet are (fraction of the picture height,
            # 0 = top) and how tall 1 cm looks there (same unit). Empty = not calibrated
            "calibration": [],
        }
        if self.settings_file.exists():
            saved = json.loads(self.settings_file.read_text())
            self.settings.update({k: v for k, v in saved.items() if k in self.settings})

        self.speaker = Speaker(root, self.cfg)
        self.lights = Lights(self.cfg.get("lights", {}))
        self.speaker.lights = self.lights
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
        self.lights.active = self.settings["lights"] and mode != OFF

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
        self.save_settings()

    def save_settings(self):
        self.settings_file.write_text(json.dumps(self.settings, indent=2))

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
                path = self.root / self.auto["model"]
                if not path.exists():
                    path = self.root / self.auto["fallback_model"]
                log.info("Loading person detector %s", path.name)
                self._person_detector = PersonDetector(path)
        return self._person_detector

    def size_of_cm(self, feet):
        """How tall 1 cm looks (fraction of the picture height) for someone whose
        feet are at this height in the picture, or None if unknown.

        With the camera above the ground looking down, people further away
        have their feet higher up in the picture and look smaller. For a flat
        ground the size grows in a straight line with the feet position, so two
        calibration spots give it for any spot. One spot: the same everywhere."""
        spots = self.settings["calibration"]
        if not spots:
            return None
        if len(spots) == 1:
            return spots[0][1]
        (f1, s1), (f2, s2) = spots
        size = s1 + (feet - f1) * (s2 - s1) / (f2 - f1)
        # Far away, near the horizon: too small to measure
        return size if size > 0.3 * min(s1, s2) else None

    def look_for_people(self):
        """Detects people in a fresh frame and estimates their height."""
        rgb = self.camera.rgb_frame()
        h, w = rgb.shape[:2]
        people = []
        for x1, y1, x2, y2, score in self.person_detector().detect(rgb):
            feet_cut = y2 >= h - 2
            # Feet outside the picture: no idea how far away, so no height
            size = None if feet_cut else self.size_of_cm(y2 / h)
            people.append({
                "box": [x1 / w, y1 / h, x2 / w, y2 / h],
                "px": y2 - y1,
                "height_cm": round((y2 - y1) / h / size) if size else None,
                # Head outside the picture: the height is too short
                "cut": y1 <= 2 or feet_cut,
            })
        self.people, self.people_at = people, time.monotonic()
        return people

    def choose_auto_mode(self):
        """Looks at the visitors for a moment; kid mode if anyone is short,
        or when unsure."""
        heights = []
        end = time.monotonic() + self.auto["look_seconds"]
        while time.monotonic() < end:  # several frames: people move and hide behind each other
            heights += [p["height_cm"] for p in self.look_for_people() if p["height_cm"] is not None]
        limit = self.settings["kid_height_cm"]
        kid, adult = self.auto["kid_mode"], self.auto["adult_mode"]
        if not self.settings["calibration"]:
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

    def calibrate(self, height_cm, add=False):
        """One person of known height stands where visitors stand. add=False
        starts over with this spot; add=True keeps the previous spot as well,
        so heights are right at any distance (stand closer or further away)."""
        if self.camera is None:
            raise ValueError("No camera")
        if not 100 <= height_cm <= 220:
            raise ValueError("Height must be 100-220 cm")
        log.info("Calibrating with %d cm%s...", height_cm, " (second spot)" if add else "")
        samples, cut = [], 0
        end = time.monotonic() + self.calibrate_seconds
        while time.monotonic() < end:
            people = self.look_for_people()
            whole = [p for p in people if not p["cut"]]
            cut += len(people) - len(whole)
            if whole:
                samples.append(max(whole, key=lambda p: p["px"]))
        if not samples:
            self._calibration_failed(
                "Your head or feet are outside the picture. Step back, or tilt the camera."
                if cut else "Nobody seen. Stand in front of the camera, head to feet in view.")
        p = sorted(samples, key=lambda p: p["px"])[len(samples) // 2]
        x1, y1, x2, y2 = p["box"]
        spot = [round(y2, 4), (y2 - y1) / height_cm]
        spots = [spot]
        if add and self.settings["calibration"]:
            far = max(self.settings["calibration"], key=lambda s: abs(s[0] - spot[0]))
            if abs(far[0] - spot[0]) < MIN_SPOT_GAP:
                self._calibration_failed("Too close to the first spot. Stand clearly closer or further away.")
            # Closer to the camera (feet lower in the picture) must look bigger
            closer, further = sorted([far, spot], key=lambda s: -s[0])
            if closer[1] <= further[1]:
                self._calibration_failed("The two spots don't fit together. Start over with Calibrate.")
            spots = sorted([far, spot])
        self.settings["calibration"] = spots
        self.save_settings()
        log.info("Calibrated: %d cm = %d px, feet at %d%% (%d spot%s)", height_cm, p["px"],
                 spot[0] * 100, len(spots), "s" if len(spots) > 1 else "")
        return {"px": round(p["px"]), "height_cm": height_cm, "spots": len(spots)}

    def _calibration_failed(self, why):
        log.info("Calibration failed: %s", why)
        raise ValueError(why)

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
            "lights_configured": self.lights.configured,
            "lights_error": self.lights.error,
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
            self.camera = Camera(c["width"], c["height"], c["fps"], c.get("tuning", ""),
                                 c.get("ev", 0.0), c.get("max_exposure_ms", 0), c.get("max_gain", 0))
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
    ctl.lights.start()
    ctl.start_camera()
    if ctl.camera:
        threading.Thread(target=ctl.detect_loop, daemon=True).start()
        threading.Thread(target=ctl.people_watch_loop, daemon=True).start()
    web = ctl.cfg["web"]
    log.info("Web page on http://%s:%d", web["host"], web["port"])
    create_app(ctl).run(host=web["host"], port=web["port"], threaded=True)


if __name__ == "__main__":
    main()
