"""A rule-based monthly investment plan for an India-based investor.

General education, not personalised advice: it knows nothing about your debts,
taxes, dependants or existing holdings. Returns are assumptions, not promises.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# Share of the monthly amount per bucket.
ALLOCATIONS = {
    "conservative": {"india_large_cap_index": 25, "international_index": 5, "mid_small_cap": 0, "debt": 55,
                     "gold": 10, "direct_stocks": 5},
    "moderate": {"india_large_cap_index": 35, "international_index": 10, "mid_small_cap": 10, "debt": 30,
                 "gold": 10, "direct_stocks": 5},
    "aggressive": {"india_large_cap_index": 35, "international_index": 15, "mid_small_cap": 20, "debt": 10,
                   "gold": 10, "direct_stocks": 10},
}
VEHICLES = {
    "india_large_cap_index": "NIFTY 50 / NIFTY Next 50 index fund or ETF",
    "international_index": "S&P 500 or Nasdaq 100 index fund / ETF",
    "mid_small_cap": "NIFTY Midcap 150 or flexi-cap fund",
    "debt": "PPF/EPF/VPF, then short-duration or liquid debt funds",
    "gold": "Gold ETF or sovereign gold bonds where available",
    "direct_stocks": "Individual stocks you have researched (start from the screen)",
}
# Assumed long-run annual returns per bucket (pessimistic, base, optimistic), in percent.
ASSUMED_RETURNS = {
    "india_large_cap_index": (7, 11, 13),
    "international_index": (6, 10, 12),
    "mid_small_cap": (6, 13, 16),
    "debt": (5, 7, 8),
    "gold": (4, 8, 10),
    "direct_stocks": (0, 11, 16),
}


@dataclass
class Plan:
    monthly: float
    years: int
    risk: str
    allocation: list[dict]
    invested: float
    projected: dict[str, float]
    blended_return_pct: dict[str, float]
    steps: list[str]
    caveats: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def future_value(monthly: float, annual_pct: float, years: int) -> float:
    """Value of a monthly SIP paid at the start of each month."""
    rate, months = annual_pct / 100 / 12, years * 12
    if rate == 0:
        return monthly * months
    return monthly * ((1 + rate) ** months - 1) / rate * (1 + rate)


def build(monthly: float, years: int, risk: str = "moderate") -> Plan:
    if monthly <= 0 or not 1 <= years <= 50:
        raise ValueError("monthly must be positive and years between 1 and 50")
    risk = risk.lower()
    if risk not in ALLOCATIONS:
        raise ValueError(f"risk must be one of {', '.join(ALLOCATIONS)}")
    weights = dict(ALLOCATIONS[risk])
    if years < 3:  # money needed soon should not sit in equity
        weights = {"india_large_cap_index": 10, "international_index": 0, "mid_small_cap": 0, "debt": 80,
                   "gold": 10, "direct_stocks": 0}
    allocation = [
        {"bucket": k, "percent": v, "monthly": round(monthly * v / 100), "where": VEHICLES[k]}
        for k, v in weights.items() if v
    ]
    blended = {
        label: round(sum(weights[k] * ASSUMED_RETURNS[k][i] for k in weights) / 100, 1)
        for i, label in enumerate(("pessimistic", "base", "optimistic"))
    }
    return Plan(
        monthly=monthly,
        years=years,
        risk=risk,
        allocation=allocation,
        invested=monthly * years * 12,
        projected={label: round(future_value(monthly, pct, years)) for label, pct in blended.items()},
        blended_return_pct=blended,
        steps=[
            "First: clear high-interest debt and hold 6 months of expenses in a savings account or liquid fund.",
            "Buy term life and health insurance before investing if anyone depends on your income.",
            "Automate the monthly SIPs on salary day; raise the amount 10% each year.",
            "Rebalance once a year, or when any bucket drifts more than 5 points from its target.",
            "Cap direct stocks at the percentage shown and at most 5% of the portfolio in one company.",
            "Stay invested through falls: the equity buckets need a horizon of 5+ years.",
        ],
        caveats=[
            "Projections use assumed returns; real results will differ and can be negative over several years.",
            "Tax rules and limits change; confirm with a SEBI-registered adviser or a chartered accountant.",
        ],
    )
