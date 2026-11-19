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
        router.typing = router.post(f"{BASE}/sendChatAction").mock(return_value=httpx.Response(200, json={"ok": True}))
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
    assert "I only know commands" in commander.handle("hello")


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
    assert "I only know commands" in commander.handle(f"/confirm {code}", spoken=True)

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


# ---- resume by file, and /doctor


def _serve_file(router, content, path="documents/file_9.pdf"):
    router.post(f"{BASE}/getFile").mock(return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": path}}))
    router.get(f"https://api.telegram.org/file/bot{TOKEN}/{path}").mock(return_value=httpx.Response(200, content=content))


def _pdf(text: str) -> bytes:
    """A one-page PDF holding *text*, built with pypdf's writer-free minimal syntax."""
    lines = text.splitlines()
    stream = "BT /F1 11 Tf 40 780 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


RESUME_PDF = _pdf("Asha Rao\nasha.rao@example.com\nEngineering Manager. Python, AWS, Kafka.")


def test_owner_sends_a_pdf_and_it_becomes_the_resume(telegram):
    pytest.importorskip("pypdf")
    from jarvis_sidekick import common, paths

    _serve_file(telegram, RESUME_PDF)
    _bot().process(_message(update_id=3) | {"message": _message()["message"] | {
        "document": {"file_id": "d1", "file_name": "My CV.pdf", "file_size": len(RESUME_PDF)}}})
    reply = _texts(telegram)[-1]
    assert reply.startswith("Resume saved. Name, email found; 4 skills recognised: Python, Kafka, AWS, Engineering Management")
    saved = paths.home() / "resume.pdf"
    assert saved.read_bytes() == RESUME_PDF and oct(saved.stat().st_mode & 0o777) == "0o600"
    assert common.resume_profile().email == "asha.rao@example.com"  # used with no SIDEKICK_RESUME set
    assert common.resume_profile().name == "Asha Rao" and common.resume_profile().phone == ""


@pytest.mark.parametrize(
    "content, name, size, why",
    [
        (b"MZ\x90\x00 not a document", "resume.pdf", 30, "PDF or a .docx"),  # a program renamed to .pdf
        (b"PK\x03\x04 zip archive", "payload.zip", 30, "PDF or a .docx"),
        (RESUME_PDF, "huge.pdf", 99_000_000, "limited to 5 MB"),
        (_pdf("No contact details here"), "empty.pdf", 400, "could not find a name and an email"),
    ],
)
def test_files_that_are_not_a_usable_resume_are_refused_and_nothing_is_kept(telegram, content, name, size, why):
    pytest.importorskip("pypdf")
    from jarvis_sidekick import paths

    _serve_file(telegram, content)
    _bot().process({"update_id": 4, "message": _message()["message"] | {
        "document": {"file_id": "d1", "file_name": name, "file_size": size}}})
    assert why in _texts(telegram)[-1]
    assert [p.name for p in paths.home().iterdir() if "resume" in p.name] == []


def test_a_stranger_cannot_replace_the_resume(telegram):
    from jarvis_sidekick import paths

    _serve_file(telegram, RESUME_PDF)
    bot = _bot()
    bot.process({"update_id": 5, "message": _message(chat_id=999, sender=999)["message"] | {
        "document": {"file_id": "d1", "file_name": "cv.pdf", "file_size": 10}}})
    assert bot.dropped == 1 and not telegram.sent.called and not (paths.home() / "resume.pdf").exists()


def test_doctor_command_reports_without_touching_telegram_itself(monkeypatch):
    from jarvis_sidekick import doctor

    monkeypatch.setattr(doctor, "run", lambda offline=False: [
        doctor.Check("news feeds", doctor.OK, "12 headlines"), doctor.Check("Telegram bot", doctor.OK, "x"),
        doctor.Check("jobs: remoteok", doctor.FAIL, "HTTP 503")])
    reply = Commander().handle("/doctor")
    assert reply == "ok  news feeds: 12 headlines\nFAIL  jobs: remoteok: HTTP 503\n\n1 failed"


# ---- called by name


def _at(hour, **kwargs):
    from datetime import datetime

    commander = Commander(**kwargs)
    commander.now = lambda: datetime(2026, 11, 19, hour, 30)
    return commander


def test_calling_it_by_name_alone_gets_a_greeting_for_the_time_of_day():
    commander = _at(9)
    for said in ("Jarvis", "jarvis.", "Hey Jarvis!", "Javis?", "OK, Jarvis"):
        assert commander.handle(said, spoken=True) == "Good morning, Master. What do you need?"
        assert commander.say == "Good morning, Master. What do you need?"
    assert _at(14, title="sir").handle("Jarvis") == "Good afternoon, sir. What do you need?"
    assert _at(19).greeting() == "Good evening" and _at(23).greeting() == _at(3).greeting() == "You are up late"


def test_greeting_uses_the_configured_time_zone_and_survives_a_bad_one():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    india, japan = Commander(timezone="Asia/Kolkata").now(), Commander(timezone="Asia/Tokyo").now()
    assert india.utcoffset().total_seconds() == 5.5 * 3600 and japan.utcoffset().total_seconds() == 9 * 3600
    assert abs((datetime.now(ZoneInfo("UTC")) - india).total_seconds()) < 5
    assert Commander(timezone="Mars/Olympus").now().tzinfo is not None


def test_asleep_it_answers_only_to_its_name():
    asked = []
    commander = _at(7, agent=lambda text: asked.append(text) or "answer")
    assert commander.handle("Jarvis, go to sleep", spoken=True) == "Yes, Master.\nGoing to sleep, Master. Say Jarvis to wake me."
    assert commander.asleep
    assert commander.handle("/brief") == "I am asleep, Master. Say Jarvis to wake me."
    assert commander.handle("what is the repo rate", spoken=True).startswith("I am asleep") and asked == []
    assert Commander().asleep  # it stays asleep across a restart

    assert commander.handle("Jarvis", spoken=True) == "Good morning, Master. I am awake. What do you need?"
    assert not commander.asleep
    commander.handle("/sleep")
    assert commander.handle("Jarvis, what is the repo rate") == "Yes, Master.\nanswer" and not commander.asleep
    commander.handle("/sleep")
    assert commander.handle("/wake") == "Good morning, Master. I am awake. What do you need?"


def test_shutdown_must_be_typed_and_happens_only_after_the_reply(telegram):
    from jarvis_sidekick import paths

    request = paths.home() / botmod.POWEROFF_REQUEST
    assert Commander().handle("/shutdown") == "Powering off is only available on the server install."

    commander = Commander(allow_poweroff=True)
    assert commander.handle("Jarvis, shut down the server", spoken=True).endswith("To pause me, say: go to sleep.")
    assert commander.take_action() is None and not request.exists()

    bot = _bot(commander)
    order = []
    telegram.sent.mock(side_effect=lambda r: order.append(request.exists()) or httpx.Response(200, json={"ok": True}))
    bot.process(_message("/shutdown"))
    assert order == [False] and request.exists()  # the reply went out first, then the request was written
    assert "Powering off the server, Master" in _texts(telegram)[-1]


def test_a_stranger_cannot_shut_the_server_down(telegram):
    from jarvis_sidekick import paths

    bot = _bot(Commander(allow_poweroff=True))
    bot.process(_message("/shutdown", chat_id=999, sender=999))
    assert bot.dropped == 1 and not (paths.home() / botmod.POWEROFF_REQUEST).exists()


def test_name_then_command_runs_the_command_and_answers_by_title(monkeypatch):
    collected = {"generated_at": "now", "indices": [{"name": "NIFTY 50", "price": 25227.95, "day_pct": -0.63,
                 "month_pct": 1.0, "half_year_pct": 2.0}], "movers": {}, "screens": {}, "news": {}, "unavailable": [],
                 "disclaimer": "Research aid."}
    monkeypatch.setattr(botmod.brief, "collect", lambda: collected)
    commander = Commander()
    reply = commander.handle("Jarvis, give me the market briefing.", spoken=True)
    assert reply.startswith("Yes, Master.\n# Market briefing") and "| NIFTY 50 | 25,227.95 | -0.63% |" in reply
    assert commander.say == "Yes, Master. NIFTY 50 is down 0.6 percent at 25,228."  # spoken: numbers, not the table

    plain = commander.handle("/brief")
    assert plain.startswith("# Market briefing") and not commander.say.startswith("Yes")


def test_words_that_merely_start_like_the_name_are_left_alone():
    commander = Commander(agent=lambda text: f"asked: {text}")
    assert commander.handle("Jarvisville is a town") == "asked: Jarvisville is a town"
    assert commander.handle("jarvis what is the repo rate") == "Yes, Master.\nasked: what is the repo rate"


def test_the_name_does_not_open_a_way_around_typed_confirmation(prepared):
    commander, job_id, calls, _ = prepared
    code = _code(commander.handle(f"/apply {job_id}"))
    commander.handle(f"Jarvis, /confirm {code}", spoken=True)
    commander.handle(f"Jarvis confirm {code}", spoken=True)
    assert True not in calls


def test_long_replies_are_cut_for_speech_and_links_are_dropped():
    spoken = botmod._speakable("See https://example.com/x | **bold** " + "word " * 400)
    assert "http" not in spoken and "|" not in spoken and "*" not in spoken
    assert len(spoken) < botmod.MAX_SPOKEN_CHARS + 40 and spoken.endswith("The rest is in the message.")


def test_it_shows_typing_and_offers_buttons_on_help(telegram):
    import json

    bot = _bot(Commander(agent=lambda text: "answer"))
    bot.process(_message("hello"))
    assert telegram.typing.called and "reply_markup" not in json.loads(telegram.sent.calls.last.request.content)
    bot.process(_message("/help", update_id=2))
    keyboard = json.loads(telegram.sent.calls.last.request.content)["reply_markup"]["keyboard"]
    assert [button["text"] for row in keyboard for button in row] == list(botmod.BUTTONS)


def test_with_a_brain_the_control_phrases_and_guards_still_hold(prepared):
    commander, job_id, calls, _ = prepared
    heard = []
    commander.agent = lambda text: heard.append(text) or "ok"
    assert "type /shutdown" in commander.handle("Jarvis, shut down the server", spoken=True)
    code = _code(commander.handle(f"/apply {job_id}"))
    commander.handle(f"confirm {code}", spoken=True)
    assert True not in calls and heard == [f"confirm {code}"]  # it reached the brain, which has no way to apply
    assert commander.handle("go to sleep").startswith("Going to sleep") and commander.asleep
