import shutil
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from scare.lights import WHITE, Lights, envelope

from .helpers import AppTestCase

CFG = {"bridge": "10.0.0.2", "key": "k", "lights": ["1", "2"], "delay_ms": 0}


class FakeBridge:
    """Records commands; lamps start warm white at brightness 200."""

    def __init__(self):
        self.puts = []
        self.fail = False

    def __call__(self, method, path, body=None):
        if self.fail:
            raise OSError("bridge unreachable")
        if method == "GET":
            return {"state": {"on": True, "bri": 200, "colormode": "ct", "ct": 366, "hue": 1, "sat": 2}}
        self.puts.append((path, body))
        return [{"success": {}}]


def write_wav(path, seconds_loud_quiet):
    """A 44.1 kHz mono wav: (seconds, amplitude) parts."""
    rate = 44100
    parts = [np.full(int(rate * s), a, np.int16) * np.sign(np.sin(np.arange(int(rate * s))))
             for s, a in seconds_loud_quiet]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.concatenate(parts).astype("<i2").tobytes())


class LightsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.bridge = FakeBridge()
        self.lights = Lights(CFG, send=self.bridge)
        self.lights.active = True
        self.wav = self.dir / "s.wav"
        write_wav(self.wav, [(0.5, 20000), (0.5, 0)])  # loud, then silent

    def test_not_configured_without_bridge_key_or_lamps(self):
        for missing in ("bridge", "key", "lights"):
            lights = Lights({**CFG, missing: [] if missing == "lights" else ""}, send=self.bridge)
            lights.active = True
            lights.music("scary")
            self.assertFalse(lights.configured)
            lights.step(0)
            self.assertEqual(self.bridge.puts, [])

    def test_envelope_follows_loudness(self):
        env = envelope(self.wav)
        self.assertEqual(len(env), 20)
        self.assertGreater(min(env[:9]), 0.9)
        self.assertEqual(max(env[11:]), 0.0)

    def test_unreadable_file_has_no_envelope(self):
        bad = self.dir / "x.mp3"
        bad.write_bytes(b"not audio")
        self.assertIsNone(envelope(bad))
        self.assertIsNone(envelope(self.dir / "missing.wav"))

    def test_nothing_happens_while_nothing_plays(self):
        self.lights.step(0)
        self.assertEqual(self.bridge.puts, [])

    def test_background_drifts_slowly_on_both_lamps(self):
        self.lights.music("scary")
        for t in range(4):
            self.lights.step(t * 0.1)
        lamps = [p for p, _ in self.bridge.puts]
        self.assertEqual(lamps, ["lights/1/state", "lights/2/state"])  # not again within 2 s
        self.assertTrue(all(b["transitiontime"] >= 10 for _, b in self.bridge.puts))

    def test_voice_brightness_follows_the_sound(self):
        self.lights.playing("voice", self.wav, "scary")
        start = self.lights._sound[3]
        lamp, loud = self.lights.frame(start + 0.2)
        lamp, quiet = self.lights.frame(start + 0.8)
        self.assertGreater(loud["bri"], 200)
        self.assertLess(quiet["bri"], 30)
        self.assertEqual(loud["hue"], 0)  # red

    def test_scary_mode_flashes_at_most_three_times_a_second(self):
        self.lights.playing("scare", self.wav, "scary")
        start = self.lights._sound[3]
        flashes = sum(self.lights.frame(start + i * 0.05)[1]["sat"] == WHITE[1] for i in range(10))
        # 0.5 s of loud sound, two lamps: at most 2 flashes per lamp
        self.assertGreaterEqual(flashes, 2)
        self.assertLessEqual(flashes, 4)

    def test_kid_mode_and_unknown_modes_never_flash(self):
        for mode in ("kids", "something-new"):
            self.lights.playing("scare", self.wav, mode)
            start = self.lights._sound[3]
            for i in range(10):
                self.assertNotEqual(self.lights.frame(start + i * 0.05)[1]["sat"], WHITE[1])

    def test_lamps_are_restored_when_the_sound_stops(self):
        self.lights.music("kids")
        self.lights.step(0)
        self.lights.music(None)
        self.lights.step(0.1)
        path, body = self.bridge.puts[-1]
        self.assertEqual(body["ct"], 366)
        self.assertEqual(body["bri"], 200)
        self.assertNotIn("hue", body)  # only the keys of the lamp's colour mode

    def test_restored_when_switched_off(self):
        self.lights.music("scary")
        self.lights.step(0)
        self.lights.active = False
        self.lights.step(0.1)
        self.lights.step(0.2)
        restores = [b for _, b in self.bridge.puts if "ct" in b]
        self.assertEqual(len(restores), 2)

    def test_brightness_setting_scales_effects(self):
        lights = Lights({**CFG, "brightness": 0.5}, send=self.bridge)
        lights.active = True
        lights.playing("voice", self.wav, "scary")
        _, state = lights.frame(lights._sound[3] + 0.2)
        self.assertLessEqual(state["bri"], 127)


class LightsInAppTest(AppTestCase):
    local = '[lights]\nenabled = true\nbridge = "10.0.0.2"\nkey = "k"\nlights = ["1"]\n'

    def test_lights_follow_the_mode_and_setting(self):
        lights = self.ctl.lights
        self.assertTrue(lights.configured)
        self.assertTrue(lights.active)
        self.ctl.update_settings({"mode": "off"})
        self.assertFalse(lights.active)
        self.ctl.update_settings({"mode": "kids", "lights": False})
        self.assertFalse(lights.active)
        self.assertFalse(self.ctl.status()["lights"])

    def test_scare_tells_the_lights_what_plays(self):
        sp = self.ctl.speaker
        sound = sp.sound_dir("scare", "scary") / "a.wav"
        write_wav(sound, [(0.1, 1000)])
        seen = []
        orig = self.ctl.lights.playing
        self.ctl.lights.playing = lambda kind, path=None, mode=None: (seen.append(kind), orig(kind, path, mode))
        sp.play = lambda path, volume=None: None
        sp._ambient_on = True
        sp.speech_file = lambda text, mode=None: sound
        sp._lock.acquire()
        sp._run("hello", "scary")
        self.assertEqual(seen, ["scare", "voice", None])
        self.assertIsNone(self.ctl.lights._sound)

    def test_add_on_is_off_unless_enabled(self):
        self.assertTrue(self.ctl.status()["lights_enabled"])
        from scare.main import Controller
        for local in (None, self.local.replace("enabled = true", "enabled = false")):
            (self.root / "config.local.toml").unlink(missing_ok=True)
            if local:
                (self.root / "config.local.toml").write_text(local)
            ctl = Controller(self.root)
            self.assertIsNone(ctl.lights)
            self.assertIsNone(ctl.speaker.lights)
            ctl.update_settings({"mode": "kids", "lights": True})  # must not fail without the add-on
            s = ctl.status()
            self.assertFalse(s["lights_enabled"] or s["lights_configured"])


if __name__ == "__main__":
    unittest.main()
