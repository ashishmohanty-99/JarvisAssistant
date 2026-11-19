"""Shared outbound HTTP: one user agent, SSRF checks on every hop, size caps, robots.txt."""

from __future__ import annotations

import ipaddress
import socket
import urllib.robotparser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "jarvis-sidekick/0.1 (personal assistant; honours robots.txt)"
MAX_BYTES = 5_000_000
_MAX_REDIRECTS = 5
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


class FetchError(RuntimeError):
    """A fetch failed or was refused. The message never contains credentials."""


def _ssrf_reason(url: str) -> str | None:
    """Return why *url* must not be fetched, or None when it is a public http(s) address."""
    try:
        from openjarvis.security.ssrf import check_ssrf

        return check_ssrf(url)
    except ImportError:
        pass
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "only http and https URLs are allowed"
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return "host could not be resolved"
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            return "host resolves to a non-public address"
    return None


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_bytes: int = MAX_BYTES,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """GET *url*, checking every redirect hop and refusing oversized bodies."""
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=False)
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            reason = _ssrf_reason(url)
            if reason:
                raise FetchError(f"refused {urlsplit(url).hostname or 'URL'}: {reason}")
            try:
                response = client.get(url, params=params, headers={"User-Agent": USER_AGENT, **(headers or {})})
            except httpx.HTTPError as exc:
                raise FetchError(f"request to {urlsplit(url).hostname} failed ({type(exc).__name__})") from None
            if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
                url, params = urljoin(str(response.url), response.headers["location"]), None
                continue
            if response.status_code >= 400:
                raise FetchError(f"{urlsplit(url).hostname} answered HTTP {response.status_code}")
            if len(response.content) > max_bytes:
                raise FetchError(f"response from {urlsplit(url).hostname} is larger than {max_bytes} bytes")
            return response
        raise FetchError("too many redirects")
    finally:
        if own:
            client.close()


def get_json(url: str, **kwargs: Any) -> Any:
    try:
        return get(url, **kwargs).json()
    except ValueError:
        raise FetchError(f"{urlsplit(url).hostname} did not return JSON") from None


def allowed_by_robots(url: str, *, client: httpx.Client | None = None) -> bool:
    """True when the site's robots.txt lets this agent fetch *url* (missing robots.txt allows)."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots:
        parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
        try:
            parser.parse(get(f"{origin}/robots.txt", timeout=10.0, max_bytes=500_000, client=client).text.splitlines())
        except FetchError:
            parser = None
        _robots[origin] = parser
    parser = _robots[origin]
    return parser is None or parser.can_fetch(USER_AGENT, url)
