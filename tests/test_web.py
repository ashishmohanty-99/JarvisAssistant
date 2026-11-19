from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jarvis_sidekick import paths, web
from jarvis_sidekick.bot import Commander

STATE = {"generated_at": "2026-11-19T02:00", "indices": [
    {"group": "India", "name": "NIFTY 50", "price": 25227.95, "day_pct": -0.63, "spark": [1.0, 2.0, 3.0], "rsi": 50}],
    "news": {"india": [{"title": "Sensex falls", "link": "https://et.example/a", "source": "ET", "summary": "s"}],
             "world": []}, "movers": {}, "screens": {}, "unavailable": [], "disclaimer": "d"}


@pytest.fixture
def screen(monkeypatch):
    """A running screen on a free local port, with stand-ins for Telegram, the microphone and the voice."""
    monkeypatch.setattr(web.brief, "collect", lambda markets=("india",): STATE)
    sent, clock = [], [1_000_000.0]
    app = web.WebApp(Commander(agent=lambda text: f"You said: {text}"), sent.append,
                     transcribe=lambda audio, fmt: "Jarvis" if audio == b"wake" else f"heard {fmt}",
                     synthesize=lambda text: b"ID3" + text.encode(), clock=lambda: clock[0])
    server = web.serve(app, 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield app, base, sent, clock
    server.shutdown()


HEADERS = {"X-Jarvis": "1"}


def _sign_in(app, base, sent) -> dict:
    assert httpx.post(f"{base}/api/login/start", headers=HEADERS).json() == {"sent": True}
    code = sent[-1].split("code: ")[1][:6]
    response = httpx.post(f"{base}/api/login/finish", headers=HEADERS, json={"code": code})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Strict" in cookie
    return {**HEADERS, "Cookie": cookie.split(";")[0]}


def test_pages_load_without_signing_in_but_nothing_else_does(screen):
    _, base, _, _ = screen
    page = httpx.get(base + "/")
    assert page.status_code == 200 and "JARVIS" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"] and page.headers["x-content-type-options"] == "nosniff"
    assert httpx.get(base + "/app.js").headers["content-type"].startswith("text/javascript")
    assert httpx.get(base + "/api/session").json() == {"signed_in": False}
    for path in ("/api/state", "/api/audio/abcdefgh", "/api/file/x.pdf"):
        assert httpx.get(base + path).status_code == 401
    assert httpx.post(base + "/api/say", headers=HEADERS, json={"text": "/brief"}).status_code == 401
    assert httpx.get(base + "/../../etc/passwd").status_code in (401, 404)


def test_sign_in_needs_the_code_sent_to_telegram_once_and_in_time(screen):
    app, base, sent, clock = screen
    headers = _sign_in(app, base, sent)
    assert httpx.get(base + "/api/session", headers=headers).json() == {"signed_in": True}
    used = sent[-1].split("code: ")[1][:6]
    assert httpx.post(f"{base}/api/login/finish", headers=HEADERS, json={"code": used}).status_code == 401  # single use

    httpx.post(f"{base}/api/login/start", headers=HEADERS)
    clock[0] += web.CODE_TTL_SECONDS + 1
    late = sent[-1].split("code: ")[1][:6]
    assert httpx.post(f"{base}/api/login/finish", headers=HEADERS, json={"code": late}).status_code == 401

    stored = json.loads((paths.home() / "web-sessions.json").read_text())
    assert headers["Cookie"].split("=")[1] not in json.dumps(stored)  # only a hash of the session is kept
    clock[0] += web.SESSION_DAYS * 86_400
    assert httpx.get(base + "/api/session", headers=headers).json() == {"signed_in": False}  # sessions expire


def test_codes_cannot_be_guessed_or_requested_endlessly(screen):
    _app, base, sent, _ = screen
    httpx.post(f"{base}/api/login/start", headers=HEADERS)
    real = sent[-1].split("code: ")[1][:6]
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(web.CODE_ATTEMPTS):
        assert httpx.post(f"{base}/api/login/finish", headers=HEADERS, json={"code": wrong}).status_code == 401
    assert httpx.post(f"{base}/api/login/finish", headers=HEADERS, json={"code": real}).status_code == 401  # burned
    for _ in range(web.CODES_PER_WINDOW - 1):
        assert httpx.post(f"{base}/api/login/start", headers=HEADERS).status_code == 200
    assert httpx.post(f"{base}/api/login/start", headers=HEADERS).status_code == 429
    assert len(sent) == web.CODES_PER_WINDOW


def test_requests_another_site_could_forge_are_refused(screen):
    app, base, sent, _ = screen
    headers = _sign_in(app, base, sent)
    cookie_only = {"Cookie": headers["Cookie"]}
    assert httpx.post(base + "/api/say", headers=cookie_only, json={"text": "/sleep"}).status_code == 403
    cross = {**headers, "Origin": "https://evil.example"}
    assert httpx.post(base + "/api/say", headers=cross, json={"text": "/sleep"}).status_code == 403
    assert not app.commander.asleep


def test_typed_and_spoken_requests_answer_with_audio_that_plays_once(screen):
    app, base, sent, _ = screen
    headers = _sign_in(app, base, sent)
    answer = httpx.post(base + "/api/say", headers=headers, json={"text": "how are markets"}).json()
    assert answer["reply"] == "You said: how are markets" and answer["choices"] == []
    audio = httpx.get(base + answer["audio"], headers=headers)
    assert audio.headers["content-type"] == "audio/mpeg" and audio.content == b"ID3You said: how are markets"
    assert httpx.get(base + answer["audio"], headers=headers).status_code == 404  # cannot be replayed or reused

    spoken = httpx.post(base + "/api/voice", headers={**headers, "Content-Type": "audio/mp4"}, content=b"x" * 2000).json()
    assert spoken["heard"] == "heard mp4" and spoken["reply"] == "You said: heard mp4"
    woke = httpx.post(base + "/api/voice", headers={**headers, "Content-Type": "audio/webm;codecs=opus"}, content=b"wake")
    assert "What do you need?" in woke.json()["reply"]
    odd = httpx.post(base + "/api/voice", headers={**headers, "Content-Type": "application/x-msdownload"}, content=b"MZ")
    assert "not supported" in odd.json()["error"]
    huge = httpx.post(base + "/api/voice", headers={**headers, "Content-Type": "audio/mp4"},
                      content=b"x" * (web.MAX_AUDIO_BYTES + 1))
    assert huge.status_code == 400


@pytest.mark.parametrize("text", ["/confirm 123456", "/publish public", "/shutdown", "/apply abcdefabcdef",
                                  "Publish public", "/youtube", "/SHUTDOWN now"])
def test_commands_that_act_are_refused_on_the_screen(screen, text):
    app, base, sent, _ = screen
    app.commander.allow_poweroff = True
    headers = _sign_in(app, base, sent)
    answer = httpx.post(base + "/api/say", headers=headers, json={"text": text}).json()
    assert "done in Telegram" in answer["reply"] and "audio" not in answer
    assert not (paths.home() / "poweroff.request").exists()


def test_board_data_buttons_and_the_pdf_download(screen, monkeypatch):
    app, base, sent, _ = screen
    headers = _sign_in(app, base, sent)
    board = httpx.get(base + "/api/state", headers=headers).json()
    assert board["indices"] == [{"group": "India", "name": "NIFTY 50", "price": 25227.95, "day_pct": -0.63,
                                 "spark": [1.0, 2.0, 3.0]}]
    assert board["headlines"] == [{"title": "Sensex falls", "link": "https://et.example/a", "source": "ET"}]
    assert board["title"] == "Master" and board["voice"] is True and "News PDF" in board["buttons"]

    pdf = paths.home() / "jarvis-brief-2026-11-19.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    def fake_paper(_):
        app.commander._attachment = pdf
        return "Here is today's edition."

    monkeypatch.setattr(app.commander, "_paper", fake_paper)
    answer = httpx.post(base + "/api/say", headers=headers, json={"text": "/paper"}).json()
    assert answer["file"] == {"name": pdf.name, "url": f"/api/file/{pdf.name}"}
    got = httpx.get(base + answer["file"]["url"], headers=headers)
    assert got.content == b"%PDF-1.4 test" and got.headers["content-type"] == "application/pdf"
    assert httpx.get(base + "/api/file/web-sessions.json", headers=headers).status_code == 404  # only offered files
    assert httpx.get(base + "/api/file/other.pdf", headers=headers).status_code == 404


def test_it_only_listens_on_this_machine():
    with pytest.raises(ValueError, match="this machine only"):
        web.serve(web.WebApp(Commander(), lambda text: None), 0, host="0.0.0.0")


def test_sign_out_ends_the_session(screen):
    app, base, sent, _ = screen
    headers = _sign_in(app, base, sent)
    httpx.post(base + "/api/logout", headers=headers)
    assert httpx.get(base + "/api/state", headers=headers).status_code == 401
    assert Path(paths.home() / "web-sessions.json").read_text() == "{}"
