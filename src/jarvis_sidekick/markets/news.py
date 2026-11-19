"""Market headlines from public RSS feeds."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime

import httpx
from lxml import etree
from lxml import html as lxml_html

from jarvis_sidekick import _http

FEEDS = {
    "india": {
        "Economic Times Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "Mint Markets": "https://www.livemint.com/rss/markets",
    },
    "world": {
        "CNBC Markets": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
        "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    },
}


@dataclass
class Headline:
    title: str
    link: str
    source: str
    published: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _plain(text: str | None, title: str, limit: int = 240) -> str:
    """A feed's description as one plain sentence or two: no markup, no repeat of the title, cut at a word."""
    text = text or ""
    if "<" in text:
        try:
            text = lxml_html.fromstring(f"<div>{text}</div>").text_content()
        except (etree.LxmlError, ValueError):
            text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    if not text or text.lower() == title.lower():
        return ""
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def parse_feed(xml: bytes, source: str) -> list[Headline]:
    """Parse RSS 2.0 or Atom. Entities and network access are disabled in the parser."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)
    try:
        root = etree.fromstring(xml, parser)
    except etree.XMLSyntaxError:
        return []
    if root is None:
        return []
    items = []
    for node in root.iter():
        if not isinstance(node.tag, str) or etree.QName(node).localname not in ("item", "entry"):
            continue
        fields = {etree.QName(c).localname: c for c in node if isinstance(c.tag, str)}
        title = (fields["title"].text or "").strip() if "title" in fields else ""
        link_node = fields.get("link")
        link = ((link_node.text or "").strip() or link_node.get("href", "")) if link_node is not None else ""
        stamp_node = next((fields[k] for k in ("pubDate", "published", "updated") if k in fields), None)
        stamp = (stamp_node.text or "").strip() if stamp_node is not None else ""
        try:
            stamp = parsedate_to_datetime(stamp).isoformat(timespec="minutes") if stamp else ""
        except (TypeError, ValueError):
            pass  # Atom dates are already ISO 8601
        if title and link.startswith(("http://", "https://")):
            blurb = next((fields[k] for k in ("description", "summary", "content") if k in fields), None)
            items.append(Headline(title=title, link=link, source=source, published=stamp,
                                  summary=_plain(blurb.text if blurb is not None else "", title)))
    return items


def headlines(region: str = "india", per_feed: int = 5, client: httpx.Client | None = None) -> list[Headline]:
    found: list[Headline] = []
    seen: set[str] = set()
    for source, url in FEEDS.get(region, {}).items():
        try:
            items = parse_feed(_http.get(url, timeout=15.0, client=client).content, source)
        except _http.FetchError:
            continue
        for item in items[:per_feed]:
            if item.title.lower() not in seen:
                seen.add(item.title.lower())
                found.append(item)
    return found
