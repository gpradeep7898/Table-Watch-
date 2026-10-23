# Known limitations

Labels: **WORKING** · **SIMULATED** · **PARTIALLY IMPLEMENTED** · **NOT VERIFIED** · **BLOCKED**

## Evidence and accuracy

- **NOT VERIFIED:** accuracy on restaurant CCTV. No restaurant footage was available. Nothing in this repo measures real-world table-state accuracy.
- **WORKING (limited evidence):** person detection + tracking on one real public pedestrian video (OpenCV's `vtest.avi`, an outdoor plaza). That proves the pipeline runs on real pixels, not that it suits dining rooms.
- **SIMULATED:** all scenario results and the synthetic week. The simulator generates tracks directly (with injected misses, ID switches, occlusion and noise). Simulator-vs-simulator evaluation scores are near perfect by construction and mean nothing about reality.

## State machine

- A person standing still in an empty table's zone for `occupancy_confirm_s` seats the table (staff setting a table, a guest chatting). Mitigation: draw zones over seats only; correct with a reason.
- Departure confirmation is deliberately slow (90 s default) to avoid false departures.
- Possible staff visits are heuristic (a non-guest that comes and goes). They are never used for service status. Uniform/role detection is not implemented.
- Guests who disappear longer than the re-link window (15 s) come back as new tracks; they join the party after `max_visit_s`.
- Each table belongs to one camera. Multi-camera fusion for one table is not implemented.
- Changing thresholds applies immediately to the running session; there is no per-camera threshold set yet.

## Computer vision

- **WORKING:** YOLOX-Nano/Tiny via ONNX Runtime on CPU; letterbox preprocessing, YOLOX decoding, NMS; ByteTrack-style tracker; frame sampling, stale-frame dropping, metrics, graceful stop.
- **NOT VERIFIED:** CoreML acceleration on Apple Silicon (tried automatically, CPU fallback). Built and measured on Linux x86.
- **NOT VERIFIED:** webcam and RTSP on physical devices. Reconnect/backoff and credential redaction are unit-tested with simulated failures only.
- The tracker uses constant-velocity prediction, not a Kalman filter. Crowded, overlapping scenes will switch IDs more than full ByteTrack.
- Detection confidence below `detection_confidence` is treated as uncertainty, not presence. Very dark scenes will mostly produce "uncertain".
- The MJPEG preview and overlays are sent separately and can be a frame apart.

## Product and security

- **PARTIALLY IMPLEMENTED:** authentication. There is none; the server binds to 127.0.0.1 and rejects foreign Host/Origin headers. Do not expose it on a network without adding auth + TLS.
- Single restaurant, single active session at a time (simulation **or** recorded **or** live).
- The restaurant floor plan comes from `configs/restaurant.yaml`; there is no floor-plan editor (zones *are* editable).
- Operator names are free text stored in the browser, not accounts.
- Retention purges live/recorded events at startup only.
- Synthetic history takes ~11 s per simulated day on the build machine.
- Recharts 2.x is in maintenance mode upstream (works; migration to v3 is a future task).

## Not done / future work

- Real restaurant pilot and annotated evaluation (process ready in [pilot-readiness.md](pilot-readiness.md)).
- Staff-role recognition that preserves privacy.
- Tablet/phone staff view, push notifications.
- Packaging (single installer, auto-start service).
