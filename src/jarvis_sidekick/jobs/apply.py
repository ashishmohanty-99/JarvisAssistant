"""Prepare and submit job applications, with a person in control.

Two steps, on purpose:

* ``prepare`` writes a packet (listing, cover letter) and records it. No network.
* ``submit`` opens the employer's Greenhouse or Lever form in a real browser, fills
  what your resume answers, and stops. It sends the form only when called with
  ``confirm=True`` and only when no required question is left unanswered.

It never guesses answers to screening questions, never solves CAPTCHAs, never
applies twice to one job, and stops at a daily cap.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from jarvis_sidekick import paths
from jarvis_sidekick.jobs.match import Match, cover_letter
from jarvis_sidekick.jobs.tracker import Tracker
from jarvis_sidekick.resume import Profile

SUPPORTED_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co")
DAILY_CAP = 10

# Profile field -> selectors tried in order (Greenhouse ids first, then Lever names).
_FIELDS = {
    "first_name": ["#first_name", "input[name='first_name']"],
    "last_name": ["#last_name", "input[name='last_name']"],
    "name": ["input[name='name']"],
    "email": ["#email", "input[name='email']", "input[type='email']"],
    "phone": ["#phone", "input[name='phone']", "input[type='tel']"],
    "linkedin": ["input[name='urls[LinkedIn]']", "input[aria-label*='LinkedIn' i]"],
    "github": ["input[name='urls[GitHub]']", "input[aria-label*='GitHub' i]"],
    "cover_letter": ["#cover_letter_text", "textarea[name='cover_letter_text']", "textarea[name='comments']"],
}
_RESUME = ["#resume", "input[type='file'][name='resume']", "input[type='file']"]
_CAPTCHA = "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha"
_SUBMIT = "#submit_app, button[type='submit'], input[type='submit']"

# Runs in the page: required controls that are still empty, described by their label.
_UNANSWERED_JS = """() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (!(el.required || el.getAttribute('aria-required') === 'true')) continue;
    if (el.type === 'hidden' || el.disabled) continue;
    let empty;
    if (el.type === 'checkbox' || el.type === 'radio') {
      if (seen.has(el.name)) continue;
      seen.add(el.name);
      empty = !document.querySelector(`input[name="${CSS.escape(el.name)}"]:checked`);
    } else if (el.type === 'file') {
      empty = el.files.length === 0;
    } else {
      empty = !el.value.trim();
    }
    if (!empty) continue;
    const label = (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label')
      || el.name || el.id || 'unnamed field';
    out.push(label.trim().replace(/\\s+/g, ' ').slice(0, 120));
  }
  return out;
}"""


@dataclass
class Result:
    status: str  # ready | submitted | needs_human | skipped | error
    detail: str = ""
    filled: list[str] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    screenshot: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def prepare(match: Match, profile: Profile, tracker: Tracker | None = None) -> Path:
    """Write the application packet for one job and record it as prepared."""
    folder = paths.home() / "applications" / match.job.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "job.json").write_text(json.dumps(match.to_dict(), indent=2), encoding="utf-8")
    letter = folder / "cover_letter.md"
    if not letter.exists():  # never overwrite a letter the person has edited
        letter.write_text(cover_letter(match, profile), encoding="utf-8")
    own = tracker is None
    tracker = tracker or Tracker()
    existing = tracker.get(match.job.id)
    if not existing or existing["status"] not in ("submitted",):
        tracker.record(
            match.job.id, url=match.job.url, title=match.job.title, company=match.job.company,
            score=match.score, status="prepared", detail=str(folder),
        )
    if own:
        tracker.close()
    return folder


def _values(profile: Profile, letter: str) -> dict[str, str]:
    first, _, last = profile.name.partition(" ")
    return {
        "first_name": first,
        "last_name": last,
        "name": profile.name,
        "email": profile.email,
        "phone": profile.phone,
        "linkedin": profile.linkedin,
        "github": profile.github,
        "cover_letter": letter,
    }


def _fill(page, profile: Profile, letter: str, answers: dict[str, str]) -> list[str]:
    filled = []
    for key, value in _values(profile, letter).items():
        if not value:
            continue
        for selector in _FIELDS[key]:
            target = page.locator(selector).first
            if target.count() and target.is_visible():
                target.fill(value)
                filled.append(key)
                break
    if profile.path:
        for selector in _RESUME:
            target = page.locator(selector).first
            if target.count():
                target.set_input_files(profile.path)
                filled.append("resume")
                break
    # Screening questions: only ones the person has pre-answered, matched by label text.
    for question, answer in answers.items():
        target = page.get_by_label(question, exact=False).first
        if not target.count():
            continue
        tag = target.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            target.select_option(label=answer)
        elif target.get_attribute("type") in ("checkbox", "radio"):
            target.check()
        else:
            target.fill(answer)
        filled.append(f"answer:{question}")
    return filled


def submit(
    url: str,
    profile: Profile,
    letter: str = "",
    *,
    confirm: bool = False,
    answers: dict[str, str] | None = None,
    tracker: Tracker | None = None,
    job_id: str = "",
    daily_cap: int = DAILY_CAP,
    headless: bool = True,
    allow_any_host: bool = False,
) -> Result:
    """Fill the application form at *url*; send it only if ``confirm`` and nothing is unanswered."""
    host = urlsplit(url).hostname or ""
    if not allow_any_host and host not in SUPPORTED_HOSTS:
        return Result("skipped", f"{host or 'this address'} is not a supported form (Greenhouse or Lever only)")
    if not (profile.name and profile.email):
        return Result("error", "the resume has no name or email to apply with")
    if tracker and job_id:
        previous = tracker.get(job_id)
        if previous and previous["status"] == "submitted":
            return Result("skipped", "already applied to this job")
        if confirm and tracker.submitted_today() >= daily_cap:
            return Result("skipped", f"daily cap of {daily_cap} applications reached")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return Result("error", "submitting needs: pip install 'jarvis-sidekick[apply]' && playwright install chromium")

    if host == "jobs.lever.co" and not url.rstrip("/").endswith("/apply"):
        url = url.rstrip("/") + "/apply"
    shot = paths.home() / "applications" / (job_id or "adhoc") / "form.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    launch = {"headless": headless}
    if os.environ.get("SIDEKICK_CHROMIUM"):
        launch["executable_path"] = os.environ["SIDEKICK_CHROMIUM"]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            filled = _fill(page, profile, letter, answers or {})
            unanswered = page.evaluate(_UNANSWERED_JS)
            page.screenshot(path=str(shot), full_page=True)
            if page.locator(_CAPTCHA).count():
                result = Result("needs_human", "the form has a CAPTCHA; finish it yourself", filled, unanswered)
            elif unanswered:
                result = Result("needs_human", "required questions are unanswered", filled, unanswered)
            elif not confirm:
                result = Result("ready", "form filled, not sent (dry run)", filled)
            else:
                button = page.locator(_SUBMIT).first
                if not button.count():
                    result = Result("needs_human", "no submit button found", filled)
                else:
                    button.click()
                    page.wait_for_load_state("networkidle", timeout=30_000)
                    page.screenshot(path=str(shot.with_name("submitted.png")), full_page=True)
                    result = Result("submitted", "form sent", filled)
        except PlaywrightError as exc:
            result = Result("error", f"browser step failed ({type(exc).__name__})")
        finally:
            browser.close()
    result.screenshot = str(shot) if shot.exists() else ""
    if tracker and job_id and result.status in ("submitted", "needs_human", "ready"):
        previous = tracker.get(job_id) or {}
        tracker.record(
            job_id, url=url, title=previous.get("title", ""), company=previous.get("company", ""),
            score=previous.get("score") or 0.0, status=result.status,
            detail="; ".join([result.detail] + result.unanswered),
        )
    return result
