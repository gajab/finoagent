"""Technical analysis for the top sectors favored in the current US business cycle phase.

Given a phase (early/mid/late/recession), picks the top 3 asset categories from the
performance matrix, maps each to a representative sector ETF, fetches 400 days of
daily OHLCV data, and returns full TA indicators + chart series.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from .business_cycle_service import ASSET_MATRIX

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category → sector ETF mapping
# ---------------------------------------------------------------------------

CATEGORY_ETF: dict[str, str] = {
    "Large Growth":            "QQQ",
    "Technology":              "XLK",
    "Industrials":             "XLI",
    "Healthcare":              "XLV",
    "Mid Cap":                 "IJH",
    "International Developed": "EFA",
    "Energy":                  "XLE",
    "Small Cap":               "IWM",
    "Consumer Discretionary":  "XLY",
    "Financials":              "XLF",
    "Real Estate (REITs)":     "VNQ",
    "High Yield Bonds":        "HYG",
    "Consumer Staples":        "XLP",
    "Utilities":               "XLU",
    "Large Value":             "IWD",
    "Investment Grade Bonds":  "LQD",
    "Materials & Commodities": "XLB",
    "Treasuries (Long)":       "TLT",
    "Gold":                    "GLD",
    "Large Cap Blend":         "SPY",
    "Cyclicals (Energy/Mats)": "XLE",
}


# ---------------------------------------------------------------------------
# TA helpers
# ---------------------------------------------------------------------------

def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l = loss.ewm(com=period - 1, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(closes: pd.Series, fast=12, slow=26, signal=9):
    ema_f  = closes.ewm(span=fast,   adjust=False).mean()
    ema_s  = closes.ewm(span=slow,   adjust=False).mean()
    macd_l = ema_f - ema_s
    sig_l  = macd_l.ewm(span=signal, adjust=False).mean()
    hist   = macd_l - sig_l
    return macd_l, sig_l, hist


def _sma(closes: pd.Series, n: int) -> pd.Series:
    return closes.rolling(n).mean()


def _pct(series: pd.Series, n: int) -> float | None:
    if len(series) < n + 1:
        return None
    v0 = float(series.iloc[-(n + 1)])
    v1 = float(series.iloc[-1])
    return round((v1 / v0 - 1) * 100, 2) if v0 else None


def _ytd(series: pd.Series) -> float | None:
    try:
        this_year = series[series.index.year == datetime.now().year]
        if this_year.empty:
            return None
        return round((float(series.iloc[-1]) / float(this_year.iloc[0]) - 1) * 100, 2)
    except Exception:
        return None


def _analyse(closes: pd.Series, volumes: pd.Series) -> dict:
    price = float(closes.iloc[-1])

    # Moving averages (current value)
    def cur_sma(n):
        s = _sma(closes, n)
        v = s.iloc[-1]
        return round(float(v), 2) if not pd.isna(v) else None

    sma20  = cur_sma(20)
    sma50  = cur_sma(50)
    sma200 = cur_sma(200)

    # RSI series (last 90 days for chart) + current value
    rsi_series  = _rsi(closes)
    rsi_current = round(float(rsi_series.iloc[-1]), 2) if not pd.isna(rsi_series.iloc[-1]) else None
    rsi_90      = rsi_series.tail(90)

    # MACD series (last 90 days) + current values
    macd_l, sig_l, hist = _macd(closes)
    macd_90  = macd_l.tail(90)
    sig_90   = sig_l.tail(90)
    hist_90  = hist.tail(90)
    macd_cur = round(float(macd_l.iloc[-1]),   4) if not pd.isna(macd_l.iloc[-1])   else None
    sig_cur  = round(float(sig_l.iloc[-1]),    4) if not pd.isna(sig_l.iloc[-1])     else None
    hist_cur = round(float(hist.iloc[-1]),     4) if not pd.isna(hist.iloc[-1])      else None
    macd_bullish       = bool(macd_l.iloc[-1]  > sig_l.iloc[-1])  if macd_cur and sig_cur else None
    macd_cross_up      = bool(macd_l.iloc[-1]  > sig_l.iloc[-1] and macd_l.iloc[-2] <= sig_l.iloc[-2]) if len(macd_l) > 2 and macd_cur else False
    macd_cross_down    = bool(macd_l.iloc[-1]  < sig_l.iloc[-1] and macd_l.iloc[-2] >= sig_l.iloc[-2]) if len(macd_l) > 2 and macd_cur else False

    # Volume
    vol5  = float(volumes.tail(5).mean())  if len(volumes) >= 5  else None
    vol20 = float(volumes.tail(20).mean()) if len(volumes) >= 20 else None
    vol_ratio = round(vol5 / vol20, 2) if vol5 and vol20 and vol20 > 0 else None

    # 52-week range
    hi52 = round(float(closes.tail(252).max()), 2) if len(closes) >= 50 else None
    lo52 = round(float(closes.tail(252).min()), 2) if len(closes) >= 50 else None
    vs200_pct = round((price / sma200 - 1) * 100, 2) if sma200 else None

    # Signal generation
    signals: list[dict] = []

    if sma50 and sma200:
        if sma50 > sma200:
            signals.append({"type": "bullish", "text": "Golden cross — 50-DMA above 200-DMA"})
        else:
            signals.append({"type": "bearish", "text": "Death cross — 50-DMA below 200-DMA"})

    if sma50 and sma200:
        if price > sma50 and price > sma200:
            signals.append({"type": "bullish", "text": "Price above both 50-DMA and 200-DMA"})
        elif price < sma50 and price < sma200:
            signals.append({"type": "bearish", "text": "Price below both 50-DMA and 200-DMA"})

    if rsi_current is not None:
        if rsi_current > 70:
            signals.append({"type": "bearish", "text": f"RSI overbought ({rsi_current:.1f})"})
        elif rsi_current < 30:
            signals.append({"type": "bullish", "text": f"RSI oversold — potential reversal ({rsi_current:.1f})"})
        elif 50 <= rsi_current <= 65:
            signals.append({"type": "bullish", "text": f"RSI in bullish momentum zone ({rsi_current:.1f})"})
        elif 35 <= rsi_current < 50:
            signals.append({"type": "bearish", "text": f"RSI in bearish territory ({rsi_current:.1f})"})

    if macd_cross_up:
        signals.append({"type": "bullish", "text": "MACD bullish crossover (fresh signal)"})
    elif macd_cross_down:
        signals.append({"type": "bearish", "text": "MACD bearish crossover (fresh signal)"})
    elif macd_bullish is True:
        signals.append({"type": "bullish", "text": "MACD above signal line"})
    elif macd_bullish is False:
        signals.append({"type": "bearish", "text": "MACD below signal line"})

    if vol_ratio and vol_ratio > 1.5:
        signals.append({"type": "neutral", "text": f"Volume surge ({vol_ratio:.1f}x 20-day avg)"})

    if hi52 and price >= hi52 * 0.98:
        signals.append({"type": "bullish", "text": "Trading near 52-week high"})
    elif lo52 and price <= lo52 * 1.03:
        signals.append({"type": "bearish", "text": "Trading near 52-week low"})

    # Overall score
    bull_n = sum(1 for s in signals if s["type"] == "bullish")
    bear_n = sum(1 for s in signals if s["type"] == "bearish")
    if bull_n >= bear_n + 2:
        overall = "bullish"
    elif bear_n >= bull_n + 2:
        overall = "bearish"
    else:
        overall = "neutral"

    # Build chart series (last 90 trading days)
    def to_list(s: pd.Series) -> list:
        return [round(float(v), 4) if not pd.isna(v) else None for v in s]

    closes_90  = closes.tail(90)
    sma20_90   = _sma(closes, 20).tail(90)
    sma50_90   = _sma(closes, 50).tail(90)
    sma200_ref = sma200  # single reference value for chart line

    dates_90 = [str(d.date()) for d in closes_90.index]

    return {
        "price":       round(price, 2),
        "sma20":       sma20,
        "sma50":       sma50,
        "sma200":      sma200,
        "vs_sma200_pct": vs200_pct,
        "hi52":        hi52,
        "lo52":        lo52,
        "rsi":         rsi_current,
        "macd_current": macd_cur,
        "signal_current": sig_cur,
        "macd_histogram": hist_cur,
        "macd_bullish": macd_bullish,
        "vol_ratio":   vol_ratio,
        "signals":     signals[:6],
        "overall":     overall,
        "chart": {
            "dates":   dates_90,
            "price":   to_list(closes_90),
            "sma20":   to_list(sma20_90),
            "sma50":   to_list(sma50_90),
            "sma200_ref": sma200_ref,
            "rsi":     to_list(rsi_90),
            "macd":    to_list(macd_90),
            "macd_signal": to_list(sig_90),
            "macd_hist":   to_list(hist_90),
        },
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def get_cycle_sectors_ta(phase: str) -> dict:
    """Return TA for the top 3 sector ETFs favored in the given cycle phase."""
    phase_data = ASSET_MATRIX.get(phase)
    if not phase_data:
        raise ValueError(f"Unknown phase: {phase}")

    # Top 3 by stars (must have an ETF mapping)
    candidates = [
        p for p in phase_data["performers"]
        if CATEGORY_ETF.get(p["category"])
    ][:5]
    top3 = candidates[:3]

    tickers = [
        {
            "category": p["category"],
            "stars":    p["stars"],
            "note":     p["note"],
            "ticker":   CATEGORY_ETF[p["category"]],
        }
        for p in top3
    ]

    symbols = [t["ticker"] for t in tickers]
    end   = datetime.now()
    start = end - timedelta(days=420)  # 420d ensures enough history for SMA200

    try:
        raw = await asyncio.to_thread(
            yf.download,
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
    except Exception as exc:
        logger.error("yfinance download failed for %s: %s", symbols, exc)
        raise

    def get_series(col: str, tkr: str) -> pd.Series:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                return raw[col][tkr].dropna() if tkr in raw[col].columns else pd.Series(dtype=float)
            return raw[col].dropna() if col in raw.columns else pd.Series(dtype=float)
        except Exception:
            return pd.Series(dtype=float)

    results = []
    for t in tickers:
        tkr    = t["ticker"]
        closes  = get_series("Close",  tkr)
        volumes = get_series("Volume", tkr)
        if closes.empty:
            logger.warning("No price data for %s", tkr)
            continue

        ta = _analyse(closes, volumes)
        results.append({
            **t,
            **{k: v for k, v in ta.items() if k != "chart"},
            "chart":    ta["chart"],
            "pct_1m":   _pct(closes, 22),
            "pct_3m":   _pct(closes, 66),
            "pct_6m":   _pct(closes, 130),
            "pct_ytd":  _ytd(closes),
        })

    return {
        "phase":       phase,
        "phase_label": phase_data["label"],
        "phase_color": phase_data["color"],
        "sectors":     results,
        "as_of":       datetime.now().strftime("%Y-%m-%d"),
    }
