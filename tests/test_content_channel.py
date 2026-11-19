from __future__ import annotations

import json
import plistlib
from pathlib import Path

import httpx
import respx
from click.testing import CliRunner

from jarvis_sidekick import content
from jarvis_sidekick.bot import Commander, _spoken_to_command
from jarvis_sidekick.cli import main
from jarvis_sidekick.profile import youtube

ROOT = Path(__file__).resolve().parent.parent
RULES = {"blocklist": ["Globex Corp", "Project Falcon"], "watchlist": ["Jon Smyth"],
         "required_in_youtube_description": ["https://github.com/me/skill"]}
DESCRIPTION = """Trace a payment rule from requirement to test in a made-up wallet app.
1. Write the rule. 2. Run /sdd-verify.
Free open-source skill: https://github.com/me/skill
Narration: AI-generated voice.
#specdriven #ai"""


def _rules(findings):
    return [(f.level, f.rule) for f in findings]


def test_clean_description_passes():
    assert content.check(DESCRIPTION, "youtube-description", RULES) == []


def test_blocklisted_names_are_errors_and_are_not_echoed():
    text = "Line one.\nAt globex corp we shipped Project Falcon.\n"
    findings = content.check(text, "script", RULES)
    assert _rules(findings) == [("error", "blocklist"), ("error", "blocklist")]
    assert all(f.line == 2 for f in findings)
    assert not any("globex" in f.message.lower() or "falcon" in f.message.lower() for f in findings)
    assert not content.passed(findings)
    assert content.check("Globexcorporation is fine", "script", RULES) == []  # whole phrases only


def test_outcome_promises_are_errors_but_honest_negations_and_topic_names_are_not():
    assert _rules(content.check("This skill makes your team compliant.", "script", {})) == [("error", "promise")]
    assert _rules(content.check("Results guaranteed.", "script", {})) == [("error", "promise")]
    assert _rules(content.check("Your pipeline becomes fully audit-proof.", "script", {})) == [("error", "promise")]
    assert content.check("It does not make anything compliant or certified.", "script", {}) == []
    assert content.check("Week 2: Fintech and compliance. An approval trail a reviewer can follow.", "script", {}) == []


def test_warnings_ask_for_a_second_look_without_blocking():
    findings = content.check("In a fashion retailer, 40% of 200 teams used the exploit. Jon Smyth agrees.", "script", RULES)
    assert {f.rule for f in findings} == {"example-domain", "number", "security-tone", "watchlist"}
    assert all(f.level == "warning" for f in findings) and content.passed(findings)


def test_format_rules_per_kind():
    assert _rules(content.check("x" * 61, "youtube-title", {})) == [("error", "length")]
    assert content.check("x" * 60, "youtube-title", {}) == []
    assert _rules(content.check("too short #a #b #c #d", "linkedin", {})) == [("error", "length"), ("error", "hashtags")]
    assert content.check(" ".join(["word"] * 100), "linkedin", {}) == []
    missing = content.check("Just a line.", "youtube-description", RULES)
    assert [f.message for f in missing] == ["missing: a required link or line from content.json",
                                            "missing: Narration: AI-generated voice."]
    # the AI-voice line is required even when content.json does not list it, or does not exist
    assert [f.message for f in content.check("Hi", "youtube-description", {})] == ["missing: Narration: AI-generated voice."]


def test_cli_reads_private_rules_from_the_state_folder_and_sets_the_exit_code(tmp_path, monkeypatch):
    from jarvis_sidekick import paths

    (paths.home() / "content.json").write_text(json.dumps(RULES))
    draft = tmp_path / "post.txt"
    draft.write_text("We built this at Globex Corp.")
    runner = CliRunner()
    bad = runner.invoke(main, ["content", "check", str(draft)])
    assert bad.exit_code == 1 and "ERROR   blocklist: line 1" in bad.output and "Globex" not in bad.output
    draft.write_text("We built this in a made-up library app.")
    good = runner.invoke(main, ["content", "check", str(draft)])
    assert good.exit_code == 0 and "OK: no findings" in good.output


FEED = {"channel": "My Channel", "videos": [
    {"id": "a", "title": "Explainer", "published": "2026-11-10", "url": "https://youtu.be/a", "views": 1200, "description": ""},
    {"id": "b", "title": "First Short", "published": "2026-11-18", "url": "https://youtu.be/b", "views": 300, "description": ""},
]}


def test_channel_report_totals_and_best_video():
    report = youtube.report(FEED)
    assert report.startswith("My Channel: 2 recent videos, 1,500 views in total")
    assert report.endswith("Most watched: Explainer (1,200 views)")
    assert "no public videos" in youtube.report({"channel": "X", "videos": []})


@respx.mock
def test_bot_channel_command_and_spoken_form(monkeypatch):
    assert "SIDEKICK_YOUTUBE_CHANNEL" in Commander().handle("/channel")
    monkeypatch.setattr(youtube, "channel", lambda channel_id, client=None: FEED)
    assert "1,500 views in total" in Commander(channel_id="UC" + "a" * 22).handle("How is my channel doing?", spoken=True)
    assert _spoken_to_command("show my YouTube channel") == "/channel"


@respx.mock
def test_cli_channel(monkeypatch):
    respx.get("https://www.youtube.com/feeds/videos.xml").mock(return_value=httpx.Response(404))
    result = CliRunner().invoke(main, ["channel", "--id", "UC" + "a" * 22])
    assert result.exit_code == 1 and "HTTP 404" in result.output


def test_macos_service_definition_is_valid_and_keeps_secrets_out():
    raw = (ROOT / "deploy/macos/com.jarvis.sidekick.bot.plist").read_bytes()
    plist = plistlib.loads(raw)
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    command = plist["ProgramArguments"][-1]
    assert ".jarvis-sidekick/.env" in command and command.endswith("bot --speak")
    assert "EnvironmentVariables" not in plist  # keys stay in the chmod-600 env file, not in the plist


def test_container_is_hardened():
    compose = (ROOT / "deploy/cloud/docker-compose.yml").read_text()
    for needle in ("read_only: true", "cap_drop: [ALL]", "no-new-privileges:true", "env_file:"):
        assert needle in compose
    assert "ports:" not in compose
    dockerfile = (ROOT / "deploy/cloud/Dockerfile").read_text()
    assert "USER sidekick" in dockerfile and "\nEXPOSE" not in dockerfile
