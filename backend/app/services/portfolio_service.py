"""Portfolio service — price fetching and position computation."""

import asyncio
import datetime
import logging
import random
import time as _time
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from .rate_limit_service import note_failure, note_success

logger = logging.getLogger(__name__)

# Symbols per batched download. Keeping each request to ~50 names means one
# request returns light daily bars for the whole chunk instead of a 1 MB
# `.info` scrape per ticker — ~50× fewer requests and ~99% less data, which is
# what keeps a 300-holding portfolio from tripping Yahoo's per-IP throttle.
_PRICE_CHUNK = 50

_NULL_QUOTE = {"price": None, "previous_close": None, "day_change": None, "day_change_pct": None}


# ---------------------------------------------------------------------------
# Price / quote data
# ---------------------------------------------------------------------------

def _parse_close_frame(data, tickers: list[str]) -> dict[str, pd.Series]:
    """Defensively pull a {ticker: close_series} map out of a yf.download frame.

    Handles both the single-ticker (flat columns) and multi-ticker (MultiIndex
    (field, ticker)) shapes that yfinance returns, mirroring the house pattern
    used by the TLH / liquidity services.
    """
    out: dict[str, pd.Series] = {}
    if data is None or getattr(data, "empty", True):
        return out
    cols = data.columns
    if isinstance(cols, pd.MultiIndex):
        if "Close" not in cols.get_level_values(0):
            return out
        close = data["Close"]
        if isinstance(close, pd.Series):  # single ticker promoted to a Series
            out[tickers[0]] = close.dropna()
            return out
        for t in tickers:
            if t in close.columns:
                out[t] = close[t].dropna()
    else:
        if "Close" not in data:
            return out
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            for t in tickers:
                if t in close.columns:
                    out[t] = close[t].dropna()
        else:
            # Flat single-ticker frame.
            out[tickers[0]] = close.dropna()
    return out


def _quote_from_close(close: pd.Series) -> dict:
    """Turn a daily close series into the price/day-change quote dict."""
    s = close.dropna() if close is not None else pd.Series(dtype=float)
    if s.empty:
        return dict(_NULL_QUOTE)
    price = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) >= 2 else None
    day_change = (price - prev) if prev is not None else None
    day_change_pct = ((price - prev) / prev * 100) if (prev not in (None, 0)) else None
    return {
        "price": price,
        "previous_close": prev,
        "day_change": day_change,
        "day_change_pct": day_change_pct,
    }


def _fetch_price_chunk_sync(chunk: list[str]) -> Dict[str, dict]:
    """One batched daily-bar download for up to _PRICE_CHUNK symbols."""
    try:
        data = yf.download(
            " ".join(chunk), period="5d", interval="1d",
            progress=False, auto_adjust=False, group_by="column", threads=True,
        )
        note_success()
    except Exception as exc:  # network / parse — fall through to fallback
        logger.warning("Batched price download failed for %d tickers: %s", len(chunk), exc)
        note_failure(exc)
        data = None

    closes = _parse_close_frame(data, chunk)
    out: Dict[str, dict] = {}
    for t in chunk:
        q = _quote_from_close(closes.get(t))
        if q["price"] is not None:
            out[t.upper()] = q
    return out


def _fast_info_quote_sync(ticker: str) -> dict:
    """Light per-ticker fallback for names the batch download missed (some funds)."""
    try:
        fi = yf.Ticker(ticker).fast_info
        price = getattr(fi, "last_price", None)
        prev = getattr(fi, "previous_close", None)
        price = float(price) if price else None
        prev = float(prev) if prev else None
        if price is None:
            return dict(_NULL_QUOTE)
        day_change = (price - prev) if prev is not None else None
        day_change_pct = ((price - prev) / prev * 100) if (prev not in (None, 0)) else None
        return {"price": price, "previous_close": prev,
                "day_change": day_change, "day_change_pct": day_change_pct}
    except Exception:
        return dict(_NULL_QUOTE)


def _fetch_price_data_sync(tickers: list[str]) -> Dict[str, dict]:
    """Batched price + day-change fetch.

    Chunks the symbols into ~50-name `yf.download` calls (light daily bars,
    threaded) with gentle pacing between chunks, then falls back to per-ticker
    `fast_info` only for the handful the batch couldn't resolve. Returns the
    same {TICKER: {price, previous_close, day_change, day_change_pct}} shape as
    before, with an explicit null-quote entry for every requested ticker.
    """
    uniq = list(dict.fromkeys(t.upper() for t in tickers))
    result: Dict[str, dict] = {}

    for i in range(0, len(uniq), _PRICE_CHUNK):
        chunk = uniq[i:i + _PRICE_CHUNK]
        result.update(_fetch_price_chunk_sync(chunk))
        if i + _PRICE_CHUNK < len(uniq):
            _time.sleep(0.3 + random.random() * 0.4)  # jittered pacing between chunks

    # Fallback + guarantee an entry for every requested ticker.
    for t in uniq:
        if t not in result:
            result[t] = _fast_info_quote_sync(t)
    return result


async def fetch_price_data(tickers: list[str]) -> Dict[str, dict]:
    """Async wrapper for price fetching."""
    if not tickers:
        return {}
    unique = list(set(t.upper() for t in tickers))
    return await asyncio.to_thread(_fetch_price_data_sync, unique)


# Keep old function for backward compat
async def fetch_current_prices(tickers: list[str]) -> Dict[str, Optional[float]]:
    data = await fetch_price_data(tickers)
    return {t: v["price"] for t, v in data.items()}


# ---------------------------------------------------------------------------
# Position computation from transactions
# ---------------------------------------------------------------------------

def compute_position_from_transactions(transactions: list) -> dict:
    """
    Recompute a position's aggregate state from its transaction list.
    Uses weighted-average cost basis (WACB) method.
    Returns dict suitable for updating a PortfolioHolding.
    """
    sorted_txns = sorted(transactions, key=lambda t: (t.date, t.id))

    net_shares = 0.0
    total_cost = 0.0          # cost of current remaining shares
    total_invested = 0.0       # cumulative dollars invested (all buys)
    realized_gain_loss = 0.0
    first_buy_date: Optional[datetime.date] = None
    asset_type = "STOCK"

    for t in sorted_txns:
        asset_type = getattr(t, "asset_type", asset_type) or asset_type

        if t.transaction_type in ("BUY", "OPTION_BUY", "TRANSFER_IN"):
            cost = t.shares * t.price_per_share + (t.fees or 0)
            net_shares += t.shares
            total_cost += cost
            total_invested += cost
            if first_buy_date is None:
                first_buy_date = t.date

        elif t.transaction_type in ("SELL", "OPTION_SELL", "TRANSFER_OUT"):
            avg_cost = total_cost / net_shares if net_shares > 0 else 0.0
            sell_proceeds = t.shares * t.price_per_share - (t.fees or 0)
            realized_gain_loss += sell_proceeds - t.shares * avg_cost
            total_cost -= t.shares * avg_cost
            net_shares -= t.shares

    net_shares = max(0.0, net_shares)
    total_cost = max(0.0, total_cost)
    avg_cost_basis = total_cost / net_shares if net_shares > 0 else 0.0

    return {
        "shares": round(net_shares, 8),
        "cost_basis": round(avg_cost_basis, 6),
        "realized_gain_loss": round(realized_gain_loss, 4),
        "total_invested": round(total_invested, 4),
        "first_buy_date": first_buy_date,
        "asset_type": asset_type,
    }


# ---------------------------------------------------------------------------
# Annualised return
# ---------------------------------------------------------------------------

def compute_annualized_return(
    total_invested: Optional[float],
    current_value: float,
    realized_gain_loss: float,
    first_buy_date: Optional[datetime.date],
) -> Optional[float]:
    """
    Compute annualised total return (%).
    total_invested  — cumulative dollars put in (all buys including sold lots)
    current_value   — market value of remaining shares today
    realized_gain_loss — net realized P&L from all sells
    Returns None if insufficient data.
    """
    if not total_invested or total_invested <= 0 or first_buy_date is None:
        return None

    days_held = (datetime.date.today() - first_buy_date).days
    if days_held <= 0:
        return None

    # Don't annualise very short holding periods. Compounding a few weeks of
    # return out to a full year produces absurd, misleading figures (e.g. a
    # position up 10% over one month annualises to ~200%). Below 90 days there
    # simply isn't enough history for an annualised number to be meaningful.
    if days_held < 90:
        return None

    total_return = (current_value + realized_gain_loss) / total_invested - 1
    years = days_held / 365.25

    if 1 + total_return <= 0:
        return None

    annualized = ((1 + total_return) ** (1.0 / years) - 1) * 100
    return round(annualized, 2)
