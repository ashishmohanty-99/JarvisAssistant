"""Plain-arithmetic indicators and a transparent momentum screen."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from itertools import pairwise

from jarvis_sidekick.markets.data import Series

TRADING_DAYS = 252


def pct_change(closes: list[float], days: int) -> float | None:
    if len(closes) <= days or closes[-1 - days] == 0:
        return None
    return round((closes[-1] / closes[-1 - days] - 1) * 100, 2)


def sma(closes: list[float], window: int) -> float | None:
    return sum(closes[-window:]) / window if len(closes) >= window else None


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(closes) <= period:
        return None
    changes = [b - a for a, b in pairwise(closes)]
    gain = sum(max(c, 0) for c in changes[:period]) / period
    loss = sum(max(-c, 0) for c in changes[:period]) / period
    for change in changes[period:]:
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
    return 100.0 if loss == 0 else round(100 - 100 / (1 + gain / loss), 1)


def volatility(closes: list[float]) -> float | None:
    """Annualised standard deviation of daily log returns, in percent."""
    if len(closes) < 21:
        return None
    returns = [math.log(b / a) for a, b in pairwise(closes) if a > 0 and b > 0]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return round(math.sqrt(variance * TRADING_DAYS) * 100, 1)


def max_drawdown(closes: list[float]) -> float:
    peak, worst = closes[0], 0.0
    for close in closes:
        peak = max(peak, close)
        worst = min(worst, close / peak - 1)
    return round(worst * 100, 1)


@dataclass
class Snapshot:
    symbol: str
    price: float
    day_pct: float | None
    month_pct: float | None
    quarter_pct: float | None
    half_year_pct: float | None
    year_pct: float | None
    above_200dma: bool | None
    rsi: float | None
    volatility_pct: float | None
    max_drawdown_pct: float
    off_high_pct: float
    score: float | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def snapshot(series: Series) -> Snapshot:
    c = series.closes
    average = sma(c, 200)
    snap = Snapshot(
        symbol=series.symbol,
        price=round(c[-1], 2),
        day_pct=pct_change(c, 1),
        month_pct=pct_change(c, 21),
        quarter_pct=pct_change(c, 63),
        half_year_pct=pct_change(c, 126),
        year_pct=pct_change(c, len(c) - 1) if len(c) > 200 else None,
        above_200dma=None if average is None else c[-1] > average,
        rsi=rsi(c),
        volatility_pct=volatility(c),
        max_drawdown_pct=max_drawdown(c),
        off_high_pct=round((c[-1] / max(c) - 1) * 100, 1),
    )
    snap.score, snap.note = _score(snap)
    return snap


def _score(s: Snapshot) -> tuple[float | None, str]:
    """Risk-adjusted momentum: blended 3/6-month return per unit of volatility, trend-filtered.

    A well-known factor, not a forecast. It favours what has been rising steadily;
    it says nothing about valuation, results or news.
    """
    if s.quarter_pct is None or s.half_year_pct is None or not s.volatility_pct:
        return None, "not enough history"
    raw = (0.4 * s.quarter_pct + 0.6 * s.half_year_pct) / s.volatility_pct
    notes = []
    if s.above_200dma is False:
        raw -= 0.5
        notes.append("below 200-day average")
    if s.rsi is not None and s.rsi > 75:
        raw -= 0.25
        notes.append("overbought (RSI>75)")
    if s.rsi is not None and s.rsi < 30:
        notes.append("oversold (RSI<30)")
    if s.above_200dma and not notes:
        notes.append("steady uptrend")
    return round(raw, 2), ", ".join(notes)


def screen(series_list: list[Series], top: int = 5) -> list[Snapshot]:
    """Rank by score, best first. Symbols without enough history are dropped."""
    snaps = [snapshot(s) for s in series_list if len(s.closes) >= 2]
    ranked = sorted((s for s in snaps if s.score is not None), key=lambda s: -s.score)
    return ranked[:top]
