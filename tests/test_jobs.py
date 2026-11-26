from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
import respx

from jarvis_sidekick import resume
from jarvis_sidekick.jobs import apply, match, sources
from jarvis_sidekick.jobs.sources import Job
from jarvis_sidekick.jobs.tracker import Tracker

REMOTIVE = {
    "jobs": [
        {
            "url": "https://remotive.com/remote-jobs/1",
            "title": "Senior Python Engineer",
            "company_name": "Acme",
            "candidate_required_location": "Worldwide",
            "publication_date": "2026-11-10T08:00:00",
            "salary": "$90k",
            "tags": ["python", "aws"],
            "description": "<p>Build <b>FastAPI</b> services on AWS with Docker &amp; Kubernetes.</p><ul><li>Python</li><li>SQL</li></ul>",
        },
        {"url": "", "title": "No link", "company_name": "X"},
    ]
}
GREENHOUSE = {
    "jobs": [
        {
            "title": "Java Developer",
            "absolute_url": "https://boards.greenhouse.io/globex/jobs/7",
            "location": {"name": "Bengaluru, India"},
            "content": "&lt;p&gt;Java and Spring Boot with Kafka.&lt;/p&gt;",
            "updated_at": "2026-11-01T00:00:00Z",
        }
    ]
}
LEVER = [
    {
        "text": "AI Engineer",
        "hostedUrl": "https://jobs.lever.co/initech/abc",
        "categories": {"location": "Remote - India", "team": "ML"},
        "descriptionPlain": "LLM, RAG and AI Agents in Python.",
    }
]


def test_resume_parsing_finds_contact_details_and_skills(resume_file):
    profile = resume.load(resume_file)
    assert profile.name == "Asha Rao"
    assert profile.email == "asha.rao@example.com"
    assert profile.phone == "+91 98765 43210"
    assert profile.linkedin == "https://linkedin.com/in/asha-rao"
    assert profile.github == "https://github.com/asharao"
    assert profile.years_experience == 8
    assert {"Python", "FastAPI", "AWS", "Kubernetes", "AI Agents", "CI/CD"} <= set(profile.skills)
    assert "text" not in profile.to_dict()


def test_resume_quirks_from_real_pdfs():
    # a profile address broken across two lines by the PDF, and skills written the way people write them
    text = "Asha Rao\nBengaluru · asha@example.com · linkedin.com/in/asha-\nrao-12ab34\nEngineering Manager. GenAI, tech lead."
    profile = resume.parse(text)
    assert profile.linkedin == "https://linkedin.com/in/asha-rao-12ab34"
    assert profile.skills == ["Generative AI", "Engineering Management", "Technical Leadership"]
    assert resume.find_skills("LLM and LLMs and large language models") == ["LLM"]  # no duplicates


def test_skill_matching_uses_whole_words():
    assert resume.find_skills("We use Go and C++, not Golang or Javascripting") == ["Go", "C++"]
    assert resume.find_skills("Django is good") == ["Django"]  # no 'Go' inside Django
    assert resume.find_skills("C# and .NET on Azure") == ["C#", ".NET", "Azure"]


@respx.mock
def test_search_merges_sources_reports_failures_and_drops_bad_rows():
    respx.get("https://remotive.com/api/remote-jobs").mock(return_value=httpx.Response(200, json=REMOTIVE))
    respx.get("https://www.arbeitnow.com/api/job-board-api").mock(return_value=httpx.Response(503))
    respx.get("https://remoteok.com/api").mock(return_value=httpx.Response(200, json=[{"legal": "notice"}]))
    respx.get("https://boards-api.greenhouse.io/v1/boards/globex/jobs").mock(
        return_value=httpx.Response(200, json=GREENHOUSE)
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/initech/jobs").mock(return_value=httpx.Response(404))
    respx.get("https://api.lever.co/v0/postings/globex").mock(return_value=httpx.Response(404))
    respx.get("https://api.lever.co/v0/postings/initech").mock(return_value=httpx.Response(200, json=LEVER))

    jobs, errors = sources.search("python", companies=("globex", "initech", "../evil"))

    by_source = {j.source: j for j in jobs}
    assert set(by_source) == {"remotive", "greenhouse", "lever"}
    assert by_source["remotive"].description == "Build FastAPI services on AWS with Docker & Kubernetes. Python SQL"
    assert by_source["greenhouse"].description == "Java and Spring Boot with Kafka."
    assert by_source["lever"].remote is True
    assert errors == {"arbeitnow": "www.arbeitnow.com answered HTTP 503"}  # adzuna has no key: silently empty


@respx.mock
def test_adzuna_needs_keys_and_searches_every_requested_country(monkeypatch):
    def results(place):
        return httpx.Response(200, json={"results": [
            {"title": "Data Engineer", "redirect_url": f"https://adzuna.example/{place}", "salary_min": 1e6,
             "salary_max": 2e6, "company": {"display_name": "Tata"}, "location": {"display_name": place},
             "description": "Spark and SQL"}]})

    india = respx.get("https://api.adzuna.com/v1/api/jobs/in/search/1").mock(return_value=results("Pune"))
    singapore = respx.get("https://api.adzuna.com/v1/api/jobs/sg/search/1").mock(return_value=results("Singapore"))
    assert sources.adzuna("data engineer") == [] and not india.called

    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")
    (job,) = sources.adzuna("data engineer")  # default country: India
    assert (job.company, job.location, job.salary) == ("Tata", "Pune (IN)", "1000000-2000000")

    monkeypatch.setenv("SIDEKICK_COUNTRIES", "in, sg")
    assert [j.location for j in sources.adzuna("data engineer")] == ["Pune (IN)", "Singapore (SG)"]
    assert singapore.called
    with pytest.raises(Exception, match="does not cover: xx"):
        sources.adzuna("data engineer", countries=("xx",))


@respx.mock
def test_arbeitnow_accepts_a_page_larger_than_the_default_cap():
    big = {"data": [{"title": "Engineer", "url": "https://a.example/1", "description": "x" * 6_000_000}]}
    respx.get("https://www.arbeitnow.com/api/job-board-api").mock(return_value=httpx.Response(200, json=big))
    (job,) = sources.arbeitnow("engineer")
    assert job.title == "Engineer"


def test_several_acceptable_places(resume_file):
    profile = resume.load(resume_file)
    where = "India, Singapore, remote"

    def part(**kwargs):
        return match.score(_job("X", "", **kwargs), profile, location=where).score

    assert part(location="Singapore (SG)") == 15.0
    assert part(location="Bengaluru, India") == 15.0
    assert part(location="Berlin", remote=True) == 15.0
    assert part(location="Berlin") == 0.0
    assert match.score(_job("X", "", location="Berlin", remote=True), profile, location="India").score == 9.0


def _job(title, description, **kwargs):
    return Job(source="t", title=title, company="Co", url=f"https://x.example/{title}", description=description, **kwargs)


def test_ranking_prefers_skill_overlap_and_explains_itself(resume_file):
    profile = resume.load(resume_file)
    good = _job("Senior Python Engineer", "Python, FastAPI, AWS, Docker", remote=True)
    partial = _job("Python Engineer", "Python, Django, Kafka, Spark", remote=True)
    wrong = _job("Java Developer", "Java, Spring Boot, Kafka", location="Berlin")

    ranked = match.rank([wrong, partial, good], profile, query="python engineer", location="remote")

    assert [m.job.title for m in ranked] == ["Senior Python Engineer", "Python Engineer", "Java Developer"]
    assert ranked[0].score == 100.0 and ranked[0].missing == []
    assert ranked[1].matched == ["Python"] and set(ranked[1].missing) == {"Django", "Kafka", "Spark"}
    assert ranked[2].score == 0.0
    assert match.rank([wrong, good], profile, query="python engineer", location="remote", min_score=50) == ranked[:1]


def test_cover_letter_states_only_matched_facts(resume_file):
    profile = resume.load(resume_file)
    letter = match.cover_letter(match.score(_job("AI Engineer", "Python, LLM, Rust"), profile), profile)
    assert "AI Engineer" in letter and "Python, LLM" in letter and "8 years" in letter
    assert "Rust" not in letter and "asha.rao@example.com" in letter


def test_tracker_updates_in_place_and_counts_todays_submissions(tmp_path):
    tracker = Tracker(tmp_path / "t.db")
    tracker.record("a", url="u", title="T", company="C", score=80, status="prepared")
    tracker.record("a", url="u", title="T", company="C", score=80, status="submitted")
    tracker.record("b", url="u2", title="T2", company="C", score=70, status="needs_human")
    assert tracker.get("a")["status"] == "submitted" and len(tracker.all()) == 2
    assert tracker.submitted_today() == 1 and tracker.get("zzz") is None


def test_prepare_writes_packet_and_keeps_an_edited_letter(resume_file):
    profile = resume.load(resume_file)
    item = match.score(_job("AI Engineer", "Python, LLM"), profile)
    folder = apply.prepare(item, profile)
    letter = folder / "cover_letter.md"
    assert (folder / "job.json").exists() and "AI Engineer" in letter.read_text()
    letter.write_text("my own words")
    apply.prepare(item, profile)
    assert letter.read_text() == "my own words"
    assert Tracker().get(item.job.id)["status"] == "prepared"


def test_submit_refuses_unsupported_hosts_and_incomplete_resumes(resume_file):
    profile = resume.load(resume_file)
    assert apply.submit("https://www.linkedin.com/jobs/view/1", profile).status == "skipped"
    assert apply.submit("https://jobs.lever.co/x/1", resume.Profile()).status == "error"


def test_submit_never_reapplies_and_stops_at_the_daily_cap(resume_file, tmp_path):
    profile = resume.load(resume_file)
    tracker = Tracker(tmp_path / "t.db")
    tracker.record("done", url="u", title="T", company="C", score=1, status="submitted")
    url = "https://jobs.lever.co/x/1"
    assert apply.submit(url, profile, tracker=tracker, job_id="done", confirm=True).detail == "already applied to this job"
    assert "daily cap" in apply.submit(url, profile, tracker=tracker, job_id="new", confirm=True, daily_cap=1).detail


# ---- the form filler, against a real browser and a local stand-in for an application form

FORM = """<html><body><form method="post" action="/submit" enctype="multipart/form-data">
<label for="first_name">First name</label><input id="first_name" name="first_name" required>
<label for="last_name">Last name</label><input id="last_name" name="last_name" required>
<label for="email">Email</label><input id="email" name="email" type="email" required>
<label for="phone">Phone</label><input id="phone" name="phone">
<label for="resume">Resume</label><input id="resume" name="resume" type="file" required>
<label for="cover_letter_text">Cover letter</label><textarea id="cover_letter_text" name="cover_letter_text"></textarea>
%s
<button type="submit">Submit application</button></form></body></html>"""
QUESTION = '<label for="q1">Are you authorised to work in India?</label><input id="q1" name="q1" required>'
CAPTCHA = '<div class="g-recaptcha"></div>'


@pytest.fixture
def form_server():
    posts: list[bytes] = []
    state = {"extra": ""}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (FORM % state["extra"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Thanks</h1>")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/apply", posts, state
    server.shutdown()


@pytest.fixture
def browser_env(monkeypatch):
    pytest.importorskip("playwright")
    chromium = Path("/opt/pw-browsers/chromium")
    if chromium.exists():
        monkeypatch.setenv("SIDEKICK_CHROMIUM", str(chromium))


def test_dry_run_fills_the_form_but_sends_nothing(form_server, browser_env, resume_file, tmp_path):
    url, posts, _ = form_server
    tracker = Tracker(tmp_path / "t.db")
    result = apply.submit(url, resume.load(resume_file), "Dear team", tracker=tracker, job_id="j1", allow_any_host=True)
    assert result.status == "ready", result
    assert set(result.filled) == {"first_name", "last_name", "email", "phone", "cover_letter", "resume"}
    assert posts == [] and Path(result.screenshot).exists()
    assert tracker.get("j1")["status"] == "ready"


def test_confirm_sends_the_form_with_resume_and_details(form_server, browser_env, resume_file, tmp_path):
    url, posts, _ = form_server
    tracker = Tracker(tmp_path / "t.db")
    result = apply.submit(url, resume.load(resume_file), "Dear team", confirm=True, tracker=tracker, job_id="j1",
                          allow_any_host=True)
    assert result.status == "submitted", result
    (body,) = posts
    assert b"Asha" in body and b"Rao" in body and b"asha.rao@example.com" in body and b"Dear team" in body
    assert b'filename="resume.md"' in body and b"Senior software engineer" in body
    assert tracker.get("j1")["status"] == "submitted" and tracker.submitted_today() == 1
    # a second attempt is refused before any browser starts
    assert apply.submit(url, resume.load(resume_file), confirm=True, tracker=tracker, job_id="j1",
                        allow_any_host=True).status == "skipped"
    assert len(posts) == 1


def test_unanswered_required_question_blocks_sending_even_with_confirm(form_server, browser_env, resume_file):
    url, posts, state = form_server
    state["extra"] = QUESTION
    result = apply.submit(url, resume.load(resume_file), confirm=True, allow_any_host=True)
    assert result.status == "needs_human"
    assert result.unanswered == ["Are you authorised to work in India?"] and posts == []


def test_pre_approved_answer_unblocks_the_question(form_server, browser_env, resume_file):
    url, posts, state = form_server
    state["extra"] = QUESTION
    result = apply.submit(url, resume.load(resume_file), confirm=True, allow_any_host=True,
                          answers={"authorised to work in India": "Yes"})
    assert result.status == "submitted" and b"Yes" in posts[0]


def test_captcha_blocks_sending(form_server, browser_env, resume_file):
    url, posts, state = form_server
    state["extra"] = CAPTCHA
    result = apply.submit(url, resume.load(resume_file), confirm=True, allow_any_host=True)
    assert result.status == "needs_human" and "CAPTCHA" in result.detail and posts == []
