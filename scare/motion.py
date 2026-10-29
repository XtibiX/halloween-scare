import numpy as np


class MotionDetector:
    """Compares each frame against a slowly updated background picture.

    `level` is the percentage of the picture that changed in the last frame.
    update() returns True once per motion event: when the level has stayed
    above `area_percent` for `frames_required` frames in a row.
    """

    def __init__(self, pixel_threshold, area_percent, frames_required, learn_rate=0.05):
        self.pixel_threshold = pixel_threshold
        self.area_percent = area_percent
        self.frames_required = frames_required
        self.learn_rate = learn_rate
        self.background = None
        self.hits = 0
        self.level = 0.0

    def update(self, grey):
        # Average 2x2 blocks: a quarter of the work, and smooths sensor noise
        h, w = grey.shape[0] // 2 * 2, grey.shape[1] // 2 * 2
        small = grey[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3), dtype=np.float32)

        if self.background is None or self.background.shape != small.shape:
            self.background = small
            return False

        changed = np.abs(small - self.background) > self.pixel_threshold
        self.level = float(changed.mean() * 100)
        # Slowly absorb lighting changes and things that stop moving
        self.background += self.learn_rate * (small - self.background)

        if self.level >= self.area_percent:
            self.hits += 1
        else:
            self.hits = 0
        return self.hits == self.frames_required
