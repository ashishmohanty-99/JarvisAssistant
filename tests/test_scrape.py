from __future__ import annotations

import httpx
import pytest
import respx

from jarvis_sidekick import _http, scrape

PAGE = """<html><head><title>Docs</title><style>p{}</style></head><body>
<nav><a href="/nav">menu</a></nav>
<main><h1>Hello</h1><p>Useful   text.</p><a href="/two#frag">two</a><a href="https://other.example/x">x</a>
<script>track()</script></main><footer>legal</footer></body></html>"""


def test_extract_keeps_main_text_and_absolute_links():
    page = scrape.extract(PAGE, "https://site.example/one")
    assert page.title == "Docs"
    assert page.text == "Hello\nUseful text.\ntwo x"
    assert "track()" not in page.text and "legal" not in page.text
    assert page.links == ["https://site.example/nav", "https://site.example/two", "https://other.example/x"]


def test_extract_survives_garbage():
    assert scrape.extract("", "https://site.example").text == ""


@respx.mock
def test_crawl_stays_on_site_honours_robots_and_caps_pages():
    respx.get("https://site.example/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private")
    )
    respx.get("https://site.example/").mock(
        return_value=httpx.Response(200, text='<a href="/a">a</a><a href="/private">p</a><a href="https://other.example/">o</a>')
    )
    respx.get("https://site.example/a").mock(return_value=httpx.Response(200, text='<p>A</p><a href="/b">b</a>'))
    respx.get("https://site.example/b").mock(return_value=httpx.Response(200, text="<p>B</p>"))
    private = respx.get("https://site.example/private").mock(return_value=httpx.Response(200, text="secret"))
    other = respx.get("https://other.example/").mock(return_value=httpx.Response(200, text="elsewhere"))

    pages = scrape.crawl("https://site.example/", max_pages=2, delay_seconds=0)

    assert [p.url for p in pages] == ["https://site.example/", "https://site.example/a"]
    assert not private.called and not other.called


@respx.mock
def test_fetch_page_refuses_what_robots_disallows():
    respx.get("https://site.example/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /"))
    with pytest.raises(_http.FetchError, match="robots.txt"):
        scrape.fetch_page("https://site.example/x")


@respx.mock
def test_redirect_to_a_private_address_is_refused(monkeypatch):
    monkeypatch.undo()  # use the real SSRF check
    monkeypatch.setattr(_http, "_ssrf_reason", lambda url: "private address" if "169.254" in url else None)
    respx.get("https://site.example/go").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    )
    metadata = respx.get("http://169.254.169.254/latest/meta-data").mock(return_value=httpx.Response(200, text="keys"))
    with pytest.raises(_http.FetchError, match="refused"):
        _http.get("https://site.example/go")
    assert not metadata.called


def test_real_ssrf_check_blocks_loopback_and_file_urls(monkeypatch):
    monkeypatch.undo()
    assert _http._ssrf_reason("http://127.0.0.1:8080/admin")
    assert _http._ssrf_reason("file:///etc/passwd")


@respx.mock
def test_oversized_and_error_responses_raise():
    respx.get("https://site.example/big").mock(return_value=httpx.Response(200, content=b"x" * 50))
    respx.get("https://site.example/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(_http.FetchError, match="larger than"):
        _http.get("https://site.example/big", max_bytes=10)
    with pytest.raises(_http.FetchError, match="HTTP 404"):
        _http.get("https://site.example/missing")
