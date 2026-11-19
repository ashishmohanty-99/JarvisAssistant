from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

anthropic = pytest.importorskip("anthropic")

from jarvis_sidekick import brain as brainmod
from jarvis_sidekick import paths
from jarvis_sidekick.bot import Commander
from jarvis_sidekick.brain import Brain
from jarvis_sidekick.markets.data import Series


def _text(text):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def _tool(name, tool_input, tool_id="tu_1"):
    block = SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)
    thinking = SimpleNamespace(type="thinking", thinking="", signature="sig")
    return SimpleNamespace(stop_reason="tool_use", content=[thinking, block])


class FakeClaude:
    """Stands in for the SDK client: replays scripted responses and records every request."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _brain(*responses, **kwargs):
    client = FakeClaude(*responses)
    return Brain(client=client, now=lambda: datetime(2026, 11, 19, 9, 30), **kwargs), client


@pytest.fixture
def prices(monkeypatch):
    def history(symbol, range_="1y", client=None):
        if symbol == "BAD.NS":
            raise brainmod._http.FetchError("no price data for BAD.NS")
        closes = [100 * 1.002**i * (1.004 if i % 2 else 1) for i in range(260)]
        return Series(symbol, closes, [""] * 260)

    monkeypatch.setattr(brainmod.data, "history", history)


def test_a_question_runs_the_tool_it_needs_and_answers_in_words(prices):
    brain, client = _brain(_tool("quote", {"symbols": ["INFY.NS"]}), _text("Infosys is in a steady uptrend, Master."))

    assert brain.ask("How is Infosys doing?") == "Infosys is in a steady uptrend, Master."

    first, second = client.requests
    assert first["model"] == "claude-opus-5-5" and first["output_config"] == {"effort": "low"}
    assert first["fallbacks"] == "default" and first["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in first  # adaptive by default; disabling it is rejected on this model
    assert first["messages"] == [{"role": "user", "content": "How is Infosys doing?"}]
    # the assistant turn goes back unchanged, thinking block included, then all results in one user message
    assert second["messages"][1]["content"][0].type == "thinking"
    (result,) = second["messages"][2]["content"]
    assert result["tool_use_id"] == "tu_1" and json.loads(result["content"])[0]["symbol"] == "INFY.NS"


def test_the_conversation_is_remembered_as_plain_text_and_survives_a_restart(prices):
    brain, _ = _brain(_tool("quote", {"symbols": ["TCS.NS"]}), _text("TCS is up."), _text("Yes, the same one."))
    brain.ask("How is TCS?")
    brain.ask("Is that the IT company?")
    saved = json.loads((paths.home() / "conversation.json").read_text())
    assert saved == [{"role": "user", "content": "How is TCS?"}, {"role": "assistant", "content": "TCS is up."},
                     {"role": "user", "content": "Is that the IT company?"},
                     {"role": "assistant", "content": "Yes, the same one."}]
    assert oct((paths.home() / "conversation.json").stat().st_mode & 0o777) == "0o600"

    restarted, client = _brain(_text("Still up."))
    restarted.ask("And now?")
    assert [m["content"] for m in client.requests[0]["messages"]][:2] == ["How is TCS?", "TCS is up."]


def test_history_is_capped():
    brain, client = _brain(*[_text(f"a{i}") for i in range(20)])
    for i in range(20):
        brain.ask(f"q{i}")
    assert len(brain.history) == 2 * brainmod.MAX_HISTORY_TURNS
    assert client.requests[-1]["messages"][0]["content"] == "q7"  # the oldest turns have dropped off


def test_every_tool_reads_and_none_acts():
    brain, _ = _brain()
    names = {t["name"] for t in brain.tool_definitions()}
    assert names == {"market_brief", "stock_screen", "quote", "investment_plan", "job_search", "application_status",
                     "channel_report", "read_web_page", "remember", "web_search"}
    assert not any(word in name for name in names for word in ("apply", "confirm", "shutdown", "send", "exec", "write"))
    offline, _ = _brain(web_search=False)
    assert "web_search" not in {t["name"] for t in offline.tool_definitions()}
    for tool in brain.tool_definitions():
        if "input_schema" in tool:
            assert set(tool["input_schema"]["required"]) <= set(tool["input_schema"]["properties"])


def test_notes_are_saved_shown_to_the_model_outside_the_cached_text_and_forgettable():
    brain, client = _brain(_tool("remember", {"note": "Horizon  10 years,\n moderate risk"}), _text("Noted."),
                           _text("You said ten years."))
    brain.ask("My horizon is 10 years and I am fine with moderate risk")
    assert brain.notes() == ["Horizon 10 years, moderate risk"]

    brain.ask("What is my horizon?")
    stable, changing = client.requests[-1]["system"]
    assert stable["cache_control"] == {"type": "ephemeral"} and "10 years" not in stable["text"]
    assert "Horizon 10 years, moderate risk" in changing["text"] and "Thursday 19 November 2026, 09:30" in changing["text"]

    commander = Commander(agent=brain.ask, brain=brain)
    assert commander.handle("/memory") == "What I remember about you:\n- Horizon 10 years, moderate risk"
    assert "forgotten" in commander.handle("/forget")
    assert brain.notes() == [] and brain.history == [] and commander.handle("/memory") == "I have no notes yet."


def test_a_failing_or_unknown_tool_is_reported_to_the_model_not_raised(prices):
    brain, client = _brain(_tool("quote", {"symbols": ["BAD.NS"]}), _tool("launch_missiles", {}, "tu_2"),
                           _text("I could not get that price."))
    assert brain.ask("Price of BAD?") == "I could not get that price."
    first_result = client.requests[1]["messages"][-1]["content"][0]
    second_result = client.requests[2]["messages"][-1]["content"][0]
    assert first_result["is_error"] is True and "no price data" in first_result["content"]
    assert second_result["is_error"] is True and "unknown tool" in second_result["content"]


def test_it_stops_after_a_fixed_number_of_tool_rounds(prices):
    brain, client = _brain(*[_tool("quote", {"symbols": ["X.NS"]}, f"tu_{i}") for i in range(20)])
    assert "ran out of steps" in brain.ask("loop forever")
    assert len(client.requests) == brainmod.MAX_TOOL_ROUNDS + 1


def test_refusals_limits_and_outages_become_plain_replies():
    refused, _ = _brain(SimpleNamespace(stop_reason="refusal", content=[]))
    assert refused.ask("x") == "I can't help with that one, Master."
    refused.forget()

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    down, _ = _brain(anthropic.APIConnectionError(request=request))
    assert down.ask("x") == "I could not reach my language model. Try again shortly."
    assert down.history == []  # a failed turn is not remembered

    (paths.home() / "brain-usage.json").unlink()  # the two turns above counted towards today
    capped, client = _brain(_text("one"), daily_limit=1)
    assert capped.ask("first") == "one"
    assert "today's limit of 1" in capped.ask("second") and len(client.requests) == 1


def test_from_env_needs_a_key(monkeypatch):
    kwargs = {"title": "Master", "now": datetime.now}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert brainmod.from_env(**kwargs) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("SIDEKICK_MODEL", "claude-sonnet-5-5")
    built = brainmod.from_env(**kwargs)
    assert built.model == "claude-sonnet-5-5" and built.web_search is True


# ---- the bot with a brain


def test_with_a_brain_a_spoken_sentence_is_a_conversation_not_a_command():
    heard = []
    commander = Commander(agent=lambda text: heard.append(text) or "Here is my view.")
    said = "Give me the market briefing and tell me which stocks to put money in"
    assert commander.handle(f"Jarvis, {said}", spoken=True) == "Yes, Master.\nHere is my view."
    assert heard == [said]  # not shortened to /brief
    # without a brain the same sentence is matched to the nearest command instead
    assert Commander().handle(said, spoken=True) == "How much do you want to invest? For example: 25000"


def test_buttons_are_commands_without_a_brain_and_requests_with_one(monkeypatch):
    monkeypatch.setattr(Commander, "_status", lambda self, _: "status table")
    assert Commander().handle("My applications") == "status table"
    heard = []
    with_brain = Commander(agent=lambda text: heard.append(text) or "You have two in progress.")
    assert with_brain.handle("My applications") == "You have two in progress." and heard == ["My applications"]
    assert with_brain.handle("Go to sleep").startswith("Going to sleep")  # control buttons never go to the model


def test_morning_message_fires_once_a_day_at_the_set_time_and_not_while_asleep():
    clock = [datetime(2026, 11, 19, 7, 29)]
    commander = Commander(morning="07:30", agent=lambda text: "Good morning. Markets are calm.")
    commander.now = lambda: clock[0]
    assert not commander.morning_due()
    clock[0] = datetime(2026, 11, 19, 7, 31)
    assert commander.morning_due() and not commander.morning_due()
    assert commander.morning_message() == "Good morning. Markets are calm."
    clock[0] = datetime(2026, 11, 20, 9, 0)
    commander.handle("/sleep")
    assert not commander.morning_due()
    commander.handle("/wake")
    assert commander.morning_due()
    assert not Commander(morning="").morning_due() and not Commander(morning="25:99").morning_due()
