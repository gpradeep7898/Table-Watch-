"""Person detection.

The default detector runs YOLOX (Apache-2.0, Megvii) exported to ONNX with
ONNX Runtime (MIT). Weights are not shipped in the repo; fetch them with
``./scripts/download-model.sh``. Any object with a ``detect(frame)`` method
returning ``Detection`` objects can replace it.

numpy / onnxruntime are imported lazily so demo mode never needs them.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from ..config.layout import project_root

PERSON_CLASS = 0  # COCO class id for "person"

MODELS: dict[str, dict[str, Any]] = {
    "yolox_nano": {
        "file": "yolox_nano.onnx",
        "url": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx",
        "sha256": "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d",
        "input": 416,
        "license": "Apache-2.0 (YOLOX repository and release assets)",
    },
    "yolox_tiny": {
        "file": "yolox_tiny.onnx",
        "url": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
        "sha256": "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
        "input": 416,
        "license": "Apache-2.0 (YOLOX repository and release assets)",
    },
}


@dataclass(frozen=True)
class Detection:
    bbox: tuple[float, float, float, float]  # pixel x1, y1, x2, y2
    score: float


class Detector(Protocol):
    name: str

    def detect(self, frame: Any) -> list[Detection]: ...


class DetectorUnavailable(RuntimeError):
    pass


def model_dir() -> Path:
    return project_root() / "models"


def model_status(name: str = "yolox_nano") -> dict[str, Any]:
    spec = MODELS[name]
    path = model_dir() / spec["file"]
    status: dict[str, Any] = {"name": name, "path": str(path.relative_to(project_root())), "present": path.exists(),
                              "license": spec["license"], "verified": False}
    if path.exists():
        status["verified"] = sha256(path) == spec["sha256"]
    try:
        import onnxruntime  # noqa: F401

        status["onnxruntime"] = True
    except Exception:
        status["onnxruntime"] = False
    try:
        import cv2  # noqa: F401

        status["opencv"] = True
    except Exception:
        status["opencv"] = False
    status["ready"] = bool(status["present"] and status["verified"] and status["onnxruntime"] and status["opencv"])
    return status


_hash_cache: dict[tuple[str, float], str] = {}


def sha256(path: Path) -> str:
    key = (str(path), path.stat().st_mtime)
    if key not in _hash_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _hash_cache[key] = h.hexdigest()
    return _hash_cache[key]


def nms(boxes, scores, iou_threshold: float):
    import numpy as np

    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_threshold]
    return keep


def decode_yolox(output, input_size: int, strides: Sequence[int] = (8, 16, 32)):
    """Turn raw YOLOX head output (1, N, 85) into absolute cx, cy, w, h + scores."""
    import numpy as np

    out = output[0].copy()
    grids, expanded = [], []
    for s in strides:
        n = input_size // s
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        expanded.append(np.full((n * n, 1), s))
    grid = np.concatenate(grids, 0)
    stride = np.concatenate(expanded, 0)
    out[:, :2] = (out[:, :2] + grid) * stride
    out[:, 2:4] = np.exp(out[:, 2:4]) * stride
    return out


def postprocess(decoded, ratio: float, conf: float, nms_iou: float, width: int, height: int) -> list[Detection]:
    import numpy as np

    scores = decoded[:, 4] * decoded[:, 5 + PERSON_CLASS]
    keep = scores >= conf
    if not keep.any():
        return []
    d, s = decoded[keep], scores[keep]
    boxes = np.empty((d.shape[0], 4), dtype=np.float32)
    boxes[:, 0] = (d[:, 0] - d[:, 2] / 2) / ratio
    boxes[:, 1] = (d[:, 1] - d[:, 3] / 2) / ratio
    boxes[:, 2] = (d[:, 0] + d[:, 2] / 2) / ratio
    boxes[:, 3] = (d[:, 1] + d[:, 3] / 2) / ratio
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
    return [
        Detection(tuple(float(v) for v in boxes[i]), float(s[i]))  # type: ignore[arg-type]
        for i in nms(boxes, s, nms_iou)
    ]


class YoloxOnnxDetector:
    """YOLOX person detector on ONNX Runtime with optional CoreML acceleration."""

    def __init__(self, model: str = "yolox_nano", conf: float = 0.3, nms_iou: float = 0.45,
                 prefer_accelerator: bool = True, model_path: Path | None = None) -> None:
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
            import onnxruntime as ort
        except Exception as exc:  # pragma: no cover - depends on optional install
            raise DetectorUnavailable(
                "Vision dependencies are missing. Install them with: ./scripts/setup.sh --vision"
            ) from exc
        spec = MODELS[model]
        path = model_path or model_dir() / spec["file"]
        if not path.exists():
            raise DetectorUnavailable(f"Model file {path.name} not found. Download it with ./scripts/download-model.sh")
        if model_path is None and sha256(path) != spec["sha256"]:
            raise DetectorUnavailable(f"Model file {path.name} failed its checksum. Re-run ./scripts/download-model.sh")
        available = ort.get_available_providers()
        providers = ["CPUExecutionProvider"]
        if prefer_accelerator and "CoreMLExecutionProvider" in available:
            providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        try:
            self.session = ort.InferenceSession(str(path), sess_options=opts, providers=providers)
        except Exception:
            # An accelerator that fails to initialise must never block CPU inference.
            self.session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.providers = self.session.get_providers()
        self.input_name = self.session.get_inputs()[0].name
        self.input_size = int(self.session.get_inputs()[0].shape[2] or spec["input"])
        self.conf = conf
        self.nms_iou = nms_iou
        self.name = f"{model} (ONNX Runtime: {self.providers[0]})"
        self.model = model
        self.last_ms = 0.0

    def preprocess(self, frame):
        import cv2
        import numpy as np

        h, w = frame.shape[:2]
        size = self.input_size
        ratio = min(size / h, size / w)
        resized = cv2.resize(frame, (int(w * ratio), int(h * ratio)), interpolation=cv2.INTER_LINEAR)
        padded = np.full((size, size, 3), 114, dtype=np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        blob = padded.transpose(2, 0, 1)[None].astype(np.float32)
        return blob, ratio

    def detect(self, frame) -> list[Detection]:
        t0 = time.perf_counter()
        blob, ratio = self.preprocess(frame)
        raw = self.session.run(None, {self.input_name: blob})[0]
        dets = postprocess(decode_yolox(raw, self.input_size), ratio, self.conf, self.nms_iou, frame.shape[1], frame.shape[0])
        self.last_ms = (time.perf_counter() - t0) * 1000
        return dets
