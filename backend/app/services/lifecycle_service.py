"""Continuous Lifecycle Management — post-trade quant metrics, organized by the
three desk roles that manage a live derivatives book.

    Risk Desk  → VaR, CVaR, deterministic stress tests   (portfolio of all
                 derivative trades, not the whole account)
    PM         → Omega, Sortino, Calmar + P&L            (per trade; is the
                 thesis still paying for its risk?)
    Trader     → Delta, Vanna, Charm, Volga              (per trade + book;
                 the dynamic Greeks a delta-hedger watches intraday)

Design notes
------------
- Greeks are computed by **finite difference on the Black-Scholes engine**
  (`stock_service.bs_*`), so second/third-order sensitivities stay consistent
  with the first-order Greeks the rest of the app already shows — no separate
  closed-form that could drift.
- The PM ratios are **forward-looking**: they come from the option-implied
  terminal payoff distribution (lognormal weights over the payoff scan), which
  is far more useful for a days-old options position than a backward NAV series.
- Portfolio VaR/CVaR use a **delta-gamma-vega Monte Carlo**: aggregate Greeks
  per underlying, shock each underlying by its own implied 1-day vol, add a
  skew-linked vol move, revalue via the Greek expansion, and read percentiles.
  Stress tests apply a *common* market shock across every underlying.
- Everything here is pure/numpy — no DB, no network — so it is unit-testable
  and cheap to call on every refresh.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Optional

import numpy as np

from .stock_service import bs_delta, bs_gamma, bs_vega, bs_theta, bs_price


# ── Trader desk — dynamic Greeks (Delta, Vanna, Charm, Volga) ───────────

_VOL_BUMP = 0.01          # 1 vol-point, for vanna/volga finite differences
_DAY = 1.0 / 365.0        # one calendar day, for charm


def _leg_higher_greeks(S: float, K: float, T: float, r: float, sigma: float, right: str) -> dict:
    """Per-share first/second/third-order Greeks for one option by finite
    difference. `right` is 'C'/'P'. Returns per-share values; the caller scales
    by sign × qty × 100."""
    otype = "call" if str(right).upper().startswith("C") else "put"
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
                "vanna": 0.0, "charm": 0.0, "volga": 0.0}

    delta = bs_delta(S, K, T, r, sigma, otype)
    gamma = bs_gamma(S, K, T, r, sigma)
    vega = bs_vega(S, K, T, r, sigma)          # per 1 vol-point already
    theta = bs_theta(S, K, T, r, sigma, otype)  # per day

    # Vanna = ∂Δ/∂σ, reported per 1 vol-point.
    vanna = (bs_delta(S, K, T, r, sigma + _VOL_BUMP, otype)
             - bs_delta(S, K, T, r, sigma - _VOL_BUMP, otype)) / 2.0
    # Charm = ∂Δ/∂t, reported as the change in Δ over the next calendar day.
    charm = bs_delta(S, K, max(T - _DAY, 1e-6), r, sigma, otype) - delta
    # Volga = ∂Vega/∂σ, reported per 1 vol-point (vega is already per vol-point).
    volga = (bs_vega(S, K, T, r, sigma + _VOL_BUMP)
             - bs_vega(S, K, T, r, sigma - _VOL_BUMP)) / 2.0

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta,
            "vanna": vanna, "charm": charm, "volga": volga}


def higher_order_greeks(legs: list[dict], spot: float, r: float = 0.05,
                        stock_shares: float = 0.0) -> dict:
    """Position-level Greeks for a trade, summed across its option legs (+ any
    stock leg for delta). Each leg dict: strike, right('C'/'P'), sign(+1/−1),
    qty, iv (decimal), dte_years.

    All values are in position terms (× contract multiplier) so books add up:
      net_delta  — share-equivalents ($ P&L per +$1 in the underlying)
      net_gamma  — Δ change per +$1
      net_vega   — $ per +1 vol-point
      net_theta  — $ per calendar day (decay)
      net_vanna  — Δ (share-equiv) change per +1 vol-point
      net_charm  — Δ (share-equiv) change per +1 calendar day
      net_volga  — vega ($) change per +1 vol-point
    """
    agg = {"delta": stock_shares, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
           "vanna": 0.0, "charm": 0.0, "volga": 0.0}
    for lg in legs:
        iv = lg.get("iv")
        T = lg.get("dte_years")
        K = lg.get("strike")
        if not iv or iv <= 0 or not T or T <= 0 or not K:
            continue
        sigma = iv / 100.0 if iv > 3 else float(iv)   # accept % or decimal
        mult = lg["sign"] * float(lg.get("qty") or 1) * 100.0
        g = _leg_higher_greeks(spot, float(K), float(T), r, sigma, lg["right"])
        for k in ("delta", "gamma", "vega", "theta", "vanna", "charm", "volga"):
            agg[k] += mult * g[k]
    return {
        "net_delta": round(agg["delta"], 2),
        "net_gamma": round(agg["gamma"], 4),
        "net_vega": round(agg["vega"], 2),
        "net_theta": round(agg["theta"], 2),
        "net_vanna": round(agg["vanna"], 2),
        "net_charm": round(agg["charm"], 3),
        "net_volga": round(agg["volga"], 2),
    }


# ── PM desk — forward-looking performance ratios ────────────────────────

def _lognormal_weights(prices: np.ndarray, spot: float, iv: float, dte_days: int, r: float):
    """Risk-neutral lognormal probability mass for each price bucket midpoint."""
    T = dte_days / 365.0
    ssig = iv * math.sqrt(T)
    mids = (prices[:-1] + prices[1:]) / 2.0
    dp = np.diff(prices)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (np.log(mids / spot) - (r - 0.5 * iv ** 2) * T) / ssig
        dens = np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi) / (mids * ssig)
    w = np.where(mids > 0, dens * dp, 0.0)
    s = w.sum()
    return (w / s) if s > 0 else w


def payoff_distribution_metrics(prices, pnls, weights, capital: float,
                                max_loss: Optional[float], dte_days: int,
                                mar: float = 0.0) -> dict:
    """PoP, expected value AND the PM ratios from ONE probability-weighted payoff.

    Deriving them all from a single `weights` array is what guarantees they can
    never contradict (e.g. Omega < 1 while EV > 0). The caller builds `weights`
    from whatever distribution it trusts — the market-implied RND when available,
    else a lognormal — so the PM desk sees the same law as the Quant Advisor.

    weights: probability mass per price bucket (len = len(prices) − 1).
    """
    prices = np.asarray(prices, dtype=float)
    pnls = np.asarray(pnls, dtype=float)
    if len(prices) < 2:
        return _metrics_from_pnl(np.zeros(0), np.zeros(0), capital, max_loss, dte_days, mar)
    pnl_mid = (pnls[:-1] + pnls[1:]) / 2.0
    return _metrics_from_pnl(pnl_mid, np.asarray(weights, dtype=float), capital, max_loss, dte_days, mar)


def _metrics_from_pnl(pnl_mid: np.ndarray, weights: np.ndarray, capital: float,
                      max_loss: Optional[float], dte_days: int, mar: float = 0.0) -> dict:
    """PoP / EV / Omega / Sortino / Calmar from per-bucket P&L and probability mass — the ONE formula
    behind both the scenario-grid path (`payoff_distribution_metrics`) and the exact bucket path
    (`_payoff_distribution`), so the two can never define a ratio differently."""
    out = {"pop": None, "expected_value": None, "omega": None, "sortino": None,
           "calmar": None, "expected_return_pct": None, "downside_dev_pct": None}
    pnl_mid = np.asarray(pnl_mid, dtype=float)
    w = np.asarray(weights, dtype=float)
    s = w.sum()
    if s <= 0 or len(pnl_mid) < 1 or len(w) != len(pnl_mid) or dte_days <= 0:
        return out
    w = w / s

    out["expected_value"] = round(float(np.sum(w * pnl_mid)), 2)
    out["pop"] = round(float(np.sum(w[pnl_mid > 0]) * 100), 1)

    if not capital or capital <= 0:
        return out
    rets = pnl_mid / capital
    exp_ret = float(np.sum(w * rets))
    gains = float(np.sum(w * np.maximum(rets - mar, 0.0)))
    losses = float(np.sum(w * np.maximum(mar - rets, 0.0)))
    omega = (gains / losses) if losses > 1e-12 else (99.99 if gains > 1e-12 else None)
    downside = float(np.sqrt(np.sum(w * np.minimum(rets - mar, 0.0) ** 2)))
    sortino = ((exp_ret - mar) / downside) if downside > 1e-9 else (99.99 if exp_ret > mar else None)
    calmar = None
    if max_loss is not None and max_loss < 0:
        dd = abs(max_loss) / capital
        if dd > 1e-9:
            ann = (1 + exp_ret) ** (365.0 / dte_days) - 1 if exp_ret > -1 else -1.0
            calmar = ann / dd
    out.update({
        "omega": round(omega, 2) if omega is not None else None,
        "sortino": round(sortino, 2) if sortino is not None else None,
        "calmar": round(calmar, 2) if calmar is not None else None,
        "expected_return_pct": round(exp_ret * 100, 2),
        "downside_dev_pct": round(downside * 100, 2),
    })
    return out


def pm_ratios(scenarios: list[dict], spot: float, avg_iv: float, dte_days: int,
              capital: float, max_loss: Optional[float], r: float = 0.05,
              mar: float = 0.0) -> dict:
    """Omega, Sortino and Calmar from the option-implied terminal payoff.

    scenarios: [{price, pnl_at_expiry}]; capital: deployed $ (denominator for
    returns); max_loss: worst-case $ (Calmar drawdown proxy); mar: minimum
    acceptable *return* (as a fraction, default 0). Returns None-filled dict on
    degenerate inputs.
    """
    out = {"omega": None, "sortino": None, "calmar": None,
           "expected_return_pct": None, "downside_dev_pct": None}
    if not scenarios or len(scenarios) < 3 or not avg_iv or avg_iv <= 0 \
            or not dte_days or dte_days <= 0 or not capital or capital <= 0:
        return out

    prices = np.array([s["price"] for s in scenarios], dtype=float)
    pnls = np.array([s["pnl_at_expiry"] for s in scenarios], dtype=float)
    w = _lognormal_weights(prices, spot, avg_iv, dte_days, r)
    m = payoff_distribution_metrics(prices, pnls, w, capital, max_loss, dte_days, mar)
    return {k: m[k] for k in out}


# ── Risk desk — portfolio VaR / CVaR / stress tests ─────────────────────

# Default stress book: a common shock applied to every underlying at once.
DEFAULT_STRESS = [
    {"name": "Melt-up +10%", "dS_pct": 0.10, "dVol_pts": -3.0},
    {"name": "Rally +5%", "dS_pct": 0.05, "dVol_pts": -1.5},
    {"name": "Drift −5% + vol", "dS_pct": -0.05, "dVol_pts": 3.0},
    {"name": "Selloff −10% + vol", "dS_pct": -0.10, "dVol_pts": 6.0},
    {"name": "Crash −20% + vol spike", "dS_pct": -0.20, "dVol_pts": 15.0},
    {"name": "Vol crush (flat)", "dS_pct": 0.0, "dVol_pts": -8.0},
]


def _position_pnl(pos: dict, dS: float, dvol_pts: float) -> float:
    """Delta-gamma-vega P&L of one aggregated underlying position under a
    ($ move, vol-point move) shock."""
    return (pos["net_delta"] * dS
            + 0.5 * pos["net_gamma"] * dS * dS
            + pos["net_vega"] * dvol_pts)


def portfolio_risk(positions: list[dict], horizon_days: int = 1,
                   n_sims: int = 20000, vol_beta: float = 0.6,
                   seed: int = 7, stress: Optional[list[dict]] = None) -> dict:
    """Book-level VaR/CVaR + stress tests over the *derivative* portfolio.

    positions: one dict per trade with keys
        ticker, spot, net_delta, net_gamma, net_vega, iv (annualized decimal).
    Trades are aggregated by ticker (a book's real risk unit). VaR/CVaR come
    from a delta-gamma-vega Monte Carlo: each underlying is shocked by its own
    implied `horizon_days` vol, with a skew-linked vol move (vol up when spot
    down, scaled by `vol_beta`). Losses are reported as positive numbers.
    """
    # Aggregate greeks per ticker (keep the vol / spot of the largest-vega leg).
    books: dict[str, dict] = {}
    for p in positions:
        t = p.get("ticker") or "?"
        b = books.setdefault(t, {"ticker": t, "spot": p.get("spot", 0.0),
                                 "net_delta": 0.0, "net_gamma": 0.0,
                                 "net_vega": 0.0, "iv": p.get("iv") or 0.0})
        b["net_delta"] += p.get("net_delta", 0.0)
        b["net_gamma"] += p.get("net_gamma", 0.0)
        b["net_vega"] += p.get("net_vega", 0.0)
        if p.get("spot"):
            b["spot"] = p["spot"]
        if p.get("iv"):
            b["iv"] = max(b["iv"], p["iv"])
    book_list = [b for b in books.values() if b["spot"] > 0]

    result = {
        "horizon_days": horizon_days, "n_sims": n_sims,
        "n_underlyings": len(book_list), "n_positions": len(positions),
        "var_95": None, "var_99": None, "cvar_95": None, "cvar_99": None,
        "stress_tests": [], "by_underlying": [],
        "net_delta_notional": round(sum(b["net_delta"] * b["spot"] for b in book_list), 0),
        "net_vega": round(sum(b["net_vega"] for b in book_list), 0),
    }
    if not book_list:
        return result

    rng = np.random.default_rng(seed)
    h = math.sqrt(horizon_days / 365.0)
    pnl = np.zeros(n_sims)
    for b in book_list:
        iv = b["iv"] if b["iv"] and b["iv"] > 0 else 0.30
        z = rng.standard_normal(n_sims)
        dS = b["spot"] * iv * h * z                       # 1-horizon lognormal-ish move
        dvol_pts = -vol_beta * (dS / b["spot"]) * 100.0   # skew: vol up when spot down
        pnl += (b["net_delta"] * dS + 0.5 * b["net_gamma"] * dS * dS + b["net_vega"] * dvol_pts)

    losses = -pnl
    result["var_95"] = round(float(np.percentile(losses, 95)), 0)
    result["var_99"] = round(float(np.percentile(losses, 99)), 0)
    result["cvar_95"] = round(float(losses[losses >= np.percentile(losses, 95)].mean()), 0)
    result["cvar_99"] = round(float(losses[losses >= np.percentile(losses, 99)].mean()), 0)

    for s in (stress or DEFAULT_STRESS):
        total = 0.0
        for b in book_list:
            total += _position_pnl(b, b["spot"] * s["dS_pct"], s["dVol_pts"])
        result["stress_tests"].append({"name": s["name"], "pnl": round(total, 0),
                                       "dS_pct": s["dS_pct"], "dVol_pts": s["dVol_pts"]})

    result["by_underlying"] = [
        {"ticker": b["ticker"], "spot": round(b["spot"], 2),
         "net_delta": round(b["net_delta"], 1), "net_gamma": round(b["net_gamma"], 3),
         "net_vega": round(b["net_vega"], 1), "iv": round(b["iv"] * 100, 1)}
        for b in book_list
    ]
    return result


# ── Pre-trade desk read + algorithmic Quant recommender ─────────────────
# The same engine as the live desks, but run on a PROPOSED structure the user is
# still evaluating: Trader Greeks, PM ratios and a single-position VaR/CVaR, then
# an ALGORITHMIC (non-LLM) enter/consider/avoid recommendation derived from the
# WHOLE payoff distribution — not one ratio.

def terminal_payoff_curve(legs, stock_shares, spot, lo=-0.5, hi=0.5, step=0.025):
    """Synthesize the terminal (expiry) P&L curve from priced option legs plus any
    stock, so PM/Quant metrics can be computed for structures the frontend doesn't
    pre-scan (e.g. income opportunities).

    legs: [{strike, right('C'/'P'), sign(+1/−1), qty, price}] — `price` is the entry
    mid per share. P&L(P) = stock·(P−spot) + Σ sign·qty·100·(intrinsic(P) − price).
    """
    out = []
    n = int(round((hi - lo) / step))
    for i in range(n + 1):
        P = spot * (1 + lo + i * step)
        pnl = stock_shares * (P - spot)
        for lg in legs:
            K = float(lg.get("strike") or 0)
            intrinsic = max(0.0, P - K) if lg["right"] == "C" else max(0.0, K - P)
            pnl += lg["sign"] * float(lg.get("qty") or 1) * 100.0 * (intrinsic - float(lg.get("price") or 0.0))
        out.append({"price": round(P, 2), "pnl": round(pnl, 2)})
    return out


def horizon_payoff_curve(legs, stock_shares, spot, horizon_years, r=0.05,
                         iv_fallback=0.30, lo=-0.5, hi=0.5, step=0.025):
    """P&L curve at a HORIZON date (the nearest leg's expiry) for a trade whose legs
    expire at DIFFERENT times (calendars / diagonals). At the horizon a leg with
    ``dte_years <= horizon`` is expired → pays intrinsic; a still-alive leg is repriced
    with Black-Scholes on its remaining life (``T = dte_years − horizon``) at its own IV.
    Same ``[{price, pnl}]`` shape as ``terminal_payoff_curve`` so ``compute_pretrade_metrics``
    consumes it unchanged.

    legs: [{strike, right('C'/'P'), sign(+1/−1), qty, price, iv, dte_years}] — `price` is
    the entry mid per share; `iv` may be percent or decimal.
    """
    out = []
    n = int(round((hi - lo) / step))
    for i in range(n + 1):
        P = spot * (1 + lo + i * step)
        pnl = stock_shares * (P - spot)
        for lg in legs:
            K = float(lg.get("strike") or 0)
            otype = "call" if str(lg.get("right", "")).upper().startswith("C") else "put"
            t_rem = float(lg.get("dte_years") or 0.0) - horizon_years
            iv = lg.get("iv")
            sigma = (iv / 100.0 if iv > 3 else float(iv)) if iv else iv_fallback
            if not sigma or sigma <= 0:
                sigma = iv_fallback
            if t_rem <= 1e-6:                       # expired at the horizon → intrinsic only
                val = max(0.0, P - K) if otype == "call" else max(0.0, K - P)
            else:                                    # still alive → BS mark at the horizon
                val = bs_price(P, K, t_rem, r, sigma, otype)
            pnl += lg["sign"] * float(lg.get("qty") or 1) * 100.0 * (val - float(lg.get("price") or 0.0))
        out.append({"price": round(P, 2), "pnl": round(pnl, 2)})
    return out


def _weighted_var_cvar(pnl_mid: np.ndarray, w: np.ndarray, alpha: float = 0.05) -> tuple:
    """Weighted VaR/CVaR (positive-loss $) at the alpha tail of the terminal P&L.

    CVaR is the exact Expected Shortfall: the mass-weighted mean of the worst ``alpha`` of outcomes, with the
    bucket that straddles the alpha boundary counted FRACTIONALLY (not dropped) — so it does not jump as a
    bucket edge crosses alpha."""
    pnl_mid = np.asarray(pnl_mid, dtype=float)
    w = np.asarray(w, dtype=float)
    if len(pnl_mid) == 0 or w.sum() <= 0:
        return None, None
    order = np.argsort(pnl_mid)
    p = pnl_mid[order]
    ww = w[order] / w.sum()
    cw = np.cumsum(ww)
    idx = min(int(np.searchsorted(cw, alpha)), len(p) - 1)
    var = -float(p[idx])
    before = float(cw[idx - 1]) if idx > 0 else 0.0
    take = min(max(alpha - before, 0.0), float(ww[idx]))          # the straddling bucket's share of the tail
    tail_mass = before + take
    cvar = -(float(np.sum(p[:idx] * ww[:idx])) + float(p[idx]) * take) / tail_mass if tail_mass > 0 else var
    return round(max(var, 0.0), 2), round(max(cvar, 0.0), 2)


def _full_tail_var_cvar(life_legs, stock_shares, spot, w_iv, dte_days, r, alphas=(0.05, 0.01)) -> dict:
    """Deterministic FULL-TAIL VaR/CVaR: integrate the terminal P&L against the lognormal density on a WIDE,
    FINE price grid (≈1% → 3× spot) so the deep crash tail is NOT truncated — the ±50% scenario grid misses
    a near-zero collapse, which is exactly where a 99% CVaR on a high-win short lives. This is analytical in
    spirit (an exact quadrature over the risk-neutral/physical law), not a random Monte-Carlo simulation.
    Returns {alpha: (var$, cvar$)} — cvar is the mean loss in the worst-α tail (Expected Shortfall)."""
    curve = terminal_payoff_curve(life_legs, stock_shares, spot, lo=-0.985, hi=2.0, step=0.005)
    prices = np.array([c["price"] for c in curve], dtype=float)
    pnls = np.array([c["pnl"] for c in curve], dtype=float)
    w = _lognormal_weights(prices, spot, w_iv, dte_days, r)
    if w.sum() <= 0:
        return {a: (None, None) for a in alphas}
    pnl_mid = (pnls[:-1] + pnls[1:]) / 2.0
    wn = w / w.sum()
    return {a: _weighted_var_cvar(pnl_mid, wn, a) for a in alphas}


def _kelly_fraction(rets: np.ndarray, w: np.ndarray) -> Optional[float]:
    """Continuous Kelly on the return distribution: f* = E[r] / E[r²], clamped to
    [0,1] — a distribution-wide analogue of the binary Kelly."""
    er = float(np.sum(w * rets))
    er2 = float(np.sum(w * rets * rets))
    if er2 <= 1e-12:
        return None
    return round(max(0.0, min(1.0, er / er2)), 3)


# ── The trade's payoff DISTRIBUTION — one exact, skew-consistent law for PoP / EV / ratios / VaR / Kelly ──
# Two defects this replaces (both verified against an exact reference market):
#   1. A 2.5%-step scenario grid: the bucket holding the breakeven was all-or-nothing, so PoP was off by up to
#      ~7 pts and EV by ~10% of the premium, and neighbouring strikes swapped rank on grid alignment.
#   2. ONE flat vol (the average of the legs' IVs) for every leg: on a skewed smile that misprices every leg, so
#      a FAIR put spread read EV −$31 / Omega 0.5 (spreads, condors, jade lizards pushed down; call spreads up).
# The fix — cheap (a few hundred numpy points, no Python price loop) and exact where it matters:
#   • buckets are EXACT lognormal masses (Φ differences) with each bucket valued at its exact conditional-mean
#     price; every strike and every breakeven is a bucket edge, so the payoff is linear inside each bucket
#     ⇒ EV is exact and PoP has no breakeven bucket to misclassify;
#   • the PRIOR is the expiry's market-implied law (the scan's SVI → Breeden-Litzenberger density, carried as a
#     compact grid) — ONE consistent law for every trade on the expiry — else a lognormal centred on ATM IV;
#   • each leg is priced at its OWN vol (backed out of its mid) and the law is the minimum-relative-entropy tilt
#     of the prior that reprices them all while keeping the forward (Avellaneda-style calibration) ⇒ always a
#     valid density, and a fairly-priced trade of ANY structure has EV ≈ 0 at mid (carry only);
#   • the P-measure widening is preserved: realized variance above the centre implied is ADDED by convolving the
#     law with independent noise (body widens, the market's skewed wings keep their shape; a lone leg with no
#     anchor ⇒ exactly the old max(IV, HV) lognormal).
_DIST_NODES = 321           # log-spaced price nodes (plus every strike, ATM forward and breakeven)
_DIST_SPAN_SD = 8.0         # grid half-width in total-σ around the forward (mass outside ≈ 1e-15)
_CAL_MAX_ITER = 60
_CAL_TOL = 1e-10            # max |E[payoff] − target| per constraint, in units of the forward
_ATM_ANCHOR_RATIO = (0.4, 2.5)   # ATM IV accepted as the centre only within this ratio of the nearest leg's vol


def _ncdf(x):
    """Standard normal CDF, vectorized (scipy's ndtr ufunc — accurate deep in both tails)."""
    from scipy.special import ndtr
    return ndtr(x)


def _leg_vol(lg) -> Optional[float]:
    """A leg's quoted IV as a decimal (legs carry percent OR decimal, like horizon_payoff_curve)."""
    iv = lg.get("iv")
    try:
        iv = float(iv)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(iv) or iv <= 0:
        return None
    return iv / 100.0 if iv > 3 else iv


def _fwd_put(F: float, K: float, v: float) -> float:
    """Undiscounted (forward) Black put value E[(K − S_T)^+]; v = σ·√T."""
    if v <= 0:
        return max(K - F, 0.0)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    d2 = d1 - v
    n = lambda z: 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return K * n(-d2) - F * n(-d1)


def _vol_from_price(price: float, K: float, is_call: bool, F: float, T: float, r: float) -> Optional[float]:
    """The Black vol that reproduces this leg's traded price (bisection on the forward put value — a call is
    mapped through put-call parity). None when the price is outside the no-arbitrage band (stale/garbage)."""
    if not (price and price > 0 and K > 0 and T > 0):
        return None
    fwd = price * math.exp(r * T)
    put_fwd = fwd - (F - K) if is_call else fwd
    if not (max(K - F, 0.0) + 1e-12 < put_fwd < K):
        return None
    sq = math.sqrt(T)
    lo, hi = 1e-4, 5.0
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        if _fwd_put(F, K, mid * sq) > put_fwd:
            hi = mid
        else:
            lo = mid
    v = 0.5 * (lo + hi)
    return v if 0.02 <= v <= 4.0 else None


def _payoff_at(prices: np.ndarray, life_legs, stock_shares: float, spot: float, horizon_T: float,
               r: float, iv_fallback: float, multi_exp: bool) -> np.ndarray:
    """P&L ($) at an array of underlying prices AT THE HORIZON — vectorized, and the same math as
    `terminal_payoff_curve` (single expiry: every leg pays intrinsic) / `horizon_payoff_curve` (multi-expiry:
    a leg still alive at the horizon is Black-Scholes-marked at its own IV on its remaining life)."""
    P = np.asarray(prices, dtype=float)
    pnl = stock_shares * (P - spot)
    for lg in life_legs:
        K = float(lg.get("strike") or 0.0)
        is_call = str(lg.get("right", "")).upper().startswith("C")
        t_rem = (float(lg.get("dte_years") or 0.0) - horizon_T) if multi_exp else 0.0
        if t_rem <= 1e-6:
            val = np.maximum(P - K, 0.0) if is_call else np.maximum(K - P, 0.0)
        else:
            sig = _leg_vol(lg) or iv_fallback
            if not sig or sig <= 0:
                sig = iv_fallback
            v = sig * math.sqrt(t_rem)
            with np.errstate(divide="ignore", invalid="ignore"):
                d1 = (np.log(np.maximum(P, 1e-12) / K) + (r + 0.5 * sig * sig) * t_rem) / v
            d2 = d1 - v
            disc = K * math.exp(-r * t_rem)
            val = (P * _ncdf(d1) - disc * _ncdf(d2)) if is_call else (disc * _ncdf(-d2) - P * _ncdf(-d1))
        pnl = pnl + lg["sign"] * float(lg.get("qty") or 1) * 100.0 * (val - float(lg.get("price") or 0.0))
    return pnl


def _tilt_to_targets(m: np.ndarray, G: np.ndarray, c: np.ndarray) -> Optional[np.ndarray]:
    """Minimum-relative-entropy reweighting of prior masses ``m`` so that E_w[G] = c (one column per
    constraint). Convex dual, Newton with backtracking. Returns the new masses, or None if the targets are
    infeasible (an arbitrageable / inconsistent set of quotes) or the solve does not converge."""
    pos = m > 0
    logm = np.full(len(m), -np.inf)
    logm[pos] = np.log(m[pos])
    theta = np.zeros(G.shape[1])

    def _state(th):
        a = logm + G @ th
        amax = float(np.max(a[pos]))
        e = np.where(pos, np.exp(a - amax), 0.0)
        z = float(e.sum())
        return e / z, amax + math.log(z) - float(th @ c)

    w, phi = _state(theta)
    for _ in range(_CAL_MAX_ITER):
        eg = w @ G
        grad = eg - c
        if float(np.max(np.abs(grad))) < _CAL_TOL:
            return w
        gc = G - eg
        H = (gc * w[:, None]).T @ gc
        H[np.diag_indices_from(H)] += 1e-14 + 1e-10 * float(np.trace(H))
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            return None
        t = 1.0
        while t > 1e-8:
            w_n, phi_n = _state(theta - t * step)
            if math.isfinite(phi_n) and phi_n <= phi - 1e-4 * t * float(grad @ step):
                break
            t *= 0.5
        else:                                     # no descent left: accept only if already at precision
            return w if float(np.max(np.abs(grad))) < _CAL_TOL * 1e3 else None
        theta = theta - t * step
        w, phi = w_n, phi_n
    return w if float(np.max(np.abs(w @ G - c))) < _CAL_TOL * 1e3 else None


def _tilt_soft(m: np.ndarray, G: np.ndarray, c: np.ndarray, tol: np.ndarray,
               max_iter: int = 80) -> Optional[np.ndarray]:
    """Minimum-relative-entropy reweighting of prior masses ``m`` that matches E_w[G] = c to within ``tol`` per
    column (a quote's half-spread) — the bid/ask-aware, ALWAYS-feasible sibling of `_tilt_to_targets` (noisy or
    mutually-inconsistent quotes can't make it fail). Dual: min_θ log Σ m·e^{Gθ} − θ·c + ½ Σ (tol·θ)²."""
    pos = m > 0
    logm = np.full(len(m), -np.inf)
    logm[pos] = np.log(m[pos])
    t2 = np.asarray(tol, dtype=float) ** 2
    theta = np.zeros(G.shape[1])

    def _state(th):
        a = logm + G @ th
        amax = float(np.max(a[pos]))
        e = np.where(pos, np.exp(a - amax), 0.0)
        z = float(e.sum())
        return e / z, amax + math.log(z) - float(th @ c) + 0.5 * float(np.sum(t2 * th * th))

    w, phi = _state(theta)
    scale = np.maximum(np.sqrt(t2), 1e-9)

    def _gap(gr):                                 # optimality gap in units of each quote's tolerance
        return float(np.max(np.abs(gr) / scale))

    for _ in range(max_iter):
        eg = w @ G
        grad = eg - c + t2 * theta
        if _gap(grad) < 1e-3:
            return w
        gc = G - eg
        H = (gc * w[:, None]).T @ gc
        H[np.diag_indices_from(H)] += t2 + 1e-14
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            return None
        t = 1.0
        while t > 1e-8:
            w_n, phi_n = _state(theta - t * step)
            if math.isfinite(phi_n) and phi_n <= phi - 1e-4 * t * float(grad @ step):
                break
            t *= 0.5
        else:                                     # stalled: accept only if already at the optimum (precision)
            return w if _gap(grad) < 1e-2 else None
        theta = theta - t * step
        w, phi = w_n, phi_n
    return w if _gap(w @ G - c + t2 * theta) < 1e-2 else None     # never hand back a half-solved law


def law_is_sane(K: np.ndarray, cdf: np.ndarray, forward: float, T: float, atm_iv: Optional[float]) -> bool:
    """A market law is only trusted when it looks like one: mean ≈ the forward, and the log-return spread within a
    broad band of the ATM vol (catches a law collapsed onto a garbage quote — JPM 2026-11 put all its mass at $185
    off one stale wing quote). Shared by the scan (grid build) and the desk (grid use)."""
    try:
        w = np.diff(np.asarray(cdf, dtype=float))
        K = np.asarray(K, dtype=float)
        if len(w) < 10 or np.any(w < -1e-9) or abs(float(w.sum()) - 1.0) > 1e-3 or forward <= 0:
            return False
        m = 0.5 * (K[1:] + K[:-1])
        mean = float(w @ m) / float(w.sum())
        if abs(mean / forward - 1.0) > 0.01:
            return False
        lr = np.log(m / forward)
        sd = math.sqrt(max(float(w @ (lr * lr)) - float(w @ lr) ** 2, 0.0))
        if atm_iv and atm_iv > 0 and T > 0:
            ref = atm_iv * math.sqrt(T)
            return 0.4 * ref <= sd <= 2.5 * ref
        return sd > 0
    except (TypeError, ValueError):
        return False


def _usable_rnd_grid(g: Optional[dict], T: float, atm_iv: Optional[float] = None) -> Optional[tuple]:
    """(K, cdf, pe) arrays from a scan's compact market-implied law when it is well-formed AND for this horizon
    (the same expiry); else None → the lognormal prior is used."""
    try:
        if not g or abs(float(g.get("T") or 0.0) - T) > 2.0 / 365.0:
            return None
        K = np.asarray(g["K"], dtype=float)
        cdf = np.asarray(g["cdf"], dtype=float)
        pe = np.asarray(g["pe"], dtype=float)
        if len(K) < 50 or len(cdf) != len(K) or len(pe) != len(K) or np.any(np.diff(K) <= 0):
            return None
        if not (abs(cdf[-1] - 1.0) < 1e-3 and cdf[0] < 1e-3 and np.all(np.diff(cdf) >= -1e-9)):
            return None
        if not law_is_sane(K, cdf, float(g.get("forward") or 0.0), T, atm_iv):
            return None
        return K, np.clip(cdf, 0.0, 1.0), np.maximum(pe, 0.0)
    except (KeyError, TypeError, ValueError):
        return None


def _buckets_from_cdf_pe(edges: np.ndarray, cdf_e: np.ndarray, pe_e: np.ndarray, mean_all: float):
    """Bucket masses + conditional means on [0, e0], [e0, e1], …, [e_last, ∞) from the CDF and the partial
    expectation E[S·1{S>e}] at the edges. Means are clipped into their bucket (guards round-off)."""
    cdf = np.concatenate([[0.0], np.clip(cdf_e, 0.0, 1.0), [1.0]])
    part_e = np.concatenate([[mean_all], pe_e, [0.0]])
    mass = np.maximum(np.diff(cdf), 0.0)
    part = np.maximum(part_e[:-1] - part_e[1:], 0.0)
    b_lo = np.concatenate([[0.0], edges])
    b_hi = np.concatenate([edges, [edges[-1] * 2.0]])
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = np.where(mass > 1e-300, part / np.where(mass > 0, mass, 1.0), 0.5 * (b_lo + b_hi))
    mean = np.minimum(np.maximum(mean, b_lo), b_hi)
    tot = float(mass.sum())
    return (mass / tot if tot > 0 else mass), mean


def _widen(w: np.ndarray, mu: np.ndarray, edges: np.ndarray, dv: float, chunk: int = 64):
    """P-measure widening: convolve the law with independent lognormal noise of log-variance ``dv`` (mean 1, so
    the forward is kept) and re-bucket on the SAME edges (strikes / breakevens stay edges). Adds variance to the
    body while the market's skewed wings keep their shape. Chunked so memory stays O(chunk × buckets)."""
    sd = math.sqrt(dv)
    pos = (w > 0) & (mu > 0)
    wj, mj = w[pos], mu[pos]
    cdf_e = np.empty(len(edges))
    pe_e = np.empty(len(edges))
    lm = np.log(mj)
    for i in range(0, len(edges), chunk):
        le = np.log(edges[i:i + chunk])[:, None]
        cdf_e[i:i + chunk] = _ncdf((le - lm + 0.5 * dv) / sd) @ wj
        pe_e[i:i + chunk] = _ncdf((lm - le + 0.5 * dv) / sd) @ (wj * mj)
    return _buckets_from_cdf_pe(edges, cdf_e, pe_e, float(wj @ mj))


_NARROW_MIN_RATIO = 0.25    # never narrow the law below 25% of the market's variance (σ_P ≥ ½·σ_Q)


def _narrow(w: np.ndarray, mu: np.ndarray, F: float, var_ratio: float) -> Optional[np.ndarray]:
    """P-measure NARROWING (implied above the physical forecast — the seller's volatility risk premium): the
    minimum-relative-entropy reweighting of the market law whose log-return variance is ``var_ratio`` × the
    market's, keeping the forward. A Gaussian-shaped damping (tilt ∝ e^{θ·(x−x̄)²}, θ<0) — both tails thin
    smoothly; the market's skew shape is otherwise kept. None if it can't be solved (caller keeps the law)."""
    pos = (w > 0) & (mu > 0)
    if pos.sum() < 10:
        return None
    x = np.where(mu > 0, np.log(np.where(mu > 0, mu, 1.0) / F), 0.0)
    xbar = float(w @ x)
    d2 = (x - xbar) ** 2
    var_q = float(w @ d2)
    if var_q <= 0:
        return None
    G = np.column_stack([mu / F, d2 / var_q])
    c = np.array([1.0, max(var_ratio, _NARROW_MIN_RATIO)])
    return _tilt_to_targets(w, G, c)


def _payoff_distribution(life_legs, stock_shares: float, spot: float, dte_days: int, r: float,
                         implied: Optional[float], realized: Optional[float],
                         atm_iv: Optional[float] = None, iv_fallback: float = 0.30,
                         rnd_grid: Optional[dict] = None, event_var: float = 0.0,
                         event_fallback: bool = False) -> Optional[dict]:
    """The trade's terminal (or horizon) P&L distribution as exact buckets — see the block comment above.

    Prior = the expiry's MARKET-IMPLIED law (`rnd_grid`, the scan's SVI → Breeden-Litzenberger density) when
    available — one consistent law for every trade on the expiry — else a lognormal centred on ATM IV. It is then
    minimally tilted to reprice each leg at its mid (and keep the forward) — the Q (market) law.

    The P (physical) law re-sizes it to the PHYSICAL variance: ``realized`` (the desk's diffusion vol forecast)
    over the horizon plus ``event_var`` — an earnings print inside the window, as the MARKET's implied event
    variance (log-return σ², split from the term structure), so event premium is never mistaken for edge. When the
    print is inside but the market's variance can't be split (``event_fallback``), the physical variance is
    max(market total, diffusion forecast): no edge is claimed on an event we can't separate, but a diffusion
    forecast that alone exceeds everything the market prices is still a real negative VRP. Wider than the market → convolve
    (`_widen`); narrower (the seller's VRP) → entropy-narrow (`_narrow`). Expectation metrics (PoP / EV / Omega /
    Sortino / Kelly) use P; tail metrics use the WIDER of P and Q — a tail is never thinner than the market prices
    it.

    Returns {"w": masses, "pnl": $ per bucket, "price": conditional-mean price per bucket, "method",
    "center_vol", "weight_vol", "n_constraints", "atm_anchor", "max_price_err"} or None (caller falls back)."""
    if not life_legs or not spot or spot <= 0 or not dte_days or dte_days <= 0:
        return None
    dte_ys = [float(l.get("dte_years") or 0.0) for l in life_legs]
    multi_exp = len({round(d, 6) for d in dte_ys}) > 1
    T = (min(d for d in dte_ys if d > 0) if multi_exp and any(d > 0 for d in dte_ys) else dte_days / 365.0)
    if T <= 0:
        return None
    g = _usable_rnd_grid(rnd_grid, T, atm_iv)
    F = spot * math.exp(r * T)
    r_px = r                                       # rate that turns a quoted price into a forward value
    if g is not None:
        gF, gr = float(rnd_grid.get("forward") or 0.0), rnd_grid.get("r")
        if gF > 0 and abs(math.log(gF / F)) < 0.05:
            F = gF                                 # the market's parity forward (carry net of dividends)
            r_px = float(gr) if gr is not None else r

    # 1) Each strike's OWN vol, backed out of its MID (the market's fair value — `price` may be the expected
    #    fill, whose cost must surface as negative EV, not be absorbed into the vol), falling back to the quoted
    #    IV. Only legs that EXPIRE at the horizon constrain the horizon law (a calendar's back leg is marked on its
    #    own remaining life inside _payoff_at). Same-strike legs (a call & put) are averaged.
    by_k: dict[float, list[float]] = {}
    for lg in life_legs:
        if multi_exp and float(lg.get("dte_years") or 0.0) - T > 1e-6:
            continue
        K = float(lg.get("strike") or 0.0)
        if K <= 0:
            continue
        is_call = str(lg.get("right", "")).upper().startswith("C")
        mark = lg.get("mid") if lg.get("mid") not in (None, 0, 0.0) else lg.get("price")
        v = _vol_from_price(float(mark or 0.0), K, is_call, F, T, r_px) or _leg_vol(lg)
        if v and 0.02 <= v <= 4.0:
            by_k.setdefault(K, []).append(v)
    nodes = sorted((K, sum(vs) / len(vs)) for K, vs in by_k.items())

    # 2) Prior + CENTRE vol. The market law needs no anchor (it already carries the whole smile). The lognormal
    #    prior is centred on ATM IV when sane (it carries the skew a lone OTM leg cannot) — skipped when a leg
    #    already sits at the money — else on the leg nearest the forward.
    nearest = min(nodes, key=lambda kv: abs(math.log(kv[0] / F)))[1] if nodes else None
    atm_ok = bool(atm_iv and 0.03 <= atm_iv <= 3.0 and
                  (nearest is None or _ATM_ANCHOR_RATIO[0] <= atm_iv / nearest <= _ATM_ANCHOR_RATIO[1]))
    center = nearest or (implied if implied and implied > 0 else None) or (realized if realized and realized > 0 else None) or 0.30
    use_atm = False
    if atm_ok:
        if g is not None:
            center = atm_iv
        elif all(abs(math.log(K / F)) > 0.25 * atm_iv * math.sqrt(T) for K, _ in nodes):
            center, use_atm = atm_iv, True
    # P-measure widening: realized ABOVE the centre implied (negative VRP) adds the variance gap.
    dv = max(0.0, (realized * realized - center * center) * T) if (realized and realized > 0) else 0.0
    sig_w = math.sqrt(center * center + dv / T)

    # 3) Price grid: log-spaced over ±_DIST_SPAN_SD total-σ (sized off the WIDEST vol in play), always reaching
    #    past every strike so the open tail buckets are linear; every strike + the ATM forward is an edge.
    strikes = [float(l.get("strike") or 0.0) for l in life_legs if float(l.get("strike") or 0.0) > 0]
    _var_p = max(dv, max(float(event_var or 0.0), 0.0) + max((realized or 0.0) ** 2 * T - center * center * T, 0.0))
    s_max = math.sqrt(max([center] + [v for _, v in nodes]) ** 2 + _var_p / T) * math.sqrt(T)
    lo = min(F * math.exp(-_DIST_SPAN_SD * s_max), 0.85 * min(strikes, default=spot))
    hi = max(F * math.exp(_DIST_SPAN_SD * s_max), 1.15 * max(strikes, default=spot))
    edges = np.unique(np.concatenate([np.exp(np.linspace(math.log(lo), math.log(hi), _DIST_NODES)),
                                      np.array(strikes + [F], dtype=float)]))
    # 4) Breakevens become edges too (sign change between neighbours → linear root; exact for a terminal
    #    payoff, which is linear between consecutive edges once every strike is one).
    pe_ = _payoff_at(edges, life_legs, stock_shares, spot, T, r, iv_fallback, multi_exp)
    flip = np.nonzero(np.sign(pe_[:-1]) * np.sign(pe_[1:]) < 0)[0]
    if len(flip):
        roots = edges[flip] - pe_[flip] * (edges[flip + 1] - edges[flip]) / (pe_[flip + 1] - pe_[flip])
        edges = np.unique(np.concatenate([edges, roots]))

    # 5) Prior bucket masses + conditional-mean prices, incl. the two open tails [0, lo] and [hi, ∞).
    le = np.log(edges / F)

    def _phi_diff(a, b):                                          # Φ(b) − Φ(a), a ≤ b, cancellation-safe
        return np.where(a > 0, _ncdf(-a) - _ncdf(-b), _ncdf(b) - _ncdf(a))

    def _lognormal(sig):                                          # exact: P(S≤e)=Φ(z), E[S·1{S>e}]=F·Φ(v−z)
        v = sig * math.sqrt(T)
        z = np.concatenate([[-np.inf], (le + 0.5 * v * v) / v, [np.inf]])
        d1 = v - z
        mass = np.maximum(_phi_diff(z[:-1], z[1:]), 0.0)
        part = F * np.maximum(_phi_diff(d1[1:], d1[:-1]), 0.0)
        b_lo = np.concatenate([[0.0], edges])
        b_hi = np.concatenate([edges, [edges[-1] * 2.0]])
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = np.where(mass > 1e-300, part / np.where(mass > 0, mass, 1.0), 0.5 * (b_lo + b_hi))
        return mass / mass.sum(), np.minimum(np.maximum(mean, b_lo), b_hi)

    if g is not None:
        gK, gcdf, gpe = g
        m, mu = _buckets_from_cdf_pe(edges, np.interp(edges, gK, gcdf, left=0.0, right=1.0),
                                     np.interp(edges, gK, gpe, left=float(gpe[0]), right=0.0), float(gpe[0]))
        prior = "rnd"
    else:
        m, mu = _lognormal(center)
        prior = "lognormal"

    # 6) Calibrate: reprice every node strike at its own mid-implied vol (+ the ATM anchor on a lognormal prior),
    #    keep the forward — the minimum-relative-entropy tilt of the prior.
    cons = [(K, vol) for K, vol in nodes]
    if use_atm:
        cons.append((F, center))
    method, w = prior, m
    max_err = 0.0
    if cons:
        def _solve(cset):
            G = np.column_stack([mu / F] + [np.maximum(K - mu, 0.0) / F for K, _ in cset])
            c = np.array([1.0] + [_fwd_put(F, K, vk * math.sqrt(T)) / F for K, vk in cset])
            return _tilt_to_targets(m, G, c), G, c
        w_c, G, c = _solve(cons)
        if w_c is None and use_atm:                                # inconsistent with the anchor → legs only
            cons, use_atm = cons[:-1], False
            w_c, G, c = _solve(cons) if cons else (None, None, None)
        if w_c is not None:
            w, method = w_c, ("calibrated_rnd" if prior == "rnd" else "calibrated")
            max_err = float(np.max(np.abs(w @ G - c))) * F
        elif prior == "lognormal":
            # Quotes no single density can reprice (stale / crossed legs): keep the exact bucket grid but use the
            # previous flat law, max(implied, realized) — already physical, so no further widening.
            sig_w = max([x for x in (implied, realized) if x and x > 0] or [center])
            center = implied or sig_w
            w, mu = _lognormal(sig_w)
            method, dv = "lognormal_fallback", 0.0
        # (an RND prior that can't be tilted stays the market's own law — method "rnd")
    # 7) Physical (P) law: re-size the market law to the physical variance (diffusion forecast + event).
    w_q, mu_q = w, mu
    measure = "market"
    if method != "lognormal_fallback":
        v_q = center * center * T
        v_diff = (realized * realized * T) if (realized and realized > 0) else v_q
        v_p = max(v_q, v_diff) if event_fallback else v_diff + max(float(event_var or 0.0), 0.0)
        if v_p > v_q * (1 + 1e-9):
            w, mu = _widen(w, mu, edges, v_p - v_q)
            measure = "physical_wider"
        elif v_p < v_q * (1 - 1e-6):
            w_n = _narrow(w, mu, F, v_p / v_q)
            if w_n is not None:
                w, measure = w_n, "physical_narrower"
        sig_w = math.sqrt(max(v_p, 0.0) / T)
    elif dv > 1e-12:                               # (fallback law is already flat at max(implied, realized))
        w, mu = _widen(w, mu, edges, dv)
    pnl = _payoff_at(mu, life_legs, stock_shares, spot, T, r, iv_fallback, multi_exp)
    wider_p = measure != "physical_narrower"       # tails: the WIDER of the physical and market laws
    pnl_risk = pnl if wider_p else _payoff_at(mu_q, life_legs, stock_shares, spot, T, r, iv_fallback, multi_exp)
    return {"w": w, "pnl": pnl, "price": mu, "w_risk": w if wider_p else w_q, "pnl_risk": pnl_risk,
            "measure": measure, "method": method, "center_vol": center, "weight_vol": sig_w,
            "n_constraints": len(cons) if method.startswith("calibrated") else 0, "atm_anchor": bool(use_atm),
            "max_price_err": max_err, "multi_exp": multi_exp, "horizon_T": T}


# ── Income lens: CARRY-TO-RISK ─────────────────────────────────────────────────────────────────────────────
# premium collected ÷ the 1% expected shortfall (CVaR99) it underwrites, × √(365/DTE). √ (Sharpe-style), not
# linear: every cycle re-exposes you to the tail, so linear annualization would make a 7-DTE weekly look 6× a
# monthly; √ leaves only the mild short-tenor preference the VRP evidence supports. Log-scaled between bounds.
_CTR_LO = 0.03              # carry-to-risk (per year) that scores 0 on the Income lens — the live p5 …
_CTR_HI = 0.40              # … and 1 — the live p95 (24 liquid names, 261 desk candidates, 2026-10-15),
                            #     log-interpolated between
_CTR_CAP = 10.0             # a trade with ~no 1% tail (CVaR99 ≈ 0) is capped, not infinite
# Tail-lens denominator: "capital" = the trade's capital basis (Reg-T BPR / width / stock — financing-DEPENDENT);
# "exposure" = |max loss| when bounded, else the short legs' notional (financing-neutral, but a spread's tail IS its
# whole small exposure, so it punishes defined risk); "notional" = the underlying notional the shorts control
# (spot × 100 × short contracts on the larger side — financing-neutral AND structure-fair).
_TAIL_BASIS = "notional"     # chosen 2026-10-15 after the live 4-variant comparison (24 names / 261 candidates)


def _carry_to_risk(life_legs, cvar99: Optional[float], dte_days: int) -> Optional[dict]:
    """{"ctr", "per_cycle", "credit", "cvar99"} — or None for a debit structure / missing tail. Premium is the
    net option credit at the price you transact (`price` = expected fill on a new trade): option legs only, so a
    covered call's capped stock appreciation is never counted as income."""
    if not life_legs or not dte_days or dte_days <= 0 or cvar99 is None:
        return None
    credit = sum(-float(l["sign"]) * float(l.get("qty") or 1) * 100.0 * float(l.get("price") or 0.0) for l in life_legs)
    if credit <= 0:
        return None
    per_cycle = (credit / cvar99) if cvar99 > 1e-9 else _CTR_CAP
    ctr = min(per_cycle * math.sqrt(365.0 / dte_days), _CTR_CAP)
    return {"ctr": ctr, "per_cycle": per_cycle, "credit": credit, "cvar99": float(cvar99)}


def _tail_exposure(life_legs, spot: float, max_loss: Optional[float]) -> Optional[float]:
    """Financing-neutral size of what the trade can lose: |max loss| when bounded; otherwise the larger side's
    short notional (puts: strike, calls: spot — ×100×qty). None when there is nothing short to size."""
    if max_loss is not None and max_loss < 0:
        return abs(float(max_loss))
    put_n = sum(float(l.get("strike") or 0.0) * 100.0 * float(l.get("qty") or 1)
                for l in (life_legs or []) if l.get("sign", 0) < 0 and str(l.get("right", "")).upper().startswith("P"))
    call_n = sum(float(spot) * 100.0 * float(l.get("qty") or 1)
                 for l in (life_legs or []) if l.get("sign", 0) < 0 and str(l.get("right", "")).upper().startswith("C"))
    exp = max(put_n, call_n)
    return exp if exp > 0 else None


def _tail_notional(life_legs, spot: float) -> Optional[float]:
    """The underlying notional the trade's shorts control: spot × 100 × short contracts on the larger side."""
    sp = sum(float(l.get("qty") or 1) for l in (life_legs or [])
             if l.get("sign", 0) < 0 and str(l.get("right", "")).upper().startswith("P"))
    sc = sum(float(l.get("qty") or 1) for l in (life_legs or [])
             if l.get("sign", 0) < 0 and str(l.get("right", "")).upper().startswith("C"))
    n = float(spot) * 100.0 * max(sp, sc)
    return n if n > 0 else None


def _matches_curve(dist_args: tuple, scenarios: list[dict]) -> bool:
    """True when the legs reproduce the caller's own payoff curve — the guard that lets the exact path take
    over ONLY when the legs fully describe the trade (a caller that dropped a leg, or built a custom curve,
    keeps its scenario path). Tolerance covers the curve's 2-decimal rounding of price and P&L."""
    life_legs, stock_shares, spot, T, r, iv_fallback, multi_exp = dist_args
    try:
        P = np.array([float(s["price"]) for s in scenarios], dtype=float)
        given = np.array([float(s.get("pnl", s.get("pnl_at_expiry"))) for s in scenarios], dtype=float)
    except (KeyError, TypeError, ValueError):
        return False
    model = _payoff_at(P, life_legs, stock_shares, spot, T, r, iv_fallback, multi_exp)
    slope = sum(float(l.get("qty") or 1) * 100.0 for l in life_legs) + abs(stock_shares)
    tol = 0.006 * slope + 0.02 + 1e-3 * float(np.ptp(given) if len(given) else 0.0)
    return bool(np.all(np.abs(model - given) <= tol))


def algorithmic_quant(pm: dict, cvar95: Optional[float], capital: float, max_loss,
                      max_profit, kelly, dte_days: int, sofr_pct: float = 5.0,
                      income_mode: bool = False, buffer_mode: bool = False,
                      upside_cap_pct: Optional[float] = None,
                      carry: Optional[dict] = None, tail_capital: Optional[float] = None) -> dict:
    """Deterministic entry recommendation from the whole payoff distribution.

    ``income_mode`` RE-ANCHORS the blend for premium-income selling — the goal there is a SAFE trade that
    beats cash by a bit of alpha, NOT risk-neutral profit (fair-priced income has ~0 risk-neutral alpha, so
    the default edge/Sortino lenses read ~0). In income mode SAFETY (keep-prob) + INCOME (premium yield vs
    the cash hurdle) dominate, and the risk-neutral lenses are de-weighted; the vol edge (VRP) is added by
    the grade factors on top. The lifecycle/hold path leaves it False (unchanged).

    Blends five standard quant lenses into a 0-100 score:
      • Edge     — Omega (probability-weighted gains ÷ losses)
      • Hit-rate — Probability of Profit
      • Risk-adj — Sortino (return per unit of downside deviation)
      • Tail     — CVaR95 as a fraction of capital (smaller = better)
      • Carry    — expected return vs the risk-free hurdle over the horizon
    Verdict: ENTER ≥ 66, CONSIDER ≥ 45, else AVOID. Crucially the tail term uses
    CVaR (expected shortfall), NOT the deep worst case — so a large max loss that
    only bites near a collapse doesn't by itself sink an otherwise strong trade.
    """
    def clamp(x, lo=0.0, hi=1.0):
        return max(lo, min(hi, x))

    omega, pop = pm.get("omega"), pm.get("pop")
    sortino, exp_ret = pm.get("sortino"), pm.get("expected_return_pct")
    _tcap = tail_capital if (tail_capital and tail_capital > 0) else capital    # Tail-lens denominator (see _TAIL_BASIS)
    tail_frac = (cvar95 / _tcap) if (_tcap and _tcap > 0 and cvar95 is not None) else 0.5
    ctr = (carry or {}).get("ctr")                     # carry-to-risk (income mode) — see _carry_to_risk
    _tbase = ("underlying notional" if _TAIL_BASIS == "notional" else _TAIL_BASIS) if (tail_capital and tail_capital > 0) else "capital"
    hurdle = (sofr_pct / 100.0) * (dte_days / 365.0) * 100.0 if dte_days else 0.0
    prem_ann = ((max_profit / capital) * (365.0 / dte_days) * 100.0
                if (max_profit and capital and capital > 0 and dte_days) else None)   # gross premium yield %/yr

    if income_mode:
        # SAFE-INCOME anchoring — SAFETY (keep-prob) + INCOME (premium yield vs the cash hurdle) DOMINATE; the
        # risk-neutral lenses (edge = Omega, risk-adj = Sortino) are re-anchored gently and de-weighted; the
        # tail uses a gentler slope so a rare, contained assignment doesn't zero the score. See the docstring.
        s_edge = clamp(((omega or 0) - 0.88) / (1.15 - 0.88)) if omega is not None else 0.4    # Omega ≥ ~0.9 = fair+
        s_pop = clamp(((pop or 0) - 70.0) / (92.0 - 70.0)) if pop is not None else 0.4         # keep-prob 70→92%
        s_sortino = clamp(((sortino or 0) + 0.2) / 1.2) if sortino is not None else 0.4
        s_tail = clamp(1.0 - tail_frac / 0.90)
        # INCOME = carry-to-risk: premium collected per $ of 1%-tail underwritten, √-annualized, on a LOG scale
        # (it spans ~10× across a ladder). Financing-neutral — identical on Reg-T, cash-secured or portfolio
        # margin — and premium-only (a covered call's capped stock gain is not income). Debit structures
        # (calendars / back ratios) collect no premium → their expected return vs the cash hurdle.
        s_carry = (clamp(math.log(max(ctr, 1e-9) / _CTR_LO) / math.log(_CTR_HI / _CTR_LO)) if ctr is not None
                   else clamp(((exp_ret or 0) - hurdle) / (abs(hurdle) + 3.0) + 0.5))
        wts = {"edge": 0.12, "pop": 0.34, "sortino": 0.10, "tail": 0.14, "carry": 0.30}
    elif buffer_mode:
        # DEFENSIVE-PARTICIPATION anchoring — a dual-direction buffer / protective hedge is bought for
        # PROTECTED equity participation, NOT to beat cash carry. So downside PROTECTION (contained tail)
        # and a high probability of a good outcome DOMINATE; positive expected participation is rewarded
        # against ZERO (not the SOFR hurdle), so a defensive structure that lags cash isn't punished for
        # doing its job. This is the opposite anchoring from typical premium-selling / directional trades.
        s_edge = clamp(((omega or 0) - 0.9) / (1.6 - 0.9)) if omega is not None else 0.5       # Omega ≥ ~1 = fine
        s_pop = clamp(((pop or 0) - 55.0) / (90.0 - 55.0)) if pop is not None else 0.5         # decent odds of a gain
        s_sortino = clamp((sortino or 0) / 1.5) if sortino is not None else 0.5
        s_tail = clamp(1.0 - tail_frac / 0.50)                                                 # PROTECTION is the point
        # Participation: a dual-direction buffer must KEEP real upside. When the achieved
        # upside cap is known, score participation off it (a good cap ≈ 12%+); a collapsed
        # cap ⇒ it's no longer "dual direction" and this lens must tank. Fall back to E[ret].
        if upside_cap_pct is not None:
            s_carry = clamp(upside_cap_pct / 12.0)
        else:
            s_carry = clamp(((exp_ret or 0)) / 8.0 + 0.5)
        wts = {"edge": 0.18, "pop": 0.25, "sortino": 0.13, "tail": 0.26, "carry": 0.18}
    else:
        s_edge = clamp(((omega or 0) - 0.8) / (2.0 - 0.8)) if omega is not None else 0.4
        s_pop = clamp(((pop or 0) - 50.0) / (90.0 - 50.0)) if pop is not None else 0.4
        s_sortino = clamp((sortino or 0) / 2.0) if sortino is not None else 0.4
        s_tail = clamp(1.0 - tail_frac / 0.40)
        s_carry = clamp(((exp_ret or 0) - hurdle) / (abs(hurdle) + 3.0) + 0.5)
        wts = {"edge": 0.28, "pop": 0.22, "sortino": 0.20, "tail": 0.18, "carry": 0.12}

    score = round((s_edge*wts["edge"] + s_pop*wts["pop"] + s_sortino*wts["sortino"]
                   + s_tail*wts["tail"] + s_carry*wts["carry"]) * 100)

    reasons = []
    if omega is not None:
        reasons.append(f"Omega {omega:.2f} ({'edge' if omega >= 1 else 'no edge'})")
    if pop is not None:
        reasons.append(f"PoP {pop:.0f}%")
    if sortino is not None:
        reasons.append(f"Sortino {sortino:.2f}")
    if capital and cvar95 is not None:
        label = "downside protection — CVaR" if buffer_mode else "CVaR95"
        reasons.append(f"{label} {tail_frac*100:.0f}% of {_tbase} (expected shortfall, not deep tail)")
    if income_mode and ctr is not None:
        reasons.append(f"carry-to-risk {ctr * 100:.0f}%/yr (${carry['credit']:.0f} premium per "
                       f"${carry['cvar99']:.0f} of 1% tail)")
    elif income_mode and prem_ann is not None:
        reasons.append(f"premium yield {prem_ann:.1f}%/yr vs {sofr_pct:.1f}% cash (the income alpha)")
    elif buffer_mode and upside_cap_pct is not None:
        reasons.append(f"upside participation to a {upside_cap_pct:.1f}% cap")
    elif buffer_mode and exp_ret is not None:
        reasons.append(f"expected participation {exp_ret:+.1f}% (protected — judged vs 0, not the cash hurdle)")
    elif exp_ret is not None:
        reasons.append(f"exp. return {exp_ret:+.1f}% vs {hurdle:.1f}% hurdle")
    if kelly is not None:
        reasons.append(f"Kelly {kelly*100:.0f}%")

    # Hard gate: a "dual-direction buffer" whose upside cap has collapsed (<2%) is no
    # longer dual-direction — never let it read ENTER no matter how strong the protection.
    if buffer_mode and upside_cap_pct is not None and upside_cap_pct < 2.0:
        score = min(score, 44)
        reasons.append(f"upside cap collapsed to {upside_cap_pct:.1f}% — not a genuine dual-direction buffer")

    if score >= 66:
        verdict, tone = "ENTER", "good"
    elif score >= 45:
        verdict, tone = "CONSIDER", "warn"
    else:
        verdict, tone = "AVOID", "bad"

    # Per-lens breakdown (label · sub-score · weight · weighted contribution · metric-carrying note) so the
    # ENTRY base quality is as auditable as the management base — NOT a black box. The note leads with THIS
    # trade's measured metric (the UI shows it inline); the generic meaning follows the em-dash (on hover).
    # Σ(contribution) == score by construction, so the lenses reconcile to the base.
    _carry_note = (f"carry-to-risk {ctr * 100:.0f}%/yr — ${carry['credit']:.0f} premium per ${carry['cvar99']:.0f} "
                   f"of 1%-tail ({carry['per_cycle'] * 100:.1f}% per cycle) — income per $ of tail you underwrite"
                   if (income_mode and ctr is not None)
                   else f"exp. return {exp_ret:+.1f}% vs {hurdle:.1f}% hurdle — a debit structure collects no premium"
                   if (income_mode and exp_ret is not None)
                   else f"expected participation {exp_ret:+.1f}% — protected return, judged vs 0 not the cash hurdle"
                   if (buffer_mode and exp_ret is not None)
                   else f"exp. return {exp_ret:+.1f}% vs {hurdle:.1f}% hurdle — carry over the risk-free rate"
                   if exp_ret is not None else "premium yield vs the cash hurdle — the income alpha you harvest")
    _lens_spec = [
        ("Safety",   s_pop,     wts["pop"],
         (f"PoP {pop:.0f}% — probability you keep the FULL premium (the short expires OTM)" if pop is not None
          else "probability you keep the full premium — the short expires OTM")),
        ("Income",   s_carry,   wts["carry"], _carry_note),
        ("Edge",     s_edge,    wts["edge"],
         (f"Omega {omega:.2f} — probability-weighted gains ÷ losses (≥1 = you keep more than you risk)" if omega is not None
          else "Omega — probability-weighted gains vs losses (≥1 = edge)")),
        ("Tail",     s_tail,    wts["tail"],
         (f"CVaR95 {abs(tail_frac)*100:.0f}% of {_tbase} — the contained expected shortfall, not the deep worst case"
          if (capital and cvar95 is not None) else "expected shortfall (CVaR) vs capital — how contained the left tail is")),
        ("Risk-adj", s_sortino, wts["sortino"],
         (f"Sortino {sortino:.2f} — reward per unit of downside deviation" if sortino is not None
          else "Sortino — reward per unit of downside deviation")),
    ]
    lenses = [{"label": lbl, "score": round(s * 100), "weight": round(wt * 100),
               "contribution": round(s * wt * 100, 1), "note": note} for lbl, s, wt, note in _lens_spec]
    return {"verdict": verdict, "tone": tone, "score": score, "reasons": reasons,
            "lenses": lenses,
            "subscores": {"edge": round(s_edge*100), "pop": round(s_pop*100),
                          "sortino": round(s_sortino*100), "tail": round(s_tail*100),
                          "carry": round(s_carry*100)}}


def algorithmic_exit(pm: dict, cvar95: Optional[float], capital: float, max_loss,
                     max_profit, kelly, dte_days: int, captured_pct: Optional[float],
                     unrealized_pnl: Optional[float], sofr_pct: float = 5.0) -> dict:
    """The *lifecycle* algorithmic recommendation for a PLACED trade — reliable and
    fully transparent (no LLM). Reuses the 5-lens base-quality score (how good the
    position still is to HOLD) and layers the two things that only matter once a
    trade is on: how much profit is already banked, and how close expiry's
    gamma/pin risk is. Buildup is auditable:

        hold_score = base_quality + Σ(lifecycle adjustments)
        STRONG_HOLD ≥ 68 · HOLD ≥ 45 · CLOSE ≥ 28 · STRONG_CLOSE < 28
        (+ hard overrides: ≥85% captured, near max loss, ≤2 DTE)
    """
    base = algorithmic_quant(pm, cvar95, capital, max_loss, max_profit, kelly, dte_days, sofr_pct)
    overlay = lifecycle_overlay(base["score"], captured_pct, dte_days, unrealized_pnl, max_loss)
    return {
        "signal": overlay["signal"], "score": overlay["score"], "base_quality": base["score"],
        "subscores": base["subscores"], "adjustments": overlay["adjustments"],
        "reasons": base["reasons"], "overrides": overlay["overrides"],
    }


# A hard override (≥90% captured, near max loss, tested strike, ≤2 DTE …) can demote the SIGNAL past what the points
# alone say. The number shown beside it must not contradict the label — "53/100 STRONG CLOSE" sits in the 45–68 HOLD
# band — so an override also caps the SCORE at the top of the band the final signal belongs to (STRONG_HOLD ≥68 ·
# HOLD ≥45 · CLOSE ≥28 · STRONG_CLOSE <28). The Trade Manager's quant lens already floored its blended score this way;
# this is the ONE definition all three (light overlay, deep desk score, Trade Manager) share, and `raw_score` keeps the
# pre-override points for audit.
SIGNAL_SCORE_CEILING = {"HOLD": 67, "CLOSE": 44, "STRONG_CLOSE": 27}


def clamp_score_to_signal(score: float, signal: str) -> int:
    """`score` capped at the ceiling of `signal`'s band (STRONG_HOLD has none). Never raises a score."""
    cap = SIGNAL_SCORE_CEILING.get(signal)
    return int(min(score, cap)) if cap is not None else int(score)


def lifecycle_overlay(base_score: float, captured_pct: Optional[float], dte_days: Optional[int],
                      unrealized_pnl: Optional[float], max_loss) -> dict:
    """Turn ANY 0-100 quality score for a PLACED trade into the 4-level exit signal.

    Layers the two things that only matter once a trade is on — profit already
    banked (the take-profit discipline) and expiry's gamma/pin risk — then applies
    hard overrides. Shared by the light 5-lens engine AND the full desk score, so
    the exit mapping is identical no matter which base quality drives it.

        hold_score = base_score + Σ(adjustments)
        STRONG_HOLD ≥ 68 · HOLD ≥ 45 · CLOSE ≥ 28 · STRONG_CLOSE < 28
        overrides: ≥85% captured, near max loss, ≤2 DTE
    """
    adjustments: list[dict] = []
    adj = 0.0
    if captured_pct is not None:
        cp = (-50 if captured_pct >= 85 else -35 if captured_pct >= 60
              else -22 if captured_pct >= 40 else -10 if captured_pct >= 25 else 0)
        if cp:
            adjustments.append({"name": "Profit captured", "pts": cp,
                                "note": f"{captured_pct:.0f}% of max profit banked — less left to earn"})
            adj += cp
    if dte_days is not None:
        td = -25 if dte_days <= 2 else -10 if dte_days <= 7 else -3 if dte_days <= 21 else 4
        adjustments.append({"name": "Time / gamma", "pts": td,
                            "note": f"{dte_days} DTE" + (" — gamma/pin risk elevated" if dte_days <= 7 else " — runway to keep collecting" if td > 0 else "")})
        adj += td

    hold_score = int(max(0, min(100, round(base_score + adj))))
    signal = ("STRONG_HOLD" if hold_score >= 68 else "HOLD" if hold_score >= 45
              else "CLOSE" if hold_score >= 28 else "STRONG_CLOSE")

    overrides: list[str] = []
    if captured_pct is not None and captured_pct >= 85:
        signal = "STRONG_CLOSE"
        overrides.append("≥85% of max profit captured — bank it")
    if max_loss is not None and max_loss < 0 and unrealized_pnl is not None and unrealized_pnl <= max_loss * 0.8:
        signal = "STRONG_CLOSE"
        overrides.append("near max loss — cut it")
    if dte_days is not None and dte_days <= 2 and signal in ("STRONG_HOLD", "HOLD"):
        signal = "CLOSE"
        overrides.append("≤2 DTE — gamma/pin/assignment risk")

    return {"signal": signal, "score": clamp_score_to_signal(hold_score, signal), "raw_score": hold_score,
            "adjustments": adjustments, "overrides": overrides}


def _management_factors(*, iv_pct, hv_pct, pop_pct, keep_drift_pct, cushion_pct,
                        captured_pct, dte_days, is_income: bool,
                        structure: Optional[str] = None) -> list[dict]:
    """Holder-framed reading of the SAME market factors the entry desk score uses,
    but interpreted for someone ALREADY in the position — where the entry sign is
    often inverted. Display only; the signal comes from the overlay. `favorable`
    is True (good for the holder) / False (a risk) / None (context).

    The canonical example the entry score gets 'backwards' for a holder: negative
    VRP (implied < realized) is an entry DEMERIT (you'd be selling cheap vol) but
    for someone SHORT premium it means the options are decaying cheaply in their
    favour — a REASON THE POSITION IS WORKING, not a reason to bail.
    """
    f: list[dict] = []

    if is_income and iv_pct is not None and hv_pct is not None:
        if iv_pct <= hv_pct:
            f.append({"label": "Vol decay", "favorable": True,
                      "note": (f"IV {iv_pct:.0f}% ≤ realized {hv_pct:.0f}% — the market isn't pricing a big "
                               "move; your short premium is bleeding out in your favour and is cheap to buy back. "
                               "(The entry score marks this DOWN because you'd be selling cheap vol — irrelevant once you're short.)")})
        else:
            f.append({"label": "Vol premium", "favorable": None,
                      "note": (f"IV {iv_pct:.0f}% > realized {hv_pct:.0f}% — extra extrinsic still in your shorts; "
                               "a vol drop accelerates your gain, a spike works against you.")})

    if keep_drift_pct is not None and pop_pct is not None:
        d = keep_drift_pct - pop_pct
        if d <= -5:
            f.append({"label": "Trend", "favorable": False,
                      "note": (f"Recent trend is a headwind toward your short strike — keep-prob "
                               f"{pop_pct:.0f}% → {keep_drift_pct:.0f}% once velocity is folded in.")})
        elif d >= 5:
            f.append({"label": "Trend", "favorable": True,
                      "note": (f"Trend is drifting AWAY from your short strike (tailwind) — keep-prob "
                               f"lifts {pop_pct:.0f}% → {keep_drift_pct:.0f}%.")})
        else:
            f.append({"label": "Trend", "favorable": None,
                      "note": f"Trend broadly neutral to your strike — keep-prob ~{keep_drift_pct:.0f}%."})

    if cushion_pct is not None:
        if cushion_pct <= 0:
            f.append({"label": "Strike tested", "favorable": False,
                      "note": "Spot has reached your short strike — defend (roll) or close; gamma is against you here."})
        elif cushion_pct < 5:
            f.append({"label": "Cushion", "favorable": False,
                      "note": f"Only {cushion_pct:.1f}% between spot and your short strike — thin buffer, watch closely."})
        else:
            f.append({"label": "Cushion", "favorable": True,
                      "note": f"{cushion_pct:.1f}% buffer to your short strike — comfortably out-of-the-money."})

    # Covered vs naked short call — capital is already committed, so the RISK differs.
    if structure == "covered_call":
        f.append({"label": "Covered", "favorable": None,
                  "note": ("You hold the stock — the risk here is being CALLED AWAY above the strike, not a cash "
                           "loss. If you want to keep the shares, roll the call up/out when it's threatened; "
                           "otherwise let it decay for the income.")})
    elif structure == "naked_call":
        f.append({"label": "Naked call", "favorable": False,
                  "note": ("No stock behind this call — upside risk is UNBOUNDED. A sharp rally loses far more than "
                           "the credit; keep it small and defend (roll up / buy a wing / close) if the strike is threatened.")})

    if captured_pct is not None:
        if captured_pct < 0:
            f.append({"label": "Underwater", "favorable": False,
                      "note": (f"Down {abs(captured_pct):.0f}% of max profit — the short premium has moved AGAINST "
                               "you (the position is at a loss). Watch the tested strike and your loss discipline, "
                               "not theta.")})
        elif captured_pct >= 50:
            f.append({"label": "Take profit", "favorable": None,
                      "note": (f"{captured_pct:.0f}% of max profit banked — most of the juice is gone; the "
                               "remainder isn't worth the gamma/assignment risk of holding on.")})
        else:
            f.append({"label": "Theta left", "favorable": True,
                      "note": f"Only {captured_pct:.0f}% of max profit captured — meaningful premium still to decay in your favour."})

    if dte_days is not None and dte_days <= 7:
        f.append({"label": "Gamma clock", "favorable": False,
                  "note": f"{dte_days} DTE — gamma/pin/assignment risk climbs into expiry; small moves swing P&L hard."})

    return f


def _management_base(*, keep_pct: Optional[float], captured_pct: Optional[float],
                     cushion_pct: Optional[float], omega: Optional[float],
                     sortino: Optional[float], cvar95: Optional[float],
                     capital: Optional[float]) -> dict:
    """COMPUTED hold-quality (0-100) for a placed trade — the holder analogue of the
    scan's `algorithmic_quant` base, but measuring REMAINING risk vs REMAINING reward
    from HERE instead of enter-vs-skip. Replaces the old fixed-50 anchor.

    The five lenses answer "given I'm already in, is what's LEFT worth the risk?":
      • Edge      — keep-prob: does the short strike survive to expiry?
      • Reward    — premium still to decay × the chance of actually keeping it (this is
                    what makes a booked winner score DOWN smoothly, so we no longer need
                    a punitive profit-captured overlay — but a trade with lots left and a
                    manageable tail stays HIGH even while green).
      • Risk-adj  — Omega + Sortino of the remaining payoff (risk-adjusted quality).
      • Tail      — CVaR95 as a fraction of capital: is the downside MANAGEABLE?
      • Cushion   — buffer from spot to the short strike.
    A missing metric scores its lens NEUTRAL (0.5), never a penalty. Returns
    {base, lenses[]} — the lenses are surfaced so the read is fully auditable."""
    def clamp(x, lo=0.0, hi=1.0):
        return max(lo, min(hi, x))

    # 1) Edge — probability the short strike holds to expiry.
    s_edge = clamp(((keep_pct - 50.0) / 40.0)) if keep_pct is not None else 0.5
    # 2) Reward left — remaining premium × expected-capture (keep-prob). Little left → low.
    rem = clamp(1.0 - (captured_pct or 0) / 100.0)
    kp = (keep_pct if keep_pct is not None else 65.0) / 100.0
    s_reward = clamp(rem * kp / 0.7)                         # ~70% expected capture = full marks
    # 3) Risk-adjusted quality of the REMAINING payoff.
    s_omega = clamp(((omega - 0.8) / 1.2)) if omega is not None else 0.5
    s_sortino = clamp(sortino / 2.0) if sortino is not None else 0.5
    s_riskadj = 0.5 * s_omega + 0.5 * s_sortino
    # 4) Tail — is the downside manageable? CVaR95 (expected shortfall) as % of capital.
    if capital and capital > 0 and cvar95 is not None:
        tail_frac = abs(cvar95) / capital
        s_tail = clamp(1.0 - tail_frac / 0.40)
    else:
        tail_frac, s_tail = None, 0.5
    # 5) Cushion — buffer to the short strike (tested = 0, ≥10% = full).
    s_cushion = clamp((cushion_pct or 0) / 10.0) if cushion_pct is not None else 0.5

    w = {"edge": 0.28, "reward": 0.20, "riskadj": 0.20, "tail": 0.18, "cushion": 0.14}
    # Each lens: (label, 0..1 sub-score, weight, note). base = Σ (sub × weight) × 100, and
    # every lens carries its weight + weighted contribution so the UI can show the FULL
    # arithmetic (sub × weight% = pts) that reaches the base — nothing is a black box.
    spec = [
        ("Edge survives", s_edge, w["edge"],
         f"{keep_pct:.0f}% keep-prob to expiry — does the short strike survive?" if keep_pct is not None else "keep-prob unavailable"),
        ("Reward left", s_reward, w["reward"],
         f"{rem*100:.0f}% of premium still to decay × keep-prob — expected capture from here"),
        ("Risk-adjusted", s_riskadj, w["riskadj"],
         (f"Omega {omega:.2f} · Sortino {sortino:.2f} — reward per unit of downside" if (omega is not None and sortino is not None)
          else "risk-adjusted quality of the remaining payoff (Omega/Sortino unavailable → neutral)")),
        ("Tail manageable", s_tail, w["tail"],
         (f"CVaR95 {tail_frac*100:.0f}% of capital — is the expected shortfall containable?" if tail_frac is not None
          else "downside not yet priced → neutral")),
        ("Cushion", s_cushion, w["cushion"],
         f"{cushion_pct:.1f}% from spot to your short strike" if cushion_pct is not None else "cushion unavailable"),
    ]
    base = sum(s * wt for _, s, wt, _ in spec) * 100.0
    lenses = [
        {"label": lbl, "score": round(s * 100), "weight": round(wt * 100),
         "contribution": round(s * wt * 100, 1), "note": note}
        for lbl, s, wt, note in spec
    ]
    return {"base": round(base, 1), "lenses": lenses}


def management_exit(*, pop_pct: Optional[float], captured_pct: Optional[float],
                    dte_days: Optional[int], unrealized_pnl: Optional[float] = None,
                    max_profit=None, max_loss=None, keep_drift_pct: Optional[float] = None,
                    iv_pct: Optional[float] = None, hv_pct: Optional[float] = None,
                    cushion_pct: Optional[float] = None, theta_per_day: float = 0.0,
                    structure: Optional[str] = None,
                    quality_subscores: Optional[dict] = None,
                    quality_score: Optional[float] = None) -> dict:
    """The MANAGEMENT recommendation for a trade you ALREADY hold — stay in to keep
    the edge decaying, or close to bank it / shed risk.

    A placed trade starts from a NEUTRAL 50 ("no reason either way to keep or close")
    and the holder-relevant factors push it toward HOLD or CLOSE: the chance of
    KEEPING the edge, vol decay, cushion and trend — then the take-profit + time/gamma
    overlay + hard overrides. This is deliberately NOT the entry desk score (which
    marks every short-premium trade poorly), and NOT the raw keep-prob either:
    anchoring the score on keep-prob floored every safe trade near 90+ so it read
    HOLD no matter how thin the remaining edge. A neutral base lets the factors
    decide. The entry 5-lens is still shown as reference (`base_quality`/`subscores`).
    """
    keep = keep_drift_pct if keep_drift_pct is not None else pop_pct
    hf: list[dict] = []
    if keep is not None:
        kp = (12 if keep >= 90 else 8 if keep >= 80 else 4 if keep >= 70
              else 0 if keep >= 55 else -8 if keep >= 45 else -18)
        if kp:
            hf.append({"name": "Keep-prob", "pts": kp,
                       "note": (f"{keep:.0f}% chance of keeping the edge to expiry — safe to hold" if kp > 0
                                else f"only {keep:.0f}% chance of keeping the edge — the position is at risk")})
    if (theta_per_day or 0.0) > 0 and iv_pct is not None and hv_pct is not None:
        hf.append({"name": "Vol decay", "pts": 5,
                   "note": "implied ≤ realized — your short premium is decaying cheaply in your favour"}
                  if iv_pct <= hv_pct else
                  {"name": "Vol premium", "pts": -3,
                   "note": "rich implied still in your shorts — a vol spike works against you"})
    if cushion_pct is not None:
        cf = 4 if cushion_pct >= 5 else -6 if cushion_pct > 0 else -14
        hf.append({"name": "Cushion", "pts": cf,
                   "note": f"{cushion_pct:.1f}% from spot to your short strike"})
    if keep_drift_pct is not None and pop_pct is not None:
        d = keep_drift_pct - pop_pct
        if d <= -5:
            hf.append({"name": "Trend", "pts": -5, "note": "recent trend is drifting toward your short strike"})
        elif d >= 5:
            hf.append({"name": "Trend", "pts": 4, "note": "recent trend is drifting away from your short strike"})
    if structure == "naked_call":
        hf.append({"name": "Naked risk", "pts": -6, "note": "unbounded upside — no stock behind the call"})

    hf_sum = sum(f["pts"] for f in hf)
    overlay = lifecycle_overlay(50 + hf_sum, captured_pct, dte_days, unrealized_pnl, max_loss)
    return {
        "signal": overlay["signal"], "score": overlay["score"],
        "hold_base": int(round(50 + hf_sum)),
        "base_source": "hold",
        "adjustments": overlay["adjustments"], "overrides": overlay["overrides"],
        "factors": _management_factors(
            iv_pct=iv_pct, hv_pct=hv_pct, pop_pct=pop_pct, keep_drift_pct=keep_drift_pct,
            cushion_pct=cushion_pct, captured_pct=captured_pct, dte_days=dte_days,
            is_income=(theta_per_day or 0.0) > 0, structure=structure),
        # entry-flavoured 5-lens, reference only (NOT the anchor):
        "subscores": quality_subscores or {},
        "base_quality": quality_score,
    }


# How each ENTRY desk factor is re-read for someone ALREADY holding the trade.
# (weight, holder-note). weight 1.0 = keep · 0 = drop · negative = FLIP the sign
# (the entry demerit becomes a holder positive, or vice-versa).
_MGMT_FACTOR_POLICY: dict = {
    # ── does the short strike survive? — KEEP, same sign (common with the entry desk) ──
    "Moneyness":    (1.0,  "cushion from spot to your short strike — the further out, the safer the hold"),
    # ── vol — FLIP, but GENTLY: cheap/falling implied is a clear holder positive
    # (decaying, cheap to buy back). Rich remaining implied is NOT a pure demerit the
    # way the entry score treats it — it's also premium still to collect; only the
    # spike RISK is a negative, so we damp the flip (−0.6 → −0.3) and the base's
    # Reward/Tail lenses carry the rest. (This is the "penalised for lower vol" fix.)
    "VRP":          (-0.3, "cheap/falling implied is GOOD once you're short (decaying, cheap to buy back); rich implied is mostly still-collectable premium, only a spike hurts"),
    # ── earnings — an ENTRY-timing demerit that becomes a HOLD RISK once you're in: the
    # binary gap is now yours to defend/close before the print, not a reason to skip. Kept
    # (not flipped), lightly downweighted — you can still act, unlike at entry.
    "Earnings timing": (0.7, "earnings BEFORE expiry — a binary gap you now HOLD through; theta is event compensation, not free decay. Defend / close / roll past the print rather than let the fat premium lull you"),
    # ── liquidity — the cost to ROLL or CLOSE if you need to act. Real, but NOT a reason to
    # hold-or-fold on its own (renamed from the punitive "Exit cost", downweighted 0.5→0.35).
    "Liquidity":    (0.35, "bid/ask width — the cost to ROLL or CLOSE if you have to act (a friction to plan around, not a hold-vs-close signal by itself)"),
    # ── expected value — a loss at high keep-prob is a remote tail ──────────
    "Expectation":  (0.4,  "a losing expectation here is the remote tail, not the base case"),
    # ── entry-only, irrelevant / double-counted once held ──────────────────
    # Skew: the tail it warns about is already in the base's CVaR lens.
    # Beta: the position's market risk is carried by the TA "Systemic beta" factor (kept
    #       as-is), so re-adding the entry Beta demerit would double-count it.
    "Skew":         (0.0,  ""),
    "Beta":         (0.0,  ""),
    # ── term structure — a holder still benefits from contango roll-down / is warned by an event-inverted
    #    curve; kept same sign, lightly downweighted (you can still act once in). ──
    "Term structure": (0.6, "contango = the short keeps rolling DOWN the curve (favorable carry); backwardation = an event-inverted curve, a caution to defend/close before the move"),
}


def _mgmt_factor_data(label: str, adj: dict, cushion_pct: Optional[float],
                      next_earnings: Optional[str] = None) -> str:
    """A concise, trade-specific DATA value for a management factor row — the number the UI shows
    BEFORE the em-dash (the holder help follows it, on hover). Pulls the RIGHT datum per factor from
    the scan's own evidence (cushion %/σ, IV vs HV, bid/ask width, earnings date), so every row
    carries a number rather than a generic sentence."""
    detail = (adj.get("detail") or "").strip()
    lead = detail.split(" — ")[0].split("; ")[0].strip() if detail else ""

    if label == "Moneyness":
        # % cushion from spot to the short strike (+ the σ multiple the scan already measured).
        bits = []
        if cushion_pct is not None:
            bits.append(f"{cushion_pct:.1f}% cushion")
        m = re.search(r"([\d.]+)\s*σ", detail)
        if m:
            bits.append(f"{m.group(1)}σ from spot")
        if bits:
            return " · ".join(bits)
        return lead

    if label == "VRP":
        # implied-vs-realized — the whole point of "vol decay / premium".
        m = re.search(r"IV/HV\s*([\d.]+)", detail)
        if m:
            return f"IV/HV {m.group(1)}× (implied vs realized)"
        m = re.search(r"implied\s*(\d+)%\s*of\s*realized", detail)
        if m:
            return f"implied {m.group(1)}% of realized vol"
        return lead

    if label == "Earnings timing":
        # the actual print date + how many days out — a binary event you hold through.
        when = ""
        if next_earnings:
            try:
                ed = date.fromisoformat(next_earnings)
                days = (ed - date.today()).days
                when = f"earnings {ed.strftime('%b %-d')}" + (f" · in {days}d" if days >= 0 else "")
            except (ValueError, TypeError):
                when = ""
        if when:
            return when
        m = re.search(r"earnings in (\d+)d", detail)     # scan fallback ("earnings in 5d …")
        if m:
            return f"earnings in {m.group(1)}d"
        return lead

    return lead                      # Liquidity (spread %), Term structure (vp), Expectation, … lead with their own number


def management_desk_score(*, keep_drift_pct: Optional[float], keep_standard_pct: Optional[float],
                          subscores: Optional[dict], grade_adjustments: Optional[list],
                          ta_factors: Optional[list], captured_pct: Optional[float],
                          dte_days: Optional[int], unrealized_pnl: Optional[float] = None,
                          max_profit=None, max_loss=None, cushion_pct: Optional[float] = None,
                          structure: Optional[str] = None,
                          omega: Optional[float] = None, sortino: Optional[float] = None,
                          cvar95: Optional[float] = None, capital: Optional[float] = None,
                          net_gamma: Optional[float] = None, net_vega: Optional[float] = None,
                          net_theta: Optional[float] = None,
                          next_earnings: Optional[str] = None,
                          covered: bool = False) -> dict:
    """The DEEP management read for a trade you ALREADY hold — "given I'm in, is what's
    LEFT worth the risk?" Not enter-vs-skip; hold-vs-close (STRONG_HOLD / HOLD / CLOSE /
    STRONG_CLOSE).

    Build-up (auditable, mirrors the scan's `base + Σ factors` shape):

        score = COMPUTED hold-quality base   ← _management_base: remaining reward vs
              + Σ(re-signed scan factors)      remaining risk from HERE (keep-prob,
              + Σ(slim time / gamma overlay)   premium-left×keep, Omega/Sortino, CVaR
                                               tail, cushion) — NOT a fixed 50.

    Why the base is computed, not anchored at 50: a placed trade's merit is entirely
    "risk vs reward from here", so we score that directly from the live greeks / risk-
    adjusted metrics / cushion — the same lenses the entry desk uses, re-pointed at the
    REMAINING trade. This is what lets a booked winner with lots of premium left and a
    manageable tail keep reading HOLD, while one with little left and a fat tail reads
    CLOSE — instead of a blunt "you're green → close". The old punitive profit-captured
    overlay is GONE (reward-left lives in the base); only a high-capture discipline
    backstop and the hard risk stops remain.
    """
    keep_pct = keep_drift_pct if keep_drift_pct is not None else keep_standard_pct
    base_read = _management_base(keep_pct=keep_pct, captured_pct=captured_pct,
                                 cushion_pct=cushion_pct, omega=omega, sortino=sortino,
                                 cvar95=cvar95, capital=capital)
    base = base_read["base"]
    contribs: list[dict] = []

    # TA factors — kept as-is; they already read "does the position hold?". Carry each factor's OWN evidence
    # (breach/touch %, which wall & how far, fortified, defensibility, systemic beta, …) so the hold/close
    # read EXPLAINS itself the same way the entry desk does, re-pointed at the position you're holding.
    for f in (ta_factors or []):
        label = f.get("label")
        pts = round(float(f.get("points", 0) or 0))
        # Breach risk (P-touch — the chance the short goes ITM before expiry) is THE hold metric
        # for a placed trade: always surface it, even at 0 pts (a benign breach is reassuring info).
        if pts or label == "Breach risk":
            contribs.append({"label": label, "pts": pts, "favorable": pts >= 0,
                             "note": f.get("detail") or ("supports your strike holding" if pts > 0
                                                         else "pressures your short strike" if pts < 0
                                                         else "within the comfort zone — the short is unlikely to be breached before expiry")})

    # Option-math factors — re-signed / re-weighted per the holder policy. (Tail is NOT
    # re-added here — the base's CVaR Tail lens already carries downside; adding the
    # scan's tail sub-score too would double-count it.)
    for a in (grade_adjustments or []):
        label = a.get("label")
        pol = _MGMT_FACTOR_POLICY.get(label)
        if pol is None:
            continue
        w, help_note = pol
        pts = round(float(a.get("points", 0) or 0) * w)
        if pts == 0:
            continue
        if label == "VRP":
            disp = "Vol decay" if pts > 0 else "Vol premium"
            help_note = ("your shorts are decaying and cheap to buy back" if pts > 0
                         else "rich implied still in your shorts — more premium to collect, but a vol spike would hurt")
        else:
            disp = label   # Liquidity, Moneyness, Earnings timing, Expectation keep their own names
        # The note is this trade's MEASURED value only (cushion %/σ, bid/ask width, IV vs HV, earnings
        # date) — shown inline in front of the factor name. The generic "what this factor means" help is
        # a static glossary on the frontend (the hover), NOT trade data. Fall back to the policy help
        # only when the scan carries no number for this factor.
        data = _mgmt_factor_data(label, a, cushion_pct, next_earnings)
        note = data if data else help_note
        contribs.append({"label": disp, "pts": pts, "favorable": pts > 0, "note": note})

    # Dynamic greeks — the CONVEXITY the static score misses. Short gamma tightening into
    # expiry is the real "holding cost" of a winner: small moves swing P&L hard.
    if net_gamma is not None and net_gamma < 0 and dte_days is not None and dte_days <= 21:
        pts = -min(6, round((21 - dte_days) / 21 * 6) + 1)
        contribs.append({"label": "Convexity (short Γ)", "pts": pts, "favorable": False,
                         "note": f"short gamma with {dte_days} DTE — delta flips fast near your strike; the "
                                 "dynamic risk of holding a winner into the gamma zone"})

    # Covered vs naked short call — capital is already committed, so the ADVICE differs. `covered` = the
    # user holds the shares (a covered_call structure, OR a short call explicitly MARKED covered with the
    # shares held elsewhere) → NO unbounded-upside / naked penalty; the risk is being called away, not a
    # cash loss.
    advisories: list[str] = []
    if structure == "naked_call" and not covered:
        contribs.append({"label": "Naked risk", "pts": -6, "favorable": False,
                         "note": "unbounded upside — no stock cap; size small and defend a tested strike"})
        advisories.append(
            "Naked call — upside risk is UNBOUNDED. A sharp rally loses far more than the credit; keep it small "
            "and defend (roll up · add a long-call wing · close) if the strike is threatened.")
    elif structure == "covered_call" or covered:
        advisories.append(
            "Covered call — you hold the stock (held elsewhere if you marked it covered), so the risk is being "
            "CALLED AWAY above the strike (opportunity cost), not a cash loss. To keep the shares, roll the call "
            "up/out when it's threatened; otherwise let it decay for the income.")

    factors_net = sum(c["pts"] for c in contribs)

    # SLIM overlay — only the two things that genuinely change once you're in: the expiry
    # gamma clock, and hard risk/stop overrides. NO graduated profit-captured penalty —
    # remaining reward is already priced into the base's Reward lens.
    overlay: list[dict] = []
    adj = 0.0
    if dte_days is not None:
        td = -14 if dte_days <= 2 else -7 if dte_days <= 7 else -2 if dte_days <= 21 else 3
        overlay.append({"name": "Time / gamma", "pts": td,
                        "note": (f"{dte_days} DTE — gamma/pin risk elevated" if dte_days <= 7
                                 else f"{dte_days} DTE — runway to keep collecting" if td > 0
                                 else f"{dte_days} DTE")})
        adj += td

    score = int(max(0, min(100, round(base + factors_net + adj))))
    signal = ("STRONG_HOLD" if score >= 68 else "HOLD" if score >= 45
              else "CLOSE" if score >= 28 else "STRONG_CLOSE")

    # Hard overrides — risk/discipline stops that beat the score.
    overrides: list[str] = []
    if captured_pct is not None and captured_pct >= 90:
        signal = "STRONG_CLOSE"
        overrides.append("≥90% of max profit captured — almost nothing left to earn, bank it")
    elif captured_pct is not None and captured_pct >= 75 and signal == "STRONG_HOLD":
        signal = "HOLD"
        overrides.append(f"{captured_pct:.0f}% captured — thin remaining edge, don't over-hold")
    if max_loss is not None and max_loss < 0 and unrealized_pnl is not None and unrealized_pnl <= max_loss * 0.8:
        signal = "STRONG_CLOSE"
        overrides.append("near max loss — cut it")
    if cushion_pct is not None and cushion_pct <= 0 and signal in ("STRONG_HOLD", "HOLD"):
        signal = "CLOSE"
        overrides.append("short strike tested — defend (roll) or close")
    if dte_days is not None and dte_days <= 2 and signal in ("STRONG_HOLD", "HOLD"):
        signal = "CLOSE"
        overrides.append("≤2 DTE — gamma/pin/assignment risk")

    return {
        "signal": signal, "score": clamp_score_to_signal(score, signal), "raw_score": score,
        "anchor": int(round(base)),
        "anchor_label": "hold quality · remaining risk vs reward",
        "base_lenses": base_read["lenses"],   # the 5 computed lenses behind the base
        "contributions": contribs,            # the re-signed scan + dynamic-greek factors
        "factors_net": round(factors_net, 1),
        "overlay": overlay,                   # slim time / gamma
        "overrides": overrides,
        "advisories": advisories,             # covered / naked call structural advice
        "greeks_used": {"net_gamma": net_gamma, "net_vega": net_vega, "net_theta": net_theta},
    }


def compute_pretrade_metrics(life_legs, spot, scenarios, capital, max_loss, max_profit,
                             avg_iv, dte_days, stock_shares=0.0, r=0.05, sofr_pct=5.0,
                             realized_vol=None, strategy_type="", upside_cap_pct=None,
                             atm_iv=None, rnd_grid=None, event_var=0.0, event_fallback=False) -> dict:
    """Full desk read for a PROPOSED trade: Trader Greeks + PM ratios + position
    VaR/CVaR + the algorithmic Quant recommendation. `avg_iv` (implied) is decimal (0 = unknown);
    `atm_iv` (decimal, optional) is the expiry's ATM vol (the centre); `rnd_grid` (optional) is the scan's compact
    market-implied law for this expiry — when given it is the prior every trade on the expiry shares.

    Distribution: when the legs fully describe the trade (they reproduce the caller's `scenarios`), every
    probability-weighted number comes from `_payoff_distribution` — exact buckets, each leg priced at its own
    vol (skew-consistent), so PoP / EV / Omega / Sortino / VaR / CVaR / Kelly all share ONE law. Otherwise
    (no legs, or a custom curve) the caller's scenario grid is weighted by a flat lognormal as before.

    Q-vs-P: the payoff distribution is widened when realized (P, physical) vol exceeds implied (Q,
    risk-neutral). When implied is crushed below realized (negative VRP), this stops PoP/tail/EV — and
    therefore the whole base-quality score — from reading falsely safe. `avg_iv` is still what we DISPLAY
    (the trade's true implied); only the probability WEIGHTS use the wider σ.
    """
    trader = higher_order_greeks(life_legs, spot, r, stock_shares) if life_legs else {}

    implied = avg_iv if (avg_iv and avg_iv > 0) else None
    realized = realized_vol if (realized_vol and realized_vol > 0) else None
    # The physical (P) measure widens the law whenever realized > implied — the negative-VRP trap.
    w_iv = max([v for v in (implied, realized) if v] or [0]) or 0.30
    pm = {"pop": None, "expected_value": None, "omega": None, "sortino": None,
          "calmar": None, "expected_return_pct": None, "downside_dev_pct": None}
    var95 = cvar95 = var99 = cvar99 = kelly = None
    dist = None
    if life_legs and dte_days and dte_days > 0:
        iv_fb = realized or 0.30                      # = the desk's horizon-curve iv_fallback (hv or 0.30)
        try:
            dist = _payoff_distribution(life_legs, stock_shares, spot, dte_days, r, implied, realized,
                                        atm_iv=atm_iv, iv_fallback=iv_fb, rnd_grid=rnd_grid, event_var=event_var,
                                        event_fallback=event_fallback)
            if (dist is not None and scenarios and len(scenarios) >= 3 and not _matches_curve(
                    (life_legs, stock_shares, spot, dist["horizon_T"], r, iv_fb, dist["multi_exp"]), scenarios)):
                dist = None                           # the legs don't reproduce the caller's curve → its grid
        except Exception:  # noqa: BLE001 — the exact path must never sink a desk read; fall back to the grid
            dist = None
    if dist is not None:
        w_iv = dist["weight_vol"]
        wn, pnl_b = dist["w"], dist["pnl"]                     # PHYSICAL law → expectation (the VRP in dollars)
        wr, pnl_r = dist["w_risk"], dist["pnl_risk"]           # wider of physical / market → tails
        pm = _metrics_from_pnl(pnl_b, wn, capital, max_loss, dte_days)
        var95, cvar95 = _weighted_var_cvar(pnl_r, wr, 0.05)
        var99, cvar99 = _weighted_var_cvar(pnl_r, wr, 0.01)
        if capital and capital > 0:
            kelly = _kelly_fraction(pnl_b / capital, wn)
    elif scenarios and len(scenarios) >= 3 and dte_days and dte_days > 0:
        prices = np.array([s["price"] for s in scenarios], dtype=float)
        pnls = np.array([s["pnl"] for s in scenarios], dtype=float)
        w = _lognormal_weights(prices, spot, w_iv, dte_days, r)
        pm = payoff_distribution_metrics(prices, pnls, w, capital, max_loss, dte_days)
        pnl_mid = (pnls[:-1] + pnls[1:]) / 2.0
        wn = w / w.sum() if w.sum() > 0 else w
        # VaR/CVaR at BOTH 95% and 99%. Single-expiry → a full-tail deterministic quadrature (near-0 → 3×
        # spot) so a 99% CVaR on a high-win short captures the deep crash the ±50% grid truncates.
        # Multi-expiry (calendars) keep the horizon-scenario grid (a terminal curve would misprice the back leg).
        multi_exp = len({round(float(l.get("dte_years") or 0.0), 6) for l in (life_legs or [])}) > 1
        if life_legs and not multi_exp:
            _tv = _full_tail_var_cvar(life_legs, stock_shares, spot, w_iv, dte_days, r, (0.05, 0.01))
            var95, cvar95 = _tv[0.05]
            var99, cvar99 = _tv[0.01]
        else:
            var95, cvar95 = _weighted_var_cvar(pnl_mid, wn, 0.05)
            var99, cvar99 = _weighted_var_cvar(pnl_mid, wn, 0.01)
        if capital and capital > 0:
            kelly = _kelly_fraction(pnl_mid / capital, wn)

    # A 95% tail is useless for a >95%-win short (the 5th percentile is still profit), so escalate the
    # HEADLINE tail to 99% there — and score the trade on that honest deep tail, not the benign 5% one.
    pop_val = pm.get("pop")
    tail_pctile = 99 if (pop_val is not None and pop_val > 95) else 95
    tail_cvar = cvar99 if (tail_pctile == 99 and cvar99 is not None) else cvar95
    # Anchor the recommendation to the STRATEGY's use case, not one-size-fits-all:
    #   buffer / dual-direction / hedge → defensive protected-participation (protection + odds dominate)
    #   income (premium selling)        → safe-income (keep-prob + yield-vs-cash dominate)
    #   otherwise                        → default risk-neutral blend
    _st = (strategy_type or "").lower()
    _buffer = ("buffer" in _st or "dual_direction" in _st or "hedge" in _st)
    _income = ("income" in _st) or (not _st)   # blank = the income-desk default (back-compat)
    _inc_mode = _income and not _buffer
    carry = _carry_to_risk(life_legs, cvar99, dte_days) if (_inc_mode and dist is not None) else None
    tail_cap = ((_tail_exposure(life_legs, spot, max_loss) if _TAIL_BASIS == "exposure"
                 else _tail_notional(life_legs, spot) if _TAIL_BASIS == "notional" else None) if _inc_mode else None)
    quant = algorithmic_quant(pm, tail_cvar, capital, max_loss, max_profit, kelly, dte_days, sofr_pct,
                              income_mode=_inc_mode, buffer_mode=_buffer,
                              upside_cap_pct=(upside_cap_pct if _buffer else None),
                              carry=carry, tail_capital=tail_cap)
    vrp_ratio = (implied / realized) if (implied and realized) else None   # < 1 = negative VRP
    return {
        "trader": {**trader, "avg_iv_pct": round(avg_iv * 100, 1) if (avg_iv and avg_iv > 0) else None},
        "pm": {**pm, "kelly_fraction": kelly},
        "risk": {"var_95": var95, "cvar_95": cvar95, "var_99": var99, "cvar_99": cvar99,
                 "tail_pctile": tail_pctile,
                 "max_loss": round(max_loss, 2) if max_loss is not None else None,
                 "max_profit": round(max_profit, 2) if max_profit is not None else None,
                 "capital": round(capital, 2) if capital else None},
        "quant": quant,
        # Q-vs-P read — what weighted the distribution, so the desk can SEE the trap.
        "vrp": {
            "implied_vol_pct": round(implied * 100, 1) if implied else None,
            "realized_vol_pct": round(realized * 100, 1) if realized else None,
            "weight_vol_pct": round(w_iv * 100, 1),
            "iv_hv_ratio": round(vrp_ratio, 2) if vrp_ratio else None,
            "physical_wider": bool(realized and implied and realized > implied),
        },
        # The Income lens's input — premium per $ of 1%-tail (None for debit structures / no exact law).
        "carry": ({"carry_to_risk_pct": round(carry["ctr"] * 100, 1), "per_cycle_pct": round(carry["per_cycle"] * 100, 2),
                   "credit": round(carry["credit"], 2), "cvar_99": round(carry["cvar99"], 2)} if carry else None),
        "tail_basis": _TAIL_BASIS if tail_cap else "capital",
        # Which law produced the numbers above — auditable, so a fallback is never silent.
        "distribution": ({"method": dist["method"], "measure": dist["measure"], "n_constraints": dist["n_constraints"],
                          "atm_anchor": dist["atm_anchor"], "buckets": int(len(dist["w"])),
                          "center_vol_pct": round(dist["center_vol"] * 100, 1),
                          "weight_vol_pct": round(dist["weight_vol"] * 100, 1),
                          "max_price_err": round(dist["max_price_err"], 4)}
                         if dist is not None else {"method": "scenario_grid"}),
    }
