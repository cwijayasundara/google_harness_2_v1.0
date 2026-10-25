#!/usr/bin/env bash
# One entry point for dependency setup, app checks, and harness RSI verification.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source scripts/load-dotenv.sh
load_dotenv

usage() {
  cat <<'HELP'
Usage: scripts/setup-and-test.sh [options]
  (no options)           Install dependencies and run all free app/RSI checks
  --docker               Also build images and run Docker isolation tests
  --live --max-usd N     Also run a paid OrderOps pilot (implies --docker)
  --skip-setup           Reuse installed Python and Node dependencies
  -h, --help             Show this help

Requires uv, Python 3.12 (managed by uv), Node >=22.19, and npm.
Live mode also requires Docker and FIREWORKS_API_KEY in the shell or in .env.
N is the total experiment cap; the script makes no separate paid doctor call.
Logs and artifacts are saved in a fresh runs/setup-test.XXXXXX directory.
The live pilot uses 2 development cases, 1 held-out case, and all four arms.
Pilot scores are descriptive and do not establish a quality improvement.
HELP
}
die() { echo "error: $*" >&2; exit 2; }
DOCKER=0 LIVE=0 SETUP=1 MAX_USD=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --docker) DOCKER=1; shift ;;
    --live) LIVE=1; DOCKER=1; shift ;;
    --skip-setup) SETUP=0; shift ;;
    --max-usd)
      [ "$#" -ge 2 ] || die "--max-usd needs a positive decimal amount"
      MAX_USD="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
if [ "$LIVE" = 1 ]; then
  [[ "$MAX_USD" =~ ^[0-9]+([.][0-9]+)?$ ]] && [ -n "${MAX_USD//[0.]/}" ] ||
    die "--live requires --max-usd with a positive finite decimal amount"
  [ -n "${FIREWORKS_API_KEY:-}" ] || die "set FIREWORKS_API_KEY in the shell or in .env before using --live"
elif [ -n "$MAX_USD" ]; then
  die "--max-usd requires --live"
fi
for tool in uv node npm; do
  command -v "$tool" >/dev/null 2>&1 || die "missing required tool: $tool"
done
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22||(a===22&&b<19)){console.error("Node >=22.19 required");process.exit(2)}'
if [ "$DOCKER" = 1 ]; then
  command -v docker >/dev/null 2>&1 || die "Docker is required for --docker/--live"
  docker info >/dev/null 2>&1 || die "start the Docker daemon first"
  docker compose version >/dev/null 2>&1 || die "Docker Compose is required"
fi

mkdir -p runs
REPORT_DIR="$(mktemp -d "$PWD/runs/setup-test.XXXXXX")"
CURRENT_STEP="initialization"
trap 'code=$?; if [ "$code" -ne 0 ]; then printf "\nFAIL: %s (exit %s). Reports: %s\n" "$CURRENT_STEP" "$code" "$REPORT_DIR" >&2; fi' EXIT
run_step() {
  CURRENT_STEP="$1"
  shift
  printf '\n==> %s\n' "$CURRENT_STEP"
  "$@" 2>&1 | tee "$REPORT_DIR/$CURRENT_STEP.log"
  printf 'PASS: %s\n' "$CURRENT_STEP"
}
if [ "$SETUP" = 1 ]; then
  run_step python-setup uv sync --frozen
  run_step pi-setup npm ci --prefix workers/pi
  run_step ui-setup npm ci --prefix ui
fi
run_step lint uv run --frozen ruff check .
# Explicitly deselect Docker tests here even if the caller exported their flag.
run_step python-tests uv run --frozen pytest -q -m 'not docker'
run_step pi-tests npm test --prefix workers/pi
run_step pi-build npm run build --prefix workers/pi
run_step ui-build npm run build --prefix ui
run_step app-and-rsi uv run --frozen python - "$REPORT_DIR" <<'PY'
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from harness_clone.ui_server import create_app

root = Path(sys.argv[1])


def cli(name, *args):
    reply = subprocess.run(
        [sys.executable, "-m", "harness_clone.cli", *map(str, args)],
        capture_output=True, text=True,
    )
    (root / f"{name}.json").write_text(reply.stdout)
    (root / f"{name}.stderr.log").write_text(reply.stderr)
    if reply.returncode:
        raise RuntimeError(f"{name} failed ({reply.returncode}): {reply.stderr}")
    return json.loads(reply.stdout)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


require(cli("doctor", "doctor")["status"] == "offline-pass", "offline doctor failed")
expectations = root / "expectations.json"
expectations.write_text(json.dumps({
    "demo-a": {"answer.json": {"count": 3, "total": 10}},
    "demo-b": {"answer.json": {"count": 3, "total": 18}},
}))
for mode in ("parallel", "sequential"):
    run = root / f"demo-{mode}"
    initial = cli(mode, "demo", "--run-dir", run, "--mode", mode, "--k", "2")
    require(initial["tasks"] == 2 and initial["executions"] == 16
            and initial["editing_stages"] == 12 and not initial["errors"],
            f"{mode}: unexpected refinement schedule or errors")
    resumed = cli(f"{mode}-resume", "resume", "--run-dir", run)
    require(resumed == initial, f"{mode}: resume changed completed results")
    inspected = cli(f"{mode}-inspect", "inspect", "--run-dir", run)
    require(not inspected["errors"] and not inspected["budget"]["unknown"],
            f"{mode}: inspection errors or unknown spend")
    scored = cli(f"{mode}-evaluation", "evaluate", "--run-dir", run,
                 "--expectations", expectations, "--output", root / f"{mode}-scores.json")
    require(scored["passed"] == 2 and scored["coverage"] == 1,
            f"{mode}: incorrect or missing selected answers")
    print(f"PASS: {mode} refinement, resume, inspection, and offline scoring", flush=True)

cli("orderops-generate", "orderops", "generate", "--tasks-dir", root / "tasks",
    "--private-dir", root / "private", "--dev-count", "2", "--holdout-count", "1")

# Serve the built UI and real API on an OS-assigned loopback port. Own and stop
# only this server; existing developer servers and their ports are untouched.
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(
        create_app(root / "app", static_dir=Path("ui/dist")), log_level="warning",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        require(server.started, "API did not start within 15 seconds")
        with httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
                          timeout=5, trust_env=False) as client:
            status = client.get("/api/status")
            status.raise_for_status()
            require(status.json()["base_dir"] == str(root / "app"), "wrong API base directory")
            (root / "api-status.json").write_text(json.dumps(status.json(), indent=2))
            page = client.get("/")
            page.raise_for_status()
            require('id="root"' in page.text, "built React page was not served")
            import re
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
            require(bool(assets), "built UI has no assets")
            for asset in assets:
                client.get(asset).raise_for_status()
        print("PASS: HTTP API, built UI page, and UI assets", flush=True)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        require(not thread.is_alive(), "API did not shut down")
PY

if [ "$DOCKER" = 1 ]; then
  run_step docker-build docker compose -f containers/compose.yaml --profile build build
  run_step docker-tests env HARNESS_TEST_DOCKER=1 uv run --frozen pytest -q -m docker
else
  echo "SKIP: Docker isolation checks (enable with --docker)"
fi
if [ "$LIVE" = 1 ]; then
  run_step live-orderops uv run --frozen harness-clone orderops run \
    --tasks-dir "$REPORT_DIR/tasks" --run-dir "$REPORT_DIR/experiment" \
    --max-usd "$MAX_USD" --editor-policy pi --k 1
  run_step live-evaluation uv run --frozen harness-clone orderops evaluate \
    --run-dir "$REPORT_DIR/experiment" --answers "$REPORT_DIR/private/answers.json" \
    --output "$REPORT_DIR/live-scores.json"
  run_step live-delivery-check uv run --frozen python - "$REPORT_DIR/live-scores.json" "$REPORT_DIR/experiment" <<'PY'
import json
import sys
from pathlib import Path

from harness_clone.orderops.verification import verify_live_rsi

report = json.loads(Path(sys.argv[1]).read_text())
if report["budget"]["unknown"]:
    raise SystemExit("FAIL: live spending is unknown; inspect the experiment budget")
for name, arm in report["arms"].items():
    for split in ("development", "heldout"):
        result = arm[split]
        print(f"{name}/{split}: success={result['success_rate']:.1%}, "
              f"delivery={result['delivery_coverage']:.1%}", flush=True)
        if result["delivery_coverage"] != 1:
            raise SystemExit(f"FAIL: {name}/{split} has missing deliveries; inspect live-scores.json")
rsi = verify_live_rsi(Path(sys.argv[2]))
(Path(sys.argv[1]).parent / "live-rsi-checks.json").write_text(json.dumps(rsi, indent=2))
print(f"PASS: live RSI stages and reflections: {rsi}")
print("PASS: live delivery and spending checks; quality scores are descriptive")
PY
  echo "Live pilot completed; review live-scores.json for delivery coverage and quality."
else
  echo "SKIP: Paid RSI pilot (enable with --live --max-usd N)"
fi
printf '\nPASS: all requested checks. Reports: %s\n' "$REPORT_DIR"
