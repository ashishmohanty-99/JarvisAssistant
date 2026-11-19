"""Recent uploads of a YouTube channel (public feed) and, optionally, transcripts."""

from __future__ import annotations

import re

import httpx
from lxml import etree

from jarvis_sidekick import _http

_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "m": "http://search.yahoo.com/mrss/",
}


def parse_feed(xml: bytes) -> dict:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    root = etree.fromstring(xml, parser)
    if root is None:
        return {"channel": "", "videos": []}
    videos = []
    for entry in root.findall("a:entry", _NS):
        stats = entry.find("m:group/m:community/m:statistics", _NS)
        link = entry.find("a:link", _NS)
        videos.append(
            {
                "id": entry.findtext("yt:videoId", "", _NS),
                "title": entry.findtext("a:title", "", _NS),
                "published": entry.findtext("a:published", "", _NS)[:10],
                "url": link.get("href", "") if link is not None else "",
                "views": int(stats.get("views", 0)) if stats is not None else 0,
                "description": (entry.findtext("m:group/m:description", "", _NS) or "")[:500],
            }
        )
    return {"channel": root.findtext("a:title", "", _NS), "videos": videos}


def channel(channel_id: str, client: httpx.Client | None = None) -> dict:
    """The 15 most recent uploads. *channel_id* is the ``UC...`` id from the channel URL."""
    if not re.fullmatch(r"UC[\w-]{22}", channel_id):
        raise ValueError("expected a channel id like UCxxxxxxxxxxxxxxxxxxxxxx (see the channel page URL)")
    response = _http.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": channel_id}, client=client)
    return parse_feed(response.content)


def report(result: dict) -> str:
    """Plain-text summary of a channel feed. The public feed has views per video but no subscriber count."""
    videos = result["videos"]
    if not videos:
        return f"{result['channel'] or 'Channel'}: no public videos in the feed."
    lines = [f"{result['channel']}: {len(videos)} recent videos, {sum(v['views'] for v in videos):,} views in total"]
    lines += [f"{v['published']}  {v['views']:>7,} views  {v['title']}\n            {v['url']}" for v in videos]
    best = max(videos, key=lambda v: v["views"])
    lines.append(f"Most watched: {best['title']} ({best['views']:,} views)")
    return "\n".join(lines)


def transcript(video_id: str, max_chars: int = 12_000) -> str:
    """Captions of one video. Needs: pip install 'jarvis-sidekick[youtube]'."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise RuntimeError("Transcripts need: pip install 'jarvis-sidekick[youtube]'") from None
    fetched = YouTubeTranscriptApi().fetch(video_id)
    return " ".join(snippet.text for snippet in fetched)[:max_chars]
