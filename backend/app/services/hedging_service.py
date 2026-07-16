"""Hedging Strategy Service.

Given a long holding (e.g. 500 shares of SMH) this service constructs the
menu of option-based hedges that a quantitative hedge fund would actually run
to protect the position over a chosen horizon, and reports every number a
risk desk needs to decide between them:

  * Protective Put            — full downside insurance above a floor.
  * Deep ITM Put              — high floor, minimal time decay (low theta).
  * Buffered Put Spread       — zero loss for the first X% of a drop.
  * Collar                    — buy the put, sell an upside call to pay for it.
  * Buffered Collar           — forgo early upside to fund a no-loss buffer.
  * Tail Hedge (deep OTM put) — convex crash protection for pennies.
  * Beta-Weighted Index Put   — macro overlay hedging beta x notional with
                                liquid index options (returned separately).

For every structure we compute: exact strikes, expiration / DTE, contract
count (hedge-ratio aware), net premium, cost as % of notional, annualized cost
drag, the protected floor / capped ceiling, worst-case loss, breakeven, and
the net Greeks of the *hedged* position (stock + options).

A market-conditions engine also reads trend, realized vs implied vol, IV rank,
the variance risk premium, VIX regime, skew and put/call flow into a hedge-
timing score so the user knows whether protection is currently cheap or rich.

All metrics are derived *generically from the legs*, so when the frontend lets
the user move a strike the position is repriced with identical logic. The full
option chain (with Greeks on every strike) is returned alongside the hedges so
that strike editing and repricing can happen instantly client-side.

Provider-agnostic: option data comes through the QuoteProvider abstraction in
quote_providers/ exactly like the box-spread engine.
"""

from __future__ import annotations

import asyncio
import math
import logging
from datetime import datetime, date
from typing import Optional, TYPE_CHECKING

from .quote_providers import get_provider, OptionChain, OptionQuote

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from ..models import User

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100  # shares per option contract (US equity options)
DEFAULT_RISK_FREE = 0.045


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Black-Scholes Greeks — used when the provider does not supply them (yfinance)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_greeks(spot: float, strike: float, dte_days: int, iv: float,
               right: str, r: float = DEFAULT_RISK_FREE) -> dict:
    """Per-share Black-Scholes Greeks for a single option.

    Returns delta, gamma, theta (per calendar day), vega (per 1 vol point).
    Falls back to intrinsic-only deltas if inputs are degenerate.
    """
    t = max(dte_days, 0) / 365.0
    if spot <= 0 or strike <= 0 or t <= 0 or iv <= 0:
        if right == "C":
            return {"delta": 1.0 if spot > strike else 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        return {"delta": -1.0 if spot < strike else 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    pdf_d1 = _norm_pdf(d1)
    gamma = pdf_d1 / (spot * iv * sqrt_t)
    vega = spot * pdf_d1 * sqrt_t / 100.0  # per 1% change in vol

    if right == "C":
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf_d1 * iv) / (2 * sqrt_t)
                 - r * strike * math.exp(-r * t) * _norm_cdf(d2)) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * pdf_d1 * iv) / (2 * sqrt_t)
                 + r * strike * math.exp(-r * t) * _norm_cdf(-d2)) / 365.0

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
    }


# ---------------------------------------------------------------------------
# Chain helpers
# ---------------------------------------------------------------------------

def _split_chain(chain: OptionChain):
    """Index option quotes by strike for calls and puts (keep the liquid one)."""
    calls: dict[float, OptionQuote] = {}
    puts: dict[float, OptionQuote] = {}
    for q in chain.quotes:
        target = calls if q.right == "C" else puts
        existing = target.get(q.strike)
        if existing is None or (q.oi + q.volume) > (existing.oi + existing.volume):
            target[q.strike] = q
    return calls, puts


def _nearest_strike(strikes: list[float], target: float,
                    side: str = "any") -> Optional[float]:
    """Closest available strike to *target*. side filters below/above."""
    if not strikes:
        return None
    if side == "below":
        cands = [s for s in strikes if s <= target]
    elif side == "above":
        cands = [s for s in strikes if s >= target]
    else:
        cands = strikes
    if not cands:
        cands = strikes
    return min(cands, key=lambda s: abs(s - target))


def _is_liquid(q: Optional[OptionQuote]) -> bool:
    # A quote is usable if it has a valid price. OI/volume are informational —
    # some providers (e.g. IBKR snapshots) don't populate them on tradeable
    # strikes, so requiring them would wrongly drop good legs.
    return bool(q) and q.mid > 0


def _quote_dict(q: OptionQuote, spot: float, dte: int) -> dict:
    """Serialize a single quote with Greeks (computing BS Greeks if absent)."""
    delta, gamma, theta, vega = q.delta, q.gamma, q.theta, q.vega
    if delta is None and q.iv:
        g = _bs_greeks(spot, q.strike, dte, q.iv, q.right)
        delta, gamma, theta, vega = g["delta"], g["gamma"], g["theta"], g["vega"]
    return {
        "strike": q.strike,
        "type": "CALL" if q.right == "C" else "PUT",
        "bid": round(q.bid, 4),
        "ask": round(q.ask, 4),
        "mid": round(q.mid, 4),
        "iv": round(q.iv * 100, 1) if q.iv else None,
        "oi": q.oi,
        "vol": q.volume,
        "delta": round(delta, 4) if delta is not None else None,
        "gamma": round(gamma, 6) if gamma is not None else None,
        "theta": round(theta, 4) if theta is not None else None,
        "vega": round(vega, 4) if vega is not None else None,
    }


def _leg(q: OptionQuote, action: str, contracts: int, expiration: str,
         spot: float, dte: int) -> dict:
    qd = _quote_dict(q, spot, dte)
    return {"action": action, "expiration": expiration, "contracts": contracts, **qd}


# ---------------------------------------------------------------------------
# Generic metrics — inferred straight from the legs (works for edited legs too)
# ---------------------------------------------------------------------------

def _net_premium(legs: list[dict]) -> float:
    """Net premium across the hedge in dollars (debit positive / credit negative)."""
    total = 0.0
    for leg in legs:
        sign = 1 if leg["action"] == "BUY" else -1
        total += sign * leg["mid"] * leg["contracts"] * CONTRACT_MULTIPLIER
    return total


def _net_greeks(shares: float, legs: list[dict]) -> dict:
    """Net Greeks of the hedged position in share-equivalent units."""
    net = {"delta": shares, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for leg in legs:
        sign = 1 if leg["action"] == "BUY" else -1
        qty = sign * leg["contracts"] * CONTRACT_MULTIPLIER
        for g in ("delta", "gamma", "theta", "vega"):
            v = leg.get(g)
            if v is not None:
                net[g] += qty * v
    return {
        "delta": round(net["delta"], 1),
        "gamma": round(net["gamma"], 4),
        "theta": round(net["theta"], 2),
        "vega": round(net["vega"], 2),
    }


def _pnl_at_price(price: float, shares: float, spot: float, legs: list[dict]) -> float:
    """Total position P&L vs today at *price* (options held to expiration)."""
    pnl = shares * (price - spot)
    for leg in legs:
        sign = 1 if leg["action"] == "BUY" else -1
        intr = (max(leg["strike"] - price, 0.0) if leg["type"] == "PUT"
                else max(price - leg["strike"], 0.0))
        pnl += sign * leg["contracts"] * CONTRACT_MULTIPLIER * (intr - leg["mid"])
    return pnl


def _infer_geometry(legs: list[dict]):
    """Infer the payoff geometry from the legs.

    Downside: floor (long put) and buffer_bottom (short put below it).
    Upside: a lone short call is a hard *cap*; a short call paired with a
    higher long call is instead a *give-up band* (gains forfeited between the
    two strikes, participation resumes above) — so there's no hard cap.
    """
    long_puts = [l for l in legs if l["type"] == "PUT" and l["action"] == "BUY"]
    short_puts = [l for l in legs if l["type"] == "PUT" and l["action"] == "SELL"]
    short_calls = [l for l in legs if l["type"] == "CALL" and l["action"] == "SELL"]
    long_calls = [l for l in legs if l["type"] == "CALL" and l["action"] == "BUY"]

    floor = max((l["strike"] for l in long_puts), default=None)
    buffer_bottom = None
    if floor is not None:
        belows = [l["strike"] for l in short_puts if l["strike"] < floor]
        buffer_bottom = max(belows) if belows else None

    cap = giveup_from = participate_above = None
    if short_calls:
        sc_min = min(l["strike"] for l in short_calls)
        higher_long = [l["strike"] for l in long_calls if l["strike"] > sc_min]
        if higher_long:
            giveup_from, participate_above = sc_min, min(higher_long)
        else:
            cap = sc_min

    covered = sum(l["contracts"] for l in long_puts) * CONTRACT_MULTIPLIER
    return floor, buffer_bottom, cap, giveup_from, participate_above, covered


def _chain_iv_arrays(calls: dict, puts: dict, strikes_all: list[float], spot: float):
    """(strikes, ivs, call_mids, put_mids) from the chain — put IV below spot,
    call IV above (the liquid, skew-bearing wing). Used by SVI/RND and Heston."""
    strikes, ivs, cmids, pmids = [], [], [], []
    for k in strikes_all:
        cq, pq = calls.get(k), puts.get(k)
        iv = (pq.iv if (k <= spot and pq and pq.iv) else
              cq.iv if (k > spot and cq and cq.iv) else
              (cq.iv if (cq and cq.iv) else (pq.iv if (pq and pq.iv) else None)))
        if not iv or iv <= 0:
            continue
        strikes.append(float(k)); ivs.append(float(iv))
        cmids.append(cq.mid if cq else None)
        pmids.append(pq.mid if pq else None)
    return strikes, ivs, cmids, pmids


def _build_rnd(calls: dict, puts: dict, strikes_all: list[float], spot: float, dte: int):
    """Calibrate an SVI smile and extract the Breeden-Litzenberger risk-neutral
    density from the live chain. Best-effort — returns None if it can't fit."""
    from .quant_service import calibrate_svi, risk_neutral_density
    strikes, ivs, cmids, pmids = _chain_iv_arrays(calls, puts, strikes_all, spot)
    if len(strikes) < 6:
        return None
    smile = calibrate_svi(strikes, ivs, spot, dte, call_mids=cmids, put_mids=pmids)
    if not smile:
        return None
    return risk_neutral_density(smile)


def _metrics_from_legs(legs: list[dict], shares: float, spot: float, dte: int,
                       notional: float, max_cost_pct: float) -> dict:
    """All headline metrics, derived purely from the legs. Reused on edits."""
    floor, buffer_bottom, cap, giveup_from, participate_above, covered = _infer_geometry(legs)
    covered = covered or shares

    net_cost = round(_net_premium(legs), 2)             # +debit / -credit
    cost_pct = (net_cost / notional * 100) if notional else 0.0
    annualized = cost_pct * (365.0 / dte) if dte > 0 else cost_pct
    prem_per_share = net_cost / shares if shares else 0.0

    # Honest worst case: stock to zero, options held to expiry. Captures the
    # tail that re-opens below a buffer, and uncovered shares on a partial hedge.
    max_loss = round(-_pnl_at_price(0.0, shares, spot, legs), 2)

    # Intrinsic vs extrinsic split — only the *extrinsic* (time value) decays.
    intrinsic_val = 0.0
    extrinsic_val = 0.0
    for leg in legs:
        sign = 1 if leg["action"] == "BUY" else -1
        intr = (max(leg["strike"] - spot, 0.0) if leg["type"] == "PUT"
                else max(spot - leg["strike"], 0.0))
        ext = max(leg["mid"] - intr, 0.0)
        qty = sign * leg["contracts"] * CONTRACT_MULTIPLIER
        intrinsic_val += qty * intr
        extrinsic_val += qty * ext

    budget = notional * max_cost_pct / 100 if notional else 0.0
    greeks = _net_greeks(shares, legs)

    metrics = {
        "net_cost": net_cost,
        "net_premium_per_share": round(prem_per_share, 4),
        "cost_pct_of_notional": round(cost_pct, 3),
        "annualized_cost_pct": round(annualized, 2),
        "is_credit": net_cost < 0,
        "within_budget": net_cost <= budget + 1e-6,
        "budget": round(budget, 2),
        "protection_floor": round(floor, 2) if floor is not None else None,
        "protection_floor_pct": round((floor - spot) / spot * 100, 2) if floor is not None else None,
        "buffer_bottom": round(buffer_bottom, 2) if buffer_bottom is not None else None,
        "buffer_bottom_pct": round((buffer_bottom - spot) / spot * 100, 2) if buffer_bottom is not None else None,
        "upside_cap": round(cap, 2) if cap is not None else None,
        "upside_cap_pct": round((cap - spot) / spot * 100, 2) if cap is not None else None,
        "giveup_from": round(giveup_from, 2) if giveup_from is not None else None,
        "giveup_from_pct": round((giveup_from - spot) / spot * 100, 2) if giveup_from is not None else None,
        "participate_above": round(participate_above, 2) if participate_above is not None else None,
        "participate_above_pct": round((participate_above - spot) / spot * 100, 2) if participate_above is not None else None,
        "max_loss": max_loss,
        "max_loss_pct": round(max_loss / notional * 100, 2) if notional else None,
        "net_greeks": greeks,
        "intrinsic_value": round(intrinsic_val, 2),
        "time_value": round(extrinsic_val, 2),       # the part exposed to decay
        "theta_per_day": greeks["theta"],            # $/day from time decay
        "covered_shares": int(covered),
        "uncovered_shares": int(max(0, shares - covered)),
        "tail_open_below": round(buffer_bottom, 2) if buffer_bottom is not None else None,
    }
    if net_cost > 0 and shares:
        metrics["upside_breakeven"] = round(spot + net_cost / shares, 2)
        metrics["upside_breakeven_pct"] = round((net_cost / shares) / spot * 100, 2)
    else:
        metrics["upside_breakeven"] = round(spot, 2)
        metrics["upside_breakeven_pct"] = 0.0
    return metrics


def _structure(hedge_id, name, style, plain, legs, shares, spot, dte,
               notional, max_cost_pct, notes) -> dict:
    return {
        "id": hedge_id,
        "name": name,
        "style": style,
        "plain": plain,
        "legs": legs,
        "notes": notes,
        **_metrics_from_legs(legs, shares, spot, dte, notional, max_cost_pct),
    }


# ---------------------------------------------------------------------------
# Structure builders — each returns a hedge dict or None
# ---------------------------------------------------------------------------

def _financing_put(sp, puts, spot, down_cap, long_put_strike):
    """Strike of the financing put sold at the downside cap, or None if disabled.

    Returns the nearest liquid strike to spot*(1 - down_cap/100), constrained to
    sit strictly below the long put (so it forms a protected band, not a clash).
    `down_cap <= 0` means the cap is off → no financing put.
    """
    if not down_cap or down_cap <= 0:
        return None
    below = [s for s in sp if s < long_put_strike and _is_liquid(puts.get(s))]
    if not below:
        return None
    k = _nearest_strike(below, spot * (1 - down_cap / 100))
    return k if (k and k < long_put_strike) else None

def _build_protective_put(calls, puts, sp, sc, shares, spot, dte, exp,
                          notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    kp = _nearest_strike(sp, spot * (1 - down / 100), "below")
    if kp is None or not _is_liquid(puts.get(kp)):
        return None
    legs = [_leg(puts[kp], "BUY", contracts, exp, spot, dte)]
    return _structure(
        "protective_put", "Protective Put", "Full downside insurance above the floor",
        f"Locks in a worst price of ${kp:.2f}. You keep all the upside and only pay the premium.",
        legs, shares, spot, dte, notional, max_cost_pct,
        ["Below the strike every $1 the stock loses is offset by the put.",
         "Keeps 100% of the upside — the cost is just the premium."],
    )


def _build_financed_floor(calls, puts, sp, sc, shares, spot, dte, exp,
                          notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    # Only offered when a downside cap is set: the pure protective put's twin,
    # with a financing put sold at the cap (insurance turns off below it).
    kp = _nearest_strike(sp, spot * (1 - down / 100), "below")
    if kp is None or not _is_liquid(puts.get(kp)):
        return None
    k_fin = _financing_put(sp, puts, spot, down_cap, kp)
    if not k_fin:
        return None
    legs = [_leg(puts[kp], "BUY", contracts, exp, spot, dte),
            _leg(puts[k_fin], "SELL", contracts, exp, spot, dte)]
    return _structure(
        "financed_floor", "Financed Floor (Put Spread)", "Protective put with the deep tail sold to finance it",
        f"You take the first {down:.0f}% (down to ${kp:.2f}), stay protected down to ${k_fin:.2f}, then "
        f"are exposed again below it — the sold put recovers premium where you don't expect to fall.",
        legs, shares, spot, dte, notional, max_cost_pct,
        [f"Protected between ${kp:.2f} and ${k_fin:.2f}; the sold ${k_fin:.2f} put cuts the cost.",
         f"Below ${k_fin:.2f} protection stops — you've judged a fall that deep unlikely.",
         "Cheaper than the plain protective put, in exchange for giving up crash-tail coverage."],
    )


def _build_deep_itm_put(calls, puts, sp, sc, shares, spot, dte, exp,
                        notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    # In-the-money put (strike ~10% ABOVE spot): delta near -1, almost pure
    # intrinsic value, so theta decay is minimal.
    kp = _nearest_strike(sp, spot * 1.10, "above")
    if kp is None or not _is_liquid(puts.get(kp)):
        return None
    legs = [_leg(puts[kp], "BUY", contracts, exp, spot, dte)]
    return _structure(
        "deep_itm_put", "Deep ITM Put (Low Decay)", "High floor, minimal time decay",
        f"Locks in a high floor of ${kp:.2f} with very little time value to decay.",
        legs, shares, spot, dte, notional, max_cost_pct,
        ["Delta near -1 and tiny extrinsic value, so theta bleed is minimal.",
         "Capital-intensive: you pay the intrinsic value (recoverable), not just premium.",
         "Removing nearly all downside can trigger a constructive sale — check the tax panel."],
    )


def _build_put_spread(calls, puts, sp, sc, shares, spot, dte, exp,
                      notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    # Buffer: long put at-the-money so there's ZERO loss for the first `buf`%,
    # short put at -buf% where the buffer is exhausted. `buf <= 0` means the user
    # doesn't want a buffer-style hedge — skip it.
    if buf <= 0:
        return None
    k_high = _nearest_strike(sp, spot, "any")
    if not k_high or not _is_liquid(puts.get(k_high)):
        return None
    below = [s for s in sp if s < k_high and _is_liquid(puts.get(s))]
    if not below:
        return None
    k_low = _nearest_strike(below, spot * (1 - buf / 100))
    if not k_low or k_low >= k_high:
        return None
    buf_pct = (k_high - k_low) / spot * 100
    legs = [_leg(puts[k_high], "BUY", contracts, exp, spot, dte),
            _leg(puts[k_low], "SELL", contracts, exp, spot, dte)]
    return _structure(
        "put_spread", "Buffered Put Spread", "Absorb the first part of the drop",
        f"No loss for the first {buf_pct:.0f}% the stock falls (down to ${k_low:.2f}); "
        f"below that you only lose the part beyond the buffer.",
        legs, shares, spot, dte, notional, max_cost_pct,
        [f"The first {buf_pct:.0f}% of any decline is fully absorbed — you lose nothing.",
         f"Below ${k_low:.2f} the buffer is spent and losses resume (cushioned by the buffer width)."],
    )


def _build_collar(calls, puts, sp, sc, shares, spot, dte, exp,
                  notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    # A collar is *defined* by its cap; up <= 0 means "unlimited upside" → no collar.
    if up <= 0:
        return None
    kp = _nearest_strike(sp, spot * (1 - down / 100), "below")
    kc = _nearest_strike(sc, spot * (1 + up / 100), "above")
    if not kp or not kc or not _is_liquid(puts.get(kp)) or not _is_liquid(calls.get(kc)):
        return None
    legs = [_leg(puts[kp], "BUY", contracts, exp, spot, dte),
            _leg(calls[kc], "SELL", contracts, exp, spot, dte)]
    return _structure(
        "collar", "Collar", "Put financed by a covered call",
        f"Protected below ${kp:.2f}, capped above ${kc:.2f}. The call pays for most of the put.",
        legs, shares, spot, dte, notional, max_cost_pct,
        ["The short call funds the put — often close to zero net cost.",
         "You give up gains above the cap; lower the upside % to fund more of the put."],
    )


def _build_financed_floor_collar(calls, puts, sp, sc, shares, spot, dte, exp,
                                 notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    # Only offered when a downside cap is set: the pure collar's twin, with a
    # financing put sold at the cap on top of the covered call. Needs an upside cap.
    if up <= 0:
        return None
    kp = _nearest_strike(sp, spot * (1 - down / 100), "below")
    kc = _nearest_strike(sc, spot * (1 + up / 100), "above")
    if not kp or not kc or not _is_liquid(puts.get(kp)) or not _is_liquid(calls.get(kc)):
        return None
    k_fin = _financing_put(sp, puts, spot, down_cap, kp)
    if not k_fin:
        return None
    legs = [_leg(puts[kp], "BUY", contracts, exp, spot, dte),
            _leg(calls[kc], "SELL", contracts, exp, spot, dte),
            _leg(puts[k_fin], "SELL", contracts, exp, spot, dte)]
    return _structure(
        "financed_floor_collar", "Financed-Floor Collar", "Capped collar with the deep tail sold off too",
        f"Protected from ${kp:.2f} down to ${k_fin:.2f} and capped above ${kc:.2f}; both the call and the "
        f"sold ${k_fin:.2f} put pay for the protection — often a net credit.",
        legs, shares, spot, dte, notional, max_cost_pct,
        [f"Protected band ${k_fin:.2f}–${kp:.2f}; capped above ${kc:.2f}.",
         "The covered call and the deep sold put both finance the floor — cheapest protected band.",
         f"Below ${k_fin:.2f} protection stops; above ${kc:.2f} gains are given up."],
    )


def _build_put_spread_collar(calls, puts, sp, sc, shares, spot, dte, exp,
                             notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    # Dual-direction: forgo the first `giveup`% of gains to fund zero loss on
    # the first `buf`% of the drop. Needs both a buffer to build and upside to
    # fund it; if either is zero the structure doesn't apply.
    if giveup <= 0 or buf <= 0:
        return None
    # Downside buffer — long ATM put, short put at -buf%.
    k_lp = _nearest_strike(sp, spot, "any")
    if not k_lp or not _is_liquid(puts.get(k_lp)):
        return None
    below = [s for s in sp if s < k_lp and _is_liquid(puts.get(s))]
    if not below:
        return None
    k_sp = _nearest_strike(below, spot * (1 - buf / 100))
    if not k_sp or k_sp >= k_lp:
        return None
    # Upside give-up — short call at first OTM strike, long call at +giveup%.
    k_sc = _nearest_strike([s for s in sc if s >= spot], spot, "above")
    if not k_sc or not _is_liquid(calls.get(k_sc)):
        return None
    above = [s for s in sc if s > k_sc and _is_liquid(calls.get(s))]
    if not above:
        return None
    k_lc = _nearest_strike(above, spot * (1 + giveup / 100))
    if not k_lc or k_lc <= k_sc:
        return None
    buf_pct = (k_lp - k_sp) / spot * 100
    giveup_pct = (k_lc - k_sc) / spot * 100
    legs = [_leg(puts[k_lp], "BUY", contracts, exp, spot, dte),
            _leg(puts[k_sp], "SELL", contracts, exp, spot, dte),
            _leg(calls[k_sc], "SELL", contracts, exp, spot, dte),
            _leg(calls[k_lc], "BUY", contracts, exp, spot, dte)]
    return _structure(
        "put_spread_collar", "Buffered Collar (Dual-Direction)", "Forgo early gains to buffer early losses",
        f"Give up the first {giveup_pct:.0f}% of gains (up to ${k_lc:.2f}) to take zero loss on the "
        f"first {buf_pct:.0f}% of the fall (down to ${k_sp:.2f}). Often near zero cost.",
        legs, shares, spot, dte, notional, max_cost_pct,
        [f"No loss for the first {buf_pct:.0f}% down; in exchange you forfeit gains up to ${k_lc:.2f}.",
         "Self-funding: the surrendered upside pays for the downside buffer.",
         f"Above ${k_lc:.2f} you participate again; below ${k_sp:.2f} the buffer is spent."],
    )


def _build_buffered_covered_call(calls, puts, sp, sc, shares, spot, dte, exp,
                                 notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    # Buffer + hard cap: long ATM put + short put at -buf% (the no-loss buffer),
    # financed by a covered call sold at the +up% upside cap. Unlike the
    # dual-direction collar there's NO long call, so gains are hard-capped at
    # the call strike — but the premium fully (often more than) pays for the
    # put spread, making this the cheapest "no loss for the first X%" structure.
    # Needs both a buffer to build and an upside cap (the covered call) to fund it.
    if buf <= 0 or up <= 0:
        return None
    k_lp = _nearest_strike(sp, spot, "any")
    if not k_lp or not _is_liquid(puts.get(k_lp)):
        return None
    below = [s for s in sp if s < k_lp and _is_liquid(puts.get(s))]
    if not below:
        return None
    k_sp = _nearest_strike(below, spot * (1 - buf / 100))
    if not k_sp or k_sp >= k_lp:
        return None
    kc = _nearest_strike(sc, spot * (1 + up / 100), "above")
    if not kc or not _is_liquid(calls.get(kc)):
        return None
    buf_pct = (k_lp - k_sp) / spot * 100
    legs = [_leg(puts[k_lp], "BUY", contracts, exp, spot, dte),
            _leg(puts[k_sp], "SELL", contracts, exp, spot, dte),
            _leg(calls[kc], "SELL", contracts, exp, spot, dte)]
    return _structure(
        "buffered_covered_call", "Put-Spread + Covered Call", "No-loss buffer paid for by a capped upside",
        f"No loss for the first {buf_pct:.0f}% down (to ${k_sp:.2f}); the covered call caps gains at "
        f"${kc:.2f} and pays for the buffer.",
        legs, shares, spot, dte, notional, max_cost_pct,
        [f"The first {buf_pct:.0f}% of any decline is fully absorbed — you lose nothing.",
         f"Gains are hard-capped at ${kc:.2f}; the call premium funds (often over-funds) the put spread.",
         f"Below ${k_sp:.2f} the buffer is spent and losses resume, cushioned by the buffer width.",
         "Selling the call against the stock is typically a qualified covered call when OTM — see the tax panel."],
    )


def _build_tail_hedge(calls, puts, sp, sc, shares, spot, dte, exp,
                      notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    kp = _nearest_strike(sp, spot * 0.75, "below")  # ~25% OTM
    if kp is None or not _is_liquid(puts.get(kp)):
        return None
    legs = [_leg(puts[kp], "BUY", contracts, exp, spot, dte)]
    return _structure(
        "tail_hedge", "Tail Hedge (Deep OTM Put)", "Convex crash protection",
        f"Cheap insurance at ${kp:.2f} that only pays off in a sharp crash.",
        legs, shares, spot, dte, notional, max_cost_pct,
        ["Tiny premium, large convex payoff if the underlying gaps down hard.",
         "Does nothing in a mild dip — pure protection against a crash."],
    )


def _build_giveup_funded_floor(calls, puts, sp, sc, shares, spot, dte, exp,
                               notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    # Give-up funded floor: absorb the first `down`% loss as a deductible, then
    # fully protected below. Financed by forgoing the first `giveup`% of gains
    # (call spread: short ATM call + long call at +giveup%). Participation resumes
    # above the long call — no hard cap. Self-financing when giveup ≈ down.
    if giveup <= 0 or down <= 0:
        return None
    kp = _nearest_strike(sp, spot * (1 - down / 100), "below")
    if not kp or not _is_liquid(puts.get(kp)):
        return None
    k_sc = _nearest_strike([s for s in sc if s >= spot], spot, "above")
    if not k_sc or not _is_liquid(calls.get(k_sc)):
        return None
    above = [s for s in sc if s > k_sc and _is_liquid(calls.get(s))]
    if not above:
        return None
    k_lc = _nearest_strike(above, spot * (1 + giveup / 100))
    if not k_lc or k_lc <= k_sc:
        return None
    floor_pct = (spot - kp) / spot * 100
    giveup_pct = (k_lc - k_sc) / spot * 100
    legs = [_leg(puts[kp], "BUY", contracts, exp, spot, dte),
            _leg(calls[k_sc], "SELL", contracts, exp, spot, dte),
            _leg(calls[k_lc], "BUY", contracts, exp, spot, dte)]
    return _structure(
        "giveup_funded_floor", "Give-up Funded Floor", "Doomsday protection, call-spread financed",
        f"Protected below ${kp:.2f} (−{floor_pct:.0f}% floor). "
        f"You absorb the first {floor_pct:.0f}% loss and forgo gains up to ${k_lc:.2f} (+{giveup_pct:.0f}%); "
        f"participation resumes above — near zero net cost.",
        legs, shares, spot, dte, notional, max_cost_pct,
        [f"Full protection below ${kp:.2f}; you absorb losses between spot and ${kp:.2f}.",
         f"Give up gains from spot to ${k_lc:.2f} (+{giveup_pct:.0f}%); above that you participate again.",
         "Self-financing: the surrendered upside call spread pays for the deep protective put."],
    )


def _build_tailored(calls, puts, sp, sc, shares, spot, dte, exp,
                    notional, down, up, buf, giveup, contracts, max_cost_pct, down_cap=0.0):
    """The one structure that honors EVERY preference the user set.

    Assembles legs from each non-zero input — buffer, floor, downside cap,
    upside cap and give-up — into a single coherent payoff. Always surfaced
    first as the "matches your inputs" pick. Returns None only when nothing
    can be built (all prefs zero or no liquid strikes). Mutually-exclusive
    inputs (e.g. a floor shallower than the buffer) are reported in `skipped`.
    """
    legs: list[dict] = []
    used: list[str] = []
    skipped: list[str] = []

    def _below(k): return (spot - k) / spot * 100   # % below spot
    def _above(k): return (k - spot) / spot * 100   # % above spot

    # ---------------- Downside ----------------
    buf_bottom = None  # where protection currently stops (for floor-depth check)
    if buf > 0:
        k_atm = _nearest_strike(sp, spot, "any")
        below = [s for s in sp if k_atm and s < k_atm and _is_liquid(puts.get(s))]
        k_buf = _nearest_strike(below, spot * (1 - buf / 100)) if below else None
        if k_atm and _is_liquid(puts.get(k_atm)) and k_buf and k_buf < k_atm:
            legs += [_leg(puts[k_atm], "BUY", contracts, exp, spot, dte),
                     _leg(puts[k_buf], "SELL", contracts, exp, spot, dte)]
            used.append(f"no loss for the first {_below(k_buf):.0f}% (to ${k_buf:.2f})")
            buf_bottom = k_buf

    # The floor is the core protection and is ALWAYS built. down == 0 means
    # "floor at today's price" (ATM, no deductible) — NOT "no floor". down > 0
    # means you absorb the first `down`% as a deductible before protection starts.
    kp = _nearest_strike(sp, spot * (1 - down / 100), "below")
    if kp and _is_liquid(puts.get(kp)) and (buf_bottom is None or kp < buf_bottom):
        legs.append(_leg(puts[kp], "BUY", contracts, exp, spot, dte))
        used.append(f"protected from today's price (${kp:.2f})" if down <= 0
                    else f"protected below ${kp:.2f} (−{_below(kp):.0f}% floor)")
        if down_cap > 0:
            k_fin = _financing_put(sp, puts, spot, down_cap, kp)
            if k_fin:
                legs.append(_leg(puts[k_fin], "SELL", contracts, exp, spot, dte))
                used.append(f"protection stops at ${k_fin:.2f} (−{_below(k_fin):.0f}%, sold to finance)")
            else:
                skipped.append("downside cap (no liquid strike)")
    elif kp and buf_bottom is not None and kp >= buf_bottom:
        skipped.append("floor (shallower than the buffer already set)")

    # ---------------- Upside ----------------
    k_gu = None  # give-up long-call strike (an upside cap must sit above it)
    if giveup > 0:
        k_sc = _nearest_strike([s for s in sc if s >= spot], spot, "above")
        above = [s for s in sc if k_sc and s > k_sc and _is_liquid(calls.get(s))]
        k_gu = _nearest_strike(above, spot * (1 + giveup / 100)) if above else None
        if k_sc and _is_liquid(calls.get(k_sc)) and k_gu and k_gu > k_sc:
            legs += [_leg(calls[k_sc], "SELL", contracts, exp, spot, dte),
                     _leg(calls[k_gu], "BUY", contracts, exp, spot, dte)]
            used.append(f"give up gains to ${k_gu:.2f} (+{_above(k_gu):.0f}%), then participate")
        else:
            k_gu = None

    if up > 0:
        target = spot * (1 + up / 100)
        cands = [s for s in sc if s >= target and _is_liquid(calls.get(s))
                 and (k_gu is None or s > k_gu)]
        k_cap = _nearest_strike(cands, target) if cands else None
        if k_cap:
            legs.append(_leg(calls[k_cap], "SELL", contracts, exp, spot, dte))
            used.append(f"capped above ${k_cap:.2f} (+{_above(k_cap):.0f}%)")
        else:
            skipped.append("upside cap (would clash with the give-up band)")

    if not legs:
        return None

    plain = "Your hedge — " + "; ".join(used) + "."
    if skipped:
        plain += " Couldn't combine: " + ", ".join(skipped) + "."

    notes = [f"Built from every preference you set: {', '.join(used)}."]
    if skipped:
        notes.append("Left out (mutually exclusive with the above): " + ", ".join(skipped) + ".")
    notes.append("The alternatives below are simpler single-purpose variants of this.")

    return _structure(
        "tailored", "Tailored Hedge", "Every preference combined",
        plain, legs, shares, spot, dte, notional, max_cost_pct, notes,
    )


_BUILDERS = [
    _build_tailored,
    _build_protective_put, _build_financed_floor, _build_deep_itm_put, _build_put_spread,
    _build_collar, _build_financed_floor_collar, _build_buffered_covered_call,
    _build_put_spread_collar, _build_giveup_funded_floor, _build_tail_hedge,
]


# ---------------------------------------------------------------------------
# Risk notes
# ---------------------------------------------------------------------------

def _build_risks() -> list[dict]:
    return [
        {"category": "Cost Drag (Theta)",
         "description": "Long options bleed time value every day; rolled repeatedly, premiums compound into a real drag on returns.",
         "severity": "Medium",
         "mitigation": "Prefer financed structures (collars, put spreads) and size the protected horizon to your real risk window."},
        {"category": "Basis / Coverage Risk",
         "description": "Contracts cover shares in lots of 100, so residual shares or a partial hedge ratio leave a slice unhedged.",
         "severity": "Low",
         "mitigation": "Watch covered vs uncovered shares; raise the hedge ratio to close the gap."},
        {"category": "Capped Upside (Collars)",
         "description": "Selling a call to fund the put forfeits gains above the cap; in a strong rally the call is assigned.",
         "severity": "Medium",
         "mitigation": "Set the cap above your price target, or roll the call up as the stock rises."},
        {"category": "Tail Gap Below the Buffer",
         "description": "Put spreads and put-spread collars stop protecting below the lower strike, re-exposing you in a deep crash.",
         "severity": "High",
         "mitigation": "Overlay a cheap deep-OTM tail put, or use a full protective put for crash cover."},
        {"category": "Liquidity & Assignment",
         "description": "Thin strikes widen fills; American-style short options can be assigned early, especially around dividends.",
         "severity": "Medium",
         "mitigation": "Trade liquid strikes (OI/volume shown per leg) and watch short legs near ex-dividend."},
    ]


# ---------------------------------------------------------------------------
# Market conditions / hedge timing — "is now a good time to hedge?"
# ---------------------------------------------------------------------------

def _atm_iv(puts: dict, calls: dict, spot: float) -> Optional[float]:
    """Average of the nearest-the-money put and call implied vols (decimal)."""
    ivs = []
    for book in (puts, calls):
        if not book:
            continue
        k = min(book.keys(), key=lambda s: abs(s - spot))
        q = book.get(k)
        if q and q.iv:
            ivs.append(q.iv)
    return sum(ivs) / len(ivs) if ivs else None


def _compute_skew(puts: dict, calls: dict, spot: float) -> Optional[float]:
    """25-delta-ish vertical skew: ~10% OTM put IV minus ~10% OTM call IV (decimal).

    Positive = puts richer than calls (the market is paying up for downside).
    """
    if not puts or not calls:
        return None
    kp = min(puts.keys(), key=lambda s: abs(s - spot * 0.90))
    kc = min(calls.keys(), key=lambda s: abs(s - spot * 1.10))
    pq, cq = puts.get(kp), calls.get(kc)
    if pq and cq and pq.iv and cq.iv:
        return pq.iv - cq.iv
    return None


def _pc_ratios(puts: dict, calls: dict):
    """Put/call open-interest and volume ratios across the chain."""
    put_oi = sum(q.oi for q in puts.values())
    call_oi = sum(q.oi for q in calls.values())
    put_vol = sum(q.volume for q in puts.values())
    call_vol = sum(q.volume for q in calls.values())
    oi_ratio = (put_oi / call_oi) if call_oi else None
    vol_ratio = (put_vol / call_vol) if call_vol else None
    return oi_ratio, vol_ratio


def _percentile_rank(series, value) -> Optional[float]:
    try:
        s = series.dropna()
        if len(s) == 0 or value is None:
            return None
        return float((s < value).mean())
    except Exception:  # noqa: BLE001
        return None


def _compute_market_conditions(ticker: str, spot: float, atm_iv: Optional[float], dte: int,
                               skew: Optional[float], pc_oi: Optional[float], pc_vol: Optional[float],
                               benchmark: str = "SPY") -> dict:
    """Quant read on whether conditions favor hedging now (cheap) or waiting.

    Blends trend, realized-vs-implied vol, the VIX regime, skew and put/call
    flow into a 0-100 timing score (higher = cheaper / better moment to hedge),
    a verdict, plain-English signals, and the structure family that fits.
    Also returns regression beta vs the benchmark (one download, reused).
    """
    out: dict = {"available": False, "beta": None}
    try:
        import math as _m
        import pandas as _pd
        import yfinance as yf
        clean = ticker.lstrip("^")
        # auto_adjust=False keeps BOTH raw Close and Adj Close, and we use each
        # where it's correct:
        #   • raw Close  → price-level reads (1W/1M trend, drawdown, 52w high) so they
        #     match the price change Yahoo/brokerages show, anchored to the live spot.
        #     (auto_adjust=True back-adjusts for dividends → a total return that reads
        #     systematically less negative, e.g. SPY −0.9% vs the true −1.1% price move.)
        #   • Adj Close  → return/vol reads (realized vol, beta) so a dividend ex-date
        #     doesn't inject a spurious one-day jump into the volatility.
        raw = yf.download([clean, "^VIX", "^VIX3M", benchmark], period="1y",
                          auto_adjust=False, progress=False)
        if raw is None or raw.empty:
            return out
        fields = raw.columns.get_level_values(0)
        close = raw["Close"]
        adj = raw["Adj Close"] if "Adj Close" in fields else close
        if clean not in close:
            return out
        px = close[clean].dropna()                              # raw price levels
        apx = adj[clean].dropna() if clean in adj else px        # dividend-adjusted
        if len(px) < 30:
            return out

        rets = apx.pct_change().dropna()                         # dividend-neutral returns
        ann = _m.sqrt(252)
        # Anchor every "current price" calc to the live spot the user actually sees,
        # not the (possibly stale / prior-close) last bar of the 1y download — else
        # the headline price and the trend %/drawdown silently disagree.
        last = float(spot) if spot and spot > 0 else float(px.iloc[-1])
        rv10 = float(rets.tail(10).std() * ann) if len(rets) >= 10 else None
        rv20 = float(rets.tail(20).std() * ann)
        rv60 = float(rets.tail(60).std() * ann) if len(rets) >= 60 else None
        roll_rv = (rets.rolling(20).std() * ann).dropna()
        rv_pctile = _percentile_rank(roll_rv, rv20)
        # Trend windows are *calendar* (1 week, 1 month) and anchored to the live
        # spot, so they line up with the "1W / 1M %" Yahoo & brokerages display —
        # a 20-trading-day return reads materially different and confuses users.
        def _ret_back(days: int):
            prior = px[px.index <= (px.index[-1] - _pd.Timedelta(days=days))]
            ref = float(prior.iloc[-1]) if len(prior) else None
            return (last / ref - 1) if ref else None
        ret_1w = _ret_back(7)
        ret_1m = _ret_back(30)
        ma50 = float(px.tail(50).mean())
        ma200 = float(px.tail(200).mean()) if len(px) >= 200 else None
        high_52 = float(max(float(px.max()), last))   # live spot can be a fresh high
        drawdown = (last / high_52 - 1) if high_52 else None

        vix = close["^VIX"].dropna() if "^VIX" in close else None
        vix_last = float(vix.iloc[-1]) if vix is not None and len(vix) else None
        vix_pctile = _percentile_rank(vix, vix_last) if vix is not None else None

        # VIX term structure: VIX / VIX3M. < 1 = contango (calm, short-dated vol
        # cheap → a normal window to hedge); > 1 = backwardation (acute near-term
        # stress → hedges rich, you may be late). The most-watched pro timing gauge.
        vix3m = close["^VIX3M"].dropna() if "^VIX3M" in close else None
        vix3m_last = float(vix3m.iloc[-1]) if vix3m is not None and len(vix3m) else None
        ts_ratio = (vix_last / vix3m_last) if (vix_last and vix3m_last) else None

        # Beta vs benchmark (reuse this download; dividend-neutral returns).
        beta = None
        if benchmark in adj:
            bret = adj[benchmark].pct_change().dropna()
            joined = rets.align(bret, join="inner")
            if len(joined[0]) > 30:
                var = float(joined[1].var())
                if var:
                    beta = round(float(joined[0].cov(joined[1]) / var), 2)
        out["beta"] = beta

        iv = atm_iv
        vrp = (iv - rv20) if iv is not None else None
        iv_rank = _percentile_rank(roll_rv, iv) if iv is not None else None

        def clamp(x): return max(0.0, min(1.0, x))
        s_ivrank = clamp(1 - iv_rank) if iv_rank is not None else 0.5
        s_vrp = clamp((0.15 - vrp) / 0.15) if vrp is not None else 0.5
        s_calm = clamp(1 - vix_pctile) if vix_pctile is not None else 0.5
        # Deeper contango (ratio < 1) = cheaper / better moment to hedge.
        s_term = clamp(0.5 + (1.0 - ts_ratio) / 0.25) if ts_ratio is not None else 0.5
        s_gains = clamp(1 + drawdown / 0.20) if drawdown is not None else 0.5
        s_mom = clamp(0.5 + (ret_1m or 0) * 4)
        s_protect = 0.6 * s_gains + 0.4 * s_mom
        s_skew = clamp((0.06 - skew) / 0.06) if skew is not None else 0.5
        score = round(100 * (0.24 * s_ivrank + 0.18 * s_vrp + 0.15 * s_term
                             + 0.10 * s_calm + 0.21 * s_protect + 0.12 * s_skew))

        if score >= 65:
            verdict, headline = "Favorable", "Protection looks historically cheap — a good window to hedge."
        elif score >= 45:
            verdict, headline = "Fair", "Mixed conditions — financed structures balance cost and protection."
        else:
            verdict, headline = "Expensive", "Hedging is rich right now — finance it (collar / spread) or consider waiting."

        rich_vol = (iv_rank is not None and iv_rank > 0.6) or (vrp is not None and vrp > 0.06)
        cheap_vol = iv_rank is not None and iv_rank < 0.4
        steep_skew = skew is not None and skew > 0.05
        if cheap_vol:
            rec = ["protective_put", "tail_hedge"]
        elif rich_vol:
            rec = ["collar", "buffered_covered_call", "put_spread_collar"]
        else:
            rec = ["collar", "buffered_covered_call", "put_spread"]
        if steep_skew and "put_spread" not in rec:
            rec.append("put_spread")
        # giveup_funded_floor is always surfaced as recommended when a give-up is set
        # (the caller passes upside_giveup; we can't read it here, so it's added downstream)

        def pct(v): return round(v * 100, 0) if v is not None else None

        # Plain-English signal cards. tone is from the hedger's view: "good" = cheaper/easier to hedge.
        signals = []
        if iv_rank is not None:
            signals.append({"label": "Implied vs 1-Yr Range", "value": f"{pct(iv_rank):.0f}%",
                            "tone": "good" if iv_rank < 0.4 else "bad" if iv_rank > 0.6 else "neutral",
                            "read": "Implied sits low vs how much this name has actually moved over the past year — protection is cheap." if iv_rank < 0.4
                                    else "Implied is above most of the past year's actual moves — you'd pay up for protection." if iv_rank > 0.6
                                    else "Implied is mid-range vs the past year's realized moves."})
        if vrp is not None:
            signals.append({"label": "Variance Risk Premium", "value": f"{vrp * 100:+.1f} pts",
                            "tone": "good" if vrp < 0.02 else "bad" if vrp > 0.06 else "neutral",
                            "read": "Implied is below realized — options look cheap vs how much the stock is actually moving." if vrp < 0.0
                                    else "Implied barely exceeds realized — you're not overpaying." if vrp < 0.02
                                    else "Implied runs well above realized — you pay a fear premium." if vrp > 0.06
                                    else "Normal premium of implied over realized."})
        if skew is not None:
            signals.append({"label": "Put/Call Skew", "value": f"{skew * 100:+.1f} pts",
                            "tone": "good" if skew < 0.02 else "bad" if skew > 0.05 else "neutral",
                            "read": "Puts priced near calls — downside protection is cheap." if skew < 0.02
                                    else "Puts bid well over calls — crash protection is pricey." if skew > 0.05
                                    else "Typical downside skew."})
        if vix_last is not None:
            # Absolute VIX bands the desk reads at a glance: <20 calm, 20-30 elevated,
            # 30-40 high, >40 the rare extreme.
            band = ("calm" if vix_last < 20 else "elevated" if vix_last < 30
                    else "high" if vix_last < 40 else "extreme")
            signals.append({"label": "VIX", "value": f"{vix_last:.1f}"
                            + (f" ({pct(vix_pctile):.0f}%ile)" if vix_pctile is not None else ""),
                            "tone": "good" if (vix_pctile or 0.5) < 0.4 else "bad" if (vix_pctile or 0.5) > 0.7 else "neutral",
                            "read": f"Calm regime (VIX {band}) — vol is cheap." if (vix_pctile or 0.5) < 0.4
                                    else f"Elevated fear (VIX {band}) — vol is bid." if (vix_pctile or 0.5) > 0.7
                                    else f"Average regime (VIX {band})."})
        if ts_ratio is not None:
            contango = ts_ratio < 0.97
            backward = ts_ratio > 1.0
            signals.append({"label": "VIX Term Structure", "value": f"{ts_ratio:.2f}×",
                            "tone": "good" if contango else "bad" if backward else "neutral",
                            "read": "Contango — short-dated vol cheap vs longer-dated; a calm, normal window to put hedges on." if contango
                                    else "Inverted (backwardation) — acute near-term stress; hedges are rich and you may be late." if backward
                                    else "Term structure flat — a transition regime; size in gradually."})
        if ret_1m is not None:
            near_high = drawdown is not None and drawdown > -0.05
            signals.append({"label": "1-Month Trend", "value": f"{ret_1m * 100:+.1f}%",
                            "tone": "good" if (ret_1m > 0 and near_high) else "neutral" if ret_1m > -0.05 else "bad",
                            "read": "Near highs after a run — gains worth protecting." if (ret_1m > 0 and near_high)
                                    else "Already well off the highs — hedging now locks in the drop." if (drawdown is not None and drawdown < -0.10)
                                    else "Sideways to mildly trending."})
        if pc_oi is not None:
            signals.append({"label": "Put/Call OI", "value": f"{pc_oi:.2f}",
                            "tone": "neutral",
                            "read": "Heavy put positioning — hedging demand already elevated." if pc_oi > 1.3
                                    else "Light put positioning — little crowd hedging." if pc_oi < 0.7
                                    else "Balanced positioning."})

        out.update({
            "available": True, "score": score, "verdict": verdict, "headline": headline,
            "recommended_ids": rec, "signals": signals,
            "ret_1w_pct": round(ret_1w * 100, 2) if ret_1w is not None else None,
            "ret_1m_pct": round(ret_1m * 100, 2) if ret_1m is not None else None,
            "vs_ma50_pct": round((last / ma50 - 1) * 100, 2) if ma50 else None,
            "vs_ma200_pct": round((last / ma200 - 1) * 100, 2) if ma200 else None,
            "drawdown_52w_pct": round(drawdown * 100, 2) if drawdown is not None else None,
            "rv10_pct": round(rv10 * 100, 1) if rv10 is not None else None,
            "rv20_pct": round(rv20 * 100, 1),
            "rv60_pct": round(rv60 * 100, 1) if rv60 is not None else None,
            "rv_pctile": pct(rv_pctile),
            "atm_iv_pct": round(iv * 100, 1) if iv else None, "iv_rank": pct(iv_rank),
            "vrp_pts": round(vrp * 100, 1) if vrp is not None else None,
            "vix": round(vix_last, 1) if vix_last else None, "vix_pctile": pct(vix_pctile),
            "vix3m": round(vix3m_last, 1) if vix3m_last else None,
            "term_structure_ratio": round(ts_ratio, 3) if ts_ratio is not None else None,
            "skew_pts": round(skew * 100, 1) if skew is not None else None,
            "pc_oi_ratio": round(pc_oi, 2) if pc_oi is not None else None,
            "pc_vol_ratio": round(pc_vol, 2) if pc_vol is not None else None,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Market conditions failed for {ticker}: {exc}")
    return out


# ---------------------------------------------------------------------------
# Beta-weighted index put — institutional macro overlay
# ---------------------------------------------------------------------------

def _pick_expiration(expirations: list[str], horizon_days: int, today: date):
    scored = []
    for exp_str in expirations:
        try:
            d = (datetime.strptime(exp_str, "%Y-%m-%d").date() - today).days
        except Exception:  # noqa: BLE001
            continue
        if d >= 5:
            scored.append((abs(d - horizon_days), d, exp_str))
    if not scored:
        return None, None
    scored.sort()
    return scored[0][2], scored[0][1]


async def _build_index_overlay(provider, benchmark: str, beta: Optional[float], shares: float,
                               spot: float, notional: float, horizon_days: int,
                               protection_pct: float, hedge_ratio: float, today: date) -> Optional[dict]:
    """Hedge the holding's market risk with beta-weighted index puts.

    Sizes the hedge to beta x notional and buys index puts ~protection_pct OTM.
    Carries basis risk (idiosyncratic moves are unhedged) but is cheaper, more
    liquid, and (for broad indices) gets Section 1256 60/40 tax treatment.
    """
    if not beta or beta <= 0:
        return None
    try:
        idx_u = await provider.get_underlying_price(benchmark)
        idx_spot = idx_u.price
        exps = await provider.get_option_expirations(benchmark)
        chosen, dte = _pick_expiration(exps or [], horizon_days, today)
        if not chosen:
            return None
        chain = await provider.get_option_chain(benchmark, chosen)
        _icalls, iputs = _split_chain(chain)
        if not iputs:
            return None
        sp = sorted(iputs.keys())
        kp = _nearest_strike([s for s in sp if _is_liquid(iputs.get(s))],
                             idx_spot * (1 - protection_pct / 100), "below")
        if not kp or not _is_liquid(iputs.get(kp)):
            return None
        hedge_notional = beta * notional * hedge_ratio
        contracts = max(1, round(hedge_notional / (idx_spot * CONTRACT_MULTIPLIER)))
        leg = _leg(iputs[kp], "BUY", contracts, chosen, idx_spot, dte)
        cost = round(leg["mid"] * contracts * CONTRACT_MULTIPLIER, 2)

        # Beta-translated scenarios: a holding move m implies an index move m/beta.
        scenarios = []
        for m in (-0.30, -0.20, -0.10, -0.05, 0.0, 0.10):
            idx_price = idx_spot * (1 + m / beta)
            hedge_pl = round(max(kp - idx_price, 0.0) * contracts * CONTRACT_MULTIPLIER - cost, 2)
            stock_pl = round(shares * spot * m, 2)
            scenarios.append({"move_pct": round(m * 100), "stock_pl": stock_pl,
                              "hedge_pl": hedge_pl, "net_pl": round(stock_pl + hedge_pl, 2)})

        return {
            "benchmark": benchmark,
            "beta": beta,
            "index_spot": round(idx_spot, 2),
            "expiration": chosen,
            "dte": dte,
            "strike": kp,
            "strike_pct": round((kp - idx_spot) / idx_spot * 100, 2),
            "contracts": contracts,
            "hedge_notional": round(hedge_notional, 2),
            "cost": cost,
            "cost_pct_of_notional": round(cost / notional * 100, 3) if notional else None,
            "leg": leg,
            "scenarios": scenarios,
            "is_broad_index": benchmark.upper() in ("SPY", "SPX", "XSP", "QQQ", "IWM", "NDX", "RUT"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Index overlay failed for {benchmark}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def run_hedging_strategy(
    ticker: str,
    shares: float,
    horizon_days: int = 45,
    protection_pct: float = 10.0,
    upside_pct: float = 15.0,
    downside_buffer: float = 8.0,
    upside_giveup: float = 8.0,
    downside_cap: float = 0.0,
    max_cost_pct: float = 2.0,
    hedge_ratio: float = 1.0,
    benchmark: str = "SPY",
    quote_source: str = "yfinance",
    target_expiration: str | None = None,
    user: Optional["User"] = None,
    db: Optional["AsyncSession"] = None,
) -> dict:
    """Build the hedge menu for a long holding + return the chain for editing."""
    provider = get_provider(quote_source, user=user, db=db)

    try:
        underlying = await provider.get_underlying_price(ticker)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Cannot fetch price for {ticker}: {exc}"}

    spot = underlying.price
    if not spot or spot <= 0:
        return {"error": (
            f"Could not get a live price for {ticker} from {quote_source}. "
            "The market snapshot may not have settled yet — try again in a moment, "
            "or switch to 'yfinance' as the quote source."
        )}
    display_ticker = underlying.symbol
    notional = shares * spot

    try:
        expirations = await provider.get_option_expirations(ticker)
    except Exception:  # noqa: BLE001
        return {"error": "No options data available for this ticker."}
    if not expirations:
        return {"error": "No options expiration dates found."}

    today = date.today()
    if target_expiration and target_expiration in expirations:
        chosen_exp = target_expiration
    else:
        scored = []
        for exp_str in expirations:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except Exception:  # noqa: BLE001
                continue
            d = (exp_date - today).days
            if d >= 5:
                scored.append((abs(d - horizon_days), exp_str))
        if not scored:
            return {"error": "No suitable option expirations found.",
                    "available_expirations": expirations[:15]}
        scored.sort()
        chosen_exp = scored[0][1]

    chosen_date = datetime.strptime(chosen_exp, "%Y-%m-%d").date()
    dte = (chosen_date - today).days

    try:
        chain = await provider.get_option_chain(ticker, chosen_exp)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Could not fetch option chain: {exc}"}
    if not chain.quotes:
        return {"error": "Option chain has no quotes for this expiration."}

    calls, puts = _split_chain(chain)
    strikes_p = sorted(puts.keys())
    strikes_c = sorted(calls.keys())
    if len(strikes_p) < 2:
        return {"error": "Not enough put strikes to build hedges."}

    contracts = max(1, round(shares * hedge_ratio / CONTRACT_MULTIPLIER))

    def _build_all() -> dict:
        hedges = []
        for b in _BUILDERS:
            h = b(calls, puts, strikes_p, strikes_c, shares, spot, dte, chosen_exp,
                  notional, protection_pct, upside_pct, downside_buffer, upside_giveup,
                  contracts, max_cost_pct, downside_cap)
            if h:
                hedges.append(h)
        # Serialize the chain (only liquid strikes within a usable band) for
        # client-side strike editing & repricing.
        lo, hi = spot * 0.4, spot * 1.6
        chain_puts = [_quote_dict(puts[k], spot, dte) for k in strikes_p
                      if lo <= k <= hi and _is_liquid(puts.get(k))]
        chain_calls = [_quote_dict(calls[k], spot, dte) for k in strikes_c
                       if lo <= k <= hi and _is_liquid(calls.get(k))]
        return {"hedges": hedges, "chain": {"puts": chain_puts, "calls": chain_calls}}

    built = await asyncio.to_thread(_build_all)
    if not built["hedges"]:
        return {"error": "Could not build viable hedges — strikes may be illiquid. "
                         "Try a more liquid ticker or a nearer expiration.",
                "available_expirations": expirations[:15]}

    # Market conditions / hedge-timing read (also returns beta from the same download).
    atm_iv = _atm_iv(puts, calls, spot)
    skew = _compute_skew(puts, calls, spot)
    pc_oi, pc_vol = _pc_ratios(puts, calls)
    market = await asyncio.to_thread(
        _compute_market_conditions, display_ticker, spot, atm_iv, dte,
        skew, pc_oi, pc_vol, benchmark,
    )
    beta = market.get("beta")
    # When the downside cap is on the financed twins appear alongside the pure
    # structures — badge them "fits now" too, so the comparison highlights both.
    if downside_cap and downside_cap > 0 and market.get("recommended_ids"):
        _twin = {"protective_put": "financed_floor", "collar": "financed_floor_collar"}
        rec_ids = list(market["recommended_ids"])
        for base, fin in _twin.items():
            if base in rec_ids and fin not in rec_ids:
                rec_ids.insert(rec_ids.index(base) + 1, fin)
        market["recommended_ids"] = rec_ids

    # When a give-up is set, surface giveup_funded_floor as a recommended structure.
    if upside_giveup > 0 and market.get("recommended_ids") is not None:
        rec_ids = list(market["recommended_ids"])
        if "giveup_funded_floor" not in rec_ids:
            rec_ids.insert(0, "giveup_funded_floor")
        market["recommended_ids"] = rec_ids

    # Market-implied probabilities per structure (SVI smile → Breeden-Litzenberger
    # RND). Turns each fixed-% level into a real breach/cap probability. Best-effort.
    rnd_summary = None
    try:
        strikes_all = sorted(set(strikes_p) | set(strikes_c))
        rnd = await asyncio.to_thread(_build_rnd, calls, puts, strikes_all, spot, dte)
        from .quant_service import hedge_probabilities, structure_smile_risk
        fwd = rnd.forward if rnd is not None else None
        for h in built["hedges"]:
            # Vanna-Volga smile risk (VIX-spike margin) — analytical, always available.
            h["smile_risk"] = structure_smile_risk(h["legs"], spot, dte, forward=fwd)
            if rnd is not None:
                floor, buffer_bottom, cap, _gf, _pa, _cov = _infer_geometry(h["legs"])
                h["probs"] = hedge_probabilities(rnd, spot, floor, cap, buffer_bottom)
        if rnd is not None:
            rnd_summary = {
                "forward": round(rnd.forward, 2),
                "expected_move_pct": round((rnd.forward / spot - 1) * 100, 2),
                "arb_free": rnd.smile.arb_free,
                "svi_rmse_vol_pts": round(rnd.smile.rmse * 100, 2),
                "p_down_5_pct": round(rnd.prob_below(spot * 0.95) * 100, 1),
                "p_down_10_pct": round(rnd.prob_below(spot * 0.90) * 100, 1),
                "p_down_20_pct": round(rnd.prob_below(spot * 0.80) * 100, 1),
                "floor_for_10pct_breach_pct": round((rnd.strike_for_prob_below(0.10) / spot - 1) * 100, 1),
                "floor_for_5pct_breach_pct": round((rnd.strike_for_prob_below(0.05) / spot - 1) * 100, 1),
            }
            # Downsampled curve → distribution chart + client-side prob recompute on edits.
            from .quant_service import rnd_curve
            rnd_summary["curve"] = rnd_curve(rnd, spot)
            # Heston stochastic-vol read (QuantLib; best-effort, panic-aware).
            try:
                _s, _iv, _cm, _pm = _chain_iv_arrays(calls, puts, strikes_all, spot)
                from .quant_service import calibrate_heston
                heston = await asyncio.to_thread(calibrate_heston, _s, _iv, spot, dte, 0.045, fwd)
                if heston:
                    rnd_summary["heston"] = heston
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Heston calibration error: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"RND annotation error: {exc}")

    # Beta-weighted index put overlay (best-effort; never blocks the main menu).
    index_overlay = None
    try:
        index_overlay = await _build_index_overlay(
            provider, benchmark, beta, shares, spot, notional, horizon_days,
            protection_pct, hedge_ratio, today,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Index overlay error: {exc}")

    return {
        "ticker": display_ticker,
        "current_price": round(spot, 2),
        "shares": shares,
        "notional": round(notional, 2),
        "beta": beta,
        "benchmark": benchmark,
        "hedge_ratio": hedge_ratio,
        "contracts": contracts,
        "covered_shares": contracts * CONTRACT_MULTIPLIER,
        "horizon_days": horizon_days,
        "expiration": chosen_exp,
        "dte": dte,
        "protection_pct": protection_pct,
        "upside_pct": upside_pct,
        "downside_buffer": downside_buffer,
        "upside_giveup": upside_giveup,
        "downside_cap": downside_cap,
        "max_cost_pct": max_cost_pct,
        "quote_source": quote_source,
        "atm_iv": round(atm_iv * 100, 1) if atm_iv else None,
        "hedges": built["hedges"],
        "chain": built["chain"],
        "market": market,
        "rnd": rnd_summary,
        "index_overlay": index_overlay,
        "risks": _build_risks(),
        "available_expirations": expirations[:15],
    }


# ---------------------------------------------------------------------------
# Lightweight pre-build timing check (no hedge construction, no chain download needed)
# ---------------------------------------------------------------------------

async def run_market_conditions_check(
    ticker: str,
    benchmark: str = "SPY",
    horizon_days: int = 45,
) -> dict:
    """Quick market-conditions read without building any hedge structures.

    Returns the same `market` dict shape as `run_hedging_strategy` so the
    frontend can show the MarketPanel as soon as the user types a ticker,
    before they commit to the full (slower) build.
    """
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        # Raw close (auto_adjust=False) so this spot is on the same unadjusted basis
        # as the 1y trend reference — otherwise numerator/denominator disagree.
        hist = tk.history(period="5d", auto_adjust=False)
        if hist.empty:
            return {"available": False, "error": "Cannot fetch price."}
        spot = float(hist["Close"].iloc[-1])
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}

    atm_iv: Optional[float] = None
    skew: Optional[float] = None
    pc_oi: Optional[float] = None
    pc_vol: Optional[float] = None
    calls = puts = None
    dte = horizon_days

    try:
        provider = get_provider("yfinance")
        expirations = await provider.get_option_expirations(ticker)
        if expirations:
            today = date.today()
            scored = []
            for exp_str in expirations:
                try:
                    d = (datetime.strptime(exp_str, "%Y-%m-%d").date() - today).days
                    if d >= 5:
                        scored.append((abs(d - horizon_days), exp_str))
                except Exception:  # noqa: BLE001
                    continue
            if scored:
                scored.sort()
                chosen_exp = scored[0][1]
                dte = (datetime.strptime(chosen_exp, "%Y-%m-%d").date() - today).days
                try:
                    chain = await provider.get_option_chain(ticker, chosen_exp)
                    calls, puts = _split_chain(chain)
                    atm_iv = _atm_iv(puts, calls, spot)
                    skew = _compute_skew(puts, calls, spot)
                    pc_oi, pc_vol = _pc_ratios(puts, calls)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass

    market = await asyncio.to_thread(
        _compute_market_conditions, ticker, spot, atm_iv, dte,
        skew, pc_oi, pc_vol, benchmark,
    )
    market["spot"] = round(spot, 2)
    market["atm_iv"] = round(atm_iv * 100, 1) if atm_iv else None

    # Market-implied tail probabilities (SVI → Breeden-Litzenberger RND) so the
    # agent reasons from real probabilities, not just the timing score. Best-effort.
    if calls and puts:
        try:
            strikes_all = sorted(set(calls) | set(puts))
            rnd = await asyncio.to_thread(_build_rnd, calls, puts, strikes_all, spot, dte)
            if rnd is not None:
                from .quant_service import rnd_curve
                market["rnd"] = {
                    "forward": round(rnd.forward, 2),
                    "expected_move_pct": round((rnd.forward / spot - 1) * 100, 2),
                    "arb_free": rnd.smile.arb_free,
                    "svi_rmse_vol_pts": round(rnd.smile.rmse * 100, 2),
                    "p_down_5_pct": round(rnd.prob_below(spot * 0.95) * 100, 1),
                    "p_down_10_pct": round(rnd.prob_below(spot * 0.90) * 100, 1),
                    "p_down_20_pct": round(rnd.prob_below(spot * 0.80) * 100, 1),
                    "floor_for_10pct_breach_pct": round((rnd.strike_for_prob_below(0.10) / spot - 1) * 100, 1),
                    "floor_for_5pct_breach_pct": round((rnd.strike_for_prob_below(0.05) / spot - 1) * 100, 1),
                    "curve": rnd_curve(rnd, spot),
                }
                # Heston stochastic-vol read (best-effort).
                try:
                    _s, _iv, _cm, _pm = _chain_iv_arrays(calls, puts, strikes_all, spot)
                    from .quant_service import calibrate_heston
                    heston = await asyncio.to_thread(calibrate_heston, _s, _iv, spot, dte,
                                                     0.045, rnd.forward)
                    if heston:
                        market["rnd"]["heston"] = heston
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"market-check Heston error: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"market-check RND error: {exc}")

    # Next earnings date — pros never put on a hedge without knowing whether an
    # earnings print sits inside the horizon (IV is bid into it; theta cliffs after).
    try:
        def _next_earnings():
            import yfinance as yf
            cal = yf.Ticker(ticker).calendar
            dates = (cal or {}).get("Earnings Date") or []
            ds = []
            for d in dates:
                if d is None:
                    continue
                d = d.date() if hasattr(d, "date") and not isinstance(d, date) else d
                if isinstance(d, date) and d >= date.today():
                    ds.append(d)
            return min(ds) if ds else None
        ed = await asyncio.to_thread(_next_earnings)
        if ed:
            days_to = (ed - date.today()).days
            market["next_earnings"] = {"date": ed.isoformat(), "days": days_to,
                                       "inside_horizon": days_to <= horizon_days}
    except Exception:  # noqa: BLE001
        pass
    return market


async def get_price_history_with_technicals(ticker: str, days: int = 90) -> dict:
    """Daily OHLCV history with light technicals for the hedge-context chart.

    Lookback ≈ 2× the hedge horizon (min 60d) so the user sees the run-up into
    the window they're hedging. Support/resistance = fractal pivot levels
    clustered within 1% and ranked by touch count.
    """
    def _sync() -> dict:
        import yfinance as yf
        import numpy as np

        lookback = min(max(int(days * 2), 60), 540)
        hist = yf.Ticker(ticker).history(period=f"{lookback + 40}d", auto_adjust=False)
        hist = hist.dropna(subset=["Close"]).tail(lookback)
        if hist.empty or len(hist) < 20:
            return {"available": False, "error": "Not enough price history."}

        close = hist["Close"].astype(float)
        high = hist["High"].astype(float)
        low = hist["Low"].astype(float)
        vol = hist["Volume"].fillna(0).astype(float)
        spot = float(close.iloc[-1])

        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()

        # Fractal pivots: a bar whose high (low) is the max (min) of a ±w window.
        w = 3
        piv_hi = [float(high.iloc[i]) for i in range(w, len(high) - w)
                  if high.iloc[i] == high.iloc[i - w:i + w + 1].max()]
        piv_lo = [float(low.iloc[i]) for i in range(w, len(low) - w)
                  if low.iloc[i] == low.iloc[i - w:i + w + 1].min()]

        def _cluster(levels: list[float]) -> list[dict]:
            """Merge levels within 1% of each other; rank by touches."""
            out: list[list[float]] = []
            for lv in sorted(levels):
                if out and abs(lv - np.mean(out[-1])) / spot < 0.01:
                    out[-1].append(lv)
                else:
                    out.append([lv])
            ranked = sorted(out, key=len, reverse=True)
            return [{"level": round(float(np.mean(c)), 2), "touches": len(c)} for c in ranked]

        supports = [c for c in _cluster(piv_lo) if c["level"] < spot][:3]
        resistances = [c for c in _cluster(piv_hi) if c["level"] > spot][:3]

        def _ser(s):
            return [round(float(x), 2) if x == x else None for x in s]

        return {
            "available": True,
            "ticker": ticker,
            "spot": round(spot, 2),
            "lookback_days": lookback,
            "dates": [d.strftime("%Y-%m-%d") for d in hist.index],
            "close": _ser(close),
            "volume": [int(x) for x in vol],
            "sma20": _ser(sma20),
            "sma50": _ser(sma50),
            "supports": supports,
            "resistances": resistances,
            "avg_volume": int(vol.mean()),
            "last_volume_ratio": round(float(vol.iloc[-1] / vol.mean()), 2) if vol.mean() > 0 else None,
        }

    try:
        return await asyncio.to_thread(_sync)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"price history error for {ticker}: {exc}")
        return {"available": False, "error": str(exc)}
