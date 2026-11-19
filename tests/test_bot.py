from __future__ import annotations

import httpx
import pytest
import respx

from jarvis_sidekick import bot as botmod
from jarvis_sidekick.bot import Bot, Commander
from jarvis_sidekick.jobs import apply, match
from jarvis_sidekick.jobs.sources import Job

TOKEN = "123456789:" + "A" * 35
OWNER = 4242
BASE = f"https://api.telegram.org/bot{TOKEN}"


def _message(text=None, *, chat_id=OWNER, sender=OWNER, chat_type="private", voice=None, update_id=1, is_bot=False):
    message = {"chat": {"id": chat_id, "type": chat_type}, "from": {"id": sender, "is_bot": is_bot}}
    if text is not None:
        message["text"] = text
    if voice is not None:
        message["voice"] = voice
    return {"update_id": update_id, "message": message}


@pytest.fixture
def telegram():
    with respx.mock(assert_all_called=False) as router:
        router.sent = router.post(f"{BASE}/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
        yield router


def _texts(router):
    import json

    return [json.loads(call.request.content)["text"] for call in router.sent.calls]


def _bot(commander=None, **kwargs):
    return Bot(TOKEN, str(OWNER), commander or Commander(), **kwargs)


@pytest.mark.parametrize("owner", ["", "abc", "-5", None])
def test_bot_will_not_start_without_a_numeric_owner(owner):
    with pytest.raises(ValueError, match="will not run without it"):
        Bot(TOKEN, owner, Commander())


def test_bot_rejects_a_malformed_token_without_echoing_it():
    with pytest.raises(ValueError) as caught:
        Bot("not-a-token-SECRET", str(OWNER), Commander())
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize(
    "update",
    [
        _message("/brief", chat_id=999, sender=999),  # a stranger
        _message("/brief", chat_id=-100123, sender=OWNER, chat_type="group"),  # owner, but in a group
        _message("/brief", chat_id=OWNER, sender=999),  # someone else writing into the chat
        _message("/brief", is_bot=True),
        {"update_id": 5, "edited_message": {"text": "/brief"}},
        {"update_id": 6, "message": "garbage"},
    ],
)
def test_everyone_but_the_owner_is_dropped_silently(telegram, update):
    calls = []
    bot = _bot(Commander(agent=lambda text: calls.append(text) or "x"))
    bot.process(update)
    assert bot.dropped == 1 and calls == [] and not telegram.sent.called


def test_owner_text_reaches_the_commander_and_the_reply_is_sent(telegram):
    bot = _bot(Commander(agent=lambda text: f"echo {text}"))
    bot.process(_message("what moved the market today?"))
    assert _texts(telegram) == ["echo what moved the market today?"]


def test_voice_note_is_transcribed_announced_and_run(telegram):
    telegram.post(f"{BASE}/getFile").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/file_1.oga"}})
    )
    telegram.get(f"https://api.telegram.org/file/bot{TOKEN}/voice/file_1.oga").mock(
        return_value=httpx.Response(200, content=b"OggS-audio")
    )
    heard = []
    bot = _bot(Commander(), transcribe=lambda audio: heard.append(audio) or "Help.")
    bot.process(_message(voice={"file_id": "f1", "duration": 4, "file_size": 900}))
    assert heard == [b"OggS-audio"]
    assert _texts(telegram)[0] == "Heard: Help." and _texts(telegram)[1].startswith("Commands")


@pytest.mark.parametrize(
    "voice, file_path",
    [
        ({"file_id": "f", "duration": 999, "file_size": 10}, "voice/a.oga"),
        ({"file_id": "f", "duration": 5, "file_size": 99_000_000}, "voice/a.oga"),
        ({"file_id": "f", "duration": 5, "file_size": 10}, "../../etc/passwd"),
        ({"file_id": "f", "duration": 5, "file_size": 10}, "voice/a.oga?x=http://evil"),
    ],
)
def test_oversized_or_odd_voice_notes_are_refused_before_transcription(telegram, voice, file_path):
    telegram.post(f"{BASE}/getFile").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": file_path}})
    )
    download = telegram.get(url__startswith="https://api.telegram.org/file/").mock(return_value=httpx.Response(200))
    heard = []
    bot = _bot(transcribe=lambda audio: heard.append(audio) or "x")
    bot.process(_message(voice=voice))
    assert heard == [] and not download.called
    assert _texts(telegram)[0].startswith("Could not read that voice note")


def test_rate_limit_stops_a_flood(telegram):
    calls = []
    bot = _bot(Commander(agent=lambda text: calls.append(text) or "ok"))
    for i in range(botmod.RATE_LIMIT + 5):
        bot.process(_message(f"q{i}"))
    assert len(calls) == botmod.RATE_LIMIT
    assert _texts(telegram)[-1].startswith("Slow down")


def test_backlog_is_skipped_and_offsets_advance_even_when_handling_fails(telegram):
    updates = telegram.post(f"{BASE}/getUpdates").mock(
        side_effect=[
            httpx.Response(200, json={"ok": True, "result": [_message("/apply old", update_id=70)]}),
            httpx.Response(200, json={"ok": True, "result": [_message("hi", update_id=71), _message("yo", update_id=72)]}),
            httpx.Response(200, json={"ok": True, "result": []}),
        ]
    )
    seen = []
    bot = _bot(Commander(agent=lambda text: seen.append(text) or "ok"))
    bot.skip_backlog()
    assert seen == []  # the old /apply was never run
    telegram.sent.mock(side_effect=[httpx.Response(500, json={"ok": False}), httpx.Response(200, json={"ok": True})])
    assert bot.poll_once(timeout=0) == 2
    assert seen == ["hi", "yo"]
    bot.poll_once(timeout=0)
    import json

    assert [json.loads(c.request.content).get("offset") for c in updates.calls] == [-1, 71, 73]


def test_transport_errors_never_contain_the_token(telegram):
    telegram.sent.mock(side_effect=httpx.ConnectError(f"cannot reach {BASE}/sendMessage"))
    with pytest.raises(botmod.TelegramError) as caught:
        _bot().send("hi")
    assert TOKEN not in str(caught.value) and caught.value.__suppress_context__


# ---- commands


def test_spoken_sentences_map_to_commands_but_never_to_apply_or_confirm():
    convert = botmod._spoken_to_command
    assert convert("Give me the market briefing.") == "/brief"
    assert convert("Find me engineering manager jobs") == "/jobs engineering manager"
    assert convert("Search director of engineering in Singapore") == "/jobs director of engineering in Singapore"
    assert convert("Screen India") == "/screen India"
    assert convert("What is the repo rate?") == "What is the repo rate"
    # A leading slash in a transcript is stripped, so speech cannot smuggle a raw command.
    assert convert("/confirm 123456") == "confirm 123456"
    assert convert("/apply abcdefabcdef") == "apply abcdefabcdef"


def test_plan_and_unknown_commands():
    commander = Commander()
    assert "Rs 3,500" in commander.handle("/plan 10000 10")
    assert commander.handle("/plan abc 10").startswith("/plan failed")
    assert commander.handle("/rm -rf").startswith("Unknown command")
    assert "need OpenJarvis" in commander.handle("hello")


@pytest.fixture
def prepared(resume_file, monkeypatch):
    """A prepared job and a stand-in for the browser that records what it was asked to do."""
    from jarvis_sidekick import resume

    profile = resume.load(resume_file)
    job = Job(source="t", title="Director of Engineering", company="Acme", url="https://jobs.lever.co/acme/1",
              description="Python, AWS")
    apply.prepare(match.score(job, profile), profile)
    calls = []

    def fake_submit(url, profile, letter="", *, confirm=False, **kwargs):
        calls.append(confirm)
        return apply.Result("submitted" if confirm else "ready", "form sent" if confirm else "dry run", ["email"])

    monkeypatch.setattr(apply, "submit", fake_submit)
    now = [1000.0]
    commander = Commander(resume_path=str(resume_file), clock=lambda: now[0])
    return commander, job.id, calls, now


def _code(reply: str) -> str:
    import re

    return re.search(r"/confirm (\d{6})", reply).group(1)


def test_apply_fills_only_and_the_typed_code_sends_once(prepared):
    commander, job_id, calls, _ = prepared
    reply = commander.handle(f"/apply {job_id}")
    assert "nothing sent" in reply and calls == [False]
    code = _code(reply)
    assert commander.handle(f"/confirm {code}") == "submitted: form sent" and calls == [False, True]
    assert commander.handle(f"/confirm {code}").startswith("Nothing is waiting")  # single use
    assert calls == [False, True]


def test_confirm_cannot_be_spoken_guessed_or_used_late(prepared):
    commander, job_id, calls, now = prepared

    # spoken: the transcript loses its slash and goes to the agent, never to /confirm
    code = _code(commander.handle(f"/apply {job_id}"))
    assert "need OpenJarvis" in commander.handle(f"/confirm {code}", spoken=True)

    # guessed: three wrong tries void the code, including the right one
    wrong = "000000" if code != "000000" else "111111"
    assert commander.handle(f"/confirm {wrong}") == "Wrong code."
    assert commander.handle(f"/confirm {wrong}") == "Wrong code."
    assert commander.handle(f"/confirm {wrong}").startswith("Wrong code three times")
    assert commander.handle(f"/confirm {code}").startswith("Nothing is waiting")

    # late: the code expires
    code = _code(commander.handle(f"/apply {job_id}"))
    now[0] += botmod.CODE_TTL_SECONDS + 1
    assert commander.handle(f"/confirm {code}").startswith("Nothing is waiting")

    assert True not in calls  # nothing was ever sent


def test_apply_rejects_ids_that_are_not_ids(prepared):
    commander, _, calls, _ = prepared
    assert commander.handle("/apply ../../etc").startswith("Usage")
    assert commander.handle("/apply ffffffffffff").startswith("/apply failed: no prepared job")
    assert calls == []


def test_agent_tool_list_has_nothing_that_acts():
    forbidden = {"job_apply", "shell_exec", "file_write", "code_interpreter", "apply_patch", "http_request",
                 "channel_send", "git_tool", "docker_shell_exec", "repl", "db_query"}
    assert not forbidden & set(botmod.AGENT_TOOLS)
