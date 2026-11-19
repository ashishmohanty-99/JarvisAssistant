"""`sidekick doctor`: try every integration once and say plainly what works.

Each check makes one small real call. Checks whose key or setting is missing are
skipped, not failed. The ElevenLabs check speaks three words and transcribes
them back; the Telegram check sends you one message.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from jarvis_sidekick import _http, paths

OK, FAIL, SKIP = "ok", "FAIL", "skip"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _run(name: str, function: Callable[[], str]) -> Check:
    try:
        return Check(name, OK, function())
    except _Skip as exc:
        return Check(name, SKIP, str(exc))
    except (_http.FetchError, RuntimeError, ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        return Check(name, FAIL, str(exc) or type(exc).__name__)


class _Skip(Exception):
    pass


def _need(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise _Skip(f"set {', '.join(missing)}")


def _resume() -> str:
    from jarvis_sidekick import common, resume

    path = os.environ.get("SIDEKICK_RESUME") or common.stored_resume()
    if not path:
        raise _Skip("send your resume to the bot as a PDF, or set SIDEKICK_RESUME")
    profile = resume.load(path)
    if not (profile.name and profile.email):
        raise ValueError("could not find a name and an email in the resume")
    return f"name, email{', phone' if profile.phone else ''} found; {len(profile.skills)} skills recognised"


def _env_file() -> str:
    path = paths.home() / ".env"
    if not path.exists():
        raise _Skip(f"no {path} (fine if you set variables another way)")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ValueError(f"{path} is readable by other users; run: chmod 600 {path}")
    return "private (600)"


def _prices() -> str:
    from jarvis_sidekick.markets import data

    series = data.history("^NSEI")
    return f"NIFTY 50 {series.last:,.2f} on {series.dates[-1]} via {series.source}, {len(series.closes)} days"


def _funds() -> str:
    from jarvis_sidekick.markets import data, invest

    got, missing = [], []
    for symbol, _, _ in invest.FUNDS.values():
        try:
            got.append(f"{symbol.removesuffix('.NS')} {data.history(symbol).last:,.2f}")
        except _http.FetchError:
            missing.append(symbol)
    if missing:
        raise RuntimeError(f"no price for {', '.join(missing)}" + (f"; got {', '.join(got)}" if got else ""))
    return ", ".join(got)


def _news() -> str:
    from jarvis_sidekick.markets import news

    counts, dead = {}, []
    for feeds in news.FEEDS.values():
        for source, url in feeds.items():
            try:
                counts[source] = len(news.parse_feed(_http.get(url, timeout=15.0).content, source))
                if not counts[source]:
                    dead.append(f"{source} (answered, no items)")
            except _http.FetchError as exc:
                counts[source] = 0
                dead.append(f"{source} ({exc})")
    if len(dead) == len(counts):
        raise RuntimeError("no feed returned any headline: " + "; ".join(dead))
    return f"{sum(counts.values())} headlines" + (f"; empty: {'; '.join(dead)}" if dead else "")


def _job_source(name: str) -> Callable[[], str]:
    def check() -> str:
        from jarvis_sidekick.jobs import sources

        if name == "adzuna":
            _need("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
        jobs = [j for j in sources.SOURCES[name]("engineer", None) if j.url and j.title]
        if not jobs:
            raise RuntimeError("answered, but no listings could be read")
        described = sum(1 for j in jobs if j.description)
        return f"{len(jobs)} listings, {described} with a description"

    return check


def _channel() -> str:
    _need("SIDEKICK_YOUTUBE_CHANNEL")
    from jarvis_sidekick.profile import youtube

    result = youtube.channel(os.environ["SIDEKICK_YOUTUBE_CHANNEL"])
    return f"{result['channel']}: {len(result['videos'])} videos, {sum(v['views'] for v in result['videos']):,} views"


def _elevenlabs() -> str:
    _need("ELEVENLABS_API_KEY")
    from jarvis_sidekick.speech.elevenlabs_say import synthesize
    from jarvis_sidekick.speech.elevenlabs_stt import ElevenLabsSpeechBackend

    audio = synthesize("Jarvis sidekick check.")
    heard = ElevenLabsSpeechBackend().transcribe(audio, format="mp3").text
    if "check" not in heard.lower():
        raise RuntimeError(f"spoke and transcribed, but heard {heard!r}")
    return f"spoke {len(audio):,} bytes and heard it back: {heard!r}"


def _brain() -> str:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise _Skip("set ANTHROPIC_API_KEY for conversation; without it the bot only knows commands")
    try:
        import anthropic
    except ImportError:
        raise _Skip("pip install 'jarvis-sidekick[brain]'") from None
    from jarvis_sidekick.brain import DEFAULT_MODEL

    model = os.environ.get("SIDEKICK_MODEL", "").strip() or DEFAULT_MODEL
    try:
        response = anthropic.Anthropic(timeout=60.0).messages.create(
            model=model, max_tokens=2000, output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
    except anthropic.AuthenticationError:
        raise RuntimeError("the key was rejected") from None
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"{model} answered HTTP {exc.status_code}") from None
    except anthropic.APIConnectionError:
        raise RuntimeError("could not reach the Claude API") from None
    said = " ".join(b.text for b in response.content if b.type == "text").strip()
    return f"{response.model} answered {said!r}"


def _telegram() -> str:
    _need("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    from jarvis_sidekick.bot import Bot, Commander

    bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], Commander(),
              client=httpx.Client(timeout=20.0))
    name = bot._call("getMe")["result"].get("username", "?")
    bot.send("sidekick doctor: this bot can reach you.")
    return f"@{name} sent you a message"


def _browser() -> str:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        raise _Skip("pip install 'jarvis-sidekick[apply]' to fill application forms") from None
    launch = {"executable_path": os.environ["SIDEKICK_CHROMIUM"]} if os.environ.get("SIDEKICK_CHROMIUM") else {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch)
            version = browser.version
            browser.close()
    except Error:
        raise RuntimeError("Chromium did not start; run: playwright install chromium") from None
    return f"Chromium {version} starts"


def _openjarvis() -> str:
    try:
        import openjarvis
    except ImportError:
        raise _Skip("not installed (needed for `sidekick voice` and free-form questions)") from None
    import jarvis_sidekick

    return f"{openjarvis.__version__}; {len(jarvis_sidekick.register())} sidekick tools registered"


def _microphone() -> str:
    try:
        import sounddevice
    except (ImportError, OSError):
        raise _Skip("no audio library; pip install 'jarvis-sidekick[voice]' (only needed for `sidekick voice`)") from None
    inputs = [d for d in sounddevice.query_devices() if d.get("max_input_channels", 0) > 0]
    if not inputs:
        raise RuntimeError("no input device; on macOS allow the terminal under Privacy & Security > Microphone")
    return f"{len(inputs)} input device(s); default: {sounddevice.query_devices(kind='input')['name']}"


def run(offline: bool = False) -> list[Check]:
    local: list[tuple[str, Callable[[], str]]] = [
        ("resume", _resume), ("env file", _env_file), ("browser", _browser), ("OpenJarvis", _openjarvis),
        ("microphone", _microphone),
    ]
    live: list[tuple[str, Callable[[], str]]] = [
        ("prices (Yahoo)", _prices), ("fund prices for /invest", _funds), ("news feeds", _news),
        *[(f"jobs: {name}", _job_source(name)) for name in ("remotive", "arbeitnow", "remoteok", "adzuna")],
        ("YouTube channel", _channel), ("ElevenLabs voice", _elevenlabs), ("Claude (conversation)", _brain),
        ("Telegram bot", _telegram),
    ]
    return [_run(name, function) for name, function in (local if offline else local + live)]
