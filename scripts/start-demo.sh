#!/usr/bin/env bash
# Start the backend (127.0.0.1:8000) and the dashboard (127.0.0.1:5173). Ctrl+C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -x backend/.venv/bin/python && -d frontend/node_modules ]] || { echo "Run ./scripts/setup.sh first."; exit 1; }

PIDS=()
cleanup() {
  echo; echo "Stopping TableWatch…"
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

export TABLEWATCH_HOST="${TABLEWATCH_HOST:-127.0.0.1}"
backend/.venv/bin/python -m tablewatch.cli serve &
PIDS+=($!)
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:8000/api/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:8000/api/health" >/dev/null 2>&1 || { echo "Backend did not start. See the log above."; exit 1; }

(cd frontend && npx vite --host 127.0.0.1 --port 5173 --strictPort --logLevel warn) &
PIDS+=($!)
sleep 2
cat <<MSG

  TableWatch AI is running (SIMULATION mode, no camera needed).

    Dashboard:          http://127.0.0.1:5173
    Presentation mode:  http://127.0.0.1:5173/demo
    API docs:           http://127.0.0.1:8000/api/docs

  Everything runs on this computer. Press Ctrl+C to stop.

MSG
wait
