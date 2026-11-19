"""Score jobs against a resume. Deterministic and explainable: no model needed."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis_sidekick.jobs.sources import Job
from jarvis_sidekick.resume import Profile, find_skills

_WORD = re.compile(r"[a-z0-9+#.]+")
_STOP = {"and", "or", "the", "of", "a", "in", "for", "to", "with", "senior", "junior", "lead", "staff", "sr", "jr"}


@dataclass
class Match:
    job: Job
    score: float
    matched: list[str]
    missing: list[str]
    reasons: list[str]

    def to_dict(self) -> dict:
        return {
            **self.job.to_dict(),
            "score": self.score,
            "matched_skills": self.matched,
            "missing_skills": self.missing,
            "why": self.reasons,
        }


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def score(job: Job, profile: Profile, *, query: str = "", location: str = "") -> Match:
    """0-100. 60% skill overlap, 25% title vs. the roles you asked for, 15% location."""
    wanted = find_skills(f"{job.title}\n{job.description}\n{' '.join(job.tags)}")
    mine = set(profile.skills)
    matched = [s for s in wanted if s in mine]
    missing = [s for s in wanted if s not in mine]
    skill_part = len(matched) / len(wanted) if wanted else 0.0

    query_words, title_words = _words(query), _words(job.title)
    title_part = len(query_words & title_words) / len(query_words) if query_words else 0.0

    # Several acceptable places may be given, comma-separated: "India, Singapore, UAE, remote".
    places = [p.strip().lower() for p in location.split(",") if p.strip()]
    haystack = f"{job.location} {job.title}".lower()
    if not places or "anywhere" in places or any(p in haystack for p in places if p != "remote"):
        location_part = 1.0
    elif job.remote:
        location_part = 1.0 if "remote" in places else 0.6
    else:
        location_part = 0.0

    total = round(100 * (0.6 * skill_part + 0.25 * title_part + 0.15 * location_part), 1)
    reasons = [f"{len(matched)}/{len(wanted)} listed skills match" if wanted else "no recognisable skills listed"]
    if title_part:
        reasons.append("title matches your search")
    if job.remote:
        reasons.append("remote")
    return Match(job=job, score=total, matched=matched, missing=missing, reasons=reasons)


def rank(jobs: list[Job], profile: Profile, *, query: str = "", location: str = "", min_score: float = 0.0,
         limit: int = 20) -> list[Match]:
    matches = [score(job, profile, query=query, location=location) for job in jobs]
    matches = [m for m in matches if m.score >= min_score]
    matches.sort(key=lambda m: (-m.score, m.job.title))
    return matches[:limit]


def cover_letter(match: Match, profile: Profile) -> str:
    """A short, factual letter built only from what the resume and the listing both say."""
    job = match.job
    skills = ", ".join(match.matched[:6]) or "the skills this role calls for"
    experience = f"{profile.years_experience} years of experience" if profile.years_experience else "my experience"
    lines = [
        f"Dear {job.company or 'Hiring'} team,",
        "",
        (
            f"I am applying for the {job.title} role. The listing asks for {skills}, which is the work I do: "
            f"{experience} is described in the attached resume."
        ),
        "",
        "I would welcome a conversation about how I can contribute.",
        "",
        "Regards,",
        profile.name or "",
    ]
    contact = " | ".join(x for x in (profile.email, profile.phone, profile.linkedin) if x)
    return "\n".join(lines + ([contact] if contact else [])).rstrip() + "\n"
