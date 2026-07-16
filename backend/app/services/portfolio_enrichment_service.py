"""
Portfolio enrichment service — dividend, fundamental, and technical data.

All three fetch functions are cached per ticker in the DB cache:
  portfolio:dividends:{ticker}  — TTL 24h (rarely changes intraday)
  portfolio:fundamentals:{ticker} — TTL 24h
  portfolio:technical:{ticker}   — TTL 3600s (analyst targets update daily)

Pass force_refresh=True to bypass the cache for a specific ticker.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
import time as _time
from typing import Optional

import yfinance as yf
from sqlalchemy.ext.asyncio import AsyncSession

from .cache_service import get_cached, set_cached, invalidate
from .rate_limit_service import yf_guard, YFinanceBlocked, note_failure, note_success

logger = logging.getLogger(__name__)

_TTL_DIVIDEND = 86400       # 24 h
_TTL_FUNDAMENTAL = 86400    # 24 h
_TTL_TECHNICAL = 3600       # 1 h
_TTL_PRICE = 900            # 15 min — live quotes; user clicks Refresh for fresher


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _epoch_to_date(ts) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def _pct(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v) * 100, 2)
    except Exception:
        return None


def _safe_pct(v, threshold: float = 1.5) -> Optional[float]:
    """Like _pct() but guards against yfinance inconsistency where some tickers
    return fund returns/yields as an already-in-percent value (e.g. 8.5 for 8.5%)
    instead of a decimal ratio (0.085).  If |v| > threshold, we assume it is
    already a percentage and return it as-is; otherwise multiply by 100.

    Typical thresholds:
      1.5  — for annual returns (ytd / 3yr / 5yr): rarely > ±150 % as a decimal
      1.0  — for distribution yields: rarely > 100 % as a decimal
    """
    if v is None:
        return None
    try:
        fv = float(v)
        if abs(fv) > threshold:
            return round(fv, 2)       # already expressed as a percentage
        return round(fv * 100, 2)     # decimal ratio → convert to %
    except Exception:
        return None


def _f(v, decimals=2) -> Optional[float]:
    if v is None:
        return None
    try:
        return round(float(v), decimals)
    except Exception:
        return None


def _fmt_large(v) -> Optional[float]:
    """Store large numbers (revenue, FCF) in billions rounded to 3dp."""
    if v is None:
        return None
    try:
        return round(float(v) / 1_000_000_000, 3)
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Single-ticker extractors (synchronous — run in thread)
# ──────────────────────────────────────────────────────────────────────────────

def _dividend_yield_decimal(info: dict) -> Optional[float]:
    """Return the dividend / distribution yield as a DECIMAL ratio (0.0261 = 2.61%).

    yfinance field semantics (verified 2026 — they are NOT consistent):
      yield                        → decimal ratio, fund / ETF only   (VEA 0.0261)
      trailingAnnualDividendYield  → decimal ratio, equities          (KO  0.0253)
      dividendYield                → ALREADY A PERCENT, stocks & funds (KO  2.61, VEA 2.61)

    We prefer the unambiguous decimal fields first; dividendYield is only a
    last resort and must be divided by 100. Returning a single normalised
    decimal lets every caller multiply by 100 exactly once.
    """
    y = info.get("yield")
    if y:
        try: return float(y)
        except (TypeError, ValueError): pass
    tady = info.get("trailingAnnualDividendYield")
    if tady:
        try: return float(tady)
        except (TypeError, ValueError): pass
    dy = info.get("dividendYield")
    if dy:
        try: return float(dy) / 100.0   # dividendYield is in percent form
        except (TypeError, ValueError): pass
    return None


def _extract_dividend(info: dict, ticker: str) -> dict:
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    yld = _dividend_yield_decimal(info)

    # Annual $/share rate. Equities expose dividendRate directly; most ETFs
    # leave it empty, so derive it from the (decimal) yield × price instead —
    # otherwise funds show $0 income and register as non-payers.
    rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
    if not rate and yld and price:
        try: rate = yld * float(price)
        except (TypeError, ValueError): rate = None

    return {
        "ticker": ticker,
        "sector": info.get("sector") or "",
        "company_name": info.get("longName") or info.get("shortName") or ticker,
        "dividend_yield_pct": round(yld * 100, 2) if yld is not None else None,
        "annual_dividend_rate": _f(rate),
        "last_dividend_per_share": _f(info.get("lastDividendValue")),
        "ex_dividend_date": _epoch_to_date(info.get("exDividendDate") or info.get("lastDividendDate")),
        "payout_ratio_pct": _pct(info.get("payoutRatio")),
        "five_yr_avg_yield_pct": _f(info.get("fiveYearAvgDividendYield")),  # already a percent
        "is_dividend_payer": bool((yld and yld > 0) or (rate and rate > 0)),
    }


def _extract_fundamental(info: dict, ticker: str) -> dict:
    # ── turnover: yfinance returns a ratio (0.4 = 40 %) ──────────────────────
    tr = info.get("turnoverRatio")
    turnover_pct: Optional[float] = round(float(tr) * 100, 1) if tr is not None else None

    return {
        "ticker": ticker,
        "company_name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "quote_type": info.get("quoteType", ""),
        # ── Equity metrics ───────────────────────────────────────────────────
        "trailing_pe": _f(info.get("trailingPE")),
        "forward_pe": _f(info.get("forwardPE")),
        "peg_ratio": _f(info.get("pegRatio")),
        "eps_ttm": _f(info.get("trailingEps") or info.get("epsTrailingTwelveMonths")),
        "eps_forward": _f(info.get("forwardEps") or info.get("epsForward")),
        "earnings_growth_yoy_pct": _pct(info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")),
        "revenue_growth_yoy_pct": _pct(info.get("revenueGrowth")),
        "revenue_b": _fmt_large(info.get("totalRevenue")),
        "free_cashflow_b": _fmt_large(info.get("freeCashflow")),
        "operating_margin_pct": _pct(info.get("operatingMargins")),
        "net_margin_pct": _pct(info.get("profitMargins")),
        "ebitda_margin_pct": _pct(info.get("ebitdaMargins")),
        "roe_pct": _pct(info.get("returnOnEquity")),
        "debt_to_equity": _f(info.get("debtToEquity")),
        "price_to_book": _f(info.get("priceToBook")),
        "market_cap_b": _fmt_large(info.get("marketCap")),
        # ── Fund / ETF metrics (null for pure equities) ───────────────────────
        "aum_b": _fmt_large(info.get("totalAssets") or info.get("netAssets")),
        # yfinance is inconsistent about whether yield / returns are decimal ratios
        # (0.02 = 2 %) or already-in-percent form (2.0 = 2 %). Use _safe_pct with
        # conservative thresholds to handle both cases.
        "fund_yield_pct": _safe_pct(info.get("yield") or info.get("trailingAnnualDividendYield"), threshold=1.0),
        "ytd_return_pct": _safe_pct(info.get("ytdReturn"), threshold=1.5),
        "three_yr_return_pct": _safe_pct(info.get("threeYearAverageReturn"), threshold=1.5),
        "five_yr_return_pct": _safe_pct(info.get("fiveYearAverageReturn"), threshold=1.5),
        "fund_turnover_pct": turnover_pct,
        "fund_category": info.get("category") or "",
        "fund_family": info.get("fundFamily") or "",
        "net_expense_ratio_pct": _pct(
            info.get("annualReportExpenseRatio") or info.get("totalExpenseRatio")
        ),
        "morningstar_risk_rating": info.get("morningStarRiskRating"),
        "morningstar_overall_rating": info.get("morningStarOverallRating"),
        "fund_inception_date": _epoch_to_date(info.get("fundInceptionDate")),
    }


def _extract_technical(info: dict, ticker: str) -> dict:
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    ma50 = info.get("fiftyDayAverage")
    ma200 = info.get("twoHundredDayAverage")
    hi52 = info.get("fiftyTwoWeekHigh")
    lo52 = info.get("fiftyTwoWeekLow")
    target = info.get("targetMeanPrice") or info.get("targetMedianPrice")

    vs_50d = round((float(price) / float(ma50) - 1) * 100, 2) if price and ma50 else None
    vs_200d = round((float(price) / float(ma200) - 1) * 100, 2) if price and ma200 else None
    upside = round((float(target) / float(price) - 1) * 100, 2) if target and price else None
    range_pct = (
        round((float(price) - float(lo52)) / (float(hi52) - float(lo52)) * 100, 1)
        if price and hi52 and lo52 and float(hi52) != float(lo52) else None
    )

    return {
        "ticker": ticker,
        "sector": info.get("sector") or "",
        "company_name": info.get("longName") or info.get("shortName") or ticker,
        "beta": _f(info.get("beta")),
        "ma_50d": _f(ma50),
        "ma_200d": _f(ma200),
        "vs_50d_pct": vs_50d,
        "vs_200d_pct": vs_200d,
        "week52_high": _f(hi52),
        "week52_low": _f(lo52),
        "week52_change_pct": _pct(info.get("52WeekChange") or info.get("fiftyTwoWeekChangePercent")),
        "range_position_pct": range_pct,
        "analyst_target_mean": _f(target),
        "analyst_target_high": _f(info.get("targetHighPrice")),
        "analyst_target_low": _f(info.get("targetLowPrice")),
        "analyst_upside_pct": upside,
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "recommendation": info.get("recommendationKey", ""),
        "recommendation_mean": _f(info.get("recommendationMean")),
        "avg_volume_3m": info.get("averageVolume"),
        "avg_volume_10d": info.get("averageDailyVolume10Day"),
        # Fund performance columns — same safe conversion as in fundamentals
        "ytd_return_pct": _safe_pct(info.get("ytdReturn"), threshold=1.5),
        "three_yr_return_pct": _safe_pct(info.get("threeYearAverageReturn"), threshold=1.5),
        "five_yr_return_pct": _safe_pct(info.get("fiveYearAverageReturn"), threshold=1.5),
        "quote_type": info.get("quoteType", ""),
        # Next earnings announcement date (only future / very recent dates retained)
        "earnings_date": _next_earnings_date(info),
    }


def _next_earnings_date(info: dict) -> Optional[str]:
    """Return the next announced earnings date as YYYY-MM-DD, or None."""
    raw = info.get("earningsDate") or info.get("earningsTimestamp")
    if raw is None:
        return None
    try:
        import time as _time
        # Accept dates up to 1 day in the past (market-hour ambiguity)
        floor_ts = _time.time() - 86_400
        if isinstance(raw, (list, tuple)):
            future = sorted([t for t in raw if isinstance(t, (int, float)) and t > floor_ts])
            return _epoch_to_date(future[0]) if future else None
        elif isinstance(raw, (int, float)):
            return _epoch_to_date(raw) if raw > floor_ts else None
    except Exception:
        pass
    return None


# `.info` is a per-ticker scrape that can't be batched, so we pace it: a small
# jittered gap between calls keeps even a cold 300-ticker enrichment from
# bursting. In normal operation the background warmer keeps these caches hot, so
# this serial path runs rarely and only for the handful of cache-cold tickers.
_ENRICH_PACE_SECONDS = 0.08

_EXTRACTORS = {
    "dividends": _extract_dividend,
    "fundamentals": _extract_fundamental,
    "technical": _extract_technical,
}


def _fetch_enrichment_sync(tickers: list[str], view: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    extractor = _EXTRACTORS.get(view)
    for i, ticker in enumerate(tickers):
        if i:
            _time.sleep(_ENRICH_PACE_SECONDS + random.random() * _ENRICH_PACE_SECONDS)
        try:
            info = yf.Ticker(ticker).info or {}
            note_success()
            result[ticker] = extractor(info, ticker) if extractor else {"ticker": ticker}
        except Exception as exc:
            logger.warning("Enrichment fetch failed for %s (%s): %s", ticker, view, exc)
            note_failure(exc)
            result[ticker] = {"ticker": ticker, "error": str(exc)}
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Public async batch fetchers
# ──────────────────────────────────────────────────────────────────────────────

async def fetch_enriched_view(
    db: AsyncSession,
    tickers: list[str],
    view: str,           # "dividends" | "fundamentals" | "technical"
    force_refresh: bool = False,
) -> dict[str, dict]:
    """
    Fetch enrichment data for a list of tickers.
    Checks DB cache first; fetches missing/expired entries from yfinance.
    """
    ttl_map = {"dividends": _TTL_DIVIDEND, "fundamentals": _TTL_FUNDAMENTAL, "technical": _TTL_TECHNICAL}
    ttl = ttl_map.get(view, 3600)

    cached_results: dict[str, dict] = {}
    missing: list[str] = []

    for ticker in tickers:
        key = f"portfolio:{view}:{ticker}"
        if force_refresh:
            await invalidate(db, key)

        hit = await get_cached(db, key)
        if hit:
            cached_results[ticker] = hit
        else:
            missing.append(ticker)

    if missing:
        try:
            async with yf_guard():
                fresh = await asyncio.to_thread(_fetch_enrichment_sync, missing, view)
        except YFinanceBlocked:
            logger.warning("Enrichment '%s' skipped for %d tickers — breaker open; serving cache-only",
                           view, len(missing))
            fresh = {}
        for ticker, data in fresh.items():
            if "error" not in data:
                key = f"portfolio:{view}:{ticker}"
                await set_cached(db, key, data, ttl_seconds=ttl)
        cached_results.update(fresh)

    return cached_results


async def fetch_prices_cached(
    db: AsyncSession,
    tickers: list[str],
    force_refresh: bool = False,
) -> dict[str, dict]:
    """
    Cached price + day-change fetch (15-min TTL per ticker).

    Every portfolio endpoint that needs live prices (/enhanced, /dividends,
    /copilot) should go through here instead of calling yfinance directly —
    a normal page load then serves prices from the DB cache rather than firing
    one heavy yfinance `.info` request per ticker on every request. Pass
    force_refresh=True (wired to the user's "Refresh" action) to bypass it.

    Keys in the returned dict are upper-cased tickers, matching how holdings
    are stored.
    """
    # Imported lazily to avoid any import-order coupling with portfolio_service.
    from .portfolio_service import _fetch_price_data_sync

    result: dict[str, dict] = {}
    missing: list[str] = []

    for t in tickers:
        tu = t.upper()
        key = f"portfolio:price:{tu}"
        if force_refresh:
            await invalidate(db, key)
        hit = await get_cached(db, key)
        if hit:
            result[tu] = hit
        else:
            missing.append(tu)

    if missing:
        try:
            async with yf_guard():
                fresh = await asyncio.to_thread(_fetch_price_data_sync, missing)
        except YFinanceBlocked:
            logger.warning("Price fetch skipped for %d tickers — breaker open; serving cache-only",
                           len(missing))
            fresh = {}
        for tu, data in fresh.items():
            # Only cache successful quotes; transient failures stay uncached so
            # the next request retries instead of serving a stale None for 15m.
            if data.get("price") is not None:
                await set_cached(db, f"portfolio:price:{tu}", data, ttl_seconds=_TTL_PRICE)
            result[tu] = data

    return result


async def invalidate_prices(db: AsyncSession, tickers: list[str]) -> None:
    """Bust the price cache for the given tickers."""
    for t in tickers:
        await invalidate(db, f"portfolio:price:{t.upper()}")


async def invalidate_ticker(db: AsyncSession, ticker: str) -> None:
    """Bust all enrichment caches (and price) for one ticker."""
    for view in ("dividends", "fundamentals", "technical"):
        await invalidate(db, f"portfolio:{view}:{ticker}")
    await invalidate(db, f"portfolio:price:{ticker.upper()}")


async def invalidate_all(db: AsyncSession, tickers: list[str]) -> None:
    """Bust all enrichment caches for all portfolio tickers."""
    for ticker in tickers:
        await invalidate_ticker(db, ticker)
