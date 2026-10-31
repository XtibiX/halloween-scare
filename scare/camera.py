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

    def __init__(self, width, height, fps, tuning="", ev=0.0, max_exposure_ms=0, max_gain=0):
        self.picam2 = Picamera2(tuning=self.load_tuning(tuning, max_exposure_ms, max_gain))
        config = self.picam2.create_video_configuration(
            main={"size": (width, height)},
            lores={"size": LORES_SIZE, "format": "YUV420"},
            controls=self.exposure_controls(fps, ev, max_exposure_ms),
        )
        self.picam2.configure(config)
        self.jpeg = FrameBuffer()

    @staticmethod
    def load_tuning(name, max_exposure_ms, max_gain):
        """The colour profile also caps auto exposure (66 ms, gain 8 on the OV5647),
        far too little at night, so its limits are raised in the copy we load."""
        if not name and not (max_exposure_ms or max_gain):
            return None
        if not name:
            name = Picamera2.global_camera_info()[0]["Model"] + ".json"
        tuning = Picamera2.load_tuning_file(name)
        for algo in tuning.get("algorithms", []):
            agc = algo.get("rpi.agc")
            if not agc:
                continue
            for channel in agc.get("channels", [agc]):
                for limits in channel.get("exposure_modes", {}).values():
                    if max_exposure_ms:
                        limits["shutter"][-1] = max(limits["shutter"][-1], int(max_exposure_ms * 1000))
                    if max_gain:
                        limits["gain"][-1] = max(limits["gain"][-1], float(max_gain))
        return tuning

    @staticmethod
    def exposure_controls(fps, ev, max_exposure_ms):
        """In the dark, auto exposure may use up to max_exposure_ms per frame
        (the frame rate drops to match); ev brightens (+) or darkens (-)."""
        fastest = int(1_000_000 / fps)
        slowest = max(fastest, int(max_exposure_ms * 1000))
        return {"FrameDurationLimits": (fastest, slowest), "ExposureValue": float(ev)}

    def start(self):
        self.picam2.start_recording(MJPEGEncoder(), FileOutput(self.jpeg))

    def grey_frame(self):
        """Blocks until the next frame and returns its brightness (Y) plane."""
        w, h = LORES_SIZE
        yuv = self.picam2.capture_array("lores")
        return yuv[:h, :w]

    def rgb_frame(self):
        """Full-size frame as an RGB array (XBGR8888 is [R, G, B, 255] per pixel)."""
        return self.picam2.capture_array("main")[:, :, :3]

    def snapshot(self, path):
        self.picam2.capture_file(str(path))

    def stop(self):
        self.picam2.stop_recording()
        self.picam2.close()
