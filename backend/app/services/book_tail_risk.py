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

# (label, index move, vol-points shock). Vol spikes as spot drops (skew).
_CRASH = [("−10%", -0.10, 8.0), ("−20%", -0.20, 15.0),
          ("COVID −34%", -0.34, 30.0), ("GFC −50%", -0.50, 45.0)]
_HEDGE_SCEN = (-0.20, 15.0)      # size hedges to the −20% book loss
_HORIZON_TD = 21                 # VaR/CVaR horizon: ~1 trading month
_MKT_VOL_FALLBACK = 0.16         # annualized S&P vol if VIX is unavailable
_TDF = 4                         # Student-t d.o.f. — fat market tails
_SKEW_K = 0.75                   # vol-points (decimal) added per unit adverse move


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
    return {"ticker": ticker, "name": strategy.name, "spot": spot, "iv": avg_iv,
            "n_short": n_short, "capital": round(capital, 0), "legs": life_legs, **g}


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
            "label": label, "long_put": long_k, "short_put": short_k if so else None,
            "contracts": n, "cost_per_spread": round(cost_per, 0), "total_cost": round(total_cost, 0),
            "annual_bleed": round(annual_bleed, 0), "crash_payoff_20": round(n * payoff20, 0),
            "offsets_pct": round(n * payoff20 / book_loss20 * 100, 0) if book_loss20 else None,
            "cvar_reduction": round(cvar_red, 0),
            "efficiency": round(cvar_red / annual_bleed, 2) if annual_bleed > 0 else None,   # $ CVaR cut per $/yr spent
            "cagr_lift_pct": spitz["cagr_lift_pct"], "cost_effective": spitz["cost_effective"],
        })
    # rank: cost-effective first, then most CVaR reduced per dollar.
    out.sort(key=lambda c: (c["cost_effective"], c["efficiency"] or -1), reverse=True)
    for i, c in enumerate(out):
        c["recommended"] = (i == 0)
    return out


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

    # #4 — hedge MENU (ranked), repriced on the MC scenarios for true CVaR reduction.
    hedge_menu, hedge_note = [], None
    if idx > 0 and book_loss20 > 0 and mc.get("pnl") is not None:
        puts, meta = await _index_puts(provider, hedge_dte_days, today)
        hdte = meta["dte"] if meta else hedge_dte_days
        pricing = "live SPX chain" if puts else "model (BS + VIX)"
        hedge_menu = _hedge_candidates(idx, mkt_vol, hdte / 365.0, r, puts, mc["pnl"], mc["idx_sim"],
                                       mc["cvar_95"] or 0.0, book_loss20, hedge_target_pct * book_loss20,
                                       crash_prob_annual, book_capital, annual_income, 365.0 / max(hdte, 1),
                                       horizon_years=mc.get("horizon_years", 0.0))
        for c in hedge_menu:
            c["index"], c["pricing"], c["dte_days"], c["expiry"] = "SPX (European)", pricing, hdte, (meta or {}).get("exp")
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
        "hedge_menu": hedge_menu, "hedge_note": hedge_note,
        "verdict": verdict,
        "assumptions": {"beta": "per-name β vs S&P 500" if beta_weighted else "β≈1 (beta data unavailable)",
                        "mkt_vol_pct": round(mkt_vol * 100, 1), "tail": f"Student-t (df={_TDF}) market factor + skew",
                        "crash_prob_annual_pct": round(crash_prob_annual * 100, 1),
                        "hedge_rolls_per_year": round(365 / hedge_dte_days, 1)},
    })
