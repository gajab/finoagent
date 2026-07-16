"""Macro rates / spreads service — keyless FRED access.

Fetches FRED time-series via the public, **no-API-key** CSV endpoint
``https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>`` and exposes
small statistical helpers (latest value, z-score, percentile, change) used by
the Debt Radar entry-timing engine.

FRED publishes most series once per (business) day, so series are memoised in
process for a few hours to avoid hammering the endpoint when many tickers are
scored.  The per-ticker composed payload is additionally DB-cached at the router
layer (see ``debt_entry_router``).

All functions degrade gracefully: a failed fetch returns an empty series and the
callers treat missing data as "signal unavailable" rather than raising.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_MEMO_TTL = 6 * 3600  # 6h — FRED daily data only changes once/day
_memo: dict[str, tuple[float, list[dict]]] = {}

# Series id catalogue (documented here so the engine reads cleanly) -----------
SERIES = {
    # short rates / curve
    "sofr": "SOFR",
    "t1y": "DGS1",
    "t2y": "DGS2",
    "t3mo": "DGS3MO",
    "t10y": "DGS10",
    "curve_10y2y": "T10Y2Y",
    "curve_10y3m": "T10Y3M",
    # real yield / inflation
    "real_10y": "DFII10",
    "breakeven_10y": "T10YIE",
    # option-adjusted spreads (ICE BofA)
    "oas_aaa": "BAMLC0A1CAAA",
    "oas_aa": "BAMLC0A2CAA",
    "oas_bbb": "BAMLC0A4CBBB",
    "oas_corp": "BAMLC0A0CM",
    "oas_hy": "BAMLH0A0HYM2",
    # macro / cycle
    "nfci": "NFCI",
    "anfci": "ANFCI",
    "sahm": "SAHMREALTIME",
    "delinquency_business": "DRBLACBS",
    "vix": "VIXCLS",
}


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
async def fred_series(series_id: str) -> list[dict]:
    """Return the full history of a FRED series as ``[{date, value}, ...]``.

    ``date`` is an ISO ``YYYY-MM-DD`` string, ``value`` a float.  Missing
    observations (FRED encodes them as ``.``) are skipped.  Memoised ~6h.
    Returns ``[]`` on any failure.
    """
    now = time.monotonic()
    hit = _memo.get(series_id)
    if hit and hit[0] > now:
        return hit[1]

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(_FRED_CSV, params={"id": series_id})
            resp.raise_for_status()
        rows = list(csv.reader(io.StringIO(resp.text)))
    except Exception as exc:
        logger.warning("FRED fetch failed for %s: %s", series_id, exc)
        # Serve stale memo if present, else empty
        return hit[1] if hit else []

    out: list[dict] = []
    for row in rows[1:]:  # skip header
        if len(row) != 2:
            continue
        d, v = row[0].strip(), row[1].strip()
        if v in (".", ""):
            continue
        try:
            out.append({"date": d, "value": float(v)})
        except ValueError:
            continue

    _memo[series_id] = (now + _MEMO_TTL, out)
    return out


async def fred_many(series_ids: list[str]) -> dict[str, list[dict]]:
    """Fetch several series concurrently → ``{series_id: history}``."""
    results = await asyncio.gather(*(fred_series(s) for s in series_ids))
    return dict(zip(series_ids, results))


# ---------------------------------------------------------------------------
# Statistics over a series
# ---------------------------------------------------------------------------
def _values(series: list[dict]) -> list[float]:
    return [p["value"] for p in series]


def _window(series: list[dict], days: int | None) -> list[dict]:
    """Trailing window of ``days`` calendar days ending at the last observation."""
    if not series or not days:
        return series
    try:
        last = date.fromisoformat(series[-1]["date"])
    except ValueError:
        return series
    cutoff = last - timedelta(days=days)
    return [p for p in series if p["date"] >= cutoff.isoformat()]


def latest(series: list[dict]) -> float | None:
    """Most recent value, or ``None`` if the series is empty."""
    return series[-1]["value"] if series else None


def latest_point(series: list[dict]) -> dict | None:
    return series[-1] if series else None


def mean(series: list[dict], window_days: int | None = None) -> float | None:
    vals = _values(_window(series, window_days))
    return sum(vals) / len(vals) if vals else None


def zscore(series: list[dict], window_days: int | None = None) -> float | None:
    """Z-score of the latest value vs the trailing window (population stdev)."""
    vals = _values(_window(series, window_days))
    if len(vals) < 8:
        return None
    mu = sum(vals) / len(vals)
    var = sum((v - mu) ** 2 for v in vals) / len(vals)
    sd = var ** 0.5
    if sd == 0:
        return 0.0
    return (vals[-1] - mu) / sd


def percentile(series: list[dict], window_days: int | None = None) -> float | None:
    """Percentile rank (0–100) of the latest value within the trailing window."""
    vals = _values(_window(series, window_days))
    if len(vals) < 8:
        return None
    last = vals[-1]
    below = sum(1 for v in vals if v <= last)
    return round(100.0 * below / len(vals), 1)


def change(series: list[dict], window_days: int) -> float | None:
    """Change in value over the trailing window (latest − value ~window ago)."""
    win = _window(series, window_days)
    if len(win) < 2:
        return None
    return win[-1]["value"] - win[0]["value"]


def observations(series: list[dict]) -> int:
    return len(series)
