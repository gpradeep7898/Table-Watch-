#!/usr/bin/env bash
# Start the backend and the dashboard on 127.0.0.1. Ctrl+C stops both.
# If the default ports (8000 / 5173) are taken by another program, the next
# free ports are used automatically.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -x backend/.venv/bin/python && -d frontend/node_modules ]] || { echo "Run ./scripts/setup.sh first."; exit 1; }

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
free_port() {
  local p=$1
  while port_in_use "$p"; do p=$((p + 1)); done
  echo "$p"
}

API_PORT="$(free_port "${TABLEWATCH_PORT:-8000}")"
UI_PORT="$(free_port 5173)"
[[ "$API_PORT" != "${TABLEWATCH_PORT:-8000}" ]] && echo "Port ${TABLEWATCH_PORT:-8000} is used by another program; TableWatch API will use $API_PORT."
[[ "$UI_PORT" != "5173" ]] && echo "Port 5173 is used by another program; the dashboard will use $UI_PORT."

PIDS=()
cleanup() {
  trap - INT TERM EXIT
  echo; echo "Stopping TableWatch…"
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

export TABLEWATCH_HOST="${TABLEWATCH_HOST:-127.0.0.1}"
export TABLEWATCH_PORT="$API_PORT"
export TABLEWATCH_ALLOWED_ORIGINS="http://127.0.0.1:$UI_PORT,http://localhost:$UI_PORT,http://127.0.0.1:$API_PORT,http://localhost:$API_PORT"
backend/.venv/bin/python -m tablewatch.cli serve &
BACKEND=$!
PIDS+=("$BACKEND")

# Wait until *our* backend answers (not some other app on the same port).
ok=0
for _ in $(seq 1 90); do
  if ! kill -0 "$BACKEND" 2>/dev/null; then echo "The TableWatch backend exited during startup. See the error above."; exit 1; fi
  if curl -sf "http://127.0.0.1:$API_PORT/api/health" 2>/dev/null | grep -q '"app": *"tablewatch"'; then ok=1; break; fi
  sleep 0.5
done
(( ok )) || { echo "The TableWatch backend did not become ready on port $API_PORT."; exit 1; }

(cd frontend && TABLEWATCH_API="http://127.0.0.1:$API_PORT" npx vite --host 127.0.0.1 --port "$UI_PORT" --strictPort --logLevel warn) &
PIDS+=($!)
sleep 2
cat <<MSG

  TableWatch AI is running (SIMULATION mode, no camera needed).

    Dashboard:          http://127.0.0.1:$UI_PORT
    Presentation mode:  http://127.0.0.1:$UI_PORT/demo
    API docs:           http://127.0.0.1:$API_PORT/api/docs

  Everything runs on this computer. Press Ctrl+C to stop.

MSG
wait
