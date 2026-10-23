"""Video sources: local files, webcams and RTSP cameras.

Only explicitly configured sources are ever opened. Nothing here scans the
network. RTSP URLs are redacted before they reach logs or the API.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config.layout import project_root

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}
_CRED_RE = re.compile(r"(rtsps?|https?)://([^/@\s]+)@", re.IGNORECASE)


def redact(text: str) -> str:
    """Hide credentials embedded in URLs."""
    return _CRED_RE.sub(lambda m: f"{m.group(1)}://***@", text or "")


class SourceError(ValueError):
    pass


def media_roots() -> list[Path]:
    env = os.environ.get("TABLEWATCH_MEDIA_DIRS")
    roots = [Path(p) for p in env.split(os.pathsep)] if env else [project_root() / "media"]
    return [r.resolve() for r in roots]


def resolve_video_path(path: str) -> Path:
    """Accept only existing video files inside the allowed media folders."""
    if not path or "\x00" in path:
        raise SourceError("A video path is required.")
    p = Path(path)
    if not p.is_absolute():
        p = project_root() / p
    p = p.resolve()
    if not any(p == r or r in p.parents for r in media_roots()):
        raise SourceError("Video files must live inside the media/ folder of this project.")
    if p.suffix.lower() not in VIDEO_EXTENSIONS:
        raise SourceError(f"Unsupported video type. Use one of: {', '.join(sorted(VIDEO_EXTENSIONS))}")
    if not p.is_file():
        raise SourceError(f"Video not found: {p.name}")
    return p


def validate_rtsp_url(url: str) -> str:
    if not url or len(url) > 1000 or any(c in url for c in "\r\n\x00 "):
        raise SourceError("RTSP URL is empty or malformed.")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("rtsp", "rtsps"):
        raise SourceError("Camera URL must start with rtsp:// or rtsps://")
    if not parts.hostname:
        raise SourceError("Camera URL needs a host name or IP address.")
    return url


def resolve_rtsp(config: dict[str, Any]) -> str:
    if config.get("url_env"):
        name = str(config["url_env"])
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", name):
            raise SourceError("url_env must be an environment variable name like TABLEWATCH_CAM_URL")
        val = os.environ.get(name)
        if not val:
            raise SourceError(f"Environment variable {name} is not set (put it in .env).")
        return validate_rtsp_url(val)
    return validate_rtsp_url(str(config.get("url", "")))


class VideoSource:
    """Thin OpenCV wrapper. ``read`` returns (ok, frame, source_time_s)."""

    live = False

    def __init__(self) -> None:
        self.cap = None
        self.fps = 0.0
        self.width = 0
        self.height = 0
        self.frame_count = 0

    def _target(self) -> Any:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def open(self) -> None:
        import cv2

        target = self._target()
        self.cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG) if isinstance(target, str) and target.startswith("rtsp") else cv2.VideoCapture(target)
        if not self.cap or not self.cap.isOpened():
            raise SourceError(f"Could not open {self.describe()}")
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    def read(self):
        import cv2

        if self.cap is None:
            return False, None, 0.0
        ok, frame = self.cap.read()
        if not ok:
            return False, None, 0.0
        if self.live:
            return True, frame, time.monotonic()
        pos = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        return True, frame, pos

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class FileSource(VideoSource):
    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = resolve_video_path(path)

    def _target(self) -> str:
        return str(self.path)

    def describe(self) -> str:
        return f"video file {self.path.name}"


class WebcamSource(VideoSource):
    live = True

    def __init__(self, index: int) -> None:
        super().__init__()
        if not isinstance(index, int) or not 0 <= index <= 9:
            raise SourceError("Webcam index must be between 0 and 9.")
        self.index = index

    def _target(self) -> int:
        return self.index

    def describe(self) -> str:
        return f"webcam {self.index}"


class RtspSource(VideoSource):
    live = True

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = validate_rtsp_url(url)

    def _target(self) -> str:
        return self.url

    def describe(self) -> str:
        return redact(self.url)


def make_source(camera: dict[str, Any]) -> VideoSource:
    kind = camera.get("kind")
    cfg = camera.get("config", camera)
    if kind == "file":
        return FileSource(str(cfg.get("path", "")))
    if kind == "webcam":
        return WebcamSource(int(cfg.get("index", 0)))
    if kind == "rtsp":
        return RtspSource(resolve_rtsp(cfg))
    raise SourceError(f"Camera kind '{kind}' has no video source.")
