"""Local API behind the portfolio cockpit UI.

Read-mostly: it serves frozen snapshots, delivered recommendations, the
scoreboard, and evolution runs from the portfolio base directory, and can
launch the existing ``harness-clone portfolio`` commands as background jobs.
Recommendations only: there is no order-placement path here either. Keys stay
in the host environment; the API reports only whether they are configured.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .portfolio_ops import read_adopted

DEFAULT_BASE_DIR = Path("runs/portfolio")
DEFAULT_PORT = 8765
# A UI-launched paid run may never exceed this cap, whatever the user types.
MAX_UI_USD = Decimal("25")
LOG_TAIL_BYTES = 20_000
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]
PAID_COMMANDS = {"daily", "evolve"}
UPLOAD_DIR = ".ui-uploads"
JOB_DIR = ".ui-jobs"
DEMO_MARKER = ".demo"


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid date {value!r}") from None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _snapshot_dirs(base_dir: Path) -> list[tuple[date, Path]]:
    found = []
    if not base_dir.is_dir():
        return found
    for child in base_dir.iterdir():
        try:
            day = date.fromisoformat(child.name)
        except ValueError:
            continue
        if (child / "task.json").is_file():
            found.append((day, child))
    return sorted(found)


def _run_summary(run_dir: Path) -> dict:
    """Execution counts from a run store, opened read-only."""
    db = run_dir / "run.sqlite"
    summary = {"executions": 0, "delivered": 0, "base": None}
    if not db.is_file():
        return summary
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            rows = conn.execute("select kind, value from records").fetchall()
    except sqlite3.Error:
        return summary
    for kind, value in rows:
        try:
            data = json.loads(value)
        except ValueError:
            continue
        if kind == "execution":
            summary["executions"] += 1
            summary["delivered"] += bool(data.get("delivered"))
        elif kind == "stream" and isinstance(data, dict):
            summary["base"] = data.get("base")
    return summary


class JobRequest(BaseModel):
    command: Literal["fetch", "daily", "evolve", "score"]
    date: str | None = None
    max_usd: str | None = None
    confirm_paid: bool = False
    csv: str | None = Field(default=None, max_length=1_000_000)


class JobManager:
    """Runs one CLI command at a time as a subprocess, logging to a file."""

    def __init__(self, base_dir: Path, scoreboard: Path, argv_prefix: list[str] | None = None):
        self.base_dir, self.scoreboard = base_dir, scoreboard
        self.argv_prefix = argv_prefix or [sys.executable, "-m", "harness_clone.cli"]
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def running(self) -> dict | None:
        return next((j for j in self.jobs.values() if j["status"] == "running"), None)

    def argv(self, request: JobRequest, job_id: str) -> list[str]:
        args = ["portfolio", request.command, "--base-dir", str(self.base_dir)]
        if request.date:
            args += ["--date", _day(request.date).isoformat()]
        if request.command in PAID_COMMANDS:
            try:
                cap = Decimal(request.max_usd or "")
            except InvalidOperation:
                raise HTTPException(status_code=400, detail="max_usd must be a number") from None
            if not 0 < cap <= MAX_UI_USD:
                raise HTTPException(
                    status_code=400, detail=f"max_usd must be above 0 and at most {MAX_UI_USD}"
                )
            if not request.confirm_paid:
                raise HTTPException(status_code=400, detail="paid runs need confirm_paid")
            args += ["--max-usd", str(cap)]
        if request.csv is not None:
            if request.command not in {"fetch", "daily"}:
                raise HTTPException(status_code=400, detail="csv applies to fetch and daily only")
            upload = self.base_dir / UPLOAD_DIR / f"{job_id}.csv"
            upload.parent.mkdir(parents=True, exist_ok=True)
            upload.write_text(request.csv, encoding="utf-8")
            args += ["--csv", str(upload)]
        if request.command == "score":
            args += ["--scoreboard", str(self.scoreboard)]
        return [*self.argv_prefix, *args]

    def start(self, request: JobRequest) -> dict:
        with self.lock:
            if self.running():
                raise HTTPException(status_code=409, detail="another job is still running")
            if (self.base_dir / DEMO_MARKER).is_file():
                # Synthetic data: scoring would overwrite it, paid runs would spend on fake holdings.
                raise HTTPException(status_code=403, detail="demo data is read-only")
            job_id = uuid.uuid4().hex[:12]
            argv = self.argv(request, job_id)
            log = self.base_dir / JOB_DIR / f"{job_id}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            handle = log.open("wb")
            process = subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT)
            job = {
                "id": job_id,
                "command": request.command,
                "argv": argv[len(self.argv_prefix) :],
                "status": "running",
                "returncode": None,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "log": str(log),
            }
            self.jobs[job_id] = job

        def wait():
            code = process.wait()
            handle.close()
            with self.lock:
                job["returncode"] = code
                job["status"] = "succeeded" if code == 0 else "failed"
                job["finished_at"] = datetime.now(timezone.utc).isoformat()

        threading.Thread(target=wait, daemon=True).start()
        return self.view(job)

    def view(self, job: dict) -> dict:
        log = Path(job["log"])
        try:
            data = log.read_bytes()[-LOG_TAIL_BYTES:]
        except OSError:
            data = b""
        text = data.decode("utf-8", errors="replace")
        result = None
        if job["status"] != "running":
            # The CLI prints its JSON report last; surface it when present.
            start = text.find("{")
            if start != -1:
                try:
                    result = json.loads(text[start:])
                except ValueError:
                    result = None
        return {**{k: v for k, v in job.items() if k != "log"}, "log_tail": text, "result": result}


def create_app(
    base_dir: Path = DEFAULT_BASE_DIR,
    scoreboard: Path | None = None,
    argv_prefix: list[str] | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    base_dir = Path(base_dir)
    scoreboard = Path(scoreboard) if scoreboard else base_dir / "scoreboard.json"
    jobs = JobManager(base_dir, scoreboard, argv_prefix)
    app = FastAPI(title="Portfolio cockpit", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/status")
    def status():
        return {
            "base_dir": str(base_dir),
            "demo": (base_dir / DEMO_MARKER).is_file(),
            "adopted_bundle": read_adopted(base_dir),
            "docker": shutil.which("docker") is not None,
            "keys": {
                "fireworks": bool(os.environ.get("FIREWORKS_API_KEY")),
                "etoro": bool(os.environ.get("ETORO_API_KEY") and os.environ.get("ETORO_USER_KEY")),
            },
            "max_ui_usd": str(MAX_UI_USD),
            "job_running": (jobs.running() or {}).get("id"),
        }

    @app.get("/api/snapshots")
    def snapshots():
        out = []
        for day, folder in _snapshot_dirs(base_dir):
            portfolio = _read_json(folder / "source/portfolio.json") or {}
            recs = _read_json(folder / "recommendations.json")
            recs = recs if isinstance(recs, list) else None
            out.append(
                {
                    "date": day.isoformat(),
                    "week": _week(day),
                    "holdings": len(portfolio.get("positions", [])),
                    "proxy": bool(portfolio.get("proxy")),
                    "has_recommendations": recs is not None,
                    "non_hold_calls": sum(
                        1 for r in recs or [] if isinstance(r, dict) and r.get("action") != "hold"
                    ),
                }
            )
        return out

    @app.get("/api/snapshots/{day}")
    def snapshot(day: str):
        folder = base_dir / _day(day).isoformat()
        if not (folder / "task.json").is_file():
            raise HTTPException(status_code=404, detail=f"no snapshot for {day}")
        source = folder / "source"
        report = folder / "report.md"
        return {
            "date": folder.name,
            "portfolio": _read_json(source / "portfolio.json"),
            "market": _read_json(source / "market.json") or {},
            "candidates": _read_json(source / "candidates.json") or {},
            "prior_outcomes": _read_json(source / "prior_outcomes.json") or [],
            "recommendations": _read_json(folder / "recommendations.json"),
            "report": report.read_text(encoding="utf-8") if report.is_file() else None,
        }

    @app.get("/api/scoreboard")
    def get_scoreboard():
        return _read_json(scoreboard)

    @app.get("/api/evolution")
    def evolution():
        runs_root = base_dir / "runs"
        weeks = []
        if runs_root.is_dir():
            for run in sorted(runs_root.glob("evolve-*")):
                weeks.append({"week": run.name.removeprefix("evolve-"), **_run_summary(run)})
        archive = base_dir / "bundles"
        return {
            "adopted_bundle": read_adopted(base_dir),
            "archived_bundles": sorted(p.name for p in archive.iterdir())
            if archive.is_dir()
            else [],
            "weeks": weeks,
        }

    @app.get("/api/jobs")
    def list_jobs():
        return [jobs.view(j) for j in sorted(jobs.jobs.values(), key=lambda j: j["started_at"])]

    @app.post("/api/jobs")
    def start_job(request: JobRequest):
        return jobs.start(request)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return jobs.view(job)

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    return app


def main(argv=None):
    p = argparse.ArgumentParser(prog="harness-clone-ui")
    p.add_argument(
        "--base-dir",
        type=Path,
        default=Path(os.environ.get("HARNESS_UI_BASE_DIR", DEFAULT_BASE_DIR)),
    )
    p.add_argument("--scoreboard", type=Path, default=None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--static-dir", type=Path, default=Path("ui/dist"))
    args = p.parse_args(argv)
    if args.host not in LOOPBACK_HOSTS:
        # The API can launch paid runs; it must never listen beyond this machine.
        p.error("the cockpit API only binds to a loopback host")
    import uvicorn

    uvicorn.run(
        create_app(args.base_dir, args.scoreboard, static_dir=args.static_dir),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
