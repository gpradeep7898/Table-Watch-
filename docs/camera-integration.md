# Camera and video integration

Status: recorded-video processing is **working and tested on a real public pedestrian clip**. Webcam and RTSP live modes are **implemented but not verified on physical cameras**.

## Pipeline

```mermaid
flowchart LR
  S[Source: file / webcam / RTSP] --> R[Reader thread\nsample_fps, reconnect]
  R -->|latest frame only| I[Inference thread\nYOLOX → tracker]
  I -->|TrackFrame, bounded queue| E[Engine on the main loop]
  I -->|JPEG in memory, ≤5/s| P[MJPEG preview]
```

- Frames are sampled (default 4/s). If inference falls behind, the older frame is dropped (`frames_dropped` metric).
- Inference resolution is the model's input (416×416, letterboxed).
- Metrics per camera: status, processing fps, observations/s, mean and p95 inference ms, dropped frames, reconnects, last frame time.
- Nothing is written to disk. The preview lives in memory and is served only to localhost.

## Install vision support

```bash
./scripts/setup.sh --vision      # numpy, opencv-python-headless, onnxruntime
./scripts/download-model.sh      # models/yolox_nano.onnx, sha256 checked
```

On Apple Silicon, ONNX Runtime's CoreML execution provider is tried automatically and falls back to CPU if it fails. This was **not verified on a Mac** (the MVP was built and measured on Linux). Measured on the build machine (x86 CPU, 4 threads): YOLOX-Nano ≈ 13–16 ms per frame on the 768×576 test clip. Your numbers will differ; the Cameras page shows live measurements.

## Recorded video

UI: add a *Video file* source (must be in `media/`), draw zones, *Analyse video*. It plays in real time with pause/resume; domain time is the video position.

CLI:

```bash
.venv/bin/python -m tablewatch.cli analyze --video ../media/lunch.mp4 --zones ../configs/zones/lunch.json \
    --sample-fps 4 [--max-seconds 600] [--annotate] [--model yolox_tiny]
```

Zones file format:

```json
{ "camera_id": "lunch", "anchor": "bottom_center",
  "zones": [ { "table_id": "T1", "polygon": [[0.10,0.40],[0.30,0.40],[0.30,0.70],[0.10,0.70]] } ] }
```

Table IDs must exist in `configs/restaurant.yaml`. Edit that file to describe a real dining room.

## Live cameras

- **Webcam:** device index 0–9.
- **RTSP:** `rtsp://` or `rtsps://` only. Prefer `url_env`: put `TABLEWATCH_CAM_X_URL=rtsp://user:pass@host/stream` in `.env` and enter `TABLEWATCH_CAM_X_URL` on the Cameras page. Direct URLs are stored only in the local database and are redacted everywhere they are shown or logged.
- Reconnect: exponential backoff from `reconnect_min_s` (1 s) to `reconnect_max_s` (30 s). Offline cameras raise `CAMERA_OFFLINE` and freeze their tables; they never free them.
- Only explicitly added sources are opened. The *I am authorized* box is required.

## Compatibility checklist (per camera)

- [ ] You own the system or have written permission to process its video.
- [ ] RTSP (or ONVIF profile with RTSP) is available on the local network, H.264/H.265.
- [ ] At least 720p, steady exposure, no heavy fisheye distortion (or zones drawn accordingly).
- [ ] The whole seating area of each covered table is visible. Guests' bodies (not only heads) are visible.
- [ ] Angle: overhead or steep (≥ 35° down) works best. Shallow angles cause occlusion between tables.
- [ ] No pillars, plants or screens covering seats. Note partial occlusions per table.
- [ ] Lighting at dinner is checked, not only at lunch. Avoid strong backlight from windows.
- [ ] IR/night mode does not switch on during service.
- [ ] Each table is covered by one camera (overlap between cameras is not merged).

## Swapping the model

Anything with `detect(frame) -> list[Detection]` can replace `YoloxOnnxDetector` (see `vision/detector.py`). Check the weights' licence before shipping (see [licenses.md](licenses.md)).
