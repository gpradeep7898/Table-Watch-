#!/usr/bin/env bash
# Runs every automated test. No camera, no network, no model required.
#   ./scripts/test.sh          backend + frontend unit tests + type check
#   ./scripts/test.sh --e2e    also browser end-to-end tests (needs a Playwright Chromium)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "==> Backend tests (pytest)"
(cd backend && .venv/bin/python -m pytest)
echo "==> Frontend type check"
(cd frontend && npx tsc -p . --noEmit)
echo "==> Frontend unit tests (vitest)"
(cd frontend && npx vitest run)
if [[ "${1:-}" == "--e2e" ]]; then
  echo "==> Browser end-to-end tests (playwright)"
  (cd frontend && npx playwright test)
fi
echo "All selected test suites passed."
