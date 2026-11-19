from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from jarvis_sidekick import bot as botmod
from jarvis_sidekick import paper
from jarvis_sidekick.bot import Bot, Commander
from jarvis_sidekick.markets import news

IST = timezone(timedelta(hours=5, minutes=30), "IST")
NOW = datetime(2026, 11, 19, 7, 30, tzinfo=IST)


def _index(name, group, price, day, spark=None):
    return {"group": group, "name": name, "symbol": name, "price": price, "day_pct": day, "month_pct": 1.0,
            "half_year_pct": 4.0, "spark": spark or [price * (1 + i / 500) for i in range(60)]}


def _story(title, source="Economic Times Markets", hours=3, summary="", link="https://et.example/a"):
    return {"title": title, "link": link, "source": source, "summary": summary,
            "published": (NOW - timedelta(hours=hours)).isoformat(timespec="minutes")}


def _stock(symbol, price, day, quarter=12.0, half=20.0, note="steady uptrend"):
    return {"symbol": symbol, "price": price, "day_pct": day, "quarter_pct": quarter, "half_year_pct": half,
            "rsi": 58.2, "note": note}


@pytest.fixture
def edition():
    world = [_index(n, "World", 7000 + i, 0.4) for i, n in enumerate(
        ("S&P 500", "Nasdaq", "Dow Jones", "FTSE 100", "Nikkei 225", "Hang Seng"))]
    return {
        "generated_at": "2026-11-19T02:00",
        "indices": [_index("NIFTY 50", "India", 25227.95, -0.63), _index("India VIX", "India", 12.4, 3.1), *world,
                    _index("USD/INR", "Macro", 88.42, 0.2)],
        "movers": {"india": {"gainers": [_stock("NTPC.NS", 418.0, 2.26)], "losers": [_stock("M&M.NS", 2674.0, -1.98)]}},
        "screens": {"india": [_stock("RELIANCE.NS", 2202.0, 0.4, note="overbought (RSI>75)")]},
        "news": {"india": [_story("Sensex falls 500 points", summary="Metals led the fall."), _story("Rupee steadies")],
                 "world": [_story("Fed minutes point to more rate rises", "CNBC Markets", 30)]},
        "unavailable": ["TCS.NS"],
        "disclaimer": "Research aid, not investment advice.",
    }


def test_page_has_the_masthead_the_summary_sentence_and_every_section(edition):
    page = paper.build_html(edition, now=NOW, title="Master", extras=[("Applications sent", "2")])
    assert "The Jarvis Brief" in page and "Prepared for Master" in page
    assert "Thursday" in page and "19 November 2026" in page and "07:30 IST" in page
    assert "The NIFTY 50 fell 0.6% to 25,228, the S&amp;P 500 rose 0.4% to 7,000 and a dollar costs 88.42 rupees." in page
    for heading in ("Indian markets", "World markets", "Currency and commodities", "Biggest rises, India",
                    "Momentum screen: names to research", "Your numbers"):
        assert f"<h2>{heading}</h2>" in page
    assert 'class="tiles three"' in page  # six world indices: two rows of three
    assert "Economic Times Markets · 3h ago" in page and "CNBC Markets · 1d ago" in page
    assert 'class=lead' in page and page.count("<article") == 3
    assert "Not available in this edition: TCS.NS." in page and "not investment advice" in page


def test_direction_is_a_shape_first_and_colour_only_for_shares(edition):
    page = paper.build_html(edition, now=NOW)
    nifty = page[page.index(">NIFTY 50<"):page.index(">India VIX<")]
    vix = page[page.index(">India VIX<"):page.index(">S&amp;P 500<")]
    assert "▼" in nifty and paper.DOWN in nifty
    assert "▲" in vix and paper.UP not in vix  # a rising fear index is not good news, so it is not painted green
    assert paper._mark(None).count("●") == 1


def test_everything_from_outside_is_escaped_and_bad_links_are_dropped(edition):
    edition["news"]["india"] = [_story('<script>alert(1)</script> & "quotes"', source="<b>Src</b>",
                                       summary='<img src=x onerror=alert(2)>', link="javascript:alert(3)")]
    edition["screens"]["india"][0]["note"] = "</td><script>x</script>"
    page = paper.build_html(edition, now=NOW, title="<i>M</i>")
    assert "<script>" not in page and "<img" not in page and "<b>Src" not in page and "<i>M" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;quotes&quot;" in page
    assert "javascript:" not in page and 'href="#"' in page


def test_sparkline_stays_inside_its_box_and_marks_the_latest_point():
    svg = paper.sparkline([10, 20, 15, 30], width=100, height=40)
    points = [tuple(map(float, p.split(","))) for p in svg.split('points="')[1].split('"')[0].split()]
    assert len(points) == 4 and all(0 <= x <= 100 and 0 <= y <= 40 for x, y in points)
    assert points[0][1] > points[-1][1]  # a rise goes up the page
    assert f'cx="{points[-1][0]:.1f}"' in svg and paper.ACCENT in svg
    assert paper.sparkline([5]) == "" and "polyline" in paper.sparkline([7, 7, 7])  # flat data does not divide by zero


def test_edition_without_market_data_still_prints_the_news(edition):
    edition["indices"], edition["movers"], edition["screens"] = [], {}, {}
    page = paper.build_html(edition, now=NOW)
    assert "Market data was not available for this edition" in page and "Sensex falls 500 points" in page
    assert "Indian markets" not in page


def test_feed_summaries_are_plain_short_and_never_repeat_the_title():
    xml = b"""<rss><channel>
    <item><title>Nifty ends lower</title><link>https://e.example/1</link>
      <description>&lt;p&gt;Profit booking &lt;b&gt;hit&lt;/b&gt; metals.&lt;/p&gt;&lt;img src="x"&gt;</description></item>
    <item><title>Same text</title><link>https://e.example/2</link><description>Same text</description></item>
    <item><title>Long one</title><link>https://e.example/3</link><description>""" + b"word " * 200 + b"""</description></item>
    </channel></rss>"""
    first, second, third = news.parse_feed(xml, "ET")
    assert first.summary == "Profit booking hit metals." and second.summary == ""
    assert len(third.summary) <= 241 and third.summary.endswith("…")


@pytest.fixture
def funds(monkeypatch):
    from jarvis_sidekick import _http
    from jarvis_sidekick.markets import data
    from jarvis_sidekick.markets.data import Series

    prices = {"NIFTYBEES.NS": 280.0, "JUNIORBEES.NS": 740.0, "MON100.NS": 190.0, "GOLDBEES.NS": 95.0}

    def history(symbol, range_="1y", client=None):
        if symbol in prices:
            return Series(symbol, [prices[symbol]] * 30, [""] * 30)
        if symbol not in ("RELIANCE.NS", "TCS.NS"):
            raise _http.FetchError("none")
        return Series(symbol, [900 * 1.002**i * (1.02 if i % 2 else 1) for i in range(260)], [""] * 260)

    monkeypatch.setattr(data, "history", history)


@pytest.fixture
def chromium(monkeypatch):
    pytest.importorskip("playwright")
    if Path("/opt/pw-browsers/chromium").exists():
        monkeypatch.setenv("SIDEKICK_CHROMIUM", "/opt/pw-browsers/chromium")


def test_a_real_pdf_comes_out_with_text_and_working_links(edition, chromium, tmp_path):
    pypdf = pytest.importorskip("pypdf")
    target = paper.render_pdf(paper.build_html(edition, now=NOW, title="Master"), tmp_path / "brief.pdf")
    reader = pypdf.PdfReader(str(target))
    text = " ".join(page.extract_text() for page in reader.pages)
    assert target.read_bytes().startswith(b"%PDF-") and 1 <= len(reader.pages) <= 2
    assert "The Jarvis Brief" in text and "Sensex falls 500 points" in text and "25,228" in text
    links = [a.get_object()["/A"]["/URI"] for page in reader.pages for a in page.get("/Annots") or []]
    assert set(links) == {"https://et.example/a"} and len(links) >= 3  # every headline is tappable
    width, height = (float(v) for v in reader.pages[0].mediabox[2:])
    assert (round(width), round(height)) == (595, 842)  # A4


@respx.mock
def test_bot_sends_the_text_then_the_pdf_and_the_spoken_form_asks_for_it(edition, chromium, funds, monkeypatch):
    monkeypatch.setattr(botmod.brief, "collect", lambda markets=("india", "us"): edition)
    token = "123456789:" + "A" * 35
    base = f"https://api.telegram.org/bot{token}"
    sent = respx.post(f"{base}/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.post(f"{base}/sendChatAction").mock(return_value=httpx.Response(200, json={"ok": True}))
    document = respx.post(f"{base}/sendDocument").mock(return_value=httpx.Response(200, json={"ok": True}))
    commander = Commander()
    commander.now = lambda: NOW
    bot = Bot(token, "4242", commander)

    bot.process({"update_id": 1, "message": {"chat": {"id": 4242, "type": "private"}, "from": {"id": 4242},
                                             "text": "/paper"}})
    assert json.loads(sent.calls.last.request.content)["text"] == "Here is today's edition."
    body = document.calls.last.request.content
    assert b'filename="jarvis-brief-2026-11-19.pdf"' in body and b"%PDF-" in body and b'name="chat_id"' in body

    assert botmod._spoken_to_command("Send me the news as a PDF") == "/paper"
    assert Commander().handle("News PDF").startswith(("Here is today's edition", "No market data"))
    bot.process({"update_id": 2, "message": {"chat": {"id": 999, "type": "private"}, "from": {"id": 999},
                                             "text": "/paper"}})
    assert document.call_count == 1  # a stranger gets nothing


def test_morning_message_attaches_the_edition_and_survives_a_failed_one(edition, monkeypatch):
    monkeypatch.setattr(botmod.brief, "collect", lambda markets=("india", "us"): edition)
    monkeypatch.setattr(botmod.paper, "render_pdf", lambda page, target=None: Path("/tmp/x.pdf"))
    commander = Commander()
    commander.now = lambda: NOW
    assert commander.morning_message() == "Good morning, Master. Here is today's edition."
    assert commander.take_attachment() == Path("/tmp/x.pdf") and commander.take_attachment() is None

    def broken(page, target=None):
        raise RuntimeError("the browser could not produce the PDF (TimeoutError)")

    monkeypatch.setattr(botmod.paper, "render_pdf", broken)
    assert "could not be produced" in commander.morning_message() and commander.take_attachment() is None


# ---- the investment suggestion inside the edition


def test_edition_carries_the_suggestion_with_units_reasons_and_caveats(edition, funds):
    from jarvis_sidekick.markets import invest

    page = paper.build_html(edition, now=NOW, suggestion=invest.suggest(25_000, "moderate"), suggestion_is_default=True)
    assert "<h2>Where Rs 25,000 could go · moderate risk · one time</h2>" in page
    assert "NIFTY 50 index ETF" in page and ">31</td><td>280.00</td><td>8,680</td>" in page
    assert "Stock ideas from the screen" in page and ">RELIANCE<" in page and "steady uptrend" in page
    assert "This uses a default amount. Send /invest to the bot once" in page
    assert "Nothing here promises a return." in page and "Left over after whole units" in page
    assert page.index("Where Rs 25,000 could go") < page.index("<h2>India</h2>")  # before the news, not buried after it

    chosen = paper.build_html(edition, now=NOW, suggestion=invest.suggest(40_000, "aggressive", monthly=True))
    assert "Where Rs 40,000 could go · aggressive risk · each month" in chosen
    assert "Amount and risk are the ones you last gave /invest." in chosen
    assert "Where Rs" not in paper.build_html(edition, now=NOW)  # no suggestion, no section


def test_invest_answers_are_remembered_for_the_edition(funds):
    from jarvis_sidekick.markets import invest

    assert invest.load_profile() == ({"amount": 25_000.0, "risk": "moderate", "monthly": False}, False)
    Commander().handle("/invest 30000 monthly aggressive")
    assert invest.load_profile() == ({"amount": 30_000.0, "risk": "aggressive", "monthly": True}, True)
    (invest._profile_path()).write_text('{"amount": "lots", "risk": "yolo"}')
    assert invest.load_profile()[1] is False  # a damaged file falls back to the default


def test_bot_edition_uses_the_saved_answers_and_survives_missing_prices(edition, funds, monkeypatch):
    seen = {}
    monkeypatch.setattr(botmod.brief, "collect", lambda markets=("india", "us"): edition)
    monkeypatch.setattr(botmod.paper, "render_pdf", lambda page, target=None: seen.setdefault("page", page) and target)
    commander = Commander()
    commander.now = lambda: NOW
    commander.handle("/invest 30000 once conservative")
    commander.handle("/paper")
    assert "Where Rs 30,000 could go · conservative risk · one time" in seen["page"]

    from jarvis_sidekick import _http
    from jarvis_sidekick.markets import invest

    def down(*args, **kwargs):
        raise _http.FetchError("no prices")

    monkeypatch.setattr(invest, "suggest", down)
    seen.clear()
    assert commander.handle("/paper") == "Here is today's edition." and "Where Rs" not in seen["page"]
