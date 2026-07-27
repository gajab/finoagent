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

from .stock_service import bs_delta, bs_gamma, bs_vega, bs_theta


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
        STRONG_HOLD ≥ 70 · HOLD ≥ 45 · CONSIDER_CLOSE ≥ 25 · CLOSE < 25
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
        STRONG_HOLD ≥ 70 · HOLD ≥ 45 · CONSIDER_CLOSE ≥ 25 · CLOSE
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
    signal = ("STRONG_HOLD" if hold_score >= 70 else "HOLD" if hold_score >= 45
              else "CONSIDER_CLOSE" if hold_score >= 25 else "CLOSE")

    overrides: list[str] = []
    if captured_pct is not None and captured_pct >= 85:
        signal = "CLOSE"
        overrides.append("≥85% of max profit captured — bank it")
    if max_loss is not None and max_loss < 0 and unrealized_pnl is not None and unrealized_pnl <= max_loss * 0.8:
        signal = "CLOSE"
        overrides.append("near max loss — cut it")
    if dte_days is not None and dte_days <= 2 and signal in ("STRONG_HOLD", "HOLD"):
        signal = "CONSIDER_CLOSE"
        overrides.append("≤2 DTE — gamma/pin/assignment risk")

    return {"signal": signal, "score": hold_score, "adjustments": adjustments, "overrides": overrides}


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
    var95 = cvar95 = kelly = None
    if scenarios and len(scenarios) >= 3 and dte_days and dte_days > 0:
        prices = np.array([s["price"] for s in scenarios], dtype=float)
        pnls = np.array([s["pnl"] for s in scenarios], dtype=float)
        w = _lognormal_weights(prices, spot, w_iv, dte_days, r)
        pm = payoff_distribution_metrics(prices, pnls, w, capital, max_loss, dte_days)
        pnl_mid = (pnls[:-1] + pnls[1:]) / 2.0
        wn = w / w.sum() if w.sum() > 0 else w
        var95, cvar95 = _weighted_var_cvar(pnl_mid, wn)
        if capital and capital > 0:
            kelly = _kelly_fraction(pnl_mid / capital, wn)

    quant = algorithmic_quant(pm, cvar95, capital, max_loss, max_profit, kelly, dte_days, sofr_pct)
    vrp_ratio = (implied / realized) if (implied and realized) else None   # < 1 = negative VRP
    return {
        "trader": {**trader, "avg_iv_pct": round(avg_iv * 100, 1) if (avg_iv and avg_iv > 0) else None},
        "pm": {**pm, "kelly_fraction": kelly},
        "risk": {"var_95": var95, "cvar_95": cvar95,
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
