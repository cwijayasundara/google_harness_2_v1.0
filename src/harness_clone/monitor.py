"""Live, read-only HTML view of a run directory.

Reads ``run.sqlite`` and ``budget.sqlite`` without taking write locks, so it can
watch a run in progress (or a stopped one) from this or another process.
"""

import json
import sqlite3
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOOPBACK = "127.0.0.1"
TEXT_LIMIT = 1_500
PAGE = Path(__file__).with_name("monitor.html")


def _rows(db: Path, sql: str) -> list[tuple]:
    if not db.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2) as conn:
            return conn.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def _clip(value, limit: int = TEXT_LIMIT):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


def _trace(messages) -> list[dict]:
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        out.append(
            {
                "type": m.get("type"),
                "content": _clip(m.get("content", "")),
                "tool_calls": [
                    {"name": c.get("name"), "args": _clip(c.get("args", {}), 400)}
                    for c in m.get("tool_calls") or []
                ],
            }
        )
    return out


def _response(record_id: str, value: dict) -> dict:
    message = value.get("message") or {}
    meta = message.get("response_metadata") or {}
    usage = meta.get("token_usage") or {}
    return {
        "id": record_id,
        "finish_reason": value.get("finish_reason") or meta.get("finish_reason"),
        "reasoning": _clip((message.get("additional_kwargs") or {}).get("reasoning_content", "")),
        "content": _clip(message.get("content", "")),
        "tool_calls": [
            {"name": c.get("name"), "args": _clip(c.get("args", {}), 600)}
            for c in message.get("tool_calls") or []
        ],
        "tokens": {
            "prompt": usage.get("prompt_tokens"),
            "completion": usage.get("completion_tokens"),
        },
    }


def _budget(db: Path) -> dict:
    spent = 0.0
    unknown = False
    stages = {}
    for row_id, ceiling, usage in _rows(db, "select id, ceiling, usage from budget"):
        if not row_id.startswith("alloc:"):
            try:
                parsed = json.loads(usage or "{}")
                spent += float(parsed.get("usd", 0))
                unknown = unknown or parsed.get("known") is False
            except ValueError:
                pass
            continue
        label = row_id.removeprefix("alloc:").rsplit("/", 1)[-1]
        stages[label] = {"reserved": ceiling, "settled": bool(usage)}
    return {"spent_usd": round(spent, 6), "unknown": unknown, "planned": stages}


def snapshot(run_dir: Path) -> dict:
    db = run_dir / "run.sqlite"
    records: dict[str, list[tuple[str, dict]]] = {}
    for kind, record_id, value in _rows(db, "select kind, id, value from records"):
        try:
            records.setdefault(kind, []).append((record_id, json.loads(value)))
        except ValueError:
            continue
    config = dict(records.get("experiment", [(None, {})])[0][1])
    executions = [
        {
            "label": rid.rsplit("/", 1)[-1],
            "status": v.get("status"),
            "delivered": v.get("delivered"),
            "error": v.get("error"),
            "artifacts": {k: _clip(a, 400) for k, a in (v.get("artifacts") or {}).items()},
            "trace": _trace(v.get("trace")),
        }
        for rid, v in records.get("execution", [])
    ]
    edits = [
        {k: v.get(k) for k in ("label", "scope", "status", "attempts", "error", "rationale")}
        for _, v in records.get("edit", [])
    ]
    responses = [_response(rid, v) for rid, v in sorted(records.get("editor-response", []))]
    budget = _budget(run_dir / "budget.sqlite")
    gateway_errors = [
        {"stage": v.get("stage_id"), "reason": v.get("reason")}
        for _, v in (
            (rid, json.loads(val))
            for kind, rid, val in _rows(
                run_dir / "budget.sqlite", "select kind, id, value from records"
            )
            if kind == "gateway-error"
        )
    ]
    done = {e["label"] for e in executions} | {e["label"] for e in edits}
    pipeline = [
        {"label": label, "state": "done" if label in done else "pending", **info}
        for label, info in budget["planned"].items()
    ]
    mtimes = [p.stat().st_mtime for p in run_dir.glob("*.sqlite*") if p.is_file()]
    return {
        "run_dir": str(run_dir),
        "now": time.time(),
        "last_write": max(mtimes, default=None),
        "config": {k: config.get(k) for k in ("mode", "k", "profile", "editor_policy", "max_usd")},
        "budget": {"spent_usd": budget["spent_usd"], "cap": config.get("max_usd")},
        "gateway_errors": gateway_errors,
        "unknown_usage": budget["unknown"],
        "pipeline": pipeline,
        "executions": executions,
        "edits": edits,
        "editor_responses": responses,
        "finished": bool(
            records.get("runtime") and any(r[0] == "results" for r in records["runtime"])
        ),
    }


def make_server(run_dir: Path, port: int = 0) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/snapshot"):
                body, kind = json.dumps(snapshot(run_dir), default=str).encode(), "application/json"
            elif self.path in {"/", "/index.html"}:
                body, kind = PAGE.read_bytes(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((LOOPBACK, port), Handler)


def start_background(run_dir: Path, open_browser: bool = True) -> str:
    """Serve the monitor on a free loopback port from a daemon thread; return its URL."""
    server = make_server(run_dir)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://{LOOPBACK}:{server.server_address[1]}/"
    if open_browser:
        webbrowser.open(url)
    return url


def serve_forever(run_dir: Path, port: int, open_browser: bool = True) -> None:
    server = make_server(run_dir, port)
    url = f"http://{LOOPBACK}:{server.server_address[1]}/"
    print(f"harness-clone: watching {run_dir} at {url} (Ctrl-C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
