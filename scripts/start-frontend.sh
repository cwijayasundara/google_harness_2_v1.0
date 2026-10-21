#!/usr/bin/env bash
# Start the cockpit UI dev server on http://127.0.0.1:5173.
# It proxies /api to the backend (COCKPIT_API, default http://127.0.0.1:8765),
# so start scripts/start-backend.sh first.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d ui/node_modules ] || npm ci --prefix ui
if ! curl -fsS "${COCKPIT_API:-http://127.0.0.1:8765}/api/status" >/dev/null 2>&1; then
  echo "note: backend not reachable at ${COCKPIT_API:-http://127.0.0.1:8765}; run scripts/start-backend.sh"
fi
exec npm --prefix ui run dev
