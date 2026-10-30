import json
import unittest

from scare.main import Controller

from .helpers import AppTestCase


class DefaultsTest(AppTestCase):
    def test_starts_with_config_defaults(self):
        s = self.ctl.settings
        self.assertEqual(s["mode"], "scary")
        self.assertEqual(s["pixel_threshold"], 25)
        self.assertEqual(s["cm_per_px"], 0.0)
        self.assertEqual(self.ctl.speaker.mac, "")

    def test_values_are_clamped_and_converted(self):
        self.ctl.update_settings({"volume": 9, "pixel_threshold": "1", "kid_height_cm": 500.7})
        s = self.ctl.settings
        self.assertEqual(s["volume"], 1.5)
        self.assertEqual(s["pixel_threshold"], 5)
        self.assertEqual(s["kid_height_cm"], 200)
        self.assertEqual(self.ctl.speaker.volume, 1.5)

    def test_changes_reach_the_motion_detector(self):
        self.ctl.update_settings({"pixel_threshold": 40, "area_percent": 3})
        self.assertEqual(self.ctl.detector.pixel_threshold, 40)
        self.assertEqual(self.ctl.detector.area_percent, 3.0)

    def test_unknown_setting_is_rejected(self):
        with self.assertRaises(ValueError):
            self.ctl.update_settings({"rm_rf": True})

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.ctl.update_settings({"mode": "gore"})
        self.assertEqual(self.ctl.settings["mode"], "scary")

    def test_changes_are_saved_and_loaded_again(self):
        self.ctl.update_settings({"mode": "kids", "cooldown_seconds": 45})
        saved = json.loads((self.root / "settings.json").read_text())
        self.assertEqual(saved["cooldown_seconds"], 45)
        again = Controller(self.root)
        self.assertEqual(again.settings["mode"], "kids")
        self.assertEqual(again.settings["cooldown_seconds"], 45)

    def test_auto_and_off_play_kid_mode_background(self):
        for mode in ("auto", "off"):
            self.ctl.update_settings({"mode": mode})
            self.assertEqual(self.ctl.speaker.mode, "kids")
        self.assertTrue(self.ctl.speaker.silent)
        self.ctl.update_settings({"mode": "scary"})
        self.assertEqual(self.ctl.speaker.mode, "scary")
        self.assertFalse(self.ctl.speaker.silent)


class SavedSettingsTest(AppTestCase):
    settings = json.dumps({"volume": 0.3, "mode": "deleted-mode", "junk": 1})

    def test_saved_values_override_config(self):
        self.assertEqual(self.ctl.settings["volume"], 0.3)

    def test_unknown_keys_are_ignored(self):
        self.assertNotIn("junk", self.ctl.settings)

    def test_saved_mode_that_no_longer_exists_falls_back(self):
        self.assertEqual(self.ctl.settings["mode"], "scary")


class LocalConfigTest(AppTestCase):
    local = '[speaker]\nmac = "AA:BB:CC:DD:EE:FF"\n\n[web]\nport = 9000\n'

    def test_local_values_override_config(self):
        self.assertEqual(self.ctl.speaker.mac, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(self.ctl.cfg["web"]["port"], 9000)

    def test_other_values_in_the_same_section_are_kept(self):
        self.assertEqual(self.ctl.cfg["speaker"]["volume"], 0.9)
        self.assertEqual(self.ctl.cfg["web"]["host"], "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
