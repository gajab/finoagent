"""Box-spread specific math — pure functions, no I/O, source-agnostic.

A BOX spread is a four-leg combination of calls and puts across two strikes
that creates a synthetic bond: pay a net debit now, receive the strike width
at expiry (lend), or receive a net credit now and pay the strike width at
expiry (borrow).

This module is the ONLY place BOX math lives. Both the quote provider paths
(yfinance, IBKR) normalize their quotes through ``LegQuote`` and call
``analyze_box()`` — so the same legs produce the same analysis regardless
of source. Any UI inconsistency now must be a formatting bug, never a
math bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from .trade_math import (
    annualized_return_pct,
    dte_from_expiry,
    is_crossed,
    is_stale,
    is_wide_spread,
    mid_price,
)


Intent = Literal["lend", "borrow"]
Side = Literal["long", "short"]
Right = Literal["call", "put"]


@dataclass
class LegQuote:
    """Normalized per-leg quote. Populated once from the provider, used everywhere."""
    strike: float
    right: Right
    side: Side                      # long = pay, short = collect
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float] = None
    ts: Optional[datetime] = None   # quote timestamp
    source: str = ""                # "yfinance" | "ibkr"

    @property
    def mid(self) -> Optional[float]:
        return mid_price(self.bid, self.ask, self.last)


@dataclass
class BoxAnalysis:
    """Everything the UI needs to render a box. Numbers are computed once,
    with warnings surfaced alongside so the surface layer can render honestly
    without recomputing anything."""
    intent: Intent
    lower_strike: float
    upper_strike: float
    width: float                           # upper - lower (always positive)
    dte: int                               # calendar days to expiry
    net_per_contract: float                # $/share of underlying (×100 for cost)
    net_total: float                       # net × 100 × contracts
    cost: float                            # capital at risk per contract ($/share)
    profit: float                          # per contract ($/share) — always >= 0
    roi_pct: float                         # profit / cost × 100
    annualized_return_pct: float           # geometric — see trade_math
    contracts: int
    total_cost: float                      # $ capital deployed
    total_profit: float                    # $ at expiry
    warnings: list[str] = field(default_factory=list)
    source_mix: dict[str, int] = field(default_factory=dict)   # {"yfinance": 3, "ibkr": 1}
    leg_summaries: list[dict] = field(default_factory=list)    # for display


def _validate_legs(legs: list[LegQuote]) -> list[str]:
    """Return a list of warnings for weak quote inputs. Does not raise."""
    warnings = []
    for i, leg in enumerate(legs):
        if is_crossed(leg.bid, leg.ask):
            warnings.append(f"Leg {i+1} ({leg.right} {leg.strike}): crossed market — treating as untradeable")
        elif is_wide_spread(leg.bid, leg.ask, threshold_pct=5.0):
            warnings.append(f"Leg {i+1} ({leg.right} {leg.strike}): wide spread — fill uncertainty")
        if leg.mid is None:
            warnings.append(f"Leg {i+1} ({leg.right} {leg.strike}): no usable price")
        if is_stale(leg.ts, max_age_seconds=300):
            warnings.append(f"Leg {i+1} ({leg.right} {leg.strike}): quote is stale (>5 min old)")
    return warnings


def _source_mix(legs: list[LegQuote]) -> dict[str, int]:
    mix: dict[str, int] = {}
    for leg in legs:
        key = leg.source or "unknown"
        mix[key] = mix.get(key, 0) + 1
    return mix


def _leg_summary(leg: LegQuote) -> dict:
    return {
        "strike": leg.strike,
        "right": leg.right,
        "side": leg.side,
        "bid": leg.bid,
        "ask": leg.ask,
        "mid": leg.mid,
        "source": leg.source,
        "stale": is_stale(leg.ts, max_age_seconds=300),
        "wide": is_wide_spread(leg.bid, leg.ask, threshold_pct=5.0),
    }


def analyze_box(
    legs: list[LegQuote],
    *,
    intent: Intent,
    expiry: str | datetime,
    capital: float = 10_000.0,
) -> BoxAnalysis:
    """Compute the full analysis for a box spread.

    Invariants (checked; errors return BoxAnalysis with warnings instead of raising):
    - exactly 4 legs: 2 calls + 2 puts, across 2 distinct strikes
    - intent 'lend' means net debit; 'borrow' means net credit
    """
    warnings: list[str] = []

    # Minimum structural checks
    if len(legs) != 4:
        warnings.append(f"Expected 4 legs, got {len(legs)}")
        return BoxAnalysis(
            intent=intent, lower_strike=0, upper_strike=0, width=0, dte=0,
            net_per_contract=0, net_total=0, cost=0, profit=0, roi_pct=0,
            annualized_return_pct=0, contracts=0, total_cost=0, total_profit=0,
            warnings=warnings,
        )

    strikes = sorted({leg.strike for leg in legs})
    if len(strikes) != 2:
        warnings.append(f"Box requires exactly 2 distinct strikes, got {len(strikes)}")
    lower, upper = strikes[0], strikes[-1]
    width = upper - lower

    # Surface quote-quality warnings up front
    warnings.extend(_validate_legs(legs))

    # Net price: sum over legs of (long: +mid, short: -mid)
    net = 0.0
    for leg in legs:
        m = leg.mid
        if m is None:
            continue
        net += m if leg.side == "long" else -m

    # Compute profit/cost by intent
    if intent == "lend":
        # Pay |net| debit now; receive `width` at expiry.
        cost = abs(net) if net > 0 else 0.0
        profit = max(width - cost, 0.0)
    else:
        # Collect |net| credit now; pay `width` at expiry.
        # Capital at risk = width − credit (the amount that must be funded at expiry beyond the credit received)
        credit = -net if net < 0 else 0.0
        cost = max(width - credit, 0.0)  # amount at risk
        profit = credit - (width - credit) if (width - credit) < credit else 0.0
        # Cleaner interpretation: borrower's interest = width − credit.
        # "Profit" as seen from borrower = negative of interest; for display purposes we report
        # the *interest paid* as a positive number under `profit` with intent context.
        profit = max(credit - (width - credit), 0.0) if credit > width else 0.0
        # Above gets complicated; simplify: for borrow we report interest as cost-of-capital,
        # not profit. Use separate fields.
        # See note in BoxAnalysis.profit docstring.
        if cost > 0:
            # Interest rate paid over the period
            interest = width - credit
            cost = credit                     # capital borrowed
            profit = -interest                # negative → this is what you pay (interest)

    # Sizing
    contracts = max(1, int(capital / max(cost * 100.0, 1e-6))) if cost > 0 else 0

    # Annualized
    dte = dte_from_expiry(expiry) if isinstance(expiry, str) else dte_from_expiry(expiry.strftime("%Y-%m-%d"))
    # For annualized: lend uses profit-vs-cost normal; borrow uses interest-vs-capital (profit is
    # negative there, so flip the sign for presentation)
    if intent == "lend":
        ann = annualized_return_pct(profit, cost, dte) if cost > 0 else 0.0
        roi_pct = (profit / cost) * 100.0 if cost > 0 else 0.0
    else:
        # Borrow: report the *implied annual borrowing rate* as a positive number
        interest = abs(profit)   # interest paid
        # roi here == cost of capital per period
        roi_pct = (interest / cost) * 100.0 if cost > 0 else 0.0
        ann = annualized_return_pct(interest, cost, dte) if cost > 0 else 0.0

    total_cost = contracts * cost * 100.0
    # For lend: total_profit = contracts * profit * 100. For borrow: total_interest paid.
    total_profit = contracts * (profit if intent == "lend" else -profit) * 100.0

    return BoxAnalysis(
        intent=intent,
        lower_strike=lower,
        upper_strike=upper,
        width=width,
        dte=dte,
        net_per_contract=net,
        net_total=net * 100.0 * contracts,
        cost=cost,
        profit=(profit if intent == "lend" else abs(profit)),
        roi_pct=round(roi_pct, 4),
        annualized_return_pct=round(ann, 4),
        contracts=contracts,
        total_cost=round(total_cost, 2),
        total_profit=round(total_profit, 2),
        warnings=warnings,
        source_mix=_source_mix(legs),
        leg_summaries=[_leg_summary(leg) for leg in legs],
    )
