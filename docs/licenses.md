# Third-party licences

Checked on 2026-10-23 from installed package metadata (`importlib.metadata`, `node_modules/*/package.json`, `license-checker` summary of frontend production dependencies). Re-check before any commercial release.

## Backend (runtime)

| Package | Licence |
|---|---|
| FastAPI | MIT |
| Starlette | BSD-3-Clause |
| Uvicorn (+ websockets) | BSD-3-Clause |
| Pydantic / pydantic-core | MIT |
| PyYAML | MIT |

## Backend (optional vision extra)

| Package | Licence | Note |
|---|---|---|
| numpy | BSD-3-Clause (+ bundled 0BSD/MIT/Zlib) | |
| opencv-python-headless | Apache-2.0 | Wheels bundle FFmpeg (LGPL-2.1). Dynamic linking inside the wheel; keep it as a separate, replaceable component and include the FFmpeg notice when distributing. |
| onnxruntime | MIT | |

## Model weights

| Asset | Licence | Note |
|---|---|---|
| YOLOX-Nano / YOLOX-Tiny ONNX (Megvii-BaseDetection/YOLOX release 0.1.1rc0) | Apache-2.0 (repository and release assets) | Not stored in git; downloaded explicitly by `scripts/download-model.sh` with a pinned sha256. Trained on COCO (annotations CC BY 4.0; images from Flickr under various licences). Commercial use of COCO-trained weights is common but get legal review. |

No Ultralytics/YOLOv5/v8/v11 code or weights (AGPL-3.0) are used. The tracker in `vision/tracker.py` is an independent implementation of the ByteTrack idea (paper: Zhang et al., 2022); no ByteTrack code is copied (ByteTrack itself is MIT).

## Frontend (runtime)

| Package | Licence |
|---|---|
| react, react-dom 18.3.1 | MIT |
| react-router-dom 6.28 | MIT |
| recharts 2.13 | MIT |
| lucide-react | ISC |

Transitive production dependencies: 33 MIT, 12 ISC, 2 BSD-3-Clause, 1 "MIT AND ISC". Nothing copyleft.

## Development only

Vite, Tailwind CSS, PostCSS, Autoprefixer, Vitest, Testing Library, jsdom (MIT) · TypeScript, Playwright (Apache-2.0) · pytest (MIT) · httpx2 (BSD-3-Clause).

## Assets

All icons come from Lucide (ISC). The logo, floor plan and synthetic camera render are original. No stock images, fonts or videos are bundled; the UI uses the system font stack. The public pedestrian clip used for local verification (`vtest.avi` from the OpenCV samples) is **not** committed.
