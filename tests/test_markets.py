from __future__ import annotations

import httpx
import pytest
import respx

from jarvis_sidekick import _http
from jarvis_sidekick.markets import brief, data, indicators, news, plan
from jarvis_sidekick.markets.data import Series

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"


def _chart(closes, currency="INR"):
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": currency},
                    "timestamp": [1_760_000_000 + 86_400 * i for i in range(len(closes))],
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


def _series(closes, symbol="X"):
    return Series(symbol, [float(c) for c in closes], [""] * len(closes))


def test_indicator_arithmetic():
    closes = [100, 110, 99, 120]
    assert indicators.pct_change(closes, 1) == 21.21
    assert indicators.pct_change(closes, 3) == 20.0
    assert indicators.pct_change(closes, 4) is None
    assert indicators.max_drawdown(closes) == -10.0
    assert indicators.sma(closes, 2) == 109.5 and indicators.sma(closes, 5) is None


def test_rsi_extremes_and_midpoint():
    assert indicators.rsi(list(range(1, 40))) == 100.0
    assert indicators.rsi(list(range(40, 1, -1))) == 0.0
    alternating = [100 + (1 if i % 2 else 0) for i in range(60)]
    assert 45 <= indicators.rsi(alternating) <= 55
    assert indicators.rsi([1, 2, 3]) is None


def test_volatility_of_a_flat_series_is_zero_and_grows_with_swings():
    assert indicators.volatility([100.0] * 30) == 0.0
    calm = [100 * 1.001**i for i in range(60)]
    wild = [100 * (1.05 if i % 2 else 0.95) for i in range(60)]
    assert indicators.volatility(wild) > indicators.volatility(calm) + 50


def test_screen_ranks_steady_risers_first_and_penalises_downtrends():
    steady = _series([100 * 1.002**i * (1.004 if i % 2 else 1) for i in range(260)], "STEADY")
    falling = _series([200 * 0.998**i * (1.004 if i % 2 else 1) for i in range(260)], "FALLING")
    short = _series([10, 11, 12], "NEW")

    ranked = indicators.screen([falling, short, steady], top=5)

    assert [s.symbol for s in ranked] == ["STEADY", "FALLING"]  # NEW has too little history to score
    assert ranked[0].above_200dma is True and ranked[0].score > 0
    assert ranked[1].score < 0 and "below 200-day average" in ranked[1].note


@respx.mock
def test_history_reads_yahoo_and_skips_missing_closes():
    respx.get(YAHOO + "%5ENSEI").mock(return_value=httpx.Response(200, json=_chart([100.0, None, 102.0])))
    series = data.history("^NSEI")
    assert (series.closes, series.currency, series.source) == ([100.0, 102.0], "INR", "yahoo")
    assert series.dates[0] == "2025-10-09" and series.last == 102.0


@respx.mock
def test_history_adds_the_current_quote_when_the_daily_bars_lag():
    chart = _chart([100.0, 101.0])
    last_bar = chart["chart"]["result"][0]["timestamp"][-1]
    chart["chart"]["result"][0]["meta"] = {"regularMarketPrice": 99.5, "regularMarketTime": last_bar + 86_400}
    respx.get(YAHOO + "%5ENSEI").mock(return_value=httpx.Response(200, json=chart))
    series = data.history("^NSEI")
    assert series.closes == [100.0, 101.0, 99.5] and series.dates[-1] == "2025-10-11"

    chart["chart"]["result"][0]["meta"]["regularMarketTime"] = last_bar + 600  # same day: already in the bars
    respx.get(YAHOO + "%5ENSEI").mock(return_value=httpx.Response(200, json=chart))
    assert data.history("^NSEI").closes == [100.0, 101.0]


@respx.mock
def test_stooq_failure_says_what_stooq_answered():
    respx.get("https://stooq.com/q/d/l/").mock(return_value=httpx.Response(200, text="Get your apikey:\n\n1. Open x"))
    with pytest.raises(_http.FetchError, match="Stooq answered: 'Get your apikey: 1. Open x'"):
        data._stooq("^GSPC", None)


@respx.mock
def test_history_falls_back_to_stooq_and_fails_cleanly_without_one():
    respx.get(YAHOO + "%5EGSPC").mock(return_value=httpx.Response(429))
    respx.get("https://stooq.com/q/d/l/").mock(
        return_value=httpx.Response(200, text="Date,Open,High,Low,Close,Volume\n2026-11-17,1,1,1,6000,0\n2026-11-18,1,1,1,6060,0\n")
    )
    series = data.history("^GSPC")
    assert (series.closes, series.source) == ([6000.0, 6060.0], "stooq")

    respx.get(YAHOO + "RELIANCE.NS").mock(return_value=httpx.Response(200, json={"chart": {"result": None}}))
    with pytest.raises(_http.FetchError, match="no fallback source"):
        data.history("RELIANCE.NS")


RSS = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<rss><channel>
<item><title>Sensex jumps 500 points &xxe;</title><link>https://et.example/a</link>
<pubDate>Wed, 18 Nov 2026 09:30:00 +0530</pubDate></item>
<item><title>No link</title></item>
<item><title>Bad link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Fed holds rates</title>
<link href="https://bbc.example/b"/><updated>2026-11-18T10:00:00Z</updated></entry></feed>"""


def test_feed_parsing_handles_rss_atom_and_hostile_xml():
    (item,) = news.parse_feed(RSS, "ET")
    assert item.title == "Sensex jumps 500 points" and "root:" not in item.title
    assert item.link == "https://et.example/a" and item.published == "2026-11-18T09:30+05:30"
    (entry,) = news.parse_feed(ATOM, "BBC")
    assert (entry.title, entry.link, entry.published) == ("Fed holds rates", "https://bbc.example/b", "2026-11-18T10:00:00Z")
    assert news.parse_feed(b"not xml at all", "X") == []


def test_plan_allocations_always_total_100_and_project_correctly():
    for risk in plan.ALLOCATIONS:
        for years in (2, 10):
            built = plan.build(10_000, years, risk)
            assert sum(row["percent"] for row in built.allocation) == 100
            assert sum(row["monthly"] for row in built.allocation) == 10_000
            assert built.projected["pessimistic"] <= built.projected["base"] <= built.projected["optimistic"]
    assert plan.future_value(1000, 0, 2) == 24_000
    assert round(plan.future_value(1000, 12, 1), 2) == 12_809.33  # 1%/month, paid at the start of each month
    short = {row["bucket"]: row["percent"] for row in plan.build(10_000, 2, "aggressive").allocation}
    assert short["debt"] == 80 and "mid_small_cap" not in short


@pytest.mark.parametrize("args", [(0, 10, "moderate"), (1000, 0, "moderate"), (1000, 10, "yolo")])
def test_plan_rejects_bad_input(args):
    with pytest.raises(ValueError):
        plan.build(*args)


@respx.mock
def test_briefing_reports_what_it_got_and_what_it_could_not_get():
    rising = [100 * 1.002**i * (1.004 if i % 2 else 1) for i in range(260)]
    respx.get(url__startswith=YAHOO).mock(
        side_effect=lambda request: httpx.Response(404)
        if "TCS.NS" in str(request.url)
        else httpx.Response(200, json=_chart(rising))
    )
    respx.get(url__startswith="https://stooq.com").mock(return_value=httpx.Response(200, text="No data"))
    respx.get(url__regex=r".*(rss|xml|feeds).*").mock(return_value=httpx.Response(200, content=RSS))

    collected = brief.collect(markets=("india",), top=3)
    markdown = brief.to_markdown(collected)

    assert collected["unavailable"] == ["TCS.NS"]
    assert len(collected["indices"]) == 14 and len(collected["screens"]["india"]) == 3
    nifty = collected["indices"][0]
    assert nifty["name"] == "NIFTY 50" and nifty["day_pct"] == 0.6
    assert f"| NIFTY 50 | {nifty['price']:,.2f} | +0.60% |" in markdown
    assert "## INDIA momentum screen (candidates to research)" in markdown
    assert "[Sensex jumps 500 points](https://et.example/a)" in markdown
    assert "Unavailable this run: TCS.NS" in markdown and "not investment advice" in markdown
    assert "NIFTY 50 is up 0.6 percent at 168." in brief.to_speech(collected)
    assert brief.save(markdown).endswith(".md")


@respx.mock
def test_telegram_errors_do_not_leak_the_bot_token(monkeypatch):
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        brief.send_telegram("hi")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    route = respx.post("https://api.telegram.org/bot123:SECRET/sendMessage").mock(return_value=httpx.Response(200))
    brief.send_telegram("x" * 9000)
    assert route.call_count == 3
    route.mock(side_effect=httpx.ConnectError("https://api.telegram.org/bot123:SECRET/ failed"))
    with pytest.raises(RuntimeError) as caught:
        brief.send_telegram("hi")
    assert "SECRET" not in str(caught.value) and caught.value.__suppress_context__
