from __future__ import annotations

from itertools import pairwise

import httpx
import pytest
import respx
from click.testing import CliRunner

from jarvis_sidekick import hustle
from jarvis_sidekick.cli import main
from jarvis_sidekick.profile import github, youtube

REPOS = [
    {"name": "sdd", "language": "Python", "topics": ["ai-agents", "llm"], "stargazers_count": 12, "fork": False,
     "pushed_at": "2026-11-01T00:00:00Z", "html_url": "https://github.com/u/sdd", "description": None},
    {"name": "web", "language": "TypeScript", "topics": ["llm"], "stargazers_count": 3, "fork": False},
    {"name": "forked", "language": "Go", "fork": True, "stargazers_count": 999},
]
FEED = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"
 xmlns:media="http://search.yahoo.com/mrss/"><title>My Channel</title>
<entry><yt:videoId>abc123</yt:videoId><title>Build an AI agent</title>
<link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/><published>2026-11-10T12:00:00+00:00</published>
<media:group><media:description>How to build one</media:description>
<media:community><media:statistics views="1500"/></media:community></media:group></entry></feed>"""


@respx.mock
def test_github_summary_ignores_forks():
    respx.get("https://api.github.com/users/u/repos").mock(return_value=httpx.Response(200, json=REPOS))
    summary = github.summary("u")
    assert summary["public_repos"] == 2 and summary["stars"] == 15
    assert summary["languages"] == {"Python": 1, "TypeScript": 1} and summary["topics"][0] == "llm"
    with pytest.raises(ValueError):
        github.summary("../../orgs/x")


@respx.mock
def test_youtube_channel_feed():
    channel_id = "UC" + "a" * 22
    respx.get("https://www.youtube.com/feeds/videos.xml").mock(return_value=httpx.Response(200, content=FEED))
    result = youtube.channel(channel_id)
    assert result["channel"] == "My Channel"
    assert result["videos"] == [{"id": "abc123", "title": "Build an AI agent", "published": "2026-11-10",
                                 "url": "https://www.youtube.com/watch?v=abc123", "views": 1500,
                                 "description": "How to build one"}]
    with pytest.raises(ValueError):
        youtube.channel("@handle")


def test_hustle_ranking_follows_skills():
    ideas = hustle.rank(["playwright", "Selenium", "CI/CD", "Python"])
    assert ideas[0].name == "Test automation and QA-as-a-service" and ideas[0].fit == 100
    assert ideas[0].matched_skills == ["Playwright", "Selenium", "CI/CD"]
    assert all(a.fit >= b.fit for a, b in pairwise(ideas))


def test_cli_plan_and_validation():
    runner = CliRunner()
    ok = runner.invoke(main, ["plan", "--monthly", "10000", "--years", "10"])
    assert ok.exit_code == 0 and "Invested: Rs 1,200,000" in ok.output
    bad = runner.invoke(main, ["plan", "--monthly", "-1", "--years", "10"])
    assert bad.exit_code == 1 and "monthly must be positive" in bad.output


@respx.mock
def test_cli_hustle_combines_resume_and_github(resume_file):
    respx.get("https://api.github.com/users/u/repos").mock(return_value=httpx.Response(200, json=REPOS))
    result = CliRunner().invoke(main, ["hustle", "--resume", str(resume_file), "--github", "u"])
    assert result.exit_code == 0, result.output
    # Both fit fully; the tie goes to the idea whose skill list is covered more (5 of 8 against 4 of 8).
    assert result.output.startswith("100% fit  Micro-SaaS or paid developer tool")
    assert "100% fit  AI agent and LLM integration consulting" in result.output
    assert "uses: Python, PostgreSQL, AWS, Docker, TypeScript" in result.output  # TypeScript comes from GitHub
    assert CliRunner().invoke(main, ["hustle"]).exit_code == 1


@respx.mock
def test_cli_job_search_prepares_packets_and_status_lists_them(resume_file):
    respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [
            {"url": "https://remotive.com/j/1", "title": "Python Engineer", "company_name": "Acme",
             "description": "<ul><li>Python</li><li>FastAPI</li><li>Rust</li></ul>"}]})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs", "search", "python engineer", "--resume", str(resume_file),
                                  "--source", "remotive", "--prepare", "1"])
    assert result.exit_code == 0, result.output
    assert "Python Engineer @ Acme" in result.output and "gaps: Rust" in result.output
    assert "Prepared 1 packet(s)" in result.output
    status = runner.invoke(main, ["jobs", "status"])
    assert "prepared" in status.output and "Python Engineer @ Acme" in status.output


def test_cli_voice_and_transcribe_need_a_key(tmp_path):
    runner = CliRunner()
    assert "Set ELEVENLABS_API_KEY" in runner.invoke(main, ["voice"]).output
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    result = runner.invoke(main, ["transcribe", str(audio)])
    assert result.exit_code == 1 and "ELEVENLABS_API_KEY not set" in result.output


@respx.mock
def test_cli_brief_fails_loudly_when_nothing_can_be_fetched():
    respx.route().mock(return_value=httpx.Response(503))
    result = CliRunner().invoke(main, ["brief"])
    assert result.exit_code == 1 and "No market data or news could be fetched" in result.output
