"""Auto mode must always fall back to the kid mode when in doubt."""
import unittest

from .helpers import AppTestCase, FakeCamera, FakeDetector


class AutoModeTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.ctl.auto["look_seconds"] = 0.02
        self.ctl.update_settings({"mode": "auto", "cm_per_px": 0.5, "kid_height_cm": 130})
        self.ctl.camera = FakeCamera()

    def see(self, *heights_px, top=40):
        self.ctl._person_detector = FakeDetector(list(heights_px), top)

    def test_adults_only_get_the_scary_mode(self):
        self.see(340, 360)  # 170 and 180 cm
        self.assertEqual(self.ctl.pick_mode(), "scary")

    def test_one_child_among_adults_gets_kid_mode(self):
        self.see(360, 200)  # 180 and 100 cm
        self.assertEqual(self.ctl.pick_mode(), "kids")
        self.assertIn("100 cm", self.ctl.last_auto["why"])

    def test_exactly_at_the_limit_counts_as_adult(self):
        self.see(260)  # 130 cm
        self.assertEqual(self.ctl.pick_mode(), "scary")

    def test_nobody_seen_gets_kid_mode(self):
        self.see()
        self.assertEqual(self.ctl.pick_mode(), "kids")
        self.assertEqual(self.ctl.last_auto["why"], "nobody seen clearly")

    def test_not_calibrated_gets_kid_mode(self):
        self.ctl.update_settings({"cm_per_px": 0})
        self.see(360)
        self.assertEqual(self.ctl.pick_mode(), "kids")
        self.assertEqual(self.ctl.last_auto["why"], "not calibrated")

    def test_no_camera_gets_kid_mode(self):
        self.ctl.camera = None
        self.assertEqual(self.ctl.pick_mode(), "kids")

    def test_detector_crash_gets_kid_mode(self):
        class Broken:
            def detect(self, rgb):
                raise RuntimeError("onnx exploded")
        self.ctl._person_detector = Broken()
        with self.assertLogs("halloween", "ERROR"):
            self.assertEqual(self.ctl.pick_mode(), "kids")

    def test_chosen_mode_is_used_as_is_outside_auto(self):
        self.see(200)
        self.ctl.update_settings({"mode": "scary"})
        self.assertEqual(self.ctl.pick_mode(), "scary")

    def test_people_boxes_and_heights(self):
        self.see(360)
        (p,) = self.ctl.look_for_people()
        self.assertEqual(p["height_cm"], 180)
        self.assertFalse(p["cut"])
        self.assertAlmostEqual(p["box"][1], 40 / 480)

    def test_person_cut_off_at_the_edge_is_marked(self):
        self.see(440, top=40)  # feet at the bottom of the picture
        self.assertTrue(self.ctl.look_for_people()[0]["cut"])


class CalibrationTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.ctl.calibrate_seconds = 0.02
        self.ctl.camera = FakeCamera()

    def test_calibrates_from_the_tallest_whole_person(self):
        self.ctl._person_detector = FakeDetector([360, 100])
        result = self.ctl.calibrate(180)
        self.assertEqual(result["px"], 360)
        self.assertAlmostEqual(self.ctl.settings["cm_per_px"], 0.5)

    def test_nobody_seen(self):
        self.ctl._person_detector = FakeDetector([])
        with self.assertRaisesRegex(ValueError, "Nobody seen"):
            self.ctl.calibrate(180)
        self.assertEqual(self.ctl.settings["cm_per_px"], 0.0)

    def test_head_or_feet_outside_the_picture(self):
        self.ctl._person_detector = FakeDetector([470], top=0)
        with self.assertRaisesRegex(ValueError, "outside the picture"):
            self.ctl.calibrate(180)

    def test_needs_a_camera(self):
        self.ctl.camera = None
        with self.assertRaises(ValueError):
            self.ctl.calibrate(180)


if __name__ == "__main__":
    unittest.main()
