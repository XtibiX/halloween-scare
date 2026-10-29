"""Person detection with YOLOX-nano (ONNX, ~0.2 s per frame on a Pi 4)."""
import numpy as np
import onnxruntime as ort
from PIL import Image

INPUT = 416
MIN_SCORE = 0.45
NMS_IOU = 0.45


class PersonDetector:
    def __init__(self, model_path, threads=2):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads  # leave cores for camera, audio and Piper
        self.session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        # YOLOX outputs offsets per grid cell at strides 8, 16 and 32
        grids, strides = [], []
        for s in (8, 16, 32):
            n = INPUT // s
            yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), s))
        self.grids = np.concatenate(grids).astype(np.float32)
        self.strides = np.concatenate(strides).astype(np.float32)

    def detect(self, rgb):
        """Returns [(x1, y1, x2, y2, score), ...] in pixels of the given RGB image."""
        h, w = rgb.shape[:2]
        r = min(INPUT / h, INPUT / w)
        nh, nw = int(h * r), int(w * r)
        canvas = np.full((INPUT, INPUT, 3), 114, np.uint8)
        canvas[:nh, :nw] = np.asarray(Image.fromarray(rgb).resize((nw, nh), Image.BILINEAR))
        blob = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1))[None].astype(np.float32)

        out = self.session.run(None, {self.input_name: blob})[0][0]
        scores = out[:, 4] * out[:, 5]  # objectness x "person" class
        keep = scores > MIN_SCORE
        if not keep.any():
            return []
        xy = (out[keep, :2] + self.grids[keep]) * self.strides[keep]
        wh = np.exp(out[keep, 2:4]) * self.strides[keep]
        boxes = np.concatenate([xy - wh / 2, xy + wh / 2], 1) / r
        boxes = np.clip(boxes, 0, [w, h, w, h])
        scores = scores[keep]
        return [(*boxes[i].tolist(), float(scores[i])) for i in _nms(boxes, scores)]


def _nms(boxes, scores):
    order = scores.argsort()[::-1]
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[1:][iou < NMS_IOU]
    return keep
