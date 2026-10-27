"""Generate the ledger task family: four training tasks and one held-out task.

Every task asks for the same answer.json. The ledger follows four arbitrary
house rules that nobody can guess. Each rule is stated in exactly one training
task's NOTES.txt, so that task is solvable from its own notes. The held-out
task H1 says only "apply the house rules": a harness passes it only if earlier
tasks taught it (memory, skills, guardrails) what the rules are.

    uv run python scripts/make_ledger_family.py --tasks-dir examples/tasks/ledger \
        --private-dir runs/ledger/private
"""

import argparse
import json
import random
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

PROMPT = "Summarize the ledger into answer.json."
CONTRACT = (
    "answer.json must be a JSON object with exactly these keys:\n"
    '  "total_cents": integer, in USD cents, after applying the house rules\n'
    '  "rows": integer, number of rows that were counted\n'
    '  "latest_date": "YYYY-MM-DD" of the latest counted row\n'
)
FX = {"USD": Decimal("1"), "EUR": Decimal("1.0825"), "GBP": Decimal("1.2740")}
ORDER = ["L1", "L2", "L3", "L4", "H1"]

RULE_A = "House rule A: rows with status TEST are never counted."
RULE_B = "House rule B: rows with status HOLD count at half value, rounded half-up per row."
RULE_C = (
    "House rule C: a row worth under 5.00 USD after conversion is dust and is not counted. "
    "Convert with fx.csv and round each row to whole cents half-up before judging or summing."
)
RULE_D = "House rule D: vendor Globex writes dates as DD/MM/YYYY. Report latest_date in ISO form."


def cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def make_rows(rng, n, *, currencies=("USD",)):
    return [
        {
            "txn_id": f"T{i:04d}",
            "date": f"2026-0{rng.randint(1, 9)}-{rng.randint(10, 28)}",
            "currency": rng.choice(currencies),
            "amount": Decimal(rng.randint(100, 99999)) / 100,
            "status": rng.choice(["OK", "OK", "OK", "TEST", "HOLD"]),
        }
        for i in range(n)
    ]


def write_csv(path, rows, *, comma_decimal=False, dmy=False):
    lines = ["txn_id,date,currency,amount,status"]
    for r in rows:
        amount = f"{r['amount']:.2f}"
        date = r["date"]
        if dmy:
            y, m, d = date.split("-")
            date = f"{d}/{m}/{y}"
        if comma_decimal:
            amount = '"' + amount.replace(".", ",") + '"'
        lines.append(f"{r['txn_id']},{date},{r['currency']},{amount},{r['status']}")
    path.write_text("\n".join(lines) + "\n")


def answer(rows, *, rules):
    """Apply the active house rules; rules is a subset of {"A", "B", "C"}."""
    total, kept = 0, []
    for r in rows:
        if "A" in rules and r["status"] == "TEST":
            continue
        value = r["amount"] * (FX[r["currency"]] if "C" in rules else 1)
        value = Decimal(cents(value)) / 100
        if "B" in rules and r["status"] == "HOLD":
            value = Decimal(cents(value / 2)) / 100
        if "C" in rules and value < 5:
            continue
        total += cents(value)
        kept.append(r)
    return {
        "total_cents": total,
        "rows": len(kept),
        "latest_date": max(r["date"] for r in kept),
    }


def task(root, tid, rows, notes, *, extra=None, **csv_kw):
    src = root / tid / "source"
    src.mkdir(parents=True)
    write_csv(src / "ledger.csv", rows, **csv_kw)
    (src / "NOTES.txt").write_text(CONTRACT + "\n" + notes)
    for name, body in (extra or {}).items():
        (src / name).write_text(body)
    spec = {
        "id": tid,
        "domain": "ledger-summary",
        "prompt": PROMPT,
        "source_root": "source",
        "output_paths": ["answer.json"],
        "ordinal": ORDER.index(tid),
    }
    (root / tid / "task.json").write_text(json.dumps(spec) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-dir", type=Path, required=True)
    ap.add_argument("--private-dir", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    args.private_dir.mkdir(parents=True)
    expected = {}
    fx_csv = "currency,usd\n" + "\n".join(f"{c},{v}" for c, v in FX.items()) + "\n"

    rows = make_rows(rng, 14)
    task(args.tasks_dir, "L1", rows, RULE_A + "\n")
    expected["L1"] = answer(rows, rules={"A"})

    rows = make_rows(rng, 30)
    task(
        args.tasks_dir,
        "L2",
        rows,
        RULE_B + "\nAmounts use a decimal comma inside quotes, e.g. \"12,50\".\n"
        "Rule A also applies here: " + RULE_A.split(": ", 1)[1] + "\n",
        comma_decimal=True,
    )
    expected["L2"] = answer(rows, rules={"A", "B"})

    rows = make_rows(rng, 200, currencies=tuple(FX))
    task(
        args.tasks_dir,
        "L3",
        rows,
        RULE_C + "\nRules A and B still apply.\n",
        extra={"fx.csv": fx_csv},
    )
    expected["L3"] = answer(rows, rules={"A", "B", "C"})

    rows = make_rows(rng, 24, currencies=tuple(FX))
    task(
        args.tasks_dir,
        "L4",
        rows,
        RULE_D + "\nRules A, B and C still apply.\n",
        extra={"fx.csv": fx_csv},
        dmy=True,
    )
    expected["L4"] = answer(rows, rules={"A", "B", "C"})

    rows = make_rows(rng, 60, currencies=tuple(FX))
    task(
        args.tasks_dir,
        "H1",
        rows,
        "From vendor Globex. Apply the house rules.\n",
        extra={"fx.csv": fx_csv},
        dmy=True,
        comma_decimal=True,
    )
    expected["H1"] = answer(rows, rules={"A", "B", "C"})

    (args.private_dir / "expectations.json").write_text(
        json.dumps({k: {"answer.json": v} for k, v in expected.items()}, indent=2)
    )
    print(f"wrote {len(expected)} tasks to {args.tasks_dir}")


if __name__ == "__main__":
    main()
