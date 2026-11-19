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
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from jarvis_sidekick import _http, common, paths
from jarvis_sidekick.jobs import apply, match, sources
from jarvis_sidekick.jobs.tracker import Tracker
from jarvis_sidekick.markets import brief, data, indicators, invest, plan
from jarvis_sidekick.profile import youtube

logger = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_VOICE_SECONDS = 120
MAX_VOICE_BYTES = 5_000_000
MAX_RESUME_BYTES = 5_000_000
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
/doctor - check every connection (prices, news, jobs, voice, browser)
Send a PDF or .docx to set the resume you apply with.
/invest [amount] - where to put a sum of money: funds, stock ideas and quantities
/plan <monthly> <years> [conservative|moderate|aggressive]
/memory - what I remember about you; /forget - wipe it and our conversation
/sleep - stop answering until you say "Jarvis" (or /wake)
/shutdown - power the server off (typed only; start it again from your provider's panel)
Start with "Jarvis" and it greets you: "Jarvis", or "Jarvis, give me the market briefing".
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
    title: str = "Master"  # how the bot addresses you when you call it by name
    timezone: str = ""  # IANA name such as Asia/Kolkata; empty means the machine's own zone
    allow_poweroff: bool = False
    morning: str = ""  # HH:MM for a daily message, empty for none
    brain: Any = None  # the conversational brain, when one is configured
    say: str = field(default="", init=False)
    _after_reply: Callable[[], None] | None = field(default=None, init=False)
    agent: Callable[[str], str] | None = None
    clock: Callable[[], float] = time.monotonic
    pending: Pending | None = field(default=None, init=False)
    choices: list[str] = field(default_factory=list, init=False)  # answer buttons for the reply just given
    _invest: dict | None = field(default=None, init=False)

    def handle(self, text: str, *, spoken: bool = False) -> str:
        """Answer one message. Afterwards ``self.say`` holds a short version fit for speaking aloud."""
        text = text.strip()[:MAX_TEXT_CHARS]
        called = _WAKE.match(text)
        if called:
            text = text[called.end():].strip()
        if self.asleep:
            # Asleep, it answers to its name (or /wake) and nothing else.
            if not (called or text.lower().split("@")[0] == "/wake"):
                self.say = f"I am asleep, {self.title}. Say Jarvis to wake me."
                return self.say
            _asleep_flag().unlink(missing_ok=True)
            if not text or text.lower().startswith("/wake") or _spoken_to_command(text) == "/wake":
                self.say = f"{self.greeting()}, {self.title}. I am awake. What do you need?"
                return self.say
        if called and not text:
            self.say = f"{self.greeting()}, {self.title}. What do you need?"
            return self.say
        self.say, self.choices = "", []
        reply = self._dispatch(text, spoken)
        if called:
            reply = f"Yes, {self.title}.\n{reply}"
        spoken_text = self.say or _speakable(reply)
        self.say = f"Yes, {self.title}. {spoken_text}" if called and self.say else spoken_text
        return reply

    @property
    def asleep(self) -> bool:
        return _asleep_flag().exists()

    def greeting(self) -> str:
        """Good morning / afternoon / evening by the clock where you are (SIDEKICK_TIMEZONE)."""
        hour = self.now().hour
        if 5 <= hour < 12:
            return "Good morning"
        if 12 <= hour < 17:
            return "Good afternoon"
        if 17 <= hour < 22:
            return "Good evening"
        return "You are up late"

    def now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self.timezone)) if self.timezone else datetime.now().astimezone()
        except (ZoneInfoNotFoundError, ValueError):
            return datetime.now().astimezone()

    def take_action(self) -> Callable[[], None] | None:
        """Something to do after the reply has been delivered (powering off must not cut the reply short)."""
        action, self._after_reply = self._after_reply, None
        return action

    def _sleep(self, _: str) -> str:
        _asleep_flag().touch()
        return f"Going to sleep, {self.title}. Say Jarvis to wake me."

    def _shutdown(self, _: str) -> str:
        if not self.allow_poweroff:
            return "Powering off is only available on the server install."
        self._after_reply = lambda: (paths.home() / POWEROFF_REQUEST).touch()
        return (f"Powering off the server, {self.title}. I cannot hear you while it is off: start it again from your "
                "provider's control panel and I will message you when I am back.")

    def _dispatch(self, text: str, spoken: bool) -> str:
        if self._invest is not None and not text.startswith("/"):
            answered = self._invest_answer(text)
            if answered is not None:
                return answered
        self._invest = None  # anything else ends an unfinished /invest conversation
        if text in BUTTONS and (not self.conversational or BUTTONS[text] in _CONTROL or text == "Where to invest"):
            text = BUTTONS[text]  # a tapped button; with a brain the label itself is the request
        elif spoken:
            text = _spoken_to_command(text, control_only=self.conversational)
            if text == "/shutdown":
                return "To power off the server, type /shutdown. To pause me, say: go to sleep."
        elif self.conversational and _spoken_to_command(text, control_only=True) == "/sleep":
            text = "/sleep"  # "go to sleep" typed as a sentence
        elif not self.conversational and not text.startswith("/"):
            mapped = _spoken_to_command(text)  # a typed sentence works like a spoken one
            text = text if mapped == "/shutdown" else mapped  # powering off needs the literal command
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
            "/channel": self._channel, "/doctor": self._doctor, "/sleep": self._sleep,
            "/wake": lambda _: f"I am awake, {self.title}.", "/shutdown": self._shutdown,
            "/forget": self._forget, "/memory": self._memory, "/invest": self._invest_start,
        }.get(name)
        if handler is None:
            return "Unknown command.\n" + HELP
        try:
            return handler(rest)
        except (_http.FetchError, ValueError, RuntimeError, OSError) as exc:
            return f"{name} failed: {exc}"

    # ---- commands

    @property
    def conversational(self) -> bool:
        return self.agent is not None

    def _ask(self, text: str) -> str:
        if not text:
            return HELP
        if self.agent is None:
            return "I only know commands until a language model is configured (ANTHROPIC_API_KEY).\n" + HELP
        return self.agent(text)

    # ---- /invest: a short conversation with buttons, no language model needed

    def _invest_start(self, rest: str) -> str:
        words = rest.lower()
        self._invest = {
            "amount": invest.parse_amount(rest),
            "monthly": True if re.search(r"\b(monthly|every month|each month|per month|sip)\b", words)
            else False if re.search(r"\b(once|one time|one-time|lump ?sum)\b", words) else None,
            "risk": next((r for r in invest.SPLITS if r in words), None),
        }
        return self._invest_next()

    def _invest_answer(self, text: str) -> str | None:
        """Use *text* as the answer to the pending question, or return None if it is not one."""
        state, answer = self._invest, text.strip().lower().rstrip(".!")
        if state["amount"] is None:
            state["amount"] = invest.parse_amount(text)
            if state["amount"] is None:
                return None
        elif state["monthly"] is None:
            if answer not in _INVEST_WHEN:
                return None
            state["monthly"] = _INVEST_WHEN[answer]
        elif state["risk"] is None:
            risk = next((r for r in invest.SPLITS if answer.startswith(r)), None)
            if risk is None:
                return None
            state["risk"] = risk
        return self._invest_next()

    def _invest_next(self) -> str:
        state = self._invest
        if state["amount"] is None:
            return "How much do you want to invest? For example: 25000"
        if state["monthly"] is None:
            self.choices = ["One time", "Every month"]
            return f"Rs {state['amount']:,.0f}. Is that one time, or every month?"
        if state["risk"] is None:
            self.choices = ["Conservative", "Moderate", "Aggressive"]
            return ("How much of a fall could you sit through without selling?\n"
                    "Conservative: about 10%. Moderate: about 20%. Aggressive: 30% or more.")
        self._invest = None
        suggestion = invest.suggest(state["amount"], state["risk"], monthly=state["monthly"])
        self.say = invest.to_speech(suggestion, self.title)
        return invest.to_text(suggestion, self.title)

    def _forget(self, _: str) -> str:
        return self.brain.forget() if self.brain else "There is nothing to forget: no language model is configured."

    def _memory(self, _: str) -> str:
        notes = self.brain.notes() if self.brain else []
        return "\n".join(["What I remember about you:", *(f"- {n}" for n in notes)]) if notes else "I have no notes yet."

    def morning_due(self) -> bool:
        """True once a day, at or after the configured HH:MM in your time zone, when awake."""
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", self.morning) or self.asleep:
            return False
        now, marker = self.now(), paths.home() / "morning.sent"
        today = now.date().isoformat()
        if f"{now:%H:%M}" < self.morning or (marker.exists() and marker.read_text().strip() == today):
            return False
        marker.write_text(today)
        return True

    def morning_message(self) -> str:
        if self.agent is not None:
            return self.agent("It is morning. Greet me and give me today's briefing: the market, anything notable "
                              "in the news, and one thing worth my attention today.")
        return f"{self.greeting()}, {self.title}.\n" + self._brief("")

    def _brief(self, _: str) -> str:
        collected = brief.collect()
        if not collected["indices"] and not any(collected["news"].values()):
            return "No market data or news could be fetched right now."
        self.say = brief.to_speech(collected)  # the headline numbers, not the whole table read aloud
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

    def _doctor(self, _: str) -> str:
        from jarvis_sidekick import doctor

        checks = [c for c in doctor.run() if c.name not in ("Telegram bot", "microphone", "env file")]
        failed = sum(1 for c in checks if c.status == doctor.FAIL)
        return "\n".join([*(f"{c.status}  {c.name}: {c.detail}" for c in checks), "", f"{failed} failed"])

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


# "Jarvis", "Hey Jarvis," ... at the start of a message. Speech-to-text also writes "Javis" and "Jarvis's".
_WAKE = re.compile(r"^\s*(?:hey|hi|ok|okay|hello)?[\s,]*j[ae]r?vis(?:'s)?\b[\s,.!?:;-]*", re.IGNORECASE)
MAX_SPOKEN_CHARS = 600


def _speakable(reply: str) -> str:
    """A reply trimmed for the ear: no links or table marks, and short."""
    text = " ".join(re.sub(r"https?://\S+|[|#*_`]", " ", reply).split())
    if len(text) <= MAX_SPOKEN_CHARS:
        return text
    return text[:MAX_SPOKEN_CHARS].rsplit(" ", 1)[0] + ". The rest is in the message."


# Buttons shown under the message box. Without a brain each is a command; with one, the label is the request.
BUTTONS = {"Market briefing": "/brief", "Where to invest": "/invest", "Stocks to watch": "/screen india",
           "My applications": "/status", "Go to sleep": "/sleep"}
_KEYBOARD = {"keyboard": [[{"text": "Market briefing"}, {"text": "Where to invest"}],
                          [{"text": "Stocks to watch"}, {"text": "My applications"}],
                          [{"text": "Go to sleep"}]],
             "resize_keyboard": True, "is_persistent": True}
_INVEST_WHEN = {"one time": False, "once": False, "lump sum": False, "every month": True, "monthly": True}
POWEROFF_REQUEST = "poweroff.request"  # watched by a root-owned systemd unit on the server install


def _asleep_flag():
    return paths.home() / "asleep"


_SPOKEN = (
    (re.compile(r"^(?:please )?(?:go (?:back )?to sleep|sleep(?: now)?|good ?night|stand by|that is all|that's all)\b.*",
                re.IGNORECASE), "/sleep"),
    (re.compile(r"^(?:please )?(?:wake up|wake|are you there|are you awake)\b.*", re.IGNORECASE), "/wake"),
    (re.compile(r"^(?:please )?(?:shut ?down|power ?off|switch off|turn off)\b.*", re.IGNORECASE), "/shutdown"),
    # Money questions come first: "give me the brief and tell me where to invest 25k" is about investing.
    (re.compile(r"^(.*\b(?:invest(?:ing|ment)?|put (?:my |the |some )?money|stocks? to (?:buy|invest|put))\b.*)$",
                re.IGNORECASE), "/invest {0}"),
    (re.compile(r"^(?:give me |send |get )?(?:the |my |a )?(?:market |morning |daily )?brief(?:ing)?\b.*", re.IGNORECASE), "/brief"),
    (re.compile(r"^(?:show |get )?(?:my )?(?:application )?status\b.*", re.IGNORECASE), "/status"),
    (re.compile(r"^(?:find|search|look for)(?: me)?(?: some)? (?:jobs? (?:for|as) )?(.+?)(?: jobs?)?$", re.IGNORECASE), "/jobs {0}"),
    (re.compile(r"^screen (india|us|etf)\b.*", re.IGNORECASE), "/screen {0}"),
    (re.compile(r"^(?:how is |how's |show )?my (?:youtube )?channel\b.*", re.IGNORECASE), "/channel"),
    (re.compile(r"^help\b.*", re.IGNORECASE), "/help"),
)


_CONTROL = ("/sleep", "/wake", "/shutdown")


def _spoken_to_command(text: str, *, control_only: bool = False) -> str:
    """Map a transcribed sentence onto a command. /apply and /confirm are never reachable by voice.

    With a conversational brain only the control phrases (sleep, wake, power off) are mapped;
    everything else is left as a sentence for the brain, which is what makes it a conversation.
    """
    cleaned = text.strip().rstrip(".!?").lstrip("/")
    for pattern, command in _SPOKEN:
        if control_only and command not in _CONTROL:
            continue
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

    def send(self, text: str, *, buttons: bool = False, choices: list[str] | None = None) -> None:
        for start in range(0, len(text) or 1, 4000):
            payload = {"chat_id": self._owner, "text": text[start:start + 4000] or "(empty)",
                       "disable_web_page_preview": True}
            if choices:  # answer buttons for a question; the main buttons come back with the next reply
                payload["reply_markup"] = {"keyboard": [[{"text": c} for c in choices]], "resize_keyboard": True,
                                           "one_time_keyboard": True}
            elif buttons:
                payload["reply_markup"] = _KEYBOARD
            self._call("sendMessage", json=payload)

    def _typing(self) -> None:
        """Show "typing..." while the answer is prepared. Cosmetic: a failure here must not stop the reply."""
        try:
            self._call("sendChatAction", json={"chat_id": self._owner, "action": "typing"})
        except TelegramError:
            pass

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

    def _download(self, file_id: str, max_bytes: int) -> bytes:
        path = self._call("getFile", json={"file_id": file_id})["result"].get("file_path", "")
        if not re.fullmatch(r"[\w/.-]{1,200}", path) or ".." in path:
            raise ValueError("unexpected file path from Telegram")
        try:
            response = self._client.get(f"{API}/file/bot{self._token}/{path}")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TelegramError(f"download failed ({type(exc).__name__})") from None
        if len(response.content) > max_bytes:
            raise ValueError("file is too large")
        return response.content

    def _voice_text(self, voice: dict) -> str:
        if int(voice.get("duration") or 0) > MAX_VOICE_SECONDS or int(voice.get("file_size") or 0) > MAX_VOICE_BYTES:
            raise ValueError(f"voice notes are limited to {MAX_VOICE_SECONDS} seconds")
        audio = self._download(voice["file_id"], MAX_VOICE_BYTES)
        if self._transcribe is None:
            from jarvis_sidekick.speech.elevenlabs_stt import ElevenLabsSpeechBackend

            return ElevenLabsSpeechBackend().transcribe(audio, format="ogg").text
        return self._transcribe(audio)

    def _save_resume(self, document: dict) -> str:
        """Store a PDF or DOCX the owner sent as the resume to apply with. Checked by content, not by name."""
        if int(document.get("file_size") or 0) > MAX_RESUME_BYTES:
            raise ValueError("resumes are limited to 5 MB")
        data = self._download(document["file_id"], MAX_RESUME_BYTES)
        if data.startswith(b"%PDF-"):
            suffix = ".pdf"
        elif data.startswith(b"PK\x03\x04") and str(document.get("file_name", "")).lower().endswith(".docx"):
            suffix = ".docx"
        else:
            raise ValueError("send the resume as a PDF or a .docx file")
        target = paths.home() / f"resume{suffix}"
        staged = paths.home() / f"incoming-resume{suffix}"  # keeps the real suffix so it is parsed as that type
        staged.write_bytes(data)
        staged.chmod(0o600)
        try:
            from jarvis_sidekick import resume

            profile = resume.load(staged)
            if not (profile.name and profile.email):
                raise ValueError("could not find a name and an email in it; the old resume is kept")
        except (ValueError, RuntimeError, OSError):
            staged.unlink(missing_ok=True)
            raise
        for old in ("resume.pdf", "resume.docx"):
            (paths.home() / old).unlink(missing_ok=True)
        staged.replace(target)
        return (f"Resume saved. Name, email{', phone' if profile.phone else ''} found; "
                f"{len(profile.skills)} skills recognised: {', '.join(profile.skills[:12])}"
                f"{' ...' if len(profile.skills) > 12 else ''}")

    def process(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict) or not self._authorised(message):
            self.dropped += 1  # strangers get no reply: the bot does not confirm it exists
            logger.warning("dropped an update from an unauthorised chat")
            return
        if self._rate_limited():
            self.send("Slow down: too many messages this minute.")
            return
        if isinstance(message.get("document"), dict):
            try:
                self.send(self._save_resume(message["document"]))
            except (TelegramError, ValueError, RuntimeError, OSError) as exc:
                self.send(f"Could not use that file: {exc}")
            return
        spoken = isinstance(message.get("voice"), dict)
        try:
            text = self._voice_text(message["voice"]) if spoken else str(message.get("text") or "")
        except (TelegramError, ValueError, RuntimeError) as exc:
            self.send(f"Could not read that voice note: {exc}")
            return
        if spoken:
            self.send(f"Heard: {text}")
        self._typing()
        had_choices = bool(self._commander.choices)
        reply = self._commander.handle(text, spoken=spoken)
        self.send(reply, choices=self._commander.choices,
                  buttons=had_choices or text.strip().lower().split("@")[0] in ("/start", "/help"))
        action = self._commander.take_action()
        if action:
            action()
            return
        if spoken and self._speak:
            self._say(self._commander.say or reply)

    def _say(self, reply: str) -> None:
        from jarvis_sidekick.speech.elevenlabs_say import synthesize

        try:
            audio = synthesize(_speakable(reply))
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
        (paths.home() / POWEROFF_REQUEST).unlink(missing_ok=True)
        state = " I am asleep; say Jarvis to wake me." if self._commander.asleep else ""
        self.send(f"{self._commander.greeting()}, {self._commander.title}. Jarvis is online.{state} /help lists commands.",
                  buttons=True)
        while True:
            try:
                self.poll_once()
                if self._commander.morning_due():
                    self._typing()
                    self.send(self._commander.morning_message())
            except TelegramError as exc:
                logger.warning("%s; retrying in 10s", exc)
                time.sleep(10)


def from_env(*, speak: bool = False) -> Bot:
    commander = Commander(
        resume_path=os.environ.get("SIDEKICK_RESUME", ""),
        locations=os.environ.get("SIDEKICK_LOCATIONS", ""),
        channel_id=os.environ.get("SIDEKICK_YOUTUBE_CHANNEL", ""),
        title=os.environ.get("SIDEKICK_TITLE", "").strip()[:30] or "Master",
        timezone=os.environ.get("SIDEKICK_TIMEZONE", "").strip(),
        allow_poweroff=os.environ.get("SIDEKICK_ALLOW_POWEROFF") == "1",
        morning=os.environ.get("SIDEKICK_MORNING", "").strip(),
    )
    from jarvis_sidekick import brain as brain_module

    commander.brain = brain_module.from_env(
        title=commander.title, now=commander.now, resume_path=commander.resume_path,
        channel_id=commander.channel_id, locations=commander.locations,
    )
    commander.agent = commander.brain.ask if commander.brain else jarvis_agent()
    return Bot(os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", ""), commander, speak=speak)
