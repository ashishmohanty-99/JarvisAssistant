"""Daily price history. Yahoo's chart endpoint first, Stooq CSV as the fallback.

Both are free and keyless, and neither is an official, guaranteed feed: Yahoo's
endpoint is undocumented and can change or rate-limit. Prices are end-of-day or
delayed. Good enough for research, not for trading decisions on the minute.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC
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
# Stooq uses its own tickers; only symbols listed here can fall back.
_STOOQ = {"^GSPC": "^spx", "^IXIC": "^ndq", "^DJI": "^dji", "^FTSE": "^ukx", "^N225": "^nkx", "^HSI": "^hsi"}


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


def _yahoo(symbol: str, range_: str, client: httpx.Client | None) -> Series:
    from datetime import datetime

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
    return Series(
        symbol=symbol,
        closes=[float(c) for _, c in pairs],
        dates=[datetime.fromtimestamp(t, tz=UTC).date().isoformat() for t, _ in pairs],
        currency=str((result.get("meta") or {}).get("currency") or ""),
        source="yahoo",
    )


def _stooq(symbol: str, client: httpx.Client | None) -> Series:
    ticker = _STOOQ.get(symbol) or (symbol.lower() + ".us" if symbol.isalpha() else "")
    if not ticker:
        raise _http.FetchError(f"no fallback source for {symbol}")
    text = _http.get("https://stooq.com/q/d/l/", params={"s": ticker, "i": "d"}, client=client).text
    rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get("Close") not in (None, "", "N/D")]
    if len(rows) < 2:
        raise _http.FetchError(f"no price data for {symbol}")
    rows = rows[-260:]
    return Series(symbol, [float(r["Close"]) for r in rows], [r["Date"] for r in rows], source="stooq")


def history(symbol: str, range_: str = "1y", client: httpx.Client | None = None) -> Series:
    try:
        return _yahoo(symbol, range_, client)
    except _http.FetchError:
        return _stooq(symbol, client)
