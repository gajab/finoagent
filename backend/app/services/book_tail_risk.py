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

def reprice_scenario(positions: list[dict], move: float, vol_shock_pts: float, r: float) -> float:
    """Book P&L if the market instantly moves `move` (each name × its beta) and vol
    jumps `vol_shock_pts`, by fully repricing every leg with Black-Scholes."""
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
    idx_sim = idx_spot * np.exp(mkt)
    return {"pnl": pnl, "idx_sim": idx_sim, "horizon_years": dt, **_tail_stats(pnl)}


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
    for l in opt:
        exp = str(l.get("expiration") or l.get("expiry") or "")[:10]
        if not exp:
            continue
        right = "C" if "call" in str(l.get("type", "")).lower() else "P"
        sign = -1 if any(k in str(l.get("action", "")).upper() for k in ("SELL", "SHORT")) else 1
        strike = float(l.get("strike") or 0)
        if sign < 0:
            n_short += 1
            short_strikes.append(strike)
        key = (ticker, exp)
        if key not in chain_cache:
            try:
                chain_cache[key] = await provider.get_option_chain(ticker, exp)
            except Exception:  # noqa: BLE001
                chain_cache[key] = None
        iv, chain = None, chain_cache[key]
        if chain is not None:
            try:
                calls, puts = _split_chain(chain)
                side = calls if right == "C" else puts
                q = next((qq for k, qq in side.items() if abs(float(k) - strike) < 0.01), None)
                if q and getattr(q, "iv", None):
                    iv = float(q.iv)
            except Exception:  # noqa: BLE001
                iv = None
        if iv is None:
            raw = l.get("iv")
            iv = (float(raw) / 100.0 if raw and float(raw) > 3 else float(raw)) if raw else 0.30
        try:
            dte = max(1, (date.fromisoformat(exp) - today).days)
        except (ValueError, TypeError):
            dte = 30
        life_legs.append({"strike": strike, "right": right, "sign": sign,
                          "qty": float(l.get("qty") or l.get("contracts") or 1),
                          "iv": iv, "dte_years": dte / 365.0})
    if not life_legs:
        return None
    g = higher_order_greeks(life_legs, spot, r=r, stock_shares=0.0)   # OPTION LAYER ONLY
    avg_iv = sum(x["iv"] for x in life_legs) / len(life_legs)
    capital = sum(sk * _MULT for sk in short_strikes) or abs(g["net_delta"]) * spot
    # structure + stock presence → so a short CALL can be flagged COVERED (excluded from the
    # naked-assignment total) vs naked. Any non-option long-share leg also counts as cover.
    structure = getattr(strategy, "strategy_type", None) or getattr(strategy, "structure", None)
    has_stock = any(("stock" in str(l.get("type", "")).lower() or "share" in str(l.get("type", "")).lower())
                    and float(l.get("shares") or l.get("qty") or 0) > 0 for l in legs)
    return {"ticker": ticker, "name": strategy.name, "spot": spot, "iv": avg_iv,
            "n_short": n_short, "capital": round(capital, 0), "legs": life_legs,
            "structure": structure, "has_stock": bool(has_stock), **g}


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
        })
    return out


def _rank_hedges(out: list[dict]) -> list[dict]:
    """Rank a MIXED hedge menu (SPX puts + VIX calls) together: cost-effective first, then
    most book-CVaR reduced per $/yr spent. Flags the single best as recommended."""
    out.sort(key=lambda c: (c.get("cost_effective", False), c.get("efficiency") or -1), reverse=True)
    for i, c in enumerate(out):
        c["recommended"] = (i == 0)
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
    }]


def _verdict_and_actions(*, crash20_pct, short_vol, concentration, hedge_menu, cvar_pct, beta_delta_spy):
    """Plain-language read + concrete actions for a retail holder."""
    loss = abs(crash20_pct or 0)
    if loss >= 40:
        level, msg = "Dangerous", f"A −20% market month wipes ~{loss:.0f}% of your capital. This book is over-sized for its tail."
    elif loss >= 20:
        level, msg = "Elevated", f"A −20% market month costs ~{loss:.0f}% of capital — heavy for a premium-selling book."
    elif loss >= 8:
        level, msg = "Moderate", f"A −20% month costs ~{loss:.0f}% of capital — manageable but real."
    else:
        level, msg = "Contained", f"A −20% month costs ~{loss:.0f}% of capital — a well-sized tail."
    actions = []
    laddered = [c for c in concentration if c.get("laddered") or c.get("flags")]
    if laddered:
        names = ", ".join(c["ticker"] for c in laddered[:3])
        actions.append(f"De-concentrate {names}: laddered short strikes on one name are ONE bet — a single gap hits them all.")
    if short_vol and loss >= 15:
        actions.append("Reduce size or convert the biggest naked shorts to defined-risk spreads to cap the tail.")
    rec = next((c for c in hedge_menu if c.get("recommended")), None)
    if rec and rec.get("cost_effective"):
        actions.append(f"Add the recommended hedge ({rec['label']}, {rec['contracts']}×) — it lifts compound growth (+{rec['cagr_lift_pct']}% CAGR) while capping the crash.")
    elif rec:
        actions.append(f"A tail hedge here is pure insurance (it lowers CAGR by {abs(rec['cagr_lift_pct'])}%/yr) — only add it if avoiding the drawdown matters more than yield.")
    if abs(beta_delta_spy or 0) > 0:
        actions.append(f"Net directional exposure ≈ {beta_delta_spy:+.0f} SPY-share-equivalents — neutralize with the index if you want it market-flat.")
    if not actions:
        actions.append("Book is balanced — keep managing winners early (≈50%) and defending tested strikes.")
    return {"level": level, "summary": msg, "actions": actions}


def _vol_shock_for(mv: float) -> float:
    """Vol-point shock for a market move: vol SPIKES on the downside (skew), CRUSHES mildly
    on a melt-up. Consistent with the crash table's skew constant."""
    return -_SKEW_K * 100.0 * mv if mv < 0 else -0.30 * 100.0 * mv


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
        puts_itm = calls_itm = 0
        for p in positions:
            s = p["spot"] * (1 + mv * p.get("beta", 1.0))     # β-adjusted per name
            for lg in p["legs"]:
                if lg["sign"] >= 0:                            # only SHORT legs get assigned
                    continue
                K, qty = lg["strike"], lg["qty"]
                if lg["right"] == "P" and s < K:
                    put_cap += K * _MULT * qty                 # cash to buy the assigned shares
                    puts_itm += 1
                elif lg["right"] == "C" and s > K:
                    call_cost += (s - K) * _MULT * qty         # intrinsic to deliver / cover
                    calls_itm += 1
        pnl = reprice_scenario(positions, mv, _vol_shock_for(mv), r)
        out.append({
            "move_pct": round(mv * 100, 0),
            "pnl": round(pnl, 0),
            "put_assignment_capital": round(put_cap, 0),
            "call_cover_cost": round(call_cost, 0),
            "puts_itm": puts_itm, "calls_itm": calls_itm,
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
        legs = p.get("legs", [])
        covered = (p.get("structure") == "covered_call") or p.get("has_stock")
        has_long_put = any(lg["right"] == "P" and lg["sign"] > 0 for lg in legs)
        has_long_call = any(lg["right"] == "C" and lg["sign"] > 0 for lg in legs)
        for lg in legs:
            if lg["sign"] >= 0:
                continue
            notional = lg["strike"] * _MULT * lg["qty"]
            if lg["right"] == "P" and not has_long_put:        # naked/cash-secured short put
                put_cap += notional
                n_puts += 1
            elif lg["right"] == "C" and not covered and not has_long_call:   # naked short call
                call_cap += notional
                n_calls += 1
    return {
        "put_capital": round(put_cap, 0), "call_capital": round(call_cap, 0),
        "total": round(put_cap + call_cap, 0),
        "n_naked_puts": n_puts, "n_naked_calls": n_calls,
    }


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

    spot_cache, chain_cache, positions = {}, {}, []
    for s in strategies:
        try:
            p = await _position_greeks(s, provider, r, today, spot_cache, chain_cache)
        except Exception as exc:  # noqa: BLE001
            logger.debug("book greeks failed for %s: %s", getattr(s, "id", "?"), exc)
            p = None
        if p:
            positions.append(p)
    if not positions:
        return {"positions": 0, "error": "No option positions with live quotes to aggregate."}

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

    # #2 — FULL-REPRICE crash scenarios.
    scenarios = []
    for label, move, vshock in _CRASH:
        pnl = reprice_scenario(positions, move, vshock, r)
        scenarios.append({"label": label, "move_pct": move, "pnl": round(pnl, 0),
                          "pct_of_capital": round(pnl / book_capital * 100, 1) if book_capital else None})
    crash20_pct = next((s["pct_of_capital"] for s in scenarios if s["label"] == "−20%"), None)

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
    hedge_menu, hedge_note = [], None
    if idx > 0 and book_loss20 > 0 and mc.get("pnl") is not None:
        target = hedge_target_pct * book_loss20
        puts, meta = await _index_puts(provider, hedge_dte_days, today)
        hdte = meta["dte"] if meta else hedge_dte_days
        spx = _hedge_candidates(idx, mkt_vol, hdte / 365.0, r, puts, mc["pnl"], mc["idx_sim"],
                                mc["cvar_95"] or 0.0, book_loss20, target,
                                crash_prob_annual, book_capital, annual_income, 365.0 / max(hdte, 1),
                                horizon_years=mc.get("horizon_years", 0.0))
        for c in spx:
            c["index"], c["pricing"], c["dte_days"], c["expiry"] = \
                "SPX (European)", ("live SPX chain" if puts else "model (BS + VIX)"), hdte, (meta or {}).get("exp")

        # VIX call-spread overlay — real ^VIX chain + a forward-vol base.
        vcalls, vmeta = await _vix_calls(provider, hedge_dte_days, today)
        spot_vix, fwd_vix = await asyncio.to_thread(_vix_levels)
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

        hedge_menu = _rank_hedges(spx + vix_cands + vixy_cands)
        if not vix_cands:
            hedge_note = "VIX black-swan overlay unavailable (no live ^VIX chain) — showing SPX put hedges only."
    elif book_loss20 <= 0:
        hedge_note = "The book isn't net short the tail at −20% — no index hedge needed."

    verdict = _verdict_and_actions(crash20_pct=crash20_pct, short_vol=net_gamma < 0,
                                   concentration=concentration, hedge_menu=hedge_menu,
                                   cvar_pct=(mc.get("cvar_95") or 0) / book_capital * 100 if book_capital else None,
                                   beta_delta_spy=beta_delta_spy)

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
        "hedge_menu": hedge_menu, "hedge_note": hedge_note,
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
