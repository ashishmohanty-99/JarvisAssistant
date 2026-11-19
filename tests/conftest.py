from __future__ import annotations

import pytest

from jarvis_sidekick import _http


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Each test gets its own state folder, no credentials, and no DNS lookups."""
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    for name in ("ELEVENLABS_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "TELEGRAM_BOT_TOKEN", "SIDEKICK_RESUME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_http, "_ssrf_reason", lambda url: None)
    _http._robots.clear()


RESUME = """Asha Rao
asha.rao@example.com | +91 98765 43210 | linkedin.com/in/asha-rao | github.com/asharao

Senior software engineer with 8 years of experience building Python and FastAPI services on AWS.
Skills: Python, FastAPI, PostgreSQL, Docker, Kubernetes, Playwright, LLM, RAG, AI Agents, CI/CD
"""


@pytest.fixture
def resume_file(tmp_path):
    path = tmp_path / "resume.md"
    path.write_text(RESUME, encoding="utf-8")
    return path
