"""Daily price history from Yahoo's chart endpoint.

Free and keyless, but not an official, guaranteed feed: the endpoint is
undocumented and can change or rate-limit. Prices are end-of-day or delayed.
Good enough for research, not for trading decisions on the minute. There is no
second source: Stooq, the usual free alternative, now answers scripts with a
web page instead of data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import quote

import httpx

from jarvis_sidekick import _http

INDICES = {
    "India": {"^NSEI": "NIFTY 50", "^BSESN": "SENSEX", "^NSEBANK": "NIFTY Bank", "^INDIAVIX": "India VIX"},
    "World": {
        "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow Jones", "^FTSE": "FTSE 100", "^N225": "Nikkei 225",
        "^HSI": "Hang Seng",
    },
    "Macro": {"USDINR=X": "USD/INR", "GC=F": "Gold", "CL=F": "Crude oil (WTI)", "^TNX": "US 10y yield"},
}
WATCHLISTS = {
    "india": [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "BHARTIARTL.NS", "SBIN.NS", "LT.NS",
        "ITC.NS", "HINDUNILVR.NS", "BAJFINANCE.NS", "KOTAKBANK.NS", "AXISBANK.NS", "MARUTI.NS", "SUNPHARMA.NS",
        "M&M.NS", "TITAN.NS", "NTPC.NS", "ULTRACEMCO.NS", "HCLTECH.NS",
    ],
    "us": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "BRK-B", "JPM", "LLY", "V", "XOM", "UNH", "COST",
        "HD",
    ],
    "etf": ["NIFTYBEES.NS", "JUNIORBEES.NS", "GOLDBEES.NS", "MON100.NS", "SPY", "QQQ", "VXUS"],
}


@dataclass
class Series:
    symbol: str
    closes: list[float]
    dates: list[str]
    currency: str = ""
    source: str = ""

    @property
    def last(self) -> float:
        return self.closes[-1]


def _day(stamp: float) -> date:
    return datetime.fromtimestamp(stamp, tz=UTC).date()


def _yahoo(symbol: str, range_: str, client: httpx.Client | None) -> Series:
    data = _http.get_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}",
        params={"range": range_, "interval": "1d"},
        client=client,
    )
    try:
        result = data["chart"]["result"][0]
        stamps, raw = result["timestamp"], result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        raise _http.FetchError(f"no price data for {symbol}") from None
    pairs = [(t, c) for t, c in zip(stamps, raw) if isinstance(c, (int, float))]
    if len(pairs) < 2:
        raise _http.FetchError(f"no price data for {symbol}")
    # The daily bars can lag the last close by a session; the quote in "meta" is current. Add it when it is newer.
    meta = result.get("meta") or {}
    price, when = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    has_quote = isinstance(price, (int, float)) and isinstance(when, (int, float))
    if has_quote and _day(when) > _day(pairs[-1][0]):
        pairs.append((when, price))
    return Series(
        symbol=symbol,
        closes=[float(c) for _, c in pairs],
        dates=[_day(t).isoformat() for t, _ in pairs],
        currency=str((result.get("meta") or {}).get("currency") or ""),
        source="yahoo",
    )


def history(symbol: str, range_: str = "1y", client: httpx.Client | None = None) -> Series:
    return _yahoo(symbol, range_, client)
