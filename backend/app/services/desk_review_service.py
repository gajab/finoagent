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

from .lifecycle_service import compute_pretrade_metrics, terminal_payoff_curve
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
_SKEW_EXTREME_BPS = 1500

# Volatility Risk Premium (implied ÷ realized). ≥1 is favourable (implied over-priced = a seller's edge).
# Below 1 = negative VRP: penalise IN PROPORTION to the gap, and HARD-BLOCK past the ratio floor — that is
# the "crushed implied vol vs a stock that actually moves" trap where premium selling has no edge.
_VRP_BLOCK_RATIO = 0.70          # implied < 70% of realized → VETO the trade
_VRP_PENALTY_K = 22              # points per 1.0 of (1 − IV/HV): 0.9→−2, 0.5→−11, 0.2→−18
_VRP_PENALTY_CAP = 25

_STOCK_STRUCTURES = {"covered_call", "collar"}     # hold 100 shares/contract
_BULLISH_INCOME = {"cash_secured_put", "put_credit_spread", "jade_lizard"}
_BEARISH_INCOME = {"call_credit_spread", "collar"}
_NEUTRAL_INCOME = {"iron_condor", "jade_lizard"}


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
    """Combined leg dicts usable by BOTH terminal_payoff_curve and higher_order_greeks."""
    dte_years = max(int(opp.get("dte") or 0), 1) / 365.0
    contracts = int(opp.get("contracts") or 1)
    legs = []
    for l in opp.get("legs", []):
        right = "C" if str(l.get("type", "")).upper().startswith("C") else "P"
        sign = 1 if str(l.get("action", "")).upper().startswith("B") else -1
        legs.append({
            "strike": l.get("strike"), "right": right, "sign": sign,
            "qty": contracts, "iv": l.get("iv"), "dte_years": dte_years,
            "price": l.get("mid"),
        })
    return legs


def _robust_iv(opp: dict, hv: Optional[float]) -> float:
    """A sane implied-vol for the payoff distribution. yfinance IVs are garbage when
    the market is closed (near-zero on OTM strikes), which collapses the lognormal
    weights (PoP→100%, Omega→∞) — so reject anything outside [5%, 300%] and fall back
    to the ATM smile IV, then realized vol, then a 30% floor."""
    atm = (opp.get("atm_iv_pct") or 0) / 100.0
    leg_ivs = [(l.get("iv") or 0) / 100.0 for l in opp.get("legs", []) if (l.get("iv") or 0) > 3.0]
    leg_avg = (sum(leg_ivs) / len(leg_ivs)) if leg_ivs else 0.0
    for v in (atm, leg_avg, hv):
        if v and 0.05 <= v <= 3.0:
            return v
    return 0.30


def _opp_desk_metrics(opp: dict, spot: float, sofr_pct: float, hv: Optional[float] = None) -> dict:
    """Full desk read (trader/pm/risk/quant) for one income opportunity."""
    legs = _opp_legs(opp)
    contracts = int(opp.get("contracts") or 1)
    stock_shares = 100.0 * contracts if opp.get("structure") in _STOCK_STRUCTURES else 0.0
    scenarios = terminal_payoff_curve(legs, stock_shares, spot)

    ml = opp.get("max_loss")
    mp = opp.get("max_profit")
    capital = abs(ml) if ml is not None else float(opp.get("collateral") or 0.0)
    avg_iv = _robust_iv(opp, hv)

    return compute_pretrade_metrics(
        legs, spot, scenarios, capital,
        (-abs(ml) if ml is not None else None), mp,
        avg_iv, int(opp.get("dte") or 0),
        stock_shares=stock_shares, sofr_pct=sofr_pct,
        realized_vol=hv,            # P-measure — widens the payoff law when realized > implied
    )


def _ta_alignment(opp: dict, ta: dict, gex: Optional[dict] = None) -> tuple[float, str, list]:
    """Does this trade fit the regime / smart-money structure? Evaluated on the MEDIUM-TERM read
    (6-month history, DAILY bars) — the swing horizon that governs a multi-week income option, not
    intraday noise or multi-year lag. Returns (score_bonus ±, note, factors) where `factors` is the
    itemized [{label, points}] breakdown of how the technicals moved the score."""
    inst = (ta or {}).get("institutional") or {}
    reg = inst.get("regime") or {}
    bias, mode = reg.get("bias"), reg.get("mode")
    s = opp.get("structure")
    factors: list[dict] = []
    notes: list[str] = []

    def add(label: str, pts: float, note: str):
        factors.append({"label": label, "points": pts}); notes.append(note)

    if bias == "bullish":
        if s in _BULLISH_INCOME:
            add("Regime fit", 6, "with the bullish regime")
        elif s in _BEARISH_INCOME:
            add("Regime fit", -5, "against the bullish regime")
    elif bias == "bearish":
        if s in _BEARISH_INCOME:
            add("Regime fit", 6, "with the bearish regime")
        elif s in _BULLISH_INCOME:
            add("Regime fit", -5, "against the bearish regime")
    if mode == "range" and s in _NEUTRAL_INCOME:
        add("Range fit", 5, "neutral premium suits the range")
    # Short strike protected by a value-area edge / order block on the safe side.
    vp = inst.get("volume_profile") or {}
    ss = opp.get("short_strike")
    if ss and vp.get("val") and vp.get("vah"):
        if s in _BULLISH_INCOME and ss <= vp["val"]:
            add("Value area", 3, "short strike below the value area")
        elif s in _BEARISH_INCOME and ss >= vp["vah"]:
            add("Value area", 3, "short strike above the value area")
    # Dealer gamma regime (GEX proxy) — long gamma suppresses vol (a good backdrop for selling premium);
    # short gamma exacerbates it (dangerous). A STOCK-level positioning read applied to every trade.
    if gex and gex.get("regime") == "long":
        add("Gamma regime", 4, "dealers long gamma — vol-suppressed")
    elif gex and gex.get("regime") == "short":
        add("Gamma regime", -6, "dealers short gamma — vol-expansion risk")
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
    """Dealer Gamma-Exposure (GEX) proxy from the front option chain — a POSITIONING read plain
    price/vol TA can't see. Convention: dealers are long call gamma (+) and short put gamma (−), so
    GEX = Σ(γ·OI, calls) − Σ(γ·OI, puts), scaled to $ per 1% move.
      • GEX > 0 → dealers LONG gamma → they fade moves (sell rallies / buy dips) → vol SUPPRESSED,
        mean-reverting → a GOOD backdrop for selling premium.
      • GEX < 0 → dealers SHORT gamma → they chase moves → vol EXPANSION, trending → DANGEROUS,
        especially for delta-neutral structures (iron condors).
    Retail-data proxy (open interest + a modelled γ), NOT classified dealer flow — labelled as such."""
    try:
        import yfinance as yf
        from datetime import datetime as _dt
        from .hedging_service import _bs_greeks
        stock = yf.Ticker(_norm_ticker(ticker))
        spot = None
        try:
            spot = _fin((stock.fast_info or {}).get("last_price")) or None
        except Exception:  # noqa: BLE001
            spot = None
        if not spot:
            h = stock.history(period="1d")
            spot = _fin(h["Close"].iloc[-1]) if len(h) else None
        exps = list(stock.options or [])
        if not spot or not exps:
            return {}
        today = _dt.utcnow().date()
        rows: list[tuple] = []                       # (strike, dte, iv, right, oi)
        for exp in exps[:2]:                         # front expiries dominate dealer gamma
            try:
                dte = (_dt.strptime(exp, "%Y-%m-%d").date() - today).days
            except Exception:  # noqa: BLE001
                continue
            if dte <= 0 or dte > 60:
                continue
            oc = stock.option_chain(exp)
            for df, right in ((oc.calls, "C"), (oc.puts, "P")):
                for k, iv, oi in zip(df["strike"], df["impliedVolatility"], df["openInterest"]):
                    k, iv, oi = _fin(k) or 0.0, _fin(iv) or 0.0, _fin(oi) or 0.0
                    if k <= 0 or oi <= 0 or not (0.02 < iv < 3.0) or abs(k / spot - 1) > 0.25:
                        continue
                    rows.append((k, dte, iv, right, oi))
        if len(rows) < 6:                            # too thin to be a reliable positioning read
            return {}

        def gex_at(S: float) -> float:
            tot = 0.0
            for k, dte, iv, right, oi in rows:
                gamma = _fin(_bs_greeks(S, k, dte, iv, right).get("gamma")) or 0.0
                tot += (1.0 if right == "C" else -1.0) * gamma * oi * 100 * S * S * 0.01
            return tot

        gex = _fin(gex_at(spot))
        if gex is None:
            return {}
        flip, prev_s, prev_v = None, None, None       # zero-gamma flip — sweep spot ±12%
        for i in range(25):
            S = spot * (0.88 + 0.24 * i / 24.0)
            v = gex_at(S)
            if prev_v is not None and (prev_v < 0 <= v or prev_v > 0 >= v):
                flip = round((prev_s + S) / 2.0, 2); break
            prev_s, prev_v = S, v

        return {"gex_bn": round(gex / 1e9, 2), "regime": "long" if gex >= 0 else "short",
                "flip_level": flip, "spot": round(spot, 2), "n_strikes": len(rows), "proxy": True}
    except Exception as exc:  # noqa: BLE001
        logger.debug("GEX proxy failed for %s: %s", ticker, exc)
        return {}


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

def _algo_grade(opp: dict, dm: dict, spot: float, sofr_pct: float, atm_iv_pct: Optional[float],
                iv_rank: Optional[float], beta: Optional[float], events_n: int,
                hv: Optional[float] = None, gex: Optional[dict] = None) -> dict:
    pm, rk = (dm or {}).get("pm") or {}, (dm or {}).get("risk") or {}
    merits, demerits, blocking = [], [], []
    # Itemized signed contributions (points) by factor — so the UI can show each adjustment as a bar
    # and the desk score is auditable: desk_score = base_quality + regime + Σ(these).
    comp: dict[str, float] = {"expectation": 0.0, "vrp": 0.0, "moneyness": 0.0,
                              "skew": 0.0, "liquidity": 0.0, "beta": 0.0}

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
    iv_hv = opp.get("iv_hv_ratio")
    if iv_hv is not None:
        if iv_hv >= 1.1 and (iv_rank or 0) >= 50:
            merits.append(f"rich VRP (IV/HV {iv_hv}, IV-rank {iv_rank})"); comp["vrp"] += 6
        elif iv_hv < 1.0:
            pen = min(round((1.0 - iv_hv) * _VRP_PENALTY_K), _VRP_PENALTY_CAP)   # magnitude-scaled by the gap
            if pen > 0:
                comp["vrp"] -= pen
                demerits.append(f"negative VRP — implied {round(iv_hv*100)}% of realized (IV/HV {iv_hv})")
            if iv_hv < _VRP_BLOCK_RATIO:
                blocking.append(f"crushed vol — implied only {round(iv_hv*100)}% of realized (negative VRP, no edge)")

    # 3) Moneyness — a near-ATM short leg makes 'income' a DIRECTIONAL bet. Measured against the DUAL
    #    boundary (the WIDER of the implied Q-move and the physical P-move), so a strike that looks
    #    'deep' under crushed IV but is physically exposed can no longer hide.
    dual_em, imp_em, phys_em = _dual_move_pct(opp, hv)
    nss = _nearest_short_sigmas(opp, spot, dual_em)
    if nss is not None and nss < 0.5:
        demerits.append(f"near-ATM short leg ({nss}σ dual) — directional, not cushioned"); comp["moneyness"] -= 12
    elif nss is not None and nss < 1.0:
        demerits.append(f"thin cushion ({nss}σ dual)"); comp["moneyness"] -= 4
    elif nss is not None and nss >= 1.5:
        merits.append(f"deep cushion ({nss}σ dual)"); comp["moneyness"] += 5
    pmp = _prob_max_profit(opp)
    if pmp is not None and pmp < 50:
        demerits.append(f"full-credit prob only {pmp}%"); comp["moneyness"] -= 6
    elif pmp is not None and pmp >= 85:
        merits.append(f"full-credit prob {pmp}%"); comp["moneyness"] += 3

    # 4) Skew — extreme = 'pennies in front of a steamroller'.
    sliv = _short_leg_iv(opp)
    ss_bps = round((sliv - atm_iv_pct) * 100) if (sliv is not None and atm_iv_pct is not None) else None
    if ss_bps is not None and ss_bps >= _SKEW_EXTREME_BPS:
        demerits.append(f"extreme skew ({ss_bps}bps)"); comp["skew"] -= 6

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

    # 7) Systemic beta — a high-beta name is a LEVERAGED market bet, not idiosyncratic income.
    if beta is not None and beta >= 2.0:
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

    adj = sum(comp.values())
    return {"adj": adj, "merits": merits, "demerits": demerits, "blocking": blocking, "components": comp,
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
) -> dict:
    """Rank ALL candidate income trades (best → worst) by a blended desk score:
    the algorithmic Quant 0–100 score adjusted for technical/regime alignment."""
    ticker = _norm_ticker(ticker)
    scan = await run_derivative_income(
        ticker, target_dte=target_dte, min_prob=min_prob, min_income=min_income,
        structures=structures, quote_source=quote_source, user=user, db=db,
    )
    if scan.get("error"):
        return {"error": scan["error"]}

    ctx = scan.get("context") or {}
    spot = float(ctx.get("spot") or scan.get("spot") or 0.0)
    sofr_pct = float(ctx.get("sofr_pct") or 5.0)
    hv = ((ctx.get("hv30_pct") or ctx.get("hv20_pct") or 0) / 100.0) or None
    ta, portfolio_fit, gex = await asyncio.gather(
        asyncio.to_thread(_ta_sync, ticker),
        asyncio.to_thread(_portfolio_fit_sync, ticker),
        asyncio.to_thread(_gex_sync, ticker),
    )

    opportunities = scan.get("opportunities", [])
    max_dte = max((int(o.get("dte") or 0) for o in opportunities), default=int(target_dte or 45))
    events_pre = _events_in_window(scan, ta, max_dte)        # computed once — also feeds the grade
    events_n = len(events_pre)
    vsx = ctx.get("vol_stats") or {}
    atm_iv_pct, iv_rank = vsx.get("iv_atm_pct"), vsx.get("iv_rank")
    beta = (portfolio_fit or {}).get("beta_1y_spx")

    ranked: list[dict] = []
    for opp in opportunities:
        try:
            dm = _opp_desk_metrics(opp, spot, sofr_pct, hv)
        except Exception as exc:  # noqa: BLE001 — one bad trade must not kill the desk
            logger.debug("desk metrics failed (%s): %s", opp.get("label"), exc)
            dm = {"trader": {}, "pm": {}, "risk": {}, "quant": {"score": None, "verdict": None, "reasons": []}}
        base = (dm.get("quant") or {}).get("score")
        if base is None:
            base = (opp.get("confidence") or {}).get("score") or 50
        bonus, note, ta_factors = _ta_alignment(opp, ta, gex)
        # Fold EVERY deterministic institutional factor (VRP / moneyness / skew / liquidity / tail /
        # beta / events) into the score + a hard-BLOCK filter, so the trade reaching the LLM is vetted.
        g = _algo_grade(opp, dm, spot, sofr_pct, atm_iv_pct, iv_rank, beta, events_n, hv=hv, gex=gex)
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
            {"label": "Skew",        "points": round(c["skew"], 1)},
            {"label": "Liquidity",   "points": round(c["liquidity"], 1)},
            {"label": "Beta",        "points": round(c["beta"], 1)},
        ]
        ranked.append({**opp, "desk_metrics": dm, "desk_score": desk_score, "ta_note": note,
                       "algo_grade": grade, "approval_odds": approval, "grade_merits": g["merits"],
                       "grade_demerits": g["demerits"], "grade_blocking": g["blocking"],
                       "base_quality": round(base, 1), "grade_adjustments": grade_adjustments,
                       "ta_factors": ta_factors,
                       # Q-vs-P: the boundary read (imp/phys moves + strike distance) and the vol pair
                       # that weighted the base score — surfaced as the number-line in Quant Analysis.
                       "qp": {**g["qp"], **(dm.get("vrp") or {})}})

    # BLOCKING trades (structurally broken, etc.) sink to the bottom; then desk score, Sortino, yield —
    # so the top-ranked trade has already cleared every mechanical filter the LLM desk applies.
    ranked.sort(key=lambda r: (
        not r["grade_blocking"],
        r["desk_score"],
        (r["desk_metrics"].get("pm") or {}).get("sortino") or 0,
        r.get("premium_annualized_pct") or 0,
    ), reverse=True)

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
        "vol_stats": vol_stats,
        "corporate_actions": corporate_actions,
        "portfolio_fit": portfolio_fit or None,
        "gex": gex or None,             # dealer gamma-regime proxy (stock-level chip)
        "expiry_meta": {s.get("expiration"): {"max_oi_strike": s.get("max_oi_strike"),
                                              "atm_iv_pct": s.get("atm_iv_pct")}
                        for s in scan.get("expiry_summaries", [])},
        "events": events_pre,
        "ranked": ranked,
        "algo_top_pick": ranked[0] if ranked else None,
        "n_trades": len(ranked),
        "note": scan.get("note"),
    }


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
WATCH: <the one move, level or event that would threaten the thesis>""",
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
) -> dict:
    """A genuine desk DEBATE: Quant proposes → Risk challenges → Quant rebuts → PM adjudicates.
    Each agent reasons explicitly, sees all prior turns + the full pre-computed JSON, and grounds
    every claim in a GIVEN field. Two modes: ranking (focus=None → pick the best of the top set)
    and single-trade (Desk Review v2 — `focus`={structure, expiration, short_strike} → rule
    EXECUTE/REJECT on THAT one trade)."""
    desk = await rank_desk(ticker, target_dte, min_prob, min_income, structures, quote_source, user, db)
    if desk.get("error"):
        return desk
    ranked = desk["ranked"]
    if not ranked:
        return {"error": desk.get("note") or "No candidate trades to review."}

    focus_index = None
    if focus:
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
