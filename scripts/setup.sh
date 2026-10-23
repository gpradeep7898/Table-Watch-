#!/usr/bin/env bash
# TableWatch AI setup. Safe to run repeatedly. Never touches files outside this project.
#   ./scripts/setup.sh            demo install (no ML packages, no downloads of models)
#   ./scripts/setup.sh --vision   also install OpenCV + ONNX Runtime for real video
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VISION=0
[[ "${1:-}" == "--vision" ]] && VISION=1

say() { printf "\033[1;36m==>\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31mxx\033[0m %s\n" "$*" >&2; exit 1; }

say "Checking prerequisites"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
[[ -n "$PY" ]] || fail "Python 3.11+ is required. On a Mac: brew install python@3.12"
command -v node >/dev/null 2>&1 || fail "Node.js 18+ is required. On a Mac: brew install node"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
(( NODE_MAJOR >= 18 )) || fail "Node.js 18+ is required (found $(node -v))"
echo "    python: $($PY --version)   node: $(node -v)"

say "Creating Python environment in backend/.venv"
[[ -d backend/.venv ]] || "$PY" -m venv backend/.venv
backend/.venv/bin/python -m pip install --quiet --upgrade pip
if (( VISION )); then
  backend/.venv/bin/pip install --quiet -e "backend[dev,vision]"
else
  backend/.venv/bin/pip install --quiet -e "backend[dev]"
fi

say "Installing frontend dependencies"
(cd frontend && npm install --no-audit --no-fund --silent)

say "Initialising the local database (existing data is kept)"
mkdir -p data media models demo/generated
backend/.venv/bin/python -m tablewatch.cli init-db

[[ -f .env ]] || { cp .env.example .env; echo "    created .env from .env.example"; }

say "Optional vision support"
if backend/.venv/bin/python -c 'import cv2, onnxruntime' 2>/dev/null; then
  echo "    OpenCV + ONNX Runtime installed."
  [[ -f models/yolox_nano.onnx ]] && echo "    Model present." || echo "    Model missing: run ./scripts/download-model.sh to enable real video."
else
  echo "    Not installed (demo mode does not need it). For real video: ./scripts/setup.sh --vision"
fi

say "Done. Start the demo with: ./scripts/start-demo.sh"
