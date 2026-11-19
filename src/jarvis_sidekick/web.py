"""The Jarvis screen: a small web server for a graphical, talking front end.

It listens on this machine only (127.0.0.1). To reach it from a tablet you put a
private network or an HTTPS proxy you control in front of it; it is never meant
to face the open internet directly.

Signing in takes a one-time code that the bot sends to your Telegram, so the
page is tied to the same single owner as everything else. The page can ask for
anything a voice note can, and nothing more: sending an application, publishing
a video and powering off stay typed commands in Telegram.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from jarvis_sidekick import paths
from jarvis_sidekick.bot import BUTTONS, PUBLISH_CHOICES, Commander, _speakable
from jarvis_sidekick.markets import brief

logger = logging.getLogger(__name__)

SESSION_DAYS = 30
CODE_TTL_SECONDS = 300
CODE_ATTEMPTS = 5
CODES_PER_WINDOW = 3  # sign-in codes that may be requested per 10 minutes
MAX_TEXT_BYTES = 4_000
MAX_AUDIO_BYTES = 5_000_000
STATE_TTL_SECONDS = 600
# Typed commands the page refuses: each acts on the outside world and belongs in Telegram.
TELEGRAM_ONLY = ("/confirm", "/publish", "/shutdown", "/youtube", "/apply", "/skip", "/text")
_AUDIO_FORMATS = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg", "audio/wav": "wav",
                  "audio/x-wav": "wav", "audio/mpeg": "mp3", "video/webm": "webm", "video/mp4": "mp4"}
_ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"),
           "/app.js": ("app.js", "text/javascript; charset=utf-8")}
_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; media-src 'self' blob:; base-uri 'none'; form-action 'none'; "
                               "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class WebApp:
    """State and logic of the screen, separate from the HTTP plumbing so it can be tested directly."""

    def __init__(self, commander: Commander, send_code: Callable[[str], None], *,
                 transcribe: Callable[[bytes, str], str] | None = None,
                 synthesize: Callable[[str], bytes] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.commander, self._send_code, self._clock = commander, send_code, clock
        self._transcribe, self._synthesize = transcribe, synthesize
        self._lock = threading.Lock()
        self._code: tuple[str, float, int] | None = None  # code, expiry, wrong attempts
        self._code_requests: list[float] = []
        self._speech: OrderedDict[str, str] = OrderedDict()  # id -> text waiting to be spoken
        self._files: OrderedDict[str, Path] = OrderedDict()
        self._state: tuple[float, dict] | None = None

    # ---- signing in

    def _sessions(self) -> dict[str, float]:
        try:
            data = json.loads((paths.home() / "web-sessions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        now = self._clock()
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float)) and v > now}

    def _save_sessions(self, sessions: dict[str, float]) -> None:
        path = paths.home() / "web-sessions.json"
        path.write_text(json.dumps(sessions), encoding="utf-8")  # hashes only: a stolen file cannot sign in
        path.chmod(0o600)

    def start_login(self) -> bool:
        """Send a fresh code to the owner's Telegram. False when too many were asked for recently."""
        with self._lock:
            now = self._clock()
            self._code_requests = [t for t in self._code_requests if now - t < 600]
            if len(self._code_requests) >= CODES_PER_WINDOW:
                return False
            self._code_requests.append(now)
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._code = (code, now + CODE_TTL_SECONDS, 0)
        self._send_code(f"Jarvis screen sign-in code: {code}\nIt works once, for 5 minutes. "
                        "If you did not ask for it, ignore this message.")
        return True

    def finish_login(self, code: str) -> str | None:
        """Exchange a correct code for a session token. The code dies on success, expiry or too many misses."""
        with self._lock:
            if self._code is None:
                return None
            expected, expires, misses = self._code
            if self._clock() > expires:
                self._code = None
                return None
            if not hmac.compare_digest(str(code).strip().encode(), expected.encode()):
                self._code = None if misses + 1 >= CODE_ATTEMPTS else (expected, expires, misses + 1)
                return None
            self._code = None
            token = secrets.token_urlsafe(32)
            sessions = self._sessions()
            sessions[_hash(token)] = self._clock() + SESSION_DAYS * 86_400
            self._save_sessions(sessions)
            return token

    def signed_in(self, token: str) -> bool:
        return bool(token) and _hash(token) in self._sessions()

    def sign_out(self, token: str) -> None:
        with self._lock:
            sessions = self._sessions()
            sessions.pop(_hash(token), None)
            self._save_sessions(sessions)

    # ---- talking

    def say(self, text: str, *, spoken: bool = False) -> dict:
        text = text.strip()
        command = text.lower().split(None, 1)[0].split("@")[0] if text else ""
        if command in TELEGRAM_ONLY or text in PUBLISH_CHOICES:
            return {"reply": f"{command or text} is done in Telegram, {self.commander.title}: it acts outside this "
                             "screen, so it needs your typed word there.", "choices": []}
        answer = self.commander.exchange(text, spoken=spoken)
        # One line per request in the server log, so "it did something odd" can be traced afterwards.
        logger.info("screen %s: %r -> %r", "heard" if spoken else "typed", text[:120], answer.reply[:80])
        result: dict[str, Any] = {"reply": answer.reply, "choices": answer.choices, "asleep": self.commander.asleep}
        # An action (powering off) can only come from a typed Telegram command; it is never run from here.
        if answer.attachment is not None:
            name = answer.attachment.name
            self._remember(self._files, name, answer.attachment)
            result["file"] = {"name": name, "url": f"/api/file/{name}"}
        if self._synthesize is not None:
            speech_id = secrets.token_urlsafe(9)
            self._remember(self._speech, speech_id, _speakable(answer.say))
            result["audio"] = f"/api/audio/{speech_id}"
        return result

    def voice(self, audio: bytes, content_type: str) -> dict:
        if self._transcribe is None:
            return {"error": "Voice needs ELEVENLABS_API_KEY on the server."}
        fmt = _AUDIO_FORMATS.get(content_type.split(";")[0].strip().lower())
        if fmt is None:
            return {"error": "That recording format is not supported."}
        try:
            heard = self._transcribe(audio, fmt).strip()
        except (RuntimeError, ValueError) as exc:
            return {"error": f"I could not hear that: {exc}"}
        if not heard:
            return {"error": "I did not catch anything. Hold the orb while you speak."}
        return {"heard": heard, **self.say(heard, spoken=True)}

    def audio(self, speech_id: str) -> bytes | None:
        """Speak one reply, once. The text is fixed when the reply is made, so the page cannot ask for other speech."""
        with self._lock:
            text = self._speech.pop(speech_id, None)
        if text is None or self._synthesize is None:
            return None
        try:
            return self._synthesize(text)
        except (RuntimeError, ValueError) as exc:
            logger.warning("spoken reply skipped: %s", exc)
            return None

    def file(self, name: str) -> Path | None:
        with self._lock:
            path = self._files.get(name)
        return path if path is not None and path.exists() else None

    def _remember(self, store: OrderedDict, key: str, value: Any, keep: int = 12) -> None:
        with self._lock:
            store[key] = value
            while len(store) > keep:
                store.popitem(last=False)

    # ---- the dashboard

    def state(self) -> dict:
        now = self._clock()
        with self._lock:
            cached = self._state
        if cached is None or now - cached[0] > STATE_TTL_SECONDS:
            collected = brief.collect(("india",))
            data = {
                "indices": [{k: row.get(k) for k in ("group", "name", "price", "day_pct", "spark")}
                            for row in collected["indices"]],
                "headlines": [{k: h[k] for k in ("title", "link", "source")}
                              for region in ("india", "world") for h in collected["news"].get(region, [])[:4]],
                "as_of": collected["generated_at"],
            }
            with self._lock:
                self._state = cached = (now, data)
        return {**cached[1], "greeting": self.commander.greeting(), "title": self.commander.title,
                "asleep": self.commander.asleep, "voice": self._transcribe is not None,
                "buttons": [b for b in BUTTONS if b != "Go to sleep"] + ["Go to sleep"]}


def _handler(app: WebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "jarvis"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug("web %s", fmt % args)

        # -- helpers

        def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            for key, value in {**_SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(key, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, status: int = 200, extra: dict[str, str] | None = None) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json", extra)

        def _token(self) -> str:
            found = re.search(r"(?:^|;\s*)jarvis=([\w-]{20,80})", self.headers.get("Cookie", ""))
            return found.group(1) if found else ""

        def _body(self, limit: int) -> bytes | None:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return None
            if not 0 <= length <= limit:
                return None
            return self.rfile.read(length)

        def _same_site(self) -> bool:
            """Refuse requests another website could make a browser send: they lack this header."""
            if self.headers.get("X-Jarvis") != "1":
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin.split("://", 1)[-1] == self.headers.get("Host", "")

        # -- routes

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in _ASSETS:
                name, content_type = _ASSETS[path]
                body = resources.files("jarvis_sidekick.webui").joinpath(name).read_bytes()
                return self._send(200, body, content_type)
            if path == "/api/session":
                return self._json({"signed_in": app.signed_in(self._token())})
            if not app.signed_in(self._token()):
                return self._json({"error": "sign in first"}, HTTPStatus.UNAUTHORIZED)
            if path == "/api/state":
                return self._json(app.state())
            if found := re.fullmatch(r"/api/audio/([\w-]{6,20})", path):
                audio = app.audio(found.group(1))
                return self._send(200, audio, "audio/mpeg") if audio else self._json({"error": "gone"}, 404)
            if found := re.fullmatch(r"/api/file/([\w.-]{1,80}\.pdf)", path):
                file = app.file(found.group(1))
                if file is None:
                    return self._json({"error": "gone"}, 404)
                return self._send(200, file.read_bytes(), "application/pdf",
                                  {"Content-Disposition": f'inline; filename="{file.name}"'})
            return self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if not self._same_site():
                return self._json({"error": "refused"}, HTTPStatus.FORBIDDEN)
            if path == "/api/login/start":
                if not app.start_login():
                    return self._json({"error": "Too many codes requested. Wait ten minutes."}, 429)
                return self._json({"sent": True})
            if path == "/api/login/finish":
                body = self._body(200)
                try:
                    code = str(json.loads(body or b"{}").get("code", ""))
                except ValueError:
                    code = ""
                token = app.finish_login(code)
                if token is None:
                    return self._json({"error": "That code is wrong or has expired."}, HTTPStatus.UNAUTHORIZED)
                cookie = (f"jarvis={token}; Max-Age={SESSION_DAYS * 86_400}; Path=/; HttpOnly; Secure; "
                          "SameSite=Strict")
                return self._json({"signed_in": True}, extra={"Set-Cookie": cookie})
            if not app.signed_in(self._token()):
                return self._json({"error": "sign in first"}, HTTPStatus.UNAUTHORIZED)
            if path == "/api/logout":
                app.sign_out(self._token())
                return self._json({"signed_in": False}, extra={"Set-Cookie": "jarvis=; Max-Age=0; Path=/"})
            if path == "/api/say":
                body = self._body(MAX_TEXT_BYTES)
                try:
                    text = str(json.loads(body or b"{}").get("text", ""))
                except ValueError:
                    return self._json({"error": "bad request"}, 400)
                return self._json(app.say(text))
            if path == "/api/voice":
                audio = self._body(MAX_AUDIO_BYTES)
                if not audio:
                    return self._json({"error": "The recording was empty or too long."}, 400)
                return self._json(app.voice(audio, self.headers.get("Content-Type", "")))
            return self._json({"error": "not found"}, 404)

    return Handler


def serve(app: WebApp, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Start the screen's server on a background thread and return it."""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("the screen listens on this machine only; put a private network or HTTPS proxy in front")
    server = ThreadingHTTPServer((host, port), _handler(app))
    threading.Thread(target=server.serve_forever, name="jarvis-web", daemon=True).start()
    return server


def from_env(commander: Commander, send_code: Callable[[str], None]) -> WebApp:
    import os

    transcribe = synthesize = None
    if os.environ.get("ELEVENLABS_API_KEY"):
        from jarvis_sidekick.speech.elevenlabs_say import synthesize as speak
        from jarvis_sidekick.speech.elevenlabs_stt import ElevenLabsSpeechBackend

        backend = ElevenLabsSpeechBackend()

        def transcribe(audio: bytes, fmt: str) -> str:
            return backend.transcribe(audio, format=fmt).text

        synthesize = speak
    return WebApp(commander, send_code, transcribe=transcribe, synthesize=synthesize)
