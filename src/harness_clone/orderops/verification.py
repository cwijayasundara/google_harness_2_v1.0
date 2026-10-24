"""Verify that a live pilot exercised refinement rather than silent fallbacks."""

import json
import sqlite3
from pathlib import Path


def verify_live_rsi(run_dir: Path) -> dict:
    database = Path(run_dir).resolve() / "run.sqlite"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        records = {(kind, key): json.loads(value)
                   for kind, key, value in db.execute("SELECT kind,id,value FROM records")}
    tasks = records.get(("orderops", "tasks"), {}).get("dev", [])
    if not tasks:
        raise RuntimeError("RSI verification requires development tasks")
    edits = {key: value for (kind, key), value in records.items() if kind == "edit"}
    verified = []
    reflections = 0
    for arm in ("local", "persistent"):
        for task in tasks:
            prefix = f"{arm}-{task['id']}/"
            stages = [value for key, value in edits.items() if key.startswith(prefix)]
            for scope in ("INV", "DOM", "COMPOSE"):
                matches = [value for value in stages if value["scope"] == scope]
                if not matches:
                    raise RuntimeError(f"RSI {prefix}{scope}: no editing stage was recorded")
                for stage in matches:
                    if stage["status"] not in {"accepted", "keep_base"}:
                        raise RuntimeError(
                            f"RSI {prefix}{scope}: editing fallback: {stage.get('error', '')}"
                        )
                    verified.append(stage)
            if arm == "persistent":
                reflection = records.get(("orderops-reflection", f"persistent/{task['id']}"), {})
                if "entry" not in reflection:
                    raise RuntimeError(f"RSI {prefix}: missing or failed reflection: {reflection}")
                reflections += 1
    return {
        "editing_stages": len(verified),
        "accepted_changes": sum(stage["status"] == "accepted" for stage in verified),
        "keep_base": sum(stage["status"] == "keep_base" for stage in verified),
        "reflections": reflections,
    }
