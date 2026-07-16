"""ZEBRA (Zero Extrinsic BackRatio) engine.

A bullish ZEBRA buys 2 deep-ITM calls and sells 1 ATM call to replicate ~100
shares of stock with near-zero extrinsic value.  The naive build (bid/ask mids +
`mid − intrinsic` extrinsic) is noisy and, more importantly, hides a real risk:
between the long and short strikes the structure carries +200 delta, so it loses
~2x versus stock on a moderate drop.

This module upgrades that in two ways:

  1. Institutional pricing — calibrate an SVI smile to the live chain
     (`quant_service.calibrate_svi`), read a smooth arbitrage-checked IV at ANY
     strike, price/greek every leg off it, and derive market-implied
     probabilities from the Breeden-Litzenberger risk-neutral density (RND).
     A best-effort QuantLib/Heston layer adds a panic-aware smile read where the
     library is present (deployed image); everything degrades gracefully to the
     mid-based math if the fit fails, so the endpoint never hard-errors.

  2. Loss control — an optional hedge leg (protective put, or a zero-cost collar)
     collapses the +200-delta zone back toward stock-like (or better) on the
     downside, while leaving the upside untouched.  We report the worst-case
     underperformance vs 100 shares both WITH and WITHOUT the hedge so the
     improvement is explicit.
"""

import asyncio
import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import yfinance as yf

from .stock_service import safe_float

DEFAULT_RISK_FREE = 0.045
CONTRACT_MULTIPLIER = 100


# ---------------------------------------------------------------------------
# Black-Scholes price + Greeks (per share). Closed form — identical to what a
# QuantLib AnalyticEuropeanEngine returns for a European option, and available
# without the compiled library so it works locally and in the image alike.
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_price(spot: float, strike: float, t: float, iv: float,
              is_call: bool, r: float = DEFAULT_RISK_FREE) -> float:
    """Black-Scholes fair value; falls back to intrinsic for degenerate inputs."""
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    if is_call:
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
    return strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _bs_greeks(spot: float, strike: float, t: float, iv: float,
               is_call: bool, r: float = DEFAULT_RISK_FREE) -> dict:
    """Per-share delta, gamma, theta (per calendar day), vega (per 1 vol point)."""
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        if is_call:
            delta = 1.0 if spot > strike else 0.0
        else:
            delta = -1.0 if spot < strike else 0.0
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    pdf = _norm_pdf(d1)
    gamma = pdf / (spot * iv * sqrt_t)
    vega = spot * pdf * sqrt_t / 100.0
    if is_call:
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf * iv) / (2 * sqrt_t)
                 - r * strike * math.exp(-r * t) * _norm_cdf(d2)) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * pdf * iv) / (2 * sqrt_t)
                 + r * strike * math.exp(-r * t) * _norm_cdf(-d2)) / 365.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


# ---------------------------------------------------------------------------
# Chain helpers (yfinance DataFrames -> plain dicts keyed by strike)
# ---------------------------------------------------------------------------

def _row_mid(row: dict) -> float:
    bid = safe_float(row.get("bid", 0.0))
    ask = safe_float(row.get("ask", 0.0))
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 2)
    return safe_float(row.get("lastPrice", 0.0))


def _index_by_strike(df) -> dict[float, dict]:
    out: dict[float, dict] = {}
    if df is None or df.empty:
        return out
    for _, row in df.iterrows():
        k = safe_float(row.get("strike"))
        if k <= 0:
            continue
        out[round(k, 4)] = row.to_dict()
    return out


def _find_closest(book: dict[float, dict], target: float,
                  side: str = "any") -> Optional[tuple[float, dict]]:
    """Nearest strike to *target*; side filters below/above spot."""
    if not book:
        return None
    if side == "below":
        cands = [k for k in book if k <= target]
    elif side == "above":
        cands = [k for k in book if k >= target]
    else:
        cands = list(book)
    if not cands:
        cands = list(book)
    k = min(cands, key=lambda s: abs(s - target))
    return k, book[k]


# ---------------------------------------------------------------------------
# SVI smile calibration off the live chain (best effort)
# ---------------------------------------------------------------------------

def _calibrate_smile(calls: dict[float, dict], puts: dict[float, dict],
                     spot: float, dte: int):
    """Fit an SVI smile so we can read a synthetic IV at any strike. Uses the
    put wing below spot and the call wing above (the liquid, skew-bearing side).
    Returns (smile_or_None, heston_or_None). Never raises."""
    try:
        from .quant_service import calibrate_svi, calibrate_heston
    except Exception:
        return None, None

    strikes, ivs, cmids, pmids = [], [], [], []
    for k in sorted(set(calls) | set(puts)):
        c = calls.get(k)
        p = puts.get(k)
        civ = safe_float(c.get("impliedVolatility")) if c else 0.0
        piv = safe_float(p.get("impliedVolatility")) if p else 0.0
        if k <= spot and piv > 0:
            iv = piv
        elif k > spot and civ > 0:
            iv = civ
        else:
            iv = civ or piv
        if not iv or iv <= 0:
            continue
        strikes.append(float(k))
        ivs.append(float(iv))
        cmids.append(_row_mid(c) if c else None)
        pmids.append(_row_mid(p) if p else None)

    if len(strikes) < 6:
        return None, None

    smile = None
    heston = None
    try:
        smile = calibrate_svi(strikes, ivs, spot, dte,
                              call_mids=cmids, put_mids=pmids)
    except Exception:
        smile = None
    try:
        # QuantLib layer — returns None if the library is absent (local venv) or
        # the fit is degenerate. Purely an institutional "panic-aware" read.
        heston = calibrate_heston(strikes, ivs, spot, dte)
    except Exception:
        heston = None
    return smile, heston


# ---------------------------------------------------------------------------
# Leg model — a fair value + Greeks for one option using the smile IV when
# available, else the strike's own listed IV, else zero-vol intrinsic.
# ---------------------------------------------------------------------------

class _Pricer:
    def __init__(self, smile, calls, puts, spot, t, r=DEFAULT_RISK_FREE):
        self.smile = smile
        self.calls = calls
        self.puts = puts
        self.spot = spot
        self.t = t
        self.r = r

    def iv_at(self, strike: float, is_call: bool) -> float:
        if self.smile is not None:
            try:
                v = float(self.smile.iv(strike))
                if v > 0:
                    return v
            except Exception:
                pass
        book = self.calls if is_call else self.puts
        hit = _find_closest(book, strike)
        if hit:
            v = safe_float(hit[1].get("impliedVolatility"))
            if v > 0:
                return v
        return 0.0

    def model_price(self, strike: float, is_call: bool) -> float:
        return _bs_price(self.spot, strike, self.t, self.iv_at(strike, is_call),
                         is_call, self.r)

    def extrinsic(self, strike: float, is_call: bool, mid: float) -> float:
        """Extrinsic (time) value of a quoted leg. Uses the traded mid minus
        intrinsic — the smile keeps the *selection* stable, but the extrinsic a
        trader actually pays/collects is set by the live mid."""
        intrinsic = (max(0.0, self.spot - strike) if is_call
                     else max(0.0, strike - self.spot))
        return mid - intrinsic

    def greeks(self, strike: float, is_call: bool) -> dict:
        return _bs_greeks(self.spot, strike, self.t, self.iv_at(strike, is_call),
                          is_call, self.r)


# ---------------------------------------------------------------------------
# Payoff evaluation for an arbitrary set of signed legs
# ---------------------------------------------------------------------------

def _leg_payoff(sim_price: float, strike: float, is_call: bool) -> float:
    return (max(0.0, sim_price - strike) if is_call
            else max(0.0, strike - sim_price))


def _structure_payoff(sim_price: float, legs: list[dict]) -> float:
    """Per-unit expiry payoff. Each leg: {signed_qty, strike, is_call}."""
    return sum(lg["signed_qty"] * _leg_payoff(sim_price, lg["strike"], lg["is_call"])
               for lg in legs)


def _worst_underperformance(legs: list[dict], cost_per_unit: float,
                            spot: float, is_bullish: bool,
                            grid: np.ndarray) -> dict:
    """Min over the *loss* region of (structure PnL − stock PnL), per share.
    Bullish: loss region is S < spot; bearish: S > spot. Also returns the
    per-unit max loss of the structure across the whole grid."""
    worst_gap = 0.0
    worst_price = spot
    max_loss = 0.0
    for s in grid:
        struct_pnl = _structure_payoff(s, legs) - cost_per_unit
        stock_pnl = (s - spot) if is_bullish else (spot - s)
        max_loss = min(max_loss, struct_pnl)
        in_loss_region = (s < spot) if is_bullish else (s > spot)
        if in_loss_region:
            gap = struct_pnl - stock_pnl
            if gap < worst_gap:
                worst_gap = gap
                worst_price = float(s)
    return {
        "worst_gap_per_share": worst_gap,       # most negative = worst vs stock
        "worst_price": round(worst_price, 2),
        "max_loss_per_share": max_loss,         # <= 0
    }


# ---------------------------------------------------------------------------
# Main sync worker
# ---------------------------------------------------------------------------

def _run_zebra_sync(
    ticker: str,
    amount: float,
    is_call: bool,
    duration_days: int,
    strategy_variant: str = "zero_extrinsic",
    hedge_mode: str = "protective",
) -> dict:
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)

    info = stock.info or {}
    current_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
    if not current_price:
        try:
            hist = stock.history(period="1d")
            if not hist.empty:
                current_price = hist["Close"].iloc[-1]
        except Exception:
            pass
    if not current_price:
        return {"error": f"Could not fetch current price for {ticker}"}
    current_price = float(current_price)

    try:
        expirations = list(stock.options)
    except Exception:
        return {"error": f"No options chain available for {ticker}"}
    if not expirations:
        return {"error": f"No options expirations found for {ticker}"}

    today = datetime.now().date()
    target_date = today + timedelta(days=duration_days)
    closest_exp, min_diff = None, 99999
    for exp_str in expirations:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            diff = abs((exp_date - target_date).days)
            if diff < min_diff:
                min_diff, closest_exp = diff, exp_str
        except Exception:
            pass
    if not closest_exp:
        return {"error": "Could not find a valid expiration date."}

    exp_date = datetime.strptime(closest_exp, "%Y-%m-%d").date()
    dte = max((exp_date - today).days, 1)
    t = dte / 365.0

    chain = stock.option_chain(closest_exp)
    calls = _index_by_strike(chain.calls)
    puts = _index_by_strike(chain.puts)
    if not calls or not puts:
        return {"error": f"Incomplete options chain for {ticker} at {closest_exp}."}

    # ---- Institutional vol layer (best effort) --------------------------------
    smile, heston = _calibrate_smile(calls, puts, current_price, dte)
    pricer = _Pricer(smile, calls, puts, current_price, t)
    forward = float(smile.forward) if smile is not None else current_price * math.exp(DEFAULT_RISK_FREE * t)

    # Books oriented so "long" = deep ITM, "short" = ATM, for either direction.
    long_book = calls if is_call else puts
    short_book = calls if is_call else puts
    hedge_book = puts if is_call else calls          # protective leg is opposite right

    # ---- Short ATM leg --------------------------------------------------------
    hit = _find_closest(short_book, current_price)
    if not hit:
        return {"error": "Could not find a valid short (ATM) option."}
    short_strike, short_row = hit
    short_mid = _row_mid(short_row)
    if short_mid <= 0:
        short_mid = pricer.model_price(short_strike, is_call)
    short_extrinsic = pricer.extrinsic(short_strike, is_call, short_mid)

    # ---- Long deep-ITM leg: pick the strike whose 2x extrinsic best matches the
    #      variant target, priced off the smile for stability ------------------
    if strategy_variant == "low_debit":
        target_extrinsic = short_mid * 0.35        # accept some decay, cheaper entry
    elif strategy_variant == "theta_positive":
        target_extrinsic = -(short_mid * 0.35)     # collect decay, deeper ITM
    else:
        target_extrinsic = 0.0                     # classic: immunize decay

    best = None
    best_diff = 1e18
    for k, row in long_book.items():
        itm = (k < current_price) if is_call else (k > current_price)
        if not itm:
            continue
        mid = _row_mid(row)
        if mid <= 0:
            mid = pricer.model_price(k, is_call)
        if mid <= 0:
            continue
        long_extrinsic = pricer.extrinsic(k, is_call, mid)
        net_extrinsic = (2 * long_extrinsic) - short_extrinsic
        diff = abs(net_extrinsic - target_extrinsic)
        if diff < best_diff:
            best_diff = diff
            best = {"strike": k, "mid": mid, "net_extrinsic": net_extrinsic}
    if not best:
        return {"error": "Could not find sufficient deep-ITM strikes to build ZEBRA."}
    long_strike, long_mid, net_extrinsic = best["strike"], best["mid"], best["net_extrinsic"]

    # ---- Signed legs for the base (unhedged) ZEBRA ----------------------------
    # signed_qty is per structure: +2 long, -1 short.
    base_legs = [
        {"signed_qty": 2, "strike": long_strike, "is_call": is_call},
        {"signed_qty": -1, "strike": short_strike, "is_call": is_call},
    ]
    base_cost_per_unit = (2 * long_mid) - short_mid

    # ---- Hedge legs (task: keep losses <= holding stock) ----------------------
    hedge_info = None
    active_legs = [dict(l) for l in base_legs]
    active_cost_per_unit = base_cost_per_unit

    if hedge_mode in ("protective", "collar"):
        # A protective SPREAD across the +200-delta danger zone: buy the
        # opposite-right option at the short strike (active throughout the zone)
        # and sell it at the long strike. This adds exactly one unit of
        # counter-delta between the strikes — collapsing +200 delta back toward
        # +100 (stock-like) — at a cost bounded by the zone width, so it can never
        # over-insure the way a naked ATM option can. "collar" additionally sells
        # a ~1σ OTM option on the profit side to fund the spread, pushing downside
        # losses to <= stock in exchange for a capped upside.
        h_is_call = not is_call
        buy_hit = _find_closest(hedge_book, short_strike)
        sell_hit = _find_closest(hedge_book, long_strike)
        if buy_hit and sell_hit and buy_hit[0] != sell_hit[0]:
            hb_strike, hb_row = buy_hit
            hs_strike, hs_row = sell_hit
            hb_mid = _row_mid(hb_row) or pricer.model_price(hb_strike, h_is_call)
            hs_mid = _row_mid(hs_row) or pricer.model_price(hs_strike, h_is_call)

            active_legs.append({"signed_qty": 1, "strike": hb_strike, "is_call": h_is_call})
            active_legs.append({"signed_qty": -1, "strike": hs_strike, "is_call": h_is_call})
            active_cost_per_unit += (hb_mid - hs_mid)

            fund_leg = None
            if hedge_mode == "collar":
                # Sell an OTM option on the profit side, capped no tighter than ~1σ
                # over the horizon (min 5%) so the directional view keeps a real
                # runway; take the richest-funding strike at/beyond that floor.
                sigma_move = pricer.iv_at(current_price, is_call) * math.sqrt(t)
                if is_call:
                    cap_floor = current_price * max(1.05, math.exp(sigma_move))
                    cands = [(k, _row_mid(row)) for k, row in calls.items()
                             if k >= cap_floor and _row_mid(row) > 0]
                    if cands:
                        fk, fm = min(cands, key=lambda c: c[0])
                        fund_leg = {"strike": fk, "is_call": True, "mid": fm}
                else:
                    cap_floor = current_price * min(0.95, math.exp(-sigma_move))
                    cands = [(k, _row_mid(row)) for k, row in puts.items()
                             if k <= cap_floor and _row_mid(row) > 0]
                    if cands:
                        fk, fm = max(cands, key=lambda c: c[0])
                        fund_leg = {"strike": fk, "is_call": False, "mid": fm}
                if fund_leg is not None:
                    active_legs.append({"signed_qty": -1, "strike": fund_leg["strike"],
                                        "is_call": fund_leg["is_call"]})
                    active_cost_per_unit -= fund_leg["mid"]

            hedge_info = {
                "mode": hedge_mode,
                "protectiveType": "Call" if h_is_call else "Put",
                "protectiveBuyStrike": round(hb_strike, 2),
                "protectiveSellStrike": round(hs_strike, 2),
                "protectiveCost": round(hb_mid - hs_mid, 2),
                "netHedgeCost": round(active_cost_per_unit - base_cost_per_unit, 2),
                "fundingStrike": round(fund_leg["strike"], 2) if fund_leg else None,
                "fundingType": ("Call" if is_call else "Put") if fund_leg else None,
                "fundingCredit": round(fund_leg["mid"], 2) if fund_leg else None,
                "upsideCapPct": (round((fund_leg["strike"] / current_price - 1) * 100, 1)
                                 if fund_leg else None),
            }

    if active_cost_per_unit <= 0:
        return {"error": "Invalid structure: net options cost resulted in a credit."}

    # ---- Sizing ---------------------------------------------------------------
    affordable = amount / (active_cost_per_unit * CONTRACT_MULTIPLIER)
    qty = math.floor(affordable)
    if qty < 1:
        need = round(active_cost_per_unit * CONTRACT_MULTIPLIER, 2)
        return {"error": f"Investment amount too low for a single structure. Need at least ${need}."}

    total_debit = qty * active_cost_per_unit * CONTRACT_MULTIPLIER
    full_stock_cost = qty * CONTRACT_MULTIPLIER * current_price
    capital_efficiency_saved = full_stock_cost - total_debit

    # ---- Display legs ---------------------------------------------------------
    def _leg_row(action, per_unit_qty, is_c, strike, mid, purpose):
        g = pricer.greeks(strike, is_c)
        return {
            "action": action,
            "qty": per_unit_qty * qty,
            "type": "Call" if is_c else "Put",
            "strike": round(strike, 2),
            "midPrice": round(mid, 2),
            "iv": round(pricer.iv_at(strike, is_c) * 100, 1),
            "delta": round(g["delta"], 3),
            "purpose": purpose,
            "expiration": closest_exp,
        }

    display_legs = [
        _leg_row("Buy", 2, is_call, long_strike, long_mid,
                 "2x long deep-ITM — the 100-delta stock-equivalent engine."),
        _leg_row("Sell", 1, is_call, short_strike, short_mid,
                 "1x short ATM — finances the long legs' time value."),
    ]
    if hedge_info is not None:
        h_is_call = not is_call

        def _hedge_mid(book, strike):
            hit = _find_closest(book, strike)
            m = _row_mid(hit[1]) if hit else 0.0
            return m or pricer.model_price(strike, book is calls)

        display_legs.append(_leg_row(
            "Buy", 1, h_is_call, hedge_info["protectiveBuyStrike"],
            _hedge_mid(hedge_book, hedge_info["protectiveBuyStrike"]),
            "1x protective spread (long) — cancels the extra danger-zone delta so losses track stock."))
        display_legs.append(_leg_row(
            "Sell", 1, h_is_call, hedge_info["protectiveSellStrike"],
            _hedge_mid(hedge_book, hedge_info["protectiveSellStrike"]),
            "1x protective spread (short) — bounds the hedge cost to the danger-zone width."))
        if hedge_info.get("fundingStrike") is not None:
            f_book = calls if is_call else puts
            display_legs.append(_leg_row(
                "Sell", 1, is_call, hedge_info["fundingStrike"],
                _hedge_mid(f_book, hedge_info["fundingStrike"]),
                "1x short OTM — funds the protective spread (caps the far tail)."))

    # ---- Net Greeks (per full position, at current spot) ----------------------
    net = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for lg in active_legs:
        g = pricer.greeks(lg["strike"], lg["is_call"])
        for key in net:
            net[key] += lg["signed_qty"] * g[key]
    shares = CONTRACT_MULTIPLIER * qty
    greeks_out = {
        "netDelta": round(net["delta"] * shares, 1),      # +~100*qty on the upside
        "netGamma": round(net["gamma"] * shares, 4),
        "netTheta": round(net["theta"] * shares, 2),      # $/day; ~0 for classic
        "netVega": round(net["vega"] * shares, 2),
    }

    # ---- Risk-neutral probabilities (Breeden-Litzenberger off the SVI curve) --
    rnd = None
    if smile is not None:
        try:
            from .quant_service import risk_neutral_density
            rnd = risk_neutral_density(smile)
        except Exception:
            rnd = None

    # ---- Scenario grid (wider than before to expose the crash region) ---------
    if is_call:
        pct_moves = [-0.30, -0.20, -0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15, 0.20, 0.30]
    else:
        pct_moves = [0.30, 0.20, 0.15, 0.10, 0.05, 0.0, -0.05, -0.10, -0.15, -0.20, -0.30]

    def _bucket_prob(sim_price: float, lo: Optional[float], hi: Optional[float]) -> Optional[float]:
        if rnd is None:
            return None
        p_lo = rnd.prob_below(lo) if lo is not None else 0.0
        p_hi = rnd.prob_below(hi) if hi is not None else 1.0
        return round(max(0.0, p_hi - p_lo) * 100, 1)

    prices = sorted({round(current_price * (1 + c), 2) for c in pct_moves})
    scenarios = []
    for change in pct_moves:
        sim_price = round(current_price * (1 + change), 2)
        struct_pnl = (_structure_payoff(sim_price, active_legs) - active_cost_per_unit) * shares
        stock_pnl = ((sim_price - current_price) if is_call else (current_price - sim_price)) * shares
        vs_stock = struct_pnl - stock_pnl

        # bucket edges = midpoints to neighbouring scenario prices
        idx = prices.index(sim_price)
        lo = None if idx == 0 else (prices[idx - 1] + sim_price) / 2
        hi = None if idx == len(prices) - 1 else (prices[idx + 1] + sim_price) / 2

        scenarios.append({
            "underlyingChangePct": round(change * 100, 1),
            "simulatedPrice": sim_price,
            "status": "Profitable" if struct_pnl >= 0 else "Loss",
            "netProfit": round(struct_pnl, 2),
            "stockEquivalentProfit": round(stock_pnl, 2),
            "vsStock": round(vs_stock, 2),
            "betterThanStock": vs_stock >= -0.01 * shares,   # within 1c/share
            "probabilityPct": _bucket_prob(sim_price, lo, hi),
        })

    # ---- vs-stock worst case, WITH and WITHOUT the hedge ----------------------
    grid = np.linspace(max(current_price * 0.30, 0.01), current_price * 1.70, 281)
    base_risk = _worst_underperformance(base_legs, base_cost_per_unit,
                                         current_price, is_call, grid)
    active_risk = _worst_underperformance(active_legs, active_cost_per_unit,
                                          current_price, is_call, grid)

    # loss-magnification zone (between long & short strikes) for the base build
    zone_lo, zone_hi = sorted((long_strike, short_strike))
    p_in_loss_zone = None
    p_max_loss = None
    p_profit = None
    expected_price = None
    if rnd is not None:
        p_in_loss_zone = round((rnd.prob_below(zone_hi) - rnd.prob_below(zone_lo)) * 100, 1)
        # max loss occurs past the long strike (all long legs expire worthless side)
        if is_call:
            p_max_loss = round(rnd.prob_below(long_strike) * 100, 1)
        else:
            p_max_loss = round(rnd.prob_above(long_strike) * 100, 1)
        # rough profit prob: structure PnL > 0 at/near the short strike breakeven
        be = None
        # scan grid for sign change into profit on the up side
        for s in (grid if is_call else grid[::-1]):
            pnl = _structure_payoff(s, active_legs) - active_cost_per_unit
            moved_up = (s >= current_price) if is_call else (s <= current_price)
            if pnl >= 0 and moved_up:
                be = float(s)
                break
        if be is not None:
            p_profit = round((rnd.prob_above(be) if is_call else rnd.prob_below(be)) * 100, 1)
        try:
            expected_price = round(float(np.trapezoid(rnd.K * rnd.pdf, rnd.K)), 2)
        except Exception:
            expected_price = None

    risk_out = {
        "maxLoss": round(-active_risk["max_loss_per_share"] * shares, 2),
        "maxLossVsStockFullDrop": round(full_stock_cost, 2),
        # headline: worst $ you can trail 100 shares by on the downside
        "worstUnderperfVsStock": round(active_risk["worst_gap_per_share"] * shares, 2),
        "worstUnderperfVsStockUnhedged": round(base_risk["worst_gap_per_share"] * shares, 2),
        "worstUnderperfPrice": active_risk["worst_price"],
        "lossZoneLow": round(zone_lo, 2),
        "lossZoneHigh": round(zone_hi, 2),
        "pInLossZonePct": p_in_loss_zone,
        "pMaxLossPct": p_max_loss,
        "pProfitPct": p_profit,
    }

    vol_out = {
        "atmIv": round(pricer.iv_at(current_price, is_call) * 100, 1),
        "forward": round(forward, 2),
        "expectedTerminalPrice": expected_price,
        "sviFitRmseVolPts": round(smile.rmse * 100, 2) if smile is not None else None,
        "sviArbFree": bool(smile.arb_free) if smile is not None else None,
        "pricingEngine": ("SVI smile + Breeden-Litzenberger RND"
                          if smile is not None else "chain mid / intrinsic (fallback)"),
        "heston": heston,   # QuantLib layer; None where unavailable
    }

    return {
        "success": True,
        "ticker": ticker,
        "currentPrice": round(current_price, 2),
        "investmentAmount": amount,
        "strategy": "ZEBRA (Zero Extrinsic BackRatio)",
        "variant": strategy_variant,
        "expiration": closest_exp,
        "dte": dte,
        "qtyStructures": qty,
        "netExtrinsicValue": round(net_extrinsic, 2),
        "totalDebit": round(total_debit, 2),
        "capitalEfficiencySaved": round(capital_efficiency_saved, 2),
        "greeks": greeks_out,
        "vol": vol_out,
        "risk": risk_out,
        "hedge": hedge_info,
        "legs": display_legs,
        "scenarios": scenarios,
    }


async def run_zebra(
    ticker: str,
    amount: float,
    is_call: bool,
    duration_days: int,
    strategy_variant: str = "zero_extrinsic",
    hedge_mode: str = "protective",
) -> dict:
    return await asyncio.to_thread(
        _run_zebra_sync,
        ticker,
        amount,
        is_call,
        duration_days,
        strategy_variant,
        hedge_mode,
    )
