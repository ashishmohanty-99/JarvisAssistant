"""Job sources that publish listings through an open API.

Deliberately absent: LinkedIn, Indeed and Naukri. Their terms forbid automated
access and they block it; use their own alerts and feed interesting links to
`sidekick jobs apply`. Adzuna (needs a free key) covers India well.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from lxml import etree
from lxml import html as lxml_html

from jarvis_sidekick import _http, scrape


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    location: str = ""
    remote: bool = False
    description: str = ""
    posted: str = ""
    salary: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()[:12]

    def to_dict(self, description_chars: int = 600) -> dict:
        data = asdict(self)
        data["id"] = self.id
        data["description"] = self.description[:description_chars]
        return data


def _text(value: Any) -> str:
    """Plain single-line text from a field that may hold HTML."""
    value = str(value or "")
    if "<" in value:
        try:
            value = scrape.text_of(lxml_html.fromstring(f"<div>{value}</div>"))
        except (etree.LxmlError, ValueError):
            value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def remotive(query: str, client: httpx.Client | None = None, **_: Any) -> list[Job]:
    data = _http.get_json("https://remotive.com/api/remote-jobs", params={"search": query, "limit": 50}, client=client)
    return [
        Job(
            source="remotive",
            title=_text(j.get("title")),
            company=_text(j.get("company_name")),
            url=str(j.get("url") or ""),
            location=_text(j.get("candidate_required_location")),
            remote=True,
            description=_text(j.get("description")),
            posted=str(j.get("publication_date") or "")[:10],
            salary=_text(j.get("salary")),
            tags=[str(t) for t in j.get("tags") or []],
        )
        for j in data.get("jobs") or []
    ]


def arbeitnow(query: str, client: httpx.Client | None = None, **_: Any) -> list[Job]:
    data = _http.get_json("https://www.arbeitnow.com/api/job-board-api", client=client)
    return [
        Job(
            source="arbeitnow",
            title=_text(j.get("title")),
            company=_text(j.get("company_name")),
            url=str(j.get("url") or ""),
            location=_text(j.get("location")),
            remote=bool(j.get("remote")),
            description=_text(j.get("description")),
            tags=[str(t) for t in j.get("tags") or []],
        )
        for j in data.get("data") or []
    ]


def remoteok(query: str, client: httpx.Client | None = None, **_: Any) -> list[Job]:
    data = _http.get_json("https://remoteok.com/api", client=client)
    jobs = []
    for j in data if isinstance(data, list) else []:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # the first element is a legal notice
        low, high = j.get("salary_min"), j.get("salary_max")
        jobs.append(
            Job(
                source="remoteok",
                title=_text(j.get("position")),
                company=_text(j.get("company")),
                url=str(j.get("url") or ""),
                location=_text(j.get("location")),
                remote=True,
                description=_text(j.get("description")),
                posted=str(j.get("date") or "")[:10],
                salary=f"${low}-{high}" if low and high else "",
                tags=[str(t) for t in j.get("tags") or []],
            )
        )
    return jobs


# Countries Adzuna serves. Search several with SIDEKICK_COUNTRIES="in,sg,gb,de" or --country.
ADZUNA_COUNTRIES = ("at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb", "in", "it", "mx", "nl", "nz", "pl",
                    "sg", "us", "za")


def adzuna(query: str, client: httpx.Client | None = None, *, countries: tuple[str, ...] = (),
           **_: Any) -> list[Job]:
    app_id, app_key = os.environ.get("ADZUNA_APP_ID", ""), os.environ.get("ADZUNA_APP_KEY", "")
    if not (app_id and app_key):
        return []
    countries = countries or tuple(c.strip().lower() for c in os.environ.get("SIDEKICK_COUNTRIES", "in").split(","))
    unknown = [c for c in countries if c not in ADZUNA_COUNTRIES]
    if unknown:
        raise _http.FetchError(f"Adzuna does not cover: {', '.join(unknown)}")
    params = {"app_id": app_id, "app_key": app_key, "what": query, "results_per_page": 50}
    jobs = []
    for country in countries:
        data = _http.get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params=params, client=client)
        jobs += _adzuna_jobs(data, country)
    return jobs


def _adzuna_jobs(data: dict, country: str) -> list[Job]:
    jobs = []
    for j in data.get("results") or []:
        low, high = j.get("salary_min"), j.get("salary_max")
        jobs.append(
            Job(
                source="adzuna",
                title=_text(j.get("title")),
                company=_text((j.get("company") or {}).get("display_name")),
                url=str(j.get("redirect_url") or ""),
                location=f"{_text((j.get('location') or {}).get('display_name'))} ({country.upper()})",
                description=_text(j.get("description")),
                posted=str(j.get("created") or "")[:10],
                salary=f"{low:.0f}-{high:.0f}" if low and high else "",
            )
        )
    return jobs


def greenhouse(query: str, client: httpx.Client | None = None, *, companies: tuple[str, ...] = (),
               **_: Any) -> list[Job]:
    jobs = []
    for token in companies:
        if not re.fullmatch(r"[\w-]+", token):
            continue
        try:
            data = _http.get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"}, client=client
            )
        except _http.FetchError:
            continue
        for j in data.get("jobs") or []:
            location = _text((j.get("location") or {}).get("name"))
            jobs.append(
                Job(
                    source="greenhouse",
                    title=_text(j.get("title")),
                    company=token,
                    url=str(j.get("absolute_url") or ""),
                    location=location,
                    remote="remote" in location.lower(),
                    # Greenhouse returns the description HTML-escaped once.
                    description=_text(html.unescape(str(j.get("content") or ""))),
                    posted=str(j.get("updated_at") or "")[:10],
                )
            )
    return jobs


def lever(query: str, client: httpx.Client | None = None, *, companies: tuple[str, ...] = (), **_: Any) -> list[Job]:
    jobs = []
    for token in companies:
        if not re.fullmatch(r"[\w-]+", token):
            continue
        try:
            data = _http.get_json(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"}, client=client)
        except _http.FetchError:
            continue
        for j in data if isinstance(data, list) else []:
            categories = j.get("categories") or {}
            location = _text(categories.get("location"))
            jobs.append(
                Job(
                    source="lever",
                    title=_text(j.get("text")),
                    company=token,
                    url=str(j.get("hostedUrl") or ""),
                    location=location,
                    remote="remote" in location.lower() or str(j.get("workplaceType")) == "remote",
                    description=_text(j.get("descriptionPlain") or j.get("description")),
                    tags=[_text(categories.get("team"))] if categories.get("team") else [],
                )
            )
    return jobs


SOURCES: dict[str, Callable[..., list[Job]]] = {
    "remotive": remotive,
    "arbeitnow": arbeitnow,
    "remoteok": remoteok,
    "adzuna": adzuna,
    "greenhouse": greenhouse,
    "lever": lever,
}


def search(
    query: str,
    *,
    sources: tuple[str, ...] = (),
    location: str = "",
    companies: tuple[str, ...] = (),
    countries: tuple[str, ...] = (),
    client: httpx.Client | None = None,
) -> tuple[list[Job], dict[str, str]]:
    """Query every requested source. Returns the de-duplicated jobs and per-source errors."""
    jobs: dict[str, Job] = {}
    errors: dict[str, str] = {}
    for name in sources or tuple(SOURCES):
        if name not in SOURCES:
            errors[name] = "unknown source"
            continue
        try:
            found = SOURCES[name](query, client, location=location, companies=companies, countries=countries)
        except _http.FetchError as exc:  # one broken source must not sink the search
            errors[name] = str(exc)
            continue
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            errors[name] = f"unexpected response ({type(exc).__name__})"
            continue
        for job in found:
            if job.url and job.title:
                jobs.setdefault(job.url, job)
    return list(jobs.values()), errors
