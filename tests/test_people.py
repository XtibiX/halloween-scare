"""Box maths of the person detector, with a fake model output (no model file needed)."""
import unittest
from unittest import mock

import numpy as np

from scare import people
from scare.people import PersonDetector

from .helpers import AppTestCase

IMAGE = np.zeros((480, 640, 3), np.uint8)  # the camera's size: scale 0.65 into a 416 square


class FakeSession:
    def __init__(self, output):
        self.output = output
        self.blob = None

    def run(self, names, feed):
        (self.blob,) = feed.values()
        return [self.output[None]]


def detector(output, ultralytics):
    d = PersonDetector.__new__(PersonDetector)
    d.session, d.input_name, d.size, d.ultralytics = FakeSession(output), "images", 416, ultralytics
    if not ultralytics:
        d._yolox_grid()
    return d


class Yolo26Test(unittest.TestCase):
    def output(self, *boxes):
        """boxes: (cx, cy, w, h, person score, other class score) in the 416 square."""
        out = np.zeros((84, 3549), np.float32)
        for i, (cx, cy, w, h, person, other) in enumerate(boxes):
            out[:6, i] = cx, cy, w, h, person, other
        return out

    def test_box_is_mapped_back_through_the_centred_letterbox(self):
        # Person at (100, 40)-(200, 400) in the camera picture: x * 0.65, y * 0.65 + 52 padding
        d = detector(self.output((97.5, 195, 65, 234, 0.9, 0)), ultralytics=True)
        ((x1, y1, x2, y2, score),) = d.detect(IMAGE)
        np.testing.assert_allclose([x1, y1, x2, y2], [100, 40, 200, 400], atol=0.01)
        self.assertAlmostEqual(score, 0.9, places=5)

    def test_input_is_rgb_scaled_to_0_1(self):
        d = detector(self.output(), ultralytics=True)
        d.detect(np.full((480, 640, 3), [255, 0, 0], np.uint8))
        self.assertEqual(d.session.blob.shape, (1, 3, 416, 416))
        self.assertEqual(d.session.blob[0, :, 208, 208].tolist(), [1, 0, 0])
        self.assertAlmostEqual(float(d.session.blob[0, 0, 0, 0]), 114 / 255, places=5)  # grey padding

    def test_weak_and_non_person_boxes_are_ignored(self):
        d = detector(self.output((100, 200, 50, 100, 0.3, 0), (300, 200, 50, 100, 0.1, 0.95)), ultralytics=True)
        self.assertEqual(d.detect(IMAGE), [])

    def test_overlapping_boxes_are_merged_into_the_best_one(self):
        d = detector(self.output((100, 200, 60, 200, 0.7, 0), (102, 202, 60, 200, 0.9, 0),
                                 (300, 200, 60, 200, 0.8, 0)), ultralytics=True)
        self.assertEqual([round(p[4], 1) for p in d.detect(IMAGE)], [0.9, 0.8])

    def test_boxes_are_clipped_to_the_picture(self):
        d = detector(self.output((10, 60, 100, 100, 0.9, 0)), ultralytics=True)
        ((x1, y1, x2, y2, _),) = d.detect(IMAGE)
        self.assertEqual((x1, y1), (0, 0))


class YoloxTest(unittest.TestCase):
    def test_grid_cell_box_and_bgr_input(self):
        out = np.zeros((3549, 85), np.float32)
        # Stride-8 cell (x 10, y 20), centred, 10 cells wide and tall: (44, 124)-(124, 204) in the square
        out[20 * 52 + 10, :6] = 0.5, 0.5, np.log(10), np.log(10), 0.95, 0.95
        d = detector(out, ultralytics=False)
        ((x1, y1, x2, y2, score),) = d.detect(np.full((480, 640, 3), [255, 0, 0], np.uint8))
        np.testing.assert_allclose([x1, y1, x2, y2], np.array([44, 124, 124, 204]) / 0.65, atol=0.01)
        self.assertAlmostEqual(score, 0.95 ** 2, places=5)
        self.assertEqual(d.session.blob[0, :, 100, 100].tolist(), [0, 0, 255])  # BGR, 0-255


class ModelChoiceTest(AppTestCase):
    def load(self):
        with mock.patch.object(people, "PersonDetector", lambda path: path):
            return self.ctl.person_detector().name

    def test_uses_yolo26_when_present(self):
        (self.root / "models").mkdir()
        (self.root / "models/yolo26n-416.onnx").write_bytes(b"")
        self.assertEqual(self.load(), "yolo26n-416.onnx")

    def test_falls_back_to_yolox(self):
        self.assertEqual(self.load(), "yolox_nano.onnx")


if __name__ == "__main__":
    unittest.main()
