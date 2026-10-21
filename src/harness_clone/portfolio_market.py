"""Market data providers, pure summaries, screening, and universe fetchers.

No network access happens at import time. ``yfinance`` is imported lazily
inside :class:`YFinanceProvider` so this module (and its tests, which use
injected fakes) works without that package installed.
"""

import statistics
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Protocol, runtime_checkable

from harness_clone.contracts import Candidate, MarketSummary

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_MARKETS_URL = f"{COINGECKO_BASE_URL}/coins/markets"

# Common CoinGecko coin ids; unknown symbols fall back to the lowercased ticker.
COIN_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "LTC": "litecoin",
    "BNB": "binancecoin",
    "TRX": "tron",
    "ATOM": "cosmos",
    "UNI": "uniswap",
}


DatedClose = tuple[date, float]


@runtime_checkable
class PriceProvider(Protocol):
    def closes(self, ticker: str, asset_class: str, end: date, days: int) -> list[float]:
        """Daily closes oldest -> newest, bounded by ``end`` (point-in-time)."""
        ...


@runtime_checkable
class DatedPriceProvider(PriceProvider, Protocol):
    def dated_closes(
        self, ticker: str, asset_class: str, end: date, days: int
    ) -> list[DatedClose]:
        """``(date, close)`` pairs oldest -> newest for ``end - days <= date <= end``.

        ``days`` counts calendar days for every provider, so equity and
        crypto windows cover the same span.
        """
        ...


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def bound_dated(points: list[DatedClose], end: date, days: int) -> list[DatedClose]:
    """Keep only closes dated within ``[end - days, end]``, oldest first."""
    start = end - timedelta(days=days)
    return sorted((d, v) for d, v in points if start <= d <= end)


def _pct_return(closes: list[float], lookback: int) -> float:
    if len(closes) < 2:
        return 0.0
    window = min(lookback, len(closes) - 1)
    base = closes[-1 - window]
    if base == 0:
        return 0.0
    return (closes[-1] / base - 1.0) * 100.0


def _volatility_30d(closes: list[float]) -> float:
    """Population stdev of daily percentage returns over the last 30 returns."""
    if len(closes) < 2:
        return 0.0
    window = closes[-31:]
    returns = [
        (curr / prev - 1.0) * 100.0
        for prev, curr in zip(window, window[1:], strict=False)
        if prev != 0
    ]
    if len(returns) < 2:
        return 0.0
    return statistics.pstdev(returns)


def market_summary(ticker: str, asset_class: str, closes: list[float]) -> MarketSummary:
    """Summarize daily closes; empty input is flagged missing, never fabricated."""
    if not closes:
        return MarketSummary(
            ticker=ticker,
            asset_class=asset_class,
            last_price=0.0,
            return_7d_pct=0.0,
            return_30d_pct=0.0,
            volatility_30d=0.0,
            high_52w_distance_pct=0.0,
            missing=True,
        )
    last = float(closes[-1])
    high = max(float(c) for c in closes)
    return MarketSummary(
        ticker=ticker,
        asset_class=asset_class,
        last_price=last,
        return_7d_pct=_pct_return(closes, 7),
        return_30d_pct=_pct_return(closes, 30),
        volatility_30d=_volatility_30d(closes),
        high_52w_distance_pct=(last / high - 1.0) * 100.0 if high != 0 else 0.0,
        missing=False,
    )


def screen_candidates(summaries: list[MarketSummary], limit: int = 30) -> list[Candidate]:
    """Rank non-missing summaries by ``return_30d_pct - 0.5 * volatility_30d``."""
    scored = [
        (
            s.ticker,
            Candidate(
                ticker=s.ticker,
                asset_class=s.asset_class,
                score=s.return_30d_pct - 0.5 * s.volatility_30d,
                metrics={
                    "last_price": float(s.last_price),
                    "return_7d_pct": float(s.return_7d_pct),
                    "return_30d_pct": float(s.return_30d_pct),
                    "volatility_30d": float(s.volatility_30d),
                    "high_52w_distance_pct": float(s.high_52w_distance_pct),
                },
            ),
        )
        for s in summaries
        if not s.missing
    ]
    scored.sort(key=lambda item: (-item[1].score, item[0]))
    return [candidate for _, candidate in scored[:limit]]


class YFinanceProvider:
    """Equity/ETF closes via yfinance, with an injectable ticker factory.

    ``ticker_factory`` defaults to ``yfinance.Ticker`` imported lazily on
    first use, so importing this module never requires yfinance. History is
    requested by explicit ``start``/``end`` dates (never ``period``, which
    some yfinance versions let override ``end``); yfinance's ``end`` is
    exclusive, so the request ends the day after ``end`` and every returned
    close is then filtered to ``<= end`` by its own date.
    """

    def __init__(self, ticker_factory: Callable | None = None) -> None:
        self._ticker_factory = ticker_factory

    def _factory(self) -> Callable:
        if self._ticker_factory is None:
            import yfinance  # lazy: optional at import/test time, required for live use

            self._ticker_factory = yfinance.Ticker
        return self._ticker_factory

    def dated_closes(self, ticker: str, asset_class: str, end: date, days: int) -> list[DatedClose]:
        symbol = ticker.replace(".", "-")  # Wikipedia BRK.B -> yfinance BRK-B
        history = self._factory()(symbol).history(
            start=end - timedelta(days=days), end=end + timedelta(days=1)
        )
        if history is None:
            return []
        try:
            close = history["Close"]
        except (KeyError, TypeError, IndexError):
            close = getattr(history, "Close", None)
        if close is None:
            return []
        values = close.tolist() if hasattr(close, "tolist") else list(close)
        if not values:
            return []
        index = getattr(close, "index", None)
        if index is None or callable(index):
            raise ValueError(f"yfinance returned undated closes for {ticker}")
        index = list(index)
        if len(index) != len(values):
            raise ValueError(f"yfinance returned misaligned dated closes for {ticker}")
        points = [
            (_as_date(stamp), float(value))
            for stamp, value in zip(index, values, strict=True)
            if value == value  # drop NaN rows
        ]
        return bound_dated(points, end, days)

    def closes(self, ticker: str, asset_class: str, end: date, days: int) -> list[float]:
        return [value for _, value in self.dated_closes(ticker, asset_class, end, days)]


def _default_coingecko_get(url: str, params: dict) -> dict:
    import httpx

    response = httpx.get(url, params=params, timeout=30.0)
    response.raise_for_status()
    return response.json()


class CoinGeckoProvider:
    """Crypto closes via CoinGecko ``market_chart``, with an injected ``get``.

    ``get(url, params) -> dict`` follows the same seam as
    :func:`top_crypto_symbols`; the default transport uses httpx.
    """

    def __init__(
        self,
        get: Callable[[str, dict], dict] | None = None,
        coin_ids: dict[str, str] | None = None,
    ) -> None:
        self._get = get or _default_coingecko_get
        self._coin_ids = {**COIN_IDS, **(coin_ids or {})}

    def add_coin_ids(self, coin_ids: dict[str, str]) -> None:
        """Register symbol -> CoinGecko id pairs (e.g. from ``/coins/markets``)."""
        for symbol, coin_id in coin_ids.items():
            self._coin_ids.setdefault(symbol.upper(), coin_id)

    def closes(self, ticker: str, asset_class: str, end: date, days: int) -> list[float]:
        return [value for _, value in self.dated_closes(ticker, asset_class, end, days)]

    def has_coin_id(self, ticker: str) -> bool:
        return ticker.upper() in self._coin_ids

    def dated_closes(self, ticker: str, asset_class: str, end: date, days: int) -> list[DatedClose]:
        coin_id = self._coin_ids.get(ticker.upper(), ticker.lower())
        to_ts = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp())
        start = end - timedelta(days=days)
        from_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
        payload = self._get(
            f"{COINGECKO_BASE_URL}/coins/{coin_id}/market_chart/range",
            {"vs_currency": "usd", "from": from_ts, "to": to_ts, "interval": "daily"},
        )
        prices = payload.get("prices", []) if isinstance(payload, dict) else []
        cutoff_ms = to_ts * 1000
        points = [
            (datetime.fromtimestamp(point[0] / 1000, tz=timezone.utc).date(), float(point[1]))
            for point in prices
            if len(point) >= 2 and point[0] <= cutoff_ms
        ]
        return bound_dated(points, end, days)


class _FirstTableParser(HTMLParser):
    """Collect rows of the first <table> only, as lists of cell text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_first_table = False
        self._done = False
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "table":
            if not self._in_first_table:
                self._in_first_table = True
                self._depth = 1
            else:
                self._depth += 1
        elif self._in_first_table and self._depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []

    def handle_endtag(self, tag):
        if self._done or not self._in_first_table:
            return
        if tag == "table":
            self._depth -= 1
            if self._depth == 0:
                self._in_first_table = False
                self._done = True
        elif self._depth == 1:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append("".join(self._cell).strip())
                self._cell = None
            elif tag == "tr" and self._row is not None:
                if self._row:
                    self.rows.append(self._row)
                self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def sp500_tickers(fetch: Callable[[str], str]) -> list[str]:
    """Parse S&P 500 symbols from the first table of the Wikipedia list page."""
    parser = _FirstTableParser()
    parser.feed(fetch(SP500_URL))
    tickers = []
    for row in parser.rows:
        if not row:
            continue
        symbol = row[0].strip()
        if not symbol or symbol.lower() == "symbol":
            continue
        tickers.append(symbol)
    return tickers


def top_crypto_symbols(get: Callable[[str, dict], list | dict]) -> list[tuple[str, str]]:
    """Top crypto ``(SYMBOL, coingecko_id)`` pairs from CoinGecko ``/coins/markets``.

    The live endpoint returns a list ordered by market cap. The id is kept
    because symbols do not map to ids (USDT is ``tether``); entries without
    an id are skipped and a repeated symbol keeps its highest-cap coin.
    """
    payload = get(COINGECKO_MARKETS_URL, {"vs_currency": "usd", "per_page": 100})
    if isinstance(payload, dict):
        items = payload.get("coins") or payload.get("markets") or payload.get("data") or []
    else:
        items = payload or []
    pairs: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("symbol") or not item.get("id"):
            continue
        pairs.setdefault(str(item["symbol"]).upper(), str(item["id"]))
    return list(pairs.items())
