import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BUNDLE = ROOT / "examples/portfolio-harness"


def load_math():
    # Load by path without writing __pycache__ into the bundle (the bundle
    # validator rejects off-surface files).
    path = BUNDLE / "scripts/portfolio_math.py"
    spec = importlib.util.spec_from_file_location("portfolio_math", path)
    mod = importlib.util.module_from_spec(spec)
    code = compile(path.read_text(), str(path), "exec")
    exec(code, mod.__dict__)
    return mod


def rec(**over):
    base = {
        "ticker": "AAPL",
        "asset_class": "equity",
        "action": "add",
        "target_weight_pct": 10.0,
        "horizon_days": 30,
        "thesis": "growth",
        "invalidation": "falls below X",
        "benchmark": "SPY",
        "evidence_refs": ["market.json:AAPL"],
    }
    base.update(over)
    return base


def rec_json(**over):
    return json.dumps([rec(**over)])


def rec_json_many(n, action="add"):
    return json.dumps([rec(ticker="AAPL", action=action) for _ in range(n)])


# --- validator ---
def test_validator_accepts_valid_and_benchmark_tickers():
    from harness_clone.portfolio_artifacts import validate_recommendations

    out = validate_recommendations(rec_json(), {"AAPL"})
    assert out[0].ticker == "AAPL" and out[0].action == "add"
    for t in ("SPY", "QQQ", "BTC", "ETH"):
        assert validate_recommendations(rec_json(ticker=t), {"AAPL"})[0].ticker == t


def test_validator_rejects_unknown_ticker():
    from harness_clone.portfolio_artifacts import validate_recommendations

    with pytest.raises(ValueError, match="ticker"):
        validate_recommendations(rec_json(ticker="NOTREAL"), {"AAPL"})


def test_validator_rejects_six_active_calls():
    from harness_clone.portfolio_artifacts import validate_recommendations

    with pytest.raises(ValueError, match="5"):
        validate_recommendations(rec_json_many(6, action="add"), {"AAPL"})
    # Six holds are fine.
    assert len(validate_recommendations(rec_json_many(6, action="hold"), {"AAPL"})) == 6


def test_validator_rejects_overweight_and_bare_calls():
    from harness_clone.portfolio_artifacts import validate_recommendations

    with pytest.raises(ValueError, match="20"):
        validate_recommendations(rec_json(target_weight_pct=25.0), {"AAPL"})
    with pytest.raises(ValueError, match="invalidation"):
        validate_recommendations(rec_json(invalidation=""), {"AAPL"})
    with pytest.raises(ValueError, match="thesis"):
        validate_recommendations(rec_json(thesis="  "), {"AAPL"})
    with pytest.raises(ValueError, match="evidence_refs"):
        validate_recommendations(rec_json(evidence_refs=[]), {"AAPL"})


def test_validator_rejects_malformed():
    from harness_clone.portfolio_artifacts import validate_recommendations

    with pytest.raises(ValueError):
        validate_recommendations("not json", {"AAPL"})
    with pytest.raises(ValueError):
        validate_recommendations('{"ticker":"AAPL"}', {"AAPL"})
    with pytest.raises(ValueError):
        validate_recommendations(rec_json(action="buy"), {"AAPL"})


def test_recommendation_contract_shape():
    from harness_clone.contracts import Recommendation

    r = Recommendation(**rec())
    assert r.target_weight_pct == 10.0


# --- bundle ---
def test_portfolio_bundle_validates():
    from harness_clone.bundles import validate_bundle

    manifest = validate_bundle(BUNDLE)
    assert set(manifest.components) == {
        "system", "guardrails", "memory", "tools",
        "subagents", "skills", "scripts", "middleware",
    }


def test_guardrails_verbatim_limits():
    text = (BUNDLE / "guardrails.md").read_text()
    for needle in (
        "max 20% single-position target weight",
        "max 5 non-hold calls/day",
        "recommendations only (no trade execution)",
        "research-only, not financial advice",
    ):
        assert needle in text, needle


def test_bundle_subagents_and_skills():
    for name in ("bull", "bear", "risk_reviewer"):
        data = json.loads((BUNDLE / f"subagents/{name}.json").read_text())
        assert data["name"] == name and data["description"] and data["system_prompt"]
    system = (BUNDLE / "system.md").read_text()
    for skill in ("screening", "rebalancing", "post_mortem"):
        text = (BUNDLE / f"skills/{skill}/SKILL.md").read_text()
        assert text.startswith("---\n") and f"name: {skill}" in text and skill in system


# --- portfolio_math (loaded by path from the bundle) ---
def test_weights_sums_to_one_on_task2_fixture():
    math = load_math()
    fixture = json.loads((ROOT / "tests/fixtures/etoro_pnl.json").read_text())
    w = math.weights(fixture["positions"])
    assert w == pytest.approx({"ACME": 1.0})
    assert sum(w.values()) == pytest.approx(1.0)


def test_weights_and_drift_and_volatility():
    math = load_math()
    positions = [
        {"ticker": "A", "units": 1.0, "current_price": 100.0},
        {"ticker": "B", "units": 3.0, "current_price": 100.0},
    ]
    assert math.weights(positions) == pytest.approx({"A": 0.25, "B": 0.75})
    assert math.weights([]) == {}
    drift = math.drift({"A": 0.5, "B": 0.5}, {"A": 0.3})
    assert drift["A"] == pytest.approx(0.2)
    assert drift["B"] == pytest.approx(0.5)
    assert math.volatility([]) == 0.0
    assert math.volatility([100.0]) == 0.0
    assert math.volatility([100.0] * 10) == pytest.approx(0.0)
    assert math.volatility([100.0, 110.0, 99.0]) > 0
