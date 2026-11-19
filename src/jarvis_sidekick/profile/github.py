"""What a public GitHub profile says about someone's skills."""

from __future__ import annotations

import re
from collections import Counter

import httpx

from jarvis_sidekick import _http


def summary(user: str, client: httpx.Client | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", user):
        raise ValueError("not a valid GitHub user name")
    repos = _http.get_json(
        f"https://api.github.com/users/{user}/repos",
        params={"per_page": 100, "sort": "pushed", "type": "owner"},
        headers={"Accept": "application/vnd.github+json"},
        client=client,
    )
    repos = [r for r in repos if isinstance(r, dict) and not r.get("fork")]
    languages = Counter(r["language"] for r in repos if r.get("language"))
    topics = Counter(t for r in repos for t in r.get("topics") or [])
    return {
        "user": user,
        "public_repos": len(repos),
        "stars": sum(int(r.get("stargazers_count") or 0) for r in repos),
        "languages": dict(languages.most_common(8)),
        "topics": [t for t, _ in topics.most_common(15)],
        "repos": [
            {
                "name": r.get("name"),
                "description": r.get("description") or "",
                "language": r.get("language") or "",
                "stars": r.get("stargazers_count") or 0,
                "pushed": str(r.get("pushed_at") or "")[:10],
                "url": r.get("html_url"),
            }
            for r in repos[:15]
        ],
    }
