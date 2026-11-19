"""The sidekick's capabilities as OpenJarvis tools, so any agent (and the voice chat) can call them."""

from __future__ import annotations

import json
from typing import Any

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

from jarvis_sidekick import _http, hustle, paths, scrape
from jarvis_sidekick.common import gather_skills, load_answers, resume_profile
from jarvis_sidekick.jobs import apply as job_apply
from jarvis_sidekick.jobs import match, sources
from jarvis_sidekick.jobs.tracker import Tracker
from jarvis_sidekick.markets import brief, data, indicators, plan
from jarvis_sidekick.profile import github, youtube


class _JsonTool(BaseTool):
    """A tool whose result is JSON and whose expected failures become a plain message."""

    is_local = False
    name = ""
    description = ""
    properties: dict[str, Any] = {}
    required: tuple[str, ...] = ()
    confirm = False
    timeout = 120.0

    @property
    def tool_id(self) -> str:  # type: ignore[override]
        return self.name

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters={"type": "object", "properties": self.properties, "required": list(self.required)},
            category="sidekick",
            requires_confirmation=self.confirm,
            timeout_seconds=self.timeout,
            required_capabilities=["network:fetch"],
        )

    def run(self, **params: Any) -> Any:
        raise NotImplementedError

    def execute(self, **params: Any) -> ToolResult:
        try:
            content = json.dumps(self.run(**params), ensure_ascii=False, separators=(",", ":"))
        except (_http.FetchError, ValueError, RuntimeError, OSError, TypeError) as exc:
            return ToolResult(tool_name=self.name, content=f"{self.name} failed: {exc}", success=False)
        return ToolResult(tool_name=self.name, content=content, success=True)


class WebScrapeTool(_JsonTool):
    name = "web_scrape"
    description = (
        "Read a web page as clean text. Set crawl_pages above 1 to follow links on the same site "
        "(max 50, honours robots.txt). Use web_search first to find the right address."
    )
    properties = {
        "url": {"type": "string", "description": "http(s) address to read."},
        "crawl_pages": {"type": "integer", "minimum": 1, "maximum": scrape.MAX_PAGES, "description": "Pages to read."},
    }
    required = ("url",)

    def run(self, url: str, crawl_pages: int = 1, **_: Any) -> Any:
        if crawl_pages <= 1:
            page = scrape.fetch_page(url).to_dict()
            page["links"] = page["links"][:40]
            return page
        return [{"url": p.url, "title": p.title, "text": p.text} for p in scrape.crawl(url, max_pages=crawl_pages)]


class JobSearchTool(_JsonTool):
    name = "job_search"
    description = (
        "Search open job APIs (Remotive, Arbeitnow, RemoteOK, Adzuna, and named Greenhouse/Lever company boards) "
        "and rank the results against the user's resume. Prepares an application packet for the best matches."
    )
    properties = {
        "query": {"type": "string", "description": "Role to search for, e.g. 'senior python developer'."},
        "location": {"type": "string", "description": "City or country, or 'remote'."},
        "companies": {"type": "array", "items": {"type": "string"},
                      "description": "Greenhouse/Lever board names, e.g. ['stripe']."},
        "resume_path": {"type": "string", "description": "Resume file. Defaults to SIDEKICK_RESUME."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 30},
        "prepare_top": {"type": "integer", "minimum": 0, "maximum": 10,
                        "description": "Prepare application packets for this many top matches."},
    }
    required = ("query",)

    def run(self, query: str, location: str = "", companies: list[str] | None = None, resume_path: str = "",
            limit: int = 10, prepare_top: int = 0, **_: Any) -> Any:
        profile = resume_profile(resume_path)
        jobs, errors = sources.search(query, location=location, companies=tuple(companies or ()))
        matches = match.rank(jobs, profile, query=query, location=location, limit=limit)
        for item in matches[:prepare_top]:
            job_apply.prepare(item, profile)
        return {"matches": [m.to_dict() for m in matches], "searched": len(jobs), "source_errors": errors,
                "prepared": [m.job.id for m in matches[:prepare_top]]}


class JobApplyTool(_JsonTool):
    name = "job_apply"
    description = (
        "Fill the Greenhouse or Lever application form for a prepared job (id from job_search). "
        "Without confirm it only fills the form and reports what is unanswered. With confirm=true it sends "
        "the application. Always ask the user before using confirm=true."
    )
    properties = {
        "job_id": {"type": "string", "description": "Id of a job prepared by job_search."},
        "confirm": {"type": "boolean", "description": "Send the application. Default false (dry run)."},
        "resume_path": {"type": "string"},
    }
    required = ("job_id",)
    confirm = True
    timeout = 180.0

    def run(self, job_id: str, confirm: bool = False, resume_path: str = "", **_: Any) -> Any:
        if confirm is not True and confirm is not False:
            raise ValueError("confirm must be true or false")
        tracker = Tracker()
        try:
            record = tracker.get(job_id)
            if not record:
                raise ValueError(f"No prepared job with id {job_id}. Run job_search with prepare_top first.")
            letter_file = paths.home() / "applications" / job_id / "cover_letter.md"
            letter = letter_file.read_text(encoding="utf-8") if letter_file.exists() else ""
            result = job_apply.submit(record["url"], resume_profile(resume_path), letter, confirm=confirm,
                                      answers=load_answers(), tracker=tracker, job_id=job_id)
        finally:
            tracker.close()
        return result.to_dict()


class MarketBriefTool(_JsonTool):
    name = "market_brief"
    description = (
        "Today's market briefing: Indian and global indices, USD/INR, gold, oil, top movers, a momentum screen "
        "and news headlines. Research aid only; say so when you summarise it."
    )
    properties = {"markets": {"type": "array", "items": {"type": "string", "enum": list(data.WATCHLISTS)}}}

    def run(self, markets: list[str] | None = None, **_: Any) -> Any:
        return brief.collect(tuple(markets or ("india", "us")))


class StockScreenTool(_JsonTool):
    name = "stock_screen"
    description = (
        "Rank a watchlist ('india', 'us', 'etf') or given symbols by risk-adjusted momentum with a trend filter. "
        "Returns candidates to research, never a guarantee of profit."
    )
    properties = {
        "market": {"type": "string", "enum": list(data.WATCHLISTS)},
        "symbols": {"type": "array", "items": {"type": "string"}, "description": "Yahoo symbols, e.g. TCS.NS, AAPL."},
        "top": {"type": "integer", "minimum": 1, "maximum": 20},
    }

    def run(self, market: str = "india", symbols: list[str] | None = None, top: int = 5, **_: Any) -> Any:
        wanted = symbols or data.WATCHLISTS.get(market)
        if not wanted:
            raise ValueError(f"Unknown market {market!r}")
        series, failed = [], []
        for symbol in wanted[:40]:
            try:
                series.append(data.history(str(symbol)))
            except _http.FetchError:
                failed.append(symbol)
        return {"ranked": [s.to_dict() for s in indicators.screen(series, top)], "unavailable": failed,
                "disclaimer": brief.DISCLAIMER}


class InvestmentPlanTool(_JsonTool):
    name = "investment_plan"
    description = (
        "Build a monthly SIP plan for an India-based investor: allocation across index funds, debt, gold and a "
        "capped direct-stock bucket, with projected value under stated return assumptions."
    )
    properties = {
        "monthly": {"type": "number", "description": "Amount invested per month, in rupees."},
        "years": {"type": "integer", "minimum": 1, "maximum": 50},
        "risk": {"type": "string", "enum": list(plan.ALLOCATIONS)},
    }
    required = ("monthly", "years")

    def run(self, monthly: float, years: int, risk: str = "moderate", **_: Any) -> Any:
        return plan.build(float(monthly), int(years), risk).to_dict()


class GitHubProfileTool(_JsonTool):
    name = "github_profile"
    description = "Summarise a GitHub user's public repositories: languages, topics, stars and recent projects."
    properties = {"user": {"type": "string"}}
    required = ("user",)

    def run(self, user: str, **_: Any) -> Any:
        return github.summary(user)


class YouTubeChannelTool(_JsonTool):
    name = "youtube_channel"
    description = (
        "List a YouTube channel's latest videos with views (channel_id is the UC... id), or fetch one video's "
        "transcript by video_id."
    )
    properties = {"channel_id": {"type": "string"}, "video_id": {"type": "string"}}

    def run(self, channel_id: str = "", video_id: str = "", **_: Any) -> Any:
        if video_id:
            return {"video_id": video_id, "transcript": youtube.transcript(video_id)}
        return youtube.channel(channel_id)


class SideHustleTool(_JsonTool):
    name = "side_hustle_ideas"
    description = (
        "Rank side-income ideas by fit with the user's skills, taken from their resume and, if given, their "
        "GitHub profile. Returns effort, time to first income and first steps."
    )
    properties = {
        "skills": {"type": "array", "items": {"type": "string"}},
        "resume_path": {"type": "string"},
        "github_user": {"type": "string"},
    }

    def run(self, skills: list[str] | None = None, resume_path: str = "", github_user: str = "", **_: Any) -> Any:
        return [idea.to_dict() for idea in hustle.rank(gather_skills(skills, resume_path, github_user))]


TOOLS = (WebScrapeTool, JobSearchTool, JobApplyTool, MarketBriefTool, StockScreenTool, InvestmentPlanTool,
         GitHubProfileTool, YouTubeChannelTool, SideHustleTool)
