#!/usr/bin/env bash
# Start the cockpit UI dev server on http://127.0.0.1:5173.
# It proxies /api to the backend (COCKPIT_API, default http://127.0.0.1:8765),
# so start scripts/start-backend.sh first.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d ui/node_modules ] || npm ci --prefix ui

# Stop whatever is already listening on the UI port (e.g. a previous dev server), so we restart cleanly.
# The port is fixed by ui/vite.config.ts (strictPort).
UI_PORT=5173
PIDS="$(lsof -nP -t -iTCP:"$UI_PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$PIDS" ]; then
  echo "Port $UI_PORT is in use (pid $(echo $PIDS)); stopping it..."
  kill $PIDS 2>/dev/null || true
  for _ in $(seq 1 50); do
    lsof -nP -t -iTCP:"$UI_PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.1
  done
  PIDS="$(lsof -nP -t -iTCP:"$UI_PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$PIDS" ]; then
    echo "Still running after 5s; force-killing..."
    kill -9 $PIDS 2>/dev/null || true
    sleep 0.5
  fi
fi

if ! curl -fsS "${COCKPIT_API:-http://127.0.0.1:8765}/api/status" >/dev/null 2>&1; then
  echo "note: backend not reachable at ${COCKPIT_API:-http://127.0.0.1:8765}; run scripts/start-backend.sh"
fi
exec npm --prefix ui run dev
