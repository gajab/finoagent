"""Ticker-level Desk Review — rank every candidate income trade the orchestrator found
and (on demand) run a Quant → Risk → PM LLM cascade to recommend the single best one.

Orchestration only, over existing pieces:
  • `derivative_income_service.run_derivative_income` — all candidate strategies + legs.
  • `stock_service.compute_technical_block` — the FULL technical read (institutional
    regime / smart-money zones / volume profile + RSI / MACD / MAs).
  • `lifecycle_service.compute_pretrade_metrics` — Trader Greeks (Δ/Γ/ν/Θ/Vanna/Charm/Volga)
    + PM ratios (Omega/Sortino/Calmar/PoP/EV/Kelly) + Risk VaR/CVaR + the algorithmic Quant
    0–100 score, driven per opportunity from its legs + a synthesized payoff curve.

Deterministic ranking (`rank_desk`) is instant/no-LLM; the LLM cascade (`run_desk_agents`)
runs only on demand. Each agent's exact input context + system prompt is returned so the UI
can expose them for triage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import date, timedelta
from typing import Optional, TYPE_CHECKING

from .lifecycle_service import compute_pretrade_metrics, terminal_payoff_curve, horizon_payoff_curve
from .llm_service import call_llm
from .derivative_income_service import (
    run_derivative_income, _norm_ticker, _macro_events_in_window, _reports_earnings,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from ..models import User

logger = logging.getLogger(__name__)

# net_expected_return_vs_sofr_bps is a RISK-NEUTRAL EV vs SOFR — slightly negative is NORMAL (fair pricing).
# Only a MATERIALLY negative value signals a structural break (toxic liquidity/skew) that no VRP can save.
_MATERIAL_FAIL_BPS = -75

# short_strike_iv_premium_over_atm_bps regimes (vol-pts × 100). Normal structural skew is a few vol pts;
# EXTREME skew (>15 vol pts) usually means the market is pricing a KNOWN tail event — challenge it, don't
# bank it as free edge ("picking up pennies in front of a steamroller").
_SKEW_ELEVATED_BPS = 700
_SKEW_EXTREME_BPS = 1500          # short-strike IV ≥ 15 vol-pts over ATM → EXTREME (double-edged; the market may price a known tail)
_SKEW_RICH_BPS = 300             # ≥ 3 vol-pts over ATM → a RICH wing worth harvesting (you're paid extra for the skew)

# Volatility Risk Premium (implied ÷ realized). ≥1 is favourable (implied over-priced = a seller's edge).
# Below 1 = negative VRP: penalise IN PROPORTION to the gap, and HARD-BLOCK past the ratio floor — that is
# the "crushed implied vol vs a stock that actually moves" trap where premium selling has no edge.
_VRP_BLOCK_RATIO = 0.70          # implied < 70% of realized → VETO the trade
_VRP_PENALTY_K = 22              # points per 1.0 of (1 − IV/HV): 0.9→−2, 0.5→−11, 0.2→−18
_VRP_PENALTY_CAP = 25

# Trend drift (P-measure DRIFT half) — the annualized EMA-slope μ becomes the SINGLE continuous
# "Trend drift" factor (retiring the old ±6/±5 yes/no regime-fit). Points scale with the trend's
# actual angle: a tailwind for the short side adds, a headwind subtracts. MACD *acceleration* is a
# SEPARATE timing veto (velocity vs acceleration): if momentum is actively accelerating against the
# short side past the threshold, the trade is vetoed regardless of the drift level.
_DRIFT_SCALE = 15                # points per 1.0 of annualized μ (μ 0.30→+4.5, 0.53→cap 8)
_DRIFT_CAP = 8
# Annualizing a ~1-month EMA slope can spit out an extreme, un-persistent %/yr. Momentum is
# a NOISY, mean-reverting predictor, so clamp the drift to a credible band before it feeds the
# display AND the drift-adjusted keep-prob (μ·T). ±60%/yr leaves genuine trends untouched
# (a −42%/yr reading is preserved) and only trims the noise-driven tails.
_DRIFT_MU_CAP = 0.60
_MACD_ACCEL_VETO = 0.004         # |Δhistogram over ~3 sessions| ÷ spot beyond this = accelerating counter-trend

_STOCK_STRUCTURES = {"covered_call"}               # hold 100 shares/contract
_BULLISH_INCOME = {"cash_secured_put", "put_credit_spread", "jade_lizard"}
_BEARISH_INCOME = {"call_credit_spread"}
_NEUTRAL_INCOME = {"iron_condor", "jade_lizard", "short_strangle"}


def _fin(x) -> Optional[float]:
    """float(x) if it is finite, else None.

    yfinance leaves NaN in openInterest (and occasionally IV) for illiquid contracts, and NaN
    defeats every ordinary guard: `nan or 0` is nan (NaN is truthy) and `nan <= 0` is False, so a
    bad row slips past both the fallback and the filter and poisons whatever it is summed into.
    None (not 0.0) is the miss value on purpose — a NaN total must read as "unavailable", never
    as a real zero."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ---------------------------------------------------------------------------
# Per-opportunity desk metrics (reuse the lifecycle engine)
# ---------------------------------------------------------------------------

def _opp_legs(opp: dict) -> list[dict]:
    """Combined leg dicts usable by BOTH the payoff curves and higher_order_greeks.

    When the legs span MULTIPLE expirations (a calendar / diagonal) each leg carries its
    OWN dte_years — so per-leg greeks and the horizon payoff are correct. Single-expiry
    structures keep the opp-level dte for every leg (identical to the prior behavior)."""
    opp_dte = max(int(opp.get("dte") or 0), 1)
    raw = opp.get("legs", [])
    multi_exp = len({l.get("expiration") for l in raw if l.get("expiration")}) > 1
    today = date.today()

    def _dte_years(exp) -> float:
        if multi_exp and exp:
            try:
                return max((date.fromisoformat(str(exp)) - today).days, 1) / 365.0
            except ValueError:
                pass
        return opp_dte / 365.0

    contracts = int(opp.get("contracts") or 1)
    legs = []
    for l in raw:
        right = "C" if str(l.get("type", "")).upper().startswith("C") else "P"
        sign = 1 if str(l.get("action", "")).upper().startswith("B") else -1
        legs.append({
            "strike": l.get("strike"), "right": right, "sign": sign,
            "qty": contracts, "iv": l.get("iv"), "dte_years": _dte_years(l.get("expiration")),
            "price": l.get("mid"),
        })
    return legs


def _robust_iv(opp: dict, hv: Optional[float]) -> float:
    """A sane implied-vol for the position's payoff distribution + displayed greeks IV. Prefer the trade's
    OWN leg IVs — the vol of the STRIKES actually traded — over the ATM smile IV: for a skewed deep-OTM put
    the strike IV (e.g. 42%) is the real number, and using the ATM (e.g. 21%) understates the tail AND
    mis-displays the greeks' IV. The ATM is only a FALLBACK for when yfinance leg IVs are garbage (near-zero
    on OTM strikes with the market closed) — the >3% filter already rejects that case. Then realized, then 30%."""
    atm = (opp.get("atm_iv_pct") or 0) / 100.0
    leg_ivs = [(l.get("iv") or 0) / 100.0 for l in opp.get("legs", []) if (l.get("iv") or 0) > 3.0]
    leg_avg = (sum(leg_ivs) / len(leg_ivs)) if leg_ivs else 0.0
    for v in (leg_avg, atm, hv):                          # LEG IV first — the strikes actually traded
        if v and 0.05 <= v <= 3.0:
            return v
    return 0.30


def _opp_desk_metrics(opp: dict, spot: float, sofr_pct: float, hv: Optional[float] = None,
                      overwrite: bool = False) -> dict:
    """Full desk read (trader/pm/risk/quant) for one income opportunity. `overwrite`=True means the
    user ALREADY holds the shares (a covered-call OVERWRITE): score the INCREMENTAL short-call overlay
    (stock_shares=0) rather than a fresh buy-write — the stock leg is a sunk position, so the only new
    risk is capping upside. The stock notional stays the yield denominator (return = yield on held stock)."""
    legs = _opp_legs(opp)
    contracts = int(opp.get("contracts") or 1)
    # An evaluate trade carries its exact signed shares (collars / stock-bearing combos); otherwise
    # infer the buy-write's 100 sh/contract from the structure. Overwrite zeroes the (already-held) stock.
    if opp.get("stock_shares") is not None:
        stock_shares = 0.0 if overwrite else float(opp.get("stock_shares") or 0.0)
    else:
        stock_shares = 100.0 * contracts if (opp.get("structure") in _STOCK_STRUCTURES and not overwrite) else 0.0

    ml = opp.get("max_loss")
    mp = opp.get("max_profit")
    # On an overwrite the "loss" above the strike is OPPORTUNITY cost, not cash — capital is the held
    # stock notional (the position being overwritten), not a fresh buy-write max loss.
    # Capital = the trade's EXPLICIT basis (Reg-T BPR for naked shorts, spread width for defined risk, stock
    # notional for covered / overwrite) — NOT abs(max_loss). So a naked short's small MARGIN (BPR) is the
    # yield/return denominator, while max_loss & VaR still carry the full notional dollar risk.
    capital = float(opp.get("collateral") or 0.0) or (abs(ml) if ml is not None else 0.0)
    # Calendars / diagonals: legs expire at different times, so an intrinsic-only terminal curve
    # would misprice a still-alive leg. Use a HORIZON curve at the nearest expiry (BS-marking the
    # longer-dated legs); single-expiry structures keep the exact terminal curve.
    if len({round(float(l.get("dte_years") or 0.0), 6) for l in legs}) > 1:
        horizon = min((float(l.get("dte_years") or 0.0) for l in legs), default=0.0)
        scenarios = horizon_payoff_curve(legs, stock_shares, spot, horizon, iv_fallback=(hv or 0.30))
    else:
        scenarios = terminal_payoff_curve(legs, stock_shares, spot)
    avg_iv = _robust_iv(opp, hv)

    return compute_pretrade_metrics(
        legs, spot, scenarios, capital,
        (None if overwrite else (-abs(ml) if ml is not None else None)), mp,
        avg_iv, int(opp.get("dte") or 0),
        stock_shares=stock_shares, sofr_pct=sofr_pct,
        realized_vol=hv,            # P-measure — widens the payoff law when realized > implied
    )


def _ta_alignment(opp: dict, ta: dict, gex: Optional[dict] = None,
                  spot: Optional[float] = None) -> tuple[float, str, list]:
    """Does this trade fit the regime / smart-money structure? Evaluated on the MEDIUM-TERM read
    (6-month history, DAILY bars) — the swing horizon that governs a multi-week income option, not
    intraday noise or multi-year lag. Returns (score_bonus ±, note, factors) where `factors` is the
    itemized [{label, points}] breakdown of how the technicals moved the score."""
    inst = (ta or {}).get("institutional") or {}
    reg = inst.get("regime") or {}
    mode = reg.get("mode")
    s = opp.get("structure")
    vp = inst.get("volume_profile") or {}
    factors: list[dict] = []
    notes: list[str] = []

    def add(label: str, pts: float, note: str):
        # ``detail`` = the concrete price-point evidence, surfaced per-factor in the UI so the user sees
        # WHY the factor scored (which support/wall/value-area level, GEX value, node volume, etc.).
        factors.append({"label": label, "points": pts, "detail": note}); notes.append(note)

    # Trend drift (μ) — the continuous EMA-slope angle REPLACES the old yes/no ±6/±5 regime-fit. A trend
    # that helps the short side is a tailwind (+), one that threatens it a headwind (−); neutral structures
    # dislike ANY strong drift. Points scale with the actual angle (severity), not a binary regime label.
    mu = (ta or {}).get("_drift_mu")
    if mu is not None:
        if s in _BULLISH_INCOME:
            aligned = mu                 # up-trend keeps short puts OTM
        elif s in _BEARISH_INCOME:
            aligned = -mu                # down-trend keeps short calls OTM
        elif s in _NEUTRAL_INCOME:
            aligned = -abs(mu)           # any strong trend threatens one wing
        else:
            aligned = 0.0
        pts = max(-_DRIFT_CAP, min(_DRIFT_CAP, round(aligned * _DRIFT_SCALE)))
        if pts != 0:
            _dte = int(opp.get("dte") or 0)
            _hz = f" ≈ {'+' if mu > 0 else ''}{round(mu * _dte / 365 * 100, 1)}% over {_dte}d" if _dte else ""
            add("Trend drift", pts, f"trend velocity {round(mu * 100)}%/yr (annualized EMA slope){_hz} — {'tailwind' if pts > 0 else 'headwind'} for this structure")
    if mode == "range" and s in _NEUTRAL_INCOME:
        _va = f", value area ${round(vp['val'], 1)}–${round(vp['vah'], 1)}" if vp.get("val") and vp.get("vah") else ""
        _poc = f"POC ${round(vp['poc'], 1)}" if vp.get("poc") else "range-bound tape"
        add("Range fit", 5, f"{_poc}{_va} — neutral premium suits the range")
    # Short strike protected by a value-area edge on the safe side.
    ss = opp.get("short_strike")
    if ss and vp.get("val") and vp.get("vah"):
        _va = f"value area ${round(vp['val'], 1)}–${round(vp['vah'], 1)}"
        if s in _BULLISH_INCOME and ss <= vp["val"]:
            add("Value area", 3, f"short strike ${round(ss, 1)} below the value-area low ${round(vp['val'], 1)} ({_va})")
        elif s in _BEARISH_INCOME and ss >= vp["vah"]:
            add("Value area", 3, f"short strike ${round(ss, 1)} above the value-area high ${round(vp['vah'], 1)} ({_va})")
    # Short strike DEFENDED by a strong structural level on the safe side — the pro "sell BEYOND the level"
    # placement: price must break the level before it can reach your strike. Levels = classical S/R + dealer
    # gamma walls / flip (Put-Support, Call-Resistance, gamma flip, HVL); value-area edges are scored above,
    # so they're excluded here to avoid double-counting the same price. Scaled by how CLOSE the defending
    # level sits to the strike (a level right at the strike is the operative defense; far off = weaker).
    put_short = opp.get("put_short") or (ss if s in ("cash_secured_put", "put_credit_spread") else None)
    call_short = opp.get("call_short") or (ss if s in ("covered_call", "call_credit_spread") else None)
    named_levels = [(nm, float(v)) for nm, v in (           # (human name, price) — so the note NAMES the level
        ("support", (ta or {}).get("supportLevel")), ("resistance", (ta or {}).get("resistanceLevel")),
        ("gamma put-wall", (gex or {}).get("put_support")), ("gamma call-wall", (gex or {}).get("call_resistance")),
        ("gamma flip", (gex or {}).get("flip_level")), ("high-volume level", (gex or {}).get("hvl")),
    ) if v and float(v) > 0]
    struct_pts, struct_notes = 0.0, []
    if put_short and spot:                               # a level ABOVE the put & below spot must break first
        defs = [(nm, L) for nm, L in named_levels if put_short <= L < spot]
        if defs:
            nm, lvl = min(defs, key=lambda t: t[1])      # nearest defending level above the strike
            prox = 1.0 - min((lvl - put_short) / max(spot - put_short, 1e-6), 1.0)   # 1 = level right at the strike
            struct_pts += round(2 + 2 * prox, 1); struct_notes.append(f"short put ${round(put_short, 1)} sits below {nm} ${round(lvl, 1)}")
    if call_short and spot:                              # a level BELOW the call & above spot must break first
        defs = [(nm, L) for nm, L in named_levels if spot < L <= call_short]
        if defs:
            nm, lvl = max(defs, key=lambda t: t[1])      # nearest defending level below the strike
            prox = 1.0 - min((call_short - lvl) / max(call_short - spot, 1e-6), 1.0)
            struct_pts += round(2 + 2 * prox, 1); struct_notes.append(f"short call ${round(call_short, 1)} sits above {nm} ${round(lvl, 1)}")
    if struct_pts:   # ONE combined factor (both wings of a neutral structure sum here), capped so it can't dominate
        add("Structure", round(min(struct_pts, 6.0), 1), "; ".join(struct_notes) + " — the level must break before the strike is threatened")
    # LVN slip-through — a short strike in a thin volume node has no absorption (Phase-3 friction test).
    lvn = _lvn_check(opp, vp, spot)
    if lvn:
        add(lvn["label"], lvn["points"], lvn["note"])
    # Dealer gamma regime (GEX proxy) — long gamma suppresses vol (a good backdrop for selling premium);
    # short gamma exacerbates it (dangerous). A STOCK-level positioning read applied to every trade.
    if gex and gex.get("regime") in ("long", "short"):
        _lg = gex["regime"] == "long"
        _bits = []
        if gex.get("gex_bn") is not None:
            _bits.append(f"net GEX {gex['gex_bn']:+g}bn")
        if gex.get("flip_level"):
            _bits.append(f"flip ${round(gex['flip_level'], 1)}")
        if gex.get("put_support"):
            _bits.append(f"put-wall ${round(gex['put_support'], 1)}")
        if gex.get("call_resistance"):
            _bits.append(f"call-wall ${round(gex['call_resistance'], 1)}")
        _ev = f" ({', '.join(_bits)})" if _bits else ""
        add("Gamma regime", 4 if _lg else -6,
            f"dealers {'LONG' if _lg else 'SHORT'} gamma{_ev} — "
            f"{'vol-suppressed / mean-reverting (good backdrop for selling premium)' if _lg else 'vol-EXPANSION / trending (dealers chase moves — dangerous)'}")
    bonus = sum(f["points"] for f in factors)
    return bonus, ", ".join(notes), factors


# ---------------------------------------------------------------------------
# TA context helpers
# ---------------------------------------------------------------------------

def _ta_sync(ticker: str) -> dict:
    try:
        import yfinance as yf
        from .stock_service import compute_technical_block, compute_momentum_indicators
        stock = yf.Ticker(_norm_ticker(ticker))
        ta = compute_technical_block(stock, "medium_term") or {}
        try:
            ta.update(compute_momentum_indicators(stock))   # MACD / Bollinger / SMA-EMA (daily)
        except Exception:  # noqa: BLE001
            pass
        try:                                                # EMA-slope drift μ + ATR (gap-aware vol) — swing read
            import pandas as pd, numpy as np                # noqa: F401
            h6 = stock.history(period="6mo", interval="1d").dropna()
            closes = h6["Close"]
            if len(closes) >= 30:
                ema = closes.ewm(span=21, adjust=False).mean().values
                y = np.log(ema[-21:]); x = np.arange(len(y))
                # Exponentially-weighted regression: half-life ~7 days so the
                # current direction dominates and V-shaped reversals don't
                # report stale declines as today's drift.
                hl = 7.0
                w = np.exp(np.log(2) / hl * (x - x[-1]))      # w[-1]=1, decays backward
                _mu = float(np.polyfit(x, y, 1, w=np.sqrt(w))[0]) * 252    # annualized log-slope drift μ
                ta["_drift_mu"] = round(max(-_DRIFT_MU_CAP, min(_DRIFT_MU_CAP, _mu)), 4)   # clamp noise-driven extremes
            if len(h6) >= 15:                                # ATR (true range → GAP-AWARE, unlike close-to-close HV)
                H, L, C = h6["High"].values, h6["Low"].values, h6["Close"].values
                tr = np.maximum(H[1:] - L[1:], np.maximum(np.abs(H[1:] - C[:-1]), np.abs(L[1:] - C[:-1])))
                atr, last = float(np.mean(tr[-14:])), float(C[-1])
                if last > 0:
                    ta["_atr_pct"] = round(atr / last * 100, 2)                    # daily true-range %
        except Exception:  # noqa: BLE001
            pass
        try:                                                # last cash dividend per share + its cadence
            divs = stock.dividends
            if divs is not None and len(divs) > 0:
                ta["_last_div"] = round(float(divs.iloc[-1]), 4)
                # The dividends series is the ONLY dividend source that works for funds
                # (the calendar 404s for them), and it carries the ex-dates — so it also
                # gives us the payment cadence instead of assuming quarterly. ETFs pay
                # monthly (JEPI/QYLD), quarterly (QQQ/SPY) or annually (some sector funds).
                dates = [ix.date() for ix in divs.index[-6:] if hasattr(ix, "date")]
                gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0)
                if gaps:
                    ta["_div_cadence_days"] = gaps[len(gaps) // 2]      # median gap
                if dates:
                    ta["_next_exdiv"] = dates[-1].isoformat()           # rolled forward by the caller
        except Exception:  # noqa: BLE001
            pass
        if _reports_earnings(stock):                        # the calendar 404s for funds/indices
            try:                                            # scheduled ex-div beats the projection
                cal = stock.calendar
                ed = cal.get("Ex-Dividend Date") if isinstance(cal, dict) else None
                ed = ed[0] if isinstance(ed, (list, tuple)) else ed
                if ed is not None and hasattr(ed, "isoformat"):
                    ta["_next_exdiv"] = ed.isoformat()
            except Exception:  # noqa: BLE001
                pass
        return ta
    except Exception as exc:  # noqa: BLE001
        logger.debug("desk-review TA fetch failed for %s: %s", ticker, exc)
        return {}


def _portfolio_fit_sync(ticker: str) -> dict:
    """1-year beta & correlation vs SPY — the PM's systemic-vs-idiosyncratic read (is this
    trade genuine alpha or disguised market beta?)."""
    try:
        import yfinance as yf
        import numpy as np
        sym = _norm_ticker(ticker)
        if sym.upper() in ("SPY", "^GSPC", "SPX"):
            return {"beta_1y_spx": 1.0, "correlation_spx": 1.0,
                    "note": "the underlying IS the market proxy — pure systematic beta, no idiosyncratic edge"}
        data = yf.download([sym, "SPY"], period="1y", progress=False, auto_adjust=True)
        closes = data["Close"] if "Close" in data.columns.get_level_values(0) else data
        rets = closes[[sym, "SPY"]].pct_change().dropna()
        if len(rets) < 30:
            return {}
        s, m = rets[sym].values, rets["SPY"].values
        var_m = float(np.var(m))
        if var_m <= 0:
            return {}
        beta = float(np.cov(s, m)[0, 1] / var_m)
        corr = float(np.corrcoef(s, m)[0, 1])
        return {"beta_1y_spx": round(beta, 2), "correlation_spx": round(corr, 2),
                "note": "high correlation => behaves like a leveraged SPY position (little idiosyncratic edge); "
                        "low => genuine single-name alpha"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("portfolio-fit fetch failed for %s: %s", ticker, exc)
        return {}


def _gex_sync(ticker: str) -> dict:
    """Dealer Gamma-Exposure read for the stock-level chip. Reuses the CANONICAL
    ``dealer_positioning_service.compute_dealer_positioning`` so the gamma flip / regime /
    Call-Resistance / Put-Support match the Technical → Dealer Positioning panel EXACTLY
    (one source of truth — no more two different flip values across the app).

    Still an open-interest × modelled-γ proxy for dealer POSITIONING, not classified dealer
    flow — labelled ``proxy: True``. Convention: +GEX → dealers LONG gamma → fade moves →
    vol SUPPRESSED / mean-reverting (good for selling premium); −GEX → SHORT gamma → chase
    moves → vol EXPANSION / trending (dangerous, esp. for delta-neutral structures)."""
    try:
        import yfinance as yf
        from .dealer_positioning_service import compute_dealer_positioning
        dp = compute_dealer_positioning(yf.Ticker(_norm_ticker(ticker)))
        if not dp:
            return {}
        ng = dp.get("net_gex") or {}
        gf = dp.get("gamma_flip") or {}
        gl = dp.get("gamma_levels") or {}
        val = ng.get("value")
        return {
            "gex_bn": round(val / 1e9, 2) if val is not None else None,
            "regime": ng.get("sign"),
            "flip_level": gf.get("level"),
            "spot": dp.get("price"),
            "call_resistance": (gl.get("call_resistance") or {}).get("strike"),
            "put_support": (gl.get("put_support") or {}).get("strike"),
            "hvl": (gl.get("hvl") or {}).get("strike"),
            "n_strikes": len(dp.get("strikes") or []),
            "proxy": True,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("GEX read failed for %s: %s", ticker, exc)
        return {}


def _structural_levels(ta: dict, gex: dict) -> list[float]:
    """Flatten the technical read into absolute price levels the scan biases multi-leg SHORT strikes
    toward: classical support/resistance, the volume-profile value-area edges (VAL/VAH), and the dealer
    gamma walls / flip (Put-Support, Call-Resistance, gamma flip, HVL). The snap picks the nearest level
    on each leg's SAFE side — no spot split here (the builder classifies by side at its own spot)."""
    inst = (ta or {}).get("institutional") or {}
    vp = inst.get("volume_profile") or {}
    raw = [
        (ta or {}).get("supportLevel"), (ta or {}).get("resistanceLevel"),
        vp.get("val"), vp.get("vah"),
        (gex or {}).get("put_support"), (gex or {}).get("call_resistance"),
        (gex or {}).get("flip_level"), (gex or {}).get("hvl"),
    ]
    out: list[float] = []
    for x in raw:
        try:
            if x is not None and float(x) > 0:
                out.append(float(x))
        except (TypeError, ValueError):
            pass
    return out


def _ta_summary(ta: dict) -> dict:
    inst = (ta or {}).get("institutional") or {}
    reg = inst.get("regime") or {}
    vp = inst.get("volume_profile") or {}
    ms = inst.get("market_structure") or {}
    return {
        "state": reg.get("state"), "mode": reg.get("mode"), "bias": reg.get("bias"),
        "rsi": ta.get("currentRSI"), "rsi_signal": ta.get("rsiSignal"),
        "support": ta.get("supportLevel"), "resistance": ta.get("resistanceLevel"),
        "poc": vp.get("poc"), "value_area": [vp.get("val"), vp.get("vah")] if vp else None,
        "trend": ms.get("trend"), "bos": (ms.get("bos") or {}).get("type") if ms.get("bos") else None,
    }


def _events_in_window(scan: dict, ta: dict, max_dte: int) -> list[dict]:
    """The important DATED events that land BEFORE the contract expiry — earnings,
    ex-dividend, and macro (FOMC/CPI/NFP/OPEX) — so the agents weigh timing, gap risk,
    and (for short calls) early-assignment-around-ex-div risk. Everything is pre-dated;
    we only filter the already-computed events to the contract window."""
    today = date.today()
    end = today + timedelta(days=max(int(max_dte or 0), 0))
    ei, ee = today.isoformat(), end.isoformat()
    out: list[dict] = []
    ne = (scan.get("context") or {}).get("next_earnings")
    if ne and ei < ne <= ee:
        out.append({"kind": "earnings", "level": "warn",
                    "text": f"Earnings {ne} — BEFORE expiry: DOUBLE-EDGED. Elevated IV means richer premium "
                            "plus a post-event IV-crush tailwind for sellers (often a PRIME time to sell "
                            "income; the keep-probability already prices the bigger implied move) — but also "
                            "directional gap and assignment risk. Let the metrics (IV rank, IV vs HV, keep-"
                            "prob, cushion, CVaR) decide whether the premium compensates the risk"})
    ed = (ta or {}).get("_next_exdiv")
    if ed and ei <= ed <= ee:
        out.append({"kind": "dividend", "level": "info",
                    "text": f"Ex-dividend {ed} — before expiry: raises EARLY-ASSIGNMENT risk on ITM short "
                            "calls (holders may exercise to capture the dividend)"})
    for m in _macro_events_in_window(today, end):
        out.append({"kind": "macro", "level": "info", "text": f"{m} — macro volatility catalyst before expiry"})
    return out


def _strikes(t: dict) -> str:
    if t.get("structure") == "iron_condor":
        return f"{t.get('put_long')}/{t.get('put_short')}–{t.get('call_short')}/{t.get('call_long')}"
    if t.get("structure") == "jade_lizard":
        return f"put {t.get('put_short')} · call {t.get('call_short')}/{t.get('call_long')}"
    return f"{t.get('short_strike')}" + (f"/{t.get('long_strike')}" if t.get("long_strike") else "")


def _json_default(o):
    """Serialize numpy scalars/arrays that leak in from the TA engine."""
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:  # noqa: BLE001
        pass
    return str(o)


def _strikes_json(t: dict) -> dict:
    """Structured strikes so the model reads exact numbers (never invents one)."""
    s = t.get("structure")
    if s == "iron_condor":
        return {"put_long": t.get("put_long"), "put_short": t.get("put_short"),
                "call_short": t.get("call_short"), "call_long": t.get("call_long")}
    if s == "jade_lizard":
        return {"put_short": t.get("put_short"), "call_short": t.get("call_short"), "call_long": t.get("call_long")}
    out = {"short": t.get("short_strike")}
    if t.get("long_strike"):
        out["long"] = t.get("long_strike")
    return out


def _ta_json(ta: dict) -> dict:
    """The full technical read as a structured object (all critical data points)."""
    if not ta:
        return {}
    inst = ta.get("institutional") or {}
    reg = inst.get("regime") or {}
    vp = inst.get("volume_profile") or {}
    ms = inst.get("market_structure") or {}
    return {
        "regime": {"state": reg.get("state"), "mode": reg.get("mode"), "bias": reg.get("bias"),
                   "rationale": reg.get("rationale"), "favored_income": reg.get("favored_income_labels") or []},
        "rsi": {"value": ta.get("currentRSI"), "signal": ta.get("rsiSignal")},
        "support": ta.get("supportLevel"), "resistance": ta.get("resistanceLevel"),
        "volume_profile": ({"poc": vp.get("poc"), "value_area_low": vp.get("val"),
                            "value_area_high": vp.get("vah"),
                            "note": "spot ABOVE value area = calls richer/safer to sell; BELOW = puts richer/safer"}
                           if vp else None),
        "market_structure": {"trend": ms.get("trend"), "break_of_structure": ms.get("bos"),
                             "change_of_character": ms.get("change_of_character")},
        "order_blocks": [{"type": o.get("type"), "low": o.get("bottom"), "high": o.get("top"),
                          "mitigated": o.get("mitigated")} for o in (inst.get("order_blocks") or [])[:4]],
        "unfilled_fair_value_gaps": [{"type": f.get("type"), "low": f.get("bottom"), "high": f.get("top")}
                                     for f in (inst.get("fair_value_gaps") or []) if not f.get("filled")][:3],
        "liquidity_sweeps": [{"type": s.get("type"), "level": s.get("level")}
                             for s in (inst.get("liquidity_sweeps") or [])[:3]],
        # current MACD state only — drop the ~60-point chart series (macdValues/signalValues/…), pure bloat
        "macd": ({"macd_line": (ta.get("macd") or {}).get("macdLine"),
                  "signal_line": (ta.get("macd") or {}).get("signalLine"),
                  "histogram": (ta.get("macd") or {}).get("histogram"),
                  "signal": (ta.get("macd") or {}).get("signal"),
                  "crossover": (ta.get("macd") or {}).get("crossover")} if ta.get("macd") else None),
        "moving_averages": ta.get("movingAverages"),
    }


def _stress_pnl(opp: dict, spot: float) -> dict:
    """Terminal (at-expiry) P&L if spot settles ±10% — the capital profile if it lands there."""
    legs = _opp_legs(opp)
    contracts = int(opp.get("contracts") or 1)
    stock_shares = 100.0 * contracts if opp.get("structure") in _STOCK_STRUCTURES else 0.0
    curve = terminal_payoff_curve(legs, stock_shares, spot, lo=-0.10, hi=0.10, step=0.10)
    return {"down": curve[0]["pnl"], "up": curve[-1]["pnl"]}     # curve = [−10%, 0, +10%]


def _bs_price(S: float, K: float, dte_days: int, sigma: float, right: str, r: float = 0.0) -> float:
    """Plain Black–Scholes price (stdlib normal CDF) — used to mark legs to market under a shock."""
    from statistics import NormalDist
    T = max(int(dte_days), 1) / 365.0
    intrinsic = max(0.0, (S - K) if right == "C" else (K - S))
    if sigma <= 0 or S <= 0 or K <= 0:
        return intrinsic
    nd = NormalDist().cdf
    srt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / srt
    d2 = d1 - srt
    if right == "C":
        return S * nd(d1) - K * math.exp(-r * T) * nd(d2)
    return K * math.exp(-r * T) * nd(-d2) - S * nd(-d1)


def _instant_mtm(opp: dict, spot: float, move_frac: float, vol_bump: float) -> float:
    """Instantaneous mark-to-market P&L if spot GAPS by move_frac TOMORROW — full DTE still on,
    each leg's IV bumped by vol_bump. Captures the short-gamma/vega PAPER drawdown the terminal number
    hides (intra-life mark / voluntary-exit cost). It is a FORCED-liquidation trigger only on a leveraged
    book (margin_requirement_shock_pct > 0); for our fully-collateralized structures it is paper only."""
    s2 = spot * (1 + move_frac)
    contracts = int(opp.get("contracts") or 1)
    stock_shares = 100.0 * contracts if opp.get("structure") in _STOCK_STRUCTURES else 0.0
    dte = int(opp.get("dte") or 0)
    pnl = stock_shares * (s2 - spot)
    for l in opp.get("legs", []):
        right = "C" if str(l.get("type", "")).upper().startswith("C") else "P"
        sign = 1 if str(l.get("action", "")).upper().startswith("B") else -1
        iv = max(0.05, (l.get("iv") or 0) / 100.0 + vol_bump)
        newp = _bs_price(s2, float(l.get("strike") or 0), dte, iv, right)
        pnl += sign * contracts * 100.0 * (newp - float(l.get("mid") or 0.0))
    return round(pnl, 1)


def _short_leg_iv(r: dict) -> Optional[float]:
    """IV (%) of the primary SHORT leg — for the strike-vs-ATM skew premium."""
    ss = r.get("short_strike") or r.get("put_short") or r.get("call_short")
    for l in r.get("legs", []):
        if str(l.get("action", "")).upper().startswith("S") and l.get("strike") == ss:
            return l.get("iv")
    for l in r.get("legs", []):
        if str(l.get("action", "")).upper().startswith("S"):
            return l.get("iv")
    return None


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _macd_accel(ta: dict, spot: Optional[float]) -> Optional[dict]:
    """MACD-histogram ACCELERATION (2nd-derivative of momentum) — is the trend's speed increasing?
    accel = Δhistogram over ~3 sessions; accel_norm = accel ÷ spot (scale-free for the veto threshold)."""
    hv = ((ta or {}).get("macd") or {}).get("histogramValues") or []
    if len(hv) < 5 or not spot:
        return None
    accel = float(hv[-1]) - float(hv[-4])
    return {"histogram": round(float(hv[-1]), 4), "accel": round(accel, 4),
            "accel_norm": round(accel / spot, 5)}


def _drift_adjusted_keep(opp: dict, spot: Optional[float], mu: Optional[float],
                         phys_vol: Optional[float], r: float, keep_pct: Optional[float]) -> Optional[float]:
    """Drift-adjusted (physical, P-measure) keep-prob for the NEAREST short leg: how far the real-world
    drift μ moves the risk-neutral keep-prob. Computes P(short leg OTM) at drift=μ vs drift=r on the
    binding strike and applies that DELTA to the trade's standard keep_pct (income keep-prob is dominated
    by the nearest short). Returns a % (clamped 0–99.9) or None. It NEVER touches the headline Win%."""
    if not (spot and mu is not None and phys_vol and phys_vol > 0 and keep_pct is not None):
        return None
    dte = int(opp.get("dte") or 0)
    shorts = [(l.get("strike"), str(l.get("type", "")).upper())
              for l in (opp.get("legs") or [])
              if str(l.get("action", "")).upper().startswith("S") and l.get("strike")]
    if dte <= 0 or not shorts:
        return None
    K, typ = min(shorts, key=lambda kv: abs((kv[0] or spot) - spot))
    side_put = typ.startswith("P")
    T, sig = dte / 365.0, phys_vol

    def p_otm(drift: float) -> float:
        d = (math.log(spot / K) + (drift - 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
        return _norm_cdf(d) if side_put else _norm_cdf(-d)     # put wants S>K; call wants S<K

    delta = p_otm(mu) - p_otm(r)
    return round(max(0.0, min(99.9, keep_pct + delta * 100.0)), 1)


def _gap_aware_vol(hv: Optional[float], atr_pct: Optional[float]) -> tuple:
    """Keltner/ATR cross-check → (physical_vol, atr_vol). ATR is TRUE range (includes overnight GAPS),
    so on gappy names the ATR-implied vol exceeds close-to-close HV. The physical (P) vol used for the
    boundaries and the base reweight becomes the WIDER of the two — a short strike must survive the
    gap-aware range, not just the smooth close-to-close one."""
    atr_vol = None
    if atr_pct and atr_pct > 0:
        atr_vol = (atr_pct / 100.0) / 1.4 * (252.0 ** 0.5)    # daily true range ≈ 1.4σ → annualized vol
    cands = [v for v in (hv, atr_vol) if v and v > 0]
    return (max(cands) if cands else hv), atr_vol


def _lvn_check(opp: dict, vp: Optional[dict], spot: Optional[float]) -> Optional[dict]:
    """Low-Volume-Node 'slip-through' test. A short strike sitting in a THIN volume node has no
    absorption — price slips through it fast. Returns a demerit {label, points, note} or None. (Sitting
    BEHIND a High-Volume Node is the opposite — structural friction — already rewarded by Value-area.)"""
    bins = (vp or {}).get("bins") or []
    if len(bins) < 8 or not spot:
        return None
    import statistics
    med = statistics.median([b.get("pct", 0) for b in bins]) or 0.0
    if med <= 0:
        return None
    shorts = [l.get("strike") for l in (opp.get("legs") or [])
              if str(l.get("action", "")).upper().startswith("S") and l.get("strike")]
    for K in shorts:
        b = min(bins, key=lambda x: abs(x.get("price", spot) - K))
        if b.get("pct", med) < 0.5 * med:                    # thin node at the short strike
            return {"label": "LVN slip", "points": -5,
                    "note": f"short strike in a low-volume node (${b.get('price')}, {b.get('pct')}% vol) — price slips through"}
    return None


def _expected_move_pct(r: dict) -> Optional[float]:
    """The 1σ implied move to expiry (%) = ATM IV × √(dte/365) — pre-computed so the LLM can
    compare it to cushion_pct_otm without doing the arithmetic itself."""
    atm, dte = r.get("atm_iv_pct"), int(r.get("dte") or 0)
    return round(atm * (dte / 365.0) ** 0.5, 1) if (atm and dte) else None


# --- shared deterministic primitives (used by BOTH the ranking grade and the LLM payload) ----------

def _physical_move_pct(r: dict, hv: Optional[float]) -> Optional[float]:
    """The 1σ PHYSICAL (realized-vol) move to expiry (%) = realized vol × √(dte/365). hv is decimal."""
    dte = int(r.get("dte") or 0)
    return round(hv * 100 * (dte / 365.0) ** 0.5, 1) if (hv and dte) else None


def _dual_move_pct(r: dict, hv: Optional[float]) -> tuple:
    """(dual, implied, physical) 1σ moves (%). The DUAL boundary is the WIDER of the implied (Q-measure)
    and physical (P-measure) expected move — a short strike must clear BOTH to be genuinely cushioned.
    Relying on implied alone is the negative-VRP trap (crushed IV makes a close strike look 'safe')."""
    imp = _expected_move_pct(r)
    phys = _physical_move_pct(r, hv)
    cands = [x for x in (imp, phys) if x]
    return (max(cands) if cands else None), imp, phys


def _nearest_short_sigmas(opp: dict, spot: Optional[float], em_pct: Optional[float] = None) -> Optional[float]:
    """Closest SHORT strike to spot in 1σ expected-move units (< ~0.5 = an ATM / directional short leg).
    Pass em_pct to gate on a specific boundary (e.g. the DUAL implied-vs-physical move); defaults to implied."""
    em = em_pct or _expected_move_pct(opp)
    if not (spot and em):
        return None
    sig = [round(abs(l["strike"] - spot) / spot * 100 / em, 2)
           for l in (opp.get("legs") or [])
           if str(l.get("action", "")).upper().startswith("S") and l.get("strike")]
    return min(sig) if sig else None


def _prob_max_profit(opp: dict) -> Optional[float]:
    """Probability EVERY short leg expires OTM (the FULL credit), from short-leg deltas (|delta| ≈ P(ITM))."""
    sd = [abs(l.get("delta") or 0.0) for l in (opp.get("legs") or [])
          if str(l.get("action", "")).upper().startswith("S")]
    return round(max(0.0, min(1.0, 1.0 - sum(sd))) * 100, 1) if sd else None


def _bps_regime_of(bps: Optional[int]) -> Optional[str]:
    return (None if bps is None else "positive_alpha" if bps >= 0
            else "structurally_broken" if bps < _MATERIAL_FAIL_BPS else "marginal_fair")


def _skew_regime_of(bps: Optional[int]) -> Optional[str]:
    return (None if bps is None else "extreme" if bps >= _SKEW_EXTREME_BPS
            else "elevated" if bps >= _SKEW_ELEVATED_BPS else "normal")


def _opp_bps(opp: dict, dm: dict, sofr_pct: float) -> Optional[int]:
    """Annualized EXPECTED (EV) return minus SOFR, in bps — the risk-neutral capital-efficiency edge."""
    er = ((dm or {}).get("pm") or {}).get("expected_return_pct")
    dte = int(opp.get("dte") or 0)
    ann = er * 365.0 / dte if (er is not None and dte) else None
    return round((ann - sofr_pct) * 100) if ann is not None else None


# ---------------------------------------------------------------------------
# Algorithmic pre-vetting: fold EVERY deterministic institutional factor into the score, so the
# trade that reaches the (expensive) LLM desk is already vetted and the top-ranked one is very
# likely to be APPROVED. Returns a score adjustment + merits/demerits + hard BLOCKING flags.
# ---------------------------------------------------------------------------

# ── Earnings timing — the vega-ramp / IV-crush nuance a premium desk lives by ────────────────
_EARN_HOLD_PENALTY = 8.0    # holding a short through the pre-earnings IV RAMP (short vega into rising IV)
_EARN_CLEAN_BONUS = 3.0     # the window ENDS before earnings → clean theta/VRP harvest, no event gap
_EARN_CRUSH_BONUS = 4.0     # earnings imminent + short strike OUTSIDE the implied move → a real IV-crush harvest


def _earnings_timing_factor(opp: dict, next_earnings: Optional[str], today) -> tuple:
    """Grade the earnings TIMING of a premium sale — what pure VRP/moneyness miss. Selling well BEFORE the
    print holds the short through the IV RAMP (short vega into rising implied vol: theta is OFFSET, and the
    fat premium is EVENT compensation, not free decay) plus the binary gap → penalise. Earnings IMMINENT is
    the IV-CRUSH play — reward ONLY if the short strike sits outside the implied move. A window that ENDS
    before earnings is the cleanest harvest → reward. Returns (points, merit|None, demerit|None)."""
    if not next_earnings:
        return 0.0, None, None
    if opp.get("structure") == "calendar":
        # A calendar is LONG vega — the pre-earnings IV ramp HELPS it, so the short-vol hold-through penalty
        # is wrong. The IDEAL earnings calendar sells a front that expires BEFORE the print and owns a back
        # that expires AFTER — harvesting the term-structure collapse. Reward that; otherwise neutral.
        try:
            fe = date.fromisoformat(opp.get("expiration"))
            be = date.fromisoformat(opp.get("back_expiration"))
            ne = date.fromisoformat(next_earnings)
        except Exception:  # noqa: BLE001
            return 0.0, None, None
        if fe < ne <= be:
            return _EARN_CRUSH_BONUS, ("earnings calendar — sells the rich pre-earnings front, owns the "
                    "post-earnings back; harvests the IV term-structure collapse (long vega)"), None
        return 0.0, None, None
    try:
        ed = date.fromisoformat(next_earnings)
    except Exception:  # noqa: BLE001
        return 0.0, None, None
    dte = int(opp.get("dte") or 0)
    if dte <= 0:
        return 0.0, None, None
    expiry = today + timedelta(days=dte)
    if ed <= today or ed > expiry:                      # earnings outside the trade's life → clean harvest
        return _EARN_CLEAN_BONUS, "clean window — no earnings before expiry (pure theta/VRP harvest, no event gap)", None
    days = (ed - today).days
    imp = _expected_move_pct(opp)                        # 1σ implied move to expiry (%)
    cush = opp.get("cushion_pct")                        # short-strike distance from spot (%)
    outside = cush is not None and imp is not None and cush >= imp
    if days <= 2:                                        # imminent → the IV-crush harvest
        if outside:
            return _EARN_CRUSH_BONUS, (f"IV-crush harvest — earnings in {days}d at peak IV and the short strike sits "
                    f"OUTSIDE the implied move (cushion {cush}% ≥ implied {imp}%): the post-event vol crush works for you"), None
        return -4.0, None, (f"earnings in {days}d but the short strike is INSIDE the implied move (cushion {cush}% < "
                    f"implied {imp}%) — the crush only pays if the stock holds the cone; a gap breaches you")
    pen = _EARN_HOLD_PENALTY if days >= 5 else _EARN_HOLD_PENALTY * 0.6
    return -round(pen, 1), None, (f"sells ~{days}d before earnings — short VEGA into the IV ramp: theta is offset by "
            f"rising implied vol until the print, so the fat premium is event compensation (not free decay) and you "
            f"carry the binary gap. A desk waits until ~1d pre-print (crush) or trades the clean post-event window")


def _event_adjusted_yield(opp: dict, vsx: dict, next_earnings: Optional[str], today) -> tuple:
    """Strip the EVENT premium from the headline annualized yield. When earnings lands inside the window a
    chunk of the fat premium is compensation for the binary print (jump risk), NOT harvestable time decay —
    so the headline carry misleads. event share ≈ 1 − baseline_vol/IV (an ATM option's value is ~linear in
    vol); the adjusted yield is what pure decay would pay. Returns (adjusted_yield_pct, event_share)."""
    ann = opp.get("premium_annualized_pct")
    iv = opp.get("atm_iv_pct") or vsx.get("iv_atm_pct")
    exp = opp.get("expiration")
    if ann is None or not iv or not exp or not next_earnings:
        return ann, 0.0
    try:
        ne = date.fromisoformat(next_earnings)
        ed = date.fromisoformat(exp)
    except Exception:  # noqa: BLE001
        return ann, 0.0
    if not (today < ne <= ed):                      # earnings not in the trade's life → the yield is clean
        return ann, 0.0
    baseline = max(vsx.get("hv30_pct") or 0.0, vsx.get("har_rv_pct") or 0.0)   # realized / forward-RV floor (%)
    if baseline <= 0 or iv <= baseline:
        return ann, 0.0
    share = min(1.0 - baseline / iv, 0.6)           # cap: never claim >60% of the premium is the event
    return round(ann * (1.0 - share), 1), round(share, 2)


def _algo_grade(opp: dict, dm: dict, spot: float, sofr_pct: float, atm_iv_pct: Optional[float],
                iv_rank: Optional[float], beta: Optional[float], iv_percentile: Optional[float] = None,
                hv: Optional[float] = None, gex: Optional[dict] = None,
                macd: Optional[dict] = None, overwrite: bool = False,
                next_earnings: Optional[str] = None, today=None,
                har_rv_pct: Optional[float] = None) -> dict:
    pm = (dm or {}).get("pm") or {}
    merits, demerits, blocking = [], [], []
    # VRP ratio is GAP-AWARE: implied ÷ the physical vol used everywhere (max of HV and ATR), so the
    # grade, the veto and the number-line all agree. Falls back to the scan's HV-based ratio.
    iv_hv = round((atm_iv_pct / 100.0) / hv, 2) if (atm_iv_pct and hv and hv > 0) else opp.get("iv_hv_ratio")
    # Itemized signed contributions (points) by factor — so the UI can show each adjustment as a bar
    # and the desk score is auditable: desk_score = base_quality + regime + Σ(these).
    comp: dict[str, float] = {"expectation": 0.0, "vrp": 0.0, "moneyness": 0.0,
                              "skew": 0.0, "liquidity": 0.0, "beta": 0.0, "event": 0.0}
    if today is None:
        today = date.today()
    is_cal = opp.get("structure") == "calendar"   # LONG-vega, ATM-by-design → the short-vol penalties invert

    # 1) Genuinely-bad EV — DEMOTE (never auto-reject). A negative risk-neutral bps is NORMAL for income
    #    selling (fair pricing; the real edge is the VRP), so do NOT block on it. Only flag a trade that
    #    truly LOSES in expectation under the model — a robust signal, not the annualized-bps artifact.
    bps = _opp_bps(opp, dm, sofr_pct)
    omega, ev = pm.get("omega"), pm.get("expected_value")
    if omega is not None and omega < 0.9 and ev is not None and ev < 0:
        demerits.append(f"loses in expectation (Omega {omega}, EV {ev})"); comp["expectation"] -= 12
    elif bps is not None and 0 <= bps <= 3000:          # a sane positive edge (guard annualization blow-ups)
        merits.append("positive risk-neutral edge"); comp["expectation"] += 3

    # 2) Volatility Risk Premium — the REAL edge, and the negative-VRP TRAP. Rich implied vs realized
    #    rewards; cheap implied (implied << realized) is penalised IN PROPORTION to the gap and
    #    HARD-BLOCKED past the floor — the crushed-vol case where selling premium has no edge.
    if iv_hv is not None and is_cal:
        # A calendar is LONG vega — CHEAP implied vol is an EDGE (own vol before it reprices up), the exact
        # opposite of a premium seller. Never block it on "crushed vol".
        if iv_hv < 0.95:
            merits.append(f"long-vega calendar in cheap IV (IV/HV {iv_hv}) — own vol before it reprices"); comp["vrp"] += 5
        elif iv_hv > 1.25:
            demerits.append(f"paying up for vega (IV/HV {iv_hv}) — a calendar wants CHEAP, not rich, IV"); comp["vrp"] -= 4
    elif iv_hv is not None:
        if iv_hv >= 1.05:
            # CONTINUOUS rich-VRP reward — scales with BOTH how rich implied is vs realized AND the IV
            # rank/percentile (a high rank alone earns some credit even at a modest IV/HV). NOTE: the rank
            # & percentile here are vs the trailing 1y REALIZED-vol range (there is no historical-IV feed),
            # so they are labelled honestly as realized-range-based, NOT a true IV-vs-IV rank.
            rp = max(iv_rank or 0.0, iv_percentile or 0.0)     # 0–100, realized-range-based
            vrp_pts = min(max(round((iv_hv - 1.0) * 8) + round((rp - 50) / 50.0 * 4), 0), 9)
            if vrp_pts > 0:
                comp["vrp"] += vrp_pts
                merits.append(f"rich VRP +{vrp_pts} — IV/HV {iv_hv}, IV-rank {iv_rank}/pctile {iv_percentile} "
                              f"(vs 1y realized-vol range)")
        elif iv_hv < 1.0:
            # VRP vs the FORWARD RV forecast (HAR), not just trailing HV. Trailing realized is often
            # spike-inflated by a PAST event (an earnings/gap day) and is mean-reverting DOWN — so
            # "IV < trailing HV" can be a REASONABLE post-event crush, not a no-edge trap. Only hard-VETO
            # when the vol is crushed vs the FORWARD forecast too; otherwise it's still-elevated premium
            # normalising, so downgrade softly and keep the opportunity.
            iv_har = ((atm_iv_pct / 100.0) / (har_rv_pct / 100.0)) if (atm_iv_pct and har_rv_pct and har_rv_pct > 0) else None
            reasonable_crush = iv_har is not None and iv_har >= _VRP_BLOCK_RATIO   # OK vs the forward RV forecast
            if reasonable_crush:
                comp["vrp"] -= 3
                demerits.append(f"cheap vs trailing HV (IV/HV {iv_hv}) but FAIR vs the forward RV forecast "
                                f"(IV/HAR {round(iv_har, 2)}) — trailing realized is spike-inflated & reverting down; "
                                f"premium still sellable (not a no-edge veto)")
            else:
                pen = min(round((1.0 - iv_hv) * _VRP_PENALTY_K), _VRP_PENALTY_CAP)   # magnitude-scaled by the gap
                if pen > 0:
                    comp["vrp"] -= pen
                    demerits.append(f"negative VRP — implied {round(iv_hv*100)}% of realized (IV/HV {iv_hv})")
                if iv_hv < _VRP_BLOCK_RATIO:
                    _fwd = f" AND {round(iv_har*100)}% of forward RV" if iv_har is not None else ""
                    blocking.append(f"crushed vol — implied only {round(iv_hv*100)}% of trailing realized{_fwd} (negative VRP, no edge)")

    # 3) Moneyness — a near-ATM short leg makes 'income' a DIRECTIONAL bet. Measured against the DUAL
    #    boundary (the WIDER of the implied Q-move and the physical P-move), so a strike that looks
    #    'deep' under crushed IV but is physically exposed can no longer hide.
    dual_em, imp_em, phys_em = _dual_move_pct(opp, hv)
    nss = _nearest_short_sigmas(opp, spot, dual_em)
    pmp = _prob_max_profit(opp)
    if not is_cal:              # a calendar is ATM BY DESIGN (max profit at the strike) — the near-ATM
                                # "directional" penalty and the full-credit-prob check don't apply to it
        if nss is not None and nss < 0.5:
            demerits.append(f"near-ATM short leg ({nss}σ dual) — directional, not cushioned"); comp["moneyness"] -= 12
        elif nss is not None and nss < 1.0:
            demerits.append(f"thin cushion ({nss}σ dual)"); comp["moneyness"] -= 4
        elif nss is not None and nss >= 1.5:
            merits.append(f"deep cushion ({nss}σ dual)"); comp["moneyness"] += 5
        if pmp is not None and pmp < 50:
            demerits.append(f"full-credit prob only {pmp}%"); comp["moneyness"] -= 6
        elif pmp is not None and pmp >= 85:
            merits.append(f"full-credit prob {pmp}%"); comp["moneyness"] += 3

    # 4) Skew / per-strike IV EDGE — TRADE the skew, don't merely avoid the extreme. The SHORT strike's OWN
    #    IV vs ATM is the skew premium you harvest: a strike richer than ATM PAYS you extra for the distance
    #    (a real, persistent edge — normal equity put-skew is exactly why selling OTM puts is well-paid); a
    #    CHEAP strike leaves you underpaid for the risk. An EXTREME bump stays double-edged (the market may
    #    be pricing a KNOWN tail there). This is also the per-strike "best-paid strike" signal.
    iv_edge_vp = None
    sliv = _short_leg_iv(opp)                            # the SHORT strike's OWN IV (per-strike, not ATM)
    ss_bps = round((sliv - atm_iv_pct) * 100) if (sliv is not None and atm_iv_pct is not None) else None
    if ss_bps is not None and not is_cal:
        iv_edge_vp = round(ss_bps / 100.0, 1)            # vol-pts the short strike is rich (+) / cheap (−) vs ATM
        if ss_bps >= _SKEW_EXTREME_BPS:
            comp["skew"] -= 5
            demerits.append(f"EXTREME skew — short-strike IV {round(sliv, 1)}% is +{iv_edge_vp}vp over ATM: a fat "
                            f"premium, but double-edged (the market may be pricing a KNOWN tail here) — size down")
        elif ss_bps >= _SKEW_RICH_BPS:                   # rich wing → harvest the skew premium (best-paid strike)
            pts = min(round((ss_bps - _SKEW_RICH_BPS) / 300.0) + 2, 5)
            comp["skew"] += pts
            merits.append(f"selling the RICH wing +{pts} — short-strike IV {round(sliv, 1)}% is +{iv_edge_vp}vp "
                          f"over ATM {round(atm_iv_pct, 1)}%: you're paid EXTRA for the skew")
        elif ss_bps <= -_SKEW_RICH_BPS:                  # selling a cheap strike → underpaid for the distance
            comp["skew"] -= 3
            demerits.append(f"selling the CHEAP wing — short-strike IV {round(sliv, 1)}% is {iv_edge_vp}vp UNDER "
                            f"ATM {round(atm_iv_pct, 1)}%: underpaid for the strike's distance")

    # 5) Execution / liquidity.
    spreads = [l.get("bid_ask_spread_pct") for l in (opp.get("legs") or []) if l.get("bid_ask_spread_pct") is not None]
    worst = max(spreads) if spreads else None
    if worst is not None and worst > 15:
        demerits.append(f"wide spread ({worst}%)"); comp["liquidity"] -= 8
    elif worst is not None and worst > 10:
        demerits.append(f"wide-ish spread ({worst}%)"); comp["liquidity"] -= 5
    elif worst is not None and worst < 5:
        merits.append("tight spreads"); comp["liquidity"] += 3

    # 6) (Tail is already scored by the base quant model; a CVaR-vs-CAPITAL demerit is structure-blind —
    #     a defined-risk spread's CVaR is ~100% of capital BY DEFINITION — so it is intentionally omitted.)

    # 7) Systemic beta — a high-beta name is a LEVERAGED market bet, not idiosyncratic income. But on an
    #    OVERWRITE (shares already held) the exposure is pre-existing and the short call REDUCES it, so
    #    credit the overlay instead of penalising beta.
    if overwrite:
        merits.append("income overlay on held shares — no new capital; the short call caps existing downside")
        comp["beta"] += 3
    elif beta is not None and beta >= 2.0:
        demerits.append(f"high beta {beta} (leveraged market bet)"); comp["beta"] -= 6
    elif beta is not None and beta >= 1.5:
        demerits.append(f"elevated beta {beta}"); comp["beta"] -= 3

    # (Event density intentionally NOT a flat demerit — routine macro spans every multi-week trade, and an
    #  earnings print is DOUBLE-EDGED, not simply bad; event TIMING is a qualitative call for the desk.)

    # 8) Dealer gamma HARD FILTER — a SHORT-gamma tape (dealers chase moves → vol expansion) runs
    #    DELTA-NEUTRAL premium over both ways: veto iron condors. Directional income is only penalised
    #    (via the TA "Gamma regime" factor), not blocked.
    if gex and gex.get("regime") == "short" and opp.get("structure") == "iron_condor":
        blocking.append("short-gamma tape vetoes delta-neutral (iron condor) — vol expansion runs it over both ways")

    # 9) MACD ACCELERATION — a SEPARATE timing veto (the Trend-drift factor is velocity; THIS is
    #    acceleration). If momentum is actively accelerating AGAINST the short side past the threshold,
    #    veto — you'd be stepping in front of a speeding-up move.
    if macd and macd.get("accel_norm") is not None:
        an, h, struct = macd["accel_norm"], (macd.get("histogram") or 0.0), opp.get("structure")
        if struct in _BULLISH_INCOME and an < -_MACD_ACCEL_VETO and h < 0:
            blocking.append(f"MACD accelerating down against short puts (Δhist {macd.get('accel')}) — counter-trend timing veto")
        elif struct in _BEARISH_INCOME and an > _MACD_ACCEL_VETO and h > 0:
            blocking.append(f"MACD accelerating up against short calls (Δhist {macd.get('accel')}) — counter-trend timing veto")

    # N) Earnings TIMING — the vega-ramp / IV-crush nuance (avoid selling INTO the ramp; harvest the crush
    #    or the clean post-event window). What pure VRP/moneyness can't see.
    et_pts, et_merit, et_demerit = _earnings_timing_factor(opp, next_earnings, today)
    comp["event"] += et_pts
    if et_merit:
        merits.append(et_merit)
    if et_demerit:
        demerits.append(et_demerit)

    adj = sum(comp.values())
    return {"adj": adj, "merits": merits, "demerits": demerits, "blocking": blocking, "components": comp,
            "iv_edge_vp": iv_edge_vp,   # short-strike IV vs ATM (vol-pts) — the per-strike skew premium / edge
            # Q-vs-P boundary read (for the number-line viz): implied vs physical 1σ moves + strike distance.
            "qp": {"implied_move_pct": imp_em, "physical_move_pct": phys_em, "dual_move_pct": dual_em,
                   "short_sigmas": nss, "iv_hv_ratio": iv_hv,
                   "short_dist_pct": round(nss * dual_em, 1) if (nss is not None and dual_em) else None,
                   "physical_wider": bool(phys_em and imp_em and phys_em > imp_em),
                   "exposed_physical": bool(nss is not None and dual_em and phys_em
                                            and (nss * dual_em) < phys_em)}}


def _grade_letter(score: int, blocking: list) -> tuple[str, str]:
    """Map the final desk score (+ any hard block) to a letter grade and an LLM-approval likelihood."""
    if blocking:
        return "F", "auto_reject"
    if score >= 78:
        return "A", "high"
    if score >= 65:
        return "B", "high"
    if score >= 52:
        return "C", "medium"
    if score >= 38:
        return "D", "low"
    return "F", "low"


def _candidate_extra(r: dict, meta: dict, max_oi_strike: Optional[float]) -> dict:
    """Institutional add-ons the desks asked for: execution slippage, ±10% capital shock
    (TERMINAL vs INSTANTANEOUS MTM), gamma-pin cushion, per-strike skew premium, stressed
    liquidity capacity, edge-vs-SOFR in bps, and a capital-lockup horizon."""
    spot, sofr_pct = meta["spot"], meta["sofr_pct"]
    spreads = [l.get("bid_ask_spread_pct") for l in (r.get("legs") or [])
               if l.get("bid_ask_spread_pct") is not None]
    worst_spread = round(max(spreads), 1) if spreads else None
    ml = r.get("max_loss")
    cap = abs(ml) if ml is not None else float(r.get("collateral") or 0.0)
    def _pctcap(x):
        return round(x / cap * 100, 1) if cap else None
    stress = _stress_pnl(r, spot)                                   # terminal (at expiry)
    inst_down = _instant_mtm(r, spot, -0.10, 0.12)                 # gap TOMORROW, IV +12 pts
    inst_up = _instant_mtm(r, spot, 0.10, 0.08)                    # gap TOMORROW, IV +8 pts
    structure = r.get("structure")
    capital_profile = ("covered" if structure in _STOCK_STRUCTURES
                       else "defined_risk" if ml is not None else "cash_secured")
    ss = r.get("short_strike")
    pin_cushion = round(abs(ss - max_oi_strike), 2) if (ss and max_oi_strike) else None
    # Per-strike skew premium: how much the SHORT strike's IV sits over ATM (defends selling THIS strike).
    sliv, atm_iv = _short_leg_iv(r), meta.get("atm_iv")
    ss_iv_prem_bps = round((sliv - atm_iv) * 100) if (sliv is not None and atm_iv is not None) else None
    skew_regime = (None if ss_iv_prem_bps is None
                   else "extreme" if ss_iv_prem_bps >= _SKEW_EXTREME_BPS
                   else "elevated" if ss_iv_prem_bps >= _SKEW_ELEVATED_BPS
                   else "normal")
    # F1/F2: per-SHORT-leg moneyness — distance in 1σ units + the BINDING cushion + prob of the FULL credit.
    # Surfaces a near-ATM short leg that the FAR-leg cushion_pct_otm hides (e.g. a jade lizard's ATM call).
    spot_, em_pct = meta.get("spot"), _expected_move_pct(r)
    short_sigmas, short_cushions, short_deltas = {}, [], []
    for l in (r.get("legs") or []):
        if str(l.get("action", "")).upper().startswith("S"):
            k = l.get("strike")
            short_deltas.append(abs(l.get("delta") or 0.0))
            if k and spot_:
                dist_pct = round(abs(k - spot_) / spot_ * 100, 1)
                short_cushions.append(dist_pct)
                tag = ("C" if str(l.get("type", "")).upper().startswith("C") else "P") + str(int(round(k)))
                if em_pct:
                    short_sigmas[tag] = round(dist_pct / em_pct, 2)
    nearest_short_cushion = min(short_cushions) if short_cushions else None
    nearest_short_sigmas = min(short_sigmas.values()) if short_sigmas else None
    prob_max_profit = round(max(0.0, min(1.0, 1.0 - sum(short_deltas))) * 100, 1) if short_deltas else None
    # Stressed liquidity: spreads widen into events; multiplier is a modeling assumption.
    vols = [l.get("vol") for l in (r.get("legs") or []) if l.get("vol")]
    thin_1lot_pct = round(1.0 / min(vols) * 100, 3) if vols else None
    widen_mult = 2.5 if meta.get("earnings_before") else 1.5
    stressed_spread = round(worst_spread * widen_mult, 1) if worst_spread is not None else None
    # Edge in bps over SOFR on an EXPECTED-value basis (annualized) — NOT the headline premium yield
    # (a defined-risk spread's huge return-on-margin is leverage, not edge; its EV can be ~0).
    er = ((r.get("desk_metrics") or {}).get("pm") or {}).get("expected_return_pct")   # over the hold
    dte = int(r.get("dte") or 0)
    ann_er = er * 365.0 / dte if (er is not None and dte) else None
    bps = round((ann_er - sofr_pct) * 100) if ann_er is not None else None
    # Three-way regime (A+B): the risk-neutral bps can't show alpha; it only flags a structural break.
    bps_regime = (None if bps is None
                  else "positive_alpha" if bps >= 0
                  else "structurally_broken" if bps < _MATERIAL_FAIL_BPS
                  else "marginal_fair")
    # Kelly-based sizing options (% of capital), pre-computed so the PM never does the arithmetic.
    kelly = ((r.get("desk_metrics") or {}).get("pm") or {}).get("kelly_fraction")
    kelly_sizing = ({"full_kelly_pct": round(kelly * 100), "half_kelly_pct": round(kelly * 50),
                     "quarter_kelly_pct": round(kelly * 25),
                     "note": "full Kelly over-bets a single name — desks typically size at HALF or QUARTER "
                             "Kelly, then cut further for the instantaneous_mtm / liquidity risk"}
                    if kelly is not None else None)
    # Capital-lockup estimate: to expiry, +~one roll cycle if an early breach looks likely.
    cushion, exp_move = r.get("cushion_pct"), _expected_move_pct(r)
    breach_prone = exp_move is not None and cushion is not None and cushion < exp_move
    # Early-exercise (dividend) risk: a SHORT CALL is exercised early if ex-div is before expiry
    # and its extrinsic value has decayed below the dividend — the holder grabs the dividend.
    exdiv, div_amt = meta.get("exdiv"), meta.get("div_amount")
    ex_before = bool(exdiv and r.get("expiration") and exdiv <= r.get("expiration"))
    short_call_extr, danger = None, False
    for l in (r.get("legs") or []):
        if str(l.get("action", "")).upper().startswith("S") and str(l.get("type", "")).upper().startswith("C"):
            extr = round((l.get("mid") or 0.0) - max(0.0, spot - float(l.get("strike") or 0)), 2)
            short_call_extr = extr if short_call_extr is None else min(short_call_extr, extr)
            if ex_before and div_amt is not None and extr < div_amt:
                danger = True
    return {
        "execution": {
            "worst_leg_bid_ask_spread_pct": worst_spread,
            "note": "a wide spread makes exiting into a post-event panic costly",
        },
        "capital_shock_10pct_move": {
            "capital_profile": capital_profile,
            "margin_requirement_shock_pct": 0.0,        # collateralized / defined-risk / covered → no margin call
            "terminal": {
                "pnl_down_10pct": stress["down"], "down_pct_of_capital": _pctcap(stress["down"]),
                "pnl_up_10pct": stress["up"], "up_pct_of_capital": _pctcap(stress["up"]),
                "basis": "P&L at EXPIRY if spot SETTLES ±10% away",
            },
            "instantaneous_mtm": {
                "pnl_down_10pct": inst_down, "down_pct_of_capital": _pctcap(inst_down),
                "pnl_up_10pct": inst_up, "up_pct_of_capital": _pctcap(inst_up),
                "vol_shock_pts": {"down": 12, "up": 8},
                "basis": "mark-to-market TOMORROW if spot GAPS ±10% with full DTE remaining and IV shocked up "
                         "— captures short-gamma/vega paper loss and margin stress (what breaks margin, not "
                         "where it settles in 32d)",
            },
            "note": "Interpret by capital_profile, and judge SEVERITY by down_pct_of_capital — NEVER the "
                    "nominal dollar figure (a large negative $ is not a large risk when capital is large). For "
                    "cash_secured / covered / defined_risk the collateral (or defined max loss) is FULLY posted "
                    "(margin_requirement_shock_pct = 0): the instantaneous_mtm is a PAPER drawdown (holding "
                    "pain / voluntary-exit cost), NOT a forced margin liquidation, and a cash-secured put's "
                    "mark largely REVERSES by expiry if the strike holds. On a 10% underlying gap a "
                    "SINGLE-DIGIT down_pct_of_capital (a drawdown SMALLER than the gap) means the cushion + "
                    "premium absorbed the move — the position lost LESS than owning the stock would have: that "
                    "is strong capital PRESERVATION, not a vulnerability. Reserve 'major vulnerability' for a "
                    "DOUBLE-DIGIT % drawdown or a positive margin_requirement_shock_pct. Any forced-liquidation "
                    "warning must come ONLY from margin_requirement_shock_pct; the real tail is cvar_95 + "
                    "assignment, not this ±10% paper mark.",
        },
        "volatility_skew": {
            "skew_slope_90_110_pts": meta.get("skew_pts"),
            "short_strike_iv_premium_over_atm_bps": ss_iv_prem_bps,
            "skew_regime": skew_regime,     # normal <700 | elevated 700-1500 | extreme >=1500 bps
            "note": "a steep short-strike IV premium over ATM defends selling THIS strike even when ATM IV "
                    "looks cheap vs HV — BUT extreme skew (elevated/extreme regime) can mean the market is "
                    "pricing a KNOWN tail event: it is edge to sell ONLY if it is STRUCTURAL skew, not a "
                    "priced-in disaster (check events_before_expiry) or an illiquid / unreliable quote (check "
                    "the leg's oi/volume and stressed_bid_ask_spread_pct).",
        },
        "moneyness": {
            "cushion_pct_otm_reported": r.get("cushion_pct"),    # the HEADLINE cushion = the FAR short leg only
            "nearest_short_cushion_pct": nearest_short_cushion,  # the BINDING short leg's distance from spot
            "short_strike_sigmas_from_spot": short_sigmas,       # each short leg's distance in 1σ expected-move units
            "nearest_short_sigmas": nearest_short_sigmas,        # < ~0.5 = the trade has an AT-THE-MONEY short leg
            "prob_max_profit_pct": prob_max_profit,              # FULL credit (every short leg OTM), from short-leg deltas
            "note": "cushion_pct_otm reports the FAR short leg; nearest_short_cushion_pct / nearest_short_sigmas "
                    "are the BINDING one. A short leg within ~0.5σ is effectively AT-THE-MONEY — a DIRECTIONAL "
                    "bet, NOT cushioned; do NOT call the trade 'cushioned' off the far leg alone. keep_prob_pct "
                    "is a DOWNSIDE (put-not-assigned) number; prob_max_profit_pct is the probability of the FULL "
                    "credit (EVERY short leg expires OTM). For a near-ATM short leg these diverge sharply — the "
                    "headline max_profit is far from certain, so cite prob_max_profit_pct and expected_value.",
        },
        "liquidity_capacity": {
            "thinnest_leg_1lot_vs_today_volume_pct": thin_1lot_pct,
            "bid_ask_widening_multiplier_at_stress": widen_mult,
            "stressed_bid_ask_spread_pct": stressed_spread,
            "basis": ("multiplier " + ("2.5x (earnings before expiry)" if meta.get("earnings_before")
                      else "1.5x (no binary event)") + "; stressed spread = worst leg × multiplier "
                      "(forced-liquidation slippage)"),
        },
        "early_exercise": {
            "ex_div_before_expiry": ex_before,
            "danger": danger,                          # only meaningful for structures with a SHORT CALL
            "short_call_extrinsic": short_call_extr,
            "dividend_amount": div_amt,
            "note": "TRUE for a short call when ex-div is before expiry and its extrinsic < the dividend "
                    "(the holder exercises early to capture the dividend)",
        },
        "pin_risk": {
            "nearest_max_oi_strike": max_oi_strike,
            "cushion_pts": pin_cushion,
            "note": "distance from the short strike to the largest-open-interest strike (gamma-pin magnet into expiry)",
        },
        "capital_allocation": {
            "net_expected_return_vs_sofr_bps": bps,
            "bps_regime": bps_regime,                   # positive_alpha | marginal_fair | structurally_broken
            "structurally_broken": (bps is not None and bps < _MATERIAL_FAIL_BPS),        # HARD reject — no VRP override
            "requires_vrp_override": (bps is not None and _MATERIAL_FAIL_BPS <= bps < 0),  # marginal — needs a RICH VRP or pass
            "bps_basis": ("net_expected_return_vs_sofr_bps is a RISK-NEUTRAL (Q-measure) EV minus SOFR — under "
                          "no-arbitrage a slightly-negative value is the NORMAL cost of fair pricing, NOT alpha "
                          "and NOT grounds to reject. The real edge is the VOLATILITY RISK PREMIUM (P-measure): "
                          "sell only when IV is rich vs realized — iv_hv_ratio > ~1, elevated iv_rank/iv_percentile, "
                          "steep short_strike_iv_premium_over_atm_bps. bps_regime: marginal_fair (bps >= -75) = "
                          "fairly priced → EXECUTE only on a genuinely RICH VRP (the override), else pass; "
                          "structurally_broken (bps < -75) = a HARD reject (toxic liquidity/skew) NO override can "
                          "save; positive_alpha (bps >= 0) = rare, clears on its own. (Collateral earns ~SOFR "
                          "natively, so this is the alpha ON TOP — do not treat headline premium as free money.)"),
            "kelly_sizing": kelly_sizing,               # full/half/quarter Kelly as % of capital (PM sizing input)
            "max_capital_lockup_days_est": dte + (30 if breach_prone else 0),
            "lockup_basis": ("days to expiry" + (" + ~30d defensive roll (early breach likely: cushion < 1σ move)"
                             if breach_prone else " (cushion ≥ 1σ move — early breach unlikely)")),
        },
    }


def _candidate_json(i: int, r: dict, meta: dict) -> dict:
    """One candidate trade — every pre-computed metric, greek and leg, structured."""
    dm = r.get("desk_metrics") or {}
    q = dm.get("quant") or {}
    max_oi_strike = (meta["exp_meta"].get(r.get("expiration")) or {}).get("max_oi_strike")
    return {
        "id": i + 1,
        "structure": r.get("structure"),
        "label": r.get("label"),
        "expiration": r.get("expiration"),
        "dte": r.get("dte"),
        "strikes": _strikes_json(r),
        "short_strike_pct_from_spot": r.get("short_strike_pct"),
        "cushion_pct_otm": r.get("cushion_pct"),        # short strike distance from spot — bigger = safer
        "desk_score_0_100": r.get("desk_score"),
        "algo_rank": i + 1,
        "algo_grade": r.get("algo_grade"),              # A–F after the full deterministic overlay
        "approval_odds": r.get("approval_odds"),        # high | medium | low | auto_reject (LLM-approval likelihood)
        "algo_demerits": r.get("grade_demerits") or [], # every deterministic mark AGAINST the trade
        "algo_blocking": r.get("grade_blocking") or [], # hard fails — a graded trade that reached you should have none
        "algo_quant": {"score": q.get("score"), "verdict": q.get("verdict"), "reasons": q.get("reasons")},
        "ta_alignment": r.get("ta_note") or None,
        "pricing": {"net_premium": r.get("premium"), "premium_annualized_pct": r.get("premium_annualized_pct"),
                    "sofr_hurdle_pct": r.get("sofr_pct"), "max_profit": r.get("max_profit"),
                    "max_loss": r.get("max_loss"), "collateral": r.get("collateral")},
        "probability": {"keep_prob_pct": r.get("prob_keep_pct")},
        "volatility": {"atm_iv_pct": r.get("atm_iv_pct"), "iv_hv_ratio": r.get("iv_hv_ratio"),
                       "expected_move_pct_1sigma": _expected_move_pct(r)},   # 1σ implied move to expiry
        "pm_ratios": dm.get("pm") or {},                # Omega/Sortino/Calmar/EV/Kelly/expected_return
        "risk": dm.get("risk") or {},                   # VaR95/CVaR95/max_loss/capital
        "net_greeks": dm.get("trader") or {},           # Δ/Γ/Θ/ν + Vanna/Charm/Volga + avg_iv
        "institutional": _candidate_extra(r, meta, max_oi_strike),
        "flags": [f.get("text") for f in (r.get("flags") or [])],
        # WATCH→DEFEND→EXIT price ladder from the TA levels + trade geometry — the pre-committed management
        # plan the desk should endorse/refine (each rung: side, tier, price, pct_from_spot, atr_units, action).
        "management": {"risk_triggers": r.get("risk_triggers") or []},
        "legs": r.get("legs") or [],                    # per-leg bid/ask/spread/iv/oi/vol/greeks/prob_reach
    }


def _reject_reason(r: dict) -> str:
    """One line the desk can dismiss a lower-ranked trade on — the algo grade's blocking/demerits first."""
    graded = (r.get("grade_blocking") or []) + (r.get("grade_demerits") or [])
    if graded:
        return "; ".join(graded[:2])
    dm = r.get("desk_metrics") or {}
    pm, rk = dm.get("pm") or {}, dm.get("risk") or {}
    omega, ev, kelly = pm.get("omega"), pm.get("expected_value"), pm.get("kelly_fraction")
    cvar, cap = rk.get("cvar_95"), rk.get("capital")
    out = []
    if omega is not None and omega < 1.0:
        out.append(f"Omega {omega} < 1 (negative edge)")
    if ev is not None and ev <= 0:
        out.append("expected value ≤ 0")
    if cvar and cap and cvar >= cap * 0.99:
        out.append("CVaR ≈ 100% of capital (full-capital tail for a thin credit)")
    if not out and kelly is not None and kelly <= 0.01:
        out.append("Kelly ~0 (no sizing edge)")
    if not out:
        if r.get("structure") == "covered_call" and (r.get("premium_annualized_pct") or 0) < 8:
            out.append("premium yield barely beats SOFR while carrying full equity downside")
        else:
            out.append("weak risk-adjusted edge vs the top-ranked trades")
    return "; ".join(out[:2])


def _candidate_condensed(i: int, r: dict) -> dict:
    """Slim one-liner for lower-ranked candidates (beyond the top 5) — enough for the desk to
    see the full opportunity set (and WHY it ranked low) without the full-detail token cost."""
    dm = r.get("desk_metrics") or {}
    return {
        "id": i + 1,
        "structure": r.get("structure"),
        "strikes": _strikes_json(r),
        "expiration": r.get("expiration"),
        "dte": r.get("dte"),
        "desk_score_0_100": r.get("desk_score"),
        "algo_grade": r.get("algo_grade"),
        "approval_odds": r.get("approval_odds"),
        "algo_verdict": (dm.get("quant") or {}).get("verdict"),
        "reject_reason": _reject_reason(r),
        "keep_prob_pct": r.get("prob_keep_pct"),
        "cushion_pct_otm": r.get("cushion_pct"),
        "premium_annualized_pct": r.get("premium_annualized_pct"),
        "omega": (dm.get("pm") or {}).get("omega"),
        "cvar_95": (dm.get("risk") or {}).get("cvar_95"),
    }


_TOP_N_FULL = 5     # full institutional detail for the top-ranked candidates; the rest go condensed


def _desk_payload(desk: dict, focus_index: Optional[int] = None) -> dict:
    """The structured input for the LLM desk — pricing, bid/ask, greeks, strategy legs, technical
    analysis, market structure, volume profile and every algorithmic metric, as JSON.

    Two modes:
      • ranking (focus_index=None): the top `_TOP_N_FULL` candidates carry full detail, the rest go
        condensed in `also_ranked` — the desk picks the best.
      • single-trade (focus_index set, Desk Review v2): ONLY ranked[focus_index] is the full
        `candidates[0]`; every OTHER trade is condensed in `also_ranked` (context) — the desk rules
        EXECUTE/REJECT on that one trade."""
    ranked = desk.get("ranked") or []
    vs = desk.get("vol_stats") or {}
    corp = desk.get("corporate_actions") or {}
    meta = {
        "spot": desk.get("spot") or 0.0,
        "sofr_pct": desk.get("sofr_pct") or 0.0,
        "atm_iv": vs.get("iv_atm_pct"),
        "skew_pts": vs.get("skew_pts"),
        "earnings_before": any(e.get("kind") == "earnings" for e in (desk.get("events") or [])),
        "exp_meta": desk.get("expiry_meta") or {},
        "exdiv": corp.get("next_ex_dividend_date"),
        "div_amount": corp.get("dividend_amount"),
    }
    single = focus_index is not None and 0 <= focus_index < len(ranked)
    top, rest = ranked[:_TOP_N_FULL], ranked[_TOP_N_FULL:]
    mode_note = ("SINGLE-TRADE REVIEW: candidates[0] is THE one trade under review — rule EXECUTE / REJECT / "
                 "EXECUTE_MODIFIED on IT specifically. also_ranked[] is context only; do NOT switch to another "
                 "trade unless candidates[0] is uncompensated AND a listed alternative strictly resolves it.\n"
                 if single else "")
    payload = {
        "underlying": {"ticker": desk.get("ticker"), "spot": desk.get("spot"),
                       "sofr_hurdle_pct": desk.get("sofr_pct"), "as_of": desk.get("as_of"),
                       # IV rank / vol rank / IV percentile / skew (dir + pts) / term structure
                       "volatility": vs,
                       "corporate_actions": corp,           # ex-div date + dividend yield (early-exercise input)
                       "portfolio_fit": desk.get("portfolio_fit")},   # 1y beta & correlation vs SPY

        "reading_notes": (mode_note + "All values are PRE-COMPUTED and authoritative — do NOT recompute or invent any "
                          "number or strike. Recommend ONLY a candidate from candidates[] (the detailed top "
                          f"{_TOP_N_FULL}) by its exact strikes. cushion_pct_otm = short strike's distance from "
                          "spot (bigger = safer, smaller tail). candidates[] is the algorithmic ranking; id 1 = "
                          "algo #1 (one input, not the answer). PRE-VETTING: every candidate carries an "
                          "`algo_grade` (A–F), `approval_odds` and `algo_demerits` from a FULL deterministic "
                          "screen (VRP, moneyness/near-ATM, skew, liquidity, tail, systemic beta, event "
                          "density) — the top-ranked trade has already CLEARED every mechanical filter and its "
                          "`algo_blocking` is empty, so your job is to CONFIRM it or find a genuinely "
                          "QUALITATIVE reason it should not be approved (do not re-litigate the mechanical "
                          "screen). Each candidate.institutional carries execution "
                          "slippage, the ±10% capital shock (TERMINAL vs INSTANTANEOUS overnight MTM), per-strike "
                          "skew premium, stressed liquidity, early-exercise (dividend) danger, gamma-pin "
                          "cushion, edge-vs-SOFR in bps and the capital-lockup estimate. underlying."
                          "corporate_actions (ex-div/yield) and underlying.portfolio_fit (beta & correlation vs "
                          "SPY — systemic vs idiosyncratic) are ticker-level. also_ranked[] lists lower-ranked "
                          "trades condensed, each with a reject_reason for effortless dismissal.\n"
                          "CAPITAL EFFICIENCY & THE VOL RISK PREMIUM: capital_allocation.net_expected_return_vs_"
                          "sofr_bps is a RISK-NEUTRAL EV vs SOFR — under no-arbitrage a slightly-negative value "
                          "is NORMAL (the cost of fair pricing), NOT alpha and NOT grounds to reject. The real "
                          "edge is the VOLATILITY RISK PREMIUM: sell only when IV is rich vs realized "
                          "(iv_hv_ratio > ~1, high iv_rank, steep short_strike_iv_premium_over_atm_bps). Use "
                          "bps_regime: marginal_fair (bps >= -75) → EXECUTE only if the VRP is genuinely rich "
                          "(the override), else pass; structurally_broken (bps < -75) → HARD reject (toxic "
                          "liquidity/skew), NO override; positive_alpha → clears on its own. Do NOT treat a small "
                          "negative bps as capital destruction, and do NOT use a VRP override to buy a "
                          "structurally_broken trade."),
        "events_before_expiry": desk.get("events") or [],
        "technical_analysis": _ta_json(desk.get("ta") or {}),
        "algo_top_pick_id": 1 if ranked else None,
    }
    if single:                                              # Desk Review v2 — one trade in full, the rest as context
        payload["candidates"] = [_candidate_json(focus_index, ranked[focus_index], meta)]
        payload["also_ranked"] = [_candidate_condensed(i, r) for i, r in enumerate(ranked) if i != focus_index]
    else:
        payload["candidates"] = [_candidate_json(i, r, meta) for i, r in enumerate(top)]
        if rest:
            payload["also_ranked"] = [_candidate_condensed(_TOP_N_FULL + j, r) for j, r in enumerate(rest)]
    return payload


async def _term_structure_probe(ticker: str, spot: float, front_dte: Optional[int],
                                front_iv_pct: Optional[float], quote_source: str,
                                user: Optional["User"], db: Optional["AsyncSession"]) -> Optional[dict]:
    """Probe ONE back-month expiry's ATM IV so the desk always knows contango vs backwardation
    (front IV richer than back = event/earnings inversion → capture the front, favor shorter DTE).
    On-demand only (desk review), so a second chain fetch is acceptable."""
    if not front_dte or front_iv_pct is None:
        return None
    try:
        from .quote_providers import get_provider
        provider = get_provider(quote_source, user=user, db=db)
        exps = await provider.get_option_expirations(ticker)
        today = date.today()
        back = None
        for e in exps:
            try:
                d = (date.fromisoformat(e) - today).days
            except ValueError:
                continue
            if d >= front_dte + 20:                    # a genuine back month vs the front
                back = (e, d)
                break
        if not back:
            return None
        exp, bdte = back
        chain = await provider.get_option_chain(ticker, exp)
        ivs = [q.iv for q in chain.quotes if q.iv and abs(q.strike - spot) <= spot * 0.03]
        if not ivs:
            return None
        back_iv = round(sum(ivs) / len(ivs) * 100, 1)
        diff = round(back_iv - front_iv_pct, 1)        # back minus front
        state = "backwardation" if diff < -0.5 else "contango" if diff > 0.5 else "flat"
        return {"state": state, "front_dte": front_dte, "front_iv_pct": front_iv_pct,
                "back_dte": bdte, "back_iv_pct": back_iv, "back_minus_front_pts": diff,
                "note": "backwardation (front IV > back) = event/earnings panic → favor shorter DTE",
                "source": "second-expiry probe"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("term-structure probe failed for %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Deterministic ranking (no LLM)
# ---------------------------------------------------------------------------

async def rank_desk(
    ticker: str,
    target_dte: Optional[int] = None,
    min_prob: float = 0.85,
    min_income: float = 20.0,
    structures: Optional[list[str]] = None,
    quote_source: str = "yfinance",
    user: Optional["User"] = None,
    db: Optional["AsyncSession"] = None,
    target_expiration: Optional[str] = None,
    focus: Optional[dict] = None,
    owns_underlying: bool = False,
) -> dict:
    """Rank ALL candidate income trades (best → worst) by a blended desk score:
    the algorithmic Quant 0–100 score adjusted for technical/regime alignment.

    ``focus`` injects the caller's exact placed trade as a candidate so lifecycle
    scoring works even when its strike/expiry is off the scan grid."""
    ticker = _norm_ticker(ticker)
    # Fetch the TECHNICAL read FIRST so the scan can bias multi-leg SHORT strikes toward real structural
    # levels (S/R, value area, dealer gamma walls/flip). The SAME ta/gex/portfolio_fit are handed to
    # _finalize_desk so the read happens ONCE — no double fetch, same total latency (already sequential).
    ta, portfolio_fit, gex = await asyncio.gather(
        asyncio.to_thread(_ta_sync, ticker),
        asyncio.to_thread(_portfolio_fit_sync, ticker),
        asyncio.to_thread(_gex_sync, ticker),
    )
    scan = await run_derivative_income(
        ticker, target_dte=target_dte, min_prob=min_prob, min_income=min_income,
        structures=structures, quote_source=quote_source, user=user, db=db,
        target_expiration=target_expiration, focus=focus, owns_underlying=owns_underlying,
        ta_levels=_structural_levels(ta, gex),
    )
    if scan.get("error"):
        return {"error": scan["error"]}
    return await _finalize_desk(scan, scan.get("opportunities", []), ticker,
                                quote_source, owns_underlying, user, db, target_dte,
                                collapse_strikes=True, ta=ta, portfolio_fit=portfolio_fit, gex=gex)


# ── Risk triggers — the price-level management plan ──────────────────────
def _risk_triggers(opp: dict, spot: Optional[float], ta: dict, phys_vol: Optional[float] = None) -> list[dict]:
    """WATCH → DEFEND → EXIT price levels, each with the TA reason. The rungs track the UNDERLYING'S
    STRUCTURE deteriorating toward — but NOT at — the short strike: a deep-OTM short put must be defended
    when a lower structure forms on the chart, not 30% away at the strike itself (by then it's far too
    late). Levels snap to the real technical read (support/resistance, value area, order blocks) and fall
    back to volatility (σ / measured-move) bands where the chart has no listed level; imminence is sized in
    ATR and σ. Near-the-money trades keep the intuitive strike-anchored ladder."""
    if not spot or spot <= 0:
        return []
    inst = (ta or {}).get("institutional") or {}
    vp = inst.get("volume_profile") or {}
    obs = inst.get("order_blocks") or []
    atr_pct = ta.get("_atr_pct")
    atr_abs = spot * (atr_pct / 100.0) if atr_pct else None
    mu = ta.get("_drift_mu") or 0.0
    trend = "up-trend" if mu > 0.05 else "down-trend" if mu < -0.05 else "range-bound tape"
    struct = opp.get("structure")
    credit = float(opp.get("premium_per_share") or 0.0)
    dte = int(opp.get("dte") or 30)
    iv = opp.get("atm_iv_pct")
    horizon = math.sqrt(max(dte, 1) / 365.0)
    # 1σ expected move to expiry — take the WIDER of implied and physical (realized/ATR) so a crushed or
    # MISSING IV can't collapse the ladder to a meaningless ±1% band (the GLD covered-call bug). phys_vol is
    # the gap-aware max(HV, ATR-vol) the desk already computes; ATR is the last-ditch realized proxy.
    moves = []
    if iv and float(iv) > 0:
        moves.append(spot * (float(iv) / 100.0) * horizon)
    if phys_vol and phys_vol > 0:
        moves.append(spot * float(phys_vol) * horizon)
    if atr_abs:
        moves.append((atr_abs / 1.4) * math.sqrt(max(dte, 1)))     # ATR → daily σ, scaled to the horizon
    em1 = max(moves) if moves else spot * 0.05
    em1 = max(em1, spot * 0.10 * horizon)                # floor at ~10% annualized vol — never a noise band

    def levels(direction: str) -> list[tuple]:
        out: list[tuple] = []
        if direction == "down":
            for v, lbl in ((ta.get("supportLevel"), "support"), (vp.get("val"), "value-area low"),
                           (vp.get("poc"), "point of control")):
                if v and float(v) < spot:
                    out.append((round(float(v), 2), lbl))
            out += [(round(float(o["bottom"]), 2), f"{o.get('type', '') or ''} order block".strip())
                    for o in obs if o.get("bottom") and float(o["bottom"]) < spot]
            return sorted({p: l for p, l in out}.items(), key=lambda x: -x[0])
        for v, lbl in ((ta.get("resistanceLevel"), "resistance"), (vp.get("vah"), "value-area high"),
                       (vp.get("poc"), "point of control")):
            if v and float(v) > spot:
                out.append((round(float(v), 2), lbl))
        out += [(round(float(o["top"]), 2), f"{o.get('type', '') or ''} order block".strip())
                for o in obs if o.get("top") and float(o["top"]) > spot]
        return sorted({p: l for p, l in out}.items(), key=lambda x: x[0])

    trg: list[dict] = []

    def emit(side, tier, price, basis, action, why):
        if price is None or price <= 0:
            return
        trg.append({"side": side, "tier": tier, "price": round(float(price), 2),
                    "pct_from_spot": round((price - spot) / spot * 100, 1),
                    "atr_units": round(abs(spot - price) / atr_abs, 1) if atr_abs else None,
                    "sigma": round(abs(spot - price) / em1, 1), "basis": basis, "action": action, "why": why})

    def build(side: str, short: float, covered: bool = False):
        down = side == "down"
        lv = levels(side)
        toward = (lambda p: spot - p) if down else (lambda p: p - spot)   # +ve = toward the SHORT strike (danger)
        px_at = (lambda s: round(spot - s * em1, 2)) if down else (lambda s: round(spot + s * em1, 2))
        pct = lambda p: round((p - spot) / spot * 100, 1)
        sig = lambda p: round(toward(p) / em1, 1)
        roll = "down-and-out" if down else "up-and-out"
        lower = "lower low" if down else "higher high"
        sr = "support" if down else "resistance"
        anchor = f"your {short} strike"
        be = round(short - credit, 2) if down else round(short + credit, 2)
        strike_sigma = toward(short) / em1

        def snap(target, tol=0.6):
            c = [(p, l) for p, l in lv if abs(p - target) <= tol * em1]
            return min(c, key=lambda x: abs(x[0] - target)) if c else None

        if covered:
            # COVERED CALL — the ADVERSE move is UP toward the short call (exercise = shares called away).
            # DOWN is GOOD for the trade (the call decays, you keep premium + shares), so there is NO downside
            # ladder. And there's no upside TAIL: being called away is the capped MAX GAIN, and the Defend roll
            # already carries the only decision — so it stops at Watch → Defend (no redundant rung at the strike).
            res = [(p, l) for p, l in lv if 0 < toward(p) < toward(short)]   # resistances below the strike
            w = res[0] if res else (px_at(0.4), "≈0.4σ up — early rally")
            d = (round(short - 0.5 * em1, 2), "roll zone — just below the strike")
            if toward(d[0]) <= toward(w[0]):
                d = (round((w[0] + short) / 2, 2), "mid-way to the strike")
            emit(side, "watch", w[0], w[1], "Rally underway — the call is starting to be tested.",
                 f"First resistance / ~{sig(w[0])}σ up. Pushing through it is the first sign the stock is heading for the {short} cap — the call is still OTM, but the shares could get called away.")
            emit(side, "defend", d[0], d[1], "Approaching the cap — roll the call UP-and-out to keep the shares & more upside.",
                 f"~{sig(d[0])}σ up, closing on the {short} strike: the call is going at-the-money and assignment turns live. Roll up-and-out here if you'd rather keep the stock and its upside than be capped (being called away is your capped MAX gain — not a loss).")
            return

        if strike_sigma <= 1.5:
            # NEAR-THE-MONEY — the strike itself is the near-term battleground (intuitive ladder).
            w = next(((p, l) for p, l in lv if 0 < toward(p) < toward(short)), None) or (px_at(0.4), "≈0.4σ band")
            emit(side, "watch", w[0], w[1], "Cushion eroding — tighten monitoring; add no new size.",
                 f"First {sr} between spot and {anchor}; the strike is only {abs(strike_sigma):.1f}σ away, so a break here quickly puts it in play.")
            emit(side, "defend", short, "short strike",
                 f"Short strike tested — roll {roll} or close half to cut delta.",
                 f"At the strike the option goes at-the-money — delta and gamma spike and assignment risk turns real. With the strike just {abs(strike_sigma):.1f}σ out, this is the genuine defend line.")
            deeper = [(p, l) for p, l in lv if toward(p) > toward(short)]
            e = deeper[0] if deeper else (be, "credit breakeven")
            emit(side, "exit", e[0], e[1], "Break beyond breakeven — close to cap the tail.",
                 f"Past {be} the trade is in real loss and structure has broken; cut before assignment builds.")
            return

        # FAR-OTM — anchor to STRUCTURE / σ bands, NOT the distant strike.
        w = snap(px_at(0.8)) or (px_at(0.8), "≈0.8σ measured move")
        d = snap(px_at(1.6)) or (px_at(1.6), "≈1.6σ measured move")
        e = snap(px_at(2.5)) or (px_at(2.5), "≈2.5σ measured move")
        if toward(d[0]) <= toward(w[0]):                 # keep depth strictly increasing
            d = (px_at(1.6), "≈1.6σ measured move")
        if toward(e[0]) <= toward(d[0]):
            e = (px_at(2.5), "≈2.5σ measured move")
        if toward(e[0]) > toward(be):                     # never place EXIT past breakeven (max-loss zone)
            e = (be, "credit breakeven")

        strike_txt = f"the strike is still {abs(pct(short)):.0f}% away"
        emit(side, "watch", w[0], w[1], "Trend wobbling — watch the tape closely; add no new size.",
             f"Nearest {sr} (~{sig(w[0])}σ). In the current {trend} a probe here is normal, but a decisive break is the FIRST hint a move that could reach {anchor} is starting — {strike_txt}.")
        emit(side, "defend", d[0], d[1],
             f"New {'lower' if down else 'higher'} structure forming — act now: roll {roll} or cut size.",
             f"~{sig(d[0])}σ and through {d[1]}. A close beyond prints a {lower} — a change of character out of the {trend}. {dte}d to expiry leaves room for the move to extend to {anchor}; defend the STRUCTURE break here, not at the far strike.")
        emit(side, "exit", e[0], e[1], "Structure decisively broken — close before the strike comes into play.",
             f"~{sig(e[0])}σ with the trend rolled over and momentum now pointing at {anchor}. Exit here — you never ride a broken thesis to assignment.")

    # The ladder tracks the underlying moving TOWARD the short strike (where it's exercised/assigned) — the
    # adverse direction for a premium seller: DOWN for short puts, UP for short calls. A covered call is a
    # SHORT CALL → up-side only (a falling stock is GOOD for it), with covered-call framing.
    put_short = opp.get("put_short") or (opp.get("short_strike")
                    if struct in ("cash_secured_put", "put_credit_spread") else None)
    if put_short:
        build("down", float(put_short))

    call_short = opp.get("call_short") or (opp.get("short_strike")
                    if struct in ("call_credit_spread", "covered_call") else None)
    if call_short:
        build("up", float(call_short), covered=(struct == "covered_call"))

    return trg


# ── Live monitor plan — the REAL advanced-TA read at the trade's danger levels ──────────────
# On-demand (fetched when the user opens "Monitoring & Corrective Action"). It fuses the SAME four
# institutional reads the strike selection leaned on — microstructure (MTF volume profile, naked POCs,
# AVWAP), market structure (order blocks, FVGs, liquidity pools, swings), regime (50-day VWAP) and dealer
# positioning (gamma flip, call/put walls, expected move) — and maps the REAL structures onto the trade's
# short strikes. No hard-coded σ bands: every rung is a named level that says what the SETUP does if price
# reaches it, and the corrective action. Goal = manage/avoid the tail.
_MON_IMPACT = {
    "gamma_flip": "crossing the gamma flip turns dealers SHORT gamma — their hedging then AMPLIFIES the move (vol expands)",
    "call_wall":  "the call wall is the dealer-gamma ceiling; a close above pulls the magnet and opens air",
    "put_wall":   "the put wall is dealer-gamma support; below it that hedging cushion is gone",
    "pool":       "a liquidity pool — resting stops sit here; a sweep through it accelerates the move",
    "naked_poc":  "an untested (naked) POC — price is drawn to it and it often gives way once tagged",
    "ob":         "an order block — the last supply/demand imprint; a close through it cedes control",
    "swing":      "a market-structure swing — losing it prints a lower low / higher high (change of character)",
    "avwap":      "an anchored VWAP — a shared cost basis that tends to hold on the first test",
    "vwap50":     "the 50-day VWAP — the swing mean the tape reverts to",
    "vp":         "a volume-profile edge — acceptance beyond it signals a value migration",
    "fvg":        "an unfilled fair-value gap — an imbalance that tends to fill",
    "expected_move": "the dealer expected-move fence — the market's own 1σ boundary for this tenor",
}
_MON_SIG = {"gamma_flip": 3.0, "call_wall": 2.6, "put_wall": 2.6, "naked_poc": 2.2, "ob": 2.2, "pool": 2.0,
            "swing": 1.8, "avwap": 1.7, "vwap50": 1.7, "expected_move": 1.6, "vp": 1.4, "fvg": 1.2}
_MON_CTX_CACHE: dict = {}          # ticker → (ts, ctx); in-process TTL cache for the 4 heavy reads
_MON_TTL = 600


def _mon_family(source: str) -> str:
    for pre in ("vp", "ob", "fvg", "pool", "swing"):
        if source.startswith(pre + "_"):
            return pre
    return source                  # naked_poc / avwap / vwap50 / gamma_flip / call_wall / put_wall / expected_move


def _gather_monitor_context(ticker: str) -> dict:
    """Run the four advanced-TA reads (parallel) → the REAL tagged levels + gamma context — the same
    fusion the Trade-Setup engine and the strike selection lean on. Best-effort; degrades gracefully."""
    from concurrent.futures import ThreadPoolExecutor
    import yfinance as yf
    from .trade_setup_service import _collect_levels, _atr_from_structure
    from .microstructure_service import compute_microstructure
    from .market_structure_service import compute_market_structure
    from .regime_service import compute_regime
    from .dealer_positioning_service import compute_dealer_positioning
    stock = yf.Ticker(_norm_ticker(ticker))
    with ThreadPoolExecutor(max_workers=4) as ex:
        fm = ex.submit(compute_microstructure, stock); fs = ex.submit(compute_market_structure, stock)
        fr = ex.submit(compute_regime, stock); fd = ex.submit(compute_dealer_positioning, stock)
        micro, structure, regime, dealer = fm.result(), fs.result(), fr.result(), fd.result()
    spot = None
    for src in (structure, regime, micro, dealer):
        if src and src.get("price"):
            spot = float(src["price"]); break
    walls = (dealer or {}).get("walls") or {}
    ng = (dealer or {}).get("net_gex") or {}
    return {
        "spot": spot, "atr": _atr_from_structure(structure) if structure else None,
        "levels": _collect_levels(micro, structure, regime, dealer, spot) if spot else [],
        "gamma_flip": ((dealer or {}).get("gamma_flip") or {}).get("level"),
        "gamma_sign": ng.get("sign") or ng.get("regime") or ng.get("label"),
        "call_wall": (walls.get("call_wall") or {}).get("strike"),
        "put_wall": (walls.get("put_wall") or {}).get("strike"),
        "regime": (regime or {}).get("overall") or (regime or {}).get("label"),
    }


def _cluster_side(levels: list[dict], spot: float, atr: float, down: bool) -> list[dict]:
    """Real levels on the danger side of spot, merged into confluence clusters, NEAREST-to-spot first."""
    picked = [L for L in levels if L.get("price") and ((L["price"] < spot) if down else (L["price"] > spot))]
    picked.sort(key=lambda L: L["price"], reverse=down)      # nearest to spot first
    tol = max((atr or 0) * 0.4, spot * 0.004)
    clusters: list[dict] = []
    for L in picked:
        if clusters and abs(L["price"] - clusters[-1]["_mean"]) <= tol:
            c = clusters[-1]; c["members"].append(L)
            c["_mean"] = sum(m["price"] for m in c["members"]) / len(c["members"])
        else:
            clusters.append({"members": [L], "_mean": L["price"]})
    out = []
    for c in clusters:
        head = max(c["members"], key=lambda m: _MON_SIG.get(_mon_family(m["source"]), 1.0))
        labels: list[str] = []
        for m in c["members"]:
            if m["label"] not in labels:
                labels.append(m["label"])
        out.append({"price": round(c["_mean"], 2), "labels": labels, "headline": head["label"],
                    "family": _mon_family(head["source"]),
                    "significance": round(max(_MON_SIG.get(_mon_family(m["source"]), 1.0) for m in c["members"]), 1)})
    return out


def build_monitor_plan(structure: str, put_short, call_short, credit: float, spot: float,
                       dte: int, ctx: dict) -> dict:
    """Deterministic ladder from the REAL levels — one escalating plan per exposed side, toward the short
    strike (down for short puts, up for short calls), each rung a named structure + setup impact + action."""
    levels = ctx.get("levels") or []
    atr = ctx.get("atr") or (spot * 0.015)
    triggers: list[dict] = []
    rationale: list[str] = []
    au = lambda px: round(abs(spot - px) / atr, 1) if atr else None
    pc = lambda px: round((px - spot) / spot * 100, 1)
    # 1σ expected move to expiry (gap-aware ATR) — grades HOW FAR the strike is, i.e. how likely assignment
    # is by expiry. A strike many σ away is structurally safe: near-spot structures are early warnings, not
    # "defend the trade" lines. (This is what a −54.8% put needs — nothing at −9.7% threatens it.)
    em1 = max((atr / 1.4) * math.sqrt(max(dte, 1)), spot * 0.02) if atr else spot * 0.05

    def side_plan(down: bool, short: float, covered: bool = False):
        side = "down" if down else "up"
        toward = (lambda p: spot - p) if down else (lambda p: p - spot)
        be = round(short - credit, 2) if down else round(short + credit, 2)
        clusters = _cluster_side(levels, spot, atr, down)
        between = [c for c in clusters if 0 < toward(c["price"]) < toward(short)]
        beyond = [c for c in clusters if toward(c["price"]) >= toward(short)]
        strike_sig = round(toward(short) / em1, 1) if em1 else 99.0
        safe = strike_sig > 1.5           # strike so far it's unlikely to be reached by expiry
        roll = "down-and-out" if down else "up-and-out"

        # RATIONALE — the cushion behind the strike + HOW FAR / how safe the strike is (the honest headline).
        cushion = [c for c in beyond if toward(c["price"]) - toward(short) <= 1.5 * atr] or beyond[:1]
        cu = f", tucked behind {cushion[0]['headline']}" if cushion else ""
        rationale.append(
            f"{'Put' if down else 'Call'} {short}{cu} — {abs(pc(short)):.0f}% / {strike_sig}σ away." +
            (" Deep out-of-the-money: reaching it by expiry is unlikely, so the level(s) below are "
             "EARLY-WARNING thesis checks, not active-defend lines." if safe else ""))

        def rung(tier, c, action, lead):
            extra = f" · confluence with {', '.join(l for l in c['labels'] if l != c['headline'])}" if len(c["labels"]) > 1 else ""
            triggers.append({"side": side, "tier": tier, "price": round(c["price"], 2), "pct_from_spot": pc(c["price"]),
                             "atr_units": au(c["price"]), "basis": c["headline"], "action": action,
                             "why": f"{lead} {_MON_IMPACT.get(c['family'], 'a technical level')}{extra}."})

        if safe:
            # SAFE — the strike is far. Show only the nearest 1-2 structures as EARLY WARNINGS; no defend/exit
            # (nothing is threatening a strike this far out; the distance note above says so).
            for c in between[:2]:
                rung("watch", c,
                     "Monitor only — an early sign the trend may be turning; the strike is far off, no defend needed yet.",
                     f"~{au(c['price'])} ATR from spot · still {round((toward(short) - toward(c['price'])) / em1, 1)}σ ABOVE the strike:")
            return

        # ACTIVE (near-money) — the strike is a genuine near-term risk → the full watch → defend → exit ladder.
        if between:
            rung("watch", between[0], "Watch closely — first structural line toward the strike; add no new size.",
                 f"~{au(between[0]['price'])} ATR away:")
        after = between[1:]
        if after:
            d = max(after, key=lambda c: c["significance"])
            rung("defend", d, (f"Roll {roll} to keep the shares, or trim." if covered
                               else f"Roll {roll} or cut size — the setup is breaking."),
                 f"~{au(d['price'])} ATR — the last structure before the strike:")
        else:
            triggers.append({"side": side, "tier": "defend", "price": round(short, 2), "pct_from_spot": pc(short),
                             "atr_units": au(short), "basis": "short strike",
                             "action": (f"Roll {roll} to keep the shares, or trim." if covered
                                        else f"Roll {roll} or cut size — the strike is being tested."),
                             "why": f"No listed structure between the watch line and the strike — the {short} strike "
                                    f"itself is the line: the option goes at-the-money here and assignment turns live."})
        if not covered:
            past = [c for c in beyond if toward(c["price"]) > toward(short) + 0.01]
            if past:
                rung("exit", past[0], "Close to cap the tail — the structure behind the strike is giving way.",
                     f"~{au(past[0]['price'])} ATR beyond the strike:")
            else:
                triggers.append({"side": side, "tier": "exit", "price": round(be, 2), "pct_from_spot": pc(be),
                                 "atr_units": au(be), "basis": "credit breakeven",
                                 "action": "Close to cap the tail — past breakeven with no structure to lean on.",
                                 "why": "past breakeven the position is in real loss and structure has broken."})

    if put_short:
        side_plan(True, float(put_short))
    if call_short:
        side_plan(False, float(call_short), covered=(structure == "covered_call"))

    gnote = None
    gflip = ctx.get("gamma_flip")
    if gflip:
        gnote = (f"Dealer gamma flip {round(gflip, 2)} ({pc(gflip):+.1f}%): spot is "
                 f"{'ABOVE it → dealers long gamma, moves are dampened (a tailwind for a premium seller)' if spot >= gflip else 'BELOW it → dealers short gamma, moves are amplified (dangerous for a seller)'}.")
    return {"triggers": triggers, "strike_rationale": rationale, "gamma_note": gnote,
            "regime": ctx.get("regime"), "levels_found": len(levels)}


async def monitor_trade(ticker: str, trade: dict) -> dict:
    """On-demand deterministic monitor plan for ONE trade, from the live advanced-TA read (cached per
    ticker for a few minutes so several trades on the same name reuse the four heavy computes)."""
    import time
    ticker = _norm_ticker(ticker)
    hit = _MON_CTX_CACHE.get(ticker)
    if hit and (time.time() - hit[0]) < _MON_TTL:
        ctx = hit[1]
    else:
        ctx = await asyncio.to_thread(_gather_monitor_context, ticker)
        if ctx.get("spot"):
            _MON_CTX_CACHE[ticker] = (time.time(), ctx)
    spot = float(trade.get("spot") or ctx.get("spot") or 0)
    if not spot or not ctx.get("levels"):
        return {"error": "No live technical levels available for monitoring right now.",
                "triggers": [], "strike_rationale": [], "gamma_note": None}
    return build_monitor_plan(trade.get("structure"), trade.get("put_short"), trade.get("call_short"),
                              float(trade.get("credit") or 0), spot, int(trade.get("dte") or 30), ctx)


_MON_ANALYST_SYSTEM = (
    "You are the trading desk's technical strategist. A premium-selling income trade is LIVE and the trader "
    "wants a MONITORING plan grounded ONLY in the real technical structure provided — what to watch, when to "
    "roll, when to cut, to manage the TAIL. Be specific with PRICES and name the exact structure at each rung. "
    "Never invent a level that isn't in the list; no generic advice."
)
_MON_ANALYST_GUIDANCE = (
    "\n\nWrite it in markdown with these bold labels:\n"
    "**Why these strikes** — which real structures cushion each short strike (one line).\n"
    "**Down-side** and/or **Up-side** (only the exposed sides) — the ORDERED sequence of structures price "
    "must break to reach the short strike, what EACH break means (change of character / gamma flip to short / "
    "liquidity sweep / value migration), and the action at each (watch · roll · cut). Cite the price every time.\n"
    "**Tail** — the ONE scenario that blows past the plan, and the hard price stop for it.\n"
    "Under 200 words. Refine the deterministic ladder; don't just repeat it."
)


async def monitor_analyze(ticker: str, trade: dict, api_key: str, model: str = "gpt-4o") -> dict:
    """LLM 'deep read' over the SAME live advanced-TA levels — a qualitative monitoring narrative grounded
    ONLY in the real structures + dealer gamma. Returns {content, plan}."""
    import time
    ticker = _norm_ticker(ticker)
    hit = _MON_CTX_CACHE.get(ticker)
    if hit and (time.time() - hit[0]) < _MON_TTL:
        ctx = hit[1]
    else:
        ctx = await asyncio.to_thread(_gather_monitor_context, ticker)
        if ctx.get("spot"):
            _MON_CTX_CACHE[ticker] = (time.time(), ctx)
    spot = float(trade.get("spot") or ctx.get("spot") or 0)
    if not spot or not ctx.get("levels"):
        return {"error": "No live technical levels available for the deep read right now."}
    plan = build_monitor_plan(trade.get("structure"), trade.get("put_short"), trade.get("call_short"),
                              float(trade.get("credit") or 0), spot, int(trade.get("dte") or 30), ctx)
    lv = sorted((ctx.get("levels") or []), key=lambda L: abs((L.get("price") or spot) - spot))
    level_lines = [f"  {round(p, 2)} ({(p - spot) / spot * 100:+.1f}%) · {L.get('label')} [{L.get('kind')}]"
                   for L in lv[:22] if (p := L.get("price"))]
    deter_lines = [f"  {t['tier'].upper()} {t['price']} ({t['pct_from_spot']:+.1f}%) — {t['basis']}: {t['action']}"
                   for t in plan["triggers"]]
    strike_parts = []
    if trade.get("put_short"):
        strike_parts.append(f"short put {trade['put_short']}")
    if trade.get("call_short"):
        strike_parts.append(f"short call {trade['call_short']}")
    header = [
        f"TRADE: {trade.get('structure')} — {', '.join(strike_parts)}, {trade.get('dte')}d, net credit "
        f"{trade.get('credit')}/sh. Spot {round(spot, 2)}.",
        "Danger = the underlying moving TOWARD a short strike (exercised/assigned there).", "",
        "REAL TA LEVELS (nearest first) — price (% from spot) · structure [kind]:", *level_lines, "",
        f"DEALER GAMMA: flip {ctx.get('gamma_flip')}, call wall {ctx.get('call_wall')}, "
        f"put wall {ctx.get('put_wall')}. Regime: {ctx.get('regime')}.", "",
        "DETERMINISTIC LADDER (reference — refine, don't just repeat):", *deter_lines,
    ]
    content = await call_llm(api_key=api_key, model=model, max_tokens=900, temperature=0.3,
                             messages=[{"role": "system", "content": _MON_ANALYST_SYSTEM},
                                       {"role": "user", "content": "\n".join(header) + _MON_ANALYST_GUIDANCE}])
    return {"content": content, "plan": plan, "levels_found": len(ctx.get("levels") or [])}


# ── Strike de-duplication ───────────────────────────────────────────────
# A deep option book hands the ranker dozens of $1-apart strikes of the SAME structure that score within
# a rounding error of each other, so a naive score-sort floods the headline with near-identical clones
# (15 cash-secured puts at 661/662/663/664 …) and buries genuinely different trades. Collapse each run of
# adjacent strikes (same structure + expiry, short strike within one moneyness band) down to the
# best-scoring representative, and hang the rest off it as `nearby_strikes` so nothing is lost.
_DEDUP_BAND_FLOOR_FRAC = 0.0125   # min band width as a fraction of spot …
_DEDUP_BAND_EM_FRAC = 0.15        # … or this fraction of the 1σ expected move, whichever is larger
_DEDUP_PER_STRUCTURE_CAP = 6      # cap distinct representatives per structure so one can't dominate


def _expected_move_band(spot: float, iv_pct: Optional[float], dte: Optional[int]) -> float:
    """Absolute strike-band width for de-dup: max(floor%, a fraction of the 1σ expected move). Vol- and
    tenor-aware so a quiet name bands tightly and a wild one bands wide, with a hard floor when IV is
    unavailable (e.g. a chain that returned no implied vol)."""
    if not spot or spot <= 0:
        return 1.0
    floor = spot * _DEDUP_BAND_FLOOR_FRAC
    if iv_pct and dte:
        em = spot * (iv_pct / 100.0) * math.sqrt(max(dte, 1) / 365.0)
        return max(floor, _DEDUP_BAND_EM_FRAC * em)
    return max(floor, spot * 0.02)


def _short_strikes(opp: dict) -> list[float]:
    """The short strike(s) that define a trade's risk — the axis adjacent-strike clones vary along."""
    out: list[float] = []
    for key in ("put_short", "call_short"):
        v = opp.get(key)
        if v:
            out.append(float(v))
    if not out:
        v = opp.get("short_strike") or opp.get("strike")
        if v:
            out.append(float(v))
    return out


def _dedup_signature(opp: dict, spot: float, band_abs: float) -> tuple:
    """Structure + expiry + moneyness band(s) — adjacent strikes inside one band share a signature. The
    blocking flag is included so a vetoed trade never collapses into a healthy one."""
    bands = tuple(int(abs(spot - s) / band_abs) for s in _short_strikes(opp))
    return (opp.get("structure"), opp.get("expiration"), bool(opp.get("grade_blocking")), bands)


def _collapse_adjacent_strikes(ranked: list[dict], spot: float, band_abs: float,
                               per_structure_cap: int = _DEDUP_PER_STRUCTURE_CAP) -> list[dict]:
    """Thin near-identical adjacent strikes. Input MUST be best-first (already score-sorted): the first
    row seen for a (structure, expiry, band) is the representative; later rows in that band ride along as
    compact `nearby_strikes`. Caps representatives per structure so one can't flood the headline. Order
    is preserved (still score order)."""
    reps: list[dict] = []
    by_sig: dict[tuple, dict] = {}
    per_struct: dict[str, int] = {}
    for r in ranked:
        sig = _dedup_signature(r, spot, band_abs)
        rep = by_sig.get(sig)
        if rep is not None:                                  # a near-adjacent clone → fold into the rep
            rep.setdefault("nearby_strikes", []).append({
                "strike": (_short_strikes(r) or [None])[0],
                "premium_per_share": r.get("premium_per_share"),
                "premium_annualized_pct": r.get("premium_annualized_pct"),
                "short_strike_pct": r.get("short_strike_pct"),
                "desk_score": r.get("desk_score"),
            })
            continue
        struct = r.get("structure") or "?"
        if per_struct.get(struct, 0) >= per_structure_cap:   # enough distinct strikes already → drop tail
            continue
        by_sig[sig] = r
        per_struct[struct] = per_struct.get(struct, 0) + 1
        r.setdefault("nearby_strikes", [])
        reps.append(r)
    for r in reps:                                           # cheap span summary for the UI chip
        nb = r.get("nearby_strikes") or []
        r["nearby_count"] = len(nb)
        strikes = [x["strike"] for x in nb if x.get("strike") is not None] + _short_strikes(r)
        if nb and strikes:
            r["nearby_range"] = [min(strikes), max(strikes)]
    return reps


async def _finalize_desk(scan: dict, opportunities: list[dict], ticker: str, quote_source: str,
                         owns_underlying: bool, user: Optional["User"], db: Optional["AsyncSession"],
                         target_dte: Optional[int] = None, collapse_strikes: bool = False,
                         ta: Optional[dict] = None, portfolio_fit: Optional[dict] = None,
                         gex: Optional[dict] = None) -> dict:
    """Score a set of candidate opportunities against the ticker's TA / regime / vol context and
    assemble the desk-review payload (chrome passthrough + ranked trades). Shared by ``rank_desk``
    (the full scan) and ``evaluate_desk_trade`` (one user-supplied trade).

    ``collapse_strikes`` thins near-adjacent same-structure strikes to best-in-band representatives
    (the full scan wants this; single-trade evaluate does not). ``ta``/``portfolio_fit``/``gex`` may be
    pre-fetched by the caller (``rank_desk`` reuses them so the technical read isn't fetched twice)."""
    ctx = scan.get("context") or {}
    spot = float(ctx.get("spot") or scan.get("spot") or 0.0)
    sofr_pct = float(ctx.get("sofr_pct") or 5.0)
    hv = ((ctx.get("hv30_pct") or ctx.get("hv20_pct") or 0) / 100.0) or None
    if ta is None or portfolio_fit is None or gex is None:
        ta, portfolio_fit, gex = await asyncio.gather(
            asyncio.to_thread(_ta_sync, ticker),
            asyncio.to_thread(_portfolio_fit_sync, ticker),
            asyncio.to_thread(_gex_sync, ticker),
        )

    max_dte = max((int(o.get("dte") or 0) for o in opportunities), default=int(target_dte or 45))
    events_pre = _events_in_window(scan, ta, max_dte)        # computed once — also feeds the grade
    vsx = ctx.get("vol_stats") or {}
    atm_iv_pct, iv_rank = vsx.get("iv_atm_pct"), vsx.get("iv_rank")
    beta = (portfolio_fit or {}).get("beta_1y_spx")
    mu = ta.get("_drift_mu")                          # EMA-slope drift (annualized) — Trend-drift + drift-adj keep
    macd_accel = _macd_accel(ta, spot)               # MACD acceleration — the SEPARATE timing veto
    r_free = sofr_pct / 100.0
    phys_vol, atr_vol = _gap_aware_vol(hv, ta.get("_atr_pct"))   # Keltner/ATR gap-aware physical vol
    gap_aware = bool(atr_vol and hv and atr_vol > hv)

    ranked: list[dict] = []
    for opp in opportunities:
        # Overwrite: the user already holds the shares → a covered call is an income overlay, not a
        # fresh buy-write (re-based capital + no beta penalty).
        overwrite = bool(owns_underlying and opp.get("structure") == "covered_call")
        try:
            dm = _opp_desk_metrics(opp, spot, sofr_pct, phys_vol, overwrite=overwrite)
        except Exception as exc:  # noqa: BLE001 — one bad trade must not kill the desk
            logger.debug("desk metrics failed (%s): %s", opp.get("label"), exc)
            dm = {"trader": {}, "pm": {}, "risk": {}, "quant": {"score": None, "verdict": None, "reasons": []}}
        base = (dm.get("quant") or {}).get("score")
        if base is None:
            base = (opp.get("confidence") or {}).get("score") or 50
        bonus, note, ta_factors = _ta_alignment(opp, ta, gex, spot)
        # Fold EVERY deterministic institutional factor (VRP / moneyness / skew / liquidity / tail /
        # beta / events) into the score + a hard-BLOCK filter, so the trade reaching the LLM is vetted.
        g = _algo_grade(opp, dm, spot, sofr_pct, atm_iv_pct, iv_rank, beta,
                        iv_percentile=vsx.get("iv_percentile"),
                        hv=phys_vol, gex=gex, macd=macd_accel, overwrite=overwrite,
                        next_earnings=(ctx or {}).get("next_earnings"), today=date.today(),
                        har_rv_pct=vsx.get("har_rv_pct"))
        desk_score = int(round(max(0, min(100, base + bonus + g["adj"]))))
        grade, approval = _grade_letter(desk_score, g["blocking"])
        # Itemized breakdown so the explorer can show each contribution as a signed bar. TA/regime
        # factors are their OWN group (ta_factors), kept separate from the option-math adjustments:
        #   desk_score = base_quality + Σ(grade_adjustments) + Σ(ta_factors).
        c = g["components"]
        grade_adjustments = [
            {"label": "Expectation", "points": round(c["expectation"], 1)},
            {"label": "VRP",         "points": round(c["vrp"], 1)},
            {"label": "Moneyness",   "points": round(c["moneyness"], 1)},
            {"label": "Skew / IV-edge", "points": round(c["skew"], 1)},
            {"label": "Liquidity",   "points": round(c["liquidity"], 1)},
            {"label": "Beta",        "points": round(c["beta"], 1)},
            {"label": "Earnings timing", "points": round(c.get("event", 0.0), 1)},
        ]
        risk_triggers = _risk_triggers(opp, spot, ta, phys_vol)   # WATCH→DEFEND→EXIT ladder (TA + geometry)
        ea_yield, ea_share = _event_adjusted_yield(opp, vsx, (ctx or {}).get("next_earnings"), date.today())
        ranked.append({**opp, "desk_metrics": dm, "desk_score": desk_score, "ta_note": note,
                       "risk_triggers": risk_triggers, "iv_edge_vp": g.get("iv_edge_vp"),
                       "event_adjusted_yield_pct": ea_yield, "event_premium_share": ea_share,
                       "algo_grade": grade, "approval_odds": approval, "grade_merits": g["merits"],
                       "grade_demerits": g["demerits"], "grade_blocking": g["blocking"],
                       "base_quality": round(base, 1), "grade_adjustments": grade_adjustments,
                       "ta_factors": ta_factors,
                       # Q-vs-P: the boundary read (imp/phys moves + strike distance) and the vol pair
                       # that weighted the base score — surfaced as the number-line in Quant Analysis.
                       # Drift-adjusted keep (P-measure DRIFT) is an OVERLAY only — the headline Win% stays standard.
                       "qp": {**g["qp"], **(dm.get("vrp") or {}),
                              "keep_standard_pct": opp.get("prob_keep_pct"),
                              "keep_drift_pct": _drift_adjusted_keep(opp, spot, mu, phys_vol, r_free, opp.get("prob_keep_pct")),
                              "drift_mu_pct": round(mu * 100, 1) if mu is not None else None,
                              "atr_vol_pct": round(atr_vol * 100, 1) if atr_vol else None, "gap_aware": gap_aware}})

    # BLOCKING trades (structurally broken, etc.) sink to the bottom; then desk score, Sortino, yield —
    # so the top-ranked trade has already cleared every mechanical filter the LLM desk applies.
    ranked.sort(key=lambda r: (
        not r["grade_blocking"],
        r["desk_score"],
        (r["desk_metrics"].get("pm") or {}).get("sortino") or 0,
        r.get("premium_annualized_pct") or 0,
    ), reverse=True)

    # Collapse near-identical adjacent strikes (the full scan only) so the headline shows genuinely
    # distinct trades, not $1-apart clones of the peak-delta strike. Runs AFTER the sort so each band's
    # representative is its best-scoring strike; the folded siblings ride along as `nearby_strikes`.
    if collapse_strikes and spot > 0:
        band_abs = _expected_move_band(spot, atm_iv_pct, max_dte)
        ranked = _collapse_adjacent_strikes(ranked, spot, band_abs)

    # Term structure: the scan often sees a single expiry (term_structure null) — probe a back
    # month so the Quant always knows contango vs backwardation (the earnings-inversion edge).
    vol_stats = dict(ctx.get("vol_stats") or {})
    summaries = scan.get("expiry_summaries") or []
    front = summaries[0] if summaries else {}
    if (vol_stats.get("term_structure") or {}).get("state") is None:
        ts = await _term_structure_probe(ticker, spot, front.get("dte"),
                                         front.get("atm_iv_pct"), quote_source, user, db)
        if ts:
            vol_stats["term_structure"] = ts

    # Corporate actions (ticker-level): ex-div + dividend yield for early-exercise reasoning.
    # yfinance's calendar often returns the LAST ex-div (a past date) — project it forward on the
    # observed payment cadence to the next occurrence so the early-exercise test isn't off a stale
    # date. Cadence comes from the dividends series (monthly ETFs are not quarterly payers).
    cadence = int(ta.get("_div_cadence_days") or 91)
    cadence = min(max(cadence, 7), 366)
    exdiv, exdiv_est = ta.get("_next_exdiv"), False
    if exdiv:
        try:
            ed, today_d = date.fromisoformat(exdiv), date.today()
            while ed < today_d:
                ed, exdiv_est = ed + timedelta(days=cadence), True
            exdiv = ed.isoformat()
        except ValueError:
            pass
    last_div = ta.get("_last_div")
    per_year = round(365.0 / cadence)
    div_yield_pct = round(last_div * per_year / spot * 100, 2) if (last_div and spot) else None
    corporate_actions = {
        "next_ex_dividend_date": exdiv,
        "next_ex_dividend_estimated": exdiv_est,     # True = projected forward from the last ex-div date
        "dividend_amount": last_div,                 # last cash dividend per share
        "dividend_cadence_days": cadence,            # observed gap between ex-div dates (91 = quarterly)
        "dividend_yield_pct": div_yield_pct,
        "note": "a short call faces EARLY-ASSIGNMENT if ex-div is before expiry and its extrinsic < the dividend",
    }

    return {
        "ticker": ticker,
        "spot": round(spot, 2),
        "sofr_pct": round(sofr_pct, 2),
        "as_of": scan.get("as_of"),
        "ta": ta,
        "ta_summary": _ta_summary(ta),
        "ta_timeframe": ta.get("timeframeLabel"),   # which TA read scores the trade (medium-term swing)
        "vol_stats": vol_stats,
        "corporate_actions": corporate_actions,
        "portfolio_fit": portfolio_fit or None,
        "gex": gex or None,             # dealer gamma-regime proxy (stock-level chip)
        "expiry_meta": {s.get("expiration"): {"max_oi_strike": s.get("max_oi_strike"),
                                              "atm_iv_pct": s.get("atm_iv_pct")}
                        for s in scan.get("expiry_summaries", [])},
        "events": events_pre,
        # Chrome passthrough — lets the single-ticker UI render the ticker header, the volatility
        # panel and the events banner from THIS one payload instead of a second /derivative-income
        # call. `flag_events` is the scan's UI event list (DerivativeIncomeFlag[] with scope), kept
        # distinct from `events` above (the desk-window list the LLM agents read).
        "context": scan.get("context"),
        "expiry_summaries": scan.get("expiry_summaries") or [],
        "flag_events": scan.get("events") or [],
        "ranked": ranked,
        "algo_top_pick": ranked[0] if ranked else None,
        "n_trades": len(ranked),
        "note": scan.get("note"),
        "data_source_note": scan.get("data_source_note"),   # IBKR→yfinance fallback flag, if any
    }


async def evaluate_desk_trade(
    ticker: str,
    legs: list[dict],
    quote_source: str = "yfinance",
    owns_underlying: bool = False,
    user: Optional["User"] = None,
    db: Optional["AsyncSession"] = None,
) -> dict:
    """Evaluate ONE user-supplied multi-leg trade (options and/or stock) on the FULL desk
    pipeline — identical chrome + metrics + grade as the single-ticker scan, but for the user's
    EXACT trade instead of scanned candidates. Returns a ``rank_desk``-shaped payload with
    ``ranked=[the trade]`` so the frontend renders it exactly like the Single-Ticker tab.

    Recognized single-expiry income structures (CSP / covered call / vertical spreads) are priced
    at the user's exact strikes via the scan's focus mechanism; strangles / condors / jade lizards /
    collars / calendars / custom combos are built generically and flagged ``is_custom`` (indicative
    grade)."""
    from .derivative_income_service import (
        _classify_structure, _build_evaluate_opp, _split_chain, _build_rnd, _atm_iv,
        _is_european, _EXACT_FOCUS_STRUCTURES,
    )
    from .quote_providers import get_provider

    ticker = _norm_ticker(ticker)
    norm: list[dict] = []
    for l in legs or []:
        try:
            norm.append({
                "action": "BUY" if str(l["action"]).upper().startswith("B") else "SELL",
                "type": "CALL" if str(l["type"]).upper().startswith("C") else "PUT",
                "strike": float(l["strike"]),
                "expiration": str(l["expiration"]),
            })
        except (KeyError, TypeError, ValueError):
            return {"error": "Each option leg needs action, type, strike and expiration."}
    # "I already hold the shares" is the sole stock signal: a short call becomes a COVERED call
    # (scored as an income overlay) rather than a naked call; it also enables the collar.
    has_stock = bool(owns_underlying)
    if not norm:
        return {"error": "Enter at least one option leg to evaluate."}

    exps = sorted({l["expiration"] for l in norm})
    today = date.today()

    def _dte(e: str) -> int:
        try:
            return max((date.fromisoformat(e) - today).days, 0)
        except ValueError:
            return 0

    near_exp = min(exps, key=_dte) if exps else None
    single_exp = len(exps) <= 1
    structure_id, label, is_custom = _classify_structure(norm, has_stock)

    # Chrome (+ the exact-strike opp for recognized single-expiry income structures) via the scanner.
    focus = None
    if single_exp and structure_id in _EXACT_FOCUS_STRUCTURES and near_exp:
        focus = {"structure": structure_id, "expiration": near_exp,
                 "legs": [{"strike": l["strike"], "right": "C" if l["type"] == "CALL" else "P",
                           "action": l["action"]} for l in norm]}
    known = {"covered_call", "cash_secured_put", "short_strangle", "put_credit_spread",
             "call_credit_spread", "iron_condor", "jade_lizard"}
    scan_structures = [structure_id] if structure_id in known else ["cash_secured_put"]
    scan = await run_derivative_income(
        ticker, target_expiration=near_exp, min_prob=0.0, min_income=0.0,
        structures=scan_structures, quote_source=quote_source, user=user, db=db, focus=focus,
    )
    if scan.get("error"):
        return {"error": scan["error"]}
    ctx = scan.get("context") or {}
    spot = float(ctx.get("spot") or scan.get("spot") or 0.0)
    sofr_pct = float(ctx.get("sofr_pct") or 5.0)
    hv = ((ctx.get("hv30_pct") or ctx.get("hv20_pct") or 0) / 100.0) or None
    r_free = sofr_pct / 100.0

    # The ONE opportunity to score.
    opp = None
    if focus:
        opp = next((o for o in scan.get("opportunities", []) if o.get("_is_focus")), None)
    if opp is None:
        provider = get_provider(quote_source, user=user, db=db)
        chains_by_exp: dict[str, dict] = {}
        for e in exps:
            try:
                chain = await provider.get_option_chain(ticker, e)
            except Exception as exc:  # noqa: BLE001 — one bad expiry must not kill the eval
                logger.debug("evaluate chain fetch failed %s %s: %s", ticker, e, exc)
                continue
            calls, puts = _split_chain(chain)
            strikes_all = sorted(set(calls) | set(puts))
            rnd = _build_rnd(calls, puts, strikes_all, spot, _dte(e))
            chains_by_exp[e] = {"calls": calls, "puts": puts, "rnd": rnd,
                                "atm_iv": _atm_iv(rnd, calls, puts, spot),
                                "dte": _dte(e), "strikes": strikes_all}
        if not chains_by_exp:
            return {"error": f"No option chain available for {ticker} at the requested expiries."}
        # A collar holds the underlying → model 100 sh/contract so the payoff / greeks include the stock.
        stock = {"shares": 100.0} if structure_id == "collar" else None
        opp = _build_evaluate_opp(norm, stock, chains_by_exp, spot, sofr_pct, hv,
                                  _is_european(ticker), ticker, r_free, structure_id, label, is_custom)
    if opp is None:
        return {"error": "Could not price this trade from the live chain — check the strikes and expiries."}
    opp["is_custom"] = bool(is_custom)
    opp.setdefault("label", label)

    desk = await _finalize_desk(scan, [opp], ticker, quote_source, owns_underlying, user, db)
    desk["note"] = (f"Evaluated a user-supplied {label}."
                    + (" Custom / calendar structure — the desk grade is indicative." if is_custom else ""))
    desk["evaluate"] = {"structure": structure_id, "label": label, "is_custom": is_custom,
                        "expirations": exps, "owns_underlying": owns_underlying}
    return desk


# ---------------------------------------------------------------------------
# LLM desk cascade — Quant → Risk → PM (on demand)
# ---------------------------------------------------------------------------

_QUANT_PERSONA = {
    "title": "Quant Desk", "green": {"CLEAR"},
    "system": """# ROLE
You are the QUANT DESK — a senior DERIVATIVES STRATEGIST and volatility specialist on an institutional
options-income desk. You bring what a retail screener cannot: mastery of the volatility surface, market
microstructure, options-structure selection, and how THIS kind of underlying actually trades.

# MISSION
From candidates[] (the algorithm's top-ranked income trades on ONE underlying, in full detail) pick the
SINGLE BEST trade to SELL for income and rank your top 3. The Risk desk will CHALLENGE your pick next, so
your case must be airtight and self-consistent.

# INPUTS  (a JSON object in the user message; EVERY value is PRE-COMPUTED and AUTHORITATIVE)
- underlying.volatility ......... IV rank/percentile, IV vs HV, skew_direction/skew_pts, term_structure
- underlying.corporate_actions .. ex-dividend date, dividend yield
- underlying.portfolio_fit ...... 1y beta & correlation vs SPY
- events_before_expiry .......... earnings / ex-div / macro landing inside the contract
- technical_analysis ............ regime & bias, RSI, support/resistance, volume_profile (POC/value area),
                                  market_structure, order_blocks, fair-value gaps, liquidity sweeps, MACD, MAs
- candidates[]  (top 5, FULL) ... strikes, pricing, net greeks (incl. Vanna/Charm/Volga), probability,
                                  volatility (atm_iv_pct / iv_hv_ratio / expected_move_pct_1sigma),
                                  pm_ratios (Omega/Sortino/EV/Kelly), risk (VaR/CVaR) and .institutional{
                                  execution, capital_shock_10pct_move (terminal vs instantaneous_mtm),
                                  volatility_skew, liquidity_capacity, early_exercise, pin_risk, capital_allocation }
- also_ranked[] ................. lower-ranked trades, condensed, each with a reject_reason (CONTEXT ONLY)

# HARD RULES
1. LLMs are unreliable at arithmetic — do NOT recompute, re-estimate or second-guess ANY number,
   probability, distance or ranking. READ the field and interpret it. Your value is JUDGEMENT, not math.
2. Recommend ONLY a trade from candidates[], by its EXACT given strikes. Never invent, round or
   interpolate a strike; never cite a metric you cannot copy from a field.
3. Do NOT prescribe a position size or contract count — sizing is decided elsewhere.
4. The algorithmic ranking is ONE input, not the answer — think holistically; you may override it with a
   reason grounded in the given metrics, structure or events.
5. Ground EVERY claim in a specific field, level or event. (A strike CLOSER to spot has a SMALLER
   cushion_pct_otm and a LARGER tail — read the field, never guess the direction.)

# REASONING PROCESS — CHAIN OF THOUGHT  (work through EVERY step, IN ORDER, before you conclude)
Step 1 · VOLATILITY SURFACE & VRP (Volatility Risk Premium) — Assess if the market is overpricing variance.
         Evaluate 'iv_hv_ratio' to establish baseline VRP. If < 1.0, explicitly justify the structural edge
         (e.g., extreme localized skew). Interrogate 'volatility_skew' (smile/smirk steepness) and
         'short_strike_iv_premium_over_atm_bps' to confirm you are heavily compensated for selling the fat
         tail — BUT if 'skew_regime' is 'extreme', do NOT bank it as automatic edge: be ready to PROVE it is
         STRUCTURAL skew, not the market pricing a known binary event (check events_before_expiry — the Risk
         desk WILL challenge this as a steamroller). Analyze the 'term_structure': if backwardated
         (front_iv_pct > back_iv_pct), prioritize short
         DTE to capture aggressive theta decay and IV mean-reversion; if in contango, the absolute yield must
         aggressively clear the 'sofr_hurdle_pct'.
Step 2 · CATALYST PRICING & FORWARD VOLATILITY — Isolate idiosyncratic risks. List 'events_before_expiry'.
         Treat binary events (earnings, macro) as forward-volatility distortions: elevated IV provides richer
         premium and a structural post-event vega-collapse tailwind, but you MUST cross-reference the 1σ
         'expected_move_pct_1sigma' against 'cushion_pct_otm'. For early-assignment, read
         'institutional.early_exercise' (danger / short_call_extrinsic) against 'corporate_actions'
         (ex-dividend date, dividend_amount): a short call is assigned early when ex-div is before expiry and
         its extrinsic has decayed below the dividend.
Step 3 · MICROSTRUCTURE & MONEYNESS — First check EVERY short leg's distance in σ via
         institutional.moneyness.short_strike_sigmas_from_spot: ANY short leg within ~0.5σ
         (moneyness.nearest_short_sigmas) is effectively AT-THE-MONEY — the trade is DIRECTIONAL there and NOT
         cushioned, no matter how far the OTHER leg sits, and its max_profit is only ~prob_max_profit_pct
         likely (NOT keep_prob_pct, which is downside-only). Do NOT describe a multi-leg trade as 'cushioned'
         off the FAR leg's cushion_pct_otm alone. Then anchor to institutional liquidity: map the short
         strikes against 'technical_analysis'. Institutional premium selling requires strikes positioned
         outside the Value Area (value_area_high / value_area_low) or heavily insulated behind a high-volume
         Point of Control (poc). You MUST locate the short strike relative to the 'unfilled_fair_value_gaps'
         and state explicitly whether the FVG acts as a MAGNET pulling price toward your strike, or a
         protective WALL insulating it. Demand structural defense: the strike should be protected by an
         unmitigated Order Block or sit on the far side of an unfilled FVG. Avoid selling into a liquidity
         vacuum; ensure the strike exploits 'cushion_pct_otm' safely away from recent liquidity sweeps.
Step 4 · RISK-ADJUSTED ALPHA & THE VOL RISK PREMIUM — Adjudicate the top candidates. For each, weigh edge vs
         tail. CAPITAL EFFICIENCY: net_expected_return_vs_sofr_bps is RISK-NEUTRAL, so a slightly-negative
         value is the NORMAL cost of fair pricing — NOT a reason to reject (do not try to find arbitrage in a
         no-arbitrage metric). Read bps_regime: if 'structurally_broken' (bps < -75), DISCARD immediately (toxic
         liquidity/skew — no override). If 'marginal_fair' (bps >= -75), the trade is viable ONLY if the
         VOLATILITY RISK PREMIUM is genuinely rich — your override MUST cite iv_hv_ratio > ~1, elevated
         iv_rank, and a steep short_strike_iv_premium_over_atm_bps (you are selling expensive insurance); if IV
         is CHEAP vs realized (iv_hv_ratio < 1) there is no edge → concede and DISCARD. Then evaluate the net
         Greeks — how does Charm (net_charm, delta decay) assist over time, and does Vanna (net_vanna, delta
         sensitivity to IV) expose the position to delta-expansion if the market gaps and vol spikes
         concurrently? Stress-test 'instantaneous_mtm' (down_pct_of_capital on a 10% gap),
         'stressed_bid_ask_spread_pct' (unrecoverable panic slippage) and 'pin_risk.cushion_pts' (terminal
         gamma whipsaw). (One to two sentences per top candidate.)
Step 5 · ADVERSARIAL PRE-MORTEM (TAIL RISK & MARGIN STRESS) — Anticipate the Risk desk's attack. Identify the
         most vulnerable failure point of your leading pick — typically a catastrophic 'instantaneous_mtm' gap
         shock, a toxic liquidity trap, weak CVaR95, or insufficient edge over the SOFR hurdle. Provide the
         airtight rebuttal citing the EXACT field that neutralizes it. If it cannot be neutralized, discard the
         candidate and switch picks.

# OUTPUT — fill this TEMPLATE EXACTLY. Keep every KEY. The numbered REASONING block IS your chain of thought.
REASONING:
1) Vol/regime: <your Step 1>
2) Events: <your Step 2>
3) Structure: <your Step 3>
4) Edge vs tail — top candidates: <your Step 4>
5) Pre-mortem (Risk will challenge): <your Step 5>
VERDICT: CLEAR | MIXED | NONE            (CLEAR = one clearly-best trade)
CHOICE: <structure @ exact strike(s), expiry — copied from a candidate>
AGREES_WITH_ALGO: yes | no — <do you concur with the candidates[] order, or override, and why>
RANK: <your top 3 best→worst on one line — your holistic order>
EDGE: <the ONE professional insight that makes this the best pick — structure / skew / vol / event / management>
BEST OUTCOME: <the exact PRICE ZONE for max profit (e.g. "full credit only if spot expires between the short
         put and short call") AND cite institutional.moneyness.prob_max_profit_pct + pm_ratios.expected_value —
         NOT just max_profit, which for a near-ATM short leg is far from certain>
WATCH: <the one move, level or event that would threaten the thesis>
MANAGEMENT: <the pre-committed price-level plan to avoid the tail — endorse or REFINE candidates[].management.
         risk_triggers: give the WATCH / DEFEND / EXIT prices and the corrective action at each (tighten,
         roll down-and-out, close half, hedge). Anchor each to a technical level (support/value-area/order
         block) or the credit breakeven, and note its atr_units (how imminent). This is the tail-risk stop-plan.>""",
    "guidance": ("\nWork through the five REASONING steps IN ORDER (vol/regime → events → structure → edge "
                 "vs tail → pre-mortem), then fill the template. Recommend only a candidates[] trade by its "
                 "exact strikes, cite a field for every claim, and never prescribe size. This is a debate — "
                 "make the case airtight."),
}

_RISK_PERSONA = {
    "title": "Risk Desk", "green": {"APPROVE_WITH_CONDITIONS", "APPROVE"},
    "system": """# ROLE
You are the CHIEF RISK OFFICER (CRO) / HEAD OF RISK for an institutional options-income desk. Your mandate
is capital preservation, margin defense, and surviving tail events. You do NOT generate trade ideas; you
ruthlessly stress-test the Quant's proposed trade. You are naturally skeptical of premium selling — it is
picking up pennies in front of a steamroller.

# MISSION
You receive the Quant's proposal (CHOICE + REASONING) and the SAME pre-computed JSON payload. Tear the
thesis apart by isolating the UNCOMPENSATED risks: margin vulnerabilities, Greek blow-ups, and liquidity
traps. Either VETO the trade or APPROVE it with strict management conditions. The PM adjudicates the debate.

# INPUTS (the Quant's CHOICE + REASONING, and the same JSON — EVERY value PRE-COMPUTED and AUTHORITATIVE)
Locate the Quant's chosen candidate first, then read its risk fields:
- institutional.capital_shock_10pct_move: capital_profile, margin_requirement_shock_pct (forced-liquidation
  gate — 0 = fully collateralized), instantaneous_mtm (down_pct_of_capital, a PAPER drawdown) vs terminal
- risk.cvar_95 / var_95 / max_loss / capital
- net_greeks.net_vanna / net_volga / net_gamma / net_charm
- institutional.liquidity_capacity.stressed_bid_ask_spread_pct
- institutional.early_exercise.danger / short_call_extrinsic   ·   institutional.pin_risk.cushion_pts
- cushion_pct_otm   ·   volatility.expected_move_pct_1sigma   ·   pm_ratios.omega / expected_value
- institutional.capital_allocation.net_expected_return_vs_sofr_bps
- technical_analysis.order_blocks / unfilled_fair_value_gaps / volume_profile.poc / liquidity_sweeps
- underlying.corporate_actions (ex-div date, dividend_amount)   ·   events_before_expiry

# HARD RULES
1. LLMs are unreliable at arithmetic — do NOT recompute, re-estimate or second-guess ANY number. READ the
   field and interpret it.
2. Anchor EVERY objection in a SPECIFIC field, and size it by % of capital (down_pct_of_capital, cvar as % of
   capital), NEVER the nominal dollar figure — a large negative $ on large collateral is NOT a large risk. A
   single-digit % paper drawdown on a 10% gap is capital preservation; never headline it as a vulnerability.
3. Do NOT reject a trade merely because options carry inherent risk. Reject it only if the risk is
   UNCOMPENSATED (inadequate yield vs SOFR, thin skew premium, or catastrophic path dependency).
4. Do NOT invent a brand-new trade or a strike — you evaluate the Quant's specific CHOICE. You MAY point to
   a SAFER candidate that ALREADY exists in candidates[] / also_ranked[] if the Quant's tail is uncompensated.
5. Never prescribe a position size or contract count, and never emit a literal placeholder (X / Y) — cite
   concrete numbers and price levels from the JSON.

# RISK EVALUATION PROCESS — CHAIN OF THOUGHT (work through EVERY step, IN ORDER)
Step 1 · PAPER vs MARGIN DRAWDOWN & TAIL RISK — FIRST read capital_profile and margin_requirement_shock_pct.
         For cash_secured / covered / defined_risk the collateral (or defined max loss) is FULLY posted
         (margin_requirement_shock_pct = 0): the instantaneous_mtm is a PAPER drawdown — holding pain / the
         cost of a VOLUNTARY exit or roll — NOT a forced margin liquidation, and a cash-secured put's mark
         largely REVERSES by expiry if the strike holds. JUDGE IT BY down_pct_of_capital, NEVER the nominal $:
         a single-digit % on a 10% gap (the position losing LESS than the underlying's move) is normal delta
         exposure / STRONG capital preservation — do NOT headline it as a MAJOR_VULNERABILITY. Only
         margin_requirement_shock_pct > 0 (a leveraged book) can force a liquidation. Weigh the TRUE tail
         instead: cvar_95 / max_loss and — for a short put — the ASSIGNMENT outcome (would the client accept
         owning the stock at the strike?).
Step 2 · GREEK VULNERABILITY (VANNA & VOLGA) — Premium sellers die by path dependency. Read the net Greeks:
         how sensitive is the position to a concurrent spot drop AND IV explosion (net_vanna)? If IV doubles
         overnight, does net_volga expand the tail exponentially? Does the Quant's EDGE survive a severe vol
         expansion?
Step 3 · EXECUTION & TOXIC LIQUIDITY — What happens when everyone rushes the exit? Check
         liquidity_capacity.stressed_bid_ask_spread_pct — would panic slippage destroy the expected value
         (pm_ratios.omega / expected_value)? Check pin_risk.cushion_pts and institutional.early_exercise.danger
         — a gamma trap or a synthetic (dividend) assignment near expiry?
Step 4 · MONEYNESS & MICROSTRUCTURE MAGNETS — FIRST test for a near-ATM short leg: read
         institutional.moneyness.nearest_short_sigmas — a short leg within ~0.5σ is a DIRECTIONAL bet dressed
         up as income; if the Quant called the trade 'cushioned' off the FAR leg's cushion_pct_otm while a
         near-ATM short leg drives the real exposure (its max_profit is only ~prob_max_profit_pct likely, NOT
         keep_prob_pct), CHALLENGE that as the primary flaw. Then flip the Quant's TA defense: if a support
         level breaks, does an unfilled Fair Value Gap (unfilled_fair_value_gaps) act as a gravitational magnet
         pulling spot THROUGH the short strike? Is nearest_short_cushion_pct truly sufficient to absorb a
         liquidity_sweep given expected_move_pct_1sigma, or does the strike sit exposed vs the value area / poc?
Step 5 · VOL RISK PREMIUM & CAPITAL EFFICIENCY — Read net_expected_return_vs_sofr_bps + bps_regime. The bps is
         RISK-NEUTRAL, so a slightly-negative value is normal fair pricing — do NOT flag that alone. If
         'structurally_broken' (bps < -75), the trade is a structural trap — flag it hard. If 'marginal_fair',
         the edge must come from a RICH VRP (iv_hv_ratio > ~1, high iv_rank, steep skew); if IV is CHEAP vs
         realized (iv_hv_ratio < 1) the premium does NOT compensate — flag it as selling cheap insurance.
         TOXIC SKEW CHECK: if volatility_skew.skew_regime is 'elevated' or 'extreme'
         (short_strike_iv_premium_over_atm_bps ≥ ~700, and especially ≥ 1500), do NOT bank the steep skew as
         free 'edge' — extreme skew is the classic 'pennies in front of a steamroller': the market is often
         pricing a KNOWN catastrophic binary (an earnings/catalyst in events_before_expiry) or the strike is an
         illiquid, unreliable quote. FORCE the Quant to PROVE it is structural skew you get paid to sell, not a
         priced-in disaster or a liquidity trap — cross-check events_before_expiry and the leg's oi/volume /
         stressed_bid_ask_spread_pct. AFTER adjusting for the tail from Steps 1–3, is the VRP rich enough to pay
         for the CVaR95 tail?

# OUTPUT — fill this TEMPLATE EXACTLY. Keep every KEY. The numbered block IS your chain of thought.
RISK_ANALYSIS:
1) Margin drawdown: <your Step 1>
2) Greek blow-up: <your Step 2>
3) Toxic liquidity / pin risk: <your Step 3>
4) Microstructure vulnerability: <your Step 4>
5) SOFR / capital hurdle: <your Step 5>
VERDICT: VETO | APPROVE_WITH_CONDITIONS
MAJOR_VULNERABILITY: <the single most dangerous flaw — name the specific metric AND its value (e.g. "-14.8% instantaneous MTM on a 10% gap")>
DEFENSE_BREACH: <if the Quant leaned on an order block / support level, state exactly what happens to the option's pricing (delta expands, extrinsic inflates) if that SPECIFIC level fails>
MANAGEMENT_MANDATE: <if VETO, the exact metric that forces it. If APPROVE_WITH_CONDITIONS, the strict emergency-exit thresholds using CONCRETE levels from the JSON — e.g. "cut if spot breaks 288.72 support or IV rank clears 90" — never literal placeholders>""",
    "guidance": ("\nWork through the five RISK_ANALYSIS steps IN ORDER (paper-vs-margin/tail → Greeks → "
                 "liquidity → microstructure → SOFR hurdle), then fill the template. Anchor every objection in "
                 "a specific JSON field. For collateralized trades the instantaneous_mtm is a PAPER drawdown, "
                 "NOT a forced liquidation — raise margin warnings ONLY from margin_requirement_shock_pct. VETO "
                 "only UNCOMPENSATED risk. Cite concrete levels — never placeholders like X or Y."),
}

_QUANT_REBUTTAL_PERSONA = {
    "title": "Quant Desk — Rebuttal", "green": {"HOLD"}, "verdict_key": "STANCE",
    "system": """# ROLE
You are the QUANT DESK — the same senior strategist, now RESPONDING to the Risk desk's challenge in a live
desk debate. This is cross-examination, not a fresh pitch: you defend the trade you proposed, or you concede.
Intellectual honesty over winning — your credibility with the PM depends on it.

# MISSION
The Risk desk has either VETOed your CHOICE or APPROVED it WITH CONDITIONS, citing a specific
MAJOR_VULNERABILITY and DEFENSE_BREACH. FIRST audit the challenge's OWN logic (spatial, directional,
mathematical) — Risk can be wrong, and a flawed challenge is REFUTED, not conceded to. Then, for a challenge
that survives the audit, address it HEAD-ON with the pre-computed evidence and decide to HOLD (defend),
ADJUST (switch to a safer LISTED candidate), or CONCEDE (Risk is right). The PM adjudicates next.

# INPUTS (your original proposal, the Risk desk's challenge, and the SAME JSON — all PRE-COMPUTED)
- Risk's VERDICT, MAJOR_VULNERABILITY, DEFENSE_BREACH, MANAGEMENT_MANDATE (in the context above).
- Your own CHOICE + REASONING (above).
- The full JSON — re-read the EXACT fields Risk weaponized: institutional.capital_shock_10pct_move.
  instantaneous_mtm.down_pct_of_capital, risk.cvar_95, institutional.liquidity_capacity.
  stressed_bid_ask_spread_pct, institutional.early_exercise.danger, institutional.pin_risk.cushion_pts,
  cushion_pct_otm, volatility.expected_move_pct_1sigma, institutional.volatility_skew.
  short_strike_iv_premium_over_atm_bps, institutional.capital_allocation.net_expected_return_vs_sofr_bps,
  probability.keep_prob_pct, and the technical_analysis levels.

# HARD RULES
1. LLMs are unreliable at arithmetic — do NOT recompute or second-guess ANY number. READ the field.
2. NEVER contradict a given number. If Risk cites a real, material figure you may not wave it away — either
   show it is COMPENSATED (cite the offsetting field) or CONCEDE. (But if Risk MISREAD a field — wrong sign,
   nominal-vs-%, wrong direction/side — REFUTING that misreading is NOT contradicting the number; it is
   correcting the desk's error, and you must.)
3. Recommend ONLY a trade from candidates[] / also_ranked[], by its EXACT strikes. Never invent a strike.
4. Intellectual honesty: if the risk is genuinely uncompensated, CONCEDE — do not defend the indefensible.
5. Never discuss position size or contract count. Only WHICH trade stands.

# REBUTTAL PROCESS — CHAIN OF THOUGHT (work through EVERY step, IN ORDER)
Step 1 · ISOLATE THE CHALLENGE — In one line each, restate Risk's MAJOR_VULNERABILITY and DEFENSE_BREACH.
         Name the EXACT metric/level they weaponized, and whether they VETOed or APPROVED_WITH_CONDITIONS.
Step 2 · AUDIT THE CHALLENGE FIRST (spatial · directional · mathematical) — BEFORE you draft any defense,
         AUDIT the Risk desk's OWN logic — Risk can be wrong, and a flawed challenge is REFUTED, not conceded
         to. Check all three, citing the exact field/level that proves or disproves EACH:
           • SPATIAL — are the strikes/levels placed correctly? Does the support / FVG / order block Risk cites
             actually sit BETWEEN spot and the threatened short strike, on the side that matters? (A level
             BELOW a short CALL, or a 'magnet' on the opposite side of spot, does not threaten it; a break of
             support far below a deep-OTM short put is not imminent.)
           • DIRECTIONAL — does the move Risk fears actually threaten THIS leg? (A gap DOWN does not threaten a
             short CALL; 'upside risk' is moot when a jade lizard has none; a bearish break HELPS a short-call /
             call-spread. Confirm the feared direction hits the leg Risk names.)
           • MATHEMATICAL — judge magnitude by % of capital, NEVER nominal $ (a big negative $ on large
             collateral is small); a single-digit % paper mark, a ≥1σ cushion (moneyness.nearest_short_sigmas),
             or a single-digit-% CVaR is NOT 'catastrophic'; a LOWER put strike is SAFER, not riskier. Does the
             field Risk cited actually support the claim, or did Risk misread its sign/magnitude?
         If the challenge FAILS the audit, mark it INVALID and REFUTE it outright. Only a challenge that
         SURVIVES the audit proceeds to the merits below.
Step 3 · COMPENSATION TEST — Even if the (audited-valid) risk is real, is it PAID FOR? Weigh the offsetting edge — skew
         premium (short_strike_iv_premium_over_atm_bps), carry (net_expected_return_vs_sofr_bps), keep_prob &
         cushion vs expected_move_pct_1sigma. Does the compensation clear the tail Risk raised?
Step 4 · HONEST STANCE — Conclude: HOLD (objection overstated — defend with the field that answers it),
         ADJUST (partly valid — switch to a SAFER candidate that ALREADY exists in candidates[]/also_ranked[]
         and resolves it while keeping edge), or CONCEDE (Risk won — the risk is genuinely uncompensated).
Step 5 · TERMS — If you HOLD or ADJUST, accept or tighten Risk's MANAGEMENT_MANDATE exit conditions with
         concrete levels from the JSON.

# OUTPUT — fill this TEMPLATE EXACTLY. Keep every KEY. The numbered block IS your chain of thought.
REBUTTAL:
1) The challenge: <your Step 1>
2) Challenge audit — spatial / directional / mathematical (INVALID or survives): <your Step 2>
3) Compensation: <your Step 3>
4) Stance rationale: <your Step 4>
5) Terms: <your Step 5>
STANCE: HOLD | ADJUST | CONCEDE
FINAL PICK: <the trade you stand behind — structure @ exact strike(s), expiry from a candidate; or PASS if you concede no trade clears>""",
    "guidance": ("\nWork through the five REBUTTAL steps IN ORDER. FIRST audit the Risk challenge's spatial, "
                 "directional and mathematical logic (a flawed challenge is REFUTED, not conceded to) — only "
                 "then test compensation and take a stance. Answer with the exact field, never contradict a "
                 "given number, CONCEDE if the risk is real AND uncompensated. Only a listed trade by its exact "
                 "strikes — never a size."),
}

_PM_PERSONA = {
    "title": "Portfolio Manager", "green": {"EXECUTE"}, "verdict_key": "FINAL_DECISION",
    "system": """# ROLE
You are the PORTFOLIO MANAGER (PM) and Head of the Options Desk — the ultimate decision-maker and capital
allocator. You hold the Quant's grasp of the volatility edge and the Risk Officer's respect for tail events,
but your mandate is risk-adjusted return (RAROC), capital efficiency, and portfolio-level dynamics.

# MISSION
Adjudicate the debate between the Quant (who wants yield) and the Chief Risk Officer (who stress-tested the
tail). Synthesize their arguments, break any tie using the raw JSON, and make the final, binding capital-
allocation decision: EXECUTE, REJECT, or EXECUTE_MODIFIED.

# INPUTS
- The raw JSON payload (underlying, pm_ratios, net_greeks, risk, institutional, portfolio_fit, events).
- The Quant's CHOICE / EDGE / REASONING and the Rebuttal's STANCE / FINAL PICK (in the context above).
- The Risk Officer's VERDICT, MAJOR_VULNERABILITY, DEFENSE_BREACH, MANAGEMENT_MANDATE (above).

# HARD RULES
1. LLMs are unreliable at arithmetic — do NOT recompute. Rely on the pre-computed JSON fields (EV, Sortino,
   kelly_sizing, beta, instantaneous_mtm, etc.).
2. You cannot sit on the fence. Explicitly rule on whether the Quant's edge overcomes the Risk Officer's
   MAJOR_VULNERABILITY, and FLAG any place either desk contradicted a given field. Size EVERY risk by its %
   of capital, NEVER the nominal dollar figure.
3. CAPITAL EFFICIENCY via the VOL RISK PREMIUM (not a naive SOFR gate): net_expected_return_vs_sofr_bps is
   RISK-NEUTRAL, so a slightly-negative value is the NORMAL cost of fair pricing — do NOT reject on that
   alone (you cannot find arbitrage in a no-arbitrage metric). Use bps_regime: 'structurally_broken'
   (bps < -75) = automatic REJECT, no override. 'marginal_fair' (bps >= -75) = EXECUTE only if the VOLATILITY
   RISK PREMIUM is genuinely rich (iv_hv_ratio > ~1, elevated iv_rank, steep skew) — a rich VRP IS the
   legitimate override; if IV is cheap vs realized (iv_hv_ratio < 1) there is no edge → REJECT. A high
   keep-probability alone is NOT an override. Earnings are DOUBLE-EDGED, not an auto-reject: elevated IV can
   be a PRIME time to sell if the VRP pays.
4. If EXECUTE / EXECUTE_MODIFIED you MUST state sizing as a % of capital: read
   institutional.capital_allocation.kelly_sizing (full/half/quarter %) and pick a fraction — default to HALF
   or QUARTER Kelly (full Kelly over-bets one name), then cut further for Risk's instantaneous_mtm and
   liquidity findings. NEVER a contract count — a % only.
5. Factor in underlying.portfolio_fit (beta_1y_spx, correlation_spx): a trade piling on undiversified market
   beta (high correlation) demands a HIGHER edge to justify — discount its 'alpha'.
6. Recommend ONLY the trade under debate (or the listed candidate the Rebuttal switched to), by its EXACT
   strikes. Never invent a strike; never emit a literal placeholder (X / [Price]) — cite concrete levels.

# ADJUDICATION PROCESS — CHAIN OF THOUGHT (work through EVERY step, IN ORDER)
Step 1 · CONFLICT RESOLUTION (EDGE vs TAIL) — Address the Risk Officer's MAJOR_VULNERABILITY head-on. Did the
         Quant/Rebuttal fairly price the instantaneous_mtm paper drawdown and Vanna risk, or did Risk expose a
         genuine blind spot? IMPORTANT: if Risk warned of a 'margin liquidation' on a collateralized trade
         (capital_profile cash_secured/covered/defined_risk, margin_requirement_shock_pct = 0), that objection
         is INVALID — the MTM is paper only; discount it and re-center on the real tail (cvar_95 / assignment).
         MTM SHOCK SIZING RULE: judge instantaneous_mtm ONLY by down_pct_of_capital, NEVER the nominal $ — a
         single-digit % drawdown on a 10% gap (e.g. −4% to −8%) is standard delta exposure and STRONG capital
         preservation (the position lost LESS than the underlying's 10%); REJECT any Risk argument that
         headlines a nominal dollar figure or frames a <10% paper drawdown as a 'major vulnerability'.
         CONSISTENCY CROSS-CHECK: verify the Quant's 'cushion / safe' claim against
         institutional.moneyness.nearest_short_sigmas — a short leg within ~0.5σ is AT-THE-MONEY and NOT
         cushioned, so discount any 'wide cushion' framing built on the FAR leg; remember keep_prob_pct is
         downside-only (the FULL-credit odds are prob_max_profit_pct). Also confirm the Quant did not contradict
         a given field (e.g. claiming a trade is 'with the regime' when ta_alignment says 'against'). Who is
         right on the microstructure defense (order_blocks / unfilled_fair_value_gaps / poc)? Flag EVERY such
         contradiction and state explicitly whose argument wins the core point of contention.
Step 2 · EXPECTED VALUE & THE VOL RISK PREMIUM — Read pm_ratios.expected_value / sortino and
         net_expected_return_vs_sofr_bps WITH bps_regime. The bps is RISK-NEUTRAL: 'marginal_fair' just means
         fairly priced — the edge must come from a rich VRP (iv_hv_ratio > ~1, iv_rank, skew), so demand that
         override or REJECT; 'structurally_broken' is an automatic REJECT no override can save. If Risk is right about
         toxic execution slippage (institutional.liquidity_capacity.stressed_bid_ask_spread_pct), does the edge
         survive? Is the capital lockup (max_capital_lockup_days_est) justified by a real premium over the
         risk-free rate?
Step 3 · PORTFOLIO CONTEXT & MACRO — Zoom out. Read events_before_expiry and underlying.portfolio_fit
         (beta_1y_spx / correlation_spx). Is the desk taking undiversified, idiosyncratic risk right before a
         macro event? If so, probability.keep_prob_pct must be exceptionally high (>90%) to proceed.
Step 4 · SIZING & MANAGEMENT TRIGGER — If viable, how big? Read institutional.capital_allocation.kelly_sizing
         and choose full / half / quarter Kelly (default lower), cutting further for Risk's instantaneous_mtm.
         Cut HARD for a high-beta underlying (underlying.portfolio_fit.beta_1y_spx ≥ ~1.5 is a LEVERAGED market
         bet, not idiosyncratic income → quarter Kelly or less) and for a near-ATM short leg
         (moneyness.nearest_short_sigmas < ~0.5 = directional → size down). Then set the exact exit: a
         take-profit (e.g. close at 50% of max premium) and a stop tied to a CONCRETE level (a support / poc
         price, or an IV-rank threshold) from the JSON.

# OUTPUT — fill this TEMPLATE EXACTLY. Keep every KEY. The numbered block IS your chain of thought.
PM_REASONING:
1) Conflict resolution: <your Step 1>
2) EV & SOFR hurdle: <your Step 2>
3) Portfolio context: <your Step 3>
4) Sizing & triggers: <your Step 4>
FINAL_DECISION: EXECUTE | REJECT | EXECUTE_MODIFIED
WINNING_ARGUMENT: <Quant | Risk — which desk's thesis drove the decision, and the SPECIFIC metric that proved it>
TRADE_PARAMETERS: <if REJECT, 'N/A'; else the exact structure @ strike(s), expiry — copied from the candidate>
SIZING_ALLOCATION: <if REJECT, '0%'; else the exact % of capital from kelly_sizing (e.g. "quarter Kelly ≈ 25%"), adjusted down for tail risk>
DESK_MANDATE: <strict trader instructions with CONCRETE levels — e.g. "Enter at mid or better; take profit at 50% of max premium; stop out if spot breaks 288.72 support or IV rank clears 90" — never placeholders>""",
    "guidance": ("\nWork through the four PM_REASONING steps IN ORDER (conflict → EV/hurdle → portfolio → "
                 "sizing/triggers), then fill the template. You MUST rule EXECUTE / REJECT / EXECUTE_MODIFIED — "
                 "name the winning desk and the metric that proved it. Size as a % from kelly_sizing (default "
                 "half/quarter), NEVER a contract count; use concrete exit levels, never placeholders."),
}


def _parse_verdict(response: str, green: set, key: str = "VERDICT") -> tuple[str, bool]:
    for line in (response or "").splitlines():
        if line.strip().upper().startswith(key.upper()):
            v = line.split(":", 1)[-1].strip()
            return v, bool(v) and v.upper() not in green
    return "", False


def _extract_line(text: str, prefix: str) -> Optional[str]:
    for line in (text or "").splitlines():
        if line.strip().upper().startswith(prefix.upper()):
            return line.split(":", 1)[-1].strip()
    return None


_STRUCT_ALIASES = {
    "cash_secured_put": ("cash-secured put", "cash secured put", "csp", "cash_secured_put", "short put"),
    "covered_call": ("covered call", "covered_call", "buy-write", "buywrite"),
    "collar": ("collar",),
    "put_credit_spread": ("put credit spread", "bull put", "put_credit_spread", "put spread", "bull-put"),
    "call_credit_spread": ("call credit spread", "bear call", "call_credit_spread", "call spread", "bear-call"),
    "iron_condor": ("iron condor", "iron_condor", "condor"),
    "jade_lizard": ("jade lizard", "jade_lizard", "jade"),
}


def _resolve_pick(text: Optional[str], ranked: list[dict]) -> Optional[int]:
    """Best-effort map an LLM's free-text pick (e.g. 'Cash-Secured Put $280, exp …') to an
    index in `ranked`, so the UI can Explore the exact trade the desk recommended. Requires a
    structure match; strike matches break ties. Returns None if nothing matches confidently."""
    if not text:
        return None
    t = text.lower()
    best_i, best_score = None, 0
    for i, r in enumerate(ranked):
        struct = r.get("structure") or ""
        score = 2 if any(a in t for a in _STRUCT_ALIASES.get(struct, (struct,))) else 0
        if score:                                     # only bother scoring strikes once the structure matches
            for key in ("short_strike", "long_strike", "put_short", "put_long", "call_short", "call_long"):
                v = r.get(key)
                if v and re.search(rf"(?<!\d){int(round(v))}(?:\.0+)?(?!\d)", t):
                    score += 1
        if score > best_score:                        # ties keep the earlier (higher-ranked) trade
            best_i, best_score = i, score
    return best_i if best_score >= 2 else None


def _find_focus_index(ranked: list[dict], structure: Optional[str], expiration: Optional[str],
                      short_strike: Optional[float]) -> Optional[int]:
    """Map a Desk-Review-v2 trade selector (structure + expiry + primary short strike) to a ranked
    index. Matches structure + expiration + any short leg strike (short_strike/put_short/call_short)."""
    for i, r in enumerate(ranked):
        if structure and r.get("structure") != structure:
            continue
        if expiration and r.get("expiration") != expiration:
            continue
        strikes = [r.get("short_strike"), r.get("put_short"), r.get("call_short")]
        if short_strike is None or any(s is not None and abs(float(s) - float(short_strike)) < 0.01 for s in strikes):
            return i
    return None


async def _run_agent(role: str, persona: dict, context: str, api_key: str, model: str) -> dict:
    messages = [
        {"role": "system", "content": persona["system"]},
        {"role": "user", "content": context + persona["guidance"]},
    ]
    content = await call_llm(api_key=api_key, model=model, messages=messages,
                             max_tokens=1300, temperature=0.3)
    verdict, action = _parse_verdict(content, persona["green"], persona.get("verdict_key", "VERDICT"))
    return {
        "role": role, "title": persona["title"], "verdict": verdict,
        "action_needed": action, "content": content, "model": model,
        # returned so the UI can expose exactly what each agent was given (debug/triage)
        "input_context": context, "system_prompt": persona["system"],
    }


# ── Blind independent read — an LLM second opinion NOT anchored to our score ─────────────────
_BLIND_SYS = (
    "You are an INDEPENDENT options strategist giving a SECOND OPINION on a premium-selling income trade. "
    "You have deliberately NOT been shown any pre-computed score, grade, or ranking — form your OWN view "
    "from the raw market facts alone.\n"
    "HARD RULES:\n"
    "• NEVER output a 1–10 / X-out-of-10 / star rating — a vibes score is useless and forbidden. DECIDE.\n"
    "• EVERY judgment must cite a specific NUMBER from the data (IV, HV30, forward RV, cushion, σ, delta, "
    "CVaR, keep-prob, an event date). If you can't cite a number, don't assert it.\n"
    "• The quant math (greeks, CVaR, keep-prob, expected move) is GIVEN and correct — USE it, never recompute.\n"
    "• Judge what a mechanical screen can't: does the vol / cushion / event / regime picture actually FIT "
    "together, and what is the real risk. Be willing to DISAGREE with what a rule model would conclude."
)
_BLIND_GUIDE = (
    "\n\nFill this template EXACTLY — nothing before or after it:\n"
    "VERDICT: <ENTER | RESIZE | PASS | AVOID>\n"
    "FACTORS:\n"
    "- Volatility edge: <FAVORABLE | NEUTRAL | ADVERSE> — <cite IV vs HV30 vs the forward RV>\n"
    "- Cushion / moneyness: <FAVORABLE | NEUTRAL | ADVERSE> — <cite cushion vs the 1σ expected move>\n"
    "- Event / timing: <FAVORABLE | NEUTRAL | ADVERSE> — <cite the earnings/macro date vs expiry, or 'none in window'>\n"
    "- Technical / regime: <FAVORABLE | NEUTRAL | ADVERSE> — <cite the level or regime>\n"
    "- Liquidity / execution: <FAVORABLE | NEUTRAL | ADVERSE> — <cite the spread or OI>\n"
    "EDGE: <the ONE concrete reason to do — or to skip — this, tied to a number>\n"
    "BREAK: <the ONE scenario or price level that would invalidate it, tied to a number>"
)


def _blind_facts(r: dict, desk: dict) -> dict:
    """RAW market facts for an independent read — deliberately EXCLUDES our desk_score / grade / factor
    adjustments / algo verdict, so the LLM can't anchor to the rule model."""
    dm = r.get("desk_metrics") or {}
    vs = desk.get("vol_stats") or {}
    return {
        "trade": {"structure": r.get("structure"), "strikes": _strikes_json(r), "expiration": r.get("expiration"),
                  "dte": r.get("dte"), "net_credit": r.get("premium"), "cushion_pct": r.get("cushion_pct"),
                  "short_strike_pct_from_spot": r.get("short_strike_pct")},
        "spot": desk.get("spot"), "sofr_pct": desk.get("sofr_pct"),
        "volatility": {"atm_iv_pct": r.get("atm_iv_pct"), "hv30_pct": vs.get("hv30_pct"),
                       "forward_rv_har_pct": vs.get("har_rv_pct"), "iv_over_hv": r.get("iv_hv_ratio"),
                       "iv_minus_har_vp": vs.get("iv_vs_har_pts"), "iv_rank": vs.get("iv_rank"),
                       "iv_percentile": vs.get("iv_percentile"), "skew_pts": vs.get("skew_pts"),
                       "expected_move_pct_1sigma": _expected_move_pct(r)},
        "probability_from_option_market_RND": {"keep_prob_pct": r.get("prob_keep_pct")},
        "quant_facts": {"pm_ratios": dm.get("pm") or {}, "risk": dm.get("risk") or {},   # from the RND payoff — not our score
                        "greeks": dm.get("trader") or {}},
        "yield": {"premium_annualized_pct": r.get("premium_annualized_pct")},
        "implied_vs_realized_boundary": r.get("qp"),
        "events_in_window": [e.get("text") for e in (desk.get("events") or [])],
        "technical_read": r.get("ta_note"),
    }


def _parse_blind(content: str) -> dict:
    verdict = None
    m = re.search(r"VERDICT:\s*([A-Za-z_]+)", content)
    if m:
        v = m.group(1).strip().upper()
        verdict = v if v in ("ENTER", "RESIZE", "PASS", "AVOID") else v
    factors = []
    for line in content.splitlines():
        lm = re.match(r"\s*[-•]\s*([^:]+?):\s*(FAVORABLE|NEUTRAL|ADVERSE)\b\s*[—:-]*\s*(.*)", line, re.I)
        if lm:
            factors.append({"name": lm.group(1).strip(), "call": lm.group(2).strip().upper(),
                            "reason": lm.group(3).strip()})

    def _grab(key):
        mm = re.search(rf"\b{key}:\s*(.+?)(?:\n[A-Z][A-Z ]+:|\Z)", content, re.S)
        return mm.group(1).strip() if mm else None
    return {"verdict": verdict, "factors": factors, "edge": _grab("EDGE"), "break_scenario": _grab("BREAK")}


def _blind_divergence(llm_verdict: Optional[str], grade: Optional[str], blocking: bool) -> str:
    """agree / partial / disagree between the independent LLM verdict and the (hidden-from-it) rule grade."""
    g = (grade or "").upper()
    rule = "avoid" if (blocking or g == "F") else ("favorable" if g in ("A", "B") else "neutral")
    v = (llm_verdict or "").upper()
    llm = "favorable" if v == "ENTER" else ("avoid" if v in ("PASS", "AVOID") else "neutral")
    if rule == llm:
        return "agree"
    return "disagree" if {rule, llm} == {"favorable", "avoid"} else "partial"


async def blind_read(ticker: str, api_key: str, target_dte: Optional[int] = None, min_prob: float = 0.85,
                     min_income: float = 20.0, structures: Optional[list[str]] = None,
                     quote_source: str = "yfinance", model: str = "gpt-4o", focus: Optional[dict] = None,
                     user: Optional["User"] = None, db: Optional["AsyncSession"] = None,
                     target_expiration: Optional[str] = None) -> dict:
    """An INDEPENDENT LLM second opinion on ONE trade, BLIND to our desk score/grade — so its read isn't
    anchored to (or biased by) the rule model. Returns a DECISION + cited factor calls (NO fuzzy rating),
    and the divergence vs the rule grade the LLM never saw."""
    desk = await rank_desk(ticker, target_dte, min_prob, min_income, structures, quote_source,
                           user, db, target_expiration=target_expiration, focus=focus)
    if desk.get("error"):
        return desk
    ranked = desk.get("ranked") or []
    if not ranked:
        return {"error": "No candidate trade to read."}
    idx = 0
    if focus:
        fi = _find_focus_index(ranked, focus.get("structure"), focus.get("expiration"), focus.get("short_strike"))
        idx = fi if fi is not None else 0
    r = ranked[idx]
    context = "RAW MARKET FACTS (no score, grade, or ranking is shown to you):\n" + json.dumps(
        _blind_facts(r, desk), indent=1, default=str)
    content = await call_llm(api_key=api_key, model=model, max_tokens=750, temperature=0.2,
                             messages=[{"role": "system", "content": _BLIND_SYS},
                                       {"role": "user", "content": context + _BLIND_GUIDE}])
    parsed = _parse_blind(content)
    return {
        "trade_label": r.get("label"),
        "blind": {**parsed, "content": content, "model": model},
        "rule": {"grade": r.get("algo_grade"), "desk_score": r.get("desk_score"),
                 "vetoed": bool(r.get("grade_blocking"))},
        "divergence": _blind_divergence(parsed.get("verdict"), r.get("algo_grade"), bool(r.get("grade_blocking"))),
    }


async def run_desk_agents(
    ticker: str,
    api_key: str,
    target_dte: Optional[int] = None,
    min_prob: float = 0.85,
    min_income: float = 20.0,
    structures: Optional[list[str]] = None,
    quote_source: str = "yfinance",
    model: str = "gpt-4o",
    focus: Optional[dict] = None,
    user: Optional["User"] = None,
    db: Optional["AsyncSession"] = None,
    target_expiration: Optional[str] = None,
    evaluate: Optional[dict] = None,
) -> dict:
    """A genuine desk DEBATE: Quant proposes → Risk challenges → Quant rebuts → PM adjudicates.
    Each agent reasons explicitly, sees all prior turns + the full pre-computed JSON, and grounds
    every claim in a GIVEN field. Three modes: ranking (focus=None → pick the best of the top set),
    single-trade (Desk Review v2 — `focus`={structure, expiration, short_strike} → rule
    EXECUTE/REJECT on THAT scanned trade), and evaluate (`evaluate`={legs, owns_underlying} → debate
    the user's own bring-your-own trade — works for custom/calendar trades the focus selector can't
    rebuild)."""
    if evaluate:
        desk = await evaluate_desk_trade(
            ticker, legs=evaluate.get("legs") or [], quote_source=quote_source,
            owns_underlying=bool(evaluate.get("owns_underlying")), user=user, db=db)
    else:
        desk = await rank_desk(ticker, target_dte, min_prob, min_income, structures, quote_source,
                               user, db, target_expiration=target_expiration)
    if desk.get("error"):
        return desk
    ranked = desk["ranked"]
    if not ranked:
        return {"error": desk.get("note") or "No candidate trades to review."}

    focus_index = None
    if evaluate:
        focus_index = 0                    # the evaluated trade IS candidates[0]
    elif focus:
        focus_index = _find_focus_index(ranked, focus.get("structure"), focus.get("expiration"),
                                        focus.get("short_strike"))
        if focus_index is None:
            return {"error": "The selected trade is no longer among the current candidates "
                             "(market data may have moved — rescan and retry)."}
    single = focus_index is not None
    row = ranked[focus_index] if single else ranked[0]
    top_pick = (f"{row.get('label')} @ {_strikes(row)} exp {row.get('expiration')} ({row.get('dte')}d) — "
                f"desk {row.get('desk_score')}/100, PoP(keep) {row.get('prob_keep_pct')}%")
    payload_json = json.dumps(_desk_payload(desk, focus_index=focus_index), indent=2, default=_json_default)
    intro = ("The user has SELECTED ONE trade for a focused desk review — it is candidates[0]. Rule "
             "EXECUTE / REJECT / EXECUTE_MODIFIED on THAT specific trade (also_ranked[] is context only)."
             if single else
             "The user is EVALUATING which income trade to enter — none placed yet. Pick the best from candidates[].")
    header = ("## DESK REVIEW — STRUCTURED INPUT (JSON)\n"
              + intro + " Position sizing is the PM's call only (a Kelly-% of capital, never a contract count).\n"
              "Everything below — option pricing, bid/ask, per-leg greeks, the strategy legs, technical "
              "analysis, market structure, volume profile, and every algorithmic metric — is PRE-COMPUTED and "
              "authoritative. Read the fields; do NOT recompute or invent any value. Treat the ranking as ONE "
              "input; recommend a candidate by its EXACT `strikes`.\n\n"
              f"```json\n{payload_json}\n```\n")

    # A genuine desk DEBATE: Quant proposes → Risk challenges → Quant rebuts → PM adjudicates.
    quant = await _run_agent("quant", _QUANT_PERSONA, header, api_key, model)
    risk_ctx = header + f"\n## QUANT DESK — PROPOSAL (reasoning + pick)\n{quant['content']}\n"
    risk = await _run_agent("risk", _RISK_PERSONA, risk_ctx, api_key, model)
    rebut_ctx = risk_ctx + f"\n## RISK DESK — CHALLENGE\n{risk['content']}\n"
    rebuttal = await _run_agent("rebuttal", _QUANT_REBUTTAL_PERSONA, rebut_ctx, api_key, model)
    pm_ctx = rebut_ctx + f"\n## QUANT DESK — REBUTTAL\n{rebuttal['content']}\n"
    pm = await _run_agent("pm", _PM_PERSONA, pm_ctx, api_key, model)

    final = {
        "verdict": pm["verdict"],                                          # EXECUTE | REJECT | EXECUTE_MODIFIED
        "action_needed": pm["action_needed"],
        "decision": _extract_line(pm["content"], "TRADE_PARAMETERS") or pm["verdict"],
        "rationale": _extract_line(pm["content"], "WINNING_ARGUMENT"),
        "winning_argument": _extract_line(pm["content"], "WINNING_ARGUMENT"),
        "sizing": _extract_line(pm["content"], "SIZING_ALLOCATION"),
        "desk_mandate": _extract_line(pm["content"], "DESK_MANDATE"),
        "quant_choice": _extract_line(quant["content"], "CHOICE"),
        "quant_agrees_with_algo": _extract_line(quant["content"], "AGREES_WITH_ALGO"),
        "rebuttal_stance": _extract_line(rebuttal["content"], "STANCE"),
        "final_pick": _extract_line(rebuttal["content"], "FINAL PICK") or _extract_line(quant["content"], "CHOICE"),
        "algo_top_pick": top_pick,
        "risk_verdict": risk["verdict"],
    }
    # Resolve the desk's recommended trade back to a ranked-trade index so the UI can Explore it
    # (unless the PM REJECTed). The PM's TRADE_PARAMETERS is authoritative; fall back to the debate's pick.
    chosen_index = None
    if (pm["verdict"] or "").strip().upper() != "REJECT":
        for cand in (final["decision"], final["final_pick"], final["quant_choice"]):
            chosen_index = _resolve_pick(cand, ranked)
            if chosen_index is not None:
                break
        if chosen_index is None and single:            # single-trade review defaults to the reviewed trade
            chosen_index = focus_index
    final["chosen_index"] = chosen_index
    return {
        "ticker": desk["ticker"],
        "n_trades": len(ranked),
        "mode": "single" if single else "ranking",
        "reviewed_trade": top_pick if single else None,
        "quant": quant, "risk": risk, "rebuttal": rebuttal, "pm": pm,
        "final_recommendation": final,
    }
