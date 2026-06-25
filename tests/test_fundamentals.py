from stock_agent.fundamentals.yfinance_fetcher import FundamentalsFetcher


class FakeTicker:
    def __init__(self, info):
        self.info = info


def _factory(mapping):
    def make(symbol):
        if symbol not in mapping:
            raise KeyError(symbol)
        return FakeTicker(mapping[symbol])
    return make


FULL_INFO = {
    "revenueGrowth": 0.15,
    "earningsGrowth": 0.20,
    "profitMargins": 0.25,
    "returnOnEquity": 0.30,
    "debtToEquity": 150.0,   # -> 1.5
    "freeCashflow": 1.0e10,
    "trailingPE": 28.0,
    "pegRatio": 1.8,
    "priceToBook": 12.0,
    "marketCap": 3.0e12,
    "currentPrice": 190.0,
    "targetMeanPrice": 210.0,
    "sector": "Technology",
    "shortName": "Apple Inc.",
}


def test_parse_full_info():
    f = FundamentalsFetcher(ticker_factory=_factory({"AAPL": FULL_INFO}))
    fun = f.fetch("aapl")
    assert fun.ticker == "AAPL"
    assert fun.revenue_growth == 0.15
    assert fun.earnings_growth == 0.20
    assert fun.profit_margin == 0.25
    assert fun.roe == 0.30
    assert fun.debt_to_equity == 1.5            # percent converted to ratio
    assert fun.free_cash_flow == 1.0e10
    assert fun.trailing_pe == 28.0
    assert fun.peg_ratio == 1.8
    assert fun.sector == "Technology"
    assert fun.name == "Apple Inc."
    assert fun.error is None
    assert fun.available_count() == 8


def test_missing_fields_become_none():
    f = FundamentalsFetcher(ticker_factory=_factory({"X": {"trailingPE": 10.0}}))
    fun = f.fetch("X")
    assert fun.trailing_pe == 10.0
    assert fun.revenue_growth is None
    assert fun.roe is None
    assert fun.error is None
    assert fun.available_count() == 1


def test_nan_and_bad_values_treated_as_missing():
    info = {"revenueGrowth": float("nan"), "trailingPE": "n/a",
            "returnOnEquity": 0.1}
    f = FundamentalsFetcher(ticker_factory=_factory({"Y": info}))
    fun = f.fetch("Y")
    assert fun.revenue_growth is None
    assert fun.trailing_pe is None
    assert fun.roe == 0.1
    assert fun.available_count() == 1


def test_fetch_failure_captured_in_error():
    def boom(symbol):
        raise RuntimeError("429 too many requests")

    f = FundamentalsFetcher(ticker_factory=boom)
    fun = f.fetch("AAPL")
    assert fun.error is not None
    assert "429" in fun.error
    assert fun.available_count() == 0


def test_fallback_keys_used():
    info = {
        "earningsQuarterlyGrowth": 0.05,
        "trailingPegRatio": 2.1,
        "regularMarketPrice": 50.0,
    }
    f = FundamentalsFetcher(ticker_factory=_factory({"Z": info}))
    fun = f.fetch("Z")
    assert fun.earnings_growth == 0.05
    assert fun.peg_ratio == 2.1
    assert fun.current_price == 50.0


def test_cache_avoids_refetch():
    calls = {"n": 0}

    def counting_factory(symbol):
        calls["n"] += 1
        return FakeTicker(FULL_INFO)

    f = FundamentalsFetcher(ticker_factory=counting_factory, cache_ttl_seconds=999)
    f.fetch("AAPL")
    f.fetch("AAPL")
    assert calls["n"] == 1  # second call served from cache


def test_fetch_many():
    f = FundamentalsFetcher(
        ticker_factory=_factory({"AAPL": FULL_INFO, "X": {"trailingPE": 10.0}})
    )
    out = f.fetch_many(["AAPL", "X"])
    assert set(out) == {"AAPL", "X"}
    assert out["AAPL"].revenue_growth == 0.15
