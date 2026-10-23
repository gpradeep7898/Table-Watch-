"""Vision pipeline pieces. Uses fake detectors and generated frames; the real
model test only runs when the weights and a local test video are present."""

import queue
import time
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from tablewatch.config.layout import project_root
from tablewatch.vision.detector import Detection, decode_yolox, model_status, nms, postprocess
from tablewatch.vision.pipeline import CameraWorker, VisionPipeline
from tablewatch.vision.sources import (
    SourceError,
    redact,
    resolve_rtsp,
    resolve_video_path,
    validate_rtsp_url,
)
from tablewatch.vision.tracker import ByteTracker, iou


class FakeDetector:
    """Test double: one person drifting right, optionally blinking."""

    name = "fake"

    def __init__(self):
        self.n = 0

    def detect(self, frame):
        self.n += 1
        x = 50 + 2 * self.n
        return [Detection((x, 100, x + 40, 200), 0.9)]


def test_yolox_decode_and_postprocess():
    size = 416
    n = sum((size // s) ** 2 for s in (8, 16, 32))
    raw = np.zeros((1, n, 85), dtype=np.float32)
    raw[0, :, 4] = 0.0
    # Cell (x=10, y=5) at stride 8 holds a person centered in that cell, 4 cells wide.
    idx = 5 * (size // 8) + 10
    raw[0, idx, :4] = [0.5, 0.5, np.log(4), np.log(8)]
    raw[0, idx, 4] = 0.9
    raw[0, idx, 5] = 0.95  # person class
    dets = postprocess(decode_yolox(raw, size), ratio=0.5, conf=0.3, nms_iou=0.45, width=1000, height=1000)
    assert len(dets) == 1
    x1, y1, x2, y2 = dets[0].bbox
    assert (x1 + x2) / 2 == pytest.approx((10.5 * 8) / 0.5)
    assert (y1 + y2) / 2 == pytest.approx((5.5 * 8) / 0.5)
    assert x2 - x1 == pytest.approx(32 / 0.5)
    assert dets[0].score == pytest.approx(0.855, abs=1e-3)


def test_nms_suppresses_overlaps():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    assert nms(boxes, scores, 0.5) == [0, 2]


def test_tracker_keeps_ids_and_survives_weak_detections():
    tr = ByteTracker(min_hits=2, max_lost_s=1.0)
    ids = []
    for i in range(10):
        score = 0.3 if 4 <= i <= 6 else 0.9  # partially occluded for three frames
        out = tr.update([Detection((10 + 3 * i, 10, 50 + 3 * i, 90), score)], i * 0.25)
        ids += [t.track_id for t in out]
    assert set(ids) == {1}
    # Lost for longer than max_lost_s: the next detection gets a new ID.
    tr.update([], 3.0)
    out = tr.update([Detection((200, 10, 240, 90), 0.9)], 4.5)
    out = tr.update([Detection((200, 10, 240, 90), 0.9)], 4.75)
    assert out and out[0].track_id != 1


def test_tracker_separates_two_people():
    tr = ByteTracker(min_hits=1)
    for i in range(5):
        out = tr.update([Detection((0, 0, 20, 40), 0.9), Detection((100, 0, 120, 40), 0.9)], i * 0.2)
    assert len({t.track_id for t in out}) == 2
    assert iou((0, 0, 10, 10), (5, 5, 15, 15)) == pytest.approx(25 / 175)


def test_pipeline_normalizes_coordinates():
    p = VisionPipeline(FakeDetector(), "cam", ByteTracker(min_hits=1))
    frame = np.zeros((400, 800, 3), dtype=np.uint8)
    tf, dets = p.process(frame, 1.0)
    assert tf.camera_id == "cam" and tf.source.value == "computer_vision"
    (person,) = tf.tracks
    assert person.bbox[1] == pytest.approx(100 / 400) and person.bbox[2] <= 1.0


def _make_video(path: Path, seconds: float = 2.0, fps: int = 10) -> Path:
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (320, 240))
    for i in range(int(seconds * fps)):
        img = np.full((240, 320, 3), 40, dtype=np.uint8)
        cv2.rectangle(img, (20 + i * 3, 60), (60 + i * 3, 180), (200, 200, 200), -1)
        w.write(img)
    w.release()
    return path


def test_worker_processes_file_and_ends(tmp_path, monkeypatch):
    monkeypatch.setenv("TABLEWATCH_MEDIA_DIRS", str(tmp_path))
    video = _make_video(tmp_path / "clip.avi")
    out: queue.Queue = queue.Queue(maxsize=100)
    w = CameraWorker({"id": "file-cam", "kind": "file", "config": {"path": str(video)}}, FakeDetector, out,
                     clock_now=time.monotonic, sample_fps=5, realtime=False, tables_count=2)
    w.start()
    deadline = time.time() + 10
    while w.alive and time.time() < deadline:
        time.sleep(0.05)
    assert w.metrics.status == "ended"
    assert w.metrics.frames_read == 20
    assert w.metrics.frames_processed >= 5
    frames = []
    while not out.empty():
        frames.append(out.get())
    assert frames and all(f.camera_id == "file-cam" for f in frames)
    assert frames[-1].t > frames[0].t  # video time, not wall time
    assert w.latest_jpeg and w.latest_jpeg[:2] == b"\xff\xd8"


def test_worker_reconnects_live_source_with_backoff():
    attempts = []

    def failing_source(camera):
        attempts.append(time.monotonic())
        raise SourceError("connection refused for rtsp://admin:hunter2@10.0.0.9/live")

    out: queue.Queue = queue.Queue()
    w = CameraWorker({"id": "rtsp-cam", "kind": "rtsp", "config": {}}, FakeDetector, out, time.monotonic,
                     source_factory=failing_source, reconnect_min_s=0.05, reconnect_max_s=0.2)
    w.start()
    deadline = time.time() + 10
    while w.metrics.reconnects < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert w.metrics.status == "reconnecting"
    assert w.metrics.reconnects >= 2
    assert "hunter2" not in (w.metrics.error or "")
    w.stop()
    assert not w.alive


def test_redaction_and_source_validation(tmp_path, monkeypatch):
    assert redact("rtsp://admin:pw@1.2.3.4/x and https://u:p@h") == "rtsp://***@1.2.3.4/x and https://***@h"
    assert validate_rtsp_url("rtsp://cam.local:554/s")
    for bad in ("http://cam/s", "rtsp://", "rtsp://a b/s", "file:///etc/passwd"):
        with pytest.raises(SourceError):
            validate_rtsp_url(bad)
    monkeypatch.setenv("TABLEWATCH_MEDIA_DIRS", str(tmp_path))
    (tmp_path / "ok.mp4").write_bytes(b"x")
    assert resolve_video_path(str(tmp_path / "ok.mp4")).name == "ok.mp4"
    with pytest.raises(SourceError):
        resolve_video_path(str(tmp_path / ".." / "etc.mp4"))
    with pytest.raises(SourceError):
        resolve_video_path(str(tmp_path / "ok.exe"))
    monkeypatch.setenv("TW_CAM_URL", "rtsp://u:p@cam/s")
    assert resolve_rtsp({"url_env": "TW_CAM_URL"}) == "rtsp://u:p@cam/s"
    with pytest.raises(SourceError):
        resolve_rtsp({"url_env": "lower"})


REAL_VIDEO = project_root() / "media" / "vtest.avi"


@pytest.mark.skipif(not (model_status()["ready"] and REAL_VIDEO.exists()),
                    reason="needs ./scripts/download-model.sh and media/vtest.avi (see docs/camera-integration.md)")
def test_real_detector_finds_people_in_real_video():
    from tablewatch.vision.detector import YoloxOnnxDetector

    det = YoloxOnnxDetector()
    cap = cv2.VideoCapture(str(REAL_VIDEO))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 100)
    ok, frame = cap.read()
    assert ok
    people = det.detect(frame)
    assert len(people) >= 4  # a busy pedestrian scene, not a restaurant
    assert all(0 <= d.score <= 1 for d in people)


def test_recorded_mode_runs_real_pipeline_into_its_own_dataset(tmp_path, monkeypatch):
    """Runtime → CvSession → CameraWorker → engine → SQLite, with a fake detector."""
    from tablewatch.config.layout import load_layout
    from tablewatch.services.runtime import Runtime
    from tablewatch.services.settings import SettingsService
    from tablewatch.simulation.scenarios import load_scenarios
    from tablewatch.storage.db import Store

    monkeypatch.setenv("TABLEWATCH_MEDIA_DIRS", str(tmp_path))
    video = _make_video(tmp_path / "service.avi", seconds=3.0)

    class StillPerson:
        name = "fake-still"

        def detect(self, frame):
            return [Detection((40, 60, 80, 180), 0.9)]  # always inside T1's zone

    store = Store(tmp_path / "rt.db")
    layout = load_layout()
    rt = Runtime(store, layout, load_scenarios(), SettingsService(store), detector_factory=StillPerson, require_model=False)
    rt.start_simulation(autoplay=False)
    store.upsert_camera("clip", layout.id, "clip", "file", {"path": str(video), "sample_fps": 10})
    rt.set_zones("clip", "bottom_center", [{"table_id": "T1", "polygon": [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]}])
    th, v = rt.settings.thresholds()
    from dataclasses import replace

    rt.apply_thresholds(replace(th, occupancy_confirm_s=1.0, lost_track_grace_s=0.5), v)
    rt.start_cv("recorded", ["clip"])
    assert rt.mode == "recorded"
    deadline = time.time() + 15
    while time.time() < deadline and not rt.cv.finished:
        rt.tick(0.1)
        time.sleep(0.05)
    rt.tick(0.1)
    assert rt.cv.workers["clip"].metrics.status == "ended"
    events = store.events("recorded", rt.run_id, limit=100)
    seated = [e for e in events if e["type"] == "GUESTS_SEATED"]
    assert seated and seated[0]["table_id"] == "T1" and seated[0]["source"] == "computer_vision"
    assert store.events("simulation", limit=1000) == [] or all(e["dataset"] == "simulation" for e in store.events("simulation", limit=1000))
    snap = rt.snapshot()
    assert snap["mode"] == "recorded" and snap["clock"]["simulated"] is False
    t1 = next(t for t in snap["tables"] if t["table_id"] == "T1")
    assert t1["state"] == "SEATED_UNSERVED" and t1["health"] == "OK"
    assert next(t for t in snap["tables"] if t["table_id"] == "T2")["health"] == "UNKNOWN"
    rt.back_to_simulation()
    assert rt.mode == "simulation" and rt.cv is None
