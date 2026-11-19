"""`sidekick` command line."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from jarvis_sidekick import __version__, _http

VOICE_TOOLS = (
    "web_search,web_scrape,job_search,job_apply,market_brief,stock_screen,investment_plan,"
    "github_profile,youtube_channel,side_hustle_ideas,calculator,think"
)
_resume_option = click.option(
    "--resume", "resume_path", envvar="SIDEKICK_RESUME", required=True, type=click.Path(exists=True, dir_okay=False),
    help="Resume file (PDF, DOCX, Markdown or text). Or set SIDEKICK_RESUME.",
)


def _emit(value: object) -> None:
    click.echo(json.dumps(value, indent=2, ensure_ascii=False))


def _run_jarvis(args: list[str]) -> None:
    try:
        from openjarvis.cli import cli as jarvis_cli

        from jarvis_sidekick import register
    except ImportError:
        raise click.ClickException(
            "OpenJarvis is not installed. Install it: pip install 'git+https://github.com/open-jarvis/OpenJarvis'"
        ) from None
    register()
    jarvis_cli.main(args=args, prog_name="jarvis")


@click.group()
@click.version_option(__version__, prog_name="sidekick")
def main() -> None:
    """OpenJarvis sidekick: ElevenLabs voice, web reading, job hunting, market briefings."""


# ---------------------------------------------------------------- OpenJarvis and voice


@main.command(context_settings={"ignore_unknown_options": True, "allow_extra_args": True}, add_help_option=False)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def jarvis(args: tuple[str, ...]) -> None:
    """Run any `jarvis` command with the sidekick tools and ElevenLabs speech loaded."""
    _run_jarvis(list(args))


@main.command("voice-setup")
def voice_setup() -> None:
    """Point OpenJarvis at ElevenLabs for both listening and speaking."""
    for key, value in (("speech.backend", "elevenlabs"), ("speech.tts_backend", "elevenlabs")):
        try:
            _run_jarvis(["config", "set", key, value])
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise
    if not os.environ.get("ELEVENLABS_API_KEY"):
        click.echo("Now export ELEVENLABS_API_KEY (and optionally ELEVENLABS_VOICE_ID).")


@main.command()
@click.option("--agent", default="orchestrator", show_default=True)
@click.option("--tools", default=VOICE_TOOLS, show_default=False, help="Comma-separated tools the agent may use.")
@click.option("-m", "--model", default=None)
@click.option("-e", "--engine", default=None)
def voice(agent: str, tools: str, model: str | None, engine: str | None) -> None:
    """Talk to Jarvis: microphone in (ElevenLabs Scribe), speech out (ElevenLabs), tools on."""
    if not os.environ.get("ELEVENLABS_API_KEY"):
        raise click.ClickException("Set ELEVENLABS_API_KEY first (see .env.example).")
    args = ["chat", "--voice", "--agent", agent, "--tools", tools]
    args += ["-m", model] if model else []
    args += ["-e", engine] if engine else []
    _run_jarvis(args)


@main.command()
@click.argument("audio", type=click.Path(exists=True, dir_okay=False))
@click.option("--language", default=None, help="ISO language code, e.g. en or hi. Default: detect.")
def transcribe(audio: str, language: str | None) -> None:
    """Transcribe an audio file with ElevenLabs Scribe."""
    from jarvis_sidekick.speech.elevenlabs_stt import ElevenLabsSpeechBackend

    path = Path(audio)
    try:
        result = ElevenLabsSpeechBackend().transcribe(
            path.read_bytes(), format=path.suffix.lstrip(".").lower() or "wav", language=language
        )
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(result.text)


@main.command()
@click.argument("text")
@click.option("-o", "--output", default="speech.mp3", show_default=True, type=click.Path(dir_okay=False))
def say(text: str, output: str) -> None:
    """Synthesise TEXT with ElevenLabs and save it as an MP3."""
    try:
        from openjarvis.speech.elevenlabs_tts import ElevenLabsTTSBackend

        ElevenLabsTTSBackend().synthesize(text).save(Path(output))
    except ImportError:
        raise click.ClickException("OpenJarvis is not installed.") from None
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(output)


@main.command("bot")
@click.option("--speak", is_flag=True, help="Answer voice notes with a spoken reply as well as text.")
def bot_cmd(speak: bool) -> None:
    """Run your private Telegram bot: commands and voice notes from your phone or tablet."""
    import logging

    from jarvis_sidekick import bot

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines would print the bot token
    try:
        running = bot.from_env(speak=speak)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo("Bot running. Only your own Telegram account is answered. Ctrl+C stops it.")
    try:
        running.run()
    except KeyboardInterrupt:
        click.echo("stopped")


# ---------------------------------------------------------------- web


@main.command("scrape")
@click.argument("url")
@click.option("--crawl", "pages", default=1, show_default=True, help="Pages to read on the same site (max 50).")
def scrape_cmd(url: str, pages: int) -> None:
    """Read a page (or a small same-site crawl) as clean text."""
    from jarvis_sidekick import scrape

    try:
        found = [scrape.fetch_page(url)] if pages <= 1 else scrape.crawl(url, max_pages=pages)
    except _http.FetchError as exc:
        raise click.ClickException(str(exc)) from None
    for page in found:
        click.echo(f"# {page.title}\n{page.url}\n\n{page.text}\n")


# ---------------------------------------------------------------- jobs


@main.group()
def jobs() -> None:
    """Find jobs that fit your resume and apply with your approval."""


@jobs.command("search")
@click.argument("query")
@_resume_option
@click.option("--location", default="", envvar="SIDEKICK_LOCATIONS",
              help="Acceptable places, comma-separated: 'India, Singapore, UAE, remote'.")
@click.option("--country", "countries", multiple=True,
              help="Adzuna country code (in, sg, gb, de, us, ...). Repeatable. Default: SIDEKICK_COUNTRIES or in.")
@click.option("--company", "companies", multiple=True, help="Greenhouse/Lever board name. Repeatable.")
@click.option("--source", "source_names", multiple=True, help="Limit to these sources. Repeatable.")
@click.option("--limit", default=15, show_default=True)
@click.option("--min-score", default=30.0, show_default=True)
@click.option("--prepare", "prepare_top", default=0, help="Prepare application packets for the top N.")
@click.option("--json", "as_json", is_flag=True)
def jobs_search(query: str, resume_path: str, location: str, countries: tuple[str, ...], companies: tuple[str, ...],
                source_names: tuple[str, ...], limit: int, min_score: float, prepare_top: int, as_json: bool) -> None:
    """Search job sources for QUERY and rank the results against your resume."""
    from jarvis_sidekick import resume
    from jarvis_sidekick.jobs import apply, match, sources

    profile = resume.load(resume_path)
    found, errors = sources.search(query, sources=source_names, location=location, companies=companies,
                                   countries=countries)
    matches = match.rank(found, profile, query=query, location=location, min_score=min_score, limit=limit)
    for item in matches[:prepare_top]:
        apply.prepare(item, profile)
    if as_json:
        _emit({"matches": [m.to_dict() for m in matches], "source_errors": errors})
        return
    click.echo(f"Resume skills: {', '.join(profile.skills) or 'none recognised'}")
    click.echo(f"{len(found)} listings searched, {len(matches)} at or above score {min_score:g}\n")
    for item in matches:
        job = item.job
        click.echo(f"{item.score:5.1f}  {job.id}  {job.title} @ {job.company} [{job.location or 'n/a'}] ({job.source})")
        click.echo(f"       {job.url}")
        click.echo(f"       match: {', '.join(item.matched) or '-'} | gaps: {', '.join(item.missing) or '-'}")
    for name, error in errors.items():
        click.echo(f"source {name} unavailable: {error}", err=True)
    if prepare_top:
        click.echo(f"\nPrepared {min(prepare_top, len(matches))} packet(s). Review, then: sidekick jobs apply <id>")


@jobs.command("apply")
@click.argument("job_id")
@_resume_option
@click.option("--submit", is_flag=True, help="Actually send the application. Without it: fill and stop.")
@click.option("--show-browser", is_flag=True, help="Watch the browser fill the form.")
def jobs_apply(job_id: str, resume_path: str, submit: bool, show_browser: bool) -> None:
    """Fill (and with --submit, send) the application for a prepared job."""
    from jarvis_sidekick import paths, resume
    from jarvis_sidekick.common import load_answers
    from jarvis_sidekick.jobs import apply
    from jarvis_sidekick.jobs.tracker import Tracker

    tracker = Tracker()
    record = tracker.get(job_id)
    if not record:
        raise click.ClickException(f"No prepared job {job_id}. Run: sidekick jobs search ... --prepare N")
    letter_file = paths.home() / "applications" / job_id / "cover_letter.md"
    if submit and not click.confirm(f"Send your application for {record['title']} at {record['company']}?"):
        raise click.Abort()
    result = apply.submit(
        record["url"], resume.load(resume_path), letter_file.read_text(encoding="utf-8") if letter_file.exists() else "",
        confirm=submit, answers=load_answers(), tracker=tracker, job_id=job_id, headless=not show_browser,
    )
    tracker.close()
    _emit(result.to_dict())
    if result.status in ("error", "skipped"):
        sys.exit(1)


@jobs.command("status")
def jobs_status() -> None:
    """List every application and where it stands."""
    from jarvis_sidekick.jobs.tracker import Tracker

    for row in Tracker().all():
        click.echo(f"{row['updated_at'][:10]}  {row['status']:<11}  {row['job_id']}  {row['title']} @ {row['company']}")


# ---------------------------------------------------------------- markets


@main.command("brief")
@click.option("--market", "markets", multiple=True, default=("india", "us"), show_default=True)
@click.option("--send", type=click.Choice(["telegram"]), help="Also deliver the briefing.")
@click.option("--speak", type=click.Path(dir_okay=False), help="Also write a spoken summary to this MP3.")
@click.option("--json", "as_json", is_flag=True)
def brief_cmd(markets: tuple[str, ...], send: str | None, speak: str | None, as_json: bool) -> None:
    """News, index levels, movers and a momentum screen for India and world markets."""
    from jarvis_sidekick.markets import brief

    collected = brief.collect(markets)
    if not collected["indices"] and not any(collected["news"].values()):
        raise click.ClickException("No market data or news could be fetched. Check your network access.")
    markdown = brief.to_markdown(collected)
    click.echo(json.dumps(collected, indent=2) if as_json else markdown)
    click.echo(f"saved: {brief.save(markdown)}", err=True)
    try:
        if send == "telegram":
            brief.send_telegram(markdown)
        if speak:
            from openjarvis.speech.elevenlabs_tts import ElevenLabsTTSBackend

            ElevenLabsTTSBackend().synthesize(brief.to_speech(collected)).save(Path(speak))
    except (RuntimeError, ValueError, ImportError) as exc:
        raise click.ClickException(str(exc)) from None


@main.command("screen")
@click.option("--market", default="india", show_default=True, type=click.Choice(["india", "us", "etf"]))
@click.option("--symbol", "symbols", multiple=True, help="Yahoo symbol, e.g. TCS.NS. Repeatable.")
@click.option("--top", default=5, show_default=True)
def screen_cmd(market: str, symbols: tuple[str, ...], top: int) -> None:
    """Rank a watchlist by risk-adjusted momentum."""
    from jarvis_sidekick.markets import brief, data, indicators

    series = []
    for symbol in symbols or data.WATCHLISTS[market]:
        try:
            series.append(data.history(symbol))
        except _http.FetchError as exc:
            click.echo(f"{symbol}: {exc}", err=True)
    if not series:
        raise click.ClickException("No price data could be fetched.")
    for snap in indicators.screen(series, top):
        click.echo(f"{snap.score:6.2f}  {snap.symbol:<14} {snap.price:>10,.2f}  3m {snap.quarter_pct:+.1f}%  "
                   f"6m {snap.half_year_pct:+.1f}%  vol {snap.volatility_pct}%  RSI {snap.rsi}  {snap.note}")
    click.echo(f"\n{brief.DISCLAIMER}")


@main.command("plan")
@click.option("--monthly", required=True, type=float, help="Rupees invested per month.")
@click.option("--years", required=True, type=int)
@click.option("--risk", default="moderate", show_default=True, type=click.Choice(["conservative", "moderate", "aggressive"]))
def plan_cmd(monthly: float, years: int, risk: str) -> None:
    """A monthly SIP plan with allocation and projected value."""
    from jarvis_sidekick.markets import plan

    try:
        built = plan.build(monthly, years, risk)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(f"Rs {monthly:,.0f}/month for {years} years, {risk} risk\n")
    for row in built.allocation:
        click.echo(f"  {row['percent']:>3}%  Rs {row['monthly']:>9,}  {row['where']}")
    click.echo(f"\nInvested: Rs {built.invested:,.0f}")
    for label, value in built.projected.items():
        click.echo(f"  {label:<12} ({built.blended_return_pct[label]}%/yr assumed): Rs {value:,.0f}")
    click.echo("\n" + "\n".join(f"- {s}" for s in built.steps + built.caveats))


# ---------------------------------------------------------------- profile and side hustle


@main.group()
def profile() -> None:
    """Read your public footprint."""


@profile.command("github")
@click.argument("user")
def profile_github(user: str) -> None:
    from jarvis_sidekick.profile import github

    try:
        _emit(github.summary(user))
    except (_http.FetchError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None


@profile.command("youtube")
@click.argument("channel_id")
def profile_youtube(channel_id: str) -> None:
    from jarvis_sidekick.profile import youtube

    try:
        _emit(youtube.channel(channel_id))
    except (_http.FetchError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None


@main.command("channel")
@click.option("--id", "channel_id", envvar="SIDEKICK_YOUTUBE_CHANNEL", required=True,
              help="Your channel id (UC...). Or set SIDEKICK_YOUTUBE_CHANNEL.")
def channel_cmd(channel_id: str) -> None:
    """Your YouTube channel: recent uploads and their views."""
    from jarvis_sidekick.profile import youtube

    try:
        click.echo(youtube.report(youtube.channel(channel_id)))
    except (_http.FetchError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None


@main.group()
def content() -> None:
    """Check drafts against your publishing rules before you post."""


@content.command("check")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--kind", default="script", show_default=True,
              type=click.Choice(["youtube-title", "youtube-description", "linkedin", "script"]))
def content_check(file: str, kind: str) -> None:
    """Exit 1 if FILE breaks a rule: blocklisted names, outcome promises, length, missing lines."""
    from jarvis_sidekick import content as rules

    try:
        findings = rules.check(Path(file).read_text(encoding="utf-8"), kind)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    for finding in findings:
        where = f"line {finding.line}: " if finding.line else ""
        click.echo(f"{finding.level.upper():<7} {finding.rule}: {where}{finding.message}")
    if not findings:
        click.echo("OK: no findings")
    if not rules.passed(findings):
        sys.exit(1)


@main.command("hustle")
@click.option("--resume", "resume_path", envvar="SIDEKICK_RESUME", type=click.Path(exists=True, dir_okay=False))
@click.option("--github", "github_user", default="")
@click.option("--skill", "skills", multiple=True)
def hustle_cmd(resume_path: str | None, github_user: str, skills: tuple[str, ...]) -> None:
    """Side-income ideas ranked by fit with your resume and GitHub profile."""
    from jarvis_sidekick import hustle
    from jarvis_sidekick.common import gather_skills

    try:
        found = gather_skills(list(skills), resume_path or "", github_user)
    except (_http.FetchError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    for idea in hustle.rank(found):
        click.echo(f"{idea.fit:>3}% fit  {idea.name}  ({idea.effort}; first income in {idea.time_to_first_income})")
        click.echo(f"         uses: {', '.join(idea.matched_skills) or 'no direct overlap yet'}")
        for step in idea.first_steps:
            click.echo(f"         - {step}")


if __name__ == "__main__":
    main()
