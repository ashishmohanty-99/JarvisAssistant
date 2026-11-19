"""A concrete, rule-based suggestion for a sum of money: what to buy, how many units, and why.

No language model: the split comes from fixed percentages per risk level, the
stock ideas from the momentum screen, the quantities from the latest prices.
It is a starting point for your own research, not personalised advice. It
knows nothing about your debts, taxes, existing holdings or goals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jarvis_sidekick import _http
from jarvis_sidekick.markets import data, indicators

# Percent of the amount per bucket. Stock ideas stay the minority at every level.
SPLITS = {
    "conservative": {"large": 30, "next": 0, "world": 5, "gold": 15, "debt": 40, "stocks": 10},
    "moderate": {"large": 35, "next": 10, "world": 10, "gold": 10, "debt": 15, "stocks": 20},
    "aggressive": {"large": 30, "next": 15, "world": 15, "gold": 5, "debt": 5, "stocks": 30},
}
# Exchange-traded funds on the NSE, bought like a share through any broker.
FUNDS = {
    "large": ("NIFTYBEES.NS", "NIFTY 50 index ETF", "the 50 largest Indian companies in one holding"),
    "next": ("JUNIORBEES.NS", "NIFTY Next 50 index ETF", "the next 50, more growth and more swing"),
    "world": ("MON100.NS", "Nasdaq 100 ETF", "US technology leaders, and a hedge against the rupee"),
    "gold": ("GOLDBEES.NS", "Gold ETF", "tends to hold up when shares fall"),
}
DEBT = "a liquid or short-duration debt fund, or PPF"
CAVEATS = (
    ("The stock ideas come from a momentum screen: it ranks how prices have behaved, not what companies are worth "
     "or what happens next. Read about each one before buying."),
    ("Shares can fall a third or more and stay down for years. Only money you will not need for five years or more "
     "belongs in the share and ETF lines."),
    "This is general information, not advice for your situation. Nothing here promises a return.",
)
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?|l)?\b", re.IGNORECASE)
_SCALE = {"k": 1_000, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
          "l": 100_000}


def parse_amount(text: str) -> float | None:
    """Rupees from '25000', '25,000', '25k', '25 thousand', '1.5 lakh'. For a range, the first figure."""
    found = _AMOUNT.search(text.replace("₹", " ").replace("rs.", " ").replace("rs", " "))
    if not found:
        return None
    value = float(found.group(1).replace(",", "")) * _SCALE.get((found.group(2) or "").lower(), 1)
    # "25-30K": the unit sits on the second number, so borrow it for the first.
    ranged = re.search(r"\d\s*(?:-|to)\s*\d[\d,]*\s*(k|thousand|lakhs?|lacs?)\b", text, re.IGNORECASE)
    if ranged and not found.group(2):
        value *= _SCALE[ranged.group(1).lower()]
    return value if 500 <= value <= 100_000_000 else None


@dataclass
class Line:
    label: str
    amount: float
    symbol: str = ""
    price: float | None = None
    units: int = 0
    why: str = ""

    @property
    def spent(self) -> float:
        return self.units * self.price if self.price else self.amount


@dataclass
class Suggestion:
    amount: float
    risk: str
    monthly: bool
    lines: list[Line] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    @property
    def left_over(self) -> float:
        return round(self.amount - sum(line.spent for line in self.lines if line.units or not line.symbol), 2)


DEFAULT_PROFILE = {"amount": 25_000.0, "risk": "moderate", "monthly": False}


def _profile_path():
    from jarvis_sidekick import paths

    return paths.home() / "invest-profile.json"


def save_profile(amount: float, risk: str, monthly: bool) -> None:
    """Remember the last answers given to /invest, so the PDF edition can repeat the suggestion daily."""
    import json

    path = _profile_path()
    path.write_text(json.dumps({"amount": amount, "risk": risk, "monthly": monthly}), encoding="utf-8")
    path.chmod(0o600)


def load_profile() -> tuple[dict, bool]:
    """The saved answers and True, or the default (Rs 25,000, moderate, one time) and False."""
    import json

    try:
        saved = json.loads(_profile_path().read_text(encoding="utf-8"))
        if float(saved["amount"]) >= 500 and saved["risk"] in SPLITS and isinstance(saved["monthly"], bool):
            return {"amount": float(saved["amount"]), "risk": saved["risk"], "monthly": saved["monthly"]}, True
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return dict(DEFAULT_PROFILE), False


def _price(symbol: str) -> float | None:
    try:
        return data.history(symbol).last
    except _http.FetchError:
        return None


def _stock_ideas(count: int) -> list[indicators.Snapshot]:
    """Top of the India screen, keeping only steady uptrends that are not overbought."""
    series = []
    for symbol in data.WATCHLISTS["india"]:
        try:
            series.append(data.history(symbol))
        except _http.FetchError:
            continue
    ranked = indicators.screen(series, top=len(series))
    good = [s for s in ranked if s.score > 0 and s.above_200dma and (s.rsi is None or s.rsi < 75)]
    return good[:count]


def suggest(amount: float, risk: str = "moderate", *, monthly: bool = False) -> Suggestion:
    risk = risk.lower()
    if risk not in SPLITS:
        raise ValueError(f"risk must be one of {', '.join(SPLITS)}")
    if amount < 500:
        raise ValueError("the amount is too small to split")
    result = Suggestion(amount=amount, risk=risk, monthly=monthly)
    split = SPLITS[risk]

    for bucket, (symbol, name, why) in FUNDS.items():
        slice_ = amount * split[bucket] / 100
        if not slice_:
            continue
        price = _price(symbol)
        if price is None:
            result.unavailable.append(symbol)
        result.lines.append(Line(name, slice_, symbol, price, int(slice_ // price) if price else 0, why))

    if split["debt"]:
        result.lines.append(Line(DEBT, amount * split["debt"] / 100, why="the part that should not swing"))

    stock_money = amount * split["stocks"] / 100
    ideas = _stock_ideas(3 if stock_money >= 4500 else 2)
    if not ideas:
        result.unavailable.append("stock screen")
    for idea in ideas:
        slice_ = stock_money / len(ideas)
        why = f"up {idea.quarter_pct:.0f}% in 3 months and {idea.half_year_pct:.0f}% in 6, {idea.note}"
        result.lines.append(Line(idea.symbol.removesuffix(".NS"), slice_, idea.symbol, idea.price,
                                 int(slice_ // idea.price), why))
    return result


def to_text(s: Suggestion, title: str = "") -> str:
    when = "each month" if s.monthly else "once"
    out = [f"Rs {s.amount:,.0f} {when}, {s.risk} risk{', ' + title if title else ''}:", ""]
    for line in s.lines:
        share = round(100 * line.amount / s.amount)
        if not line.symbol:
            out.append(f"{share}%  Rs {line.amount:,.0f}  into {line.label} ({line.why})")
        elif line.price is None:
            out.append(f"{share}%  Rs {line.amount:,.0f}  {line.label} [{line.symbol}]: price unavailable right now")
        elif line.units == 0:
            out.append(f"{share}%  Rs {line.amount:,.0f}  {line.label} [{line.symbol}]: one unit costs "
                       f"Rs {line.price:,.0f}, more than this slice. Skip it or move the money to the NIFTY 50 ETF.")
        else:
            out.append(f"{share}%  {line.units} x {line.label} [{line.symbol}] at about Rs {line.price:,.2f} "
                       f"= Rs {line.spent:,.0f} ({line.why})")
    if s.left_over >= 1:
        out += ["", f"Left over after whole units: Rs {s.left_over:,.0f}. Add it to the NIFTY 50 ETF or keep it as cash."]
    if s.unavailable:
        out += ["", f"Could not fetch: {', '.join(s.unavailable)}."]
    out += ["", *CAVEATS]
    return "\n".join(out)


def to_speech(s: Suggestion, title: str = "") -> str:
    funds = [line for line in s.lines if line.symbol in {f[0] for f in FUNDS.values()} and line.units]
    stocks = [line for line in s.lines if line.symbol and line.symbol not in {f[0] for f in FUNDS.values()}]
    parts = [f"For {s.amount:,.0f} rupees at {s.risk} risk{', ' + title if title else ''}:"]
    if funds:
        parts.append("most of it goes into index funds, " + ", ".join(line.label for line in funds) + ".")
    debt = next((line for line in s.lines if not line.symbol), None)
    if debt:
        parts.append(f"About {debt.amount:,.0f} goes into a debt fund.")
    if stocks:
        parts.append("The stock ideas from the screen are " + ", ".join(line.label for line in stocks) + ".")
    parts.append("The exact quantities are in the message. These are ideas to research, not a promise of profit.")
    return " ".join(parts)
