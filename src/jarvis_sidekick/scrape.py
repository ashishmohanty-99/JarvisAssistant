"""Readable-text extraction and a small, polite, bounded crawler.

Nothing can scrape "the whole internet". What works is: search for what you need
(OpenJarvis `web_search`), then read the pages that matter. This module does the
reading: one page, or a bounded same-site crawl that honours robots.txt.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from lxml import etree
from lxml import html as lxml_html

from jarvis_sidekick import _http

_BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "blockquote", "pre", "dd", "dt",
    "h1", "h2", "h3", "h4", "h5", "h6",
}
_DROP = ("script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe")
MAX_PAGES = 50


@dataclass
class Page:
    url: str
    title: str
    text: str
    links: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def text_of(node) -> str:
    """Visible text of an element: one line per block, single spaces within a line."""
    pieces: list[str] = []

    def walk(element) -> None:
        separator = "\n" if element.tag in _BLOCK else " "
        pieces.append(separator + (element.text or ""))
        for child in element:
            if isinstance(child.tag, str):
                walk(child)
            pieces.append(separator if not isinstance(child.tag, str) else "")
            pieces.append(child.tail or "")
        pieces.append(separator)

    walk(node)
    return "".join(pieces)


def tidy(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def extract(html_text: str, base_url: str = "", max_chars: int = 20_000) -> Page:
    """Turn HTML into a title, readable text and absolute links."""
    try:
        tree = lxml_html.fromstring(html_text)
    except (etree.LxmlError, ValueError):
        return Page(url=base_url, title="", text="", links=[])
    title = (tree.findtext(".//title") or "").strip()
    links: list[str] = []
    for href in tree.xpath("//a/@href"):
        absolute = urldefrag(urljoin(base_url, href.strip()))[0]
        if absolute.startswith(("http://", "https://")) and absolute not in links:
            links.append(absolute)
    for node in tree.xpath("|".join(f"//{tag}" for tag in _DROP)):
        node.drop_tree()
    main = tree.xpath("//main|//article") or [tree]
    text = tidy(text_of(main[0]))
    return Page(url=base_url, title=title, text=text[:max_chars], links=links)


def fetch_page(url: str, *, max_chars: int = 20_000, client: httpx.Client | None = None) -> Page:
    if not _http.allowed_by_robots(url, client=client):
        raise _http.FetchError(f"robots.txt for {urlsplit(url).hostname} disallows this page")
    response = _http.get(url, client=client)
    return extract(response.text, str(response.url), max_chars)


def crawl(
    start_url: str,
    *,
    max_pages: int = 10,
    delay_seconds: float = 1.0,
    max_chars: int = 8_000,
    client: httpx.Client | None = None,
) -> list[Page]:
    """Breadth-first crawl that stays on the starting host, capped at MAX_PAGES."""
    max_pages = max(1, min(max_pages, MAX_PAGES))
    host = urlsplit(start_url).netloc
    queue, seen, pages = deque([start_url]), {start_url}, []
    while queue and len(pages) < max_pages:
        url = queue.popleft()
        try:
            page = fetch_page(url, max_chars=max_chars, client=client)
        except _http.FetchError:
            continue
        pages.append(page)
        for link in page.links:
            if urlsplit(link).netloc == host and link not in seen:
                seen.add(link)
                queue.append(link)
        if queue and delay_seconds:
            time.sleep(delay_seconds)
    return pages
