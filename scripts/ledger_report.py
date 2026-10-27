"""Compare base-harness and evolved-harness outputs per ledger task.

    uv run python scripts/ledger_report.py runs/ledger/exp3 runs/ledger/private/expectations.json
"""

import json
import sqlite3
import sys
from pathlib import Path


def passed(raw, expected):
    try:
        return json.loads(raw) == expected
    except (TypeError, ValueError):
        return False


def main(run_dir, expectations):
    db = sqlite3.connect(Path(run_dir) / "run.sqlite")
    expected = json.loads(Path(expectations).read_text())
    runs = {}
    for _id, value in db.execute("select id, value from records where kind='execution'"):
        record = json.loads(value)
        runs[record["execution_id"]] = record
    results = json.loads(db.execute("select value from records where id='results'").fetchone()[0])
    print(f"{'task':5} {'base':6} {'evolved':8} edits")
    for result in results:
        task = result["task_id"]
        want = expected[task]["answer.json"]
        base = next(r for r in result["executions"].values() if r["execution_id"].endswith("base1"))
        chosen = runs.get(result["selection"]["chosen_execution_id"])
        edits = [
            json.loads(v)["status"]
            for _i, v in db.execute(
                "select id, value from records where kind='edit' and id like ? order by rowid",
                (f"{task}/%",),
            )
        ]
        print(
            f"{task:5} {'PASS' if passed(base['artifacts'].get('answer.json'), want) else 'fail':6}"
            f" {'PASS' if chosen and passed(chosen['artifacts'].get('answer.json'), want) else 'fail':8}"
            f" {','.join(edits)}"
        )


if __name__ == "__main__":
    main(*sys.argv[1:3])
