import unittest

import numpy as np

from scare.motion import MotionDetector


def frame(value=100):
    return np.full((240, 320), value, np.uint8)


def with_square(size, value=200):
    f = frame()
    f[:size, :size] = value
    return f


class MotionTest(unittest.TestCase):
    def setUp(self):
        self.det = MotionDetector(pixel_threshold=25, area_percent=1.5, frames_required=3)

    def test_first_frame_only_learns_the_background(self):
        self.assertFalse(self.det.update(with_square(120)))
        self.assertEqual(self.det.level, 0.0)

    def test_still_picture_never_triggers(self):
        for _ in range(20):
            self.assertFalse(self.det.update(frame()))
        self.assertEqual(self.det.level, 0.0)

    def test_small_noise_below_threshold_is_ignored(self):
        self.det.update(frame(100))
        for _ in range(5):
            self.assertFalse(self.det.update(frame(110)))  # 10 < pixel_threshold
        self.assertEqual(self.det.level, 0.0)

    def test_triggers_once_after_frames_required(self):
        self.det.update(frame())
        results = [self.det.update(with_square(120)) for _ in range(6)]
        self.assertEqual(results, [False, False, True, False, False, False])
        self.assertAlmostEqual(self.det.level, 120 * 120 / (240 * 320) * 100, delta=1)

    def test_flicker_resets_the_count(self):
        self.det.update(frame())
        moving = with_square(120)
        for f in (moving, moving, frame(), moving, moving):
            self.assertFalse(self.det.update(f))
        self.assertTrue(self.det.update(moving))

    def test_area_below_area_percent_does_not_trigger(self):
        self.det.update(frame())
        for _ in range(5):
            self.assertFalse(self.det.update(with_square(20)))  # about 0.5 % of the picture
        self.assertGreater(self.det.level, 0)

    def test_background_slowly_absorbs_something_that_stopped_moving(self):
        self.det.update(frame())
        for _ in range(200):
            self.det.update(with_square(120))
        self.assertEqual(self.det.level, 0.0)

    def test_odd_frame_size_and_size_change(self):
        self.det.update(np.full((241, 321), 100, np.uint8))
        self.assertFalse(self.det.update(np.full((100, 100), 100, np.uint8)))  # starts over
        self.assertEqual(self.det.level, 0.0)


if __name__ == "__main__":
    unittest.main()
