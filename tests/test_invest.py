from __future__ import annotations

import json

import httpx
import pytest
import respx

from jarvis_sidekick import _http
from jarvis_sidekick.bot import Bot, Commander
from jarvis_sidekick.markets import data, invest
from jarvis_sidekick.markets.data import Series

PRICES = {"NIFTYBEES.NS": 280.0, "JUNIORBEES.NS": 740.0, "MON100.NS": 190.0, "GOLDBEES.NS": 95.0}


@pytest.fixture
def market(monkeypatch):
    """Funds at fixed prices; RELIANCE and TCS rising steadily, INFY falling, MARUTI too dear for a slice."""

    def history(symbol, range_="1y", client=None):
        if symbol in PRICES:
            return Series(symbol, [PRICES[symbol]] * 30, [""] * 30)
        trend = {"RELIANCE.NS": 1.002, "TCS.NS": 1.0015, "INFY.NS": 0.998, "MARUTI.NS": 1.001}.get(symbol)
        if trend is None:
            raise _http.FetchError(f"no price data for {symbol}")
        start = 9000 if symbol == "MARUTI.NS" else 900
        return Series(symbol, [start * trend**i * (1.02 if i % 2 else 1) for i in range(260)], [""] * 260)

    monkeypatch.setattr(data, "history", history)


@pytest.mark.parametrize(
    "text, rupees",
    [("25000", 25_000), ("25,000", 25_000), ("25k", 25_000), ("25 thousand", 25_000), ("Rs 30K please", 30_000),
     ("1.5 lakh", 150_000), ("25-30K", 25_000), ("where should I invest 25 to 30 thousand", 25_000),
     ("invest ₹40,000 every month", 40_000), ("some money", None), ("3", None)],
)
def test_amounts_are_read_the_way_people_say_them(text, rupees):
    assert invest.parse_amount(text) == rupees


def test_every_split_adds_up_and_stocks_stay_the_minority():
    for risk, split in invest.SPLITS.items():
        assert sum(split.values()) == 100, risk
        assert split["stocks"] <= 30 and split["stocks"] < split["large"] + split["debt"]


def test_suggestion_buys_whole_units_from_live_prices_and_accounts_for_every_rupee(market):
    s = invest.suggest(25_000, "moderate")
    by_symbol = {line.symbol or "debt": line for line in s.lines}

    assert by_symbol["NIFTYBEES.NS"].units == 31 and by_symbol["NIFTYBEES.NS"].amount == 8750  # 35% / 280
    assert by_symbol["JUNIORBEES.NS"].units == 3 and by_symbol["MON100.NS"].units == 13
    assert by_symbol["GOLDBEES.NS"].units == 26 and by_symbol["debt"].amount == 3750
    # stock ideas: only steady uptrends, best first; the falling one is left out
    ideas = [line.symbol for line in s.lines if line.symbol and line.symbol not in PRICES]
    assert ideas == ["RELIANCE.NS", "TCS.NS", "MARUTI.NS"] and "INFY.NS" not in ideas
    assert by_symbol["MARUTI.NS"].units == 0  # one share costs more than its slice
    spent = sum(line.spent for line in s.lines if line.units or not line.symbol)
    assert spent + s.left_over == pytest.approx(25_000) and s.left_over > 0


def test_text_states_quantities_reasons_gaps_and_caveats(market):
    text = invest.to_text(invest.suggest(25_000, "moderate"), "Master")
    assert text.startswith("Rs 25,000 once, moderate risk, Master:")
    assert "35%  31 x NIFTY 50 index ETF [NIFTYBEES.NS] at about Rs 280.00 = Rs 8,680" in text
    assert "15%  Rs 3,750  into a liquid or short-duration debt fund, or PPF" in text
    assert "RELIANCE [RELIANCE.NS]" in text and "steady uptrend" in text
    assert "MARUTI [MARUTI.NS]: one unit costs" in text and "Left over after whole units" in text
    assert "not what companies are worth" in text and "Nothing here promises a return" in text
    spoken = invest.to_speech(invest.suggest(25_000, "moderate"), "Master")
    assert "RELIANCE, TCS, MARUTI" in spoken and "not a promise of profit" in spoken and "[" not in spoken


def test_overbought_names_are_left_out_of_the_ideas(monkeypatch):
    def history(symbol, range_="1y", client=None):
        if symbol not in ("RELIANCE.NS", "TCS.NS"):
            raise _http.FetchError("none")
        swing = 1.004 if symbol == "RELIANCE.NS" else 1.02  # RELIANCE has risen almost without a pause: RSI above 75
        return Series(symbol, [900 * 1.002**i * (swing if i % 2 else 1) for i in range(260)], [""] * 260)

    monkeypatch.setattr(data, "history", history)
    assert [idea.symbol for idea in invest._stock_ideas(3)] == ["TCS.NS"]


def test_missing_prices_are_reported_not_invented(market, monkeypatch):
    real = data.history
    monkeypatch.setattr(data, "history", lambda s, *a, **k: (_ for _ in ()).throw(_http.FetchError("down"))
                        if s == "GOLDBEES.NS" else real(s, *a, **k))
    s = invest.suggest(25_000, "conservative")
    assert "GOLDBEES.NS" in s.unavailable
    assert "Gold ETF [GOLDBEES.NS]: price unavailable right now" in invest.to_text(s)
    with pytest.raises(ValueError):
        invest.suggest(25_000, "yolo")


# ---- the conversation in the bot


def test_invest_asks_only_what_it_does_not_know(market):
    commander = Commander()
    assert commander.handle("/invest") == "How much do you want to invest? For example: 25000"
    assert commander.handle("25k").startswith("Rs 25,000. Is that one time, or every month?")
    assert commander.choices == ["One time", "Every month"]
    assert commander.handle("Every month").startswith("How much of a fall")
    assert commander.choices == ["Conservative", "Moderate", "Aggressive"]
    answer = commander.handle("Moderate")
    assert answer.startswith("Rs 25,000 each month, moderate risk, Master:") and commander.choices == []

    # everything given at once: no questions
    assert Commander().handle("/invest 30000 once aggressive").startswith("Rs 30,000 once, aggressive risk")


def test_a_sentence_about_investing_starts_the_conversation_typed_or_spoken(market):
    said = "Jarvis, give me the market brief and tell me the stocks to put money in, 25 to 30K"
    commander = Commander()
    reply = commander.handle(said, spoken=True)
    assert reply.startswith("Yes, Master.\nRs 25,000. Is that one time, or every month?")
    assert Commander().handle("where should I invest 25000").startswith("Rs 25,000. Is that one time")
    assert Commander().handle("Where to invest") == "How much do you want to invest? For example: 25000"


def test_an_unrelated_message_ends_an_unfinished_invest_conversation(market):
    commander = Commander()
    commander.handle("/invest 25000")
    assert "Rs 3,500" in commander.handle("/plan 10000 10")  # a command still works mid-question
    assert commander.handle("Moderate").startswith("I only know commands")  # no longer an answer to anything
    typed = Commander()
    assert typed.handle("shut down the server").startswith("I only know commands")  # power-off needs /shutdown


@respx.mock
def test_bot_shows_answer_buttons_then_brings_the_main_buttons_back(market):
    token = "123456789:" + "A" * 35
    sent = respx.post(f"https://api.telegram.org/bot{token}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True}))
    respx.post(f"https://api.telegram.org/bot{token}/sendChatAction").mock(return_value=httpx.Response(200, json={"ok": True}))
    bot = Bot(token, "4242", Commander())

    def say(text, update_id):
        bot.process({"update_id": update_id, "message": {"chat": {"id": 4242, "type": "private"},
                                                         "from": {"id": 4242}, "text": text}})
        return json.loads(sent.calls.last.request.content).get("reply_markup", {})

    first = say("/invest 25000 once", 1)
    assert [b["text"] for b in first["keyboard"][0]] == ["Conservative", "Moderate", "Aggressive"]
    assert first["one_time_keyboard"] is True
    final = say("Moderate", 2)
    assert final["keyboard"][0][0]["text"] == "News PDF"  # main buttons restored with the answer
