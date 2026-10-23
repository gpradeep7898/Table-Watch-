# Build progress

Built 2026-10-22/23 in a Linux container (Python 3.13, Node 22, x86 CPU). Not run on a Mac. Test counts below are from the final run.

| Phase | Scope | Status | Verified by |
|---|---|---|---|
| A | Project skeleton, venv, scripts | WORKING | fresh `git clone` → `setup.sh` (17 s, demo-only) → `test.sh` → `start-demo.sh` → Ctrl+C releases ports |
| B | Deterministic state machine, alerts, engine | WORKING | 24 state-machine + 18 engine tests |
| C | SQLite event store, simulator, 5 scenarios + guided demo | WORKING / SIMULATED | storage tests (idempotency, replay, dataset isolation), 17 scenario tests incl. reproducibility |
| D | REST + WebSocket API, runtime loop | WORKING | 16 API contract tests (incl. WS, origin, Host header, CORS) |
| E | React dashboard (Overview, Live Floor, Cameras, Alerts, Analytics, History, Settings) | WORKING | `tsc`, 4 Vitest tests, screenshots reviewed, 9 Playwright flows against the real backend |
| F | Simulation ↔ backend ↔ frontend | WORKING | e2e flows; integration test arrival → alert → service → departure → cleaning → analytics with SQLite cross-check |
| G | Analytics + zone editor | WORKING | analytics unit tests (offline time excluded), e2e zone draw/save/reload |
| H | Real-video CV (YOLOX ONNX + tracker + CLI analyze) | WORKING on a real pedestrian clip, NOT VERIFIED on restaurant footage | `cli analyze` on `vtest.avi`: 265 analysed frames, inference mean 13.4 ms / p95 16.8 ms, 14.7× real time (this machine only); recorded mode via the UI with live MJPEG + overlays; CI test with fake detector |
| I | Webcam / RTSP with reconnect | PARTIALLY VERIFIED | reconnect/backoff + redaction unit tests with simulated failures; no physical camera available |
| J | Docs, pilot readiness, evaluation tooling | WORKING | evaluator tests; `cli simulate` + `cli evaluate` round trip on synthetic truth |

## Final test results

- Backend: **108 passed** (`pytest`, 30 s). On a demo-only install without numpy/OpenCV: 98 passed, vision module skipped.
- Frontend: type check clean, **4 Vitest** tests passed, production build OK.
- Browser e2e: **9 Playwright** flows passed (start demo, scenario, speed, pause/resume, table select, confirm service, confirm cleaning, acknowledge, correction, analytics, zone editing, thresholds, presentation mode).

## Bugs found and fixed during the build

- Time zero treated as "missing" (`seated_t or now`) gave 0-second visit durations. Replaced every such pattern with explicit `is None` checks.
- Lost guests were not re-linked on the exact frame a camera came back. Fixed by matching on "not seen in this frame".
- Alert IDs were random, so identical runs differed. Now a hash of run + dedup key.
- A guest returning with a new track ID was logged as a staff visit when the party left. Visits now require the party to still be there afterwards.
- e2e frontend on a non-default port was (correctly) refused by the WebSocket origin check; test config now allow-lists it.

## Not yet verified

Restaurant CCTV accuracy · physical webcams/RTSP · Apple Silicon/CoreML performance · multi-hour live stability.
