from datetime import date, datetime, timedelta, timezone

import pytest

from harness_clone.contracts import Candidate, MarketSummary
from harness_clone.portfolio_market import (
    CoinGeckoProvider,
    YFinanceProvider,
    market_summary,
    screen_candidates,
    sp500_tickers,
    top_crypto_symbols,
)

END = date(2026, 10, 21)


def summary(ticker, ret30, vol, *, ret7=0.0, missing=False):
    return MarketSummary(
        ticker=ticker,
        asset_class="equity",
        last_price=100.0,
        return_7d_pct=ret7,
        return_30d_pct=ret30,
        volatility_30d=vol,
        high_52w_distance_pct=0.0,
        missing=missing,
    )


def missing(ticker):
    return MarketSummary(
        ticker=ticker,
        asset_class="equity",
        last_price=0.0,
        return_7d_pct=0.0,
        return_30d_pct=0.0,
        volatility_30d=0.0,
        high_52w_distance_pct=0.0,
        missing=True,
    )


def test_summary_from_closes():
    s = market_summary("AAPL", "equity", [100.0] * 29 + [110.0])
    assert s.return_30d_pct == pytest.approx(10.0)
    assert s.missing is False


def test_summary_missing_data_is_flagged_not_fabricated():
    s = market_summary("AAPL", "equity", [])
    assert s.missing is True and s.last_price == 0.0
    assert s.return_7d_pct == 0.0 and s.return_30d_pct == 0.0
    assert s.volatility_30d == 0.0 and s.high_52w_distance_pct == 0.0


def test_screen_ranks_and_excludes_missing():
    out = screen_candidates([summary("AAA", 20.0, 4.0), summary("BBB", 5.0, 1.0), missing("CCC")])
    assert [c.ticker for c in out] == ["AAA", "BBB"]
    assert out[0].score == pytest.approx(18.0)


def test_summary_computes_7d_last_and_high_distance():
    closes = [100.0] * 23 + [100.0] * 7 + [120.0]
    # last 7 intervals: 100 -> 120; high is last itself
    s = market_summary("AAPL", "equity", closes)
    assert s.last_price == pytest.approx(120.0)
    assert s.return_7d_pct == pytest.approx(20.0)
    assert s.high_52w_distance_pct == pytest.approx(0.0)


def test_summary_high_distance_below_high():
    s = market_summary("AAPL", "equity", [200.0, 100.0])
    assert s.high_52w_distance_pct == pytest.approx(-50.0)


def test_summary_single_close_and_constant_volatility():
    s = market_summary("AAPL", "equity", [42.0])
    assert s.last_price == 42.0
    assert s.return_7d_pct == 0.0 and s.return_30d_pct == 0.0
    assert s.volatility_30d == 0.0
    assert market_summary("AAPL", "equity", [100.0] * 31).volatility_30d == pytest.approx(0.0)


def test_summary_uses_30d_window_not_earlier():
    # 31 closes before final jump far in past should not affect 30d base beyond window
    closes = [50.0] + [100.0] * 30 + [110.0]
    s = market_summary("AAPL", "equity", closes)
    assert s.return_30d_pct == pytest.approx(10.0)


def test_screen_ties_broken_by_ticker_and_limit_and_metrics():
    out = screen_candidates(
        [summary("BBB", 10.0, 0.0), summary("AAA", 10.0, 0.0), summary("CCC", 0.0, 0.0)],
        limit=2,
    )
    assert [c.ticker for c in out] == ["AAA", "BBB"]
    assert isinstance(out[0], Candidate)
    assert out[0].asset_class == "equity"
    assert out[0].metrics["return_30d_pct"] == pytest.approx(10.0)
    assert out[0].metrics["volatility_30d"] == pytest.approx(0.0)


def test_screen_empty_and_all_missing():
    assert screen_candidates([]) == []
    assert screen_candidates([missing("CCC")]) == []


def test_yfinance_provider_uses_injected_factory_and_truncates():
    calls = {}

    class FakeTicker:
        def history(self, *, period, end):
            calls["period"] = period
            calls["end"] = end
            return {"Close": [10.0, 11.0, 12.0, 13.0]}

    provider = YFinanceProvider(ticker_factory=lambda ticker: FakeTicker())
    assert provider.closes("AAPL", "equity", END, days=3) == [11.0, 12.0, 13.0]
    assert calls["end"] == END
    assert calls["period"]


def test_yfinance_provider_handles_empty_history():
    class FakeTicker:
        def history(self, *, period, end):
            return {"Close": []}

    provider = YFinanceProvider(ticker_factory=lambda ticker: FakeTicker())
    assert provider.closes("AAPL", "equity", END, days=30) == []


def test_coingecko_provider_uses_injected_get():
    calls = []

    def fake_get(url, params):
        calls.append((url, params))
        return {"prices": [[0, 100.0], [86400000, 101.0], [172800000, 102.0]]}

    provider = CoinGeckoProvider(get=fake_get)
    assert provider.closes("BTC", "crypto", END, days=30) == [100.0, 101.0, 102.0]
    assert "bitcoin" in calls[0][0]
    assert "market_chart" in calls[0][0]
    assert calls[0][1]["vs_currency"] == "usd"


def test_coingecko_provider_bounds_closes_by_end():
    calls = []

    def ms(d):
        return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)

    def fake_get(url, params):
        calls.append((url, params))
        return {
            "prices": [
                [ms(END - timedelta(days=1)), 100.0],
                [ms(END), 101.0],
                [ms(END + timedelta(days=1)), 102.0],
            ]
        }

    provider = CoinGeckoProvider(get=fake_get)
    # No returned close is from after END, and the request itself is bounded.
    assert provider.closes("BTC", "crypto", END, days=30) == [100.0, 101.0]
    url, params = calls[0]
    assert "market_chart/range" in url
    expected_to = int(datetime(END.year, END.month, END.day, tzinfo=timezone.utc).timestamp())
    expected_from = int(datetime(2026, 9, 21, tzinfo=timezone.utc).timestamp())  # END - 30 days
    assert params["to"] == expected_to
    assert params["from"] == expected_from


def test_coingecko_provider_unknown_symbol_lowercases_and_empty():
    def fake_get(url, params):
        assert url.rstrip("/").endswith("coins/some-coin/market_chart") or "some-coin" in url
        return {"prices": []}

    provider = CoinGeckoProvider(get=fake_get)
    assert provider.closes("SOME-COIN", "crypto", END, days=7) == []


def test_sp500_tickers_parses_first_table_only():
    html = """
    <html><body>
    <table><tr><th>Symbol</th><th>Security</th></tr>
    <tr><td>AAPL</td><td>Apple</td></tr>
    <tr><td><a href="#">MSFT</a></td><td>Microsoft</td></tr></table>
    <table><tr><td>ZZZ</td><td>Other</td></tr></table>
    </body></html>
    """
    seen = []

    def fetch(url):
        seen.append(url)
        return html

    assert sp500_tickers(fetch) == ["AAPL", "MSFT"]
    assert "List_of_S%26P_500_companies" in seen[0] or "List_of_S&P_500" in seen[0]


def test_top_crypto_symbols_from_list_response():
    calls = []

    def fake_get(url, params):
        calls.append((url, params))
        return [{"symbol": "btc"}, {"symbol": "eth"}, {"symbol": "sol"}]

    assert top_crypto_symbols(fake_get) == ["BTC", "ETH", "SOL"]
    assert "coins/markets" in calls[0][0]
    assert calls[0][1]["vs_currency"] == "usd"
    assert calls[0][1]["per_page"] == 100


def test_top_crypto_symbols_from_dict_response():
    def fake_get(url, params):
        return {"coins": [{"symbol": "btc"}]}

    # dict-shaped fakes are tolerated even though the live API returns a list
    assert top_crypto_symbols(fake_get) == ["BTC"]
