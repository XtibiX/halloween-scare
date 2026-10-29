import io
import threading

from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

LORES_SIZE = (320, 240)


class FrameBuffer(io.BufferedIOBase):
    """Keeps the latest JPEG from the encoder for the web stream."""

    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = bytes(buf)
            self.condition.notify_all()
        return len(buf)

    def wait(self, timeout=2.0):
        with self.condition:
            self.condition.wait(timeout)
            return self.frame


class Camera:
    """Main stream is JPEG-encoded in hardware for the web page; the small
    lores stream is used for motion detection."""

    def __init__(self, width, height, fps):
        self.picam2 = Picamera2()
        config = self.picam2.create_video_configuration(
            main={"size": (width, height)},
            lores={"size": LORES_SIZE, "format": "YUV420"},
            controls={"FrameRate": fps},
        )
        self.picam2.configure(config)
        self.jpeg = FrameBuffer()

    def start(self):
        self.picam2.start_recording(MJPEGEncoder(), FileOutput(self.jpeg))

    def grey_frame(self):
        """Blocks until the next frame and returns its brightness (Y) plane."""
        w, h = LORES_SIZE
        yuv = self.picam2.capture_array("lores")
        return yuv[:h, :w]

    def snapshot(self, path):
        self.picam2.capture_file(str(path))

    def stop(self):
        self.picam2.stop_recording()
        self.picam2.close()
