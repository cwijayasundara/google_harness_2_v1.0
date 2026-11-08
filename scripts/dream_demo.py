"""Seed a run store with experiences and hand off to `harness-clone dream`.

No model calls and no spend: the script fabricates execution records and
experience entries that exercise every dream behavior — a duplicate pair, a
later correction, a verified single lesson, a broken evidence reference, a
secret-shaped lesson, and a superseded entry.

    uv run python scripts/dream_demo.py            # seeds runs/dream-demo, prints next step
    uv run harness-clone dream --run-dir runs/dream-demo --base-harness examples/base-harness
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness_clone.contracts import ExecutionRecord, ExperienceEntry
from harness_clone.experience import ExperienceLibrary
from harness_clone.storage import RunStore

BASE = Path("examples/base-harness")
RUN_DIR = Path("runs/dream-demo")


def main() -> None:
    store = RunStore(RUN_DIR)
    digest = store.publish_bundle(BASE)

    # Two execution records the lessons can cite; the dream re-resolves every
    # reference against these.
    for exec_id, trace in [
        ("w1/weight/agg1", [{"id": "t0", "type": "tool_call", "content": "sized positions"}]),
        ("w2/weight/agg2", [{"id": "t0", "type": "tool_call", "content": "reviewed order"}]),
    ]:
        store.save_execution(
            ExecutionRecord(
                execution_id=exec_id,
                task_id=exec_id.split("/")[0],
                bundle_digest=digest,
                delivered=True,
                trace=trace,
                artifacts={"recommendations.json": "{}"},
            )
        )
    good_refs = [
        "w1/weight/agg1:trace:t0",
        "w1/weight/agg1:artifact:recommendations.json",
    ]

    lib = ExperienceLibrary(store)
    rows = [
        # (task_id, ordinal, cue, lesson, refs) — duplicates share a cue+lesson.
        ("d1", 1, "position-sizes", "Cap any single position at 20% target weight", good_refs),
        ("d2", 2, "position-sizes", "Cap any single position at 20% target weight", good_refs),
        # A later correction of the pair above.
        ("d3", 3, "position-sizes", "Cap positions at 12%; the 0.4.0 review tightened it", good_refs),
        # A lone verified lesson.
        ("d4", 4, "fill-times", "Submit limit orders before 9:30; the opening auction fills late", good_refs),
        # Broken evidence reference: never restated, only noted.
        ("d5", 5, "stale-claim", "Legacy import format is still accepted", ["ghost:trace:0"]),
        # Secret-shaped lesson: kept in the store, dropped from the wiki.
        ("d6", 6, "credentials", "Use api_key=sk-demo1234567890abcd for the sandbox", good_refs),
    ]
    for task_id, ordinal, cue, lesson, refs in rows:
        lib.append(
            ExperienceEntry(
                namespace="ns",
                domain="portfolio",
                task_id=task_id,
                ordinal=ordinal,
                bundle_digest=digest,
                cue=cue,
                lesson=lesson,
                evidence_refs=refs,
            )
        )
    print(f"seeded {RUN_DIR} with 6 experiences under bundle {digest[:12]}")
    print("now run:")
    print(f"  uv run harness-clone dream --run-dir {RUN_DIR} --base-harness {BASE}")


if __name__ == "__main__":
    main()
