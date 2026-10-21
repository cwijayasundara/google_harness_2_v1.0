#!/usr/bin/env bash
# Set up everything the portfolio cockpit needs: Python env, Node packages,
# and the Docker images live runs execute in.
#
#   scripts/setup.sh            full setup
#   scripts/setup.sh --demo     also seed synthetic demo data in runs/portfolio-demo
#   scripts/setup.sh --no-docker  skip building container images
#
# Never writes credentials. Export FIREWORKS_API_KEY / ETORO_API_KEY in your
# own shell before starting the backend.
set -euo pipefail
cd "$(dirname "$0")/.."

DEMO=0
DOCKER=1
for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=1 ;;
    --no-docker) DOCKER=0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1 ($2)" >&2; exit 1; }; }

step "Checking prerequisites"
need uv "https://docs.astral.sh/uv/"
need node "Node >= 22.19 from https://nodejs.org"
need npm "ships with Node"
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if (a<22||(a===22&&b<19)) { console.error("Node >= 22.19 required, found "+process.versions.node); process.exit(1) }'
echo "uv $(uv --version | cut -d' ' -f2), node $(node --version)"

step "Installing Python dependencies (uv sync --frozen)"
uv sync --frozen

step "Installing Pi worker packages"
npm ci --prefix workers/pi

step "Installing cockpit UI packages"
npm ci --prefix ui

if [ "$DOCKER" = 1 ]; then
  need docker "Docker Desktop or another Docker daemon"
  if ! docker info >/dev/null 2>&1; then
    echo "Docker is installed but the daemon is not running. Start it, or re-run with --no-docker." >&2
    exit 1
  fi
  step "Building container images (executor + Pi)"
  docker compose -f containers/compose.yaml --profile build build
else
  echo "Skipping Docker images (--no-docker). Paid daily/evolve runs need them."
fi

mkdir -p runs/portfolio

if [ "$DEMO" = 1 ]; then
  step "Seeding synthetic demo data"
  uv run python scripts/seed_demo.py --base-dir runs/portfolio-demo
fi

step "Done"
cat <<MSG
Next:
  export FIREWORKS_API_KEY=...   # needed for paid daily/evolve runs
  export ETORO_API_KEY=...       # optional; otherwise fetch from a CSV
  scripts/start-backend.sh$( [ "$DEMO" = 1 ] && echo " --demo" )
  scripts/start-frontend.sh      # then open http://127.0.0.1:5173
MSG
