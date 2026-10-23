"""Video → detections → tracks → TrackFrame, plus threaded camera workers.

Pipeline per camera::

    source ──read──▶ [1-slot latest frame] ──infer──▶ tracker ──▶ TrackFrame queue ──▶ engine
                       (stale frames are dropped)

Frames are sampled at ``sample_fps`` so the detector never has to keep up
with the full camera frame rate. Nothing is written to disk.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from ..core.models import ObservationHealth, Source, TrackedPerson, TrackFrame
from .detector import Detection, Detector
from .sources import SourceError, VideoSource, make_source, redact
from .tracker import ByteTracker

log = logging.getLogger("tablewatch.vision")


class VisionPipeline:
    def __init__(self, detector: Detector, camera_id: str, tracker: ByteTracker | None = None,
                 min_confidence: float = 0.15) -> None:
        self.detector = detector
        self.camera_id = camera_id
        self.tracker = tracker or ByteTracker()
        self.min_confidence = min_confidence

    def process(self, frame: Any, t: float) -> tuple[TrackFrame, list[Detection]]:
        dets = [d for d in self.detector.detect(frame) if d.score >= self.min_confidence]
        tracks = self.tracker.update(dets, t)
        h, w = frame.shape[:2]
        persons = tuple(
            TrackedPerson(tr.track_id, (tr.bbox[0] / w, tr.bbox[1] / h, tr.bbox[2] / w, tr.bbox[3] / h), round(tr.score, 3))
            for tr in tracks
        )
        return TrackFrame(self.camera_id, t, persons, Source.COMPUTER_VISION, ObservationHealth.OK), dets


@dataclass
class WorkerMetrics:
    status: str = "starting"  # starting, connecting, running, paused, reconnecting, ended, error, stopped
    error: str | None = None
    detector: str | None = None
    frames_read: int = 0
    frames_sampled: int = 0
    frames_processed: int = 0
    frames_dropped: int = 0
    processing_fps: float = 0.0
    observations_per_s: float = 0.0
    inference_ms: float = 0.0
    inference_ms_p95: float = 0.0
    last_frame_wall: float | None = None
    last_frame_t: float | None = None
    source_fps: float = 0.0
    width: int = 0
    height: int = 0
    reconnects: int = 0
    video_duration_s: float | None = None
    _ms: list[float] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_ms", None)
        return d


class CameraWorker:
    """Runs one authorized camera in background threads."""

    def __init__(
        self,
        camera: dict[str, Any],
        detector_factory: Callable[[], Detector],
        out: "queue.Queue[TrackFrame]",
        clock_now: Callable[[], float],
        sample_fps: float = 4.0,
        realtime: bool = True,
        tables_count: int = 0,
        source_factory: Callable[[dict[str, Any]], VideoSource] = make_source,
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 30.0,
    ) -> None:
        self.camera = camera
        self.camera_id = camera["id"]
        self.detector_factory = detector_factory
        self.out = out
        self.clock_now = clock_now
        self.sample_fps = max(0.2, min(30.0, float(sample_fps)))
        self.realtime = realtime
        self.tables_count = tables_count
        self.source_factory = source_factory
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self.metrics = WorkerMetrics()
        self._stop = threading.Event()
        self._play = threading.Event()
        self._play.set()
        self._slot_lock = threading.Lock()
        self._slot: tuple[Any, float] | None = None
        self._slot_ready = threading.Event()
        self.latest_jpeg: bytes | None = None
        self.latest_jpeg_seq = 0
        self.latest_tracks: list[dict[str, Any]] = []
        self.latest_size: tuple[int, int] = (0, 0)
        self._threads: list[threading.Thread] = []
        self._proc_times: list[float] = []

    # ------------------------------------------------------------------
    def start(self) -> None:
        r = threading.Thread(target=self._reader, name=f"tw-read-{self.camera_id}", daemon=True)
        i = threading.Thread(target=self._infer, name=f"tw-infer-{self.camera_id}", daemon=True)
        self._threads = [r, i]
        r.start()
        i.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._play.set()
        self._slot_ready.set()
        for th in self._threads:
            th.join(timeout)
        if self.metrics.status not in ("ended", "error"):
            self.metrics.status = "stopped"

    def pause(self) -> None:
        self._play.clear()
        if self.metrics.status == "running":
            self.metrics.status = "paused"

    def resume(self) -> None:
        self._play.set()
        if self.metrics.status == "paused":
            self.metrics.status = "running"

    @property
    def alive(self) -> bool:
        return any(th.is_alive() for th in self._threads)

    # ------------------------------------------------------------------
    def _open(self) -> VideoSource | None:
        backoff = self.reconnect_min_s
        while not self._stop.is_set():
            self.metrics.status = "connecting" if self.metrics.reconnects == 0 else "reconnecting"
            try:
                src = self.source_factory(self.camera)
                src.open()
                self.metrics.source_fps = src.fps
                self.metrics.width, self.metrics.height = src.width, src.height
                if not src.live and src.fps and src.frame_count:
                    self.metrics.video_duration_s = src.frame_count / src.fps
                self.metrics.status = "running" if self._play.is_set() else "paused"
                self.metrics.error = None
                return src
            except SourceError as exc:
                self.metrics.error = redact(str(exc))
                if self.camera.get("kind") == "file":
                    self.metrics.status = "error"
                    return None
            except Exception as exc:  # pragma: no cover - OpenCV backend errors
                self.metrics.error = redact(f"{type(exc).__name__}: {exc}")
            self.metrics.reconnects += 1
            log.warning("camera %s unavailable, retrying in %.0fs: %s", self.camera_id, backoff, self.metrics.error)
            self._stop.wait(backoff)
            backoff = min(self.reconnect_max_s, backoff * 2)
        return None

    def _reader(self) -> None:
        src = self._open()
        if src is None:
            self._slot_ready.set()
            return
        interval = 1.0 / self.sample_fps
        next_sample = None
        start_wall = time.monotonic()
        start_pts = None
        paused_total = 0.0
        try:
            while not self._stop.is_set():
                if not self._play.is_set():
                    p0 = time.monotonic()
                    self._play.wait()
                    paused_total += time.monotonic() - p0
                    continue
                ok, frame, ts = src.read()
                if not ok:
                    if src.live:
                        src.close()
                        self.metrics.error = "Stream interrupted"
                        self.metrics.reconnects += 1
                        src = self._open()
                        if src is None:
                            return
                        continue
                    self.metrics.status = "ended"
                    return
                self.metrics.frames_read += 1
                t = ts if not src.live else self.clock_now()
                if start_pts is None:
                    start_pts = ts
                if self.realtime and not src.live:
                    # Pace file playback to real time (pauses excluded).
                    due = start_wall + paused_total + (ts - start_pts)
                    delay = due - time.monotonic()
                    if delay > 0:
                        self._stop.wait(delay)
                if next_sample is not None and t < next_sample:
                    continue
                next_sample = t + interval if next_sample is None else max(next_sample + interval, t)
                self.metrics.frames_sampled += 1
                with self._slot_lock:
                    if self._slot is not None:
                        self.metrics.frames_dropped += 1  # inference is behind: drop the stale frame
                    self._slot = (frame, t)
                self._slot_ready.set()
        finally:
            src.close()
            self._slot_ready.set()

    def _infer(self) -> None:
        try:
            detector = self.detector_factory()
            pipeline = VisionPipeline(detector, self.camera_id)
            self.metrics.detector = getattr(detector, "name", type(detector).__name__)
        except Exception as exc:
            self.metrics.status = "error"
            self.metrics.error = str(exc)
            self._stop.set()
            return
        import cv2

        last_preview = 0.0
        while True:
            self._slot_ready.wait(0.5)
            with self._slot_lock:
                item, self._slot = self._slot, None
                self._slot_ready.clear()
            if item is None:
                if self._stop.is_set() or not self._threads[0].is_alive():
                    if self.metrics.status not in ("ended", "error"):
                        self.metrics.status = "stopped"
                    return
                continue
            frame, t = item
            t0 = time.perf_counter()
            track_frame, _ = pipeline.process(frame, t)
            ms = (time.perf_counter() - t0) * 1000
            m = self.metrics
            m.frames_processed += 1
            m._ms.append(ms)
            m._ms = m._ms[-200:]
            m.inference_ms = round(sum(m._ms) / len(m._ms), 1)
            m.inference_ms_p95 = round(sorted(m._ms)[int(0.95 * (len(m._ms) - 1))], 1)
            now = time.monotonic()
            self._proc_times = [x for x in self._proc_times if now - x < 5.0] + [now]
            if len(self._proc_times) > 1:
                span = self._proc_times[-1] - self._proc_times[0]
                m.processing_fps = round((len(self._proc_times) - 1) / span, 2) if span > 0 else 0.0
                m.observations_per_s = round(m.processing_fps * self.tables_count, 2)
            m.last_frame_wall = time.time()
            m.last_frame_t = t
            try:
                self.out.put_nowait(track_frame)
            except queue.Full:
                try:
                    self.out.get_nowait()
                except queue.Empty:
                    pass
                self.out.put_nowait(track_frame)
            self.latest_tracks = [
                {"id": p.track_id, "bbox": [round(v, 4) for v in p.bbox], "conf": p.confidence} for p in track_frame.tracks
            ]
            if now - last_preview > 0.2:  # at most ~5 preview frames per second, kept in memory only
                last_preview = now
                h, w = frame.shape[:2]
                scale = min(1.0, 960 / max(1, w))
                small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
                ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    self.latest_jpeg = buf.tobytes()
                    self.latest_jpeg_seq += 1
                    self.latest_size = (small.shape[1], small.shape[0])
