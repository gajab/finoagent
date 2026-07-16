"""Data adapter for the valuation engine — the ONE place that touches yfinance.

Packs a ticker's income-statement history + market data into the pure engine's inputs:
an anchored base P&L, a fitted operating-leverage curve, the current price, a
margin-quality signal (operating margin) for the warranted-multiple tilt, and the
trailing P/E that caps the bull multiple. Kept separate so ``valuation_engine`` stays
pure and unit-testable; this module is the impure edge.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

import yfinance as yf

from .valuation_engine import (
    OperatingLeverage, PnL, base_eps, estimate_operating_leverage, pnl_anchored,
)


@dataclass
class ValuationInputs:
    ticker: str
    base: PnL
    leverage: OperatingLeverage
    price: float
    margin_quality: float          # operating margin (fraction) — warranted-multiple tilt
    own_hist_pe: float | None      # trailing P/E — caps the bull multiple
    trailing_growth: float         # latest YoY revenue growth (context)
    base_eps: float


def _clean(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _series(df, *keys) -> list[float]:
    """Oldest→newest values for the first matching row (yfinance columns are newest-first)."""
    if df is None or getattr(df, "empty", True):
        return []
    for k in keys:
        if k in df.index:
            vals = [v for v in (_clean(x) for x in df.loc[k].values) if v is not None]
            if vals:
                return vals[::-1]
    return []


def _fetch_sync(ticker: str) -> ValuationInputs:
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    inc = tk.income_stmt

    rev_h = _series(inc, "Total Revenue", "TotalRevenue")
    op_h = _series(inc, "Operating Income", "OperatingIncome", "EBIT")
    if not rev_h or not op_h:
        raise ValueError(f"no income-statement history for {ticker}")

    latest_rev, latest_op = rev_h[-1], op_h[-1]
    pre_h = _series(inc, "Pretax Income", "PretaxIncome", "Income Before Tax")
    tax_h = _series(inc, "Tax Provision", "Income Tax Expense")
    ni_h = _series(inc, "Net Income", "NetIncome", "Net Income Common Stockholders")

    latest_pre = pre_h[-1] if pre_h else latest_op
    tax_rate = 0.21
    if tax_h and latest_pre:
        tr = tax_h[-1] / latest_pre
        if 0.0 <= tr <= 0.5:                       # ignore benefits / distorted years
            tax_rate = tr
    latest_ni = ni_h[-1] if ni_h else latest_pre * (1 - tax_rate)

    shares = _clean(info.get("sharesOutstanding")) or 0.0
    price = _clean(info.get("currentPrice")) or _clean(info.get("regularMarketPrice")) or 0.0
    own_hist_pe = _clean(info.get("trailingPE"))

    lev = estimate_operating_leverage(rev_h, op_h)
    base = pnl_anchored(latest_rev, latest_op, tax_rate, shares, latest_ni)
    margin_quality = (latest_op / latest_rev) if latest_rev else 0.0
    trailing_growth = (rev_h[-1] / rev_h[-2] - 1) if (len(rev_h) >= 2 and rev_h[-2]) else 0.0

    return ValuationInputs(
        ticker=ticker.upper(), base=base, leverage=lev, price=price,
        margin_quality=round(margin_quality, 4), own_hist_pe=own_hist_pe,
        trailing_growth=round(trailing_growth, 4), base_eps=base_eps(base, lev),
    )


async def fetch_valuation_inputs(ticker: str) -> ValuationInputs:
    """Async wrapper — yfinance is blocking, so run it off the event loop."""
    return await asyncio.to_thread(_fetch_sync, ticker)
