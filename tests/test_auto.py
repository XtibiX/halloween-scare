"""Auto mode must always fall back to the kid mode when in doubt."""
import unittest

from .helpers import AppTestCase, FakeCamera, FakeDetector

# One spot where 1 cm looks 2 px tall (of 480): heights are pixels / 2
ONE_SPOT = [[0.9, 2 / 480]]
# Two spots for the same 180 cm person: close by (feet at 0.9 of the picture
# height, 360 px tall) and further away (feet at 0.5, 180 px tall)
FAR, NEAR = [0.5, 0.375 / 180], [0.9, 0.75 / 180]


class AutoModeTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.ctl.auto["look_seconds"] = 0.02
        self.ctl.update_settings({"mode": "auto", "kid_height_cm": 130})
        self.ctl.settings["calibration"] = ONE_SPOT
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
        self.ctl.settings["calibration"] = []
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

    def test_head_cut_off_is_marked_but_measured(self):
        self.see(300, top=0)
        (p,) = self.ctl.look_for_people()
        self.assertTrue(p["cut"])
        self.assertEqual(p["height_cm"], 150)

    def test_feet_cut_off_gives_no_height(self):
        self.see(440, top=40)  # feet at the bottom of the picture: distance unknown
        (p,) = self.ctl.look_for_people()
        self.assertTrue(p["cut"])
        self.assertIsNone(p["height_cm"])

    def test_only_people_with_feet_cut_off_gets_kid_mode(self):
        self.see(440, top=40)
        self.assertEqual(self.ctl.pick_mode(), "kids")
        self.assertEqual(self.ctl.last_auto["why"], "nobody seen clearly")


class TwoSpotTest(AppTestCase):
    """With two spots, heights are right at any distance from the camera."""

    def setUp(self):
        super().setUp()
        self.ctl.auto["look_seconds"] = 0.02
        self.ctl.update_settings({"mode": "auto", "kid_height_cm": 130})
        self.ctl.settings["calibration"] = [FAR, NEAR]
        self.ctl.camera = FakeCamera()

    def heights(self, *people):
        self.ctl._person_detector = FakeDetector(list(people))
        return [p["height_cm"] for p in self.ctl.look_for_people()]

    def test_same_adult_close_by_and_further_away(self):
        self.assertEqual(self.heights((72, 360), (60, 180)), [180, 180])

    def test_child_in_between(self):
        # Feet at 0.7: 1 cm looks 1.5 px tall, so 180 px = 120 cm
        self.assertEqual(self.heights((156, 180)), [120])
        self.ctl._person_detector = FakeDetector([(156, 180)])
        self.assertEqual(self.ctl.pick_mode(), "kids")

    def test_adult_walking_up_from_far_away_gets_scary_mode(self):
        # Feet at 0.3, smaller than at either spot: still 180 cm
        self.assertEqual(self.heights((54, 90)), [180])

    def test_too_far_away_gives_no_height(self):
        # Near the horizon (feet at 0.15): far too small to measure
        self.assertEqual(self.heights((60, 12)), [None])


class CalibrationTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.ctl.calibrate_seconds = 0.02
        self.ctl.camera = FakeCamera()

    def stand(self, *people):
        self.ctl._person_detector = FakeDetector(list(people))

    def test_calibrates_from_the_tallest_whole_person(self):
        self.stand((72, 360), (300, 100))
        result = self.ctl.calibrate(180)
        self.assertEqual(result["px"], 360)
        self.assertEqual(result["spots"], 1)
        (spot,) = self.ctl.settings["calibration"]
        self.assertAlmostEqual(spot[0], NEAR[0])
        self.assertAlmostEqual(spot[1], NEAR[1])

    def test_second_spot_is_added(self):
        self.stand((72, 360))
        self.ctl.calibrate(180)
        self.stand((60, 180))
        self.assertEqual(self.ctl.calibrate(180, add=True)["spots"], 2)
        (far, near) = self.ctl.settings["calibration"]
        self.assertAlmostEqual(far[0], FAR[0])
        self.assertAlmostEqual(near[0], NEAR[0])

    def test_calibrate_without_add_starts_over(self):
        self.stand((72, 360))
        self.ctl.calibrate(180)
        self.stand((60, 180))
        self.ctl.calibrate(180)
        self.assertEqual(len(self.ctl.settings["calibration"]), 1)
        self.assertAlmostEqual(self.ctl.settings["calibration"][0][0], FAR[0])

    def test_adding_a_third_spot_replaces_the_nearer_one(self):
        self.ctl.settings["calibration"] = [FAR, NEAR]
        self.stand((114, 270))  # feet at 0.8: replaces NEAR, keeps FAR
        self.ctl.calibrate(180, add=True)
        (far, new) = self.ctl.settings["calibration"]
        self.assertAlmostEqual(far[0], FAR[0])
        self.assertAlmostEqual(new[0], 0.8)

    def test_second_spot_too_close_to_the_first(self):
        self.ctl.settings["calibration"] = [NEAR]
        self.stand((60, 360))  # feet at 0.875
        with self.assertRaisesRegex(ValueError, "Too close"):
            self.ctl.calibrate(180, add=True)
        self.assertEqual(self.ctl.settings["calibration"], [NEAR])

    def test_further_away_but_bigger_is_rejected(self):
        self.ctl.settings["calibration"] = [NEAR]
        self.stand((14, 370))  # feet higher up (0.8), yet taller: camera moved or a mistake
        with self.assertRaisesRegex(ValueError, "don't fit"):
            self.ctl.calibrate(180, add=True)

    def test_calibration_is_saved(self):
        self.stand((72, 360))
        self.ctl.calibrate(180)
        from scare.main import Controller
        self.assertEqual(len(Controller(self.root).settings["calibration"]), 1)

    def test_nobody_seen(self):
        self.stand()
        with self.assertRaisesRegex(ValueError, "Nobody seen"):
            self.ctl.calibrate(180)
        self.assertEqual(self.ctl.settings["calibration"], [])

    def test_head_or_feet_outside_the_picture(self):
        self.stand((0, 470))
        with self.assertRaisesRegex(ValueError, "outside the picture"):
            self.ctl.calibrate(180)

    def test_silly_height_is_rejected(self):
        self.stand((72, 360))
        with self.assertRaises(ValueError):
            self.ctl.calibrate(0)

    def test_needs_a_camera(self):
        self.ctl.camera = None
        with self.assertRaises(ValueError):
            self.ctl.calibrate(180)


if __name__ == "__main__":
    unittest.main()
