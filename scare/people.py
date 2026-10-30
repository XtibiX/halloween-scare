"""Person detection with a small ONNX model on the CPU.

Two model layouts are supported, recognised from the model's output:
- Ultralytics YOLO26n at 416 px (the default, ~0.29 s per frame on a Pi 4,
  better boxes: heads are less often cut off, so heights are more accurate)
- YOLOX-nano (the fallback, ~0.2 s per frame)
"""
import numpy as np
import onnxruntime as ort
from PIL import Image

MIN_SCORE = 0.45
NMS_IOU = 0.45


class PersonDetector:
    def __init__(self, model_path, threads=2):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads  # leave cores for camera, audio and Piper
        self.session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.size = inp.shape[2]
        # Ultralytics: (1, 4 + 80 classes, boxes). YOLOX: (1, boxes, 4 + 1 + 80 classes)
        self.ultralytics = self.session.get_outputs()[0].shape[1] == 84
        if not self.ultralytics:
            self._yolox_grid()

    def detect(self, rgb):
        """Returns [(x1, y1, x2, y2, score), ...] in pixels of the given RGB image."""
        if self.ultralytics:
            blob, scale, left, top = self._letterbox(rgb, centred=True)
            out = self.session.run(None, {self.input_name: blob / np.float32(255)})[0][0]
            return self._decode_ultralytics(out, rgb.shape, scale, left, top)
        blob, scale, _, _ = self._letterbox(rgb, centred=False)
        out = self.session.run(None, {self.input_name: blob[:, ::-1].copy()})[0][0]  # YOLOX wants BGR
        return self._decode_yolox(out, rgb.shape, scale)

    def _letterbox(self, rgb, centred):
        """Scales the picture into a grey square, keeping its shape. Returns a
        (1, 3, size, size) float32 RGB blob, the scale and the padding."""
        h, w = rgb.shape[:2]
        scale = min(self.size / h, self.size / w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        top, left = ((self.size - nh) // 2, (self.size - nw) // 2) if centred else (0, 0)
        canvas = np.full((self.size, self.size, 3), 114, np.uint8)
        canvas[top:top + nh, left:left + nw] = np.asarray(Image.fromarray(rgb).resize((nw, nh), Image.BILINEAR))
        return canvas.transpose(2, 0, 1)[None].astype(np.float32), scale, left, top

    def _decode_ultralytics(self, out, shape, scale, left, top):
        out = out.T  # (boxes, 84): cx, cy, w, h, then a score per class
        scores = out[:, 4]  # class 0 = person
        keep = scores > MIN_SCORE
        if not keep.any():
            return []
        cx, cy, bw, bh = out[keep, :4].T
        boxes = np.stack([cx - bw / 2 - left, cy - bh / 2 - top, cx + bw / 2 - left, cy + bh / 2 - top], 1) / scale
        return _finish(boxes, scores[keep], shape)

    def _yolox_grid(self):
        # YOLOX outputs offsets per grid cell at strides 8, 16 and 32
        grids, strides = [], []
        for s in (8, 16, 32):
            n = self.size // s
            yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), s))
        self.grids = np.concatenate(grids).astype(np.float32)
        self.strides = np.concatenate(strides).astype(np.float32)

    def _decode_yolox(self, out, shape, scale):
        scores = out[:, 4] * out[:, 5]  # objectness x "person" class
        keep = scores > MIN_SCORE
        if not keep.any():
            return []
        xy = (out[keep, :2] + self.grids[keep]) * self.strides[keep]
        wh = np.exp(out[keep, 2:4]) * self.strides[keep]
        boxes = np.concatenate([xy - wh / 2, xy + wh / 2], 1) / scale
        return _finish(boxes, scores[keep], shape)


def _finish(boxes, scores, shape):
    h, w = shape[:2]
    boxes = np.clip(boxes, 0, [w, h, w, h])
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
