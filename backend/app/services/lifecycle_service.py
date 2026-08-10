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
    out = {"pop": None, "expected_value": None, "omega": None, "sortino": None,
           "calmar": None, "expected_return_pct": None, "downside_dev_pct": None}
    prices = np.asarray(prices, dtype=float)
    pnls = np.asarray(pnls, dtype=float)
    w = np.asarray(weights, dtype=float)
    s = w.sum()
    if s <= 0 or len(prices) < 2 or dte_days <= 0:
        return out
    w = w / s
    pnl_mid = (pnls[:-1] + pnls[1:]) / 2.0

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
    """Weighted VaR/CVaR (positive-loss $) at the alpha tail of the terminal P&L."""
    if len(pnl_mid) == 0 or w.sum() <= 0:
        return None, None
    order = np.argsort(pnl_mid)
    p = pnl_mid[order]
    ww = w[order] / w.sum()
    cw = np.cumsum(ww)
    idx = min(int(np.searchsorted(cw, alpha)), len(p) - 1)
    var = -float(p[idx])
    mask = cw <= alpha
    if not mask.any():
        mask[0] = True
    cvar = -float(np.sum(p[mask] * ww[mask]) / ww[mask].sum())
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


def algorithmic_quant(pm: dict, cvar95: Optional[float], capital: float, max_loss,
                      max_profit, kelly, dte_days: int, sofr_pct: float = 5.0) -> dict:
    """Deterministic entry recommendation from the whole payoff distribution.

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

    s_edge = clamp(((omega or 0) - 0.8) / (2.0 - 0.8)) if omega is not None else 0.4
    s_pop = clamp(((pop or 0) - 50.0) / (90.0 - 50.0)) if pop is not None else 0.4
    s_sortino = clamp((sortino or 0) / 2.0) if sortino is not None else 0.4
    tail_frac = (cvar95 / capital) if (capital and capital > 0 and cvar95 is not None) else 0.5
    s_tail = clamp(1.0 - tail_frac / 0.40)
    hurdle = (sofr_pct / 100.0) * (dte_days / 365.0) * 100.0 if dte_days else 0.0
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
        reasons.append(f"CVaR95 {tail_frac*100:.0f}% of capital (expected shortfall, not deep tail)")
    if exp_ret is not None:
        reasons.append(f"exp. return {exp_ret:+.1f}% vs {hurdle:.1f}% hurdle")
    if kelly is not None:
        reasons.append(f"Kelly {kelly*100:.0f}%")

    if score >= 66:
        verdict, tone = "ENTER", "good"
    elif score >= 45:
        verdict, tone = "CONSIDER", "warn"
    else:
        verdict, tone = "AVOID", "bad"
    return {"verdict": verdict, "tone": tone, "score": score, "reasons": reasons,
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

    return {"signal": signal, "score": hold_score, "adjustments": adjustments, "overrides": overrides}


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
    # ── does the short strike survive? — keep, same sign ───────────────────
    "Trend drift":  (1.0,  "trend relative to your short strike"),
    "Value area":   (1.0,  "spot's location within the value area"),
    "Gamma regime": (1.0,  "dealer-gamma vol regime (suppressed = your strike holds)"),
    "Moneyness":    (1.0,  "cushion from spot to your short strike"),
    # ── vol — FLIP, but GENTLY: cheap/falling implied is a clear holder positive
    # (decaying, cheap to buy back). Rich remaining implied is NOT a pure demerit the
    # way the entry score treats it — it's also premium still to collect; only the
    # spike RISK is a negative, so we damp the flip (−0.6 → −0.3) and the base's
    # Reward/Tail lenses carry the rest. (This is the "penalised for lower vol" fix.)
    "VRP":          (-0.3, "cheap/falling implied is GOOD once you're short (decaying, cheap to buy back); rich implied is mostly still-collectable premium, only a spike hurts"),
    # ── exit mechanics — reframed, downweighted ────────────────────────────
    "Liquidity":    (0.5,  "bid/ask width is the cost to CLOSE now, not to enter"),
    # ── expected value — a loss at high keep-prob is a remote tail ──────────
    "Expectation":  (0.4,  "a losing expectation here is the remote tail, not the base case"),
    # ── entry-only, irrelevant once held ───────────────────────────────────
    "Skew":         (0.0,  ""),
    "Beta":         (0.0,  ""),
}


def management_desk_score(*, keep_drift_pct: Optional[float], keep_standard_pct: Optional[float],
                          subscores: Optional[dict], grade_adjustments: Optional[list],
                          ta_factors: Optional[list], captured_pct: Optional[float],
                          dte_days: Optional[int], unrealized_pnl: Optional[float] = None,
                          max_profit=None, max_loss=None, cushion_pct: Optional[float] = None,
                          structure: Optional[str] = None,
                          omega: Optional[float] = None, sortino: Optional[float] = None,
                          cvar95: Optional[float] = None, capital: Optional[float] = None,
                          net_gamma: Optional[float] = None, net_vega: Optional[float] = None,
                          net_theta: Optional[float] = None) -> dict:
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

    # TA factors — kept as-is; they already read "does the position hold?".
    for f in (ta_factors or []):
        pts = round(float(f.get("points", 0) or 0))
        if pts:
            contribs.append({"label": f.get("label"), "pts": pts, "favorable": pts > 0,
                             "note": "supports your strike holding" if pts > 0 else "pressures your short strike"})

    # Option-math factors — re-signed / re-weighted per the holder policy. (Tail is NOT
    # re-added here — the base's CVaR Tail lens already carries downside; adding the
    # scan's tail sub-score too would double-count it.)
    for a in (grade_adjustments or []):
        label = a.get("label")
        pol = _MGMT_FACTOR_POLICY.get(label)
        if pol is None:
            continue
        w, note = pol
        pts = round(float(a.get("points", 0) or 0) * w)
        if pts == 0:
            continue
        if label == "VRP":
            disp = "Vol decay" if pts > 0 else "Vol premium"
            note = ("cheap / falling implied vol — your shorts are decaying and cheap to buy back" if pts > 0
                    else "rich implied still in your shorts — more premium to collect, but a vol spike would hurt")
        elif label == "Liquidity":
            disp = "Exit cost"
        else:
            disp = label
        contribs.append({"label": disp, "pts": pts, "favorable": pts > 0, "note": note})

    # Dynamic greeks — the CONVEXITY the static score misses. Short gamma tightening into
    # expiry is the real "holding cost" of a winner: small moves swing P&L hard.
    if net_gamma is not None and net_gamma < 0 and dte_days is not None and dte_days <= 21:
        pts = -min(6, round((21 - dte_days) / 21 * 6) + 1)
        contribs.append({"label": "Convexity (short Γ)", "pts": pts, "favorable": False,
                         "note": f"short gamma with {dte_days} DTE — delta flips fast near your strike; the "
                                 "dynamic risk of holding a winner into the gamma zone"})

    # Covered vs naked short call — capital is already committed, so the ADVICE differs.
    advisories: list[str] = []
    if structure == "naked_call":
        contribs.append({"label": "Naked risk", "pts": -6, "favorable": False,
                         "note": "unbounded upside — no stock cap; size small and defend a tested strike"})
        advisories.append(
            "Naked call — upside risk is UNBOUNDED. A sharp rally loses far more than the credit; keep it small "
            "and defend (roll up · add a long-call wing · close) if the strike is threatened.")
    elif structure == "covered_call":
        advisories.append(
            "Covered call — you hold the stock, so the risk is being CALLED AWAY above the strike (opportunity "
            "cost), not a cash loss. To keep the shares, roll the call up/out when it's threatened; otherwise let "
            "it decay for the income.")

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
        "signal": signal, "score": score,
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
                             realized_vol=None) -> dict:
    """Full desk read for a PROPOSED trade: Trader Greeks + PM ratios + position
    VaR/CVaR + the algorithmic Quant recommendation. `avg_iv` (implied) is decimal (0 = unknown).

    Q-vs-P: the payoff distribution is weighted by the WIDER of implied (Q, risk-neutral) and
    realized (P, physical) vol. When implied is crushed below realized (negative VRP), this stops
    PoP/tail/EV — and therefore the whole base-quality score — from reading falsely safe. `avg_iv`
    is still what we DISPLAY (the trade's true implied); only the probability WEIGHTS use the wider σ.
    """
    trader = higher_order_greeks(life_legs, spot, r, stock_shares) if life_legs else {}

    implied = avg_iv if (avg_iv and avg_iv > 0) else None
    realized = realized_vol if (realized_vol and realized_vol > 0) else None
    # The physical (P) measure widens the law whenever realized > implied — the negative-VRP trap.
    w_iv = max([v for v in (implied, realized) if v] or [0]) or 0.30
    pm = {"pop": None, "expected_value": None, "omega": None, "sortino": None,
          "calmar": None, "expected_return_pct": None, "downside_dev_pct": None}
    var95 = cvar95 = var99 = cvar99 = kelly = None
    if scenarios and len(scenarios) >= 3 and dte_days and dte_days > 0:
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
    quant = algorithmic_quant(pm, tail_cvar, capital, max_loss, max_profit, kelly, dte_days, sofr_pct)
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
    }
