"""The briefing as a designed, printable page: a small morning paper in PDF.

Layout is HTML and CSS, turned into a PDF by the Chromium that is already
installed for application forms. Everything that comes from outside (headlines,
summaries, source names, links) is escaped before it reaches the page, the page
runs with JavaScript off, and it is allowed no network requests.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from jarvis_sidekick import paths

# Ink and surfaces. Colour is used for two things only: the accent on the newest point of a trend line,
# and the direction mark beside a change. Text is always ink.
INK, SECOND, MUTED, HAIR, SURFACE, PLANE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb", "#f4f3ee"
ACCENT, UP, DOWN = "#2a78d6", "#0ca30c", "#d03b3b"
EQUITY = {"NIFTY 50", "SENSEX", "NIFTY Bank", "S&P 500", "Nasdaq", "Dow Jones", "FTSE 100", "Nikkei 225", "Hang Seng"}

CSS = f"""
@page {{ size: A4; margin: 13mm 13mm 15mm; }}
* {{ box-sizing: border-box; }}
html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin: 0; color: {INK}; background: {SURFACE};
       font: 9.4pt/1.42 "Inter", "Liberation Sans", "DejaVu Sans", Arial, sans-serif; }}
a {{ color: inherit; text-decoration: none; }}
.serif {{ font-family: "Noto Serif", "Liberation Serif", "DejaVu Serif", Georgia, serif; }}
.kicker {{ font-size: 7pt; letter-spacing: .14em; text-transform: uppercase; color: {MUTED}; font-weight: 600; }}

header {{ border-bottom: 2.2pt solid {INK}; padding-bottom: 7pt; margin-bottom: 3pt; }}
header .row {{ display: flex; justify-content: space-between; align-items: flex-end; }}
header h1 {{ font-size: 33pt; line-height: 1; margin: 2pt 0 0; font-weight: 700; letter-spacing: -.02em; }}
header .date {{ text-align: right; font-size: 8.4pt; color: {SECOND}; line-height: 1.35; }}
.rule {{ border-top: .6pt solid {INK}; margin-bottom: 9pt; }}

.glance {{ font-size: 13.2pt; line-height: 1.34; margin: 0 0 11pt; max-width: 165mm; }}

h2 {{ font-size: 7.4pt; letter-spacing: .14em; text-transform: uppercase; font-weight: 700; margin: 0 0 6pt;
      padding-top: 5pt; border-top: .6pt solid {INK}; }}
section {{ margin-bottom: 12pt; break-inside: avoid; }}

.tiles {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6pt; }}
.tiles.three {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
.tile {{ background: {PLANE}; border-radius: 5pt; padding: 7pt 8pt 6pt; }}
.tile .label {{ font-size: 7.4pt; color: {SECOND}; }}
.tile .value {{ font-size: 14.5pt; font-weight: 600; letter-spacing: -.01em; line-height: 1.25;
               font-variant-numeric: tabular-nums; }}
.tile .foot {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 4pt; }}
.delta {{ font-size: 8pt; white-space: nowrap; font-variant-numeric: tabular-nums; }}
.delta small {{ color: {MUTED}; font-size: 7pt; }}
.mark {{ font-size: 7pt; margin-right: 2pt; }}
.spark {{ width: 62pt; height: 20pt; flex: 0 1 62pt; min-width: 0; }}

.news {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0 16pt; }}
.news > div + div {{ border-left: .6pt solid {HAIR}; padding-left: 16pt; }}
article {{ padding: 6pt 0 7pt; border-bottom: .6pt solid {HAIR}; break-inside: avoid; }}
article:last-child {{ border-bottom: 0; }}
article h3 {{ font-size: 11.4pt; line-height: 1.24; margin: 2pt 0 2pt; font-weight: 700; }}
article.lead h3 {{ font-size: 16pt; line-height: 1.18; }}
article p {{ margin: 0; color: {SECOND}; font-size: 8.8pt; }}

.cols {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0 32pt; }}
table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
th {{ font-size: 7pt; letter-spacing: .08em; text-transform: uppercase; color: {MUTED}; font-weight: 600;
      text-align: right; padding: 0 0 3pt; border-bottom: .6pt solid {HAIR}; }}
td {{ padding: 3.4pt 0; border-bottom: .6pt solid {HAIR}; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
td.note {{ color: {SECOND}; font-size: 8pt; text-align: left; padding-left: 8pt; }}
tr:last-child td {{ border-bottom: 0; }}
.name {{ font-weight: 600; }}

footer {{ margin-top: 4pt; padding-top: 6pt; border-top: .6pt solid {INK}; color: {MUTED}; font-size: 7.2pt;
          line-height: 1.4; }}
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _pct(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{abs(value):.{digits}f}%"


def _mark(value: float | None, coloured: bool = True) -> str:
    """An up or down triangle. The shape carries the direction; colour only repeats it."""
    if value is None or value == 0:
        return f'<span class="mark" style="color:{MUTED}">●</span>'
    colour = (UP if value > 0 else DOWN) if coloured else SECOND
    return f'<span class="mark" style="color:{colour}">{"▲" if value > 0 else "▼"}</span>'


def _number(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}"


def sparkline(values: list[float], width: float = 124, height: float = 40) -> str:
    """A trend line with no axes: grey line, the latest point marked in the accent colour."""
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    pad = 4.0
    step = (width - 2 * pad) / (len(values) - 1)
    points = [(pad + i * step, pad + (height - 2 * pad) * (1 - (v - low) / span)) for i, v in enumerate(values)]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    x, y = points[-1]
    return (f'<svg class="spark" viewBox="0 0 {width:g} {height:g}" role="img" aria-label="trend, last 60 sessions">'
            f'<polyline points="{path}" fill="none" stroke="{MUTED}" stroke-width="2" stroke-linejoin="round" '
            f'stroke-linecap="round"/><circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{ACCENT}" stroke="{PLANE}" '
            f'stroke-width="2"/></svg>')


def _tile(row: dict) -> str:
    coloured = row["name"] in EQUITY  # for a currency, a yield or a fear index, "up" is not good news
    return (f'<div class="tile"><div class="label">{_e(row["name"])}</div>'
            f'<div class="value">{_number(row["price"])}</div><div class="foot">'
            f'<div class="delta">{_mark(row["day_pct"], coloured)}{_pct(row["day_pct"])} <small>day</small></div>'
            f'{sparkline(row.get("spark") or [])}</div></div>')


def _ago(published: str, now: datetime) -> str:
    try:
        then = datetime.fromisoformat(published)
        if then.tzinfo is None:
            return ""
        hours = (now.astimezone() - then).total_seconds() / 3600
    except (ValueError, TypeError):
        return ""
    if hours < 0 or hours > 24 * 14:
        return ""
    if hours < 1:
        return "just now"
    return f"{hours:.0f}h ago" if hours < 24 else f"{hours / 24:.0f}d ago"


def _article(item: dict, now: datetime, lead: bool = False) -> str:
    when = _ago(item.get("published", ""), now)
    kicker = _e(item["source"]) + (f" · {when}" if when else "")
    link = item["link"] if str(item["link"]).startswith(("http://", "https://")) else "#"
    summary = f'<p>{_e(item["summary"])}</p>' if item.get("summary") else ""
    return (f'<article{" class=lead" if lead else ""}><div class="kicker">{kicker}</div>'
            f'<h3 class="serif"><a href="{_e(link)}">{_e(item["title"])}</a></h3>{summary}</article>')


def _glance(brief: dict) -> str:
    rows = {r["name"]: r for r in brief["indices"]}
    parts = []
    for name in ("NIFTY 50", "S&P 500"):
        row = rows.get(name)
        if row and row["day_pct"] is not None:
            verb = "rose" if row["day_pct"] > 0 else "fell" if row["day_pct"] < 0 else "was flat at"
            change = "" if row["day_pct"] == 0 else f" {abs(row['day_pct']):.1f}% to"
            parts.append(f"the {name} {verb}{change} {_number(row['price'])}")
    rupee = rows.get("USD/INR")
    if rupee:
        parts.append(f"a dollar costs {rupee['price']:.2f} rupees")
    if not parts:
        return "Market data was not available for this edition; the headlines below are current."
    sentence = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1]
    return sentence[0].upper() + sentence[1:] + "."


def _movers(rows: list[dict]) -> str:
    body = "".join(f'<tr><td class="name">{_e(r["symbol"].removesuffix(".NS"))}</td><td>{_number(r["price"])}</td>'
                   f'<td>{_mark(r["day_pct"])}{_pct(r["day_pct"])}</td></tr>' for r in rows)
    return f"<table><tr><th>Stock</th><th>Price</th><th>Day</th></tr>{body}</table>"


def _screen(rows: list[dict]) -> str:
    body = "".join(
        f'<tr><td class="name">{_e(r["symbol"].removesuffix(".NS"))}</td><td>{_number(r["price"])}</td>'
        f'<td>{_mark(r["quarter_pct"])}{_pct(r["quarter_pct"], 1)}</td>'
        f'<td>{_mark(r["half_year_pct"])}{_pct(r["half_year_pct"], 1)}</td><td>{r["rsi"]}</td>'
        f'<td class="note">{_e(r["note"])}</td></tr>' for r in rows)
    return ("<table><tr><th>Stock</th><th>Price</th><th>3 months</th><th>6 months</th><th>RSI</th>"
            f'<th style="text-align:left;padding-left:8pt">Reading</th></tr>{body}</table>')


def build_html(brief: dict, *, now: datetime, title: str = "", extras: list[tuple[str, str]] | None = None) -> str:
    """The whole page as one self-contained HTML string."""
    groups: dict[str, list[dict]] = {}
    for row in brief["indices"]:
        groups.setdefault(row["group"], []).append(row)
    sections = []
    for group, heading in (("India", "Indian markets"), ("World", "World markets"), ("Macro", "Currency and commodities")):
        if groups.get(group):
            tiles = "".join(_tile(r) for r in groups[group])
            # Six tiles sit better as two rows of three than as four and two.
            grid = "tiles three" if len(groups[group]) % 4 and len(groups[group]) % 3 == 0 else "tiles"
            sections.append(f'<section><h2>{heading}</h2><div class="{grid}">{tiles}</div></section>')

    columns = []
    for region, heading in (("india", "India"), ("world", "World")):
        items = brief["news"].get(region) or []
        if items:
            stories = "".join(_article(item, now, lead=i == 0) for i, item in enumerate(items[:6]))
            columns.append(f"<div><h2>{heading}</h2>{stories}</div>")
    if columns:
        sections.append(f'<section style="break-inside:auto"><div class="news">{"".join(columns)}</div></section>')

    movers = (brief["movers"].get("india") or {})
    if movers.get("gainers"):
        sections.append('<section><div class="cols">'
                        f'<div><h2>Biggest rises, India</h2>{_movers(movers["gainers"])}</div>'
                        f'<div><h2>Biggest falls, India</h2>{_movers(movers["losers"])}</div></div></section>')
    screen = brief["screens"].get("india") or []
    if screen:
        sections.append(f"<section><h2>Momentum screen: names to research</h2>{_screen(screen)}</section>")
    if extras:
        tiles = "".join(f'<div class="tile"><div class="label">{_e(label)}</div>'
                        f'<div class="value">{_e(value)}</div></div>' for label, value in extras)
        sections.append(f'<section><h2>Your numbers</h2><div class="tiles">{tiles}</div></section>')

    missing = f" Not available in this edition: {_e(', '.join(brief['unavailable']))}." if brief["unavailable"] else ""
    reader = f"Prepared for {_e(title)}" if title else "Morning edition"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>The Jarvis Brief</title>
<style>{CSS}</style></head><body>
<header><div class="row"><div><div class="kicker">{reader}</div><h1 class="serif">The Jarvis Brief</h1></div>
<div class="date">{now:%A}<br>{now:%-d %B %Y}<br>{now:%H:%M} {_e(now.tzname() or "")}</div></div></header>
<div class="rule"></div>
<p class="glance serif">{_e(_glance(brief))}</p>
{"".join(sections)}
<footer>Each trend line shows the last 60 sessions, with the latest marked. Triangles show direction, up or down.
Prices come from an unofficial feed.{missing} {_e(brief["disclaimer"])}</footer>
</body></html>"""


def render_pdf(page_html: str, target: Path | None = None) -> Path:
    """Print *page_html* to an A4 PDF with Chromium. No scripts run and nothing is fetched."""
    import os

    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        raise RuntimeError("The PDF needs the browser: pip install 'jarvis-sidekick[apply]' and "
                           "playwright install chromium") from None
    target = target or paths.home() / "briefings" / "jarvis-brief.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    launch = {"executable_path": os.environ["SIDEKICK_CHROMIUM"]} if os.environ.get("SIDEKICK_CHROMIUM") else {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch)
            try:
                context = browser.new_context(java_script_enabled=False)
                context.route("**/*", lambda route: route.abort())
                page = context.new_page()
                page.set_content(page_html, wait_until="load")
                page.pdf(path=str(target), format="A4", print_background=True, prefer_css_page_size=True)
            finally:
                browser.close()
    except Error as exc:
        raise RuntimeError(f"the browser could not produce the PDF ({type(exc).__name__})") from None
    return target
