"""Institutional book-level short-vol / tail-risk desk.

Income selling is CONCAVE (short gamma/vega, negative skew): many small wins, rare
large losses. Per-trade the tail looks tiny; the real exposure is the AGGREGATE, and
in a crash correlations → 1. This desk, on demand:

  1. sums the OPTION-LAYER greeks + beta-weighted (SPY-equivalent) delta,
  2. flags where short premium is laddered / concentrated into one bet,
  3. **fully reprices** the book under crash scenarios (not a Taylor expansion — that
     breaks for large moves),
  4. runs a **full-revaluation Monte-Carlo** VaR/CVaR at a 1-month horizon with a
     FAT-TAILED (Student-t) market factor + volatility skew,
  5. builds a **menu** of index (SPX, European) tail hedges and ranks them by
     risk-reduction-per-dollar and the Spitznagel cost-vs-drag (CAGR lift),
  6. gives a plain-language verdict + concrete actions.

Research: Carr-Wu / Bakshi-Kapadia (VRP = crash-risk premium), Rockafellar-Uryasev
(CVaR), Taleb / Spitznagel (convex tail hedging, the volatility tax on compounding).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import date
from typing import Optional

import numpy as np
from scipy.stats import norm

from .lifecycle_service import higher_order_greeks
from .stock_service import bs_price

logger = logging.getLogger(__name__)
_MULT = 100
_BENCH = "^GSPC"          # S&P 500 index — beta benchmark + hedge underlier
_SPX_OPT = "^SPX"         # CBOE SPX options (European · cash-settled), $100/point
_SPY_DIV = 10.0           # SPY ≈ SPX / 10

# (label, index move, vol-points shock). Vol SPIKES as spot drops (skew) and CRUSHES on a
# melt-up. A short-GAMMA book loses on a big move in EITHER direction — so the stress set is
# two-sided: the deep downside crashes AND the upside melt-ups the net-short-delta / short-
# call side is exposed to (the risk a downside-only crash table hides).
_CRASH = [("GFC −50%", -0.50, 45.0), ("COVID −34%", -0.34, 30.0),
          ("−20%", -0.20, 15.0), ("−10%", -0.10, 8.0),
          ("Melt-up +10%", 0.10, -4.0), ("Melt-up +20%", 0.20, -6.0),
          ("Squeeze +35%", 0.35, 8.0)]   # a violent gap-up can BID call skew (vol up, not crush)
_HEDGE_SCEN = (-0.20, 15.0)      # size hedges to the −20% book loss
_HORIZON_TD = 21                 # VaR/CVaR horizon: ~1 trading month
_MKT_VOL_FALLBACK = 0.16         # annualized S&P vol if VIX is unavailable
_TDF = 4                         # Student-t d.o.f. — fat market tails
_SKEW_K = 0.75                   # vol-points (decimal) added per unit adverse move

_VIX_IDX = "^VIX"                # spot VIX (30-day implied vol)
_VIX_3M = "^VIX3M"               # 3-month VIX → forward-vol proxy for a ~90d hedge
_VIX_MULT = 100.0                # VIX options: $100 per index point, European, cash-settled
# SPX 21-day return → VIX FRONT-FUTURE bump over the forward. VIX options settle on the VIX
# FUTURE (not spot VIX), which mean-reverts — so the future peaks BELOW spot VIX in a crash
# (COVID: spot VIX 82, ~1-mo future ~60). Anchors: historical monthly SPX drawdowns → front
# future levels, dampened for that mean reversion so the hedge payoff is NOT overstated.
_VIX_CURVE_X = [-0.50, -0.34, -0.20, -0.10, -0.05, 0.0, 0.05]   # SPX log-return (ascending)
_VIX_CURVE_Y = [55.0, 48.0, 33.0, 17.0, 8.0, 0.0, -3.0]         # +VIX future points over forward


# ── vectorized Black-Scholes (for the MC reval) ──────────────────────────────

def _bs_vec(S, K: float, T: float, r: float, sigma, right: str):
    """BS price over arrays of S and sigma (K/T/r scalar). right = 'C' | 'P'."""
    S = np.asarray(S, dtype=float)
    sig = np.maximum(np.asarray(sigma, dtype=float), 1e-4)
    T = max(float(T), 1.0 / 365)
    sqrtT = math.sqrt(T)
    d1 = (np.log(np.maximum(S, 1e-9) / K) + (r + 0.5 * sig ** 2) * T) / (sig * sqrtT)
    d2 = d1 - sig * sqrtT
    disc = K * math.exp(-r * T)
    if right == "C":
        return S * norm.cdf(d1) - disc * norm.cdf(d2)
    return disc * norm.cdf(-d2) - S * norm.cdf(-d1)


# ── crash scenarios — FULL repricing (Taylor is invalid for large moves) ─────

def reprice_scenario(positions: list[dict], move: float, vol_shock_pts: float, r: float,
                     include_stock: bool = False) -> float:
    """Book P&L if the market instantly moves `move` (each name × its beta) and vol
    jumps `vol_shock_pts`, by fully repricing every OPTION leg with Black-Scholes.

    COVERED CALLS: the shares behind a short call (`cov_shares`, capped at the calls' size) are part
    of the position. In a RALLY their gain offsets the short call — a covered call is never a cash
    loss on the way up, it only forgoes upside — so that gain is ALWAYS counted. On the way DOWN their
    loss is counted only when the position carries `down_stock` (the user's "include stock losses"
    choice); otherwise the downside is the OPTION OVERLAY only, the shares being a separate core
    holding. Naked / spread shorts have no `cov_shares` and are unaffected.
    (include_stock=True revalues EVERY share both ways — an economic-exposure view.)"""
    total = 0.0
    for p in positions:
        s_crash = p["spot"] * (1 + move * p.get("beta", 1.0))
        for lg in p["legs"]:
            ot = "call" if lg["right"] == "C" else "put"
            iv0 = lg["iv"]
            iv1 = max(0.02, iv0 + vol_shock_pts / 100.0)
            v0 = bs_price(p["spot"], lg["strike"], lg["dte_years"], r, iv0, ot)
            v1 = bs_price(s_crash, lg["strike"], lg["dte_years"], r, iv1, ot)
            total += lg["sign"] * lg["qty"] * _MULT * (v1 - v0)
        if include_stock and p.get("shares"):
            total += p["shares"] * (s_crash - p["spot"])   # signed: long>0 loses on the way down
        elif p.get("cov_shares"):
            d_stock = p["cov_shares"] * (s_crash - p["spot"])
            if d_stock >= 0 or p.get("down_stock"):         # gains always; losses only when asked
                total += d_stock
    return total


# ── full-revaluation Monte Carlo (fat-tailed market factor + skew) ───────────

def book_mc(positions: list[dict], r: float, mkt_vol: float, idx_spot: float,
            horizon_td: int = _HORIZON_TD, n_sims: int = 20000, seed: int = 7) -> dict:
    """1-month VaR/CVaR by FULLY REPRICING the book over a fat-tailed market factor.

    Market return ~ Student-t(df) scaled to the horizon; each name = β·market + its
    idiosyncratic move; vol rises as spot falls (skew). Every leg is repriced with BS
    at the simulated spot+vol (instantaneous reval). Returns the loss distribution +
    the simulated INDEX path so hedges can be repriced on the SAME scenarios."""
    rng = np.random.default_rng(seed)
    dt = horizon_td / 252.0          # year-fraction elapsed at the risk horizon…
    h = math.sqrt(dt)                # …the SAME dt scales the vol move (√t) and the decay (t)
    # standardized Student-t (unit variance) → market log-return over the horizon.
    t = rng.standard_t(_TDF, n_sims) * math.sqrt((_TDF - 2) / _TDF)
    mkt = t * mkt_vol * h
    pnl = np.zeros(n_sims)
    # Taylor attribution of the SAME sims (delta / gamma / vega / theta; the rest is higher-order + expiry effects),
    # so the tail can be read as "how much of the bad month is direction, convexity or vol".
    c_delta, c_gamma, c_vega, c_theta = (np.zeros(n_sims) for _ in range(4))
    for p in positions:
        beta = p.get("beta", 1.0)
        tot_vol = max(p.get("iv") or 0.3, 0.05)
        idio_vol = math.sqrt(max(0.0, tot_vol ** 2 - (beta * mkt_vol) ** 2))
        name_ret = beta * mkt + rng.standard_normal(n_sims) * idio_vol * h - 0.5 * tot_vol ** 2 * dt
        s_sim = p["spot"] * np.exp(name_ret)
        for lg in p["legs"]:
            iv1 = np.maximum(0.02, lg["iv"] - _SKEW_K * (s_sim / p["spot"] - 1.0))
            v0 = bs_price(p["spot"], lg["strike"], lg["dte_years"], r, lg["iv"],
                          "call" if lg["right"] == "C" else "put")
            # reprice at the HORIZON, not today: T shrinks by dt → the book banks the
            # month of decay (carry) AND runs hotter gamma; legs that expire inside the
            # month settle at intrinsic (real assignment/worthless outcome, not BS value).
            t1 = lg["dte_years"] - dt
            if t1 > 1e-4:
                v1 = _bs_vec(s_sim, lg["strike"], t1, r, iv1, lg["right"])
            elif lg["right"] == "C":
                v1 = np.maximum(0.0, s_sim - lg["strike"])
            else:
                v1 = np.maximum(0.0, lg["strike"] - s_sim)
            pnl += lg["sign"] * lg["qty"] * _MULT * (v1 - v0)
        # Shares behind a covered call: their GAIN always offsets the short call; their LOSS only when
        # the position carries `down_stock` (see reprice_scenario).
        d_stock = np.zeros(n_sims)
        if p.get("cov_shares"):
            d_stock = p["cov_shares"] * (s_sim - p["spot"])
            d_stock = d_stock if p.get("down_stock") else np.maximum(d_stock, 0.0)
            pnl += d_stock
        ds = s_sim - p["spot"]
        dvol_pts = -_SKEW_K * (s_sim / p["spot"] - 1.0) * 100.0          # the legs' vol rule, in vol points
        c_delta += (p.get("net_delta") or 0.0) * ds + d_stock             # covered shares are delta too
        c_gamma += 0.5 * (p.get("net_gamma") or 0.0) * ds * ds
        c_vega += (p.get("net_vega") or 0.0) * dvol_pts
        c_theta += (p.get("net_theta") or 0.0) * (dt * 365.0)
    idx_sim = idx_spot * np.exp(mkt)
    return {"pnl": pnl, "idx_sim": idx_sim, "idx_spot": idx_spot, "horizon_years": dt,
            "components": {"delta": c_delta, "gamma": c_gamma, "vega": c_vega, "theta": c_theta},
            **_tail_stats(pnl)}


def _tail_attribution(mc: dict) -> Optional[dict]:
    """What the worst-5% months are MADE OF: the average P&L of those sims split into delta / gamma / vega /
    theta (+ higher-order), and whether they are rallies or selloffs. Deterministic — the same sims as CVaR."""
    pnl, comps = mc.get("pnl"), mc.get("components")
    if pnl is None or not comps:
        return None
    losses = -pnl
    tail = losses >= np.percentile(losses, 95)
    if not tail.any():
        return None
    avg = lambda a: float(np.mean(a[tail]))
    parts = {k: avg(v) for k, v in comps.items()}
    total = avg(pnl)
    other = total - sum(parts.values())
    up_pct = float(np.mean(mc["idx_sim"][tail] > mc["idx_spot"])) * 100.0
    return {"avg_tail_pnl": round(total, 0), "n_sims": int(tail.sum()),
            "delta": round(parts["delta"], 0), "gamma": round(parts["gamma"], 0), "vega": round(parts["vega"], 0),
            "theta": round(parts["theta"], 0), "other": round(other, 0),
            "up_pct": round(up_pct, 0), "down_pct": round(100.0 - up_pct, 0),
            "side": "up" if up_pct >= 50.0 else "down"}


def _tail_stats(pnl: np.ndarray) -> dict:
    losses = -pnl
    v95, v99 = np.percentile(losses, 95), np.percentile(losses, 99)
    return {
        "var_95": round(float(v95), 0), "var_99": round(float(v99), 0),
        "cvar_95": round(float(losses[losses >= v95].mean()), 0),
        "cvar_99": round(float(losses[losses >= v99].mean()), 0),
        "worst_1pct_move": None,
    }


def _cvar95_of(pnl: np.ndarray) -> float:
    losses = -pnl
    v = np.percentile(losses, 95)
    return float(losses[losses >= v].mean())


# ── Spitznagel cost-vs-drag ──────────────────────────────────────────────────

def spitznagel_cost_vs_drag(*, annual_bleed: float, crash_loss: float, hedge_payoff: float,
                            crash_prob_annual: float, book_capital: float, annual_income: float) -> dict:
    """Does capping the crash lift the book's COMPOUND growth (remove the volatility
    tax), not merely pay off arithmetically? CAGR with vs without the overlay over
    {normal year (1−p) · crash year (p)}."""
    cap = max(book_capital, 1.0)
    p = min(max(crash_prob_annual, 0.0), 0.5)
    r_norm = annual_income / cap
    loss_frac = min(crash_loss / cap, 0.99)
    bleed_frac = annual_bleed / cap
    hedged_loss = min(max(loss_frac - hedge_payoff / cap, 0.0), 0.99)

    def _cagr(rn, lf):
        return math.exp((1 - p) * math.log(max(1e-6, 1 + rn)) + p * math.log(max(1e-6, 1 - lf))) - 1.0

    cu, ch = _cagr(r_norm, loss_frac), _cagr(r_norm - bleed_frac, hedged_loss)
    return {
        "annual_bleed": round(annual_bleed, 0),
        "cagr_unhedged_pct": round(cu * 100, 2), "cagr_hedged_pct": round(ch * 100, 2),
        "cagr_lift_pct": round((ch - cu) * 100, 2), "cost_effective": bool(ch > cu),
        "crash_prob_annual_pct": round(p * 100, 1),
    }


# ── async orchestration ──────────────────────────────────────────────────────

def _native(o):
    if isinstance(o, dict):
        return {k: _native(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_native(x) for x in o]
    if isinstance(o, np.generic):
        return o.item()
    return o


def _compute_betas(tickers: list[str]) -> dict:
    """β vs S&P 500 from ~1y daily simple returns (Cov/Var), PAIRWISE-complete per name.

    Two deliberate choices for correctness:
      • pairwise dropna per ticker (not one global dropna) — so a single illiquid or
        unlisted symbol can't blank the whole row grid and collapse the book to β=1;
        var_m is measured on the SAME window as each covariance.
      • winsorize to [−0.5, 3.0] instead of flooring at 0 — a defensive / negative-β
        name (KO, GLD, TLT) genuinely RISES in a sell-off, and that crash cushioning
        must survive into reprice_scenario/book_mc; the −0.5 floor and 3.0 cap only
        trim 1-year-noise extremes. Simple (not log) returns = the textbook CAPM β."""
    betas: dict[str, float] = {}
    uniq = list(dict.fromkeys(tickers))
    if not uniq:
        return betas
    try:
        import pandas as pd
        import yfinance as yf
        data = yf.download([_BENCH] + uniq, period="1y", progress=False, auto_adjust=True)["Close"]
        if data is None or getattr(data, "empty", True) or _BENCH not in data.columns:
            return betas
        rets = data.pct_change()
        mkt = rets[_BENCH]
        for t in uniq:
            if t not in rets.columns:
                continue
            pair = pd.concat([rets[t], mkt], axis=1).dropna()
            pair.columns = ["a", "m"]
            if len(pair) < 30:
                continue
            var_m = float(pair["m"].var())
            if var_m <= 0:
                continue
            b = float(pair["a"].cov(pair["m"])) / var_m
            if math.isfinite(b):
                betas[t] = round(min(3.0, max(-0.5, b)), 2)
    except Exception as exc:  # noqa: BLE001
        logger.debug("beta compute failed: %s", exc)
    return betas


# ── Implied-vol sanity: a quote's IV must reprice to the quote's own price ─────────────────────────────
_IV_MIN, _IV_MAX = 0.03, 5.0


def _solve_iv(price: float, S: float, K: float, T: float, r: float, ot: str) -> Optional[float]:
    """Implied vol that makes Black-Scholes equal `price` (bisection). None when the price is at/below the
    no-vol floor (a deep-ITM option quoted at intrinsic carries no vol information) or above the cap."""
    if not (price > 0 and S > 0 and K > 0 and T > 0):
        return None
    lo, hi = 0.01, _IV_MAX
    if price <= bs_price(S, K, T, r, lo, ot) + 1e-6 or price >= bs_price(S, K, T, r, hi, ot):
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, r, mid, ot) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _sane_iv(q: Optional[dict], S: float, K: float, T: float, r: float, right: str, fallback: float) -> tuple[float, str]:
    """The IV to use for one leg, and where it came from.

    yfinance's `impliedVolatility` is junk whenever the market is closed or a strike has no two-sided quote (values
    like 1e-5) while the PRICE (last / mid) is fine — feeding that IV into the Greeks made θ, vega and the stress P&Ls
    collapse to ~0. So the chain IV is trusted only if it is plausible AND reprices to the quote's own price; otherwise
    the vol is re-solved from that price; only with no usable price at all do we fall back to the trade's own IV / 30%.
        'chain'    — the quote's IV, cross-checked against its price (or plausible with no price to check)
        'solved'   — re-derived from the live/last price because the quote's IV was missing or inconsistent
        'fallback' — no usable price or IV: the trade's recorded IV (or 30%) — an approximation"""
    ot = "call" if right == "C" else "put"
    px = None
    if q:
        px = q.get("mid") if (q.get("mid") or 0) > 0 else (q.get("last") if (q.get("last") or 0) > 0 else None)
    iv_q = (q or {}).get("iv")
    plausible = iv_q is not None and _IV_MIN <= iv_q <= _IV_MAX
    if plausible and px is None:
        return float(iv_q), "chain"
    if plausible and abs(bs_price(S, K, T, r, iv_q, ot) - px) <= max(0.15 * px, 0.05):
        return float(iv_q), "chain"
    if px is not None:
        solved = _solve_iv(px, S, K, T, r, ot)
        if solved is not None:
            return solved, "solved"
    return float(fallback), "fallback"


def _quote_dict(q) -> Optional[dict]:
    """A provider OptionQuote → the few live fields a fix needs (bid/ask/mid/iv), or None."""
    if q is None:
        return None
    def _f(x):
        try:
            v = float(x)
            return v if v == v else None   # NaN → None
        except (TypeError, ValueError):
            return None
    return {"bid": _f(getattr(q, "bid", None)), "ask": _f(getattr(q, "ask", None)),
            "mid": _f(getattr(q, "mid", None)), "last": _f(getattr(q, "last", None)), "iv": _f(getattr(q, "iv", None)),
            "oi": _f(getattr(q, "oi", None)), "volume": _f(getattr(q, "volume", None))}


def _pay_price(q: Optional[dict]) -> tuple[Optional[float], str]:
    """What it costs to BUY one share-equivalent of a leg from its live quote: the ask; the mid if there is
    no ask; (None, 'model') when the chain has no usable quote and the caller must fall back to a model price."""
    if not q:
        return None, "model"
    if (q.get("ask") or 0) > 0:
        return float(q["ask"]), "live ask"
    if (q.get("mid") or 0) > 0:
        return float(q["mid"]), "live mid"
    return None, "model"


async def _position_greeks(strategy, provider, r, today, spot_cache, chain_cache) -> Optional[dict]:
    from .hedging_service import _split_chain
    legs = json.loads(strategy.legs_data or "[]")
    opt = [l for l in legs if "call" in str(l.get("type", "")).lower() or "put" in str(l.get("type", "")).lower()]
    if not opt:
        return None
    ticker = strategy.ticker
    if ticker not in spot_cache:
        try:
            uq = await provider.get_underlying_price(ticker)
            spot_cache[ticker] = float(getattr(uq, "price", None) or getattr(uq, "last", None) or 0.0)
        except Exception:  # noqa: BLE001
            spot_cache[ticker] = 0.0
    spot = spot_cache[ticker]
    if spot <= 0:
        return None

    life_legs, n_short, short_strikes = [], 0, []
    iv_audit = {"chain": 0, "solved": 0, "fallback": 0}      # where each leg's IV came from (surfaced as data quality)
    indicative = False                                        # any chain filled from last prices (market closed)
    strikes_by_exp: dict[str, dict] = {}   # exp → {"C":[listed call strikes], "P":[...]} for real-leg remediation
    quotes_by_exp: dict[str, dict] = {}    # exp → {"C": {strike: live quote}, "P": {...}} — what a fix will really PAY
    for l in opt:
        exp = str(l.get("expiration") or l.get("expiry") or "")[:10]
        if not exp:
            continue
        right = "C" if "call" in str(l.get("type", "")).lower() else "P"
        sign = -1 if any(k in str(l.get("action", "")).upper() for k in ("SELL", "SHORT")) else 1
        strike = float(l.get("strike") or 0)
        if sign < 0:
            n_short += 1
            short_strikes.append((strike, float(l.get("qty") or l.get("contracts") or 1)))   # (strike, contracts)
        key = (ticker, exp)
        if key not in chain_cache:
            try:
                chain_cache[key] = await provider.get_option_chain(ticker, exp)
            except Exception:  # noqa: BLE001
                chain_cache[key] = None
        chain, own_q = chain_cache[key], None
        try:
            dte = max(1, (date.fromisoformat(exp) - today).days)
        except (ValueError, TypeError):
            dte = 30
        if chain is not None:
            try:
                calls, puts = _split_chain(chain)
                side = calls if right == "C" else puts
                q = next((qq for k, qq in side.items() if abs(float(k) - strike) < 0.01), None)
                own_q = _quote_dict(q)
                if exp not in strikes_by_exp:   # capture the LISTED strikes + their live quotes once (chain already fetched)
                    strikes_by_exp[exp] = {"C": sorted(float(k) for k in calls.keys()),
                                           "P": sorted(float(k) for k in puts.keys())}
                    quotes_by_exp[exp] = {"C": {float(k): _quote_dict(v) for k, v in calls.items()},
                                          "P": {float(k): _quote_dict(v) for k, v in puts.items()}}
                indicative = indicative or ("indicative" in str(getattr(chain, "source", "") or ""))
            except Exception:  # noqa: BLE001
                own_q = None
        raw = l.get("iv")
        fb = (float(raw) / 100.0 if raw and float(raw) > 3 else float(raw)) if raw else 0.30   # the trade's own IV, else 30%
        iv, iv_src = _sane_iv(own_q, spot, strike, dte / 365.0, r, right, fb)
        iv_audit[iv_src] += 1
        life_legs.append({"strike": strike, "right": right, "sign": sign,
                          "qty": float(l.get("qty") or l.get("contracts") or 1),
                          "iv": iv, "iv_src": iv_src, "dte_years": dte / 365.0, "exp": exp, "dte_days": dte,
                          # the leg's OWN live quote (None when the chain had none) — real cost to close, real extrinsic
                          "bid": (own_q or {}).get("bid"), "ask": (own_q or {}).get("ask"), "mid": (own_q or {}).get("mid")})
    if not life_legs:
        return None
    g = higher_order_greeks(life_legs, spot, r=r, stock_shares=0.0)   # OPTION LAYER ONLY
    avg_iv = sum(x["iv"] for x in life_legs) / len(life_legs)
    capital = sum(sk * _MULT * q for sk, q in short_strikes) or abs(g["net_delta"]) * spot
    # structure + stock presence → so a short CALL can be flagged COVERED (excluded from the
    # naked-assignment total) vs naked. Any non-option long-share leg also counts as cover.
    structure = getattr(strategy, "strategy_type", None) or getattr(strategy, "structure", None)
    # Signed share exposure behind the overlay (covered-call/collar long stock, or any holding) —
    # the economic leg the crash / CVaR / ladder must revalue. Prefer parameters.shares (how the
    # combo P&L path stores it); fall back to a stock/share leg in legs_data. long>0, short<0.
    shares = 0.0
    _sparams: dict = {}
    try:
        _sparams = json.loads(strategy.parameters or "{}")
        shares = float(_sparams.get("shares") or 0)
    except (ValueError, TypeError):
        shares = 0.0
    # Explicit "covered" mark — the user holds the shares ELSEWHERE (not added to the income book), so a
    # short call is covered for risk/assignment even with no stock leg here. Respected like a covered_call
    # structure / a real stock leg (see _is_covered).
    covered_flag = bool(_sparams.get("covered"))
    if not shares:
        for l in legs:
            if "stock" in str(l.get("type", "")).lower() or "share" in str(l.get("type", "")).lower():
                try:
                    q = abs(float(l.get("shares") or l.get("qty") or 0))
                except (ValueError, TypeError):
                    q = 0.0
                if q:
                    shares += (-1 if any(k in str(l.get("action", "")).upper() for k in ("SELL", "SHORT")) else 1) * q
    has_stock = bool(shares)
    pos = {"ticker": ticker, "name": strategy.name, "trade_id": getattr(strategy, "id", None),
           "spot": spot, "iv": avg_iv,
           "n_short": n_short, "capital": round(capital, 0), "legs": life_legs, "shares": round(shares, 2),
           "strikes_by_exp": strikes_by_exp, "quotes_by_exp": quotes_by_exp, "structure": structure,
           "has_stock": bool(has_stock), "covered": covered_flag, **g}
    pos["cov_shares"] = _covered_shares(pos)    # shares behind the short calls — scenarios net their P&L
    pos["iv_audit"], pos["indicative"] = iv_audit, indicative
    return pos


async def _index_puts(provider, dte_days: int, today: date):
    """Live SPX put chain (nearest ~dte_days expiry) as {strike: quote}, or None."""
    from .hedging_service import _split_chain
    try:
        exps = await provider.get_option_expirations(_SPX_OPT)
    except Exception:  # noqa: BLE001
        exps = None
    if not exps:
        return None, None

    def _dist(e):
        try:
            return abs((date.fromisoformat(str(e)[:10]) - today).days - dte_days)
        except (ValueError, TypeError):
            return 10 ** 6
    exp = min(exps, key=_dist)
    try:
        _, puts = _split_chain(await provider.get_option_chain(_SPX_OPT, exp))
    except Exception:  # noqa: BLE001
        return None, None
    if not puts:
        return None, None
    try:
        adte = max(1, (date.fromisoformat(str(exp)[:10]) - today).days)
    except (ValueError, TypeError):
        adte = dte_days
    return puts, {"exp": str(exp)[:10], "dte": adte}


def _price_put(puts, idx, otm_frac, iv, T, r):
    """(strike, mid$) for a put ~otm_frac OTM — from the live chain if listed, else BS."""
    target = idx * (1 - otm_frac)
    if puts:
        strikes = [k for k in puts if k <= idx]
        if strikes:
            k = min(strikes, key=lambda x: abs(x - target))
            q = puts.get(k)
            if q is not None and getattr(q, "mid", None):
                return float(k), float(q.mid) * _MULT
    k = round(target, 0)
    return k, bs_price(idx, k, T, r, iv, "put") * _MULT


def _hedge_candidates(idx, iv, T, r, puts, book_pnl, idx_sim, cvar0,
                      book_loss20, hedge_target, crash_prob, book_capital, annual_income, rolls,
                      horizon_years=0.0):
    """Build a MENU of index tail hedges; for each, reprice on the SAME MC scenarios to
    get the TRUE book CVaR reduction, then rank by risk-reduced-per-$ and CAGR lift.

    The hedge is repriced at the SAME risk horizon as the book (T → T − horizon_years),
    so its CVaR offset is apples-to-apples with the horizon-decayed book P&L."""
    t_h = max(1e-4, T - horizon_years)   # hedge time-to-expiry AT the risk horizon
    designs = [
        ("Put spread 15%/30%", 0.15, 0.30),
        ("Put spread 10%/25%", 0.10, 0.25),
        ("Put spread 20%/35%", 0.20, 0.35),
        ("Outright put 15% OTM", 0.15, None),
    ]
    idx_crash = idx * (1 + _HEDGE_SCEN[0])
    out = []
    for label, lo, so in designs:
        long_k, long_cost = _price_put(puts, idx, lo, iv, T, r)
        if so is not None:
            short_k, short_cost = _price_put(puts, idx, so, iv, T, r)
        else:
            short_k, short_cost = 0.0, 0.0
        cost_per = max(0.0, long_cost - short_cost)
        payoff20 = max(0.0, (long_k - idx_crash) - (max(0.0, short_k - idx_crash))) * _MULT
        if cost_per <= 0 or payoff20 <= 0:
            continue
        n = max(1, round(hedge_target / payoff20))
        total_cost = n * cost_per
        annual_bleed = total_cost * rolls
        # reprice the hedge on the SAME MC index paths, at the SAME horizon (t_h) → true hedged CVaR.
        long_leg = _bs_vec(idx_sim, long_k, t_h, r, iv, "P")
        long_leg0 = bs_price(idx, long_k, T, r, iv, "put")
        hedge_pnl = (long_leg - long_leg0) * _MULT
        if so is not None:
            short_leg = _bs_vec(idx_sim, short_k, t_h, r, iv, "P")
            hedge_pnl = hedge_pnl - (short_leg - bs_price(idx, short_k, T, r, iv, "put")) * _MULT
        cvar_h = _cvar95_of(book_pnl + n * hedge_pnl)
        cvar_red = cvar0 - cvar_h
        spitz = spitznagel_cost_vs_drag(annual_bleed=annual_bleed, crash_loss=book_loss20,
                                        hedge_payoff=n * payoff20, crash_prob_annual=crash_prob,
                                        book_capital=book_capital, annual_income=annual_income)
        out.append({
            "label": label, "instrument": "SPX", "kind": "put spread" if so else "put",
            "long_strike": long_k, "short_strike": short_k if so else None,
            "long_put": long_k, "short_put": short_k if so else None,   # back-compat aliases
            "contracts": n, "cost_per_spread": round(cost_per, 0), "total_cost": round(total_cost, 0),
            "annual_bleed": round(annual_bleed, 0), "crash_payoff_20": round(n * payoff20, 0),
            "offsets_pct": round(n * payoff20 / book_loss20 * 100, 0) if book_loss20 else None,
            "cvar_reduction": round(cvar_red, 0),
            "efficiency": round(cvar_red / annual_bleed, 2) if annual_bleed > 0 else None,   # $ CVaR cut per $/yr spent
            "cagr_lift_pct": spitz["cagr_lift_pct"], "cost_effective": spitz["cost_effective"],
            # private: ONE contract's P&L over the MC sims + its annual cost — lets the plan re-size to a budget
            "_unit_pnl": hedge_pnl, "_unit_bleed": cost_per * rolls, "_max_units": 200,
            "_unit": "spread" if so else "put",
        })
    return out


def _hedge_call_candidates(idx, iv, T, r, book_pnl, idx_sim, rolls, horizon_years=0.0):
    """SPX CALL SPREADS — the hedge for an UPSIDE-driven tail. A short-call / short-gamma book loses in a
    melt-up; index puts and VIX calls do nothing there. Priced with Black-Scholes at the index vol (no listed
    call chain is fetched, so this is a model price and is labelled as one); repriced on the SAME MC paths at
    the SAME horizon, with vol crushing on the rally exactly as the book's own legs do."""
    t_h = max(1e-4, T - horizon_years)
    out = []
    for label, lo, so in (("Call spread 3%/12%", 0.03, 0.12), ("Call spread 5%/15%", 0.05, 0.15),
                          ("Call spread 10%/25%", 0.10, 0.25)):
        lk, sk = round(idx * (1 + lo) / 5.0) * 5.0, round(idx * (1 + so) / 5.0) * 5.0
        if sk <= lk:
            continue
        v0l, v0s = bs_price(idx, lk, T, r, iv, "call"), bs_price(idx, sk, T, r, iv, "call")
        cost_per = max(0.0, v0l - v0s) * _MULT
        if cost_per <= 0:
            continue
        iv_h = np.maximum(0.02, iv - _SKEW_K * (idx_sim / idx - 1.0))
        vh = (_bs_vec(idx_sim, lk, t_h, r, iv_h, "C") - _bs_vec(idx_sim, sk, t_h, r, iv_h, "C"))
        out.append({"label": label, "instrument": "SPX", "kind": "call spread", "long_strike": lk, "short_strike": sk,
                    "cost_per_spread": round(cost_per, 0), "pricing": "model (BS · index vol)",
                    "_unit_pnl": (vh - (v0l - v0s)) * _MULT, "_unit_bleed": cost_per * rolls, "_max_units": 200,
                    "_unit": "spread"})
    return out


def _rank_hedges(out: list[dict]) -> list[dict]:
    """Rank a MIXED hedge menu (SPX puts + VIX calls) together: cost-effective first, then
    most book-CVaR reduced per $/yr spent. Flags the single best as recommended."""
    out.sort(key=lambda c: (not c.get("standby", False), c.get("cost_effective", False), c.get("efficiency") or -1), reverse=True)
    first = next((c for c in out if not c.get("standby")), None)     # the standby sleeve is never the pick: its
    for c in out:                                                    # ~zero assumed cost would always rank it first
        c["recommended"] = c is first
    return out


# ── VIX black-swan overlay — call spreads that only pay in a genuine vol spike ─────

def _vix_future(mkt_ret, base_fwd: float, cap: float = 80.0):
    """Terminal VIX FUTURE level for a horizon SPX log-return, from the empirical crash
    curve. Vectorized (scalar or array). Dampened vs SPOT-VIX peaks (the future mean-
    reverts) so the VIX-hedge payoff isn't overstated. Floors near the current forward."""
    bump = np.interp(mkt_ret, _VIX_CURVE_X, _VIX_CURVE_Y)   # np.interp clamps outside the range
    floor = max(9.0, base_fwd - 5.0)
    return np.clip(base_fwd + bump, floor, cap)


def _vix_levels() -> tuple:
    """(spot_vix, forward_vix) from ^VIX and ^VIX3M. Forward ≈ 3-month vol — the right base
    for a ~90d hedge (the term structure is usually in contango, so 3M > spot)."""
    try:
        import yfinance as yf
        d = yf.download([_VIX_IDX, _VIX_3M], period="5d", progress=False, auto_adjust=True)["Close"]
        spot = float(d[_VIX_IDX].dropna().iloc[-1]) if _VIX_IDX in d else None
        fwd = float(d[_VIX_3M].dropna().iloc[-1]) if _VIX_3M in d and not d[_VIX_3M].dropna().empty else spot
        return spot, (fwd or spot)
    except Exception as exc:  # noqa: BLE001
        logger.debug("VIX levels fetch failed: %s", exc)
        return None, None


async def _vix_calls(provider, dte_days: int, today: date):
    """Live ^VIX CALL chain {strike: quote} nearest ~dte_days, + meta. VIX options are
    European and cash-settled on the VIX future, so we take REAL chain mids for cost and
    value the payoff on the future — never a naive BS on spot VIX."""
    from .hedging_service import _split_chain
    try:
        exps = await provider.get_option_expirations(_VIX_IDX)
    except Exception:  # noqa: BLE001
        exps = None
    if not exps:
        return None, None

    def _dist(e):
        try:
            return abs((date.fromisoformat(str(e)[:10]) - today).days - dte_days)
        except (ValueError, TypeError):
            return 10 ** 6
    exp = min(exps, key=_dist)
    try:
        calls, _ = _split_chain(await provider.get_option_chain(_VIX_IDX, exp))
    except Exception:  # noqa: BLE001
        return None, None
    if not calls:
        return None, None
    try:
        adte = max(1, (date.fromisoformat(str(exp)[:10]) - today).days)
    except (ValueError, TypeError):
        adte = dte_days
    return calls, {"exp": str(exp)[:10], "dte": adte}


def _vix_hedge_candidates(vix_calls, fwd_vix, idx, idx_sim, book_pnl, cvar0, book_loss20,
                          hedge_target, crash_prob, book_capital, annual_income, rolls):
    """VIX CALL-SPREAD black-swan hedges (multi-leg → cost-capped). VIX explodes in a crash,
    so deep-OTM call spreads are cheap convexity that ONLY pay in a genuine vol event and do
    nothing in an ordinary drawdown (which diversification already handles). Cost from the
    REAL ^VIX chain; payoff valued at intrinsic on the VIX future implied by each MC SPX path
    (idx_sim → SPX return → VIX future) — the SAME scenarios as the SPX hedges, so CVaR
    reduction is apples-to-apples. Intrinsic (no residual time value) is deliberately
    CONSERVATIVE — it understates the hedge, never flatters it."""
    if not vix_calls or not fwd_vix:
        return []
    # Onset strikes span moderate → deep black-swan. VIX ~15 now, so a 30 onset only
    # triggers on a real vol event (SPX ≈ −7%); 40 is a genuine crash (SPX ≈ −12%).
    designs = [("VIX 25/50 call spread", 25.0, 50.0),   # cost-reduced, earlier onset
               ("VIX 30/60 call spread", 30.0, 60.0),   # black-swan
               ("VIX 40/80 call spread", 40.0, 80.0)]   # deep black-swan, cheapest convexity
    mkt_ret = np.log(np.maximum(idx_sim / idx, 1e-6))            # per-path SPX log-return over the horizon
    vix_term = _vix_future(mkt_ret, fwd_vix)                     # terminal VIX future per path
    vix20 = float(_vix_future(math.log(1 + _HEDGE_SCEN[0]), fwd_vix))   # VIX future at a −20% month

    def _pick(target):
        ks = [s for s in vix_calls if s >= target - 1e-9]
        if not ks:
            return None, None
        k = min(ks, key=lambda s: abs(s - target))
        q = vix_calls.get(k)
        mid = getattr(q, "mid", None) if q is not None else None
        return (float(k), float(mid)) if (mid and mid > 0) else (float(k), None)

    out = []
    for label, lo, so in designs:
        lk, lmid = _pick(lo)
        sk, smid = _pick(so)
        if lk is None or sk is None or lmid is None or smid is None or sk <= lk:
            continue
        width = sk - lk
        cost_per = max(0.0, lmid - smid) * _VIX_MULT
        payoff20 = min(max(0.0, vix20 - lk), width) * _VIX_MULT
        if cost_per <= 0 or payoff20 <= 0:
            continue
        n = max(1, round(hedge_target / payoff20))
        total_cost = n * cost_per
        annual_bleed = total_cost * rolls
        v1 = np.minimum(np.maximum(vix_term - lk, 0.0), width) * _VIX_MULT   # spread value at horizon (intrinsic)
        hedge_pnl = n * (v1 - cost_per)                                      # paid cost_per → worth v1
        cvar_red = cvar0 - _cvar95_of(book_pnl + hedge_pnl)
        spitz = spitznagel_cost_vs_drag(annual_bleed=annual_bleed, crash_loss=book_loss20,
                                        hedge_payoff=n * payoff20, crash_prob_annual=crash_prob,
                                        book_capital=book_capital, annual_income=annual_income)
        out.append({
            "label": label, "instrument": "VIX", "kind": "call spread",
            "long_strike": lk, "short_strike": sk,
            "long_put": lk, "short_put": sk,                    # aliases so the table renders uniformly
            "contracts": n, "cost_per_spread": round(cost_per, 0), "total_cost": round(total_cost, 0),
            "annual_bleed": round(annual_bleed, 0), "crash_payoff_20": round(n * payoff20, 0),
            "offsets_pct": round(n * payoff20 / book_loss20 * 100, 0) if book_loss20 else None,
            "cvar_reduction": round(cvar_red, 0),
            "efficiency": round(cvar_red / annual_bleed, 2) if annual_bleed > 0 else None,
            "cagr_lift_pct": spitz["cagr_lift_pct"], "cost_effective": spitz["cost_effective"],
            "vix_at_minus20": round(vix20, 1),
            "_unit_pnl": v1 - cost_per, "_unit_bleed": cost_per * rolls, "_max_units": 500, "_unit": "spread",
        })
    return out


def _vixy_dynamic_candidate(spot_vix, fwd_vix, idx, idx_sim, book_pnl, cvar0, book_loss20,
                            hedge_target, crash_prob, book_capital, annual_income):
    """DYNAMIC VIXY sleeve — the (near) zero-COST hedge. Hold a cash sleeve and deploy it
    into a short-term VIX-futures ETF (VIXY) ONLY when the term structure INVERTS (spot VIX
    > 3-month VIX = backwardation = stress onset). This dodges the constant contango roll-
    decay of holding VIXY permanently — you pay almost nothing in calm markets and only
    carry the position while a cascade is actually building.

    Honest modelling of the trade-offs:
      • cost ≈ a small whipsaw/friction only (the sleeve otherwise sits in cash earning the
        risk-free rate) — NOT an options premium bleed. That's the whole point.
      • BUT a signal-based deploy LAGS a gap-down crash, so we haircut the payoff by a
        capture factor; and it ties up cash. Both are surfaced, not hidden."""
    ref = (spot_vix or fwd_vix)
    if not ref or ref <= 0:
        return []
    front = ref * 1.03                 # VIXY tracks the ~1-mo future — mild contango over spot
    invert_thresh = -0.05              # SPX horizon return that flips the curve into backwardation
    capture = 0.65                     # deploy lags a gap → partial capture (conservative)
    whipsaw = 0.015                    # ~1.5%/yr friction from entries/exits & false signals
    ret20 = float(_vix_future(math.log(1 + _HEDGE_SCEN[0]), front) / front - 1.0)   # VIXY % at −20%
    if ret20 <= 0:
        return []
    sleeve = hedge_target / (ret20 * capture)          # cash sized so its −20% gain hits the target
    annual_bleed = max(1.0, sleeve * whipsaw)
    mkt_ret = np.log(np.maximum(idx_sim / idx, 1e-6))
    vixy_ret = _vix_future(mkt_ret, front) / front - 1.0
    deployed = mkt_ret < invert_thresh                 # only long VIXY when the curve inverts
    payoff = sleeve * capture * np.where(deployed, np.maximum(vixy_ret, 0.0), 0.0)
    hedge_pnl = payoff - annual_bleed / 12.0           # ~monthly friction over the horizon
    cvar_red = cvar0 - _cvar95_of(book_pnl + hedge_pnl)
    payoff20 = sleeve * capture * ret20
    spitz = spitznagel_cost_vs_drag(annual_bleed=annual_bleed, crash_loss=book_loss20,
                                    hedge_payoff=payoff20, crash_prob_annual=crash_prob,
                                    book_capital=book_capital, annual_income=annual_income)
    return [{
        "label": "Dynamic VIXY sleeve", "instrument": "VIXY", "kind": "signal-based",
        "long_strike": None, "short_strike": None, "long_put": None, "short_put": None,
        "contracts": None, "sleeve_capital": round(sleeve, 0),
        "cost_per_spread": 0, "total_cost": round(sleeve, 0),
        "annual_bleed": round(annual_bleed, 0), "crash_payoff_20": round(payoff20, 0),
        "offsets_pct": round(payoff20 / book_loss20 * 100, 0) if book_loss20 else None,
        "cvar_reduction": round(cvar_red, 0),
        "efficiency": round(cvar_red / annual_bleed, 2) if annual_bleed > 0 else None,
        "cagr_lift_pct": spitz["cagr_lift_pct"], "cost_effective": spitz["cost_effective"],
        "signal": "deploy when spot VIX > VIX3M (backwardation)", "capture_pct": round(capture * 100),
        "standby": True,        # a rule that arms on a signal — never "the trade to put on today"
        # unit = a $1,000 slice of the sleeve (P&L and friction both scale linearly with it); cap at 20% of capital
        "_unit_pnl": hedge_pnl / max(sleeve, 1.0) * 1000.0, "_unit_bleed": 1000.0 * whipsaw,
        "_max_units": int(max(0.0, 0.20 * book_capital) // 1000), "_unit": "$1k sleeve",
    }]


# ── Budget-driven hedge plan: size insurance to a share of the INCOME it must be paid out of ──────────
_HEDGE_BUDGETS = (5, 10, 15, 25)     # % of annual option income the insurance may cost
_DEFAULT_BUDGET = 10
_PRIVATE = ("_unit_pnl", "_unit_bleed", "_max_units", "_unit")
_TARGETS = {"put": ["Downside tail", "Net direction"], "put spread": ["Downside tail", "Net direction"],
            "call spread": ["Upside tail (melt-up)"], "VIX": ["Vol spike", "Crash tail"], "VIXY": ["Vol spike", "Crash tail"]}


def _strip_private(items: list[dict]) -> None:
    for c in items:
        for k in _PRIVATE:
            c.pop(k, None)


def _hedge_plan(items: list[dict], *, mc: dict, cvar0: float, annual_income: float, book_capital: float,
                tail: Optional[dict]) -> dict:
    """Re-size every candidate hedge to a BUDGET (a % of annual income) instead of to a loss target, and
    measure what it does to the book's CVaR on the SAME MC sims. A hedge whose payoff is outside the book's
    tail (a put against a rally-driven tail) shows a ~0 or negative CVaR change at every budget — it is
    flagged `helps: false` with the reason, instead of being ranked as if it were insurance."""
    pnl = mc["pnl"]
    cap = max(float(book_capital or 0.0), 1.0)
    income_ok = annual_income > 0
    lim_warn, lim_breach = _GUARDRAILS["cvar"]["warn"] * cap, _GUARDRAILS["cvar"]["breach"] * cap
    gap = max(0.0, cvar0 - lim_warn)
    side, up_pct = (tail or {}).get("side"), (tail or {}).get("up_pct")
    min_useful = max(100.0, 0.02 * cvar0)
    cands = []
    for item in items:
        c = dict(item)
        up, ub, mx, unit = (c.pop(k) for k in _PRIVATE)
        kind = c.get("kind") or ""
        key = "VIXY" if c.get("instrument") == "VIXY" else ("VIX" if c.get("instrument") == "VIX" else kind)
        plans = []
        for b in _HEDGE_BUDGETS:
            n = int(min((annual_income * b / 100.0) // ub, mx)) if (income_ok and ub > 0) else 0
            if n < 1:
                plans.append({"budget_pct": b, "units": 0})
                continue
            after = _cvar95_of(pnl + n * up)
            red = cvar0 - after
            cost = n * ub
            plans.append({"budget_pct": b, "units": n, "annual_cost": round(cost, 0),
                          "cost_pct_income": round(cost / annual_income * 100, 1),
                          "cvar_after": round(after, 0), "cvar_reduction": round(red, 0),
                          "reduction_pct": round(red / cvar0 * 100, 1) if cvar0 > 0 else None,
                          "closes_gap_pct": round(min(100.0, max(0.0, red / gap * 100)), 0) if gap > 0 else None,
                          "per_dollar": round(red / cost, 2) if cost > 0 else None})
        helps = any(pl["units"] >= 1 and pl["cvar_reduction"] >= min_useful for pl in plans)
        reason = None
        if not helps:
            if not any(pl["units"] >= 1 for pl in plans):
                reason = ("no income to fund insurance" if not income_ok
                          else f"one {unit} costs more than {_HEDGE_BUDGETS[-1]}% of your annual income")
            elif kind != "call spread" and side == "up" and (up_pct or 0) >= 60:
                reason = f"pays when the market FALLS, but {up_pct:.0f}% of your worst months are rallies"
            elif kind == "call spread" and side == "down" and (100 - (up_pct or 0)) >= 60:
                reason = f"pays in a RALLY, but {100 - (up_pct or 0):.0f}% of your worst months are selloffs"
            else:
                reason = "barely moves CVaR at any budget — its payoff sits outside your tail"
        cands.append({**c, "unit": unit, "unit_annual": round(ub, 0), "targets": _TARGETS.get(key, []),
                      "plans": plans, "helps": helps, "reason": reason, "standby": key == "VIXY"})

    def _score(c):   # CVaR cut at the default budget (else the largest budget it can afford)
        pl = next((p for p in c["plans"] if p["budget_pct"] == _DEFAULT_BUDGET and p["units"] >= 1), None) \
            or next((p for p in reversed(c["plans"]) if p["units"] >= 1), None)
        return pl["cvar_reduction"] if pl else float("-inf")
    cands.sort(key=lambda c: (c["helps"] and not c["standby"], c["helps"], _score(c)), reverse=True)
    for i, c in enumerate(cands):
        c["recommended"] = bool(i == 0 and c["helps"] and not c["standby"])
    return {"budgets": list(_HEDGE_BUDGETS), "default_budget": _DEFAULT_BUDGET, "income_ok": income_ok,
            "annual_income": round(annual_income, 0), "cvar_now": round(cvar0, 0),
            "cvar_pct_capital": round(cvar0 / cap * 100, 1), "limit_warn": round(lim_warn, 0),
            "limit_breach": round(lim_breach, 0), "gap": round(gap, 0), "tail": tail, "candidates": cands}


def _exposure_map(positions: list[dict], r: float, *, bw_delta_notional: float, beta_delta_spy, spy_price,
                  net_vega: float, book_capital: float, mkt_vol: float, hedge_dte_days: int) -> list[dict]:
    """Each Greek exposure of the book → what it costs in a standard shock (full reprice) → the instrument that
    neutralises it and the size. Options only (covered shares are a separate holding). Ties the hedge back to
    the risk it is for: some exposures are simply too small to be worth insuring."""
    cap = max(float(book_capital or 0.0), 1.0)
    opt = [{**p, "cov_shares": 0.0, "down_stock": False} for p in positions]
    pct = lambda v: round(v / cap * 100, 2)
    rows = []

    # 1) net direction (β-Δ) — linear in the move, so a delta hedge is exact
    dn, up = bw_delta_notional * -0.10, bw_delta_notional * 0.10
    n_spy = int(round(abs(beta_delta_spy))) if beta_delta_spy else 0
    rows.append({"key": "delta", "label": "Net direction (β-Δ)",
                 "value": (f"{beta_delta_spy:+,.0f} SPY-equiv shares" if beta_delta_spy is not None else "—"),
                 "shock": "market ±10% (β-weighted)", "impact_down": round(dn, 0), "impact_up": round(up, 0),
                 "worst": round(min(dn, up), 0), "worst_pct_capital": pct(min(dn, up)),
                 "hedge": ({"instrument": "SPY shares", "action": f"{'BUY' if beta_delta_spy < 0 else 'SELL'} {n_spy:,} SPY",
                            "units": n_spy, "notional": round(n_spy * spy_price, 0) if spy_price else None, "annual_cost": 0.0,
                            "note": "flattens β-Δ exactly — no premium, but it ties up the notional and removes the delta income too"}
                           if n_spy and spy_price else None)})

    # 2) net vega — full reprice of a pure vol move
    v_up = reprice_scenario(opt, 0.0, 10.0, r)
    hedge = None
    if net_vega < 0 and spy_price and mkt_vol > 0:
        T = max(hedge_dte_days, 7) / 365.0
        d1 = (r + 0.5 * mkt_vol ** 2) * T / (mkt_vol * math.sqrt(T))
        vega_ct = spy_price * norm.pdf(d1) * math.sqrt(T) / 100.0 * _MULT          # $ per vol-pt per SPY ATM option
        prem_ct = bs_price(spy_price, spy_price, T, r, mkt_vol, "call") * _MULT
        need = abs(net_vega) / vega_ct if vega_ct > 0 else 0.0
        hedge = {"instrument": f"SPY ~{max(hedge_dte_days, 7)}d ATM option", "units": round(need, 2),
                 "action": (f"BUY {max(1, round(need))}× SPY ATM option" if need >= 0.5 else "below one SPY option"),
                 "annual_cost": round(need * prem_ct * (365.0 / (T * 365.0)), 0), "vega_per_contract": round(vega_ct, 0),
                 "note": "assumes your names' IV moves ~1:1 with index IV; a long option also adds gamma/downside cover"}
    rows.append({"key": "vega", "label": "Net vega (short vol)", "value": f"{net_vega:,.0f} $/vol-pt",
                 "shock": "vol +10 pts, spot unchanged", "impact_up": round(v_up, 0), "impact_down": None,
                 "worst": round(min(v_up, 0.0), 0), "worst_pct_capital": pct(min(v_up, 0.0)), "hedge": hedge})

    # 3) gamma / convexity — what a ±10% move costs BEYOND the delta line
    g_dn = reprice_scenario(opt, -0.10, 0.0, r) - bw_delta_notional * -0.10
    g_up = reprice_scenario(opt, 0.10, 0.0, r) - bw_delta_notional * 0.10
    rows.append({"key": "gamma", "label": "Short gamma (convexity)", "value": "",
                 "shock": "market ±10%, beyond the delta line", "impact_down": round(g_dn, 0), "impact_up": round(g_up, 0),
                 "worst": round(min(g_dn, g_up), 0), "worst_pct_capital": pct(min(g_dn, g_up)),
                 "hedge": {"instrument": "Cap the short strikes (Fixes tab)", "action": "buy a wing on the offending shorts",
                           "units": None, "annual_cost": None,
                           "note": "targeted gamma cover is cheaper and more exact than an index option"}})
    return rows



def _lvl(x: float) -> str:
    return "Dangerous" if x >= 40 else "Elevated" if x >= 20 else "Moderate" if x >= 8 else "Contained"


def _verdict_and_actions(*, scenarios, short_vol, concentration, hedge_menu, cvar_pct, beta_delta_spy):
    """Plain-language read + concrete actions — TWO-SIDED. A short-gamma book loses on a big move
    EITHER way, so the level is set by the worse tail (down OR up), and the melt-up is named when it
    is the bigger one (a −20%-only verdict called a book with a huge squeeze tail 'Contained')."""
    downs = [s for s in (scenarios or []) if (s.get("move_pct") or 0) < 0 and s.get("pct_of_capital") is not None]
    ups = [s for s in (scenarios or []) if (s.get("move_pct") or 0) > 0 and s.get("pct_of_capital") is not None]
    worst_down = min(downs, key=lambda s: s["pct_of_capital"], default=None)
    worst_up = min(ups, key=lambda s: s["pct_of_capital"], default=None)
    crash20 = next((s for s in downs if abs((s.get("move_pct") or 0) + 0.20) < 1e-6), None) or worst_down
    d = abs((crash20 or {}).get("pct_of_capital") or 0)            # the "−20% month" downside reference
    d_worst = abs((worst_down or {}).get("pct_of_capital") or 0)   # deepest downside on the ladder
    u = abs((worst_up or {}).get("pct_of_capital") or 0)           # worst melt-up / squeeze
    u_label = (worst_up or {}).get("label") or "melt-up"
    worst = max(d_worst, u)
    level = _lvl(worst)
    up_bigger = u > d_worst + 1e-9

    if up_bigger and u >= 4:
        msg = (f"Short gamma cuts BOTH ways — and your BIGGER tail is the upside: a {u_label} costs "
               f"~{u:.0f}% of capital, vs ~{d:.0f}% for a −20% month. A melt-up / short-squeeze, not a "
               f"crash, is the under-hedged side here.")
    elif u >= 8:
        msg = (f"Two-sided tail: a −20% month costs ~{d:.0f}% of capital and a {u_label} ~{u:.0f}% — "
               f"short gamma loses on a big move either way, so hedge both, not just the downside.")
    elif d_worst >= 40:
        msg = f"A −20% market month wipes ~{d:.0f}% of your capital (deepest downside ~{d_worst:.0f}%). Over-sized for its tail."
    elif d_worst >= 20:
        msg = f"A −20% market month costs ~{d:.0f}% of capital — heavy for a premium-selling book."
    elif worst >= 8:
        msg = f"A −20% month costs ~{d:.0f}% of capital (melt-up ~{u:.0f}%) — manageable but real."
    else:
        msg = f"A −20% month costs ~{d:.0f}% and a melt-up ~{u:.0f}% of capital — a well-sized tail on both sides."

    actions = []
    if up_bigger and u >= 8:
        actions.append(
            f"Your dominant tail is a MELT-UP ({u_label} → ~{u:.0f}% of capital): the short-call / "
            f"net-short-delta upside is under-protected. Cap it with call SPREADS (not naked calls), trim "
            f"the biggest short calls, or add a small long-call/upside hedge — a downside index put does NOT help this side.")
    laddered = [c for c in concentration if c.get("laddered") or c.get("flags")]
    if laddered:
        names = ", ".join(c["ticker"] for c in laddered[:3])
        actions.append(f"De-concentrate {names}: laddered short strikes on one name are ONE bet — a single gap hits them all.")
    if short_vol and worst >= 15:
        actions.append("Reduce size or convert the biggest naked shorts to defined-risk spreads to cap the tail (both sides).")
    rec = next((c for c in hedge_menu if c.get("recommended")), None)     # always an ACTIONABLE hedge (never the standby sleeve)
    if rec and rec.get("cost_effective"):
        actions.append(f"Add the recommended hedge ({_hedge_trade_str(rec)}) — it lifts compound growth (+{rec['cagr_lift_pct']}% CAGR) while capping the crash.")
    elif rec:
        actions.append(f"A tail hedge here is pure insurance ({_hedge_trade_str(rec)} lowers CAGR by {abs(rec['cagr_lift_pct'])}%/yr) — only add it if avoiding the drawdown matters more than yield.")
    standby = next((c for c in hedge_menu if c.get("standby")), None)
    if standby:
        actions.append(f"Standby, not a trade today: keep ~{_usd0(standby.get('sleeve_capital'))} in cash for the VIXY sleeve and buy VIXY ONLY when spot VIX closes above VIX3M "
                       f"(backwardation). Until that signal fires there is nothing to buy — protect the book with the actionable hedge and the Fixes above.")
    if abs(beta_delta_spy or 0) > 0:
        actions.append(f"Net directional exposure ≈ {beta_delta_spy:+.0f} SPY-share-equivalents — neutralize with the index if you want it market-flat.")
    if not actions:
        actions.append("Book is balanced — keep managing winners early (≈50%) and defending tested strikes.")
    return {"level": level, "summary": msg, "actions": actions}


def _vol_shock_for(mv: float) -> float:
    """Vol-point shock for a market move: vol SPIKES on the downside (skew), CRUSHES mildly
    on a melt-up. Consistent with the crash table's skew constant."""
    return -_SKEW_K * 100.0 * mv if mv < 0 else -0.30 * 100.0 * mv


def _covered_shares(p: dict) -> float:
    """Shares economically behind the position's SHORT CALLS (0 when none are covered).

    Real shares in the trade cover up to the calls' size (1 contract = 100 sh); the user's explicit
    'covered' mark (shares held ELSEWHERE) or a covered_call structure with no recorded shares covers
    every short call. Short stock is never cover."""
    short_call_sh = sum(lg["qty"] * _MULT for lg in p.get("legs", []) if lg["right"] == "C" and lg["sign"] < 0)
    if short_call_sh <= 0:
        return 0.0
    sh = float(p.get("shares") or 0)
    if sh < 0:
        return 0.0
    if p.get("covered"):
        return float(short_call_sh)
    if sh > 0:
        return float(min(sh, short_call_sh))
    return float(short_call_sh) if p.get("structure") == "covered_call" else 0.0


def _is_covered(p: dict) -> bool:
    """A short CALL is COVERED when the shares sit behind it — a covered_call structure, a real long-share
    leg (has_stock), or the user's explicit 'covered' mark (shares held ELSEWHERE, not in the income book).
    A covered call delivers held shares on assignment, so it carries no naked-assignment / buy-to-cover cost."""
    return bool(p.get("structure") == "covered_call" or p.get("has_stock") or p.get("covered"))


def _is_naked_short(p: dict, lg: dict) -> bool:
    """A short leg with nothing behind it. A short PUT is protected by a long put on the same position (a
    spread — its loss is capped at the width, the long leg can be exercised); a short CALL by covering shares
    or a long call (spread). Used by BOTH the all-naked-assigned total and the assignment ladder, so the two
    can never disagree about which legs count."""
    if lg["sign"] >= 0:
        return False
    legs = p.get("legs", [])
    if lg["right"] == "P":
        return not any(x["right"] == "P" and x["sign"] > 0 for x in legs)
    return (not _is_covered(p)) and not any(x["right"] == "C" and x["sign"] > 0 for x in legs)


def _assignment_ladder(positions: list[dict], r: float, moves=None) -> list[dict]:
    """What-if ASSIGNMENT / capital lab — 'what happens in various market scenarios'.

    For each market move, full-reprice the book AND tally the capital an assignment would
    demand at that spot:
      • a short PUT finishing ITM → cash-secured you must PRODUCE to take delivery
        (strike × 100 × qty — the 'forced to purchase' capital);
      • a short CALL finishing ITM → stock called away / bought-to-cover
        (intrinsic (spot − strike) × 100 × qty).
    Long legs never demand assignment capital. On a strangle only ONE wing is ITM at a given
    spot, so the binding side is picked automatically — no double count, matching "if it's a
    straddle take the highest capital required"."""
    moves = moves or [-0.35, -0.25, -0.20, -0.15, -0.10, -0.05,
                      0.05, 0.10, 0.15, 0.20, 0.25, 0.35]
    out = []
    for mv in moves:
        put_cap = call_cost = 0.0
        puts_itm = calls_itm = 0.0                             # CONTRACTS in the money (same unit as the capital)
        for p in positions:
            s = p["spot"] * (1 + mv * p.get("beta", 1.0))     # β-adjusted per name
            for lg in p["legs"]:
                if not _is_naked_short(p, lg):                 # only NAKED shorts demand assignment capital
                    continue
                K, qty = lg["strike"], lg["qty"]
                if lg["right"] == "P" and s < K:
                    put_cap += K * _MULT * qty                 # cash to buy the assigned shares
                    puts_itm += qty
                elif lg["right"] == "C" and s > K:
                    call_cost += (s - K) * _MULT * qty         # intrinsic to deliver / cover
                    calls_itm += qty
        pnl = reprice_scenario(positions, mv, _vol_shock_for(mv), r)
        out.append({
            "move_pct": round(mv * 100, 0),
            "pnl": round(pnl, 0),
            "put_assignment_capital": round(put_cap, 0),
            "call_cover_cost": round(call_cost, 0),
            "puts_itm": int(round(puts_itm)), "calls_itm": int(round(calls_itm)),
        })
    return out


def _naked_assignment(positions: list[dict]) -> dict:
    """Worst-case capital if EVERY NAKED short is assigned at once — the aggregate obligation
    a scenario-by-scenario ladder never sums (a put is assigned low, a call high). COVERED
    calls (stock behind them, or a covered_call structure) and spread-protected legs (an
    offsetting long option on the same side) are EXCLUDED — only the genuinely naked shorts.

      • naked short PUT  → cash to buy the shares put to you   = strike × 100 × qty
      • naked short CALL → notional you must deliver / source  = strike × 100 × qty
        (true buy-to-cover can exceed this if the stock has already run — upside is unbounded)."""
    put_cap = call_cap = 0.0
    n_puts = n_calls = 0
    for p in positions:
        for lg in p.get("legs", []):
            if not _is_naked_short(p, lg):
                continue
            notional = lg["strike"] * _MULT * lg["qty"]
            if lg["right"] == "P":                             # naked / cash-secured short put
                put_cap += notional
                n_puts += 1
            else:                                              # naked short call
                call_cap += notional
                n_calls += 1
    return {
        "put_capital": round(put_cap, 0), "call_capital": round(call_cap, 0),
        "total": round(put_cap + call_cap, 0),
        "n_naked_puts": n_puts, "n_naked_calls": n_calls,
    }


def _loss_by_name(positions: list[dict], r: float, move: float, vshock: float) -> list[dict]:
    """Per-underlying OPTION-OVERLAY P&L at one scenario — the 'who is actually hurting me'
    waterfall. Options only (the long shares behind covered names are a separate holding, not this
    desk's P&L). Sorted worst-first. Sums EXACTLY to the same-scenario stress tile (same engine)."""
    agg: dict[str, dict] = {}
    for p in positions:
        pnl = reprice_scenario([p], move, vshock, r)
        a = agg.setdefault(p["ticker"], {"ticker": p["ticker"], "pnl": 0.0, "beta": round(p.get("beta", 1.0), 2)})
        a["pnl"] += pnl
    rows = sorted(agg.values(), key=lambda x: x["pnl"])
    return [{**a, "pnl": round(a["pnl"], 0)} for a in rows]


# ── Concentration: where the RISK is vs where the MONEY is, and what a correlated group does ──────────
_GROUP_SHOCKS = (-0.30, -0.20, -0.10, 0.10, 0.20, 0.30)


def _concentration_view(positions: list[dict], r: float, book_capital: float, factor_exposure: Optional[dict]) -> dict:
    """Concentration that means something — every number is a full reprice or a plain share, nothing invented.

      names   — per underlying: its share of CAPITAL, of the tail LOSS on the book's worse side, and of the
                income (θ); plus the days of its own θ it takes to pay back that loss. A name whose tail share
                dwarfs its capital/income share is where the book is mis-paid for its risk.
      groups  — each measured-ρ cluster and sector: what the group does if IT gaps (every member moves X%
                itself, the rest of the book flat; full BS reprice + the usual vol shock). A correlated
                cluster is one bet, so this is the loss a single sector headline can cause.
      effective_bets — 1 / Σ share² of the tail with correlated clusters merged: how many INDEPENDENT bets
                the book really holds (a name count of 14 can be ~4)."""
    names: dict[str, dict] = {}
    for p in positions:
        n = names.setdefault(p["ticker"], {"ticker": p["ticker"], "trades": 0, "capital": 0.0, "theta": 0.0,
                                           "gamma": 0.0, "beta": p.get("beta", 1.0), "down": 0.0, "up": 0.0})
        n["trades"] += 1
        n["capital"] += p.get("capital") or 0.0
        n["theta"] += p.get("net_theta") or 0.0
        n["gamma"] += abs(p.get("net_gamma") or 0.0)
        n["down"] += _position_side_metrics(p, "down", r)["tail"]
        n["up"] += _position_side_metrics(p, "up", r)["tail"]
    if not names:
        return {"names": [], "groups": [], "side": None, "effective_bets": None, "effective_names": None,
                "n_names": 0, "n_groups": 0, "shock_moves": [int(m * 100) for m in _GROUP_SHOCKS]}

    loss = lambda x: max(0.0, -x)
    tot_down = sum(loss(n["down"]) for n in names.values())
    tot_up = sum(loss(n["up"]) for n in names.values())
    side = "up" if tot_up > tot_down else "down"            # the tail that actually dominates this book
    tot_loss = tot_up if side == "up" else tot_down
    tot_cap = sum(n["capital"] for n in names.values()) or 0.0
    tot_theta = sum(max(0.0, n["theta"]) for n in names.values())
    tot_gamma = sum(n["gamma"] for n in names.values()) or 0.0

    rows = []
    for n in names.values():
        lo = loss(n["up"] if side == "up" else n["down"])
        tail_pct = lo / tot_loss * 100 if tot_loss > 0 else 0.0
        cap_pct = n["capital"] / tot_cap * 100 if tot_cap > 0 else 0.0
        rows.append({
            "ticker": n["ticker"], "trades": n["trades"], "beta": round(n["beta"], 2),
            "capital": round(n["capital"], 0), "capital_pct": round(cap_pct, 1),
            "theta": round(n["theta"], 1), "theta_pct": round(max(0.0, n["theta"]) / tot_theta * 100, 1) if tot_theta > 0 else 0.0,
            "down_pnl": round(n["down"], 0), "up_pnl": round(n["up"], 0),
            "tail_loss": round(lo, 0), "tail_pct": round(tail_pct, 1),
            "gamma_pct": round(n["gamma"] / tot_gamma * 100, 1) if tot_gamma > 0 else 0.0,
            "risk_ratio": round(tail_pct / cap_pct, 2) if cap_pct > 0 else None,        # tail share ÷ capital share
            "payback_days": round(lo / n["theta"], 0) if (n["theta"] > 0 and lo > 0) else None,   # days of own θ to repay the loss
        })
    rows.sort(key=lambda x: (-x["tail_pct"], -x["capital"]))

    # measured-ρ clusters merged (union-find over overlapping ticker lists) → independent bets
    present = set(names)
    clusters = [[t for t in (c.get("tickers") or []) if t in present] for c in ((factor_exposure or {}).get("clusters") or [])]
    clusters = [c for c in clusters if len(c) >= 2]
    parent = {t: t for t in present}

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for c in clusters:
        for t in c[1:]:
            parent[_find(t)] = _find(c[0])
    share: dict[str, float] = {}
    for rw in rows:
        share[_find(rw["ticker"])] = share.get(_find(rw["ticker"]), 0.0) + rw["tail_pct"] / 100.0
    hhi_g = sum(v * v for v in share.values())
    hhi_n = sum((rw["tail_pct"] / 100.0) ** 2 for rw in rows)

    # what each correlated group / sector does if IT gaps
    def _group(kind: str, label: str, tickers: list[str]) -> Optional[dict]:
        mem = [{**p, "beta": 1.0} for p in positions if p["ticker"] in tickers]
        if not mem:
            return None
        shocks = {f"{round(mv * 100):+d}": round(reprice_scenario(mem, mv, _vol_shock_for(mv), r), 0) for mv in _GROUP_SHOCKS}
        cap = sum(p.get("capital") or 0.0 for p in mem)
        th = sum(p.get("net_theta") or 0.0 for p in mem)
        wk, wv = min(shocks.items(), key=lambda kv: kv[1])
        return {"kind": kind, "label": label, "tickers": sorted(set(tickers)), "capital": round(cap, 0),
                "capital_pct": round(cap / tot_cap * 100, 1) if tot_cap > 0 else 0.0,
                "theta": round(th, 1), "shocks": shocks, "worst_move": wk, "worst": wv,
                "worst_pct_capital": round(wv / cap * 100, 1) if cap > 0 else None,
                "carry_years": round(abs(wv) / (th * 365.0), 1) if (th > 0 and wv < 0) else None}

    groups, seen = [], set()
    book = _group("book", "Whole book moves together", sorted(present))
    for c in clusters:
        key = frozenset(c)
        if key not in seen:
            seen.add(key)
            g = _group("cluster", " · ".join(sorted(c)), sorted(c))
            if g:
                g["avg_rho"] = next((x.get("avg_rho") for x in ((factor_exposure or {}).get("clusters") or [])
                                     if set(x.get("tickers") or []) >= set(c)), None)
                groups.append(g)
    for sct in ((factor_exposure or {}).get("sectors") or []):
        tk = [t for t in (sct.get("tickers") or []) if t in present]
        key = frozenset(tk)
        if len(tk) >= 2 and key not in seen:
            seen.add(key)
            g = _group("sector", str(sct.get("sector")), tk)
            if g:
                groups.append(g)
    groups.sort(key=lambda g: g["worst"])
    if book:
        groups.insert(0, book)

    return {"side": side, "side_label": ("+30% squeeze × β" if side == "up" else "−20% crash × β"),
            "names": rows, "groups": groups,
            "effective_bets": round(1.0 / hhi_g, 1) if hhi_g > 0 else None,
            "effective_names": round(1.0 / hhi_n, 1) if hhi_n > 0 else None,
            "n_names": len(rows), "n_groups": len(share), "shock_moves": [round(m * 100) for m in _GROUP_SHOCKS]}


# Independent-axis risk array (SPAN/RiskMetrics style): spot shock × vol shock, full reprice.
_GRID_SPOT = [-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20]
_GRID_VOL = [-10.0, 0.0, 10.0, 20.0, 30.0]   # absolute vol-point shocks (independent of the move)


def _scenario_grid(positions: list[dict], r: float, book_capital: float) -> dict:
    """2-D stress matrix — book P&L for every (spot move × vol-point shock) combination, whole
    position repriced. This is the desk's risk array: it shows the short-gamma 'valley' (loss on a
    move EITHER way) and the short-vega gradient (loss as vol rises) that a single row of tiles hides."""
    rows = []
    for mv in _GRID_SPOT:
        cells = []
        for vp in _GRID_VOL:
            pnl = reprice_scenario(positions, mv, vp, r)
            cells.append({"pnl": round(pnl, 0),
                          "pct": round(pnl / book_capital * 100, 1) if book_capital else None})
        rows.append({"move_pct": round(mv * 100, 0), "cells": cells})
    return {"spot_moves": [round(m * 100, 0) for m in _GRID_SPOT],
            "vol_shocks": [round(v, 0) for v in _GRID_VOL], "rows": rows}


async def _book_factor_exposure(positions: list[dict], db) -> Optional[dict]:
    """DETERMINISTIC factor decomposition of the book (no LLM, no invented links) — the pillar the
    greeks miss: which SECTORS the book is really betting on, which names actually MOVE TOGETHER
    (measured ρ → hidden concentration a per-name view hides), and which MACRO factors (rates, oil,
    gold, USD, …) the whole book loads on. Reuses the shared correlated-assets registry: GICS
    sectors + 1y returns, Pearson ρ. Labels are only ever attached to a correlation that is measured.

    Returns {sectors, clusters, macro, drivers} in $ and ρ, or None if returns are unavailable.
    DB reads are SEQUENTIAL on the shared session (see get_profiles' note)."""
    from .correlated_assets_service import get_profiles, fetch_returns, pearson, _MACRO_PROXIES, _FACTOR_LABEL

    tickers = sorted({p["ticker"] for p in positions})
    if len(tickers) < 1:
        return None
    macro = [(m["factor"], m["label"], m["proxy"], float(m.get("sign", "1"))) for m in _MACRO_PROXIES if m["factor"] != "market"]
    try:
        profiles = await get_profiles(tickers, db)                          # sequential (shared session)
        returns = await fetch_returns(tickers + [p for _, _, p, _ in macro], db)
    except Exception as exc:  # noqa: BLE001
        logger.debug("factor exposure fetch failed: %s", exc)
        return None
    if not returns:
        return None

    # Per-ticker committed capital + beta-weighted directional $ (delta incl. shares).
    cap_by: dict[str, float] = {}
    dir_by: dict[str, float] = {}
    for p in positions:
        t = p["ticker"]
        cap_by[t] = cap_by.get(t, 0.0) + (p.get("capital") or 0.0)
        dir_shares = (p.get("net_delta") or 0.0) + (p.get("shares") or 0.0)
        dir_by[t] = dir_by.get(t, 0.0) + dir_shares * p.get("beta", 1.0) * p["spot"]

    # 1) SECTOR buckets — where the committed capital and net direction actually sit.
    sec_agg: dict[str, dict] = {}
    for t in tickers:
        sec = (profiles.get(t, {}).get("sector") or "Unclassified").strip() or "Unclassified"
        s = sec_agg.setdefault(sec, {"sector": sec, "tickers": [], "capital": 0.0, "net_directional": 0.0})
        s["tickers"].append(t)
        s["capital"] += cap_by.get(t, 0.0)
        s["net_directional"] += dir_by.get(t, 0.0)
    sectors = sorted(({**s, "capital": round(s["capital"], 0), "net_directional": round(s["net_directional"], 0),
                       "n": len(s["tickers"])} for s in sec_agg.values()),
                     key=lambda x: -x["capital"])

    # 2) CORRELATED CLUSTERS — names that measurably move together (ρ ≥ 0.6) = one bet.
    def _al(a, b):
        n = min(len(a), len(b))
        return (a[-n:], b[-n:]) if n >= 40 else (None, None)
    parent = list(range(len(tickers)))
    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    pair_rho: dict[tuple, float] = {}
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            a, b = _al(returns.get(tickers[i], []), returns.get(tickers[j], []))
            if a is None:
                continue
            rho = pearson(a, b)
            if rho is not None and rho >= 0.6:
                pair_rho[(tickers[i], tickers[j])] = round(rho, 2)
                ri, rj = _find(i), _find(j)
                if ri != rj:
                    parent[ri] = rj
    groups: dict[int, list[str]] = {}
    for k, t in enumerate(tickers):
        groups.setdefault(_find(k), []).append(t)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        rhos = [v for (a, b), v in pair_rho.items() if a in members and b in members]
        clusters.append({"tickers": sorted(members), "capital": round(sum(cap_by.get(t, 0.0) for t in members), 0),
                         "net_directional": round(sum(dir_by.get(t, 0.0) for t in members), 0),
                         "avg_rho": round(sum(rhos) / len(rhos), 2) if rhos else None})
    clusters.sort(key=lambda c: -c["capital"])

    # 3) MACRO loadings of the WHOLE book — capital-weighted book return vs each macro proxy.
    book_macro = []
    series = [(t, returns.get(t)) for t in tickers if returns.get(t)]
    n = min((len(s) for _, s in series), default=0)
    if n >= 40 and series:
        w = np.array([max(cap_by.get(t, 0.0), 0.0) for t, _ in series], dtype=float)
        if w.sum() > 0:
            w = w / w.sum()
            driver = np.average(np.vstack([np.array(s[-n:]) for _, s in series]), axis=0, weights=w)
            for factor, label, proxy, sign in macro:
                mp = returns.get(proxy)
                if not mp:
                    continue
                m = min(len(driver), len(mp))
                if m < 40:
                    continue
                rho = pearson(list(driver[-m:]), mp[-m:])
                if rho is not None and abs(rho) >= 0.4:
                    book_macro.append({"factor": factor, "label": _FACTOR_LABEL.get(factor, label),
                                       "rho": round(rho, 2)})
            book_macro.sort(key=lambda x: -abs(x["rho"]))

    return {"sectors": sectors, "clusters": clusters, "macro": book_macro,
            "by_name": [{"ticker": t, "sector": profiles.get(t, {}).get("sector") or None,
                         "capital": round(cap_by.get(t, 0.0), 0),
                         "net_directional": round(dir_by.get(t, 0.0), 0)} for t in tickers]}


# ── Per-position remediation optimizer (real legs, risk-vs-reward ranked) ─────
_REM_DOWN, _REM_UP = -0.20, 0.30   # representative down / squeeze moves for per-position tail sizing


def _pick_wing(strikes: list[float], ref: float, direction: int,
               target_frac: float = 0.10, min_gap_frac: float = 0.03) -> Optional[float]:
    """Nearest LISTED strike beyond `ref` (direction +1 higher / −1 lower) to ~target_frac OTM —
    the real protective wing to turn a naked short into a defined-risk spread."""
    if not strikes:
        return None
    tgt = ref * (1 + direction * target_frac)
    cands = [s for s in strikes if (s - ref) * direction >= ref * min_gap_frac]
    return min(cands, key=lambda s: abs(s - tgt)) if cands else None


def _short_extrinsic(spot: float, lg: dict, r: float) -> float:
    """$ of extrinsic (time value) still to collect on one short leg — the 'reward remaining'."""
    right = "call" if lg["right"] == "C" else "put"
    # the leg's live MID when the chain gave one (the market's own value), else the model price
    val = lg["mid"] if (lg.get("mid") or 0) > 0 else bs_price(spot, lg["strike"], lg["dte_years"], r, lg["iv"], right)
    intrin = max(0.0, spot - lg["strike"]) if lg["right"] == "C" else max(0.0, lg["strike"] - spot)
    return max(0.0, val - intrin) * _MULT * lg["qty"]


def _position_side_metrics(p: dict, side: str, r: float) -> dict:
    """This position's tail on `side` (whole position) + the premium still to collect on that side."""
    move = _REM_UP if side == "up" else _REM_DOWN
    right = "C" if side == "up" else "P"
    tail = reprice_scenario([p], move, _vol_shock_for(move), r)
    prem = sum(_short_extrinsic(p["spot"], lg, r) for lg in p["legs"] if lg["sign"] < 0 and lg["right"] == right)
    return {"tail": tail, "premium_left": round(prem, 0)}


def _remediate_position(p: dict, side: str, r: float) -> Optional[dict]:
    """Best CONCRETE fix for one position on `side` using REAL listed strikes: cap it with a spread
    (buy a real wing) or close it — whichever cuts more tail per dollar while preserving premium.
    Returns real leg strings, $ cost, premium kept, and tail before→after (all from live data)."""
    move = _REM_UP if side == "up" else _REM_DOWN
    vshock = _vol_shock_for(move)
    right = "C" if side == "up" else "P"
    ot = "call" if side == "up" else "put"
    shorts = [lg for lg in p["legs"] if lg["sign"] < 0 and lg["right"] == right]
    if not shorts:
        return None
    tgt = min(shorts, key=lambda lg: lg["strike"]) if side == "up" else max(shorts, key=lambda lg: lg["strike"])
    spot, K, qty, iv, dte = p["spot"], tgt["strike"], tgt["qty"], tgt["iv"], tgt["dte_years"]
    tail_before = reprice_scenario([p], move, vshock, r)
    prem_leg = _short_extrinsic(spot, tgt, r)
    quotes = (p.get("quotes_by_exp") or {}).get(tgt.get("exp"), {}).get(right, {})
    beta = p.get("beta", 1.0)
    stress = {"market_move_pct": round(move * 100, 1), "beta": round(beta, 2),
              "name_move_pct": round(move * beta * 100, 1), "vol_shock_pts": round(vshock, 1)}

    opts = []
    # CAP — buy a real wing beyond the short → defined-risk spread.
    wing = _pick_wing((p.get("strikes_by_exp") or {}).get(tgt.get("exp"), {}).get(right, []), K, +1 if side == "up" else -1)
    if wing:
        # Cost = what the wing REALLY costs: its live ask captured when the book was refreshed (mid if no
        # ask). Only if the chain had no quote for that strike do we fall back to a model price — and say so.
        wq = quotes.get(float(wing))
        px, how = _pay_price(wq)
        wing_iv = _sane_iv(wq, spot, float(wing), dte, r, right, iv)[0]   # the wing's own IV (skew), sanity-checked
        if px is None:
            px = bs_price(spot, wing, dte, r, wing_iv, ot)
        wing_cost = px * _MULT * qty
        p_cap = {**p, "legs": p["legs"] + [{"strike": wing, "right": right, "sign": 1, "qty": qty, "iv": wing_iv, "dte_years": dte}]}
        tail_after = reprice_scenario([p_cap], move, vshock, r)
        opts.append({"action": "cap", "cost": round(wing_cost, 0), "tail_after": round(tail_after, 0),
                     "premium_kept": round(prem_leg - wing_cost, 0),
                     "unit_price": round(px, 2), "pricing": how,
                     "legs": f"BUY {qty:g}× {tgt.get('exp','')} {wing:g}{right} (vs your short {K:g}{right})",
                     "detail": f"turns the naked {K:g}{right} into a {K:g}/{wing:g} spread",
                     # structured legs so the UI can hand the REPAIRED spread to Evaluate for exact pricing.
                     "prefill": {"ticker": p["ticker"], "legs": [
                         {"action": "SELL", "type": ot.upper(), "strike": K, "expiration": tgt.get("exp"), "qty": int(qty)},
                         {"action": "BUY", "type": ot.upper(), "strike": wing, "expiration": tgt.get("exp"), "qty": int(qty)},
                     ]}})
    # CLOSE — buy the short back.
    p_close = {**p, "legs": [lg for lg in p["legs"] if lg is not tgt]}
    cpx, chow = _pay_price({"ask": tgt.get("ask"), "mid": tgt.get("mid")})     # buy-to-close at the live ask
    if cpx is None:
        cpx = bs_price(spot, K, dte, r, iv, ot)
    opts.append({"action": "close", "cost": round(cpx * _MULT * qty, 0),
                 "tail_after": round(reprice_scenario([p_close], move, vshock, r), 0),
                 "premium_kept": 0.0, "unit_price": round(cpx, 2), "pricing": chow,
                 "legs": f"BUY-to-close {qty:g}× {tgt.get('exp','')} {K:g}{right}",
                 "detail": "exit the leg entirely"})
    # rank by LOSS REDUCED per $ spent (tail_after is less negative than tail_before → improvement
    # is tail_after − tail_before, a positive number); break ties by premium kept.
    def _eff(o):
        improvement = o["tail_after"] - tail_before
        return (improvement / max(o["cost"], 1.0), o["premium_kept"])
    opts.sort(key=_eff, reverse=True)
    best = opts[0]
    return {"tail_before": round(tail_before, 0), "stress": stress, "recommended": best,
            "alt": next((o for o in opts if o["action"] != best["action"]), None),
            # the targeted short leg — so the UI can ALWAYS render a "Close {K}{right}" action, even
            # when no cap wing is available (e.g. an already-defined-risk spread → close is the only fix).
            "short": {"strike": K, "right": right, "qty": int(qty)}}


def _offender_targets(positions: list[dict], side: str, r: float, *, tickers=None, top: int = 2) -> list[dict]:
    """Rank the positions creating the most `side` risk vs the premium they still pay, worst first,
    and attach each one's concrete remediation. Optionally restrict to `tickers` (a concentrated set)."""
    pool = [p for p in positions if (tickers is None or p["ticker"] in tickers)]
    scored = []
    for p in pool:
        m = _position_side_metrics(p, side, r)
        if m["tail"] >= -1:            # only positions that actually LOSE on this side
            continue
        scored.append((p, m))
    scored.sort(key=lambda pm: pm[1]["tail"])   # most negative first
    out = []
    for p, m in scored[:top]:
        rem = _remediate_position(p, side, r)
        if not rem:
            continue
        out.append({
            "ticker": p["ticker"], "name": p.get("name") or p["ticker"], "trade_id": p.get("trade_id"),
            "structure": p.get("structure"),
            "risk": round(m["tail"], 0), "premium_left": m["premium_left"],
            "why": f"{_usd0(m['tail'])} of the {'squeeze' if side == 'up' else '−20% crash'} loss"
                   + (f", only {_usd0(m['premium_left'])} premium left to earn" if m["premium_left"] < abs(m["tail"]) else ""),
            **rem,
        })
    return out


# ── Institutional risk scorecard ─────────────────────────────────────────────
# Standardized guardrails a professional vol desk runs the book against, each with the
# book's ACTUAL value vs the industry limit, a status, and — for anything off-limit — the
# CHEAPEST concrete trade that brings it back in line (with cost + premium-income impact).
# Thresholds are the mainstream institutional ranges (Basel/￼fund-mandate style):
_GUARDRAILS = {
    "extreme_tail":  {"warn": 0.07, "breach": 0.10, "label": "Extreme tail cap",       "unit": "cap"},   # worst 1-mo downside
    "cvar":          {"warn": 0.02, "breach": 0.03, "label": "CVaR 95% (1-mo)",         "unit": "cap"},
    "var":           {"warn": 0.015, "breach": 0.02, "label": "VaR 95% (1-mo)",         "unit": "cap"},
    "symmetry":      {"warn": 1.5,  "breach": 2.0,  "label": "Up/down tail symmetry",   "unit": "ratio"},
    "name_conc":     {"warn": 0.20, "breach": 0.25, "label": "Single-name concentration", "unit": "cap"},
    "cluster_conc":  {"warn": 0.30, "breach": 0.40, "label": "Correlated-cluster conc.", "unit": "cap"},
    "sector_conc":   {"warn": 0.35, "breach": 0.45, "label": "Sector concentration",    "unit": "cap"},
    "net_dir":       {"warn": 0.20, "breach": 0.30, "label": "Net directional (β-Δ)",   "unit": "cap"},
}


def _usd0(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{'−' if x < 0 else ''}${abs(x):,.0f}"


def _pick_hedge(menu: list[dict], need_cvar: float):
    """Cheapest hedge that cuts CVaR by ≥ need_cvar; else the biggest-reduction one (partial)."""
    covering = [c for c in menu if (c.get("cvar_reduction") or 0) >= need_cvar and (c.get("annual_bleed") or 0) >= 0]
    if covering:
        return min(covering, key=lambda c: c.get("annual_bleed") or 1e18), True
    if menu:
        return max(menu, key=lambda c: c.get("cvar_reduction") or 0), False
    return None, False


def _hedge_trade_str(c: dict) -> str:
    if not c:
        return "—"
    if c.get("instrument") == "VIXY":
        return f"VIXY dynamic sleeve ({_usd0(c.get('sleeve_capital'))} cash, standby until VIX backwardation)"
    lo = c.get("long_strike") if c.get("long_strike") is not None else c.get("long_put")
    sh = c.get("short_strike") if c.get("short_strike") is not None else c.get("short_put")
    strikes = f"{lo:g}/{sh:g}" if (lo is not None and sh is not None) else (f"{lo:g}" if lo is not None else "")
    return f"{c.get('instrument', 'SPX')} {strikes} {c.get('contracts', '')}×".strip()


def _risk_scorecard(*, positions, r, book_capital, annual_income, scenarios, cvar_95, var_95,
                    beta_delta_notional, beta_delta_spy, spy_price, concentration,
                    factor_exposure, naked_assignment, hedge_menu, hedge_plan=None) -> dict:
    cap = max(float(book_capital or 0), 1.0)
    carry = float(annual_income or 0)
    positions = positions or []
    checks: list[dict] = []

    def _worse_side(tks):
        d = sum(_position_side_metrics(p, "down", r)["tail"] for p in positions if p["ticker"] in tks)
        u = sum(_position_side_metrics(p, "up", r)["tail"] for p in positions if p["ticker"] in tks)
        return "down" if d <= u else "up"

    def _status(val, key, higher_bad=True):
        g = _GUARDRAILS[key]
        if higher_bad:
            return "breach" if val > g["breach"] else "warn" if val > g["warn"] else "pass"
        return "breach" if val < g["breach"] else "warn" if val < g["warn"] else "pass"

    def _carry_pct(cost):
        return f" ({cost / carry * 100:.0f}% of carry)" if carry > 0 and cost else ""

    downs = [s for s in scenarios if (s.get("move_pct") or 0) < 0 and s.get("pnl") is not None]
    ups = [s for s in scenarios if (s.get("move_pct") or 0) > 0 and s.get("pnl") is not None]
    worst_down = min((s["pnl"] for s in downs), default=0.0)
    worst_up_row = min(ups, key=lambda s: s["pnl"], default=None)
    worst_up = worst_up_row["pnl"] if worst_up_row else 0.0

    # 1) EXTREME TAIL CAP — worst 1-month downside ≤ 10% of capital.
    et = abs(worst_down) / cap
    st = _status(et, "extreme_tail")
    c = dict(key="extreme_tail", label=_GUARDRAILS["extreme_tail"]["label"], status=st,
             value_pct=round(et * 100, 1), value_str=f"{_usd0(worst_down)} · {et*100:.0f}%",
             limit_str="≤ 10% of capital",
             note="Deepest 1-month crash on the ladder — a single black swan must not threaten solvency.")
    if st != "pass" and hedge_menu:
        gap = abs(worst_down) - _GUARDRAILS["extreme_tail"]["breach"] * cap
        h, full = _pick_hedge(hedge_menu, gap)
        if h:
            after = abs(worst_down) - (h.get("crash_payoff_20") or h.get("cvar_reduction") or 0)
            c["fix"] = {"headline": f"Add {_hedge_trade_str(h)}",
                        "cost": f"{_usd0(h.get('annual_bleed'))}/yr" + _carry_pct(h.get("annual_bleed")),
                        "effect": f"worst crash {_usd0(worst_down)} → {_usd0(-max(after,0))} ({max(after,0)/cap*100:.0f}% of cap){'' if full else ' — partial'}",
                        "targets": _offender_targets(positions, "down", r, top=2)}
    checks.append(c)

    # 2) CVaR 95% ≤ 3% of capital.
    if cvar_95 is not None:
        cv = float(cvar_95) / cap
        st = _status(cv, "cvar")
        c = dict(key="cvar", label=_GUARDRAILS["cvar"]["label"], status=st,
                 value_pct=round(cv * 100, 1), value_str=f"{_usd0(cvar_95)} · {cv*100:.1f}%",
                 limit_str="≤ 2–3% of capital",
                 note="Average loss in the worst 5% of months — the fund-mandate tail measure.")
        if st != "pass" and hedge_plan:
            # The CVaR fix must act on the side the tail is actually on — an index put cannot cut a rally-driven
            # CVaR (it can even raise it) — and be priced against the income that pays for it.
            tail_side = (hedge_plan.get("tail") or {}).get("side") or "down"
            best = next((h for h in hedge_plan["candidates"] if h.get("recommended")), None)
            plan = None
            if best:
                plan = next((pl for pl in best["plans"] if pl["budget_pct"] == hedge_plan["default_budget"] and pl["units"] >= 1), None) \
                    or next((pl for pl in best["plans"] if pl["units"] >= 1), None)
            if best and plan:
                c["fix"] = {"headline": f"Add {plan['units']}× {best['label']}",
                            "cost": f"{_usd0(plan['annual_cost'])}/yr ({plan['cost_pct_income']:.0f}% of income)",
                            "effect": f"CVaR {_usd0(cvar_95)} → {_usd0(plan['cvar_after'])} ({plan['cvar_after']/cap*100:.1f}% of cap)",
                            "targets": _offender_targets(positions, tail_side, r, top=2)}
            else:
                c["fix"] = {"headline": "No index hedge cuts this CVaR — it is single-name risk; cap the offending shorts",
                            "effect": f"the worst months are {'rallies' if tail_side == 'up' else 'selloffs'} in a few names",
                            "targets": _offender_targets(positions, tail_side, r, top=2)}
        elif st != "pass" and hedge_menu:
            gap = float(cvar_95) - _GUARDRAILS["cvar"]["breach"] * cap
            h, full = _pick_hedge(hedge_menu, gap)
            if h:
                after = float(cvar_95) - (h.get("cvar_reduction") or 0)
                c["fix"] = {"headline": f"Add {_hedge_trade_str(h)}",
                            "cost": f"{_usd0(h.get('annual_bleed'))}/yr" + _carry_pct(h.get("annual_bleed")),
                            "effect": f"CVaR {_usd0(cvar_95)} → {_usd0(max(after,0))} ({max(after,0)/cap*100:.1f}% of cap){'' if full else ' — partial, stack a 2nd'}",
                            "targets": _offender_targets(positions, "down", r, top=2)}
        checks.append(c)

    # 3) VaR 95% ≤ 2% of capital.
    if var_95 is not None:
        vv = float(var_95) / cap
        checks.append(dict(key="var", label=_GUARDRAILS["var"]["label"], status=_status(vv, "var"),
                           value_pct=round(vv * 100, 1), value_str=f"{_usd0(var_95)} · {vv*100:.1f}%",
                           limit_str="≤ 1.5–2% of capital",
                           note="The 5%-worst-month loss threshold (CVaR’s companion)."))

    # 4) TAIL SYMMETRY — neither tail more than ~1.5× the other.
    a, b = abs(worst_down), abs(worst_up)
    ratio = (max(a, b) / max(min(a, b), 1.0))
    up_bigger = b > a
    st = _status(ratio, "symmetry")
    c = dict(key="symmetry", label=_GUARDRAILS["symmetry"]["label"], status=st,
             value_pct=round(ratio, 1), value_str=f"{ratio:.1f}× ({'up' if up_bigger else 'down'} heavier)",
             limit_str="≤ 1.5× either way",
             note="A short-vol book must not hide an asymmetric melt-up (or crash) tail.")
    if st != "pass":
        if up_bigger:
            tgts = _offender_targets(positions, "up", r, top=2)
            n = abs(round(beta_delta_spy or 0))
            c["fix"] = {"headline": f"Cap the short calls driving the melt-up" + (f" (or Buy ~{n} SPY to flatten β-Δ, {_usd0(n * (spy_price or 0))})" if n else ""),
                        "cost": "each target priced below",
                        "effect": f"shrinks the {worst_up_row['label'] if worst_up_row else 'squeeze'} tail toward the downside",
                        "targets": tgts}
        else:
            c["fix"] = {"headline": "Downside-heavy — lift the crash floor",
                        "cost": "each target priced below", "effect": "raises the worst-case toward the upside tail",
                        "targets": _offender_targets(positions, "down", r, top=2)}
    checks.append(c)

    # 5-7) CONCENTRATION — single name / correlated cluster / sector, as % of committed capital.
    fe = factor_exposure or {}
    by_name = fe.get("by_name") or []
    if by_name:
        top = max(by_name, key=lambda x: x.get("capital") or 0)
        share = (top.get("capital") or 0) / cap
        st = _status(share, "name_conc")
        c = dict(key="name_conc", label=_GUARDRAILS["name_conc"]["label"], status=st,
                 value_pct=round(share * 100, 0), value_str=f"{top['ticker']} {share*100:.0f}%",
                 limit_str="≤ 20–25% per name",
                 note="No single underlying should dominate — a gap in one name shouldn’t sink the book.")
        if st != "pass":
            trim = (share - _GUARDRAILS["name_conc"]["breach"]) * cap
            c["fix"] = {"headline": f"Reduce {top['ticker']} (biggest concentration)",
                        "cost": "each target priced below",
                        "effect": f"{top['ticker']} {share*100:.0f}% → {_GUARDRAILS['name_conc']['breach']*100:.0f}% of capital",
                        "targets": _offender_targets(positions, _worse_side({top['ticker']}), r, tickers={top['ticker']}, top=2)}
        checks.append(c)

    clusters = fe.get("clusters") or []
    if clusters:
        top = max(clusters, key=lambda x: x.get("capital") or 0)
        share = (top.get("capital") or 0) / cap
        st = _status(share, "cluster_conc")
        c = dict(key="cluster_conc", label=_GUARDRAILS["cluster_conc"]["label"], status=st,
                 value_pct=round(share * 100, 0),
                 value_str=f"{'·'.join(top['tickers'][:3])}{'…' if len(top['tickers'])>3 else ''} {share*100:.0f}% (ρ {top.get('avg_rho')})",
                 limit_str="≤ 30–40% per cluster",
                 note="Names that move together (measured ρ) are ONE bet — the hidden concentration.")
        if st != "pass":
            trim = (share - _GUARDRAILS["cluster_conc"]["breach"]) * cap
            c["fix"] = {"headline": f"Cut the {'·'.join(top['tickers'][:3])} cluster (one bet, ρ {top.get('avg_rho')})",
                        "cost": "each target priced below",
                        "effect": f"cluster {share*100:.0f}% → {_GUARDRAILS['cluster_conc']['breach']*100:.0f}% of capital",
                        "targets": _offender_targets(positions, _worse_side(set(top['tickers'])), r, tickers=set(top['tickers']), top=3)}
        checks.append(c)

    sectors = fe.get("sectors") or []
    if len([s for s in sectors if (s.get("capital") or 0) > 0]) > 1:
        top = max(sectors, key=lambda x: x.get("capital") or 0)
        share = (top.get("capital") or 0) / cap
        st = _status(share, "sector_conc")
        c = dict(key="sector_conc", label=_GUARDRAILS["sector_conc"]["label"], status=st,
                 value_pct=round(share * 100, 0), value_str=f"{top['sector']} {share*100:.0f}%",
                 limit_str="≤ 35–45% per sector",
                 note="Sector-wide shocks hit every name in it at once.")
        if st != "pass":
            tks = set(top.get("tickers") or [])
            c["fix"] = {"headline": f"Reduce {top['sector']} exposure (or hedge the sector ETF)",
                        "cost": "each target priced below", "effect": f"{top['sector']} {share*100:.0f}% → below limit",
                        "targets": _offender_targets(positions, _worse_side(tks), r, tickers=tks, top=3)}
        checks.append(c)

    # 8) NET DIRECTIONAL — a premium book should be ~market-neutral.
    nd = abs(beta_delta_notional or 0) / cap
    st = _status(nd, "net_dir")
    c = dict(key="net_dir", label=_GUARDRAILS["net_dir"]["label"], status=st,
             value_pct=round(nd * 100, 0),
             value_str=f"{_usd0(beta_delta_notional)} ({beta_delta_spy:+.0f} SPY-eq)" if beta_delta_spy is not None else _usd0(beta_delta_notional),
             limit_str="≤ 20–30% of capital",
             note="Big net delta = a hidden market bet on top of the premium book.")
    if st != "pass" and beta_delta_spy:
        n = abs(round(beta_delta_spy))
        side = "Sell" if beta_delta_spy > 0 else "Buy"
        cost = n * (spy_price or 0)
        c["fix"] = {"headline": f"{side} ~{n} SPY to flatten β-Δ",
                    "cost": f"{_usd0(cost)} capital · ~$0 premium hit",
                    "effect": f"β-Δ {beta_delta_spy:+.0f} → ~0 (market-neutral)"}
    checks.append(c)

    n_breach = sum(1 for c in checks if c["status"] == "breach")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    grade = "At risk" if n_breach else "Watch" if n_warn else "Sound"
    return {"grade": grade, "n_breach": n_breach, "n_warn": n_warn, "n_checks": len(checks),
            "checks": sorted(checks, key=lambda c: {"breach": 0, "warn": 1, "pass": 2}[c["status"]])}


# The result fields that depend on whether covered shares' downside losses are counted. The
# `with_stock_downside` variant carries ONLY these; the UI overlays them on the default result.
_DOWNSIDE_KEYS = ("crash_scenarios", "loss_by_name", "scenario_grid", "assignment_ladder",
                  "var_95", "cvar_95", "var_99", "cvar_99", "cvar_capital_pct",
                  "risk_scorecard", "hedge_menu", "hedge_note", "verdict", "concentration_view", "hedge_plan", "etf_hedges")


async def _fetch_etf_static(positions: list[dict], provider, today: date):
    """Network side of the ETF hedge view (name→ETF mapping + live ETF chains); a separate hook so tests never hit the net."""
    from .etf_hedge_service import fetch_etf_static
    return await fetch_etf_static(positions, provider, today)


def _skip_reason(strategy, spot_cache: dict) -> str:
    """Why a trade produced no position — so the UI can say what was left out instead of implying it was analysed."""
    try:
        legs = json.loads(getattr(strategy, "legs_data", None) or "[]")
    except (ValueError, TypeError):
        legs = []
    opt = [l for l in legs if "call" in str(l.get("type", "")).lower() or "put" in str(l.get("type", "")).lower()]
    if not opt:
        return "no open option legs (stock / futures only, or the options are closed)"
    if (spot_cache.get(getattr(strategy, "ticker", None)) or 0) <= 0:
        return "no underlying price from the quote provider"
    return "no usable option legs (missing expiration)"


async def compute_book_tail_risk(strategies: list, quote_source: str, user, db,
                                 *, hedge_target_pct: float = 0.6, crash_prob_annual: float = 0.05,
                                 hedge_dte_days: int = 90) -> dict:
    from .quote_providers import get_provider
    from .derivative_income_service import _get_sofr
    provider = get_provider(quote_source, user=user, db=db)
    try:
        r = (await _get_sofr())[0]
    except Exception:  # noqa: BLE001
        r = 0.045
    today = date.today()

    spot_cache, chain_cache, positions, skipped = {}, {}, [], []
    for s in strategies:
        reason = None
        try:
            p = await _position_greeks(s, provider, r, today, spot_cache, chain_cache)
        except Exception as exc:  # noqa: BLE001
            logger.debug("book greeks failed for %s: %s", getattr(s, "id", "?"), exc)
            p, reason = None, f"error: {str(exc)[:80]}"
        if p:
            positions.append(p)
        else:
            skipped.append({"trade_id": getattr(s, "id", None), "ticker": getattr(s, "ticker", None),
                            "reason": reason or _skip_reason(s, spot_cache)})
    if not positions:
        return {"positions": 0, "error": "No option positions with live quotes to aggregate.", "coverage": {"requested": len(strategies), "analysed": 0, "skipped": skipped}}
    coverage = {"requested": len(strategies), "analysed": len(positions), "skipped": skipped}
    quality = {"legs": sum(sum(p["iv_audit"].values()) for p in positions if p.get("iv_audit")),
               "chain": sum(p["iv_audit"]["chain"] for p in positions if p.get("iv_audit")),
               "solved": sum(p["iv_audit"]["solved"] for p in positions if p.get("iv_audit")),
               "fallback": sum(p["iv_audit"]["fallback"] for p in positions if p.get("iv_audit")),
               "indicative": any(p.get("indicative") for p in positions)}

    betas = await asyncio.to_thread(_compute_betas, [p["ticker"] for p in positions])
    for p in positions:
        p["beta"] = betas.get(p["ticker"], 1.0)
    beta_weighted = bool(betas)

    net_delta = sum(p["net_delta"] for p in positions)
    net_gamma = sum(p["net_gamma"] for p in positions)
    net_vega = sum(p["net_vega"] for p in positions)
    net_theta = sum(p["net_theta"] for p in positions)
    book_capital = sum(p["capital"] for p in positions)
    annual_income = net_theta * 365.0

    # index spot + market vol (VIX) for the MC + hedges.
    try:
        idx = float(getattr(await provider.get_underlying_price(_BENCH), "price", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        idx = 0.0
    try:
        vix = float(getattr(await provider.get_underlying_price("^VIX"), "price", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        vix = 0.0
    mkt_vol = (vix / 100.0) if 0.05 < vix / 100.0 < 2.0 else _MKT_VOL_FALLBACK

    # #1 — beta-weighted (SPY-equivalent) delta.
    bw_delta_notional = sum(p["net_delta"] * p["beta"] * p["spot"] for p in positions)
    spy_price = idx / _SPY_DIV if idx > 0 else None
    beta_delta_spy = round(bw_delta_notional / spy_price, 0) if spy_price else None   # SPY-share equivalents

    # The scenario / CVaR / hedge / scorecard block depends on ONE policy: whether the SHARES behind covered
    # calls lose money on a down move (`down_stock`). Gains always offset the short call. We run the block
    # for the default (overlay-only downside) and, when any covered shares exist, a second time with the
    # shares' losses included — so the UI toggle is instant and needs no live-quote refresh. The market /
    # index-chain / factor lookups are made once and shared.
    io: dict = {}

    async def _once(key, make):
        if key not in io:
            io[key] = await make()
        return io[key]

    async def _run(positions: list[dict]) -> dict:
        # #2 — FULL-REPRICE crash scenarios. OPTION-OVERLAY ONLY (long shares behind covered calls /
        # collars are a separate core holding, not this income desk's P&L — see reprice_scenario).
        scenarios = []
        for label, move, vshock in _CRASH:
            pnl = reprice_scenario(positions, move, vshock, r)
            scenarios.append({"label": label, "move_pct": move, "pnl": round(pnl, 0),
                              "pct_of_capital": round(pnl / book_capital * 100, 1) if book_capital else None})
        book_stock_notional = sum(abs(p.get("shares") or 0) * p["spot"] for p in positions)

        # #2 — full-reval MC VaR/CVaR at a 1-month horizon (fat tails + skew).
        mc = book_mc(positions, r, mkt_vol, idx or 1.0) if idx > 0 else {"pnl": None, "var_95": None, "cvar_95": None, "var_99": None, "cvar_99": None}
        book_loss20 = -reprice_scenario(positions, _HEDGE_SCEN[0], _HEDGE_SCEN[1], r)

        # Concentration (β-aware).
        by_u: dict[str, dict] = {}
        for p in positions:
            b = by_u.setdefault(p["ticker"], {"ticker": p["ticker"], "trades": 0, "short_legs": 0,
                                              "net_gamma": 0.0, "net_vega": 0.0, "net_delta": 0.0,
                                              "spot": p["spot"], "beta": p["beta"]})
            b["trades"] += 1
            b["short_legs"] += p["n_short"]
            for k in ("net_gamma", "net_vega", "net_delta"):
                b[k] += p[k]
        tot_g = sum(abs(b["net_gamma"]) for b in by_u.values()) or 1.0
        concentration = []
        for b in sorted(by_u.values(), key=lambda x: abs(x["net_gamma"]), reverse=True):
            share = abs(b["net_gamma"]) / tot_g
            flags = []
            if b["short_legs"] > 1:
                flags.append(f"{b['short_legs']} short legs on {b['ticker']} — one concentrated bet, not diversification")
            if share >= 0.40 and len(by_u) > 1:
                flags.append(f"{share*100:.0f}% of the book's gamma is in {b['ticker']}")
            if b["beta"] >= 1.3:
                flags.append(f"β {b['beta']} — moves ~{b['beta']}× the market, so it leads the book down in a crash")
            concentration.append({**{k: round(v, 4) if isinstance(v, float) else v for k, v in b.items()},
                                  "gamma_share_pct": round(share * 100, 1), "laddered": b["short_legs"] > 1, "flags": flags})

        # #4 — hedge MENU (ranked), repriced on the MC scenarios for true CVaR reduction. Two
        # families ranked TOGETHER: SPX put spreads (linear crash protection) and VIX call
        # spreads (convex black-swan protection — VIX explodes in a vol spike, cheap, pays
        # nothing in an ordinary drawdown that diversification already handles).
        hedge_menu, hedge_note, hedge_plan = [], None, None
        tail_attr = _tail_attribution(mc) if mc.get("pnl") is not None else None
        if idx > 0 and mc.get("pnl") is not None:
            # Candidates are built whenever there is a tail to hedge (the downside menu is only SHOWN when the
            # book is net short the −20% tail; the budget plan below also covers an UPSIDE-driven tail).
            target = hedge_target_pct * max(book_loss20, 0.0)
            puts, meta = await _once('puts', lambda: _index_puts(provider, hedge_dte_days, today))
            hdte = meta["dte"] if meta else hedge_dte_days
            spx = _hedge_candidates(idx, mkt_vol, hdte / 365.0, r, puts, mc["pnl"], mc["idx_sim"],
                                    mc["cvar_95"] or 0.0, book_loss20, target,
                                    crash_prob_annual, book_capital, annual_income, 365.0 / max(hdte, 1),
                                    horizon_years=mc.get("horizon_years", 0.0))
            for c in spx:
                c["index"], c["pricing"], c["dte_days"], c["expiry"] = \
                    "SPX (European)", ("live SPX chain" if puts else "model (BS + VIX)"), hdte, (meta or {}).get("exp")

            # VIX call-spread overlay — real ^VIX chain + a forward-vol base.
            vcalls, vmeta = await _once('vcalls', lambda: _vix_calls(provider, hedge_dte_days, today))
            spot_vix, fwd_vix = await _once('vix_levels', lambda: asyncio.to_thread(_vix_levels))
            spot_vix = spot_vix or (vix if vix > 5 else None)
            fwd_vix = fwd_vix or spot_vix
            vix_cands = []
            if vcalls and fwd_vix:
                vdte = vmeta["dte"] if vmeta else hedge_dte_days
                vix_cands = _vix_hedge_candidates(vcalls, fwd_vix, idx, mc["idx_sim"], mc["pnl"],
                                                  mc["cvar_95"] or 0.0, book_loss20, target,
                                                  crash_prob_annual, book_capital, annual_income,
                                                  365.0 / max(vdte, 1))
                for c in vix_cands:
                    c["index"], c["pricing"], c["dte_days"], c["expiry"] = \
                        "VIX (European)", "live ^VIX chain", vdte, (vmeta or {}).get("exp")

            # Dynamic VIXY sleeve — the near-zero-COST, signal-based hedge (only deploys on
            # term-structure inversion, so no permanent roll decay).
            vixy_cands = _vixy_dynamic_candidate(spot_vix, fwd_vix, idx, mc["idx_sim"], mc["pnl"],
                                                 mc["cvar_95"] or 0.0, book_loss20, target,
                                                 crash_prob_annual, book_capital, annual_income)
            for c in vixy_cands:
                c["index"], c["pricing"], c["dte_days"], c["expiry"] = \
                    "VIXY (ETF)", "signal-based · VIX term structure", None, None

            # SPX call spreads (model-priced) — the only index hedge for an upside-driven tail.
            call_cands = _hedge_call_candidates(idx, mkt_vol, hdte / 365.0, r, mc["pnl"], mc["idx_sim"],
                                                365.0 / max(hdte, 1), horizon_years=mc.get("horizon_years", 0.0))
            for c in call_cands:
                c["index"], c["dte_days"], c["expiry"] = "SPX (European)", hdte, (meta or {}).get("exp")

            if (mc.get("cvar_95") or 0) > 0:
                hedge_plan = _hedge_plan(
                    [dict(c) for c in spx + vix_cands + vixy_cands + call_cands],
                    mc=mc, cvar0=float(mc["cvar_95"]), annual_income=annual_income, book_capital=book_capital, tail=tail_attr)
                hedge_plan["exposures"] = _exposure_map(
                    positions, r, bw_delta_notional=bw_delta_notional, beta_delta_spy=beta_delta_spy, spy_price=spy_price,
                    net_vega=net_vega, book_capital=book_capital, mkt_vol=mkt_vol, hedge_dte_days=hdte)
            _strip_private(spx + vix_cands + vixy_cands + call_cands)

            if book_loss20 > 0:
                hedge_menu = _rank_hedges(spx + vix_cands + vixy_cands)
                if not vix_cands:
                    hedge_note = "VIX black-swan overlay unavailable (no live ^VIX chain) — showing SPX put hedges only."
        if book_loss20 <= 0:
            hedge_note = "The book isn't net short the tail at −20% — no downside index hedge needed (see the budget plan for the upside)."

        # ETF hedges around the book's own themes (nearest ETF by measured ρ; live ETF chains fetched once, shared by both passes).
        etf_hedges = None
        try:
            from .etf_hedge_service import build_etf_hedges
            etf_static = await _once('etf', lambda: _fetch_etf_static(positions, provider, today))
            etf_hedges = build_etf_hedges(positions, etf_static, r, annual_income, (tail_attr or {}).get("side"))
        except Exception as exc:  # noqa: BLE001 — the ETF view must never fail the desk
            logger.debug("etf hedges failed: %s", exc)

        # DETERMINISTIC factor / correlation / macro decomposition (sequential DB — shared session).
        try:
            factor_exposure = await _once('factor', lambda: _book_factor_exposure(positions, db))
        except Exception as exc:  # noqa: BLE001 — a factor-data miss must never fail the desk
            logger.debug("factor exposure failed: %s", exc)
            factor_exposure = None

        verdict = _verdict_and_actions(scenarios=scenarios, short_vol=net_gamma < 0,
                                       concentration=concentration, hedge_menu=hedge_menu,
                                       cvar_pct=(mc.get("cvar_95") or 0) / book_capital * 100 if book_capital else None,
                                       beta_delta_spy=beta_delta_spy)

        # Institutional risk SCORECARD — standardized guardrails + the cheapest fix for each breach.
        scorecard = _risk_scorecard(
            positions=positions, r=r,
            book_capital=book_capital, annual_income=annual_income, scenarios=scenarios,
            cvar_95=mc.get("cvar_95"), var_95=mc.get("var_95"),
            beta_delta_notional=bw_delta_notional, beta_delta_spy=beta_delta_spy, spy_price=spy_price,
            concentration=concentration, factor_exposure=factor_exposure,
            naked_assignment=_naked_assignment(positions), hedge_menu=hedge_menu, hedge_plan=hedge_plan)

        return _native({
            "positions": len(positions),
            "net_delta": round(net_delta, 1), "net_gamma": round(net_gamma, 4),
            "net_vega": round(net_vega, 0), "net_theta": round(net_theta, 0),
            "net_delta_notional": round(sum(p["net_delta"] * p["spot"] for p in positions), 0),
            "beta_delta_notional": round(bw_delta_notional, 0), "beta_delta_spy": beta_delta_spy,
            "book_capital": round(book_capital, 0), "annual_income": round(annual_income, 0),
            # What the "% of capital" denominator IS — surfaced so the user can see it, not guess.
            "capital_basis": ("Σ committed capital across positions = short-put strikes × 100 (the cash "
                              "each CSP secures) + short-call/other strike notional. It is the capital PUT TO "
                              "WORK, the % of cap denominator — NOT your whole account net-liq."),
            # θ/Net-Liq — daily decay income as a % of capital at work (how hard the book earns);
            # carry_yield = that annualized. tastytrade rule-of-thumb: ~0.1%/day (~25%/yr) is healthy.
            "theta_net_liq_pct": round(net_theta / book_capital * 100, 3) if book_capital else None,
            "carry_yield_pct": round(annual_income / book_capital * 100, 1) if book_capital else None,
            "cvar_capital_pct": round((mc.get("cvar_95") or 0) / book_capital * 100, 1) if book_capital else None,
            "short_vol": bool(net_gamma < 0),
            "stock_notional": round(book_stock_notional, 0),   # $ of shares behind the overlay (covered/collar/holding)
            # Who is actually hurting me — per-name P&L at a −20% month (whole position, shares split out).
            "loss_by_name": _loss_by_name(positions, r, _HEDGE_SCEN[0], _HEDGE_SCEN[1]),
            # DETERMINISTIC factor read: sector buckets, correlated clusters (measured ρ), macro loadings.
            "factor_exposure": factor_exposure,
            # Concentration that means something: tail share vs capital share vs income, per-group shocks.
            "concentration_view": _concentration_view(positions, r, book_capital, factor_exposure),
            # Institutional risk scorecard: standardized guardrails, book value vs limit, cheapest fix per breach.
            "risk_scorecard": scorecard,
            # 2-D risk array: book P&L for spot × vol shocks (the short-gamma valley + short-vega gradient).
            "scenario_grid": _scenario_grid(positions, r, book_capital),
            "avg_beta": round(sum(abs(p["net_gamma"]) * p["beta"] for p in positions) / (sum(abs(p["net_gamma"]) for p in positions) or 1), 2),
            "var_95": mc.get("var_95"), "cvar_95": mc.get("cvar_95"),
            "var_99": mc.get("var_99"), "cvar_99": mc.get("cvar_99"),
            "horizon": "1 month (21 trading days)",
            "concentration": concentration,
            "crash_scenarios": scenarios,
            # Assignment / scenario lab — capital demanded and P&L across a two-sided move ladder,
            # plus the worst-case if EVERY naked short is assigned (covered calls excluded).
            "assignment_ladder": _assignment_ladder(positions, r),
            "naked_assignment": _naked_assignment(positions),
            "hedge_menu": hedge_menu, "hedge_note": hedge_note, "hedge_plan": hedge_plan, "etf_hedges": etf_hedges,
            "verdict": verdict,
            "assumptions": {"beta": "per-name β vs S&P 500" if beta_weighted else "β≈1 (beta data unavailable)",
                            "mkt_vol_pct": round(mkt_vol * 100, 1), "tail": f"Student-t (df={_TDF}) market factor + skew",
                            "crash_prob_annual_pct": round(crash_prob_annual * 100, 1),
                            "hedge_rolls_per_year": round(365 / hedge_dte_days, 1),
                            "vix": ("VIX call spreads priced off the live ^VIX chain; payoff valued at intrinsic on a "
                                    "VIX FUTURE (not spot VIX) mapped from each MC path's SPX move, dampened for mean "
                                    "reversion — conservative, so the black-swan hedge is never overstated."),
                            "vixy": ("Dynamic VIXY sleeve is near-zero COST (cash until the VIX term structure inverts) "
                                     "but its payoff is haircut ~35% for signal/gap lag and it ties up the sleeve in cash — "
                                     "shown as 'capital', not premium.")},
        })

    result = await _run(positions)
    result["coverage"], result["data_quality"] = coverage, quality
    has_cov = any(p.get("cov_shares") for p in positions)
    result["has_covered_stock"] = has_cov
    if has_cov:
        alt = await _run([{**p, "down_stock": True} for p in positions])
        result["with_stock_downside"] = {k: alt[k] for k in _DOWNSIDE_KEYS if k in alt}
    return result
