"""A private Telegram bot: your phone or tablet becomes the remote control.

Type a command or send a voice note; the bot transcribes it with ElevenLabs, runs
it, and replies (optionally with a spoken answer). It runs on a computer you
control; Telegram is only the transport.

Guardrails, all enforced in this file:

* One owner. Only private messages whose chat id and sender id both equal
  TELEGRAM_CHAT_ID are processed. Everything else is dropped without a reply.
* Refuses to start without an owner id. There is no "open" mode.
* Messages sent while the bot was offline are discarded, so nothing replays.
* Sending a job application needs a one-time 6-digit code, typed (never spoken),
  valid for 5 minutes, bound to one job, void after 3 wrong tries.
* The free-form agent gets read-only research tools. It cannot apply to jobs,
  run shell commands or write files, whatever a web page tells it to do.
* Rate limit, voice-note size and length caps, and no secrets in logs or errors.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from jarvis_sidekick import _http, common, paths
from jarvis_sidekick.jobs import apply, match, sources
from jarvis_sidekick.jobs.tracker import Tracker
from jarvis_sidekick.markets import brief, data, indicators, plan
from jarvis_sidekick.profile import youtube

logger = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_VOICE_SECONDS = 120
MAX_VOICE_BYTES = 5_000_000
MAX_TEXT_CHARS = 2000
RATE_LIMIT = 20  # messages per minute
CODE_TTL_SECONDS = 300
CODE_ATTEMPTS = 3
# What the free-form agent may use from a phone. Nothing here changes state or sends anything.
AGENT_TOOLS = ("web_search", "web_scrape", "job_search", "market_brief", "stock_screen", "investment_plan",
               "github_profile", "youtube_channel", "side_hustle_ideas", "calculator", "think")
HELP = """Commands (type them, or say them in a voice note):
/brief - market briefing
/screen india|us|etf - momentum screen
/jobs <role> [in <place, place>] - search and rank against your resume
/apply <id> - fill the form (sends nothing), then gives you a code
/confirm <code> - send that application (typed only)
/status - your applications
/channel - your YouTube channel's recent videos and views
/plan <monthly> <years> [conservative|moderate|aggressive]
Anything else is answered by Jarvis."""


class TelegramError(RuntimeError):
    """A Telegram call failed. The message never contains the bot token."""


@dataclass
class Pending:
    job_id: str
    code: str
    expires: float
    attempts: int = 0


@dataclass
class Commander:
    """Turns one message into one reply. Holds no network state, so it is easy to test."""

    resume_path: str = ""
    locations: str = ""
    channel_id: str = ""
    agent: Callable[[str], str] | None = None
    clock: Callable[[], float] = time.monotonic
    pending: Pending | None = field(default=None, init=False)

    def handle(self, text: str, *, spoken: bool = False) -> str:
        text = text.strip()[:MAX_TEXT_CHARS]
        if spoken:
            text = _spoken_to_command(text)
        if not text.startswith("/"):
            return self._ask(text)
        name, _, rest = text.partition(" ")
        name, rest = name.lower().split("@")[0], rest.strip()
        if name == "/confirm":
            if spoken:
                return "For safety, /confirm must be typed, not spoken."
            return self._confirm(rest)
        handler = {
            "/start": lambda _: HELP, "/help": lambda _: HELP, "/brief": self._brief, "/screen": self._screen,
            "/jobs": self._jobs, "/apply": self._apply, "/status": self._status, "/plan": self._plan,
            "/channel": self._channel,
        }.get(name)
        if handler is None:
            return "Unknown command.\n" + HELP
        try:
            return handler(rest)
        except (_http.FetchError, ValueError, RuntimeError, OSError) as exc:
            return f"{name} failed: {exc}"

    # ---- commands

    def _ask(self, text: str) -> str:
        if not text:
            return HELP
        if self.agent is None:
            return "Free-form questions need OpenJarvis with a model configured.\n" + HELP
        return self.agent(text)

    def _brief(self, _: str) -> str:
        collected = brief.collect()
        if not collected["indices"] and not any(collected["news"].values()):
            return "No market data or news could be fetched right now."
        return brief.to_markdown(collected)

    def _screen(self, rest: str) -> str:
        market = rest.lower() or "india"
        if market not in data.WATCHLISTS:
            return f"Markets: {', '.join(data.WATCHLISTS)}"
        series = []
        for symbol in data.WATCHLISTS[market]:
            try:
                series.append(data.history(symbol))
            except _http.FetchError:
                continue
        ranked = indicators.screen(series)
        if not ranked:
            return "No price data could be fetched right now."
        lines = [f"{s.symbol}: score {s.score}, 3m {s.quarter_pct:+.1f}%, 6m {s.half_year_pct:+.1f}%, {s.note}"
                 for s in ranked]
        return "\n".join([f"{market.upper()} momentum screen", *lines, "", brief.DISCLAIMER])

    def _jobs(self, rest: str) -> str:
        if not rest:
            return "Usage: /jobs <role> [in <place, place>]"
        query, _, where = rest.partition(" in ")
        where = where.strip() or self.locations
        profile = common.resume_profile(self.resume_path)
        found, errors = sources.search(query.strip(), location=where)
        matches = match.rank(found, profile, query=query, location=where, min_score=30, limit=5)
        if not matches:
            return f"No listings scored 30 or more out of {len(found)} searched."
        lines = []
        for item in matches:
            apply.prepare(item, profile)
            job = item.job
            lines.append(f"{item.score:.0f}  {job.id}  {job.title} @ {job.company} [{job.location or 'n/a'}]\n{job.url}")
        note = f"\n(unavailable: {', '.join(errors)})" if errors else ""
        return "\n\n".join(lines) + f"\n\nPackets prepared. /apply <id> fills the form without sending.{note}"

    def _apply(self, rest: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{12}", rest):
            return "Usage: /apply <id>  (the 12-character id from /jobs)"
        result = self._submit(rest, confirm=False)
        if result.status != "ready":
            extra = "\nUnanswered: " + "; ".join(result.unanswered) if result.unanswered else ""
            return f"{result.status}: {result.detail}{extra}"
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.pending = Pending(rest, code, self.clock() + CODE_TTL_SECONDS)
        return (f"Form filled, nothing sent. Filled: {', '.join(result.filled)}.\n"
                f"To send it, type /confirm {code} within 5 minutes.")

    def _confirm(self, rest: str) -> str:
        pending = self.pending
        if pending is None or self.clock() > pending.expires:
            self.pending = None
            return "Nothing is waiting for confirmation. Run /apply <id> first."
        if not hmac.compare_digest(rest.encode(), pending.code.encode()):
            pending.attempts += 1
            if pending.attempts >= CODE_ATTEMPTS:
                self.pending = None
                return "Wrong code three times. Cancelled; run /apply again."
            return "Wrong code."
        self.pending = None  # single use, whatever happens next
        result = self._submit(pending.job_id, confirm=True)
        return f"{result.status}: {result.detail}"

    def _submit(self, job_id: str, *, confirm: bool) -> apply.Result:
        tracker = Tracker()
        try:
            record = tracker.get(job_id)
            if not record:
                raise ValueError("no prepared job with that id; run /jobs first")
            letter = paths.home() / "applications" / job_id / "cover_letter.md"
            return apply.submit(
                record["url"], common.resume_profile(self.resume_path),
                letter.read_text(encoding="utf-8") if letter.exists() else "",
                confirm=confirm, answers=common.load_answers(), tracker=tracker, job_id=job_id,
            )
        finally:
            tracker.close()

    def _status(self, _: str) -> str:
        tracker = Tracker()
        rows = tracker.all()
        tracker.close()
        if not rows:
            return "No applications yet."
        return "\n".join(f"{r['updated_at'][:10]} {r['status']}: {r['title']} @ {r['company']} ({r['job_id']})"
                         for r in rows[:20])

    def _channel(self, _: str) -> str:
        if not self.channel_id:
            return "Set SIDEKICK_YOUTUBE_CHANNEL to your channel id (UC...) first."
        return youtube.report(youtube.channel(self.channel_id))

    def _plan(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) < 2:
            return "Usage: /plan <monthly> <years> [risk]"
        built = plan.build(float(parts[0].replace(",", "")), int(parts[1]), parts[2] if len(parts) > 2 else "moderate")
        lines = [f"{row['percent']}%  Rs {row['monthly']:,}  {row['where']}" for row in built.allocation]
        lines += [f"{label}: Rs {value:,.0f} ({built.blended_return_pct[label]}%/yr assumed)"
                  for label, value in built.projected.items()]
        return "\n".join([*lines, "", *built.caveats])


_SPOKEN = (
    (re.compile(r"^(?:give me |send |get )?(?:the |my |a )?(?:market |morning |daily )?brief(?:ing)?\b.*", re.IGNORECASE), "/brief"),
    (re.compile(r"^(?:show |get )?(?:my )?(?:application )?status\b.*", re.IGNORECASE), "/status"),
    (re.compile(r"^(?:find|search|look for)(?: me)?(?: some)? (?:jobs? (?:for|as) )?(.+?)(?: jobs?)?$", re.IGNORECASE), "/jobs {0}"),
    (re.compile(r"^screen (india|us|etf)\b.*", re.IGNORECASE), "/screen {0}"),
    (re.compile(r"^(?:how is |how's |show )?my (?:youtube )?channel\b.*", re.IGNORECASE), "/channel"),
    (re.compile(r"^help\b.*", re.IGNORECASE), "/help"),
)


def _spoken_to_command(text: str) -> str:
    """Map a transcribed sentence onto a command. /apply and /confirm are never reachable by voice."""
    cleaned = text.strip().rstrip(".!?").lstrip("/")
    for pattern, command in _SPOKEN:
        found = pattern.match(cleaned)
        if found:
            return command.format(*(g.strip() for g in found.groups()))
    return cleaned


def jarvis_agent() -> Callable[[str], str] | None:
    """An ask() backed by OpenJarvis with the read-only tool set, or None when it is not installed."""
    try:
        from openjarvis.sdk import Jarvis

        import jarvis_sidekick
    except ImportError:
        return None
    jarvis_sidekick.register()

    def ask(text: str) -> str:
        try:
            with Jarvis() as jarvis:
                return str(jarvis.ask(text, agent="orchestrator", tools=list(AGENT_TOOLS)))
        except Exception as exc:  # noqa: BLE001 - any engine failure becomes a plain reply, never a crash
            logger.warning("agent failed: %s", type(exc).__name__)
            return f"Jarvis could not answer ({type(exc).__name__}). Is a model configured? Try /help."

    return ask


class Bot:
    def __init__(self, token: str, owner_id: str, commander: Commander, *, speak: bool = False,
                 client: httpx.Client | None = None, transcribe: Callable[[bytes], str] | None = None) -> None:
        if not re.fullmatch(r"\d{6,12}:[\w-]{30,}", token or ""):
            raise ValueError("TELEGRAM_BOT_TOKEN is missing or malformed")
        if not re.fullmatch(r"\d{4,15}", str(owner_id or "")):
            raise ValueError("TELEGRAM_CHAT_ID must be your own numeric Telegram id; the bot will not run without it")
        self._token, self._owner = token, int(owner_id)
        self._commander, self._speak = commander, speak
        self._client = client or httpx.Client(timeout=70.0)
        self._transcribe = transcribe
        self._offset: int | None = None
        self._recent: deque[float] = deque()
        self.dropped = 0

    # ---- transport

    def _call(self, method: str, **kwargs) -> dict:
        try:
            response = self._client.post(f"{API}/bot{self._token}/{method}", **kwargs)
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:  # the URL holds the token: report the category only
            raise TelegramError(f"Telegram {method} failed ({type(exc).__name__})") from None
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise TelegramError(f"Telegram {method} was rejected (HTTP {response.status_code})")
        return payload

    def send(self, text: str) -> None:
        for start in range(0, len(text) or 1, 4000):
            self._call("sendMessage", json={"chat_id": self._owner, "text": text[start:start + 4000] or "(empty)",
                                            "disable_web_page_preview": True})

    def skip_backlog(self) -> None:
        """Drop everything sent while the bot was not running: stale commands must not fire on start."""
        updates = self._call("getUpdates", json={"offset": -1, "timeout": 0})["result"]
        if updates:
            self._offset = updates[-1]["update_id"] + 1

    # ---- one update

    def _authorised(self, message: dict) -> bool:
        chat, sender = message.get("chat") or {}, message.get("from") or {}
        return (chat.get("type") == "private" and chat.get("id") == self._owner and sender.get("id") == self._owner
                and not sender.get("is_bot"))

    def _rate_limited(self) -> bool:
        now = time.monotonic()
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        self._recent.append(now)
        return len(self._recent) > RATE_LIMIT

    def _voice_text(self, voice: dict) -> str:
        if int(voice.get("duration") or 0) > MAX_VOICE_SECONDS or int(voice.get("file_size") or 0) > MAX_VOICE_BYTES:
            raise ValueError(f"voice notes are limited to {MAX_VOICE_SECONDS} seconds")
        path = self._call("getFile", json={"file_id": voice["file_id"]})["result"].get("file_path", "")
        if not re.fullmatch(r"[\w/.-]{1,200}", path) or ".." in path:
            raise ValueError("unexpected file path from Telegram")
        try:
            response = self._client.get(f"{API}/file/bot{self._token}/{path}")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TelegramError(f"voice download failed ({type(exc).__name__})") from None
        if len(response.content) > MAX_VOICE_BYTES:
            raise ValueError("voice note is too large")
        if self._transcribe is None:
            from jarvis_sidekick.speech.elevenlabs_stt import ElevenLabsSpeechBackend

            return ElevenLabsSpeechBackend().transcribe(response.content, format="ogg").text
        return self._transcribe(response.content)

    def process(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict) or not self._authorised(message):
            self.dropped += 1  # strangers get no reply: the bot does not confirm it exists
            logger.warning("dropped an update from an unauthorised chat")
            return
        if self._rate_limited():
            self.send("Slow down: too many messages this minute.")
            return
        spoken = isinstance(message.get("voice"), dict)
        try:
            text = self._voice_text(message["voice"]) if spoken else str(message.get("text") or "")
        except (TelegramError, ValueError, RuntimeError) as exc:
            self.send(f"Could not read that voice note: {exc}")
            return
        if spoken:
            self.send(f"Heard: {text}")
        reply = self._commander.handle(text, spoken=spoken)
        self.send(reply)
        if spoken and self._speak:
            self._say(reply)

    def _say(self, reply: str) -> None:
        from jarvis_sidekick.speech.elevenlabs_say import synthesize

        try:
            audio = synthesize(re.sub(r"https?://\S+|[|#*_`]", " ", reply))
            self._call("sendAudio", data={"chat_id": str(self._owner), "title": "Jarvis"},
                       files={"audio": ("reply.mp3", audio, "audio/mpeg")})
        except (RuntimeError, ValueError) as exc:
            logger.warning("spoken reply skipped: %s", exc)

    # ---- loop

    def poll_once(self, timeout: int = 50) -> int:
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset
        updates = self._call("getUpdates", json=params)["result"]
        for update in updates:
            self._offset = update["update_id"] + 1  # advance first: a crashing update is never retried
            try:
                self.process(update)
            except TelegramError as exc:
                logger.warning("%s", exc)
        return len(updates)

    def run(self) -> None:
        self.skip_backlog()
        self.send("Jarvis sidekick is online. /help lists commands.")
        while True:
            try:
                self.poll_once()
            except TelegramError as exc:
                logger.warning("%s; retrying in 10s", exc)
                time.sleep(10)


def from_env(*, speak: bool = False) -> Bot:
    commander = Commander(
        resume_path=os.environ.get("SIDEKICK_RESUME", ""),
        locations=os.environ.get("SIDEKICK_LOCATIONS", ""),
        channel_id=os.environ.get("SIDEKICK_YOUTUBE_CHANNEL", ""),
        agent=jarvis_agent(),
    )
    return Bot(os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""), commander, speak=speak)
