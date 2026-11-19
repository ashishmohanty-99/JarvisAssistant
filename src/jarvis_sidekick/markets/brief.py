"""The daily briefing: indices, movers, headlines and screen results in one document."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx

from jarvis_sidekick import _http, paths
from jarvis_sidekick.markets import data, indicators, news

DISCLAIMER = (
    "Research aid, not investment advice. Prices are delayed; screens rank past price behaviour "
    "and cannot predict returns. Check fundamentals and your own situation before investing."
)


def collect(markets: tuple[str, ...] = ("india", "us"), top: int = 5, client: httpx.Client | None = None) -> dict:
    """Gather everything. Symbols or feeds that fail are listed under ``unavailable``, not hidden."""
    unavailable: list[str] = []

    def load(symbol: str) -> data.Series | None:
        try:
            return data.history(symbol, client=client)
        except _http.FetchError:
            unavailable.append(symbol)
            return None

    index_rows = []
    for group, symbols in data.INDICES.items():
        for symbol, name in symbols.items():
            series = load(symbol)
            if series:
                index_rows.append({"group": group, "name": name, **indicators.snapshot(series).to_dict()})

    screens, movers = {}, {}
    for market in markets:
        series_list = [s for s in (load(sym) for sym in data.WATCHLISTS.get(market, [])) if s]
        snaps = [indicators.snapshot(s) for s in series_list]
        by_day = sorted((s for s in snaps if s.day_pct is not None), key=lambda s: s.day_pct)
        movers[market] = {
            "gainers": [s.to_dict() for s in reversed(by_day[-3:])],
            "losers": [s.to_dict() for s in by_day[:3]],
        }
        screens[market] = [s.to_dict() for s in indicators.screen(series_list, top)]

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="minutes"),
        "indices": index_rows,
        "movers": movers,
        "screens": screens,
        "news": {region: [h.to_dict() for h in news.headlines(region, client=client)] for region in ("india", "world")},
        "unavailable": unavailable,
        "disclaimer": DISCLAIMER,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def to_markdown(brief: dict) -> str:
    out = [f"# Market briefing ({brief['generated_at']} UTC)", ""]
    if brief["indices"]:
        out += ["## Indices and macro", "", "| | Last | Day | 1 month | 6 months |", "|---|---:|---:|---:|---:|"]
        out += [
            f"| {r['name']} | {r['price']:,.2f} | {_pct(r['day_pct'])} | {_pct(r['month_pct'])} | "
            f"{_pct(r['half_year_pct'])} |"
            for r in brief["indices"]
        ]
        out.append("")
    for market, groups in brief["movers"].items():
        if groups["gainers"]:
            def names(rows: list[dict]) -> str:
                return ", ".join(f"{r['symbol']} {_pct(r['day_pct'])}" for r in rows)

            out += [f"## {market.upper()} movers", "", f"- Up: {names(groups['gainers'])}",
                    f"- Down: {names(groups['losers'])}", ""]
    for market, rows in brief["screens"].items():
        if rows:
            out += [f"## {market.upper()} momentum screen (candidates to research)", "",
                    "| Symbol | Price | 3 months | 6 months | Volatility | RSI | Score | Note |",
                    "|---|---:|---:|---:|---:|---:|---:|---|"]
            out += [
                f"| {r['symbol']} | {r['price']:,.2f} | {_pct(r['quarter_pct'])} | {_pct(r['half_year_pct'])} | "
                f"{r['volatility_pct']}% | {r['rsi']} | {r['score']} | {r['note']} |"
                for r in rows
            ]
            out.append("")
    for region, items in brief["news"].items():
        if items:
            out += [f"## News: {region}", ""] + [f"- [{h['title']}]({h['link']}) ({h['source']})" for h in items] + [""]
    if brief["unavailable"]:
        out += [f"Unavailable this run: {', '.join(brief['unavailable'])}", ""]
    out += [f"_{brief['disclaimer']}_", ""]
    return "\n".join(out)


def to_speech(brief: dict) -> str:
    """A short script for text-to-speech: headline numbers only."""
    parts = []
    for row in brief["indices"]:
        if row["name"] in ("NIFTY 50", "SENSEX", "S&P 500", "Nasdaq", "USD/INR", "Gold") and row["day_pct"] is not None:
            direction = "up" if row["day_pct"] >= 0 else "down"
            parts.append(f"{row['name']} is {direction} {abs(row['day_pct']):.1f} percent at {row['price']:,.0f}.")
    for market, rows in brief["screens"].items():
        if rows:
            parts.append(f"Top of the {market} momentum screen: {', '.join(r['symbol'].split('.')[0] for r in rows[:3])}.")
    headline = next((items[0]["title"] for items in brief["news"].values() if items), "")
    if headline:
        parts.append(f"Top headline: {headline}.")
    return " ".join(parts) or "No market data was available for this briefing."


def save(markdown: str) -> str:
    folder = paths.home() / "briefings"
    folder.mkdir(exist_ok=True)
    path = folder / f"{datetime.now(UTC):%Y-%m-%d}.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path)


def send_telegram(text: str) -> None:
    """Send through your own bot. Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to send briefings")
    for start in range(0, len(text), 4000):  # Telegram caps a message at 4096 characters
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text[start:start + 4000], "disable_web_page_preview": True},
                timeout=30.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Telegram rejected the message (HTTP {exc.response.status_code})") from None
        except httpx.HTTPError as exc:  # the URL holds the token: never echo it
            raise RuntimeError(f"Telegram request failed ({type(exc).__name__})") from None
