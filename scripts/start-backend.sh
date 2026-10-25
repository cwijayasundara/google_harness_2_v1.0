#!/usr/bin/env bash
# Start the cockpit API on 127.0.0.1 (loopback only; it can launch paid runs).
#
#   scripts/start-backend.sh                 serve runs/portfolio
#   scripts/start-backend.sh --demo          serve the synthetic runs/portfolio-demo
#   scripts/start-backend.sh --base-dir DIR  serve another portfolio base directory
#
# Environment: PORT (default 8765). Keys come from this shell or from .env.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/load-dotenv.sh
load_dotenv

BASE_DIR="${HARNESS_UI_BASE_DIR:-runs/portfolio}"
while [ $# -gt 0 ]; do
  case "$1" in
    --demo) BASE_DIR="runs/portfolio-demo"; shift ;;
    --base-dir) BASE_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ "$BASE_DIR" = "runs/portfolio-demo" ] && [ ! -f "$BASE_DIR/.demo" ]; then
  echo "Seeding synthetic demo data first..."
  uv run python scripts/seed_demo.py --base-dir "$BASE_DIR"
fi
mkdir -p "$BASE_DIR"

PORT="${PORT:-8765}"

# Stop whatever is already listening on the port (e.g. a previous backend), so we restart cleanly.
PIDS="$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$PIDS" ]; then
  echo "Port $PORT is in use (pid $(echo $PIDS)); stopping it..."
  kill $PIDS 2>/dev/null || true
  for _ in $(seq 1 50); do
    lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.1
  done
  PIDS="$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$PIDS" ]; then
    echo "Still running after 5s; force-killing..."
    kill -9 $PIDS 2>/dev/null || true
    sleep 0.5
  fi
fi

[ -n "${FIREWORKS_API_KEY:-}" ] || echo "note: FIREWORKS_API_KEY is not set in the shell or .env; paid runs will be disabled in the UI."
echo "Cockpit API → http://127.0.0.1:${PORT}  (serving $BASE_DIR)"
exec uv run python -m harness_clone.ui_server --base-dir "$BASE_DIR" --host 127.0.0.1 --port "$PORT"
