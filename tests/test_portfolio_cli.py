import json
from datetime import date

import pytest


def test_portfolio_daily_requires_cap(capsys):
    from harness_clone.cli import main

    assert main(["portfolio", "daily", "--max-usd", "0"]) == 2
    assert "spending cap" in capsys.readouterr().err


def test_portfolio_evolve_requires_cap(capsys):
    from harness_clone.cli import main

    assert main(["portfolio", "evolve", "--max-usd", "0"]) == 2
    assert "spending cap" in capsys.readouterr().err


def test_portfolio_daily_requires_fireworks_key(tmp_path, monkeypatch):
    from harness_clone.cli import main

    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    assert (
        main(
            [
                "portfolio",
                "daily",
                "--max-usd",
                "1",
                "--base-dir",
                str(tmp_path / "portfolio"),
            ]
        )
        == 2
    )


def test_portfolio_fetch_requires_csv_or_etoro_key(tmp_path, monkeypatch, capsys):
    from harness_clone.cli import main

    monkeypatch.delenv("ETORO_API_KEY", raising=False)
    monkeypatch.delenv("ETORO_USER_KEY", raising=False)
    assert main(["portfolio", "fetch", "--base-dir", str(tmp_path / "portfolio")]) == 2
    assert "ETORO_API_KEY" in capsys.readouterr().err


def test_etoro_http_errors_are_named_without_leaking_keys(monkeypatch):
    import httpx

    import harness_clone.cli as cli

    def fake_get(url, headers, timeout):
        return httpx.Response(401, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(ValueError, match="rejected the keys") as err:
        cli._etoro_get("https://x/api?instrumentIds=1", {"x-api-key": "SECRET", "x-user-key": "S2"})
    assert "SECRET" not in str(err.value) and "instrumentIds" not in str(err.value)


def test_portfolio_fetch_with_csv_builds_snapshot(tmp_path, monkeypatch, capsys):
    import harness_clone.cli as cli
    from harness_clone.contracts import MarketSummary
    from harness_clone.portfolio_snapshot import build_snapshot_task

    csv_path = tmp_path / "portfolio.csv"
    csv_path.write_text(
        "instrument,ticker,asset_class,units,open_rate,current_price\n"
        "Apple Inc,AAPL,equity,10,100,120\n"
        "Cash,USD,cash,500,1,1\n"
    )
    seen = {}

    def fake_fetch_task(base, day, portfolio, provider, **kwargs):
        seen.update(day=day, tickers=[p.ticker for p in portfolio.positions])
        summary = MarketSummary(
            ticker="AAPL",
            asset_class="equity",
            last_price=120.0,
            return_7d_pct=1.0,
            return_30d_pct=10.0,
            volatility_30d=2.0,
            high_52w_distance_pct=-5.0,
        )
        return build_snapshot_task(base, day, portfolio, {"AAPL": summary}, [], 6, [])

    monkeypatch.setattr(cli, "fetch_task", fake_fetch_task)
    base = tmp_path / "portfolio"
    assert (
        cli.main(
            [
                "portfolio",
                "fetch",
                "--base-dir",
                str(base),
                "--date",
                "2026-10-21",
                "--csv",
                str(csv_path),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["task"] == "portfolio-20261021"
    assert seen["day"] == date(2026, 10, 21) and seen["tickers"] == ["AAPL"]
    assert (base / "2026-10-21" / "task.json").is_file()


def test_portfolio_score_refuses_output_inside_run_dir(tmp_path):
    from harness_clone.cli import main

    run_dir = tmp_path / "run"
    assert (
        main(
            [
                "portfolio",
                "score",
                "--run-dir",
                str(run_dir),
                "--scoreboard",
                str(run_dir / "sb.json"),
            ]
        )
        == 2
    )


def test_portfolio_score_writes_scoreboard_outside_run_dir(tmp_path, monkeypatch, capsys):
    import harness_clone.cli as cli

    run_dir = tmp_path / "run"
    out = tmp_path / "scoreboard.json"
    seen = {}

    def fake_scoreboard(run_root, out_path, today, prices, volatilities):
        seen.update(run_root=run_root, out=out_path, today=today)
        return {"generated_at": today.isoformat(), "matured": 0, "pending": 0}

    monkeypatch.setattr(cli, "portfolio_scoreboard", fake_scoreboard)
    assert (
        cli.main(
            [
                "portfolio",
                "score",
                "--run-dir",
                str(run_dir),
                "--scoreboard",
                str(out),
                "--date",
                "2026-10-21",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["matured"] == 0
    assert seen["today"] == date(2026, 10, 21)


def test_portfolio_score_prices_route_by_asset_class(tmp_path, monkeypatch, capsys):
    import harness_clone.cli as cli

    run_dir = tmp_path / "run"
    out = tmp_path / "scoreboard.json"
    closes_calls = []

    class FakeProvider:
        def closes(self, ticker, asset_class, end, days):
            closes_calls.append((ticker, asset_class))
            return [100.0, 110.0]

        def dated_closes(self, ticker, asset_class, end, days):
            closes_calls.append((ticker, asset_class))
            return [(date(2026, 10, 14), 100.0), (end, 110.0)]

    def fake_scoreboard(run_root, out_path, today, prices, volatilities):
        # A screened crypto ticker must reach the provider as crypto.
        assert prices("SOL", "crypto", date(2026, 10, 14), today) == (100.0, 110.0)
        assert prices("AAPL", "equity", date(2026, 10, 14), today) == (100.0, 110.0)
        return {"generated_at": today.isoformat(), "matured": 0, "pending": 0}

    monkeypatch.setattr(cli, "portfolio_scoreboard", fake_scoreboard)
    monkeypatch.setattr(cli, "live_price_provider", lambda: FakeProvider())
    assert (
        cli.main(
            [
                "portfolio",
                "score",
                "--run-dir",
                str(run_dir),
                "--scoreboard",
                str(out),
                "--date",
                "2026-10-21",
            ]
        )
        == 0
    )
    assert ("SOL", "crypto") in closes_calls
    assert ("AAPL", "equity") in closes_calls


def test_portfolio_score_prices_span_task_date_to_maturity_across_weekend(
    tmp_path, monkeypatch, capsys
):
    from datetime import timedelta

    import harness_clone.cli as cli

    run_dir = tmp_path / "run"
    out = tmp_path / "scoreboard.json"

    def price_on(day):
        return float(day.toordinal() - date(2026, 10, 1).toordinal() + 100)

    class DatedProvider:
        def closes(self, ticker, asset_class, end, days):
            return [v for _, v in self.dated_closes(ticker, asset_class, end, days)]

        def dated_closes(self, ticker, asset_class, end, days):
            out, day = [], end - timedelta(days=days)
            while day <= end + timedelta(days=5):  # a leaky provider: beyond end
                if day.weekday() < 5:
                    out.append((day, price_on(day)))
                day += timedelta(days=1)
            return out

    seen = {}

    def fake_scoreboard(run_root, out_path, today, prices, volatilities):
        seen["window"] = prices("AAPL", "equity", date(2026, 10, 18), date(2026, 10, 25))
        return {"generated_at": today.isoformat(), "matured": 0, "pending": 0}

    monkeypatch.setattr(cli, "portfolio_scoreboard", fake_scoreboard)
    monkeypatch.setattr(cli, "live_price_provider", lambda: DatedProvider())
    args = ["portfolio", "score", "--run-dir", str(run_dir), "--scoreboard", str(out)]
    assert cli.main([*args, "--date", "2026-10-28"]) == 0
    # Friday 10-16 close (on/before the Sunday call) to Friday 10-23 close.
    assert seen["window"] == (price_on(date(2026, 10, 16)), price_on(date(2026, 10, 23)))


def test_portfolio_fetch_warns_on_partial_universe(tmp_path, monkeypatch, capsys):
    import harness_clone.cli as cli
    from harness_clone.portfolio_snapshot import build_snapshot_task

    csv_path = tmp_path / "portfolio.csv"
    csv_path.write_text(
        "instrument,ticker,asset_class,units,open_rate,current_price\n"
        "Apple Inc,AAPL,equity,10,100,120\n"
    )

    def fake_fetch_task(base, day, portfolio, provider, **kwargs):
        missing = {"count": 2, "sample": ["MSFT", "USDT"], "sources_unavailable": []}
        return build_snapshot_task(base, day, portfolio, {}, [], 6, [], universe_missing=missing)

    monkeypatch.setattr(cli, "fetch_task", fake_fetch_task)
    argv = ["portfolio", "fetch", "--base-dir", str(tmp_path / "p"), "--date", "2026-10-21"]
    assert cli.main([*argv, "--csv", str(csv_path)]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["universe_missing"]["count"] == 2
    assert any("2 of 6" in w for w in report["warnings"])
    assert "2 of 6" in captured.err


def _score_run(tmp_path, recs, coin_ids):
    from harness_clone.contracts import ExecutionRecord, SelectionRecord, TaskResult
    from harness_clone.storage import RunStore

    source = tmp_path / "snap" / "source"
    source.mkdir(parents=True)
    if coin_ids is not None:
        (source / "coin_ids.json").write_text(json.dumps(coin_ids))
    run_dir = tmp_path / "run"
    store = RunStore(run_dir)
    tid = "portfolio-20261014"
    ex = ExecutionRecord(
        execution_id="e1",
        task_id=tid,
        bundle_digest="a" * 64,
        delivered=True,
        artifacts={"recommendations.json": json.dumps(recs)},
    )
    result = TaskResult(
        task_id=tid, executions={"e1": ex}, selection=SelectionRecord(chosen_execution_id="e1")
    )
    store.put("runtime", "results", [result.model_dump(mode="json")])
    store.put(
        "runtime",
        "tasks",
        [{"id": tid, "ordinal": 20261014, "source_root": str(source)}],
    )
    return run_dir


def _crypto_rec(ticker, **over):
    rec = {
        "ticker": ticker,
        "asset_class": "crypto",
        "action": "add",
        "target_weight_pct": 5.0,
        "horizon_days": 7,
        "thesis": "t",
        "invalidation": "i",
        "benchmark": "BTC",
        "evidence_refs": ["e"],
    }
    rec.update(over)
    return rec


def _coingecko_provider(requested):
    from datetime import datetime, timezone

    from harness_clone.portfolio_market import CoinGeckoProvider
    from harness_clone.portfolio_ops import RoutedPriceProvider

    class NoEquity:
        def closes(self, *a):
            raise AssertionError("equity not expected")

        dated_closes = closes

    def get(url, params):
        coin_id = url.split("/coins/")[1].split("/")[0]
        requested.append(coin_id)
        if coin_id not in {"hyperliquid", "bitcoin"}:
            raise RuntimeError(f"404 for {coin_id}")
        scale = 2.0 if coin_id == "hyperliquid" else 1.0
        return {
            "prices": [
                [
                    int(datetime(2026, 10, d, tzinfo=timezone.utc).timestamp() * 1000),
                    scale * (100.0 + (d - 13)),
                ]
                for d in range(13, 22)
            ]
        }

    return RoutedPriceProvider(NoEquity(), CoinGeckoProvider(get=get))


def test_portfolio_score_prices_top100_coin_via_persisted_id(tmp_path, monkeypatch):
    import harness_clone.cli as cli

    requested: list[str] = []
    run_dir = _score_run(tmp_path, [_crypto_rec("HYPE")], {"HYPE": "hyperliquid", "BTC": "bitcoin"})
    monkeypatch.setattr(cli, "live_price_provider", lambda: _coingecko_provider(requested))
    out = tmp_path / "sb.json"
    argv = ["portfolio", "score", "--run-dir", str(run_dir), "--scoreboard", str(out)]
    assert cli.main([*argv, "--date", "2026-10-28"]) == 0
    report = json.loads(out.read_text())
    assert report["matured"] == 1 and report["unscored"] == 0
    assert "hype" not in requested
    assert "hyperliquid" in requested


def test_portfolio_score_unknown_crypto_id_is_unscored_not_fatal(tmp_path, monkeypatch):
    import harness_clone.cli as cli

    requested: list[str] = []
    run_dir = _score_run(
        tmp_path, [_crypto_rec("ZZZ"), _crypto_rec("HYPE")], {"HYPE": "hyperliquid"}
    )
    monkeypatch.setattr(cli, "live_price_provider", lambda: _coingecko_provider(requested))
    out = tmp_path / "sb.json"
    argv = ["portfolio", "score", "--run-dir", str(run_dir), "--scoreboard", str(out)]
    assert cli.main([*argv, "--date", "2026-10-28"]) == 0
    report = json.loads(out.read_text())
    assert report["matured"] == 1 and report["unscored"] == 1
    zzz = next(c for c in report["calls"] if c["ticker"] == "ZZZ")
    assert zzz["status"] == "unscored" and "ZZZ" in zzz["reason"]
    assert "zzz" not in requested


def test_portfolio_score_pricing_error_is_unscored_not_fatal(tmp_path, monkeypatch):
    import harness_clone.cli as cli

    requested: list[str] = []
    run_dir = _score_run(tmp_path, [_crypto_rec("HYPE")], {"HYPE": "nonexistent-id"})
    monkeypatch.setattr(cli, "live_price_provider", lambda: _coingecko_provider(requested))
    out = tmp_path / "sb.json"
    argv = ["portfolio", "score", "--run-dir", str(run_dir), "--scoreboard", str(out)]
    assert cli.main([*argv, "--date", "2026-10-28"]) == 0
    report = json.loads(out.read_text())
    assert report["unscored"] == 1 and report["matured"] == 0
    assert "404" in report["calls"][0]["reason"]
