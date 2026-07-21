"""Derivative Income — sell option premium with a high probability of NOT being
exercised, ranked to beat the SOFR hurdle.

The desk's brief: generate income on the user's *holdings* (covered calls) and on
cash that sits in a money-market fund earning ~SOFR (cash-secured puts), plus
defined-risk variants (collars, credit spreads) — always filtered to a target
no-assignment probability (default 85%) and a minimum premium (default $20).

The no-exercise probability is read from the **market-implied risk-neutral density
(RND)**, not a flat-vol approximation. We reuse the institutional vol engine the
hedging desk already runs (``quant_service`` SVI smile → Breeden-Litzenberger RND,
wired to a live chain by ``hedging_service._build_rnd``):

  * Covered call, short strike ``Kc > spot`` — assigned iff ``S_T ≥ Kc`` →
    ``P(keep) = RND.prob_below(Kc)``.
  * Cash-secured put, short strike ``Kp < spot`` — assigned iff ``S_T ≤ Kp`` →
    ``P(keep) = RND.prob_above(Kp)``.
  * The 85%-safe strike is read straight off the inverse CDF
    (``RND.strike_for_prob_below``) and snapped to a listed strike.

When a smile can't be fit (illiquid name, <6 strikes) we fall back to flat-vol
``stock_service.bs_prob_otm`` and badge the row ``BS`` instead of ``RND``.

API-efficiency (the user's explicit constraint): by default we only look at
**monthly** expirations ≤45 DTE (≈1–2 chain fetches/ticker). A caller-supplied
``target_dte`` switches to expiries within ±10 days across all listing types,
capped at ``MAX_EXPIRIES``. Per-(ticker, expiry) analysis is DB-cached, and the
portfolio sweep is paginated to the top-N holdings.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timedelta
from typing import Optional, TYPE_CHECKING

import numpy as np

from .quote_providers import get_provider, OptionChain, OptionQuote
# Reuse the hedging desk's proven chain → Greeks/RND helpers (DRY — same pattern
# box_strategy_service uses to borrow autocallable_service._risk_free_rate).
from .hedging_service import _bs_greeks, _split_chain, _nearest_strike, _build_rnd, _chain_iv_arrays
from .quant_service import structure_smile_risk, calibrate_heston
from .stock_service import bs_prob_otm
from .trade_math import annualized_return_pct
from .cache_service import get_cached, set_cached

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from ..models import User

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100          # shares per US equity option contract
DEFAULT_RISK_FREE = 0.045
DEFAULT_MIN_PROB = 0.85            # target probability of NOT being assigned
DEFAULT_MIN_INCOME = 20.0         # minimum premium ($/contract) to surface
MIN_DTE = 7
MAX_DEFAULT_DTE = 45              # default-mode horizon (monthlies only)
TARGET_DTE_BAND = 10             # ± window around a user-supplied target DTE
MAX_EXPIRIES = 3                 # hard cap on chain fetches per ticker
PER_STRUCTURE_CAP = 3            # CC/CSP candidates kept per expiry
TTL_ANALYSIS = 900              # 15 min — matches _TTL_PRICE
TTL_PORTFOLIO = 300
EVENTS_HORIZON_DAYS = 90         # always surface events for the next 90 days …
                                # … even though the option scan defaults to ≤45 DTE

# Cash-settled, European-exercise index options: no early assignment → strictly
# safer for the "never exercised" goal. ^-prefixed major indices are treated the
# same. Editable single source of truth.
EUROPEAN_CASH_SETTLED = {
    "SPX", "SPXW", "XSP", "NDX", "NDXP", "RUT", "RUTW", "MRUT", "XND",
    "VIX", "DJX", "OEX", "XEO", "MXEA", "MXEF", "SPXPM",
}

# Curated upcoming high-impact macro events (editable; informational only — they
# move IV/HV and gap risk). Keep it chronological and ~4 months deep so any 90-day
# lookahead is populated. Same curated-list spirit as BOX_SCAN_UNIVERSE.
MACRO_EVENTS: list[tuple[str, str]] = [
    ("2026-07-15", "US CPI"),
    ("2026-07-17", "Monthly OPEX (triple witching)"),
    ("2026-07-29", "FOMC decision"),
    ("2026-08-07", "US jobs report (NFP)"),
    ("2026-08-13", "US CPI"),
    ("2026-08-21", "Monthly OPEX"),
    ("2026-09-04", "US jobs report (NFP)"),
    ("2026-09-10", "US CPI"),
    ("2026-09-16", "FOMC decision"),
    ("2026-09-18", "Quarterly OPEX (triple witching)"),
    ("2026-10-02", "US jobs report (NFP)"),
    ("2026-10-13", "US CPI"),
    ("2026-10-16", "Monthly OPEX"),
    ("2026-10-28", "FOMC decision"),
    ("2026-11-06", "US jobs report (NFP)"),
    ("2026-11-13", "US CPI"),
]


# ---------------------------------------------------------------------------
# Ticker / expiry helpers
# ---------------------------------------------------------------------------

def _norm_ticker(t: str) -> str:
    t = (t or "").strip().upper()
    if t.startswith("."):           # ".XSP" → "^XSP" (router convention)
        t = "^" + t[1:]
    return t


def _is_european(ticker: str) -> bool:
    base = ticker.lstrip("^.").upper()
    return ticker.startswith("^") or base in EUROPEAN_CASH_SETTLED


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    offset = (4 - d.weekday()) % 7      # weekday(): Mon=0 … Fri=4
    return date(year, month, 1 + offset + 14)


def _is_monthly_expiry(d: date) -> bool:
    """Standard monthly options expire the 3rd Friday of the month."""
    return d == _third_friday(d.year, d.month)


def _select_expirations(all_exps: list[str], target_dte: Optional[int],
                        today: date) -> list[tuple[str, int]]:
    """Pick the expirations to actually fetch — the API-budget gate.

    Default (``target_dte`` is None): monthlies only, ``MIN_DTE ≤ DTE ≤ 45``.
    Target mode: any listing type within ``±TARGET_DTE_BAND`` of the target,
    nearest first. Both capped at ``MAX_EXPIRIES``; resilient fallbacks keep a
    weekly-only name from returning nothing.
    """
    parsed: list[tuple[str, int, date]] = []
    for s in all_exps:
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        dte = (d - today).days
        if dte >= 1:
            parsed.append((s, dte, d))
    if not parsed:
        return []

    if target_dte is None:
        monthly = [(s, dte) for (s, dte, d) in parsed
                   if _is_monthly_expiry(d) and MIN_DTE <= dte <= MAX_DEFAULT_DTE]
        if monthly:
            monthly.sort(key=lambda x: x[1])
            return monthly[:MAX_EXPIRIES]
        # Fallback: nearest few expiries inside the horizon (e.g. weekly-only ETFs).
        near = sorted([(s, dte) for (s, dte, d) in parsed if dte <= MAX_DEFAULT_DTE],
                      key=lambda x: x[1])
        if near:
            return near[:MAX_EXPIRIES]
        nearest = min(parsed, key=lambda x: x[1])
        return [(nearest[0], nearest[1])]

    band = [(s, dte) for (s, dte, d) in parsed if abs(dte - target_dte) <= TARGET_DTE_BAND]
    band.sort(key=lambda x: abs(x[1] - target_dte))
    if band:
        return band[:MAX_EXPIRIES]
    nearest = min(parsed, key=lambda x: abs(x[1] - target_dte))
    return [(nearest[0], nearest[1])]


def _macro_events_in_window(start: date, end: date) -> list[str]:
    out = []
    for ds, label in MACRO_EVENTS:
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            continue
        if start <= d <= end:
            out.append(f"{label} ({ds})")
    return out


# ---------------------------------------------------------------------------
# Per-ticker context (HV + earnings) — yfinance, off-thread, best-effort
# ---------------------------------------------------------------------------

def _context_sync(ticker: str) -> dict:
    """Realized vol (20/30d, annualized), 52-week high/low and the next earnings
    date, from ONE 1-year history pull. Best-effort: returns ``None`` defaults if
    yfinance is unavailable for the name."""
    out: dict = {"hv20": None, "hv30": None, "next_earnings": None,
                 "week52_high": None, "week52_low": None, "hv_series": None}
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist is not None and not hist.empty:
            closes = hist["Close"].dropna()
            if len(closes) > 5:
                out["week52_high"] = round(float(closes.max()), 2)
                out["week52_low"] = round(float(closes.min()), 2)
            log_ret = np.log(closes / closes.shift(1)).dropna()
            if len(log_ret) >= 20:
                out["hv20"] = round(float(log_ret.tail(20).std() * math.sqrt(252)), 4)
            if len(log_ret) >= 30:
                out["hv30"] = round(float(log_ret.tail(30).std() * math.sqrt(252)), 4)
            # Rolling 30-day annualized realized-vol series → drives vol rank/percentile.
            roll = (log_ret.rolling(30).std() * math.sqrt(252)).dropna()
            if len(roll) >= 20:
                out["hv_series"] = [round(float(x), 4) for x in roll.tolist()]
        # Next earnings — try the modern earnings_dates frame, then the calendar.
        try:
            ed = getattr(stock, "earnings_dates", None)
            if ed is not None and not ed.empty:
                today = date.today()
                future = [ix.date() for ix in ed.index
                          if hasattr(ix, "date") and ix.date() >= today]
                if future:
                    out["next_earnings"] = min(future).isoformat()
        except Exception:  # noqa: BLE001
            pass
        if out["next_earnings"] is None:
            try:
                cal = stock.calendar
                ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
                if ed:
                    d0 = ed[0] if isinstance(ed, (list, tuple)) else ed
                    if hasattr(d0, "isoformat"):
                        out["next_earnings"] = d0.isoformat()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("derivinc context fetch failed for %s: %s", ticker, exc)
    return out


async def _get_sofr() -> tuple[float, str]:
    """(rate_fraction, label) for SOFR from keyless FRED; falls back to ^IRX-ish default."""
    try:
        from . import rates_service
        series = await rates_service.fred_series(rates_service.SERIES["sofr"])
        val = rates_service.latest(series)
        if val is not None and val > 0:
            return float(val) / 100.0, "SOFR (FRED)"
    except Exception as exc:  # noqa: BLE001
        logger.debug("SOFR fetch failed: %s", exc)
    return DEFAULT_RISK_FREE, f"Default ({DEFAULT_RISK_FREE * 100:.1f}%)"


# ---------------------------------------------------------------------------
# Probability + expected value off the RND (with flat-vol fallback)
# ---------------------------------------------------------------------------

def _prob_keep(rnd, strike: float, right: str, spot: float, dte: int,
               r: float, iv: Optional[float]) -> tuple[Optional[float], str]:
    """Probability the short option expires OTM (we keep the premium, unassigned).

    Market-implied off the RND when available; otherwise flat-vol Black-Scholes.
    """
    if rnd is not None:
        p = rnd.prob_below(strike) if right == "C" else rnd.prob_above(strike)
        return float(p), "RND"
    if iv and iv > 0:
        T = max(dte, 1) / 365.0
        p = bs_prob_otm(spot, strike, T, r, iv, "call" if right == "C" else "put")
        return float(p), "BS"
    return None, ""


def _prob_reach(rnd, strike: float, spot: float, dte: int,
                iv: Optional[float]) -> Optional[float]:
    """Probability the underlying *reaches* this strike by expiry (finishes on the
    far side of it, in the direction away from spot). For a leg above spot that's
    P(S_T ≥ K); below spot, P(S_T ≤ K). Market-implied off the RND, else flat-vol BS."""
    if strike >= spot:
        if rnd is not None:
            return float(rnd.prob_above(strike))
        if iv and iv > 0:
            return float(bs_prob_otm(spot, strike, max(dte, 1) / 365.0, DEFAULT_RISK_FREE, iv, "put"))
    else:
        if rnd is not None:
            return float(rnd.prob_below(strike))
        if iv and iv > 0:
            return float(bs_prob_otm(spot, strike, max(dte, 1) / 365.0, DEFAULT_RISK_FREE, iv, "call"))
    return None


def _expected_intrinsic(rnd, strike: float, right: str) -> Optional[float]:
    """Risk-neutral E[intrinsic at expiry] per share via ∫ payoff·f(K) dK."""
    if rnd is None:
        return None
    payoff = (np.maximum(rnd.K - strike, 0.0) if right == "C"
              else np.maximum(strike - rnd.K, 0.0))
    return float(np.trapezoid(payoff * rnd.pdf, rnd.K))


def _vol_spike_pnl(strike: float, iv: Optional[float], spot: float, dte: int,
                   contracts: int = 1) -> Optional[float]:
    """$ P&L on the short leg if IV jumps 10 vol points (short-vol → a loss)."""
    if not iv or iv <= 0:
        return None
    res = structure_smile_risk(
        [{"action": "SELL", "strike": strike, "contracts": contracts, "iv": iv * 100.0}],
        spot, dte,
    )
    return res.get("pnl_on_vol_spike")


# ---------------------------------------------------------------------------
# Opportunity builders
# ---------------------------------------------------------------------------

# Executability gates — drop quotes that won't realistically fill. A short option
# with no bid can't be sold; a very wide bid-ask or a near-empty book means the mid
# is a mirage. This is why so many "opportunities" were not actually tradeable.
# The bid-ask tolerance is NOT a flat cap — a high-beta / high-IV name quotes wider
# by nature, so we scale the allowed spread with the option's own IV and grant extra
# room to a deep book (which can be worked at mid). Only genuinely un-fillable quotes
# (no bid, dead book, spread beyond even the high-vol ceiling) are dropped.
BASE_SPREAD_PCT = 0.12     # base tolerance (fraction of mid) at low IV …
IV_SPREAD_COEF = 0.22      # … + this × IV  (e.g. IV 0.70 ⇒ ~27% tolerance)
MAX_SPREAD_CAP = 0.40      # hard ceiling — past this the mid is a mirage even for high vol
DEEP_BOOK = 100            # OI+vol above which a wide quote can still be worked at mid
TICK_TOLERANCE = 0.05      # always allow one 5-cent tick (cheap-option granularity)
MIN_LIQUIDITY = 10         # OI + volume floor per leg (truly dead strikes)


def _spread_pct(q: OptionQuote) -> Optional[float]:
    if q.mid and q.mid > 0 and q.ask > 0 and q.bid > 0:
        return round((q.ask - q.bid) / q.mid * 100, 1)
    return None


def _executable(q: OptionQuote) -> tuple[bool, str]:
    """Can this option realistically be traded near mid? Returns ``(ok, reason)``.

    Filters the non-fillable quotes (no bid, blown-out spread, empty book) that made
    many recommendations un-actionable.
    """
    if q.bid <= 0 or q.ask <= 0 or q.mid <= 0:
        return False, "no two-sided market (missing bid/ask)"
    depth = q.oi + q.volume
    if depth < MIN_LIQUIDITY:
        return False, f"thin book (OI {q.oi} + vol {q.volume} < {MIN_LIQUIDITY})"
    iv = q.iv if (q.iv and q.iv > 0) else 0.30
    allowed_pct = min(BASE_SPREAD_PCT + IV_SPREAD_COEF * iv, MAX_SPREAD_CAP)
    if depth >= DEEP_BOOK:                      # deep books can be worked even when quoted wide
        allowed_pct = min(allowed_pct * 1.4, MAX_SPREAD_CAP + 0.10)
    allowed = max(TICK_TOLERANCE, allowed_pct * q.mid)
    spread = q.ask - q.bid
    if spread > allowed:
        return False, f"bid-ask {spread / q.mid * 100:.0f}% > {allowed_pct * 100:.0f}% IV-scaled tolerance"
    return True, ""


def _leg(q: OptionQuote, action: str, exp: str, spot: float, dte: int,
         atm_iv: Optional[float], rnd=None) -> dict:
    iv = q.iv if q.iv else atm_iv
    g = _bs_greeks(spot, q.strike, dte, iv or 0.0, q.right)
    reach = _prob_reach(rnd, q.strike, spot, dte, iv)
    return {
        "action": action,
        "type": "CALL" if q.right == "C" else "PUT",
        "strike": round(q.strike, 2),
        "expiration": exp,
        "bid": round(q.bid, 2), "ask": round(q.ask, 2), "mid": round(q.mid, 2),
        "bid_ask_spread_pct": _spread_pct(q),      # execution slippage — costly to exit if wide
        "iv": round(iv * 100, 1) if iv else None,
        "oi": q.oi, "vol": q.volume,
        "prob_reach_pct": round(reach * 100, 1) if reach is not None else None,
        "delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"],
    }


def _opp_flags(richness: str, atm_iv: Optional[float], iv_hv_ratio: Optional[float]) -> list[dict]:
    """Only *opportunity-specific* flags. Common context (exercise style, earnings,
    macro events) is surfaced once at the top level, not repeated on every row."""
    flags: list[dict] = []
    iv_pct = round(atm_iv * 100, 1) if atm_iv else None
    hv_pct = round(iv_pct / iv_hv_ratio, 1) if (iv_pct and iv_hv_ratio) else None
    if richness == "rich":
        flags.append({"level": "good",
                      "text": f"IV ({iv_pct}%) rich vs HV ({hv_pct}%) — premium well-paid (good time to sell)"})
    elif richness == "cheap":
        flags.append({"level": "warn",
                      "text": f"IV ({iv_pct}%) cheap vs HV ({hv_pct}%) — premium light"})
    return flags


def _quant_block(rnd, calls: dict, puts: dict, strikes_all: list[float],
                 spot: float, dte: int, atm_iv: Optional[float]) -> dict:
    """Institutional quant diagnostics that build (or erode) confidence in a fill:
    SVI smile fit quality + arbitrage check, a QuantLib Heston (panic-aware) read,
    and the risk-neutral 1-sigma expected move."""
    q: dict = {"rnd_available": rnd is not None, "svi_rmse_vol_pts": None,
               "arb_free": None, "n_quotes": None, "heston": None,
               "expected_move_pct": None, "skew_pts": _skew_pts(rnd, spot)}
    if rnd is not None and getattr(rnd, "smile", None) is not None:
        sm = rnd.smile
        q["svi_rmse_vol_pts"] = round(sm.rmse * 100, 2)
        q["arb_free"] = bool(sm.arb_free)
        q["n_quotes"] = int(sm.n_quotes)
    if atm_iv:
        q["expected_move_pct"] = round(atm_iv * math.sqrt(max(dte, 1) / 365.0) * 100, 1)
    try:
        strikes, ivs, _c, _p = _chain_iv_arrays(calls, puts, strikes_all, spot)
        if len(strikes) >= 6:
            h = calibrate_heston(strikes, ivs, spot, dte)   # QuantLib, best-effort → None
            if h:
                q["heston"] = h
    except Exception:  # noqa: BLE001
        pass
    return q


def _confidence(opp: dict, quant: dict) -> dict:
    """A 5–99 confidence score for actually filling *and* trusting this trade,
    from the probability model, smile fit, QuantLib read and live liquidity."""
    score = 55
    reasons: list[str] = []
    if opp.get("prob_method") == "RND":
        score += 18; reasons.append("market-implied RND probability")
    else:
        score -= 12; reasons.append("flat-vol fallback (thin chain)")
    if quant.get("arb_free"):
        score += 8; reasons.append("arbitrage-free smile")
    elif quant.get("arb_free") is False:
        score -= 5; reasons.append("smile butterfly flag")
    rmse = quant.get("svi_rmse_vol_pts")
    if rmse is not None:
        if rmse <= 1.5:
            score += 8; reasons.append(f"tight vol fit ({rmse} pts)")
        elif rmse >= 4:
            score -= 8; reasons.append(f"loose vol fit ({rmse} pts)")
    liq = opp.get("liquidity", {})
    sp = liq.get("spread_pct")
    depth = (liq.get("oi") or 0) + (liq.get("volume") or 0)
    if sp is not None:
        if sp <= 6:
            score += 10; reasons.append("tight bid-ask")
        elif sp > 15:
            score -= 10; reasons.append("wide bid-ask")
    if depth >= 500:
        score += 8; reasons.append("deep liquidity")
    elif depth < 25:
        score -= 6; reasons.append("thin liquidity")
    h = quant.get("heston")
    if h and h.get("feller_ok"):
        score += 4; reasons.append("Heston Feller-stable")
    score = int(max(5, min(99, score)))
    label = "High" if score >= 75 else "Medium" if score >= 55 else "Low"
    return {"score": score, "label": label, "reasons": reasons[:4]}


def _richness(atm_iv: Optional[float], hv: Optional[float]) -> tuple[Optional[float], str]:
    if not atm_iv or not hv or hv <= 0:
        return None, "n/a"
    ratio = round(atm_iv / hv, 2)
    label = "rich" if ratio >= 1.1 else "cheap" if ratio <= 0.9 else "fair"
    return ratio, label


def _skew_pts(rnd, spot: float) -> Optional[float]:
    """Put-vs-call vol skew in vol points: IV(90% strike) − IV(110% strike) off the
    fitted smile. Positive = downside puts richer (normal equity skew)."""
    if rnd is None or getattr(rnd, "smile", None) is None:
        return None
    try:
        return round((float(rnd.smile.iv(spot * 0.90)) - float(rnd.smile.iv(spot * 1.10))) * 100, 1)
    except Exception:  # noqa: BLE001
        return None


def _rank_pctile(series: Optional[list], value: Optional[float]) -> tuple[Optional[int], Optional[int]]:
    """(rank, percentile) of *value* within *series* (both 0–100). Rank is where the
    value sits in the min→max range; percentile is the fraction of the series below it."""
    if not series or value is None:
        return None, None
    lo, hi = min(series), max(series)
    rank = max(0, min(100, round((value - lo) / (hi - lo) * 100))) if hi > lo else None
    pctile = round(sum(1 for x in series if x <= value) / len(series) * 100)
    return rank, pctile


def _vol_stats(ctx: dict, front_summary: dict) -> dict:
    """Per-ticker volatility read: IV/vol rank & percentile + skew. IV rank/percentile
    are positioned against the trailing 1-year *realized*-vol range (no free historical
    IV feed) — i.e. 'how rich is today's implied vol vs how much the stock actually moved'."""
    hv_series = ctx.get("hv_series") or []
    hv_cur = ctx.get("hv30")
    iv_atm = (front_summary.get("atm_iv_pct") / 100.0) if front_summary.get("atm_iv_pct") else None
    skew = (front_summary.get("quant") or {}).get("skew_pts")
    vol_rank, vol_pctile = _rank_pctile(hv_series, hv_cur)
    iv_rank, iv_pctile = _rank_pctile(hv_series, iv_atm)
    skew_dir = None
    if skew is not None:
        skew_dir = "put_skew" if skew > 0.5 else "call_skew" if skew < -0.5 else "flat"
    return {
        "iv_atm_pct": round(iv_atm * 100, 1) if iv_atm else None,
        "hv_current_pct": round(hv_cur * 100, 1) if hv_cur else None,
        "iv_rank": iv_rank, "iv_percentile": iv_pctile,
        "vol_rank": vol_rank, "vol_percentile": vol_pctile,
        "skew_pts": skew, "skew_direction": skew_dir,
        "skew_basis": "IV(90% strike) − IV(110% strike) in vol pts; positive = downside puts richer (sell puts)",
        "basis": "IV rank/percentile vs trailing 1y realized-vol range",
    }


def _term_structure(summaries: list[dict]) -> dict:
    """Contango (normal) vs backwardation (event/earnings panic) from scanned expiries'
    ATM IV. Backwardation = front IV > back IV → favor SHORTER DTE to harvest fast decay."""
    pts = sorted((s["dte"], s["atm_iv_pct"]) for s in summaries
                 if s.get("dte") and s.get("atm_iv_pct"))
    if len(pts) < 2:
        return {"state": None, "basis": "needs ≥2 scanned expiries; only one available"}
    (d0, v0), (d1, v1) = pts[0], pts[-1]
    diff = round(v1 - v0, 1)                       # back minus front, vol pts
    state = "backwardation" if diff < -0.5 else "contango" if diff > 0.5 else "flat"
    return {"state": state, "front_dte": d0, "front_iv_pct": v0, "back_dte": d1, "back_iv_pct": v1,
            "back_minus_front_pts": diff,
            "note": "backwardation (front IV > back) signals event/earnings panic → favor shorter DTE"}


def _single_leg_income(structure: str, label: str, q: OptionQuote, spot: float,
                       dte: int, exp: str, rnd, r: float, sofr_pct: float,
                       atm_iv: Optional[float], iv_hv_ratio: Optional[float],
                       richness: str, european: bool, earnings_before: Optional[str],
                       macro: list[str], min_prob: float, min_income: float,
                       ticker: str) -> Optional[dict]:
    """Covered call (right C) or cash-secured put (right P) on a single short strike."""
    right = q.right
    if not _executable(q)[0]:            # drop un-fillable quotes up front
        return None
    premium = q.mid * CONTRACT_MULTIPLIER
    if premium < min_income or q.mid <= 0:
        return None
    iv = q.iv if q.iv else atm_iv
    p_keep, method = _prob_keep(rnd, q.strike, right, spot, dte, r, iv)
    if p_keep is None or p_keep < min_prob:
        return None

    collateral = (spot if right == "C" else q.strike) * CONTRACT_MULTIPLIER
    premium_ann = annualized_return_pct(premium, collateral, dte)
    static_ret = premium / collateral * 100 if collateral else 0.0

    if right == "C":
        cushion_pct = (q.strike - spot) / spot * 100
        breakeven = round(spot - q.mid, 2)                 # position breakeven (downside)
        if_assigned = ((q.strike - spot) + q.mid) / spot * 100   # capped gain + premium
        # CSP earns SOFR on collateral; a covered call's collateral is the stock,
        # so SOFR-excess is the premium yield itself (alpha on top of holding).
        total_ann = premium_ann
        sofr_excess = round(premium_ann, 2)
    else:
        cushion_pct = (spot - q.strike) / spot * 100
        breakeven = round(q.strike - q.mid, 2)             # effective purchase price
        if_assigned = (spot - breakeven) / spot * 100      # discount vs spot if assigned
        total_ann = sofr_pct + premium_ann                 # MMF base + premium alpha
        sofr_excess = round(premium_ann, 2)

    g = _bs_greeks(spot, q.strike, dte, iv or 0.0, right)
    exp_intr = _expected_intrinsic(rnd, q.strike, right)
    expected_pnl = round(premium - exp_intr * CONTRACT_MULTIPLIER, 2) if exp_intr is not None else None

    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    return {
        "structure": structure,
        "label": label,
        "expiration": exp,
        "dte": dte,
        "short_strike": round(q.strike, 2),
        "short_strike_pct": round((q.strike - spot) / spot * 100, 1),
        "short_delta": g["delta"],
        "prob_keep_pct": round(p_keep * 100, 1),
        "prob_assign_pct": round((1 - p_keep) * 100, 1),
        "prob_method": method,
        "premium": round(premium, 2),
        "premium_per_share": round(q.mid, 2),
        "collateral": round(collateral, 2),
        "premium_annualized_pct": round(premium_ann, 2),
        "total_annualized_pct": round(total_ann, 2),
        "sofr_pct": round(sofr_pct, 2),
        "sofr_excess_pct": sofr_excess,
        "beats_sofr": premium_ann > sofr_pct,
        "static_return_pct": round(static_ret, 2),
        "if_assigned_return_pct": round(if_assigned, 2),
        "breakeven": breakeven,
        "cushion_pct": round(cushion_pct, 2),
        "max_profit": round(premium, 2) if right == "P" else round(premium + (q.strike - spot) * CONTRACT_MULTIPLIER, 2),
        "max_loss": None,                       # naked CSP / covered-call downside is open-ended
        "expected_pnl": expected_pnl,
        "greeks": {"delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"]},
        "theta_per_day": round(-g["theta"] * CONTRACT_MULTIPLIER, 2),   # short option → theta accrues to us
        "vol_spike_pnl": _vol_spike_pnl(q.strike, iv, spot, dte),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio,
        "premium_richness": richness,
        "liquidity": {"oi": q.oi, "volume": q.volume, "spread_pct": _spread_pct(q)},
        "exercise_style": "European (cash-settled)" if european else "American",
        "flags": flags,
        "legs": [_leg(q, "SELL", exp, spot, dte, atm_iv, rnd)],
    }


def _credit_spread(structure: str, label: str, short_q: OptionQuote, long_q: OptionQuote,
                   spot: float, dte: int, exp: str, rnd, r: float, atm_iv: Optional[float],
                   iv_hv_ratio: Optional[float], richness: str, european: bool,
                   earnings_before: Optional[str], macro: list[str], min_prob: float,
                   min_income: float, ticker: str) -> Optional[dict]:
    """Defined-risk put/call credit spread: sell ``short_q``, buy further-OTM ``long_q``."""
    right = short_q.right
    if not _executable(short_q)[0] or not _executable(long_q)[0]:   # both legs must fill
        return None
    net_credit = (short_q.mid - long_q.mid)
    premium = net_credit * CONTRACT_MULTIPLIER
    width = abs(short_q.strike - long_q.strike)
    if premium < min_income or net_credit <= 0 or width <= 0:
        return None
    iv = short_q.iv if short_q.iv else atm_iv
    p_keep, method = _prob_keep(rnd, short_q.strike, right, spot, dte, r, iv)
    if p_keep is None or p_keep < min_prob:
        return None

    capital = (width - net_credit) * CONTRACT_MULTIPLIER     # max loss = collateral
    if capital <= 0:
        return None
    premium_ann = annualized_return_pct(premium, capital, dte)
    cushion_pct = ((short_q.strike - spot) / spot * 100 if right == "C"
                   else (spot - short_q.strike) / spot * 100)
    g = _bs_greeks(spot, short_q.strike, dte, iv or 0.0, right)

    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    flags.append({"level": "good", "text": f"Defined risk — max loss capped at ${round(capital, 2)}"})
    return {
        "structure": structure,
        "label": label,
        "expiration": exp,
        "dte": dte,
        "short_strike": round(short_q.strike, 2),
        "short_strike_pct": round((short_q.strike - spot) / spot * 100, 1),
        "long_strike": round(long_q.strike, 2),
        "width": round(width, 2),
        "short_delta": g["delta"],
        "prob_keep_pct": round(p_keep * 100, 1),
        "prob_assign_pct": round((1 - p_keep) * 100, 1),
        "prob_method": method,
        "premium": round(premium, 2),
        "premium_per_share": round(net_credit, 2),
        "collateral": round(capital, 2),
        "premium_annualized_pct": round(premium_ann, 2),
        "total_annualized_pct": round(premium_ann, 2),
        "sofr_excess_pct": round(premium_ann, 2),
        "beats_sofr": True,
        "static_return_pct": round(premium / capital * 100, 2),
        "breakeven": round(short_q.strike - net_credit, 2) if right == "P" else round(short_q.strike + net_credit, 2),
        "cushion_pct": round(cushion_pct, 2),
        "max_profit": round(premium, 2),
        "max_loss": round(capital, 2),
        "expected_pnl": None,
        "greeks": {"delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"]},
        "theta_per_day": round(-g["theta"] * CONTRACT_MULTIPLIER, 2),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio,
        "premium_richness": richness,
        "liquidity": {"oi": short_q.oi, "volume": short_q.volume, "spread_pct": _spread_pct(short_q)},
        "exercise_style": "European (cash-settled)" if european else "American",
        "flags": flags,
        "legs": [_leg(short_q, "SELL", exp, spot, dte, atm_iv, rnd),
                 _leg(long_q, "BUY", exp, spot, dte, atm_iv, rnd)],
    }


def _best_credit_spread(structure: str, label: str, short_q: OptionQuote,
                        long_qs: list[OptionQuote], spot: float, **kw) -> Optional[dict]:
    """Pick the most capital-efficient (highest annualized ROC) defined-risk spread
    among the candidate long wings that still clears the premium floor."""
    best = None
    for lq in long_qs:
        o = _credit_spread(structure, label, short_q, lq, spot=spot, **kw)
        if o is None or o["width"] > spot * 0.20:    # reject absurdly wide spreads
            continue
        if best is None or o["premium_annualized_pct"] > best["premium_annualized_pct"]:
            best = o
    return best


def _collar(short_call: OptionQuote, long_put: OptionQuote, spot: float, dte: int,
            exp: str, rnd, r: float, atm_iv: Optional[float], iv_hv_ratio: Optional[float],
            richness: str, european: bool, earnings_before: Optional[str],
            macro: list[str], min_prob: float, min_income: float, ticker: str) -> Optional[dict]:
    """Income collar: covered call financed protective put (defines the downside)."""
    if not _executable(short_call)[0] or not _executable(long_put)[0]:   # both legs must fill
        return None
    net_credit = (short_call.mid - long_put.mid)
    premium = net_credit * CONTRACT_MULTIPLIER
    if premium < min_income:
        return None
    iv = short_call.iv if short_call.iv else atm_iv
    p_keep, method = _prob_keep(rnd, short_call.strike, "C", spot, dte, r, iv)
    if p_keep is None or p_keep < min_prob:
        return None

    collateral = spot * CONTRACT_MULTIPLIER
    premium_ann = annualized_return_pct(premium, collateral, dte)
    floor_pct = (long_put.strike - spot) / spot * 100
    cap_pct = (short_call.strike - spot) / spot * 100
    p_in_band = None
    if rnd is not None:
        p_in_band = round((rnd.prob_below(short_call.strike) - rnd.prob_below(long_put.strike)) * 100, 1)
    max_loss = round((spot - long_put.strike) * CONTRACT_MULTIPLIER - premium, 2)
    g = _bs_greeks(spot, short_call.strike, dte, iv or 0.0, "C")

    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    flags.append({"level": "good",
                  "text": f"Downside floor at ${round(long_put.strike, 2)} ({floor_pct:.1f}%) — gap-down protected"})
    return {
        "structure": "collar",
        "label": "Collar (covered call + protective put)",
        "expiration": exp,
        "dte": dte,
        "short_strike": round(short_call.strike, 2),
        "short_strike_pct": round((short_call.strike - spot) / spot * 100, 1),
        "floor_strike": round(long_put.strike, 2),
        "short_delta": g["delta"],
        "prob_keep_pct": round(p_keep * 100, 1),
        "prob_assign_pct": round((1 - p_keep) * 100, 1),
        "prob_in_band_pct": p_in_band,
        "prob_method": method,
        "premium": round(premium, 2),
        "premium_per_share": round(net_credit, 2),
        "collateral": round(collateral, 2),
        "premium_annualized_pct": round(premium_ann, 2),
        "total_annualized_pct": round(premium_ann, 2),
        "sofr_excess_pct": round(premium_ann, 2),
        "beats_sofr": premium_ann > 0,
        "static_return_pct": round(premium / collateral * 100, 2) if collateral else 0.0,
        "breakeven": round(spot - net_credit, 2),
        "cushion_pct": round(cap_pct, 2),
        "max_profit": round((short_call.strike - spot) * CONTRACT_MULTIPLIER + premium, 2),
        "max_loss": max_loss,
        "floor_pct": round(floor_pct, 2),
        "cap_pct": round(cap_pct, 2),
        "expected_pnl": None,
        "greeks": {"delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"]},
        "theta_per_day": round(-g["theta"] * CONTRACT_MULTIPLIER, 2),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio,
        "premium_richness": richness,
        "liquidity": {"oi": short_call.oi, "volume": short_call.volume, "spread_pct": _spread_pct(short_call)},
        "exercise_style": "European (cash-settled)" if european else "American",
        "flags": flags,
        "legs": [_leg(short_call, "SELL", exp, spot, dte, atm_iv, rnd),
                 _leg(long_put, "BUY", exp, spot, dte, atm_iv, rnd)],
    }


def _pick_wing(qmap: dict, short_strike: float, side: str) -> Optional[float]:
    """Nearest *executable* strike further OTM than the short. side: 'below' | 'above'."""
    cands = [k for k in qmap if (k < short_strike if side == "below" else k > short_strike)]
    cands.sort(key=lambda k: abs(k - short_strike))
    for k in cands[:15]:
        if _executable(qmap[k])[0]:
            return k
    return None


def _iron_condor(calls: dict, puts: dict, spot: float, dte: int, exp: str, rnd, r: float,
                 atm_iv: Optional[float], iv_hv_ratio: Optional[float], richness: str,
                 min_prob: float, min_income: float, european: bool, ticker: str) -> Optional[dict]:
    """Neutral, defined-risk both sides: short put spread + short call spread. Income if
    the underlying stays between the short strikes. P(keep) = P(in band)."""
    if rnd is None:
        return None
    put_strikes = sorted((k for k in puts if k < spot), reverse=True)
    call_strikes = sorted(k for k in calls if k > spot)
    if not put_strikes or not call_strikes:
        return None
    tail = (1 - min_prob) / 2.0                       # split the breach budget both sides
    kp_s = _nearest_strike(put_strikes, rnd.strike_for_prob_below(tail), "below")
    kc_s = _nearest_strike(call_strikes, rnd.strike_for_prob_below(1 - tail), "above")
    if not kp_s or not kc_s or kp_s not in puts or kc_s not in calls:
        return None
    kp_l = _pick_wing(puts, kp_s, "below")
    kc_l = _pick_wing(calls, kc_s, "above")
    if not kp_l or not kc_l:
        return None
    if not all(_executable(q)[0] for q in (puts[kp_s], puts[kp_l], calls[kc_s], calls[kc_l])):
        return None
    net_credit = (puts[kp_s].mid - puts[kp_l].mid) + (calls[kc_s].mid - calls[kc_l].mid)
    premium = net_credit * CONTRACT_MULTIPLIER
    if net_credit <= 0 or premium < min_income:
        return None
    p_keep = rnd.prob_below(kc_s) - rnd.prob_below(kp_s)   # both shorts OTM (finish in band)
    if p_keep < min_prob:
        return None
    capital = max(kp_s - kp_l, kc_l - kc_s) * CONTRACT_MULTIPLIER - premium   # only one side breaches
    if capital <= 0:
        return None
    premium_ann = annualized_return_pct(premium, capital, dte)
    g = _bs_greeks(spot, kc_s, dte, (calls[kc_s].iv or atm_iv or 0.0), "C")
    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    flags.append({"level": "good", "text": f"Defined risk both sides — max loss capped at ${round(capital, 2)}"})
    flags.append({"level": "info", "text": f"Profit if it stays in ${round(kp_s, 2)}–${round(kc_s, 2)}"})
    return {
        "structure": "iron_condor", "label": "Iron Condor",
        "expiration": exp, "dte": dte,
        "short_strike": round(kp_s, 2),
        "short_strike_pct": round((kp_s - spot) / spot * 100, 1),
        "put_short": round(kp_s, 2), "put_long": round(kp_l, 2),
        "call_short": round(kc_s, 2), "call_long": round(kc_l, 2),
        "band_low": round(kp_s, 2), "band_high": round(kc_s, 2),
        "short_delta": g["delta"],
        "prob_keep_pct": round(p_keep * 100, 1), "prob_assign_pct": round((1 - p_keep) * 100, 1),
        "prob_in_band_pct": round(p_keep * 100, 1), "prob_method": "RND",
        "premium": round(premium, 2), "premium_per_share": round(net_credit, 2),
        "collateral": round(capital, 2),
        "premium_annualized_pct": round(premium_ann, 2), "total_annualized_pct": round(premium_ann, 2),
        "sofr_excess_pct": round(premium_ann, 2), "beats_sofr": True,
        "static_return_pct": round(premium / capital * 100, 2),
        "breakeven": round(kp_s - net_credit, 2),
        "cushion_pct": round((spot - kp_s) / spot * 100, 2),
        "max_profit": round(premium, 2), "max_loss": round(capital, 2),
        "expected_pnl": None,
        "greeks": {"delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"]},
        "theta_per_day": round(-g["theta"] * CONTRACT_MULTIPLIER, 2),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio, "premium_richness": richness,
        "liquidity": {"oi": calls[kc_s].oi, "volume": calls[kc_s].volume, "spread_pct": _spread_pct(calls[kc_s])},
        "exercise_style": "European (cash-settled)" if european else "American",
        "flags": flags,
        "legs": [_leg(puts[kp_s], "SELL", exp, spot, dte, atm_iv, rnd), _leg(puts[kp_l], "BUY", exp, spot, dte, atm_iv, rnd),
                 _leg(calls[kc_s], "SELL", exp, spot, dte, atm_iv, rnd), _leg(calls[kc_l], "BUY", exp, spot, dte, atm_iv, rnd)],
    }


def _jade_lizard(calls: dict, puts: dict, spot: float, dte: int, exp: str, rnd, r: float,
                 atm_iv: Optional[float], iv_hv_ratio: Optional[float], richness: str,
                 min_prob: float, min_income: float, european: bool, ticker: str) -> Optional[dict]:
    """Short put + short call spread, sized so net credit ≥ call-spread width ⇒ NO upside
    risk. Downside is CSP-style (the short put). P(keep) = P(put not assigned)."""
    if rnd is None:
        return None
    put_strikes = sorted((k for k in puts if k < spot), reverse=True)
    call_strikes = sorted(k for k in calls if k > spot)
    if not put_strikes or not call_strikes:
        return None
    kp = _headline_put(rnd, put_strikes, spot, min_prob)      # CSP-style short put
    if not kp or kp not in puts or not _executable(puts[kp])[0]:
        return None
    p_keep, method = _prob_keep(rnd, kp, "P", spot, dte, r, puts[kp].iv or atm_iv)
    if p_keep is None or p_keep < min_prob:
        return None
    # Find the call spread that MAXIMIZES credit while keeping net credit ≥ call width.
    best = None
    for kc_s in call_strikes:
        if not _executable(calls[kc_s])[0]:
            continue
        kc_l = _pick_wing(calls, kc_s, "above")
        if not kc_l:
            continue
        net_credit = puts[kp].mid + calls[kc_s].mid - calls[kc_l].mid
        premium = net_credit * CONTRACT_MULTIPLIER
        if net_credit < (kc_l - kc_s) or premium < min_income:   # the no-upside-risk condition
            continue
        if best is None or premium > best[0]:
            best = (premium, kc_s, kc_l, net_credit)
    if best is None:
        return None
    premium, kc_s, kc_l, net_credit = best
    collateral = kp * CONTRACT_MULTIPLIER
    premium_ann = annualized_return_pct(premium, collateral, dte)
    g = _bs_greeks(spot, kp, dte, (puts[kp].iv or atm_iv or 0.0), "P")
    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    flags.append({"level": "good", "text": f"No upside risk — credit ${round(net_credit, 2)} ≥ call-spread width ${round(kc_l - kc_s, 2)}"})
    flags.append({"level": "info", "text": f"Downside like a CSP: assigned below ${round(kp, 2)}"})
    return {
        "structure": "jade_lizard", "label": "Jade Lizard",
        "expiration": exp, "dte": dte,
        "short_strike": round(kp, 2),
        "short_strike_pct": round((kp - spot) / spot * 100, 1),
        "put_short": round(kp, 2), "call_short": round(kc_s, 2), "call_long": round(kc_l, 2),
        "short_delta": g["delta"],
        "prob_keep_pct": round(p_keep * 100, 1), "prob_assign_pct": round((1 - p_keep) * 100, 1),
        "prob_method": method,
        "premium": round(premium, 2), "premium_per_share": round(net_credit, 2),
        "collateral": round(collateral, 2),
        "premium_annualized_pct": round(premium_ann, 2), "total_annualized_pct": round(premium_ann, 2),
        "sofr_excess_pct": round(premium_ann, 2), "beats_sofr": premium_ann > 0,
        "static_return_pct": round(premium / collateral * 100, 2),
        "breakeven": round(kp - net_credit, 2),
        "cushion_pct": round((spot - kp) / spot * 100, 2),
        "max_profit": round(premium, 2), "max_loss": None,       # CSP-style open downside
        "expected_pnl": None,
        "greeks": {"delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"]},
        "theta_per_day": round(-g["theta"] * CONTRACT_MULTIPLIER, 2),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio, "premium_richness": richness,
        "liquidity": {"oi": puts[kp].oi, "volume": puts[kp].volume, "spread_pct": _spread_pct(puts[kp])},
        "exercise_style": "European (cash-settled)" if european else "American",
        "flags": flags,
        "legs": [_leg(puts[kp], "SELL", exp, spot, dte, atm_iv, rnd),
                 _leg(calls[kc_s], "SELL", exp, spot, dte, atm_iv, rnd),
                 _leg(calls[kc_l], "BUY", exp, spot, dte, atm_iv, rnd)],
    }


# ---------------------------------------------------------------------------
# Per-expiry scan
# ---------------------------------------------------------------------------

def _atm_iv(rnd, calls: dict, puts: dict, spot: float) -> Optional[float]:
    if rnd is not None:
        try:
            return float(rnd.smile.iv(spot))
        except Exception:  # noqa: BLE001
            pass
    k = _nearest_strike(sorted(set(calls) | set(puts)), spot)
    if k is not None:
        for src in (calls, puts):
            q = src.get(k)
            if q and q.iv:
                return float(q.iv)
    return None


def _scan_expiry(chain: OptionChain, spot: float, dte: int, exp: str, today: date,
                 r: float, sofr_pct: float, hv: Optional[float], structures: list[str],
                 european: bool, next_earnings: Optional[str], min_prob: float,
                 min_income: float, ticker: str) -> tuple[list[dict], dict]:
    """All qualifying opportunities for one expiration + an expiry summary."""
    calls, puts = _split_chain(chain)
    strikes_all = sorted(set(calls) | set(puts))
    rnd = _build_rnd(calls, puts, strikes_all, spot, dte)
    atm_iv = _atm_iv(rnd, calls, puts, spot)
    iv_hv_ratio, richness = _richness(atm_iv, hv)
    quant = _quant_block(rnd, calls, puts, strikes_all, spot, dte, atm_iv)

    exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
    earnings_before = (next_earnings if next_earnings and today.isoformat() < next_earnings <= exp_date.isoformat()
                       else None)
    macro = _macro_events_in_window(today, exp_date)

    common = dict(spot=spot, dte=dte, exp=exp, rnd=rnd, r=r, atm_iv=atm_iv,
                  iv_hv_ratio=iv_hv_ratio, richness=richness, european=european,
                  earnings_before=earnings_before, macro=macro, min_prob=min_prob,
                  min_income=min_income, ticker=ticker)
    opps: list[dict] = []

    call_strikes = sorted(k for k in calls if k > spot * 1.001)
    put_strikes = sorted((k for k in puts if k < spot * 0.999), reverse=True)

    # ---- Covered calls (OTM calls) ----
    if "covered_call" in structures:
        cc = [o for k in call_strikes
              if (o := _single_leg_income("covered_call", "Covered Call", calls[k],
                                          sofr_pct=sofr_pct, **common))]
        opps += sorted(cc, key=lambda o: o["premium_annualized_pct"], reverse=True)[:PER_STRUCTURE_CAP]

    # ---- Cash-secured puts (OTM puts) ----
    if "cash_secured_put" in structures:
        cp = [o for k in put_strikes
              if (o := _single_leg_income("cash_secured_put", "Cash-Secured Put", puts[k],
                                          sofr_pct=sofr_pct, **common))]
        opps += sorted(cp, key=lambda o: o["premium_annualized_pct"], reverse=True)[:PER_STRUCTURE_CAP]

    # ---- Collar — headline safe call financed by a ~min_prob-breach floor put ----
    if "collar" in structures and call_strikes and put_strikes:
        kc = _headline_call(rnd, call_strikes, spot, min_prob)
        kp = _headline_floor(rnd, [k for k in puts if k < spot], spot, min_prob)
        if kc in calls and kp in puts:
            o = _collar(calls[kc], puts[kp], iv_hv_ratio=iv_hv_ratio, richness=richness,
                        european=european, earnings_before=earnings_before, macro=macro,
                        spot=spot, dte=dte, exp=exp, rnd=rnd, r=r, atm_iv=atm_iv,
                        min_prob=min_prob, min_income=min_income, ticker=ticker)
            if o:
                opps.append(o)

    # ---- Defined-risk credit spreads off the headline short strikes ----
    # Scan candidate long wings and keep the most capital-efficient (best ROC) one
    # that still clears the premium floor — the narrowest spread is usually best.
    if "credit_spread" in structures:
        kp_short = _headline_put(rnd, put_strikes, spot, min_prob)
        if kp_short in puts:
            longs = sorted((puts[k] for k in puts if k < kp_short),
                           key=lambda q: kp_short - q.strike)[:25]
            o = _best_credit_spread("put_credit_spread", "Put Credit Spread", puts[kp_short],
                                    longs, spot=spot, dte=dte, exp=exp, rnd=rnd, r=r, atm_iv=atm_iv,
                                    iv_hv_ratio=iv_hv_ratio, richness=richness, european=european,
                                    earnings_before=earnings_before, macro=macro, min_prob=min_prob,
                                    min_income=min_income, ticker=ticker)
            if o:
                opps.append(o)
        kc_short = _headline_call(rnd, call_strikes, spot, min_prob)
        if kc_short in calls:
            longs = sorted((calls[k] for k in calls if k > kc_short),
                           key=lambda q: q.strike - kc_short)[:25]
            o = _best_credit_spread("call_credit_spread", "Call Credit Spread", calls[kc_short],
                                    longs, spot=spot, dte=dte, exp=exp, rnd=rnd, r=r, atm_iv=atm_iv,
                                    iv_hv_ratio=iv_hv_ratio, richness=richness, european=european,
                                    earnings_before=earnings_before, macro=macro, min_prob=min_prob,
                                    min_income=min_income, ticker=ticker)
            if o:
                opps.append(o)

    # ---- Iron Condor (neutral, defined risk both sides) ----
    if "iron_condor" in structures:
        o = _iron_condor(calls, puts, spot, dte, exp, rnd, r, atm_iv, iv_hv_ratio,
                         richness, min_prob, min_income, european, ticker)
        if o:
            opps.append(o)

    # ---- Jade Lizard (short put + call spread, no upside risk) ----
    if "jade_lizard" in structures:
        o = _jade_lizard(calls, puts, spot, dte, exp, rnd, r, atm_iv, iv_hv_ratio,
                         richness, min_prob, min_income, european, ticker)
        if o:
            opps.append(o)

    # Per-opportunity confidence (fill + trust), from the expiry's quant diagnostics.
    for o in opps:
        o["confidence"] = _confidence(o, quant)

    # Gamma-pin magnet: the strike carrying the most open interest at this expiry.
    oi_by_strike: dict[float, int] = {}
    for q in chain.quotes:
        oi_by_strike[q.strike] = oi_by_strike.get(q.strike, 0) + (q.oi or 0)
    max_oi_strike = max(oi_by_strike, key=oi_by_strike.get) if any(oi_by_strike.values()) else None

    summary = {
        "expiration": exp, "dte": dte, "monthly": _is_monthly_expiry(exp_date),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "hv30_pct": round(hv * 100, 1) if hv else None,
        "iv_hv_ratio": iv_hv_ratio, "premium_richness": richness,
        "rnd_available": rnd is not None,
        "max_oi_strike": max_oi_strike,
        "earnings_before_expiry": earnings_before,
        "macro_events": macro,
        "quant": quant,
        "n_opportunities": len(opps),
    }
    return opps, summary


def _headline_call(rnd, call_strikes: list[float], spot: float, min_prob: float) -> Optional[float]:
    """The 85%-safe call strike: read off the RND inverse, snapped up to a listed strike."""
    if not call_strikes:
        return None
    target = rnd.strike_for_prob_below(min_prob) if rnd is not None else spot * (1 + 0.08)
    return _nearest_strike(call_strikes, max(target, spot), "above")


def _headline_put(rnd, put_strikes: list[float], spot: float, min_prob: float) -> Optional[float]:
    if not put_strikes:
        return None
    target = rnd.strike_for_prob_below(1 - min_prob) if rnd is not None else spot * (1 - 0.08)
    return _nearest_strike(put_strikes, min(target, spot), "below")


def _headline_floor(rnd, put_strikes: list[float], spot: float, min_prob: float) -> Optional[float]:
    """Protective-put floor for a collar — a low-breach-probability strike."""
    if not put_strikes:
        return None
    target = rnd.strike_for_prob_below(min(0.15, 1 - min_prob)) if rnd is not None else spot * 0.92
    return _nearest_strike(put_strikes, min(target, spot * 0.97), "below")


# ---------------------------------------------------------------------------
# Public — single ticker
# ---------------------------------------------------------------------------

async def run_derivative_income(
    ticker: str,
    target_dte: Optional[int] = None,
    min_prob: float = DEFAULT_MIN_PROB,
    min_income: float = DEFAULT_MIN_INCOME,
    structures: Optional[list[str]] = None,
    quote_source: str = "yfinance",
    user: Optional["User"] = None,
    db: Optional["AsyncSession"] = None,
) -> dict:
    """Deep-scan one underlying for income opportunities (≥``min_prob`` no-assignment,
    ≥``min_income`` premium), ranked by annualized yield vs SOFR."""
    ticker = _norm_ticker(ticker)
    structures = structures or ["covered_call", "cash_secured_put", "collar",
                                "credit_spread", "iron_condor", "jade_lizard"]
    min_prob = min(max(min_prob, 0.5), 0.99)
    today = date.today()

    cache_key = (f"derivinc:{ticker}:{target_dte if target_dte else 'monthly'}:"
                 f"{min_prob:.2f}:{int(min_income)}:{','.join(sorted(structures))}:{quote_source}:v1")
    if db is not None:
        cached = await get_cached(db, cache_key)
        if cached is not None:
            return cached

    provider = get_provider(quote_source, user=user, db=db)
    try:
        underlying = await provider.get_underlying_price(ticker)
        spot = float(underlying.price)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Could not fetch price for {ticker}: {exc}"}
    if not spot or spot <= 0:
        return {"error": f"No valid price for {ticker}"}

    try:
        all_exps = await provider.get_option_expirations(ticker)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"No options chain for {ticker}: {exc}"}
    chosen = _select_expirations(all_exps, target_dte, today)
    if not chosen:
        return {"error": f"No expirations in range for {ticker}"}

    sofr_frac, sofr_label = await _get_sofr()
    sofr_pct = sofr_frac * 100.0
    ctx = await asyncio.to_thread(_context_sync, ticker)
    hv = ctx.get("hv30") or ctx.get("hv20")
    european = _is_european(ticker)

    opportunities: list[dict] = []
    expiry_summaries: list[dict] = []
    for exp, dte in chosen:
        try:
            chain = await provider.get_option_chain(ticker, exp)
        except Exception as exc:  # noqa: BLE001
            logger.debug("chain fetch failed %s %s: %s", ticker, exp, exc)
            continue
        opps, summary = _scan_expiry(
            chain, spot, dte, exp, today, sofr_frac, sofr_pct, hv, structures,
            european, ctx.get("next_earnings"), min_prob, min_income, ticker,
        )
        opportunities.extend(opps)
        expiry_summaries.append(summary)

    opportunities.sort(key=lambda o: o.get("premium_annualized_pct", 0), reverse=True)
    best_by_structure: dict[str, dict] = {}
    for o in opportunities:
        best_by_structure.setdefault(o["structure"], o)

    # ── Common context + events — surfaced ONCE, not repeated on every row ──
    w52h, w52l = ctx.get("week52_high"), ctx.get("week52_low")
    week52 = None
    if w52h and w52l and w52h > w52l:
        week52 = {"high": w52h, "low": w52l,
                  "position_pct": round((spot - w52l) / (w52h - w52l) * 100, 1)}

    # scope: "ticker" = name-specific (earnings, European style); "common" = market-wide
    # macro (FOMC/CPI/NFP/OPEX) — the UI shows common ones once, ticker ones per name.
    events: list[dict] = []
    if european:            # only worth calling out European (no early assignment); American is the default
        events.append({"level": "good", "scope": "ticker",
                       "text": "European, cash-settled — no early assignment risk"})
    if ctx.get("next_earnings"):
        before = any(s.get("earnings_before_expiry") for s in expiry_summaries)
        events.append({
            "level": "warn" if before else "info", "scope": "ticker",
            "text": (f"Earnings {ctx['next_earnings']}"
                     + (" — before a scanned expiry (binary vol event, gap/assignment risk)"
                        if before else " — after the scanned window")),
        })
    # Macro events always span the next 90 days (independent of the ≤45-DTE scan).
    for m in _macro_events_in_window(today, today + timedelta(days=EVENTS_HORIZON_DAYS)):
        events.append({"level": "info", "scope": "common", "text": m})

    context = {
        "spot": round(spot, 2),
        "shares_per_contract": CONTRACT_MULTIPLIER,
        "notional_per_contract": round(spot * CONTRACT_MULTIPLIER, 2),
        "week52": week52,
        "exercise_style": "European (cash-settled)" if european else "American",
        "european": european,
        "sofr_pct": round(sofr_pct, 2),
        "sofr_source": sofr_label,
        "hv30_pct": round(ctx["hv30"] * 100, 1) if ctx.get("hv30") else None,
        "hv20_pct": round(ctx["hv20"] * 100, 1) if ctx.get("hv20") else None,
        "next_earnings": ctx.get("next_earnings"),
        "vol_stats": {**_vol_stats(ctx, expiry_summaries[0] if expiry_summaries else {}),
                      "term_structure": _term_structure(expiry_summaries)},
    }

    result = {
        "ticker": ticker,
        "spot": round(spot, 2),
        "context": context,
        "events": events,
        "as_of": today.isoformat(),
        "quote_source": quote_source,
        "exercise_style": "European (cash-settled)" if european else "American",
        "european": european,
        "min_prob_pct": round(min_prob * 100, 1),
        "min_income": min_income,
        "sofr_pct": round(sofr_pct, 2),
        "sofr_source": sofr_label,
        "hv30_pct": round(ctx["hv30"] * 100, 1) if ctx.get("hv30") else None,
        "hv20_pct": round(ctx["hv20"] * 100, 1) if ctx.get("hv20") else None,
        "next_earnings": ctx.get("next_earnings"),
        "expiry_mode": "target" if target_dte else "monthly_le_45d",
        "target_dte": target_dte,
        "expiry_summaries": expiry_summaries,
        "opportunities": opportunities,
        "best_by_structure": list(best_by_structure.values()),
        "n_opportunities": len(opportunities),
    }
    if not opportunities:
        result["note"] = (f"No structure cleared {round(min_prob * 100)}% no-assignment "
                          f"AND ${min_income:.0f} premium in the scanned expiries. "
                          f"Try a longer target DTE, a lower probability, or a more volatile underlying.")
    if db is not None:
        await set_cached(db, cache_key, result, ttl_seconds=TTL_ANALYSIS)
    return result


# ---------------------------------------------------------------------------
# Public — portfolio sweep (top-N holdings, paginated)
# ---------------------------------------------------------------------------

async def _load_user_holdings(db: "AsyncSession", user: "User") -> list[dict]:
    from sqlalchemy import select
    from ..models import Portfolio, PortfolioHolding
    rows = (await db.execute(
        select(PortfolioHolding.ticker, PortfolioHolding.shares, PortfolioHolding.cost_basis)
        .join(Portfolio, PortfolioHolding.portfolio_id == Portfolio.id)
        .where(Portfolio.user_id == user.id)
    )).all()
    # Aggregate duplicate tickers across portfolios (share-weighted average cost).
    agg: dict[str, dict] = {}
    for ticker, shares, cost_basis in rows:
        sh = float(shares or 0)
        a = agg.setdefault(ticker.upper(), {"shares": 0.0, "cost": 0.0})
        a["shares"] += sh
        a["cost"] += sh * float(cost_basis or 0)
    return [{"ticker": t, "shares": a["shares"],
             "cost_basis": round(a["cost"] / a["shares"], 2) if a["shares"] else None}
            for t, a in agg.items() if a["shares"] > 0]


async def run_portfolio_derivative_income(
    offset: int = 0,
    limit: int = 10,
    target_dte: Optional[int] = None,
    min_prob: float = DEFAULT_MIN_PROB,
    min_income: float = DEFAULT_MIN_INCOME,
    structures: Optional[list[str]] = None,
    quote_source: str = "yfinance",
    user: Optional["User"] = None,
    db: Optional["AsyncSession"] = None,
) -> dict:
    """Covered-call (and collar) income across the user's top holdings by market
    value. Analyzes only ``[offset : offset+limit]`` to protect the quote provider;
    the UI bumps ``offset`` to page through ("Next 10")."""
    if user is None or db is None:
        return {"error": "Authentication required"}
    limit = max(1, min(limit, 10))
    structures = structures or ["covered_call", "collar", "cash_secured_put"]

    holdings = await _load_user_holdings(db, user)
    if not holdings:
        return {"error": "No holdings found. Add holdings to your portfolio first.",
                "results": [], "total_holdings": 0}

    from .portfolio_service import fetch_current_prices
    prices = await fetch_current_prices([h["ticker"] for h in holdings])
    for h in holdings:
        px = prices.get(h["ticker"]) or 0.0
        h["price"] = px
        h["market_value"] = px * h["shares"]
    holdings.sort(key=lambda h: h["market_value"], reverse=True)

    total = len(holdings)
    page = holdings[offset: offset + limit]

    cache_key = (f"derivinc:pf:{user.id}:{offset}:{limit}:{target_dte or 'monthly'}:"
                 f"{min_prob:.2f}:{int(min_income)}:{quote_source}:v1")
    cached = await get_cached(db, cache_key)
    if cached is not None:
        return cached

    results: list[dict] = []
    for h in page:
        entry = {"ticker": h["ticker"], "shares": h["shares"], "cost_basis": h.get("cost_basis"),
                 "price": round(h["price"], 2), "market_value": round(h["market_value"], 2)}
        if h["shares"] < 100:
            entry["note"] = "Need ≥100 shares to write a covered call on this holding."
            entry["best_opportunity"] = None
            results.append(entry)
            continue
        try:
            scan = await run_derivative_income(
                h["ticker"], target_dte=target_dte, min_prob=min_prob, min_income=min_income,
                structures=structures, quote_source=quote_source, user=user, db=db,
            )
        except Exception as exc:  # noqa: BLE001 — one bad name must not kill the sweep
            entry["note"] = f"Scan failed: {type(exc).__name__}"
            entry["best_opportunity"] = None
            results.append(entry)
            continue
        if scan.get("error"):
            entry["note"] = scan["error"]
            entry["best_opportunity"] = None
            results.append(entry)
            continue
        # Prefer the best covered call (income on shares owned), else any best.
        best = next((o for o in scan["opportunities"] if o["structure"] == "covered_call"),
                    scan["opportunities"][0] if scan["opportunities"] else None)
        if best:
            contracts = int(h["shares"] // 100)
            best = {**best, "contracts": contracts,
                    "total_premium": round(best["premium"] * contracts, 2)}
        entry.update({
            "best_opportunity": best,
            "spot": scan.get("spot"),
            "exercise_style": scan.get("exercise_style"),
            "next_earnings": scan.get("next_earnings"),
            "iv_hv_ratio": scan["expiry_summaries"][0]["iv_hv_ratio"] if scan.get("expiry_summaries") else None,
            "note": scan.get("note"),
        })
        results.append(entry)

    # Market-wide macro events over the next 90 days — surfaced ONCE at the top
    # (identical across holdings), independent of the ≤45-DTE option scan.
    common_events = [{"level": "info", "scope": "common", "text": m}
                     for m in _macro_events_in_window(date.today(), date.today() + timedelta(days=EVENTS_HORIZON_DAYS))]

    payload = {
        "offset": offset,
        "limit": limit,
        "total_holdings": total,
        "next_offset": offset + limit if offset + limit < total else None,
        "has_more": offset + limit < total,
        "min_prob_pct": round(min_prob * 100, 1),
        "min_income": min_income,
        "expiry_mode": "target" if target_dte else "monthly_le_45d",
        "common_events": common_events,
        "results": results,
    }
    await set_cached(db, cache_key, payload, ttl_seconds=TTL_PORTFOLIO)
    return payload
