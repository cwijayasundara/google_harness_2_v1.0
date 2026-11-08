import json
import sys
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness_clone.contracts import Candidate, MarketSummary, PortfolioPosition, PortfolioSnapshot
from harness_clone.portfolio_snapshot import build_snapshot_task
from harness_clone.ui_server import create_app, main

ECHO = [sys.executable, "-c", "import json, sys; print(json.dumps({'argv': sys.argv[1:]}))"]


def snapshot(base, day=date(2026, 10, 19), recs=None):
    portfolio = PortfolioSnapshot(
        fetched_at=f"{day.isoformat()}T09:00:00Z",
        currency="USD",
        cash_weight=0.1,
        positions=[
            PortfolioPosition(
                instrument="Acme Corp",
                ticker="ACME",
                asset_class="equity",
                units=10,
                open_rate=100,
                current_price=120,
                unrealized_pnl_pct=0.2,
                portfolio_weight=0.9,
            )
        ],
    )
    summary = MarketSummary(
        ticker="ACME",
        asset_class="equity",
        last_price=120,
        return_7d_pct=1.0,
        return_30d_pct=4.0,
        volatility_30d=0.2,
        high_52w_distance_pct=-3.0,
    )
    candidate = Candidate(
        ticker="NEWCO", asset_class="equity", score=0.7, metrics={"momentum": 1.0}
    )
    build_snapshot_task(base, day, portfolio, {"ACME": summary}, [candidate], 10, [])
    if recs is not None:
        (base / day.isoformat() / "recommendations.json").write_text(json.dumps(recs))
        (base / day.isoformat() / "report.md").write_text("# Report\n")


def client(tmp_path, **kwargs):
    return TestClient(create_app(tmp_path, argv_prefix=ECHO, **kwargs))


def wait(c, job_id):
    for _ in range(200):
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job never finished")


def test_status_reports_key_presence_without_values(tmp_path, monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw-secret-value")
    body = client(tmp_path).get("/api/status")
    assert body.json()["keys"] == {"fireworks": True}
    assert "fw-secret-value" not in body.text


def test_snapshots_list_and_detail(tmp_path):
    recs = [
        {"ticker": "ACME", "action": "trim", "target_weight_pct": 15},
        {"ticker": "ACME", "action": "hold", "target_weight_pct": 15},
    ]
    snapshot(tmp_path, recs=recs)
    snapshot(tmp_path, day=date(2026, 10, 20))
    c = client(tmp_path)
    listing = c.get("/api/snapshots").json()
    assert [s["date"] for s in listing] == ["2026-10-19", "2026-10-20"]
    assert listing[0] | {"week": "2026-W43"} == listing[0]
    assert listing[0]["non_hold_calls"] == 1 and listing[1]["has_recommendations"] is False
    detail = c.get("/api/snapshots/2026-10-19").json()
    assert detail["portfolio"]["positions"][0]["ticker"] == "ACME"
    assert detail["candidates"]["candidates"][0]["ticker"] == "NEWCO"
    assert detail["recommendations"] == recs and detail["report"] == "# Report\n"


@pytest.mark.parametrize("day", ["../etc", "2026-13-01", "nope"])
def test_snapshot_rejects_non_dates(tmp_path, day):
    assert client(tmp_path).get(f"/api/snapshots/{day}").status_code in {400, 404}


def test_missing_snapshot_is_404(tmp_path):
    assert client(tmp_path).get("/api/snapshots/2026-01-01").status_code == 404


def test_scoreboard_and_evolution_empty(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/scoreboard").json() is None
    assert c.get("/api/evolution").json() == {
        "adopted_bundle": None,
        "archived_bundles": [],
        "weeks": [],
    }


def test_free_job_runs_cli_with_expected_argv(tmp_path):
    c = client(tmp_path)
    job = c.post("/api/jobs", json={"command": "score", "date": "2026-10-20"}).json()
    done = wait(c, job["id"])
    assert done["status"] == "succeeded"
    assert done["result"]["argv"] == [
        "portfolio",
        "score",
        "--base-dir",
        str(tmp_path),
        "--date",
        "2026-10-20",
        "--scoreboard",
        str(tmp_path / "scoreboard.json"),
    ]


def test_fetch_csv_is_written_to_upload_dir(tmp_path):
    c = client(tmp_path)
    job = c.post("/api/jobs", json={"command": "fetch", "csv": "a,b\n1,2\n"}).json()
    argv = wait(c, job["id"])["result"]["argv"]
    upload = argv[argv.index("--csv") + 1]
    assert upload.startswith(str(tmp_path / ".ui-uploads"))
    assert open(upload).read() == "a,b\n1,2\n"


@pytest.mark.parametrize(
    "body",
    [
        {"command": "daily"},
        {"command": "daily", "max_usd": "2"},
        {"command": "daily", "max_usd": "0", "confirm_paid": True},
        {"command": "evolve", "max_usd": "26", "confirm_paid": True},
        {"command": "evolve", "max_usd": "lots", "confirm_paid": True},
        {"command": "score", "csv": "x"},
        {"command": "buy"},
    ],
)
def test_paid_and_invalid_jobs_are_refused(tmp_path, body):
    c = client(tmp_path)
    assert c.post("/api/jobs", json=body).status_code in {400, 422}
    assert c.get("/api/jobs").json() == []


def test_paid_job_passes_cap_through(tmp_path):
    c = client(tmp_path)
    body = {"command": "daily", "max_usd": "1.5", "confirm_paid": True}
    argv = wait(c, c.post("/api/jobs", json=body).json()["id"])["result"]["argv"]
    assert argv[argv.index("--max-usd") + 1] == "1.5"


def test_only_one_job_at_a_time(tmp_path):
    slow = [sys.executable, "-c", "import time; time.sleep(1)"]
    c = TestClient(create_app(tmp_path, argv_prefix=slow))
    first = c.post("/api/jobs", json={"command": "fetch"})
    assert first.status_code == 200
    assert c.post("/api/jobs", json={"command": "fetch"}).status_code == 409
    wait(c, first.json()["id"])


def test_demo_directory_refuses_jobs(tmp_path):
    (tmp_path / ".demo").write_text("synthetic")
    c = client(tmp_path)
    assert c.get("/api/status").json()["demo"] is True
    assert c.post("/api/jobs", json={"command": "score"}).status_code == 403
    assert c.get("/api/jobs").json() == []


def test_seeded_demo_recommendations_pass_the_validator(tmp_path):
    import importlib.util
    from pathlib import Path

    from harness_clone.portfolio_artifacts import validate_recommendations

    path = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_demo", path)
    seed_demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_demo)
    base = seed_demo.seed(tmp_path / "demo", date(2026, 10, 21), days=3)
    listing = client(base).get("/api/snapshots").json()
    assert len(listing) == 3 and all(s["has_recommendations"] for s in listing)
    for day in listing:
        folder = base / day["date"]
        portfolio = json.loads((folder / "source/portfolio.json").read_text())
        candidates = json.loads((folder / "source/candidates.json").read_text())
        allowed = {p["ticker"]: p["asset_class"] for p in portfolio["positions"]}
        allowed |= {c["ticker"]: c["asset_class"] for c in candidates["candidates"]}
        validate_recommendations((folder / "recommendations.json").read_text(), allowed)


def test_seed_refuses_to_replace_a_real_directory(tmp_path):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_demo", path)
    seed_demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_demo)
    (tmp_path / "real").mkdir()
    with pytest.raises(SystemExit):
        seed_demo.seed(tmp_path / "real", date(2026, 10, 21))


def test_server_refuses_non_loopback_host():
    with pytest.raises(SystemExit):
        main(["--host", "0.0.0.0"])


def _seed_experience_store(base):
    """A run store with one verified and one duplicate experience entry."""
    from harness_clone.contracts import ExecutionRecord, ExperienceEntry
    from harness_clone.experience import ExperienceLibrary
    from harness_clone.storage import RunStore

    base_harness = Path(__file__).parents[1] / "examples/base-harness"
    store = RunStore(base / "runs" / "daily-2026-10-19")
    digest = store.publish_bundle(base_harness)
    store.save_execution(
        ExecutionRecord(
            execution_id="d1/x/agg1",
            task_id="d1",
            bundle_digest=digest,
            delivered=True,
            trace=[{"id": "t0", "type": "tool_call", "content": "sized positions"}],
            artifacts={"recommendations.json": "{}"},
        )
    )
    lib = ExperienceLibrary(store)
    refs = ["d1/x/agg1:trace:t0", "d1/x/agg1:artifact:recommendations.json"]
    for ordinal, lesson in enumerate(
        ["Cap any single position at 20% target weight", "Cap any single position at 20% target weight"],
        start=1,
    ):
        lib.append(
            ExperienceEntry(
                namespace="ns",
                domain="portfolio",
                task_id=f"d{ordinal}",
                ordinal=ordinal,
                bundle_digest=digest,
                cue="position-sizes",
                lesson=lesson,
                evidence_refs=refs,
            )
        )
    return store, digest


def test_memory_wiki_and_dream_endpoints(tmp_path):
    from harness_clone.portfolio_ops import write_adopted

    base = tmp_path / "portfolio"
    store, digest = _seed_experience_store(base)
    write_adopted(base, digest, store)

    c = client(base)
    wiki = c.get("/api/memory").json()
    assert wiki["adopted_bundle"] == digest
    assert any(name.startswith("memory/") for name in wiki["files"])

    report = c.post("/api/dream", json={}).json()
    assert report["audit"]["entries"] == 2
    assert report["restated"] == 1 and report["merged"] == 1
    assert report["patch_digest"] and not report["adopted"]

    adopted_report = c.post("/api/dream", json={"adopt": True}).json()
    assert adopted_report["adopted"] is True
    served = c.get("/api/memory").json()
    assert served["adopted_bundle"] == adopted_report["patch_digest"]
    assert any("distilled offline" in text for text in served["files"].values())


def test_dream_with_no_experience_stores_is_404(tmp_path):
    response = client(tmp_path).post("/api/dream", json={})
    assert response.status_code == 404
    assert "no run store" in response.json()["detail"]
