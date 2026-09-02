"""Box Spread Strategy Service.

A box spread is a 4-leg options strategy combining a bull call spread
and a bear put spread at the same strikes and expiration.

Legs (for LENDING / collecting interest):
  - Buy  Call  @ K1 (lower strike)
  - Sell Put   @ K1
  - Sell Call  @ K2 (higher strike)
  - Buy  Put   @ K2

The box value at expiration is always K2 - K1 regardless of underlying.
If you buy the box for less than K2 - K1, you earn the difference as interest.
If you sell the box for more than you owe, you get cash now (BORROWING).

Supports pluggable quote providers (yfinance, IBKR, etc.) via the
QuoteProvider abstraction in quote_providers/.
"""

from __future__ import annotations

import asyncio
import math
import logging
from datetime import datetime, date, timedelta, timezone
from typing import Optional, TYPE_CHECKING

from .quote_providers import get_provider, OptionChain, OptionQuote

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from ..models import User

logger = logging.getLogger(__name__)

# Default risk-free rate if the live ^IRX fetch fails (kept in sync with autocallable).
_DEFAULT_RISK_FREE = 0.04
_NET_TICK = 0.05   # combo net-price increment for rounding fair-value limits

# Execution-cost model — what actually differentiates one box from another once the
# gross rate is pinned to risk-free. Tunable; defaults ≈ IBKR retail.
_COMMISSION_PER_CONTRACT = 0.65   # $/contract/leg
_SLIPPAGE_FRACTION = 0.5          # fraction of the combo half-spread you give up to fill


def _round_tick(price: float, tick: float = _NET_TICK) -> float:
    return round(round(price / tick) * tick, 2)


def _friction(legs: list[dict], num_contracts: int) -> tuple[float, float]:
    """Estimated round-the-spread cost of *entering* a box, in dollars and per-share.

    Two real costs that the gross (no-arb) rate ignores and that differ wildly
    between a penny-tight SPX box and a wide illiquid one:
      - slippage: you don't fill at fair value, you give up a fraction of the combo
        bid/ask spread (sum of per-leg spreads). This is the dominant differentiator.
      - commission: 4 legs × contracts.
    Returns ``(friction_total_dollars, slippage_per_share)``.
    """
    combo_spread = sum(
        max(_safe_float(l.get("ask")) - _safe_float(l.get("bid")), 0.0) for l in legs
    )
    slippage_per_share = _SLIPPAGE_FRACTION * (combo_spread / 2.0)
    slippage_total = slippage_per_share * 100.0 * num_contracts
    commission_total = _COMMISSION_PER_CONTRACT * len(legs) * num_contracts
    return slippage_total + commission_total, slippage_per_share


def _box_discount(dte: int, r: float) -> float:
    """Discount factor to expiry — the heart of a box's fair value.

    A box is a synthetic zero-coupon bond worth ``(K2-K1) * DF`` at trade time, so
    the *only* input that matters is the discount factor (not the noisy option
    chain). Uses QuantLib (Actual365Fixed, FlatForward) when present so we can later
    swap in a real SOFR curve; falls back to the identical closed form ``e^(-rT)``
    when QuantLib is absent (it's in the deployed image, not the local venv). Same
    lazy-import + best-effort pattern as quant_service.py.
    """
    t = max(dte, 0) / 365.0
    try:
        import QuantLib as ql
        today = ql.Date.todaysDate()
        ql.Settings.instance().evaluationDate = today
        dc = ql.Actual365Fixed()
        curve = ql.FlatForward(today, r, dc)
        expiry = today + int(max(dte, 0))
        return float(curve.discount(expiry))
    except Exception:
        return math.exp(-r * t)


async def _get_risk_free() -> tuple[float, str]:
    """Fetch the risk-free rate once (^IRX 13-week T-bill), off-thread.

    Reuses ``_risk_free_rate`` from autocallable_service (lazy import avoids any
    import cycle). Returns ``(rate_fraction, source_label)``.
    """
    try:
        from .autocallable_service import _risk_free_rate
        return await asyncio.to_thread(_risk_free_rate, None)
    except Exception:
        return _DEFAULT_RISK_FREE, "Default (4.0%)"


def _target_feasibility(intent: str, target: float, achievable: float) -> tuple[bool, str]:
    """Is the user's target return reachable at a fillable price?

    A box transacts at ≈ the box-market rate (``achievable``). Asking to *lend* above
    it (or *borrow* below it) means no dealer takes the other side — the limit never
    fills. Returns ``(feasible, human_note)``.
    """
    if intent == "lend":
        if target <= achievable + 0.05:
            return True, f"Achievable: a box lends at ≈ {achievable:.2f}% (the box-market rate)."
        return False, (
            f"Your {target:.2f}% lend target is above the ~{achievable:.2f}% box market — "
            f"no one borrows from you at {target:.2f}%, so the order won't fill. "
            f"Achievable ≈ {achievable:.2f}%; lower the target to execute."
        )
    else:
        if target >= achievable - 0.05:
            return True, (
                f"Achievable: a box borrows at ≈ {achievable:.2f}% — cheap vs typical "
                f"margin rates (8–12%)."
            )
        return False, (
            f"Your {target:.2f}% borrow target is below the ~{achievable:.2f}% box market — "
            f"no one lends to you that cheaply, so the order won't fill. "
            f"Achievable ≈ {achievable:.2f}%."
        )


# ---------------------------------------------------------------------------
# Default scan universe
# ---------------------------------------------------------------------------
# Curated for deep / active options books and volatility — the markets where a
# 4-leg box actually fills. Early-assignment and tax treatment are intentionally
# NOT a filter here (per product decision), so liquid American-style ETFs are in.
# The scanner is resilient: any ticker that returns no viable box is skipped, so
# thin names cost nothing. Edit this one list to change what gets scanned.
BOX_SCAN_UNIVERSE: list[str] = [
    # Cash-settled index options (European, deepest books)
    "SPX", "NDX", "RUT", "XSP",
    # Penny-wide, deepest ETF options books
    "SPY", "QQQ", "IWM", "DIA",
    # Volatile / active sector & thematic + user-requested
    "SMH", "QQQM", "SPYM", "TLT", "GLD",
]


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Core box-spread engine — operates on OptionChain data (provider-agnostic)
# ---------------------------------------------------------------------------

def _build_strike_maps(chain: OptionChain):
    """Index option quotes by (right, strike) for fast lookup."""
    calls: dict[float, OptionQuote] = {}
    puts: dict[float, OptionQuote] = {}
    for q in chain.quotes:
        target = calls if q.right == "C" else puts
        target[q.strike] = q
    return calls, puts


def _fill_probability(legs: list[dict]) -> tuple[int, dict]:
    """Estimate how likely a 4-leg box is to actually fill, 0–100 — purely from
    liquidity microstructure, independent of the price you choose.

    A combo order fills when there is real two-sided interest at tight quotes. We
    read that from indicators only:
      - average bid/ask tightness across all 4 legs (tight → market makers active)
      - the *thinnest* leg's depth (OI + volume) — the box is only as fillable as
        its weakest leg
      - turnover (volume / OI) — fresh two-way flow today, not just stale OI
    Crossed or missing quotes collapse the score.
    """
    if not legs:
        return 0, {}

    tightness_scores: list[float] = []
    liq_values: list[float] = []
    turnover_scores: list[float] = []
    crossed = False
    for leg in legs:
        bid = _safe_float(leg.get("bid"))
        ask = _safe_float(leg.get("ask"))
        oi = _safe_float(leg.get("oi"))
        vol = _safe_float(leg.get("vol", leg.get("volume", 0)))
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else _safe_float(leg.get("mid"))
        if bid <= 0 or ask <= 0 or ask < bid:
            crossed = True
        spread_ratio = (ask - bid) / mid if mid > 0 else 1.0
        tightness_scores.append(1.0 - min(max(spread_ratio, 0.0), 0.25) / 0.25)
        liq_values.append(oi + vol)
        turnover_scores.append(min(vol / max(oi, 1.0), 1.0))   # daily turnover, capped at 1×

    avg_tightness = sum(tightness_scores) / len(tightness_scores)
    min_liq = min(liq_values) if liq_values else 0.0
    # log-normalized; saturates around ~5k OI+vol on the thinnest leg
    liq_norm = min(math.log1p(min_liq) / math.log1p(5000), 1.0)
    avg_turnover = sum(turnover_scores) / len(turnover_scores)

    score = (0.40 * avg_tightness + 0.45 * liq_norm + 0.15 * avg_turnover) * 100.0
    if crossed:
        score *= 0.4
    score_int = int(max(0, min(100, round(score))))

    factors = {
        "spread_tightness": round(avg_tightness, 3),
        "liquidity": round(liq_norm, 3),
        "turnover": round(avg_turnover, 3),
        "min_leg_oi_vol": int(min_liq),
        "crossed": crossed,
    }
    return score_int, factors


def _find_box_spreads_from_chains(
    chains: list[OptionChain],
    ticker: str,
    current_price: float,
    amount: float,
    duration_days: int,
    target_annual_return: float,
    intent: str,
    source: str,
    max_contracts: int = 1,
    risk_free_rate: float = _DEFAULT_RISK_FREE,
) -> list[dict]:
    """Find box spread combinations from pre-fetched option chains.

    Pure computation — no I/O. All data comes from the already-fetched chains.
    ``risk_free_rate`` (fraction) anchors each box's no-arbitrage fair value.
    """
    is_index = ticker.lstrip("^").upper() in (
        "SPX", "XSP", "NDX", "RUT", "VIX", "DJX", "OEX", "GSPC",
    ) or ticker.startswith("^")

    all_spreads = []

    for chain in chains:
        calls, puts = _build_strike_maps(chain)
        common_strikes = sorted(set(calls.keys()) & set(puts.keys()))

        if len(common_strikes) < 2:
            continue

        dte_str = chain.expiration
        try:
            exp_date = datetime.strptime(dte_str, "%Y-%m-%d").date()
        except Exception:
            continue
        dte = (exp_date - date.today()).days
        if dte < 7:
            continue

        # No-arb anchor for every box at this expiry: discount factor + the rate
        # you actually transact at (≈ risk-free). Depends only on (dte, r).
        df = _box_discount(dte, risk_free_rate)
        theoretical_rate = risk_free_rate * 100.0

        # Focus search around ATM
        atm_idx = 0
        for i, s in enumerate(common_strikes):
            if s >= current_price:
                atm_idx = i
                break

        window = 30
        search_strikes = common_strikes[max(0, atm_idx - window):min(len(common_strikes), atm_idx + window)]

        for i, k1 in enumerate(search_strikes):
            for k2 in search_strikes[i + 1:]:
                c1, p1 = calls.get(k1), puts.get(k1)
                c2, p2 = calls.get(k2), puts.get(k2)
                if not all([c1, p1, c2, p2]):
                    continue

                box_width = k2 - k1
                if box_width < 1:
                    continue

                # Illiquid check
                if c1.mid <= 0 or p1.mid <= 0 or c2.mid <= 0 or p2.mid <= 0:
                    continue

                # Broken IV filter
                if any(q.iv and q.iv > 2.0 for q in [c1, p1, c2, p2]):
                    continue

                # Liquidity filter — require meaningful OI/volume for fill probability
                min_oi = min(c1.oi, p1.oi, c2.oi, p2.oi)
                total_activity = sum(q.oi + q.volume for q in [c1, p1, c2, p2])
                if is_index:
                    # Index options: require each leg to have SOME activity
                    min_activity = min(
                        c1.oi + c1.volume, p1.oi + p1.volume,
                        c2.oi + c2.volume, p2.oi + p2.volume,
                    )
                    if min_activity < 10:
                        continue
                    # Also require total activity across all 4 legs
                    if total_activity < 100:
                        continue
                elif min_oi < 10:
                    continue

                def _leg_dict(q: OptionQuote, action: str):
                    d = {
                        "action": action,
                        "type": q.right_label,
                        "strike": q.strike,
                        "bid": q.bid,
                        "ask": q.ask,
                        "mid": q.mid,
                        "iv": round(q.iv * 100, 1) if q.iv else None,
                        "oi": q.oi,
                        "vol": q.volume,
                    }
                    # Include Greeks when available (IBKR)
                    if q.delta is not None:
                        d["delta"] = round(q.delta, 4)
                    if q.gamma is not None:
                        d["gamma"] = round(q.gamma, 4)
                    if q.theta is not None:
                        d["theta"] = round(q.theta, 4)
                    if q.vega is not None:
                        d["vega"] = round(q.vega, 4)
                    if q.conid is not None:
                        d["conid"] = q.conid
                    return d

                if intent == "lend":
                    cost_mid = (c1.mid - c2.mid) + (p2.mid - p1.mid)
                    if cost_mid <= 0:
                        continue

                    profit = box_width - cost_mid
                    if profit <= 0:
                        continue

                    return_pct = (profit / cost_mid) * 100
                    # Geometric annualization — single source of truth.
                    # See backend/app/services/trade_math.py::annualized_return_pct.
                    from .trade_math import annualized_return_pct
                    annual_return = annualized_return_pct(profit, cost_mid, dte)
                    if annual_return > 20.0:   # sanity cap — above this is almost always a stale/crossed quote
                        continue

                    legs = [
                        _leg_dict(c1, "BUY"),
                        _leg_dict(p1, "SELL"),
                        _leg_dict(c2, "SELL"),
                        _leg_dict(p2, "BUY"),
                    ]

                    # Smart price: a realistic, likely-to-fill net (between bid and
                    # mid per leg, from microstructure) — the price we evaluate the
                    # return at, instead of the optimistic mid or the brutal natural.
                    smart = compute_smart_prices(legs)
                    smart_cost = smart.get("total_net_per_contract", cost_mid)
                    smart_rate = (
                        annualized_return_pct(box_width - smart_cost, smart_cost, dte)
                        if smart_cost > 0 and (box_width - smart_cost) > 0 else 0.0
                    )
                    # Fill probability is liquidity-driven, independent of price.
                    fill_prob, fill_factors = _fill_probability(legs)

                    # No-arbitrage fair value: a box is worth width * DF. Paying the
                    # fair debit lends at ≈ r — the price that actually fills. We
                    # nudge 1 tick *up* (pay a hair more = accept a hair under r) to
                    # sit just inside the dealer's band. The stale-quote "rate" above
                    # r is unfillable; edge_vs_fair quantifies the (noise) gap.
                    fair_net = box_width * df
                    recommended_net = _round_tick(fair_net + _NET_TICK)
                    edge_vs_fair = round(fair_net - cost_mid, 4)  # >0 = quoted cheaper than fair (real edge)

                    cost_per = cost_mid * 100
                    if cost_per <= 0:
                        continue
                    num = min(max_contracts, max(1, round(amount / cost_per)))
                    total_cost = num * cost_per
                    total_val = num * box_width * 100
                    total_profit = total_val - total_cost

                    # Net-of-cost rate — the real differentiator. Gross interest at the
                    # fair (fillable) price is ≈ r for every box; execution friction
                    # (slippage + commission) is what separates a tight wide box from a
                    # wide thin one, and can even turn a tiny box negative.
                    friction_total, slip_per_share = _friction(legs, num)
                    capital_at_fair = fair_net * 100 * num
                    gross_interest = (box_width - fair_net) * 100 * num
                    net_interest = gross_interest - friction_total
                    net_rate = (
                        annualized_return_pct(net_interest, capital_at_fair, dte)
                        if capital_at_fair > 0 else 0.0
                    )
                    cost_drag_bps = round((theoretical_rate - net_rate) * 100, 1)

                    all_spreads.append({
                        "expiration": dte_str,
                        "dte": dte,
                        "lower_strike": k1,
                        "upper_strike": k2,
                        "box_width": box_width,
                        "intent": "lend",
                        "legs": legs,
                        "box_cost_mid": round(cost_mid, 4),
                        "box_value_at_expiration": box_width,
                        "profit_per_contract": round(profit, 4),
                        "return_pct": round(return_pct, 2),
                        "annualized_return_pct": round(annual_return, 2),
                        "mid_annual_rate": round(annual_return, 2),
                        "smart_annual_rate": round(smart_rate, 2),
                        "smart_price": smart,
                        "net_achievable_rate": round(net_rate, 2),
                        "friction_cost": round(friction_total, 2),
                        "cost_drag_bps": cost_drag_bps,
                        "fair_value_net": round(fair_net, 4),
                        "recommended_limit_net": recommended_net,
                        "theoretical_annual_rate": round(theoretical_rate, 2),
                        "edge_vs_fair": edge_vs_fair,
                        "fill_probability": fill_prob,
                        "fill_factors": fill_factors,
                        "num_contracts": num,
                        "total_cost": round(total_cost, 2),
                        "total_value_at_expiration": round(total_val, 2),
                        "total_profit": round(total_profit, 2),
                        "implied_annual_rate": round(annual_return, 2),
                        "min_open_interest": min_oi,
                    })
                else:
                    proceeds_mid = (c1.mid - p1.mid) + (p2.mid - c2.mid)
                    if proceeds_mid <= 0:
                        continue

                    cost_borrow = box_width - proceeds_mid
                    if cost_borrow <= 0:
                        continue

                    interest_rate = (cost_borrow / proceeds_mid) * 100
                    # Geometric annualization for the implied borrow rate.
                    from .trade_math import annualized_return_pct
                    annual_rate = annualized_return_pct(cost_borrow, proceeds_mid, dte)
                    if annual_rate < 1.0 or annual_rate > 25.0:   # widened ceiling for geometric
                        continue

                    legs = [
                        _leg_dict(c1, "SELL"),
                        _leg_dict(p1, "BUY"),
                        _leg_dict(c2, "BUY"),
                        _leg_dict(p2, "SELL"),
                    ]

                    # Smart price: realistic likely-to-fill net credit. compute_smart_prices
                    # returns net = Σ(buy) − Σ(sell); for a borrow box that net is negative,
                    # so proceeds = −net.
                    smart = compute_smart_prices(legs)
                    smart_proceeds = -smart.get("total_net_per_contract", -proceeds_mid)
                    smart_rate = (
                        annualized_return_pct(box_width - smart_proceeds, smart_proceeds, dte)
                        if smart_proceeds > 0 and (box_width - smart_proceeds) > 0 else 999.0
                    )
                    fill_prob, fill_factors = _fill_probability(legs)

                    # No-arb fair value: selling the box for width * DF borrows at ≈ r —
                    # the credit that actually fills. Nudge 1 tick *down* (accept a hair
                    # less credit = pay a hair over r) to sit inside the dealer's band.
                    fair_net = box_width * df
                    recommended_net = _round_tick(fair_net - _NET_TICK)
                    edge_vs_fair = round(proceeds_mid - fair_net, 4)  # >0 = quoted credit richer than fair

                    per_contract = proceeds_mid * 100
                    if per_contract <= 0:
                        continue
                    num = min(max_contracts, max(1, round(amount / per_contract)))
                    total_proceeds = num * per_contract
                    total_owed = num * box_width * 100
                    total_interest = total_owed - total_proceeds

                    # Net-of-cost borrow rate — friction makes borrowing *more* expensive,
                    # so the net rate is above the gross ≈ r. Tight/wide boxes differ.
                    friction_total, slip_per_share = _friction(legs, num)
                    proceeds_at_fair = fair_net * 100 * num
                    gross_interest = (box_width - fair_net) * 100 * num
                    net_interest = gross_interest + friction_total   # friction adds to borrow cost
                    net_rate = (
                        annualized_return_pct(net_interest, proceeds_at_fair, dte)
                        if proceeds_at_fair > 0 else 999.0
                    )
                    cost_drag_bps = round((net_rate - theoretical_rate) * 100, 1)

                    all_spreads.append({
                        "expiration": dte_str,
                        "dte": dte,
                        "lower_strike": k1,
                        "upper_strike": k2,
                        "box_width": box_width,
                        "intent": "borrow",
                        "legs": legs,
                        "net_achievable_rate": round(net_rate, 2),
                        "friction_cost": round(friction_total, 2),
                        "cost_drag_bps": cost_drag_bps,
                        "box_proceeds_mid": round(proceeds_mid, 4),
                        "box_value_at_expiration": box_width,
                        "interest_cost_per_contract": round(cost_borrow, 4),
                        "interest_rate_pct": round(interest_rate, 2),
                        "annualized_rate_pct": round(annual_rate, 2),
                        "mid_annual_rate": round(annual_rate, 2),
                        "smart_annual_rate": round(smart_rate, 2),
                        "smart_price": smart,
                        "fair_value_net": round(fair_net, 4),
                        "recommended_limit_net": recommended_net,
                        "theoretical_annual_rate": round(theoretical_rate, 2),
                        "edge_vs_fair": edge_vs_fair,
                        "fill_probability": fill_prob,
                        "fill_factors": fill_factors,
                        "num_contracts": num,
                        "total_proceeds": round(total_proceeds, 2),
                        "total_owed_at_expiration": round(total_owed, 2),
                        "total_interest_cost": round(total_interest, 2),
                        "implied_annual_rate": round(annual_rate, 2),
                        "min_open_interest": min_oi,
                    })

    return all_spreads


# Attach a human-readable label helper to OptionQuote for _leg_dict()
OptionQuote.right_label = property(lambda self: "CALL" if self.right == "C" else "PUT")


# ---------------------------------------------------------------------------
# Risk analysis — unchanged
# ---------------------------------------------------------------------------

def _build_risks(ticker: str) -> list[dict]:
    clean = ticker.lstrip("^").upper()
    is_european = clean in ("SPX", "XSP")
    return [
        {
            "category": "Execution Risk",
            "description": "Box spreads require all 4 legs to be filled simultaneously. Partial fills can leave you exposed to directional risk.",
            "severity": "Medium",
            "mitigation": "Use limit orders for the entire spread as a single order. Ensure sufficient liquidity (OI > 100 per leg).",
        },
        {
            "category": "Pin Risk (American-Style Options)",
            "description": "If using American-style options (e.g., SPY), short options can be exercised early. This breaks the box guarantee.",
            "severity": "Low" if is_european else "High",
            "mitigation": "Use European-style index options (XSP, SPX) which can only be exercised at expiration.",
        },
        {
            "category": "Margin Requirements",
            "description": "Brokers may require significant margin for box spreads despite the risk being defined.",
            "severity": "Medium",
            "mitigation": "Verify your broker supports box spread margin treatment. IBKR, Schwab, and Fidelity generally handle this well.",
        },
        {
            "category": "Bid-Ask Spread Cost",
            "description": "Wide bid-ask spreads on any of the 4 legs can significantly reduce returns.",
            "severity": "Medium",
            "mitigation": "Only trade liquid strike prices. Use limit orders at or near mid-price.",
        },
        {
            "category": "Settlement Risk",
            "description": "Cash-settled index options settle based on opening or closing prices which may differ from expected.",
            "severity": "Low",
            "mitigation": "SPX uses AM settlement, XSP uses PM settlement.",
        },
        {
            "category": "Tax Implications",
            "description": "Index options like SPX/XSP receive favorable 60/40 tax treatment under Section 1256.",
            "severity": "Info",
            "mitigation": "Consult a tax advisor.",
        },
    ]


# ---------------------------------------------------------------------------
# Smart price recommendation
# ---------------------------------------------------------------------------

def compute_smart_prices(legs: list[dict]) -> dict:
    """Compute quant-level recommended fill prices using market microstructure signals.

    Multi-factor model combining:
      1. Last-trade position — where in the spread did the most recent fill occur
      2. Bid-ask spread analysis — tighter spreads allow more aggressive pricing
      3. Liquidity depth — OI and volume indicate market maker willingness
      4. Put-call parity / synthetic pricing — cross-check theoretical value
      5. IV skew signal — if IV is elevated, lean toward mid; if fair, lean aggressive
      6. Volume-weighted price estimation — recent volume hints at true fair value
      7. Market maker edge estimation — penny-pilot vs nickel-wide contracts

    Args:
        legs: list of dicts with keys: action, strike, bid, ask, last, oi, vol,
              and optionally right, type, mid, iv, delta, gamma, theta, vega.

    Returns dict with:
        - recommended_legs: list of per-leg {price, confidence_pct, reasoning}
        - total_net_per_contract: net debit/credit
    """
    import math

    # TICK_SIZE for rounding (index options = 0.05)
    TICK = 0.05

    def _round_tick(p: float) -> float:
        return round(round(p / TICK) * TICK, 2)

    recommended = []
    for leg in legs:
        bid = _safe_float(leg.get("bid"))
        ask = _safe_float(leg.get("ask"))
        last = _safe_float(leg.get("last", leg.get("mid", 0)))
        oi = _safe_float(leg.get("oi", 0))
        vol = _safe_float(leg.get("vol", leg.get("volume", 0)))
        iv = _safe_float(leg.get("iv", 0))
        delta = leg.get("delta")
        action = leg.get("action", "BUY").upper()

        if ask <= 0 or bid <= 0:
            recommended.append({
                "strike": leg.get("strike"),
                "action": action,
                "type": leg.get("type"),
                "price": _round_tick(last),
                "confidence_pct": 25,
                "reasoning": "No valid bid/ask — using last price.",
            })
            continue

        mid = (bid + ask) / 2
        spread = ask - bid
        spread_ratio = spread / mid if mid > 0 else 1.0

        # ── Factor 1: Last-trade position (0=at bid, 1=at ask) ──
        if spread > 0 and last > 0:
            last_pos = max(0, min(1, (last - bid) / spread))
        else:
            last_pos = 0.5

        # ── Factor 2: Liquidity depth score (log-scaled) ──
        # OI is more indicative of deep liquidity than intraday volume
        liq_raw = math.log1p(oi * 0.7 + vol * 0.3)
        liq_norm = min(liq_raw / 10, 1.0)  # saturates around ~20k OI+vol

        # ── Factor 3: Spread tightness score ──
        # Tighter spread = higher confidence, can be more aggressive
        tightness = 1 - min(spread_ratio, 0.20) / 0.20  # 0=very wide (20%+), 1=penny-tight

        # ── Factor 4: Volume-to-OI ratio (daily turnover) ──
        # High V/OI = active trading, last price is more reliable
        turnover = min(vol / max(oi, 1), 2.0) / 2.0  # normalize to [0, 1]

        # ── Factor 5: IV signal ──
        # If IV is known and elevated (>0.30 for index), prices are inflated
        # Slight lean toward bid for buys, ask for sells
        iv_adj = 0.0
        if iv > 0:
            if iv > 0.30:
                iv_adj = 0.05  # IV elevated, lean conservative
            elif iv < 0.15:
                iv_adj = -0.05  # IV low, can be more aggressive

        # ── Factor 6: Delta-based urgency ──
        # Deep ITM (|delta|>0.8) has tighter natural spreads, less edge to capture
        # ATM (|delta|~0.5) has widest spreads, most room for improvement
        delta_adj = 0.0
        if delta is not None:
            abs_d = abs(_safe_float(delta))
            if abs_d > 0.8:
                delta_adj = 0.05  # deep ITM, less room to improve
            elif abs_d < 0.3:
                delta_adj = -0.03  # OTM, can be more patient

        # ── Composite fill position ──
        # Higher = closer to ask (better for sellers, worse for buyers)
        # Lower = closer to bid (better for buyers, worse for sellers)
        fill_pos = (
            0.30 * last_pos          # where last trade filled
            + 0.25 * tightness       # tighter spread → more aggressive
            + 0.20 * liq_norm        # higher liquidity → more aggressive
            + 0.15 * turnover        # higher turnover → trust last trade more
            + 0.10 * 0.5             # baseline
        )
        fill_pos = max(0.10, min(0.90, fill_pos + iv_adj + delta_adj))

        # ── Price calculation ──
        if action == "BUY":
            # Buyer wants low price: start from bid, move toward mid
            raw_price = bid + fill_pos * spread
        else:
            # Seller wants high price: start from ask, move toward mid
            raw_price = ask - fill_pos * spread

        price = _round_tick(raw_price)

        # Ensure price stays within bid-ask
        price = max(_round_tick(bid), min(_round_tick(ask), price))

        # ── Confidence score ──
        # Base 40 + up to 20 from liquidity + up to 20 from tightness + up to 15 from turnover
        confidence = min(95, int(
            40
            + liq_norm * 20
            + tightness * 20
            + turnover * 15
        ))

        # ── Reasoning ──
        parts = []
        if spread_ratio < 0.01:
            parts.append("Penny-tight spread")
        elif spread_ratio < 0.03:
            parts.append("Tight spread")
        elif spread_ratio < 0.08:
            parts.append(f"Moderate spread ({spread_ratio:.1%})")
        else:
            parts.append(f"Wide spread ({spread_ratio:.1%})")

        if liq_norm > 0.7:
            parts.append(f"deep liquidity (OI:{int(oi)}, Vol:{int(vol)})")
        elif liq_norm > 0.4:
            parts.append(f"good liquidity (OI:{int(oi)})")
        else:
            parts.append(f"thin liquidity (OI:{int(oi)})")

        if turnover > 0.5:
            parts.append("active trading")
        if last_pos > 0.6:
            parts.append("last traded near ask")
        elif last_pos < 0.4:
            parts.append("last traded near bid")

        if iv > 0:
            parts.append(f"IV={iv:.0%}")

        recommended.append({
            "strike": leg.get("strike"),
            "action": action,
            "type": leg.get("type"),
            "price": price,
            "confidence_pct": confidence,
            "reasoning": "; ".join(parts),
        })

    # Calculate total net
    total_net = 0.0
    for r in recommended:
        if r["action"] == "BUY":
            total_net += r["price"]
        else:
            total_net -= r["price"]

    return {
        "recommended_legs": recommended,
        "total_net_per_contract": round(total_net, 4),
    }


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def run_box_strategy(
    ticker: str,
    amount: float,
    duration_days: int,
    target_annual_return: float,
    intent: str,
    quote_source: str = "yfinance",
    user: Optional["User"] = None,
    db: Optional["AsyncSession"] = None,
    target_expiration: str | None = None,
    max_contracts: int = 1,
) -> dict:
    """Async entry point for box spread computation.

    Fetches quotes from the requested provider, then runs the
    provider-agnostic spread engine.

    ``max_contracts`` caps position size (default 1) — each contract carries
    commission + bid/ask cost, so fewer is cheaper. Sizing prefers wider boxes
    so the target ``amount`` deploys in as few contracts as possible.
    """
    provider = get_provider(quote_source, user=user, db=db)

    # 1. Get underlying price
    try:
        underlying = await provider.get_underlying_price(ticker)
    except Exception as exc:
        return {"error": f"Cannot fetch price for {ticker}: {exc}"}

    price = underlying.price
    display_ticker = underlying.symbol

    # 2. Get available expirations
    try:
        expirations = await provider.get_option_expirations(ticker)
    except Exception:
        return {"error": "No options data available for this ticker."}

    if not expirations:
        return {"error": "No options expiration dates found."}

    today = date.today()

    if target_expiration and target_expiration in expirations:
        # User selected a specific expiration — use only that one
        exp_date = datetime.strptime(target_expiration, "%Y-%m-%d").date()
        dte = (exp_date - today).days
        suitable_exps = [(target_expiration, dte, 0)]
    else:
        min_dte = max(7, int(duration_days * 0.5))
        max_dte = int(duration_days * 1.5) + 15

        suitable_exps = []
        for exp_str in expirations:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except Exception:
                continue
            dte = (exp_date - today).days
            if min_dte <= dte <= max_dte:
                distance = abs(dte - duration_days)
                suitable_exps.append((exp_str, dte, distance))

        suitable_exps.sort(key=lambda x: x[2])

        if not suitable_exps:
            return {
                "error": f"No options expirations found near {duration_days} days. "
                         f"Available: {expirations[0]} to {expirations[-1] if len(expirations) > 1 else expirations[0]}.",
                "available_expirations": expirations[:15],
            }

        suitable_exps = suitable_exps[:3]

    # 3. Fetch option chains for suitable expirations
    chains: list[OptionChain] = []
    for exp_str, _dte, _dist in suitable_exps:
        try:
            chain = await provider.get_option_chain(ticker, exp_str)
            if chain.quotes:
                chains.append(chain)
        except Exception as exc:
            logger.warning(f"Failed to fetch chain for {ticker} {exp_str}: {exc}")
            continue

    if not chains:
        return {"error": "Could not fetch option chain data."}

    # 4. Risk-free rate (once) → run the box-spread engine (pure computation, no I/O)
    r, r_source = await _get_risk_free()
    all_spreads = await asyncio.to_thread(
        _find_box_spreads_from_chains,
        chains, display_ticker, price,
        amount, duration_days, target_annual_return, intent, quote_source,
        max_contracts, r,
    )

    if not all_spreads:
        return {
            "error": "No viable box spreads found. Try SPX, XSP, or SPY.",
        }

    # 5. Rank by NET-OF-COST rate (the real differentiator now that the gross rate is
    # pinned to risk-free) blended with fill probability. The best box keeps the most
    # of r after slippage + commission — which is also the tight, liquid, capital-
    # efficient one. For borrow, a *lower* net rate is better (cheaper to borrow).
    nets = [s.get("net_achievable_rate", 0.0) for s in all_spreads]
    net_lo, net_hi = (min(nets), max(nets)) if nets else (0.0, 0.0)
    net_span = (net_hi - net_lo) or 1.0

    def _net_favorability(s):  # 1.0 = best net rate for this intent, 0.0 = worst
        frac = (s.get("net_achievable_rate", 0.0) - net_lo) / net_span
        return frac if intent == "lend" else (1.0 - frac)

    all_spreads.sort(
        key=lambda x: 0.50 * _net_favorability(x)
                    + 0.50 * (x.get("fill_probability", 0) / 100.0),
        reverse=True,
    )

    # Diverse expirations — up to 5
    seen = set()
    top = []
    for s in all_spreads:
        if s["expiration"] not in seen and len(top) < 5:
            top.append(s)
            seen.add(s["expiration"])
    for s in all_spreads:
        if s not in top and len(top) < 5:
            top.append(s)

    achievable_rate = round(r * 100.0, 2)
    feasible, feasibility_note = _target_feasibility(intent, target_annual_return, achievable_rate)

    return {
        "ticker": display_ticker,
        "current_price": round(price, 2),
        "intent": intent,
        "target_amount": amount,
        "target_duration_days": duration_days,
        "target_annual_return": target_annual_return,
        "quote_source": quote_source,
        "risk_free_rate": achievable_rate,
        "risk_free_source": r_source,
        "achievable_rate": achievable_rate,
        "target_feasible": feasible,
        "feasibility_note": feasibility_note,
        "spreads": top,
        "risks": _build_risks(display_ticker),
        "available_expirations": expirations[:15],
    }


# ---------------------------------------------------------------------------
# Market timing — is now a good time to send a box order?
# ---------------------------------------------------------------------------

def box_market_timing(now: datetime | None = None) -> dict:
    """When is a box spread most likely to fill cleanly?

    A box is direction-neutral, so timing doesn't change the thesis — it changes
    *fill quality*. Bid/ask on all 4 legs widen at the open and into the close, so
    the reliable window is mid-session. Pure function (stdlib ``zoneinfo``), so it
    is trivially testable and adds no dependency.
    """
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    now_et = now.astimezone(et) if now is not None else datetime.now(et)
    minutes = now_et.hour * 60 + now_et.minute

    OPEN = 9 * 60 + 30          # 09:30 ET
    ETF_CLOSE = 16 * 60         # 16:00 ET (ETF options)
    INDEX_CLOSE = 16 * 60 + 15  # 16:15 ET (cash-settled index options)

    notes = [
        "US equity/ETF options trade 09:30–16:00 ET; cash-settled index options (SPX/NDX/RUT/XSP) to 16:15 ET.",
        "Box legs fill best mid-session — bid/ask widen at the open and into the close.",
        "Exchange holidays are not checked here — confirm the market is open on US holidays.",
    ]

    if now_et.weekday() >= 5:  # Sat/Sun
        session, market_open, good = "weekend", False, False
        rec = "Market closed (weekend). Place box orders during ET market hours for reliable fills."
    elif minutes < OPEN:
        session, market_open, good = "pre_open", False, False
        rec = "Pre-market. Option books are thin — wait for the 09:30 ET open before sending a box."
    elif minutes >= INDEX_CLOSE:
        session, market_open, good = "after_close", False, False
        rec = "After hours. Place box orders during the regular session for reliable fills."
    elif minutes < OPEN + 15:
        session, market_open, good = "open_caution", True, False
        rec = "First 15 minutes — spreads are wide and quotes jumpy. Wait ~15 min for tighter box fills."
    elif minutes >= ETF_CLOSE - 10:
        session, market_open, good = "late_close_caution", True, False
        rec = "Final minutes before the close — spreads widen and fills get less reliable."
    elif 12 * 60 <= minutes < 13 * 60:
        session, market_open, good = "lunch_lull", True, True
        rec = "Midday lull — liquidity dips slightly but boxes still fill. Good to trade."
    else:
        session, market_open, good = "regular", True, True
        rec = "Regular session with mid-day liquidity — the best window for box-spread fills."

    return {
        "now_et": now_et.isoformat(timespec="minutes"),
        "market_open": market_open,
        "session": session,
        "good_to_trade": good,
        "recommendation": rec,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Opportunity scanner — sweep the universe, surface the most fillable boxes
# ---------------------------------------------------------------------------

async def scan_box_opportunities(
    intent: str,
    duration_days: int,
    target_annual_return: float,
    amount: float = 10_000.0,
    tickers: Optional[list[str]] = None,
    max_contracts: int = 1,
    per_ticker: int = 1,
    quote_source: str = "yfinance",
    user: Optional["User"] = None,
    db: Optional["AsyncSession"] = None,
) -> dict:
    """Scan a universe of liquid/volatile option markets for the box spreads most
    likely to actually fill.

    For each ticker it runs the standard box engine and returns the top ``per_ticker``
    (1–2) boxes ranked by fill probability (then closeness/favorability to the no-arb
    fair value). Returns are NOT a gate — every box transacts at ≈ the box-market rate,
    so the scanner reports that achievable rate + whether the user's target is feasible
    rather than hiding everything when an unreachable target isn't met. Tickers are
    swept concurrently; failures are reported in ``skipped``. Per-ticker results are
    cached briefly to shield the quote provider from repeat scans.
    """
    from .cache_service import get_cached, set_cached
    from ..database import async_session

    universe = [t.strip().upper() for t in (tickers or BOX_SCAN_UNIVERSE) if t and t.strip()]
    per_ticker = max(1, min(per_ticker, 2))

    # Box-market rate + target feasibility — global (rate depends only on the curve).
    r, r_source = await _get_risk_free()
    achievable_rate = round(r * 100.0, 2)
    feasible, feasibility_note = _target_feasibility(intent, target_annual_return, achievable_rate)

    def _rank_key(s: dict):
        # Execution probability first (the user's "must actually fill"), then the
        # net-of-cost rate (lend: higher better; borrow: lower better).
        nr = s.get("net_achievable_rate", 0.0) or 0.0
        return (s.get("fill_probability", 0), nr if intent == "lend" else -nr)

    # An AsyncSession is NOT safe for concurrent use, so the gathered scans must NOT share the
    # caller's `db` (sharing collides their flush/commit → "this transaction is closed", and every
    # miss then fails to cache). Give each task its OWN short-lived session and cap how many run at
    # once so the single instance's connection pool isn't exhausted. db=None (standalone/MCP) → no DB.
    _scan_sem = asyncio.Semaphore(4)

    async def _scan_compute(tk: str, sess):
        cache_key = (
            f"box-scan:{tk}:{intent}:{duration_days}:{target_annual_return}:"
            f"{int(amount)}:{max_contracts}:{per_ticker}:{quote_source}"
        )
        if sess is not None:
            cached = await get_cached(sess, cache_key)
            if cached is not None:
                return tk, cached, None

        try:
            res = await run_box_strategy(
                ticker=tk,
                amount=amount,
                duration_days=duration_days,
                target_annual_return=target_annual_return,
                intent=intent,
                quote_source=quote_source,
                user=user,
                db=sess,
                max_contracts=max_contracts,
            )
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill the scan
            return tk, None, f"{type(exc).__name__}: {exc}"

        if res.get("error") and not res.get("spreads"):
            return tk, None, res["error"]

        spreads = res.get("spreads", [])
        # Keep liquid boxes with a computable fair value; rank by fill probability.
        viable = [s for s in spreads if s.get("fill_probability", 0) > 0 and s.get("fair_value_net")]
        if not viable:
            return tk, None, "no liquid box found"

        chosen = sorted(viable, key=_rank_key, reverse=True)[:per_ticker]
        opps = [
            {
                **s,
                "ticker": res.get("ticker", tk),
                "current_price": res.get("current_price"),
            }
            for s in chosen
        ]
        payload = {"opportunities": opps}
        if sess is not None:
            await set_cached(sess, cache_key, payload, ttl_seconds=180)
        return tk, payload, None

    async def _scan_one(tk: str):
        async with _scan_sem:
            if db is None:
                return await _scan_compute(tk, None)
            async with async_session() as sess:
                return await _scan_compute(tk, sess)

    results = await asyncio.gather(*[_scan_one(tk) for tk in universe], return_exceptions=False)

    opportunities: list[dict] = []
    skipped: list[dict] = []
    for tk, payload, err in results:
        if err or not payload or not payload.get("opportunities"):
            skipped.append({"ticker": tk, "reason": err or "no viable box"})
        else:
            opportunities.extend(payload["opportunities"])

    opportunities.sort(key=_rank_key, reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "intent": intent,
        "params": {
            "duration_days": duration_days,
            "target_annual_return": target_annual_return,
            "amount": amount,
            "max_contracts": max_contracts,
            "per_ticker": per_ticker,
            "quote_source": quote_source,
        },
        "universe": universe,
        "risk_free_rate": achievable_rate,
        "risk_free_source": r_source,
        "achievable_rate": achievable_rate,
        "target_feasible": feasible,
        "feasibility_note": feasibility_note,
        "market_timing": box_market_timing(),
        "opportunities": opportunities,
        "skipped": skipped,
    }
