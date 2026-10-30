"""A throwaway copy of the app folder, so tests never touch the real
settings.json, sounds or cache, and need no camera, speaker or Piper."""
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from scare.main import Controller

REPO = Path(__file__).resolve().parent.parent


def make_root(local=None, settings=None):
    root = Path(tempfile.mkdtemp(prefix="halloween-test-"))
    shutil.copy(REPO / "config.toml", root)
    (root / "templates").symlink_to(REPO / "templates")
    for mode in ("scary", "kids"):
        d = root / "modes" / mode
        (d / "scare").mkdir(parents=True)
        (d / "ambient").mkdir()
        (d / "messages.txt").write_text(f"# comment\n{mode} one\n\n{mode} two\n")
    if local is not None:
        (root / "config.local.toml").write_text(local)
    if settings is not None:
        (root / "settings.json").write_text(settings)
    return root


class FakeCamera:
    def rgb_frame(self):
        return np.zeros((480, 640, 3), np.uint8)


class FakeDetector:
    """Returns people of the given pixel heights, standing in the middle of the picture."""

    def __init__(self, heights_px, top=40):
        self.heights_px = heights_px
        self.top = top

    def detect(self, rgb):
        return [(100, self.top, 200, self.top + h, 0.9) for h in self.heights_px]


class AppTestCase(unittest.TestCase):
    local = None
    settings = None

    def setUp(self):
        self.root = make_root(self.local, self.settings)
        self.addCleanup(shutil.rmtree, self.root)
        self.ctl = Controller(self.root)
        # Rendering speech runs Piper for seconds per line: not in tests
        self.ctl.speaker.prerender = lambda: None
