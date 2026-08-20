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
from .stock_service import bs_prob_otm, bs_price
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
PER_STRUCTURE_CAP = 15           # CC/CSP strikes kept per expiry — effectively the WHOLE ladder above
                                 # min_prob (cheap: the chain is fetched once; a display cap, not a scan cost),
                                 # SAFEST-first (highest keep-prob) so the safe rungs are never truncated
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
                        today: date, target_expiration: Optional[str] = None) -> list[tuple[str, int]]:
    """Pick the expirations to actually fetch — the API-budget gate.

    Exact (``target_expiration`` set): scan ONLY that expiry — the user picked a
    specific date, so honour it and fetch nothing else.
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

    if target_expiration:
        exact = [(s, dte) for (s, dte, d) in parsed if s == target_expiration]
        if exact:
            return exact
        # Fall through to DTE logic if the date is stale/unlisted (don't hard-fail).

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

# Funds and indices have no earnings and no fundamentals — Yahoo's quoteSummary
# 404s for them ("No fundamentals data found for symbol: QQQ").
_NO_EARNINGS_QUOTE_TYPES = {"ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY", "FUTURE"}


def _reports_earnings(stock) -> bool:
    """Does this underlying report earnings at all?

    `quoteType` comes from `fast_info` (the chart endpoint), so the check itself costs
    nothing extra and avoids the quoteSummary calls that 404 for funds — which is what
    made yfinance log "No earnings dates found, symbol may be delisted" and a pair of
    404s on every ETF request. Fails OPEN (assume earnings) so an unknown type behaves
    exactly as before."""
    try:
        return str((stock.fast_info or {}).get("quoteType") or "").upper() not in _NO_EARNINGS_QUOTE_TYPES
    except Exception:  # noqa: BLE001
        return True


def _har_rv_forecast(r: np.ndarray) -> Optional[float]:
    """HAR-RV (Corsi 2009) forward realized-vol forecast, as an annualized vol %.

    HV30 is BACKWARD-looking (how much the stock already moved). HAR-RV is FORWARD:
    it regresses the next-month average realized variance on three horizons of past
    variance — daily, weekly (5d) and monthly (22d) — capturing volatility's long
    memory and mean-reversion. Paired with implied vol it's a sharper Volatility-Risk-
    Premium read than trailing HV alone (are you selling vol RICH to what's coming?).

    Targets the mean realized variance over the NEXT 22 trading days so the level is
    directly comparable to HV30 and to a ~monthly option's implied. Returns None on
    thin history or a degenerate fit (caller treats None as 'not available')."""
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    n = r.size
    if n < 90:
        return None
    rv = r ** 2                                    # daily variance proxy

    def _roll_mean(a: np.ndarray, w: int) -> np.ndarray:
        c = np.cumsum(np.insert(a, 0, 0.0))
        out = np.full(a.size, np.nan)
        out[w - 1:] = (c[w:] - c[:-w]) / w
        return out

    H = 22
    comp_d, comp_w, comp_m = rv, _roll_mean(rv, 5), _roll_mean(rv, H)
    y = np.full(n, np.nan)                          # forward mean variance over next H days
    for t in range(n - H):
        y[t] = rv[t + 1: t + 1 + H].mean()

    mask = np.isfinite(comp_d) & np.isfinite(comp_w) & np.isfinite(comp_m) & np.isfinite(y)
    if mask.sum() < 40:
        return None
    X = np.column_stack([np.ones(mask.sum()), comp_d[mask], comp_w[mask], comp_m[mask]])
    try:
        beta, *_ = np.linalg.lstsq(X, y[mask], rcond=None)
    except np.linalg.LinAlgError:
        return None
    var_fc = float(np.array([1.0, comp_d[-1], comp_w[-1], comp_m[-1]]) @ beta)
    if not np.isfinite(var_fc) or var_fc <= 0:      # fall back to the monthly component
        var_fc = float(comp_m[-1]) if np.isfinite(comp_m[-1]) and comp_m[-1] > 0 else 0.0
    if var_fc <= 0:
        return None
    vol_pct = (var_fc * 252) ** 0.5 * 100
    return round(vol_pct, 1) if 3.0 <= vol_pct <= 300.0 else None


def _context_sync(ticker: str) -> dict:
    """Realized vol (20/30d, annualized), 52-week high/low and the next earnings
    date, from ONE 1-year history pull. Best-effort: returns ``None`` defaults if
    yfinance is unavailable for the name."""
    out: dict = {"hv10": None, "hv20": None, "hv30": None, "next_earnings": None,
                 "week52_high": None, "week52_low": None, "hv_series": None, "har_rv30": None}
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist is not None and not hist.empty:
            closes = hist["Close"].dropna()
            if len(closes) > 5:
                out["week52_high"] = round(float(closes.max()), 2)
                out["week52_low"] = round(float(closes.min()), 2)
            # Realized (historical) vol at 10/20/30 TRADING-day windows: sample std (ddof=1) of daily
            # log returns, annualized by √252, on split/div-adjusted closes. HV30 is the desk baseline
            # every IV/HV comparison keys off; HV10/HV20 show the short-window term structure of realized.
            log_ret = np.log(closes / closes.shift(1)).dropna()
            if len(log_ret) >= 10:
                out["hv10"] = round(float(log_ret.tail(10).std() * math.sqrt(252)), 4)
            if len(log_ret) >= 20:
                out["hv20"] = round(float(log_ret.tail(20).std() * math.sqrt(252)), 4)
            if len(log_ret) >= 30:
                out["hv30"] = round(float(log_ret.tail(30).std() * math.sqrt(252)), 4)
            # Rolling 30-day annualized realized-vol series → drives vol rank/percentile.
            roll = (log_ret.rolling(30).std() * math.sqrt(252)).dropna()
            if len(roll) >= 20:
                out["hv_series"] = [round(float(x), 4) for x in roll.tolist()]
            # HAR-RV forward realized-vol forecast (next ~1 month), a forward cross-check on HV30.
            out["har_rv30"] = _har_rv_forecast(log_ret.values)
        # Next earnings — try the modern earnings_dates frame, then the calendar.
        # Skipped entirely for funds/indices: they never report, and asking only
        # buys two 404s and a spurious "may be delisted" line in the logs.
        if _reports_earnings(stock):
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


def _prob_touch(strike: float, spot: float, dte_days: int, sigma: Optional[float],
                mu: float = 0.0) -> Optional[float]:
    """Probability the underlying TOUCHES ``strike`` at ANY time before expiry (first-passage / barrier hit)
    under GBM with real-world drift ``mu`` (annualized) and vol ``sigma`` (annualized). This is the honest
    "does the short ever go ITM" measure — ≈ 2× the expiry-ITM probability when drift ≈ 0, and it RISES when
    the stock drifts TOWARD the strike (mu folded straight into the path, not just the endpoint). Down barrier
    (K < spot, a short put) → P(min ≤ K); up barrier (K > spot, a short call) → P(max ≥ K)."""
    if not (strike and spot and dte_days and dte_days > 0 and sigma and sigma > 0) or strike == spot:
        return None
    T = dte_days / 365.0
    sT = sigma * math.sqrt(T)
    if sT <= 0:
        return None
    b = math.log(strike / spot)                         # log-distance to the barrier (<0 below spot, >0 above)
    nu = mu - 0.5 * sigma * sigma                        # Itô log-drift
    def _N(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    down = strike < spot
    t1 = _N((b - nu * T) / sT) if down else _N((-b + nu * T) / sT)   # = P(finish on the far side) = expiry-ITM
    try:                                                 # the reflection term (paths that touched then recovered)
        t2 = math.exp(2.0 * nu * b / (sigma * sigma)) * (_N((b + nu * T) / sT) if down else _N((-b - nu * T) / sT))
        if not math.isfinite(t2):
            t2 = 0.0                                     # deep barrier → reflection term is negligible anyway
    except OverflowError:
        t2 = 0.0
    return max(0.0, min(1.0, t1 + t2))


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
    har = ctx.get("har_rv30")
    iv_vs_har = None
    if iv_atm and har:
        iv_vs_har = round((iv_atm * 100) - har, 1)      # + = implied rich to the forward RV forecast
    return {
        "iv_atm_pct": round(iv_atm * 100, 1) if iv_atm else None,
        "hv_current_pct": round(hv_cur * 100, 1) if hv_cur else None,   # = HV30 (kept for back-compat)
        "hv10_pct": round(ctx["hv10"] * 100, 1) if ctx.get("hv10") else None,
        "hv20_pct": round(ctx["hv20"] * 100, 1) if ctx.get("hv20") else None,
        "hv30_pct": round(hv_cur * 100, 1) if hv_cur else None,
        "har_rv_pct": har,                              # HAR-RV forward (~1mo) realized-vol forecast
        "iv_vs_har_pts": iv_vs_har,                     # implied − HAR forecast (vol pts); + = seller edge
        "iv_rank": iv_rank, "iv_percentile": iv_pctile,
        "vol_rank": vol_rank, "vol_percentile": vol_pctile,
        "skew_pts": skew, "skew_direction": skew_dir,
        "skew_basis": "IV(90% strike) − IV(110% strike) in vol pts; positive = downside puts richer (sell puts)",
        "basis": "IV rank/percentile vs trailing 1y realized-vol range",
        "har_basis": "HAR-RV (Corsi): forward ~1-month realized-vol forecast from daily/weekly/monthly variance; HV30 is trailing",
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


def _reg_t_short_put_bpr(spot: float, strike: float, prem_ps: float) -> float:
    """Reg-T naked short-put Buying Power Reduction per SHARE (Uncovered Margin, NOT cash-secured):
      max(0.20·underlying − out-of-the-money + premium, 0.10·strike + premium).
    A put is OTM when spot > strike; deep-OTM puts fall to the 0.10·strike floor (the 0.20·underlying term
    goes negative). Capital blocked = this × 100. The FULL notional is still the dollar risk on assignment —
    this only changes the capital the YIELD is measured against, never Max Loss / VaR."""
    otm = max(spot - strike, 0.0)
    return max(0.20 * spot - otm + prem_ps, 0.10 * strike + prem_ps)


def _reg_t_short_call_bpr(spot: float, strike: float, prem_ps: float) -> float:
    """Reg-T NAKED short-call Buying Power Reduction per SHARE (uncovered — no stock behind it):
      max(0.20·underlying − out-of-the-money + premium, 0.10·underlying + premium).
    A call is OTM when strike > spot; the floor is 10% of the UNDERLYING (not the strike, unlike a put).
    Upside risk is UNBOUNDED — this only sets the capital blocked, never the (unlimited) max loss."""
    otm = max(strike - spot, 0.0)
    return max(0.20 * spot - otm + prem_ps, 0.10 * spot + prem_ps)


def _single_leg_income(structure: str, label: str, q: OptionQuote, spot: float,
                       dte: int, exp: str, rnd, r: float, sofr_pct: float,
                       atm_iv: Optional[float], iv_hv_ratio: Optional[float],
                       richness: str, european: bool, earnings_before: Optional[str],
                       macro: list[str], min_prob: float, min_income: float,
                       ticker: str, allow_illiquid: bool = False, covered: bool = True) -> Optional[dict]:
    """Short call (right C) or short put (right P) on a single strike. ``covered`` (calls only) = the user
    HOLDS the shares → the call is stock-secured (covered call); otherwise it is a NAKED call sized by
    Reg-T margin. Short puts are always Reg-T naked margin (never cash-secured).

    ``allow_illiquid`` bypasses the executability / min-income gates — used for the
    FOCUS path (scoring a trade the user ALREADY holds), where the strike can be
    illiquid/wide but we must still score the position rather than drop it."""
    right = q.right
    if not allow_illiquid and not _executable(q)[0]:     # drop un-fillable quotes (scan only)
        return None
    if not q.mid or q.mid <= 0:                           # no price → can't score either way
        return None
    premium = q.mid * CONTRACT_MULTIPLIER
    if not allow_illiquid and premium < min_income:
        return None
    iv = q.iv if q.iv else atm_iv
    p_keep, method = _prob_keep(rnd, q.strike, right, spot, dte, r, iv)
    if p_keep is None or p_keep < min_prob:
        return None

    # Capital blocked (the YIELD denominator) + the true bounded RISK, by how the short is secured:
    #   short put             → Reg-T naked margin (BPR);  max loss = strike→0 (bounded notional)
    #   covered call (owns)   → the held stock (spot·100); max loss = stock→0 (bounded)
    #   naked call (no stock) → Reg-T naked margin (BPR);  max loss = UNLIMITED (unbounded upside)
    prem_ps = q.mid
    if right == "C" and covered:
        collateral = spot * CONTRACT_MULTIPLIER
        cap_basis = "covered_stock"
        max_loss_val = round(-(spot - prem_ps) * CONTRACT_MULTIPLIER, 2)
        notional_val = round(spot * CONTRACT_MULTIPLIER, 2)
    elif right == "C":
        collateral = round(_reg_t_short_call_bpr(spot, q.strike, prem_ps) * CONTRACT_MULTIPLIER, 2)
        cap_basis = "reg_t_margin"
        max_loss_val = None                                # naked call — upside risk is unbounded
        notional_val = round(spot * CONTRACT_MULTIPLIER, 2)
    else:
        collateral = round(_reg_t_short_put_bpr(spot, q.strike, prem_ps) * CONTRACT_MULTIPLIER, 2)
        cap_basis = "reg_t_margin"
        max_loss_val = round(-(q.strike - prem_ps) * CONTRACT_MULTIPLIER, 2)   # assigned at the strike, stock→0
        notional_val = round(q.strike * CONTRACT_MULTIPLIER, 2)
    premium_ann = annualized_return_pct(premium, collateral, dte)
    static_ret = premium / collateral * 100 if collateral else 0.0

    if right == "C":
        cushion_pct = (q.strike - spot) / spot * 100
        breakeven = round(spot - q.mid, 2)                 # position breakeven (downside)
        if_assigned = ((q.strike - spot) + q.mid) / spot * 100   # capped gain + premium
        total_ann = premium_ann
        # covered = premium alpha on the held stock; naked = AROM excess over the SOFR benchmark
        sofr_excess = round(premium_ann, 2) if covered else round(premium_ann - sofr_pct, 2)
    else:
        cushion_pct = (spot - q.strike) / spot * 100
        breakeven = round(q.strike - q.mid, 2)             # effective purchase price
        if_assigned = (spot - breakeven) / spot * 100      # discount vs spot if assigned
        total_ann = premium_ann                            # Reg-T margin: AROM on the BPR (no full-cash SOFR base)
        sofr_excess = round(premium_ann - sofr_pct, 2)     # carry ALPHA = AROM over the SOFR benchmark

    g = _bs_greeks(spot, q.strike, dte, iv or 0.0, right)
    exp_intr = _expected_intrinsic(rnd, q.strike, right)
    expected_pnl = round(premium - exp_intr * CONTRACT_MULTIPLIER, 2) if exp_intr is not None else None

    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    if cap_basis == "reg_t_margin":
        _risk = (f"the full ${notional_val:,.0f} notional is the assignment risk" if right == "P"
                 else "the upside risk is UNLIMITED (naked call — no stock behind it)")
        flags.append({"level": "info" if right == "P" else "warn", "text":
            f"Reg-T naked {'put' if right == 'P' else 'call'} — ${round(collateral):,} BPR blocked (not "
            f"cash/stock-secured); the yield is a return on MARGIN (AROM). Risk unchanged — {_risk}."})
    return {
        "structure": structure,
        "label": label if (right == "P" or covered) else "Naked Call (Reg-T margin)",
        "capital_basis": cap_basis,
        "notional_capital": notional_val,
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
        "max_profit": round(premium + (q.strike - spot) * CONTRACT_MULTIPLIER, 2) if (right == "C" and covered) else round(premium, 2),
        "max_loss": max_loss_val,               # put: strike→0 · covered call: stock→0 · naked call: None (unlimited)
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


def _short_strangle(calls: dict, puts: dict, spot: float, dte: int, exp: str, rnd, r: float,
                    atm_iv: Optional[float], iv_hv_ratio: Optional[float], richness: str,
                    min_prob: float, min_income: float, european: bool, ticker: str,
                    ta_levels: Optional[dict] = None, sig_frac: Optional[float] = None) -> Optional[dict]:
    """Neutral, UNDEFINED-RISK income: short OTM put + short OTM call (naked both wings). Income while
    the underlying stays between the shorts. Collects more premium than the iron condor in exchange for
    an open tail; sized by naked margin, not cash. P(keep) = P(in band). The iron condor is its
    defined-risk cousin."""
    if rnd is None:
        return None
    put_strikes = sorted((k for k in puts if k < spot), reverse=True)
    call_strikes = sorted(k for k in calls if k > spot)
    if not put_strikes or not call_strikes:
        return None
    tail = (1 - min_prob) / 2.0                       # split the breach budget both sides
    # TA-aware: bias each short toward the nearest support (put) / resistance (call) on the safe side.
    kp_s = _snap_short_to_levels(put_strikes, rnd.strike_for_prob_below(tail), spot, "below", ta_levels, sig_frac)
    kc_s = _snap_short_to_levels(call_strikes, rnd.strike_for_prob_below(1 - tail), spot, "above", ta_levels, sig_frac)
    if not kp_s or not kc_s or kp_s not in puts or kc_s not in calls:
        return None
    if not all(_executable(q)[0] for q in (puts[kp_s], calls[kc_s])):
        return None
    net_credit = puts[kp_s].mid + calls[kc_s].mid
    premium = net_credit * CONTRACT_MULTIPLIER
    if net_credit <= 0 or premium < min_income:
        return None
    p_keep = rnd.prob_below(kc_s) - rnd.prob_below(kp_s)   # both shorts OTM (finish in band)
    if p_keep < min_prob:
        return None
    # Reg-T naked margin (approx): the greater single-leg requirement + the credit kept.
    put_m = max(0.20 * spot - (spot - kp_s), 0.10 * kp_s) * CONTRACT_MULTIPLIER
    call_m = max(0.20 * spot - (kc_s - spot), 0.10 * kc_s) * CONTRACT_MULTIPLIER
    capital = round(max(put_m, call_m) + premium, 2)
    premium_ann = annualized_return_pct(premium, capital, dte)
    gp = _bs_greeks(spot, kp_s, dte, (puts[kp_s].iv or atm_iv or 0.0), "P")
    gc = _bs_greeks(spot, kc_s, dte, (calls[kc_s].iv or atm_iv or 0.0), "C")
    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    flags.append({"level": "warn",
                  "text": "Undefined risk — naked both wings; sized by margin, not cash. The iron condor caps this tail."})
    flags.append({"level": "info", "text": f"Profit if it stays in ${round(kp_s, 2)}–${round(kc_s, 2)}"})
    return {
        "structure": "short_strangle", "label": "Short Strangle (naked put + call)",
        "expiration": exp, "dte": dte,
        "short_strike": round(kp_s, 2), "short_strike_pct": round((kp_s - spot) / spot * 100, 1),
        "put_short": round(kp_s, 2), "call_short": round(kc_s, 2),
        "band_low": round(kp_s, 2), "band_high": round(kc_s, 2),
        "short_delta": gp["delta"],
        "prob_keep_pct": round(p_keep * 100, 1), "prob_assign_pct": round((1 - p_keep) * 100, 1),
        "prob_in_band_pct": round(p_keep * 100, 1), "prob_method": "RND",
        "premium": round(premium, 2), "premium_per_share": round(net_credit, 2),
        "collateral": capital,
        "premium_annualized_pct": round(premium_ann, 2), "total_annualized_pct": round(premium_ann, 2),
        "sofr_excess_pct": round(premium_ann, 2), "beats_sofr": True,
        "static_return_pct": round(premium / capital * 100, 2) if capital else 0.0,
        "breakeven": round(kp_s - net_credit, 2),
        "cushion_pct": round(min(spot - kp_s, kc_s - spot) / spot * 100, 2),
        "max_profit": round(premium, 2), "max_loss": None,          # undefined (naked)
        "expected_pnl": None,
        "greeks": {"delta": round(gp["delta"] + gc["delta"], 3), "gamma": round(gp["gamma"] + gc["gamma"], 4),
                   "theta": round(gp["theta"] + gc["theta"], 4), "vega": round(gp["vega"] + gc["vega"], 4)},
        "theta_per_day": round(-(gp["theta"] + gc["theta"]) * CONTRACT_MULTIPLIER, 2),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio, "premium_richness": richness,
        "liquidity": {"oi": min(puts[kp_s].oi or 0, calls[kc_s].oi or 0),
                      "volume": min(puts[kp_s].volume or 0, calls[kc_s].volume or 0),
                      "spread_pct": max(_spread_pct(puts[kp_s]) or 0, _spread_pct(calls[kc_s]) or 0)},
        "exercise_style": "European (cash-settled)" if european else "American",
        "flags": flags,
        "legs": [_leg(puts[kp_s], "SELL", exp, spot, dte, atm_iv, rnd),
                 _leg(calls[kc_s], "SELL", exp, spot, dte, atm_iv, rnd)],
    }


def _focus_strangle(put_q, call_q, *, spot, dte, exp, rnd, r, atm_iv,
                    iv_hv_ratio, richness, european, ticker) -> Optional[dict]:
    """Focus (placed-trade) short strangle priced at the USER'S EXACT short strikes.

    _short_strangle RE-DERIVES near-delta strikes from the RND, so scoring a HELD
    strangle through it silently scores a DIFFERENT, near-ATM strangle (the bug that
    made a far-OTM GLD 300/470 read STRONG_CLOSE at 0/100 — the desk saw a ~ATM band).
    This prices the exact put+call the user holds, gates OFF (the trade is already on).
    P(keep)=P(finish in band) from the RND when available, else a lognormal-BS estimate."""
    if put_q is None or call_q is None:
        return None
    kp_s, kc_s = float(put_q.strike), float(call_q.strike)
    net_credit = (put_q.mid or 0.0) + (call_q.mid or 0.0)
    premium = net_credit * CONTRACT_MULTIPLIER
    p_keep = None
    if rnd is not None:
        try:
            p_keep = max(0.0, min(1.0, rnd.prob_below(kc_s) - rnd.prob_below(kp_s)))
        except Exception:  # noqa: BLE001
            p_keep = None
    if p_keep is None:                                   # RND unavailable → lognormal BS band prob
        iv = atm_iv or getattr(put_q, "iv", None) or getattr(call_q, "iv", None) or 0.30
        T = max(dte, 1) / 365.0
        p_keep = max(0.0, min(1.0, bs_prob_otm(spot, kc_s, T, r, iv, "call")
                                   + bs_prob_otm(spot, kp_s, T, r, iv, "put") - 1.0))
    # Reg-T naked margin (approx): greater single-leg requirement + credit kept.
    put_m = max(0.20 * spot - (spot - kp_s), 0.10 * kp_s) * CONTRACT_MULTIPLIER
    call_m = max(0.20 * spot - (kc_s - spot), 0.10 * kc_s) * CONTRACT_MULTIPLIER
    capital = round(max(put_m, call_m) + premium, 2)
    premium_ann = annualized_return_pct(premium, capital, dte) if (premium > 0 and capital > 0) else 0.0
    gp = _bs_greeks(spot, kp_s, dte, (getattr(put_q, "iv", None) or atm_iv or 0.0), "P")
    gc = _bs_greeks(spot, kc_s, dte, (getattr(call_q, "iv", None) or atm_iv or 0.0), "C")
    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    flags.append({"level": "warn", "text": "Undefined risk — naked both wings; sized by margin, not cash."})
    flags.append({"level": "info", "text": f"Profit if it stays in ${round(kp_s, 2)}–${round(kc_s, 2)}"})
    return {
        "structure": "short_strangle", "label": "Short Strangle (naked put + call)",
        "expiration": exp, "dte": dte,
        "short_strike": round(kp_s, 2), "short_strike_pct": round((kp_s - spot) / spot * 100, 1),
        "put_short": round(kp_s, 2), "call_short": round(kc_s, 2),
        "band_low": round(kp_s, 2), "band_high": round(kc_s, 2),
        "short_delta": gp["delta"],
        "prob_keep_pct": round(p_keep * 100, 1), "prob_assign_pct": round((1 - p_keep) * 100, 1),
        "prob_in_band_pct": round(p_keep * 100, 1), "prob_method": "RND" if rnd is not None else "BS",
        "premium": round(premium, 2), "premium_per_share": round(net_credit, 2),
        "collateral": capital,
        "premium_annualized_pct": round(premium_ann, 2), "total_annualized_pct": round(premium_ann, 2),
        "sofr_excess_pct": round(premium_ann, 2), "beats_sofr": premium_ann > 0,
        "static_return_pct": round(premium / capital * 100, 2) if capital else 0.0,
        "breakeven": round(kp_s - net_credit, 2),
        "cushion_pct": round(min(spot - kp_s, kc_s - spot) / spot * 100, 2),   # nearest breach, both OTM ≥ 0
        "max_profit": round(premium, 2), "max_loss": None,          # undefined (naked)
        "expected_pnl": None,
        "greeks": {"delta": round(gp["delta"] + gc["delta"], 3), "gamma": round(gp["gamma"] + gc["gamma"], 4),
                   "theta": round(gp["theta"] + gc["theta"], 4), "vega": round(gp["vega"] + gc["vega"], 4)},
        "theta_per_day": round(-(gp["theta"] + gc["theta"]) * CONTRACT_MULTIPLIER, 2),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio, "premium_richness": richness,
        "liquidity": {"oi": min(getattr(put_q, "oi", 0) or 0, getattr(call_q, "oi", 0) or 0),
                      "volume": min(getattr(put_q, "volume", 0) or 0, getattr(call_q, "volume", 0) or 0),
                      "spread_pct": max(_spread_pct(put_q) or 0, _spread_pct(call_q) or 0)},
        "exercise_style": "European (cash-settled)" if european else "American",
        "flags": flags,
        "legs": [_leg(put_q, "SELL", exp, spot, dte, atm_iv, rnd),
                 _leg(call_q, "SELL", exp, spot, dte, atm_iv, rnd)],
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
                 min_prob: float, min_income: float, european: bool, ticker: str,
                 ta_levels: Optional[dict] = None, sig_frac: Optional[float] = None) -> Optional[dict]:
    """Neutral, defined-risk both sides: short put spread + short call spread. Income if
    the underlying stays between the short strikes. P(keep) = P(in band)."""
    if rnd is None:
        return None
    put_strikes = sorted((k for k in puts if k < spot), reverse=True)
    call_strikes = sorted(k for k in calls if k > spot)
    if not put_strikes or not call_strikes:
        return None
    tail = (1 - min_prob) / 2.0                       # split the breach budget both sides
    # TA-aware: bias each short toward the nearest support (put) / resistance (call) on the safe side.
    kp_s = _snap_short_to_levels(put_strikes, rnd.strike_for_prob_below(tail), spot, "below", ta_levels, sig_frac)
    kc_s = _snap_short_to_levels(call_strikes, rnd.strike_for_prob_below(1 - tail), spot, "above", ta_levels, sig_frac)
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
                 min_prob: float, min_income: float, european: bool, ticker: str,
                 ta_levels: Optional[dict] = None, sig_frac: Optional[float] = None) -> Optional[dict]:
    """Short put + short call spread, sized so net credit ≥ call-spread width ⇒ NO upside
    risk. Downside is CSP-style (the short put). P(keep) = P(put not assigned)."""
    if rnd is None:
        return None
    put_strikes = sorted((k for k in puts if k < spot), reverse=True)
    call_strikes = sorted(k for k in calls if k > spot)
    if not put_strikes or not call_strikes:
        return None
    kp = _headline_put(rnd, put_strikes, spot, min_prob, ta_levels, sig_frac)   # CSP-style short put — TA-biased
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
# Calendar / horizontal spread — the ONLY long-vega income structure (spans two expiries)
# ---------------------------------------------------------------------------

def _calendar(front_q: OptionQuote, back_q: OptionQuote, spot: float, front_dte: int, back_dte: int,
              front_exp: str, back_exp: str, right: str, front_atm_iv: Optional[float],
              back_atm_iv: Optional[float], r: float, min_income: float, richness: str,
              iv_hv_ratio: Optional[float]) -> Optional[dict]:
    """Neutral, defined-risk, LONG-VEGA income: SELL the near-dated option, BUY the far-dated at the SAME
    strike (a horizontal calendar). Harvests the theta differential (the front decays faster) and is long
    vega (gains if IV rises / a backwardated front-vs-back term structure normalises) — the counterpart to
    the short-vol strangle / condor. Max loss = the net debit; max profit ≈ at the strike on the FRONT
    expiry. Payoff is a HORIZON curve (the back leg is still alive at front expiry)."""
    from .lifecycle_service import horizon_payoff_curve
    if front_q is None or back_q is None or back_dte <= front_dte:
        return None
    K = float(front_q.strike)
    if abs(float(back_q.strike) - K) > 1e-6:
        return None
    if not _executable(front_q)[0] or not _executable(back_q)[0]:
        return None
    fmid, bmid = float(front_q.mid or 0.0), float(back_q.mid or 0.0)
    net_debit = round(bmid - fmid, 4)
    if net_debit <= 0.02:                                # must be a real debit (back richer than front)
        return None
    cost = round(net_debit * CONTRACT_MULTIPLIER, 2)     # capital at risk = the debit = max loss
    otype = "call" if right == "C" else "put"
    fiv = float(front_q.iv or front_atm_iv or 0.30)      # OptionQuote.iv is already a decimal
    biv = float(back_q.iv or back_atm_iv or 0.30)
    T = max(front_dte, 1) / 365.0
    t_rem = max(back_dte - front_dte, 1) / 365.0
    max_profit = round((bs_price(K, K, t_rem, r, biv, otype) - net_debit) * CONTRACT_MULTIPLIER, 2)
    if max_profit < max(min_income, 1.0):
        return None
    legs_life = [
        {"strike": K, "right": right, "sign": -1, "qty": 1, "price": fmid, "iv": fiv, "dte_years": front_dte / 365.0},
        {"strike": K, "right": right, "sign": 1, "qty": 1, "price": bmid, "iv": biv, "dte_years": back_dte / 365.0},
    ]
    curve = horizon_payoff_curve(legs_life, 0.0, spot, T, r=r)
    bes = []
    for a, b in zip(curve, curve[1:]):
        if (a["pnl"] < 0 <= b["pnl"]) or (a["pnl"] >= 0 > b["pnl"]):
            denom = (b["pnl"] - a["pnl"]) or 1e-9
            bes.append(round(a["price"] + (b["price"] - a["price"]) * (0 - a["pnl"]) / denom, 2))
    be_low = min(bes) if bes else None
    be_high = max(bes) if bes else None

    def _cdf_below(x):                                   # lognormal P(S_T < x) at the FRONT horizon
        if not x or x <= 0 or fiv <= 0:
            return None
        d = (math.log(x / spot) - (r - 0.5 * fiv * fiv) * T) / (fiv * math.sqrt(T))
        return 0.5 * (1 + math.erf(d / math.sqrt(2)))
    cl, ch = _cdf_below(be_low), _cdf_below(be_high)
    p_profit = max(0.0, min(1.0, ch - cl)) if (cl is not None and ch is not None) else None

    gf = _bs_greeks(spot, K, front_dte, fiv, right)
    gb = _bs_greeks(spot, K, back_dte, biv, right)
    net = {k: (gb[k] - gf[k]) for k in ("delta", "gamma", "theta", "vega")}   # LONG back − SHORT front
    # A calendar's max profit is a LARGE fraction of a SMALL debit over a short front — COMPOUND-annualizing
    # it explodes (billions of %). Use a LINEAR, probability-weighted annualization capped at a sane ceiling;
    # the risk-adjusted desk score (not this headline) does the real ranking.
    _pf = p_profit if p_profit is not None else 0.5
    premium_ann = round(min((max_profit / cost) * _pf * (365.0 / max(front_dte, 1)), 3.0) * 100.0, 1)

    flags = _opp_flags(richness, front_atm_iv, iv_hv_ratio)
    flags.append({"level": "info",
                  "text": f"Debit calendar — SELL {front_exp} / BUY {back_exp} @ {round(K, 2)}. Max loss = debit "
                          f"${cost}; max profit ~${max_profit} at ${round(K, 2)} on the {front_exp} expiry. LONG "
                          f"vega — gains if IV rises (best in contango / when the front is rich to the back)."})
    if be_low and be_high:
        flags.append({"level": "info", "text": f"Profit tent ${be_low}–${be_high} at front expiry"})
    return {
        "structure": "calendar",
        "label": f"{'Call' if right == 'C' else 'Put'} Calendar ({front_exp} → {back_exp})",
        "expiration": front_exp, "dte": front_dte, "back_expiration": back_exp, "back_dte": back_dte,
        "short_strike": round(K, 2), "short_strike_pct": round((K - spot) / spot * 100, 1),
        "put_short": round(be_low, 2) if be_low else None,     # danger edge DOWN (tent collapses beyond it)
        "call_short": round(be_high, 2) if be_high else None,  # danger edge UP
        "band_low": be_low, "band_high": be_high,
        "prob_keep_pct": round(p_profit * 100, 1) if p_profit is not None else None,
        "prob_in_band_pct": round(p_profit * 100, 1) if p_profit is not None else None,
        "prob_method": "lognormal(front)",
        "net_debit": net_debit,
        "premium": max_profit, "premium_per_share": round(max_profit / CONTRACT_MULTIPLIER, 2),
        "collateral": cost,
        "premium_annualized_pct": round(premium_ann, 2), "total_annualized_pct": round(premium_ann, 2),
        "sofr_excess_pct": round(premium_ann, 2), "beats_sofr": True,
        "static_return_pct": round(max_profit / cost * 100, 2) if cost else 0.0,
        "breakeven": be_low,
        "cushion_pct": (round(min(spot - be_low, be_high - spot) / spot * 100, 2)
                        if (be_low and be_high) else None),
        "max_profit": max_profit, "max_loss": round(-cost, 2), "expected_pnl": None,
        "greeks": {"delta": round(net["delta"], 3), "gamma": round(net["gamma"], 4),
                   "theta": round(net["theta"], 4), "vega": round(net["vega"], 4)},
        "theta_per_day": round(net["theta"] * CONTRACT_MULTIPLIER, 2),    # + = net decay collected
        "vega_exposure": round(net["vega"] * CONTRACT_MULTIPLIER, 2),     # + = LONG vega
        "atm_iv_pct": round(front_atm_iv * 100, 1) if front_atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio, "premium_richness": richness,
        "liquidity": {"oi": min(front_q.oi or 0, back_q.oi or 0),
                      "volume": min(front_q.volume or 0, back_q.volume or 0),
                      "spread_pct": max(_spread_pct(front_q) or 0, _spread_pct(back_q) or 0)},
        "exercise_style": "American",
        "flags": flags,
        "legs": [_leg(front_q, "SELL", front_exp, spot, front_dte, front_atm_iv),
                 _leg(back_q, "BUY", back_exp, spot, back_dte, back_atm_iv)],
    }


def _scan_calendars(chains: list, spot: float, r: float, min_income: float,
                    hv: Optional[float]) -> list[dict]:
    """Build calendar(s) from the cached per-expiry chains: pair the nearest expiry (front, ≥7 DTE) with a
    later one (≥20 days out) and write an ATM calendar. `chains` = [(dte, exp, calls, puts)] sorted by dte."""
    usable = sorted([c for c in chains if c[0] and c[0] >= 7], key=lambda c: c[0])
    if len(usable) < 2:
        return []
    fdte, fexp, fcalls, fputs = usable[0]
    back = next((c for c in reversed(usable) if c[0] - fdte >= 20), None)
    if not back:
        return []
    bdte, bexp, bcalls, bputs = back
    f_atm = _atm_iv(None, fcalls, fputs, spot)
    b_atm = _atm_iv(None, bcalls, bputs, spot)
    iv_hv_ratio, richness = _richness(f_atm, hv)
    out: list[dict] = []
    for right, fbook, bbook in (("C", fcalls, bcalls), ("P", fputs, bputs)):
        common = sorted(set(fbook) & set(bbook), key=lambda k: abs(k - spot))
        if not common:
            continue
        K = common[0]                                    # ATM common strike
        o = _calendar(fbook[K], bbook[K], spot, fdte, bdte, fexp, bexp, right,
                      f_atm, b_atm, r, min_income, richness, iv_hv_ratio)
        if o:
            out.append(o)
    # keep the single best ATM calendar (call/put are ~equivalent at ATM; pick the higher keep-prob)
    out.sort(key=lambda o: (o.get("prob_keep_pct") or 0, o.get("premium_annualized_pct") or 0), reverse=True)
    return out[:1]


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


def _focus_iron_condor(sp: OptionQuote, lp: OptionQuote, sc: OptionQuote, lc: OptionQuote,
                       spot: float, dte: int, exp: str, rnd, atm_iv: Optional[float],
                       iv_hv_ratio: Optional[float], richness: str, european: bool,
                       ticker: str) -> Optional[dict]:
    """Iron condor at the PLACED trade's EXACT four strikes — same math as
    `_iron_condor`, but the strikes are given (not scan-picked), so the desk can
    score the condor the user actually holds."""
    if rnd is None or not all(_executable(q)[0] for q in (sp, lp, sc, lc)):
        return None
    net_credit = (sp.mid - lp.mid) + (sc.mid - lc.mid)
    premium = net_credit * CONTRACT_MULTIPLIER
    if net_credit <= 0:
        return None
    p_keep = rnd.prob_below(sc.strike) - rnd.prob_below(sp.strike)   # finish in band
    capital = max(sp.strike - lp.strike, lc.strike - sc.strike) * CONTRACT_MULTIPLIER - premium
    if capital <= 0:
        return None
    premium_ann = annualized_return_pct(premium, capital, dte)
    g = _bs_greeks(spot, sc.strike, dte, (sc.iv or atm_iv or 0.0), "C")
    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    return {
        "structure": "iron_condor", "label": "Iron Condor", "expiration": exp, "dte": dte,
        "short_strike": round(sp.strike, 2), "short_strike_pct": round((sp.strike - spot) / spot * 100, 1),
        "put_short": round(sp.strike, 2), "put_long": round(lp.strike, 2),
        "call_short": round(sc.strike, 2), "call_long": round(lc.strike, 2),
        "band_low": round(sp.strike, 2), "band_high": round(sc.strike, 2),
        "short_delta": g["delta"],
        "prob_keep_pct": round(p_keep * 100, 1), "prob_assign_pct": round((1 - p_keep) * 100, 1),
        "prob_in_band_pct": round(p_keep * 100, 1), "prob_method": "RND",
        "premium": round(premium, 2), "premium_per_share": round(net_credit, 2),
        "collateral": round(capital, 2),
        "premium_annualized_pct": round(premium_ann, 2), "total_annualized_pct": round(premium_ann, 2),
        "sofr_excess_pct": round(premium_ann, 2), "beats_sofr": True,
        "static_return_pct": round(premium / capital * 100, 2),
        "breakeven": round(sp.strike - net_credit, 2),
        "cushion_pct": round((spot - sp.strike) / spot * 100, 2),
        "max_profit": round(premium, 2), "max_loss": round(capital, 2), "expected_pnl": None,
        "greeks": {"delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"]},
        "theta_per_day": round(-g["theta"] * CONTRACT_MULTIPLIER, 2),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio, "premium_richness": richness,
        "liquidity": {"oi": sc.oi, "volume": sc.volume, "spread_pct": _spread_pct(sc)},
        "exercise_style": "European (cash-settled)" if european else "American", "flags": flags,
        "legs": [_leg(sp, "SELL", exp, spot, dte, atm_iv, rnd), _leg(lp, "BUY", exp, spot, dte, atm_iv, rnd),
                 _leg(sc, "SELL", exp, spot, dte, atm_iv, rnd), _leg(lc, "BUY", exp, spot, dte, atm_iv, rnd)],
    }


def _focus_jade_lizard(kp: OptionQuote, sc: OptionQuote, lc: OptionQuote,
                       spot: float, dte: int, exp: str, rnd, r: float, atm_iv: Optional[float],
                       iv_hv_ratio: Optional[float], richness: str, european: bool,
                       ticker: str) -> Optional[dict]:
    """Jade lizard (short put + short call spread) at the PLACED trade's EXACT
    three strikes — same shape as `_jade_lizard`, strikes given not searched."""
    if rnd is None or not all(_executable(q)[0] for q in (kp, sc, lc)):
        return None
    net_credit = kp.mid + sc.mid - lc.mid
    premium = net_credit * CONTRACT_MULTIPLIER
    if net_credit <= 0:
        return None
    p_keep, method = _prob_keep(rnd, kp.strike, "P", spot, dte, r, kp.iv or atm_iv)
    if p_keep is None:
        return None
    collateral = kp.strike * CONTRACT_MULTIPLIER
    premium_ann = annualized_return_pct(premium, collateral, dte)
    g = _bs_greeks(spot, kp.strike, dte, (kp.iv or atm_iv or 0.0), "P")
    no_upside = net_credit >= (lc.strike - sc.strike)
    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    return {
        "structure": "jade_lizard", "label": "Jade Lizard", "expiration": exp, "dte": dte,
        "short_strike": round(kp.strike, 2), "short_strike_pct": round((kp.strike - spot) / spot * 100, 1),
        "put_short": round(kp.strike, 2), "call_short": round(sc.strike, 2), "call_long": round(lc.strike, 2),
        "short_delta": g["delta"],
        "prob_keep_pct": round(p_keep * 100, 1), "prob_assign_pct": round((1 - p_keep) * 100, 1),
        "prob_method": method,
        "premium": round(premium, 2), "premium_per_share": round(net_credit, 2),
        "collateral": round(collateral, 2),
        "premium_annualized_pct": round(premium_ann, 2), "total_annualized_pct": round(premium_ann, 2),
        "sofr_excess_pct": round(premium_ann, 2), "beats_sofr": premium_ann > 0,
        "static_return_pct": round(premium / collateral * 100, 2),
        "breakeven": round(kp.strike - net_credit, 2),
        "cushion_pct": round((spot - kp.strike) / spot * 100, 2),
        # No upside risk only if credit ≥ call-spread width; otherwise upside is capped-loss.
        "max_profit": round(premium, 2),
        "max_loss": None if no_upside else round((lc.strike - sc.strike) * CONTRACT_MULTIPLIER - premium, 2),
        "expected_pnl": None,
        "greeks": {"delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"]},
        "theta_per_day": round(-g["theta"] * CONTRACT_MULTIPLIER, 2),
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio, "premium_richness": richness,
        "liquidity": {"oi": kp.oi, "volume": kp.volume, "spread_pct": _spread_pct(kp)},
        "exercise_style": "European (cash-settled)" if european else "American", "flags": flags,
        "legs": [_leg(kp, "SELL", exp, spot, dte, atm_iv, rnd),
                 _leg(sc, "SELL", exp, spot, dte, atm_iv, rnd),
                 _leg(lc, "BUY", exp, spot, dte, atm_iv, rnd)],
    }


def _build_focus_opp(focus: dict, calls: dict, puts: dict, common: dict, sofr_pct: float) -> Optional[dict]:
    """Build ONE candidate at a PLACED trade's EXACT legs from the fresh chain,
    with the scan's OTM / min-prob / min-income filters turned OFF — so the desk
    can score the trade the user actually holds, not just the ones on the scan
    grid (removes the 'not among candidates' gap). Reuses the same builders, so
    price / bid-ask / greeks / IV / OI / volume are all live for the real strikes."""
    c0 = {**common, "min_prob": 0.0, "min_income": 0.0}
    structure = focus.get("structure")
    legs = focus.get("legs") or []

    def _q(strike, side):
        if strike is None:
            return None
        for k, q in side.items():
            if abs(float(k) - float(strike)) < 0.01:
                return q
        return None

    sp = [l for l in legs if l.get("right") == "P" and l.get("action") == "SELL"]
    lp = [l for l in legs if l.get("right") == "P" and l.get("action") == "BUY"]
    sc = [l for l in legs if l.get("right") == "C" and l.get("action") == "SELL"]
    lc = [l for l in legs if l.get("right") == "C" and l.get("action") == "BUY"]
    try:
        if structure == "covered_call" and sc:
            q = _q(sc[0]["strike"], calls)
            return _single_leg_income("covered_call", "Covered Call", q, sofr_pct=sofr_pct,
                                      allow_illiquid=True, covered=True, **c0) if q else None
        if structure == "naked_call" and sc:
            q = _q(sc[0]["strike"], calls)
            if not q:
                return None
            # NAKED short call — _single_leg_income(covered=False) already sets the Reg-T naked-call BPR
            # (premium included), max_profit = the credit, and max_loss = None (unbounded upside).
            opp = _single_leg_income("naked_call", "Naked Call", q, sofr_pct=sofr_pct,
                                     allow_illiquid=True, covered=False, **c0)
            if opp:
                opp["unbounded_loss"] = True
            return opp
        if structure == "cash_secured_put" and sp:
            q = _q(sp[0]["strike"], puts)
            return _single_leg_income("cash_secured_put", "Cash-Secured Put", q, sofr_pct=sofr_pct, allow_illiquid=True, **c0) if q else None
        if structure == "put_credit_spread" and sp and lp:
            sq, lq = _q(sp[0]["strike"], puts), _q(lp[0]["strike"], puts)
            return _credit_spread("put_credit_spread", "Put Credit Spread", sq, lq, **c0) if sq and lq else None
        if structure == "call_credit_spread" and sc and lc:
            sq, lq = _q(sc[0]["strike"], calls), _q(lc[0]["strike"], calls)
            return _credit_spread("call_credit_spread", "Call Credit Spread", sq, lq, **c0) if sq and lq else None
        if structure == "short_strangle" and sp and sc:
            # Price the USER'S EXACT short put + call (NOT _short_strangle, which re-derives
            # near-ATM strikes and would score a different band).
            sq, cq = _q(sp[0]["strike"], puts), _q(sc[0]["strike"], calls)
            return _focus_strangle(sq, cq, spot=common["spot"], dte=common["dte"], exp=common["exp"],
                                   rnd=common["rnd"], r=common["r"], atm_iv=common["atm_iv"],
                                   iv_hv_ratio=common["iv_hv_ratio"], richness=common["richness"],
                                   european=common["european"], ticker=common["ticker"]) if sq and cq else None
        if structure == "iron_condor" and sp and lp and sc and lc:
            sq, lpq = _q(sp[0]["strike"], puts), _q(lp[0]["strike"], puts)
            scq, lcq = _q(sc[0]["strike"], calls), _q(lc[0]["strike"], calls)
            if sq and lpq and scq and lcq:
                return _focus_iron_condor(sq, lpq, scq, lcq, spot=common["spot"], dte=common["dte"],
                                          exp=common["exp"], rnd=common["rnd"], atm_iv=common["atm_iv"],
                                          iv_hv_ratio=common["iv_hv_ratio"], richness=common["richness"],
                                          european=common["european"], ticker=common["ticker"])
        if structure == "jade_lizard" and sp and sc and lc:
            kpq = _q(sp[0]["strike"], puts)
            scq, lcq = _q(sc[0]["strike"], calls), _q(lc[0]["strike"], calls)
            if kpq and scq and lcq:
                return _focus_jade_lizard(kpq, scq, lcq, spot=common["spot"], dte=common["dte"],
                                          exp=common["exp"], rnd=common["rnd"], r=common["r"],
                                          atm_iv=common["atm_iv"], iv_hv_ratio=common["iv_hv_ratio"],
                                          richness=common["richness"], european=common["european"],
                                          ticker=common["ticker"])
    except Exception as exc:  # noqa: BLE001 — a bad focus leg must not kill the scan
        logger.debug("focus opp build failed (%s): %s", structure, exc)
    return None


# ---------------------------------------------------------------------------
# "Evaluate" — score a user-entered multi-leg trade (bring-your-own trade)
# ---------------------------------------------------------------------------

# Structures the scan's focus mechanism (_build_focus_opp) prices at the user's EXACT strikes;
# everything else (strangle / condor / jade / collar / calendar / custom) is built generically.
# Structures the scan's focus mechanism (_build_focus_opp) prices at the user's EXACT strikes.
# short_strangle is excluded on purpose — its focus builder re-derives strikes, so the Evaluate tab
# builds it generically (exact) instead. collar / calendar / diagonal / custom → generic builder.
_EXACT_FOCUS_STRUCTURES = {"cash_secured_put", "covered_call", "naked_call",
                           "put_credit_spread", "call_credit_spread", "iron_condor", "jade_lizard"}


def _classify_structure(legs: list[dict], has_stock: bool) -> tuple[str, str, bool]:
    """Detect the income structure of a user-entered trade from its leg signature.
    Returns ``(structure_id, human_label, is_custom)``. Legs that don't match a known income
    structure — or that span multiple expiries — classify as calendar / diagonal / custom
    (``is_custom=True``): still evaluated, but the desk grade is flagged indicative."""
    def _rt(t): return "C" if str(t).upper().startswith("C") else "P"
    def _ac(a): return "BUY" if str(a).upper().startswith("B") else "SELL"
    exps = {l.get("expiration") for l in legs if l.get("expiration")}
    multi_exp = len(exps) > 1
    sc = sorted(float(l["strike"]) for l in legs if _rt(l["type"]) == "C" and _ac(l["action"]) == "SELL")
    lc = sorted(float(l["strike"]) for l in legs if _rt(l["type"]) == "C" and _ac(l["action"]) == "BUY")
    sp = sorted(float(l["strike"]) for l in legs if _rt(l["type"]) == "P" and _ac(l["action"]) == "SELL")
    lp = sorted(float(l["strike"]) for l in legs if _rt(l["type"]) == "P" and _ac(l["action"]) == "BUY")
    counts = (len(sc), len(lc), len(sp), len(lp))

    if not multi_exp:
        # Stock-covered structures — gated by "I hold the underlying" (has_stock). A lone short call is
        # a COVERED call when held, else a NAKED call (undefined risk); short call + long put = collar.
        if counts == (1, 0, 0, 0):
            return ("covered_call", "Covered Call", False) if has_stock else ("naked_call", "Naked Call", False)
        if has_stock and counts == (1, 0, 0, 1):
            return "collar", "Collar", False
        # Pure-option structures — independent of holding stock (the checkbox doesn't apply).
        if counts == (0, 0, 1, 0):
            return "cash_secured_put", "Cash-Secured Put", False
        if counts == (0, 0, 1, 1) and lp[0] < sp[0]:
            return "put_credit_spread", "Put Credit Spread", False
        if counts == (1, 1, 0, 0) and lc[0] > sc[0]:
            return "call_credit_spread", "Call Credit Spread", False
        if counts == (1, 0, 1, 0):
            return "short_strangle", "Short Strangle (naked)", False
        if counts == (1, 1, 1, 1):
            return "iron_condor", "Iron Condor", False
        if counts == (1, 1, 1, 0) and lc[0] > sc[0]:
            return "jade_lizard", "Jade Lizard", False
    if multi_exp and len(legs) == 2:
        rights = {_rt(l["type"]) for l in legs}
        strikes = [float(l["strike"]) for l in legs]
        if len(rights) == 1:
            return ("calendar", "Calendar Spread", True) if abs(strikes[0] - strikes[1]) < 0.01 \
                else ("diagonal", "Diagonal Spread", True)
    return "custom", "Custom Multi-Leg", True


def _price_user_leg(leg: dict, cd: dict, spot: float, r: float) -> tuple[dict, float, Optional[float], int]:
    """Price ONE user leg off its expiry's chain (exact strike; BS-synthesized if the strike is
    not listed). ``cd`` = {calls, puts, rnd, atm_iv, dte}. Returns (leg_dict, mid_per_share,
    iv_decimal, sign) where sign is +1 for BUY, −1 for SELL."""
    right = "C" if str(leg["type"]).upper().startswith("C") else "P"
    action = "BUY" if str(leg["action"]).upper().startswith("B") else "SELL"
    sign = 1 if action == "BUY" else -1
    strike = float(leg["strike"])
    exp = leg["expiration"]
    dte = int(cd["dte"]); rnd = cd["rnd"]; atm_iv = cd["atm_iv"]
    side = cd["calls"] if right == "C" else cd["puts"]
    q = next((qq for k, qq in side.items() if abs(float(k) - strike) < 0.01), None)
    if q is not None:
        d = _leg(q, action, exp, spot, dte, atm_iv, rnd)
        iv = q.iv or atm_iv
        return d, float(q.mid), (float(iv) if iv else None), sign
    # unlisted strike → synthesize via Black-Scholes at the ATM smile vol
    iv = float(atm_iv) if atm_iv else 0.30
    otype = "call" if right == "C" else "put"
    price = bs_price(spot, strike, max(dte, 1) / 365.0, r, iv, otype)
    g = _bs_greeks(spot, strike, dte, iv, right)
    reach = _prob_reach(rnd, strike, spot, dte, iv)
    d = {
        "action": action, "type": "CALL" if right == "C" else "PUT",
        "strike": round(strike, 2), "expiration": exp,
        "bid": round(price, 2), "ask": round(price, 2), "mid": round(price, 2),
        "bid_ask_spread_pct": None, "iv": round(iv * 100, 1),
        "oi": 0, "vol": 0,
        "prob_reach_pct": round(reach * 100, 1) if reach is not None else None,
        "delta": g["delta"], "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"],
    }
    return d, float(price), iv, sign


def _grid_prob(rnd, grid: list[dict]) -> Optional[float]:
    """RND probability mass over the price region where the trade is profitable (a generic PoP,
    used when the trade has no defining short strike — pure-long / custom)."""
    if rnd is None or len(grid) < 2:
        return None
    mass = 0.0
    for a, b in zip(grid, grid[1:]):
        if (a["pnl"] + b["pnl"]) / 2.0 > 0:
            try:
                mass += max(0.0, rnd.prob_below(b["price"]) - rnd.prob_below(a["price"]))
            except Exception:  # noqa: BLE001
                pass
    return max(0.0, min(1.0, mass))


def _build_evaluate_opp(legs: list[dict], stock: Optional[dict], chains_by_exp: dict, spot: float,
                        sofr_pct: float, hv: Optional[float], european: bool, ticker: str, r: float,
                        structure_id: str, label: str, is_custom: bool) -> Optional[dict]:
    """Build ONE opportunity dict from the user's EXACT legs (+ optional stock), pricing each leg
    from its own expiry's chain and deriving premium / greeks / payoff / prob generically. Handles
    strangles, condors, jade lizards, collars, calendars/diagonals and custom combos on the same
    desk pipeline. (Single-expiry CSP / covered call / vertical spreads are routed to the exact-
    strike builders by the caller via the scan's focus mechanism.)"""
    from .lifecycle_service import terminal_payoff_curve, horizon_payoff_curve

    shares = float((stock or {}).get("shares") or 0.0)
    priced: list[tuple[dict, float, Optional[float], int]] = []
    for lg in legs:
        cd = chains_by_exp.get(lg["expiration"])
        if not cd:
            continue
        priced.append(_price_user_leg(lg, cd, spot, r))
    if not priced and shares == 0:
        return None

    leg_dicts = [p[0] for p in priced]
    exps_present = {d["expiration"] for d in leg_dicts}
    multi_exp = len(exps_present) > 1
    near = min(leg_dicts, key=lambda d: chains_by_exp[d["expiration"]]["dte"]) if leg_dicts else None
    near_cd = chains_by_exp[near["expiration"]] if near else next(iter(chains_by_exp.values()))
    near_dte = int(near_cd["dte"])
    near_exp = near["expiration"] if near else None
    rnd = near_cd["rnd"]; atm_iv = near_cd["atm_iv"]

    # premium: credit (+) for shorts, debit (−) for longs
    net_credit = sum((-sign) * mid for (_d, mid, _iv, sign) in priced)
    premium = round(net_credit * CONTRACT_MULTIPLIER, 2)

    # position greeks (signed, per-share option); authoritative net Δ/Θ (with stock, ×100) comes from
    # higher_order_greeks downstream — this is the display proxy, kept on the per-share option scale.
    def gsum(key): return sum(sign * (d.get(key) or 0.0) for (d, _m, _iv, sign) in priced)
    greeks = {"delta": round(gsum("delta"), 4), "gamma": round(gsum("gamma"), 5),
              "theta": round(gsum("theta"), 5), "vega": round(gsum("vega"), 4)}
    theta_per_day = round(gsum("theta") * CONTRACT_MULTIPLIER, 2)   # net short → positive income

    # payoff grid (S from 0 → 2×spot); horizon curve for calendars, terminal otherwise
    life = [{"strike": d["strike"], "right": ("C" if d["type"] == "CALL" else "P"),
             "sign": sign, "qty": 1, "price": mid, "iv": iv,
             "dte_years": max(chains_by_exp[d["expiration"]]["dte"], 1) / 365.0}
            for (d, mid, iv, sign) in priced]
    if multi_exp:
        grid = horizon_payoff_curve(life, shares, spot, near_dte / 365.0, r=r,
                                    iv_fallback=(hv or 0.30), lo=-1.0, hi=1.0, step=0.02)
    else:
        grid = terminal_payoff_curve(life, shares, spot, lo=-1.0, hi=1.0, step=0.02)
    pnls = [pt["pnl"] for pt in grid]
    max_profit = round(max(pnls), 2) if pnls else None
    min_pnl = min(pnls) if pnls else 0.0

    # Unbounded upside loss only when net-short calls AREN'T covered by long stock (a covered call /
    # collar's short call is covered by the 100 sh/contract, so it is NOT naked).
    net_call_qty = sum(sign for (d, _m, _iv, sign) in priced if d["type"] == "CALL")
    covered = shares / CONTRACT_MULTIPLIER if shares > 0 else 0.0
    naked_up = (net_call_qty + covered) < -1e-9
    max_loss = None if naked_up else (round(-min_pnl, 2) if min_pnl < 0 else None)

    breakevens = []
    for a, b in zip(grid, grid[1:]):
        if a["pnl"] != b["pnl"] and (a["pnl"] <= 0 <= b["pnl"] or a["pnl"] >= 0 >= b["pnl"]):
            t = a["pnl"] / (a["pnl"] - b["pnl"])
            breakevens.append(round(a["price"] + t * (b["price"] - a["price"]), 2))
    breakeven = min(breakevens, key=lambda x: abs(x - spot)) if breakevens else round(spot, 2)

    # collateral / capital
    if max_loss is not None and max_loss > 0:
        capital = max_loss
    else:
        naked_margin = 0.0
        for (d, _m, _iv, sign) in priced:
            if sign < 0:
                K = d["strike"]
                otm = max(K - spot, 0.0) if d["type"] == "CALL" else max(spot - K, 0.0)
                naked_margin += max(0.20 * spot - otm, 0.10 * K) * CONTRACT_MULTIPLIER
        capital = round(naked_margin + abs(shares) * spot + max(premium, 0.0), 2)
    if not capital or capital <= 0:
        capital = round(spot * CONTRACT_MULTIPLIER, 2)

    # probability of keeping (income) or of profit (custom)
    shorts_put = [d["strike"] for (d, _m, _iv, sign) in priced if sign < 0 and d["type"] == "PUT"]
    shorts_call = [d["strike"] for (d, _m, _iv, sign) in priced if sign < 0 and d["type"] == "CALL"]
    prob_method = "RND" if rnd is not None else "BS"
    p_keep = None
    if rnd is not None and (shorts_put or shorts_call):
        try:
            hi_b = rnd.prob_below(min(shorts_call)) if shorts_call else 1.0
            lo_b = rnd.prob_below(max(shorts_put)) if shorts_put else 0.0
            p_keep = max(0.0, min(1.0, hi_b - lo_b))
        except Exception:  # noqa: BLE001
            p_keep = None
    if p_keep is None:
        p_keep = _grid_prob(rnd, grid)
    prob_keep_pct = round((p_keep or 0.0) * 100, 1)

    iv_hv_ratio, richness = _richness(atm_iv, hv)
    static_ret = premium / capital * 100 if capital else 0.0
    # Only annualize genuine income (net credit); a net debit reads as its period return, not a yield.
    premium_ann = annualized_return_pct(premium, capital, near_dte) if (premium > 0 and capital > 0) else round(static_ret, 2)

    # Cushion = distance to the NEAREST short strike that could be breached, SIDE-AWARE:
    # a short call is breached from ABOVE (K−spot), a short put from BELOW (spot−K). The
    # book cushion is the thinner of the two (min), positive while both wings are OTM. A
    # single (nearest−spot) formula wrongly flips the sign on a below-spot short put.
    short_strike = None
    leg_cush = ([((k - spot) / spot, k) for k in shorts_call]
                + [((spot - k) / spot, k) for k in shorts_put])
    if leg_cush:
        cushion_frac, short_strike = min(leg_cush, key=lambda t: t[0])
    else:
        cushion_frac = (breakeven - spot) / spot if breakeven else 0.0
    cushion_pct = round(cushion_frac * 100, 2)

    ois = [d.get("oi") or 0 for d in leg_dicts]; vols = [d.get("vol") or 0 for d in leg_dicts]
    sprs = [d.get("bid_ask_spread_pct") for d in leg_dicts if d.get("bid_ask_spread_pct") is not None]

    flags = _opp_flags(richness, atm_iv, iv_hv_ratio)
    if is_custom:
        flags.append({"level": "info",
                      "text": "Custom / calendar structure — desk grade is INDICATIVE (income-desk "
                              "metrics are calibrated for premium-selling trades)."})
    if multi_exp:
        flags.append({"level": "info", "text": f"Multi-expiry: legs across {', '.join(sorted(exps_present))}"})
    if naked_up:
        flags.append({"level": "warn", "text": "Undefined upside risk — net short calls (naked)."})
    if premium < 0:
        flags.append({"level": "info", "text": f"Net DEBIT — pays ${abs(premium):.0f} to open (not premium income)."})

    opp = {
        "structure": structure_id, "label": label, "is_custom": bool(is_custom),
        "expiration": near_exp, "dte": near_dte,
        "short_strike": round(short_strike, 2) if short_strike else round(spot, 2),
        "short_strike_pct": round((short_strike - spot) / spot * 100, 1) if short_strike else None,
        # PRIMARY short per side (nearest spot) — so the desk's structural / breach / fortified / systemic-beta
        # factors fire on a bring-your-own EVALUATE trade EXACTLY as on a scanned one (they read put_short/
        # call_short; the scan builders set these, so the evaluate opp must too — else the two would diverge).
        "put_short": round(max(shorts_put), 2) if shorts_put else None,
        "call_short": round(min(shorts_call), 2) if shorts_call else None,
        "short_delta": round(gsum("delta"), 3),
        "stock_shares": shares,           # explicit signed shares → desk metrics include the stock leg
        "prob_keep_pct": prob_keep_pct, "prob_assign_pct": round(max(0.0, 100 - prob_keep_pct), 1),
        "prob_in_band_pct": prob_keep_pct if (shorts_put and shorts_call) else None,
        "prob_method": prob_method,
        "premium": premium, "premium_per_share": round(net_credit, 2), "collateral": capital,
        "premium_annualized_pct": round(premium_ann, 2), "total_annualized_pct": round(premium_ann, 2),
        "sofr_pct": round(sofr_pct, 2), "sofr_excess_pct": round(premium_ann, 2),
        "beats_sofr": premium_ann > sofr_pct,
        "static_return_pct": round(static_ret, 2),
        "breakeven": breakeven, "cushion_pct": cushion_pct,
        "band_low": round(max(shorts_put), 2) if shorts_put else None,
        "band_high": round(min(shorts_call), 2) if shorts_call else None,
        "max_profit": max_profit, "max_loss": max_loss, "expected_pnl": None,
        "greeks": greeks, "theta_per_day": theta_per_day,
        "atm_iv_pct": round(atm_iv * 100, 1) if atm_iv else None,
        "iv_hv_ratio": iv_hv_ratio, "premium_richness": richness,
        "liquidity": {"oi": min(ois) if ois else 0, "volume": min(vols) if vols else 0,
                      "spread_pct": max(sprs) if sprs else None},
        "exercise_style": "European (cash-settled)" if european else "American",
        "flags": flags, "legs": leg_dicts,
    }
    strikes_all = near_cd.get("strikes") or sorted(set(near_cd["calls"]) | set(near_cd["puts"]))
    quant = _quant_block(rnd, near_cd["calls"], near_cd["puts"], strikes_all, spot, near_dte, atm_iv)
    opp["confidence"] = _confidence(opp, quant)
    return opp


def _scan_expiry(chain: OptionChain, spot: float, dte: int, exp: str, today: date,
                 r: float, sofr_pct: float, hv: Optional[float], structures: list[str],
                 european: bool, next_earnings: Optional[str], min_prob: float,
                 min_income: float, ticker: str, focus: Optional[dict] = None,
                 owns_underlying: bool = False,
                 ta_levels: Optional[dict] = None) -> tuple[list[dict], dict]:
    """All qualifying opportunities for one expiration + an expiry summary. ``owns_underlying`` = the user
    holds the shares → short calls are COVERED; otherwise they are NAKED (Reg-T margin). ``ta_levels`` =
    {"levels": [S/R, value area, dealer gamma walls/flip], "atr_pct": daily ATR%} that biases multi-leg
    SHORT strikes an ATR-sized buffer beyond a wall (TA-aware selection); None on the plain scan path."""
    calls, puts = _split_chain(chain)
    strikes_all = sorted(set(calls) | set(puts))
    rnd = _build_rnd(calls, puts, strikes_all, spot, dte)
    atm_iv = _atm_iv(rnd, calls, puts, spot)
    iv_hv_ratio, richness = _richness(atm_iv, hv)
    sig_frac = _sigma_frac(atm_iv, hv, dte)              # 1σ move to expiry (max IV/HV · √T) — sizes the structural buffer
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

    # ---- Short calls (OTM) — COVERED if the user holds the shares, else NAKED (Reg-T margin) ----
    if "covered_call" in structures:
        _cc_label = "Covered Call" if owns_underlying else "Naked Call (Reg-T margin)"
        cc = [o for k in call_strikes
              if (o := _single_leg_income("covered_call", _cc_label, calls[k],
                                          sofr_pct=sofr_pct, covered=owns_underlying, **common))]
        # Prioritize SAFER strikes (higher keep-prob) — surface the ladder above min_prob, not just the
        # nearest-money / highest-yield strikes; yield breaks ties.
        opps += sorted(cc, key=lambda o: (o.get("prob_keep_pct") or 0, o.get("premium_annualized_pct") or 0),
                       reverse=True)[:PER_STRUCTURE_CAP]

    # ---- Cash-secured puts (OTM puts) ----
    if "cash_secured_put" in structures:
        cp = [o for k in put_strikes
              if (o := _single_leg_income("cash_secured_put", "Cash-Secured Put", puts[k],
                                          sofr_pct=sofr_pct, **common))]
        # Prioritize SAFER strikes (higher keep-prob) — the full ladder above min_prob; yield breaks ties.
        opps += sorted(cp, key=lambda o: (o.get("prob_keep_pct") or 0, o.get("premium_annualized_pct") or 0),
                       reverse=True)[:PER_STRUCTURE_CAP]

    # ---- Short strangle — neutral, UNDEFINED-RISK income (naked put + call) ----
    if "short_strangle" in structures:
        o = _short_strangle(calls, puts, spot, dte, exp, rnd, r, atm_iv, iv_hv_ratio,
                            richness, min_prob, min_income, european, ticker, ta_levels, sig_frac)
        if o:
            opps.append(o)

    # ---- Defined-risk credit spreads off the headline short strikes ----
    # Scan candidate long wings and keep the most capital-efficient (best ROC) one
    # that still clears the premium floor — the narrowest spread is usually best.
    if "credit_spread" in structures:
        kp_short = _headline_put(rnd, put_strikes, spot, min_prob, ta_levels, sig_frac)
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
        kc_short = _headline_call(rnd, call_strikes, spot, min_prob, ta_levels, sig_frac)
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
                         richness, min_prob, min_income, european, ticker, ta_levels, sig_frac)
        if o:
            opps.append(o)

    # ---- Jade Lizard (short put + call spread, no upside risk) ----
    if "jade_lizard" in structures:
        o = _jade_lizard(calls, puts, spot, dte, exp, rnd, r, atm_iv, iv_hv_ratio,
                         richness, min_prob, min_income, european, ticker, ta_levels, sig_frac)
        if o:
            opps.append(o)

    # Inject the PLACED trade as a candidate at its exact legs (filters off), so the
    # desk scores the trade the user holds even when its strike/expiry is off the grid.
    if focus and focus.get("expiration") == exp:
        fo = _build_focus_opp(focus, calls, puts, common, sofr_pct)
        if fo:
            fss = fo.get("short_strike")
            opps = [o for o in opps if not (o.get("structure") == fo.get("structure")
                                            and o.get("short_strike") == fss)]
            fo["_is_focus"] = True
            opps.append(fo)

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


_SNAP_BAND_PCT = 0.05    # a structural level within 5% of the RND-target strike may bias it (a bounded shift)
# Defense-zone sizing — how much room must separate a structural WALL from the SHORT strike. Sized in ATR
# (volatility-adjusted, the way vol desks size structural zones) AT THE WALL PRICE — the battleground level —
# NOT off spot: a wall far from spot must be judged by ITS OWN local volatility, not where the stock trades now.
# --- Structural BUFFER: place the strike this far BEYOND a wall (cushion so a normal wall-test doesn't breach
#     it), sized off the 1σ EXPECTED MOVE to expiry AT THE WALL — em = wall · max(IV,HV)·√(DTE/365): volatility-
#     adjusted, forward-looking & EVENT-AWARE (IV widens σ before earnings), horizon-scaled = the Q/P boundary.
#     There is deliberately NO 'reach ceiling' — a FAR wall still defends when you sell BEHIND it. A strike with
#     NO wall between it and spot is judged UNDEFENDED by the Structure factor (penalised), never silently ignored.
_BUF_SIGMA_STRONG = 0.20   # 0.20σ beyond a STRONG wall (dealer gamma wall / high-volume node — it holds, sell close)
_BUF_SIGMA_STD    = 0.30   # 0.30σ beyond a STANDARD wall (pivot S/R, value-area edge, gamma flip — more cushion)
_STRONG_WALLS = {"gamma put-wall", "gamma call-wall", "high-volume level"}   # (all others = standard)
_STRUCT_FB_BUF_PCT = 0.010   # no-vol fallback: buffer as a % OF THE WALL price (still wall-anchored)


def _sigma_frac(iv: Optional[float], hv: Optional[float], dte: Optional[int]) -> Optional[float]:
    """1σ expected move TO EXPIRY as a FRACTION of price = max(implied, realized vol)·√(DTE/365) — the desk's
    dual Q/P boundary. Event-aware (IV lifts σ before earnings) + horizon-scaled. iv/hv are DECIMALS."""
    vol = max(iv or 0.0, hv or 0.0)
    return vol * math.sqrt(max(int(dte or 30), 1) / 365.0) if vol > 0 else None


def _wall_buffer(wall: float, sig_frac: Optional[float], strong: bool = False) -> float:
    """The cushion ($) a short strike should sit BEYOND a wall — a fraction of the 1σ expected move to expiry
    AT THE WALL (em = wall·sig_frac): 0.20σ for a STRONG wall (holds → sell close), 0.30σ for a standard one.
    Event-aware + horizon-scaled; %-of-wall fallback when the expected move is unavailable."""
    if sig_frac and sig_frac > 0:
        return (_BUF_SIGMA_STRONG if strong else _BUF_SIGMA_STD) * wall * sig_frac
    return _STRUCT_FB_BUF_PCT * wall


def _snap_short_to_levels(strikes: list[float], target: float, spot: float, side: str,
                          ta_levels: Optional[dict], sig_frac: Optional[float] = None) -> float:
    """Place a multi-leg SHORT strike a VOLATILITY-SIZED BUFFER beyond the nearest structural wall — a short
    put a buffer BELOW a support, a short call a buffer ABOVE a resistance — so breaking the wall doesn't
    breach the strike at once. The buffer is a fraction of the 1σ expected move to expiry AT THE WALL (0.20σ
    for a STRONG wall = gamma wall / high-volume node, 0.30σ for a standard pivot/value-area/flip — see
    `_defense_zone`), so it scales with vol, widens before earnings (IV), and horizon-scales. ``ta_levels`` =
    {"named": [(name, price)], ...}. Safe-side only: the strike stays ≥ as far OTM as the RND target."""
    base = _nearest_strike(strikes, target, side)
    if not base or spot <= 0 or not ta_levels:
        return base
    named = ta_levels.get("named") or []
    band = _SNAP_BAND_PCT * spot
    below = side == "below"
    best = None
    for nm, L in named:
        if not L or (L >= spot if below else L <= spot):   # wall must sit BETWEEN spot and the strike's side
            continue
        buf = _wall_buffer(L, sig_frac, nm in _STRONG_WALLS)
        strike_at = (L - buf) if below else (L + buf)      # a buffer beyond the wall
        safe = (strike_at <= target) if below else (strike_at >= target)   # never less safe than the RND target
        if safe and abs(target - strike_at) <= band:
            better = best is None or (L > best[1] if below else L < best[1])   # wall nearest spot = richest safe strike
            if better:
                best = (strike_at, L)
    if not best:
        return base
    return _nearest_strike(strikes, best[0], "below" if below else "above") or base


def _headline_call(rnd, call_strikes: list[float], spot: float, min_prob: float,
                   ta_levels: Optional[dict] = None, sig_frac: Optional[float] = None) -> Optional[float]:
    """The 85%-safe call strike: read off the RND inverse, snapped up to a listed strike — then biased
    toward a nearby resistance / call-wall on the safe side (TA-aware selection)."""
    if not call_strikes:
        return None
    target = rnd.strike_for_prob_below(min_prob) if rnd is not None else spot * (1 + 0.08)
    return _snap_short_to_levels(call_strikes, max(target, spot), spot, "above", ta_levels, sig_frac)


def _headline_put(rnd, put_strikes: list[float], spot: float, min_prob: float,
                  ta_levels: Optional[dict] = None, sig_frac: Optional[float] = None) -> Optional[float]:
    if not put_strikes:
        return None
    target = rnd.strike_for_prob_below(1 - min_prob) if rnd is not None else spot * (1 - 0.08)
    return _snap_short_to_levels(put_strikes, min(target, spot), spot, "below", ta_levels, sig_frac)


def _roll_credit(opp: dict, spot: float, chains_cache: list) -> Optional[float]:
    """DEFENSIBILITY — the net credit (as a % of the short's own premium) from rolling the primary short leg
    OUT ~one cycle AND one strike further OTM: sell the further-dated / further-OTM option, buy back the
    current short. > 0 → you can roll down-and-out for a CREDIT (defend the strike for free if it's tested);
    < 0 → a protective roll costs a debit. None when the scan holds no later expiry to roll into."""
    legs = opp.get("legs") or []
    shorts = [l for l in legs if str(l.get("action", "")).upper().startswith("S")
              and l.get("strike") and l.get("mid")]
    if not shorts or not chains_cache or not spot:
        return None
    dte0 = int(opp.get("dte") or 0)
    sh = min(shorts, key=lambda l: abs(float(l["strike"]) - spot))       # primary short = nearest spot
    K = float(sh["strike"]); cur_mid = float(sh["mid"])
    if cur_mid <= 0:
        return None
    is_put = str(sh.get("type", "")).upper().startswith("P") or K < spot
    later = [(d, e, c, p) for (d, e, c, p) in chains_cache if d > dte0 + 5]   # a real roll-OUT (≥ ~a week further)
    if not later:
        return None
    _d, _e, calls2, puts2 = min(later, key=lambda t: abs(t[0] - (dte0 + 30)))   # closest to +1 cycle
    book = puts2 if is_put else calls2
    ks = sorted(book.keys())
    further = [k for k in ks if k < K] if is_put else [k for k in ks if k > K]  # one strike further OTM
    Kr = (max(further) if is_put else min(further)) if further else K
    q = book.get(Kr)
    new_mid = float(getattr(q, "mid", 0) or 0) if q is not None else 0.0
    if new_mid <= 0:
        return None
    return round((new_mid - cur_mid) / cur_mid * 100.0, 1)               # roll credit as % of the current premium


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
    target_expiration: Optional[str] = None,
    focus: Optional[dict] = None,
    owns_underlying: bool = False,
    ta_levels: Optional[dict] = None,
) -> dict:
    """Deep-scan one underlying for income opportunities (≥``min_prob`` no-assignment,
    ≥``min_income`` premium), ranked by annualized yield vs SOFR.

    ``focus`` = {structure, expiration, legs:[{strike, right, action}]} injects the
    caller's EXACT placed trade as a candidate (filters off) — for lifecycle scoring.
    ``ta_levels`` = {"levels": [S/R, value area, dealer gamma walls/flip], "atr_pct": daily ATR%} supplied
    by the desk so multi-leg SHORT strikes sit an ATR-sized buffer beyond a wall (TA-aware selection); the
    plain single-scan / portfolio callers pass None → unchanged RND-probability strikes."""
    ticker = _norm_ticker(ticker)
    structures = structures or ["covered_call", "cash_secured_put", "short_strangle",
                                "credit_spread", "iron_condor", "jade_lizard", "calendar"]
    min_prob = min(max(min_prob, 0.5), 0.99)
    today = date.today()

    exp_key = target_expiration or (target_dte if target_dte else "monthly")
    cache_key = (f"derivinc:{ticker}:{exp_key}:"
                 f"{min_prob:.2f}:{int(min_income)}:{','.join(sorted(structures))}:{quote_source}:"
                 f"{'own' if owns_underlying else 'naked'}:"
                 f"{'snap' if (ta_levels and ta_levels.get('named')) else 'plain'}:v3")   # owns → covered call · else naked-call BPR;
                 #                                             snap = multi-leg strikes biased to TA levels
    # A focus trade forces a fresh build (its exact legs aren't in the cached grid).
    if db is not None and focus is None:
        cached = await get_cached(db, cache_key)
        if cached is not None:
            return cached

    # IBKR triage: NO silent yfinance fallback for now — surface the provider's descriptive error verbatim
    # (the IBKR path logs each raw snapshot and raises with the exact payload / likely cause).
    provider = get_provider(quote_source, user=user, db=db)
    data_source_note = None
    try:
        underlying = await provider.get_underlying_price(ticker)
        spot = float(underlying.price or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Quote source %s failed for %s: %s", quote_source, ticker, exc)
        return {"error": f"[{quote_source}] {exc}"}
    if not spot or spot <= 0:
        return {"error": f"[{quote_source}] No valid price for {ticker} (provider returned 0)."}

    try:
        all_exps = await provider.get_option_expirations(ticker)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"No options chain for {ticker}: {exc}"}
    chosen = _select_expirations(all_exps, target_dte, today, target_expiration)
    if not chosen:
        return {"error": f"No expirations in range for {ticker}"}

    sofr_frac, sofr_label = await _get_sofr()
    sofr_pct = sofr_frac * 100.0
    ctx = await asyncio.to_thread(_context_sync, ticker)
    hv = ctx.get("hv30") or ctx.get("hv20")
    european = _is_european(ticker)

    opportunities: list[dict] = []
    expiry_summaries: list[dict] = []
    chains_cache: list = []       # (dte, exp, calls, puts) — kept so the CALENDAR builder can pair two expiries
    for exp, dte in chosen:
        try:
            chain = await provider.get_option_chain(ticker, exp)
        except Exception as exc:  # noqa: BLE001
            logger.debug("chain fetch failed %s %s: %s", ticker, exp, exc)
            continue
        # Decisive triage log: how much of the chain actually carries implied vol. If this shows
        # "N quotes, 0 with IV", the smile can't fit → ATM IV / IV rank / skew all blank (the vol
        # panel goes empty) even though realized-vol metrics (from price history) stay populated.
        _nq = len(chain.quotes or [])
        _niv = sum(1 for q in (chain.quotes or []) if getattr(q, "iv", None))
        logger.info("Chain %s %s via %s: %d quotes, %d with IV", ticker, exp, quote_source, _nq, _niv)
        opps, summary = _scan_expiry(
            chain, spot, dte, exp, today, sofr_frac, sofr_pct, hv, structures,
            european, ctx.get("next_earnings"), min_prob, min_income, ticker,
            focus=focus, owns_underlying=owns_underlying, ta_levels=ta_levels,
        )
        opportunities.extend(opps)
        expiry_summaries.append(summary)
        try:
            _cc, _pp = _split_chain(chain)
            chains_cache.append((dte, exp, _cc, _pp))
        except Exception:  # noqa: BLE001
            pass

    # Calendars span TWO expiries → built here (not in _scan_expiry, which sees one chain) from the cache.
    if "calendar" in structures:
        try:
            opportunities.extend(_scan_calendars(chains_cache, spot, sofr_frac, min_income, hv))
        except Exception as exc:  # noqa: BLE001
            logger.debug("calendar scan failed for %s: %s", ticker, exc)

    # DEFENSIBILITY — can the short be rolled DOWN + OUT for a credit if tested? Needs the multi-expiry cache.
    for o in opportunities:
        try:
            rc = _roll_credit(o, spot, chains_cache)
            if rc is not None:
                o["roll_credit_pct"] = rc
        except Exception:  # noqa: BLE001
            pass

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
        "data_source_note": data_source_note,   # set when a requested source (e.g. IBKR) fell back to yfinance
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
    if db is not None and focus is None:   # never cache a focus-injected (per-trade) build
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
    structures = structures or ["covered_call", "cash_secured_put"]

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


async def get_user_watchlist_tickers(db, user_id: int) -> list[str]:
    from .cache_service import get_cached
    cache_key = f"user:{user_id}:di_watchlist_tickers"
    cached = await get_cached(db, cache_key)
    if cached and "tickers" in cached:
        return cached["tickers"]
    return ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD", "NFLX"]

async def set_user_watchlist_tickers(db, user_id: int, tickers: list[str]):
    from .cache_service import set_cached
    cache_key = f"user:{user_id}:di_watchlist_tickers"
    await set_cached(db, cache_key, {"tickers": tickers}, ttl_seconds=315360000)

async def get_derivative_income_watchlist(db, user_id: int, refresh: bool = False) -> list[dict]:
    import asyncio
    import yfinance as yf
    from .cache_service import get_cached, set_cached
    
    tickers = await get_user_watchlist_tickers(db, user_id)
    if not tickers:
        return []
        
    final_items = []
    missing_tickers = []
    
    if not refresh:
        for t in tickers:
            cached = await get_cached(db, f"di_watchlist_metric:{t}")
            if cached:
                final_items.append(cached)
            else:
                missing_tickers.append(t)
    else:
        missing_tickers = tickers
        
    if not missing_tickers:
        return final_items
        
    def _fetch_item(t: str):
        try:
            stock = yf.Ticker(t)
            hist = stock.history(period="1y")
            if hist is None or hist.empty: return None
            closes = hist["Close"].dropna()
            if len(closes) < 30: return None
            
            price = round(float(closes.iloc[-1]), 2)
            prev = round(float(closes.iloc[-2]), 2)
            pct = round((price - prev) / prev * 100, 2)
            
            ctx = _context_sync(t)
            
            atm_iv = None
            exps = stock.options
            if exps:
                chain = stock.option_chain(exps[0])
                calls = chain.calls
                if not calls.empty:
                    idx = (abs(calls["strike"] - price)).idxmin()
                    atm_iv = round(float(calls.loc[idx]["impliedVolatility"]) * 100, 1)
                    
            return {
                "ticker": t,
                "current_price": price,
                "today_pct": pct,
                "week52_low": ctx.get("week52_low"),
                "week52_high": ctx.get("week52_high"),
                "atm_iv": atm_iv,
                "hv30": round(ctx["hv30"] * 100, 1) if ctx.get("hv30") else None
            }
        except Exception:
            return None

    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, _fetch_item, t) for t in missing_tickers]
    results = await asyncio.gather(*tasks)
    
    for r in results:
        if r is not None:
            final_items.append(r)
            await set_cached(db, f"di_watchlist_metric:{r['ticker']}", r, ttl_seconds=86400 * 7)
            
    order_map = {t: i for i, t in enumerate(tickers)}
    final_items.sort(key=lambda x: order_map.get(x["ticker"], 999))
    
    return final_items
