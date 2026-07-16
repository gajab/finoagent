"""Market overview service — indices, sector ETFs, top movers, LLM insights.

Provides data backing the Market dashboard page:
- Major indices (S&P 500, Dow, Nasdaq 100, Russell 2000, VIX) with multi-timeframe
  returns, sparklines and volume context.
- Sector ETFs (XLK, XLF, XLV, XLE, XLI, XLY, XLP, XLU, XLB, XLRE, XLC) with
  returns across 8 timeframes and sector rotation intelligence.
- Top movers (gainers/losers) from a curated S&P 500 universe with LLM-driven
  "why it's moving" explanations.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from .llm_service import call_llm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Universe definitions
# ---------------------------------------------------------------------------

INDICES: list[dict] = [
    # ── US Large Cap ──────────────────────────────────────────────────────
    {"symbol": "^GSPC",     "name": "S&P 500",            "short": "SPX",    "group": "US Large Cap"},
    {"symbol": "^NDX",      "name": "Nasdaq 100",          "short": "NDX",    "group": "US Large Cap"},
    {"symbol": "^DJI",      "name": "Dow Jones",           "short": "DJI",    "group": "US Large Cap"},
    {"symbol": "^IXIC",     "name": "Nasdaq Composite",    "short": "COMP",   "group": "US Large Cap"},
    # ── US Broad & Size ───────────────────────────────────────────────────
    {"symbol": "^RUI",      "name": "Russell 1000",        "short": "R1000",  "group": "US Size"},
    {"symbol": "^RUT",      "name": "Russell 2000",        "short": "RUT",    "group": "US Size"},
    {"symbol": "^RUA",      "name": "Russell 3000",        "short": "R3000",  "group": "US Size"},
    {"symbol": "^MID",      "name": "S&P MidCap 400",      "short": "MID",    "group": "US Size"},
    # ── International ─────────────────────────────────────────────────────
    {"symbol": "ACWX",      "name": "MSCI ACWI ex-US",     "short": "ACWX",   "group": "Global"},
    {"symbol": "^NSEI",     "name": "Nifty 50",            "short": "NIFTY",  "group": "Global"},
    {"symbol": "^N225",     "name": "Nikkei 225",          "short": "NIKKEI", "group": "Global"},
    {"symbol": "^KS11",     "name": "KOSPI",               "short": "KOSPI",  "group": "Global"},
    {"symbol": "^HSI",      "name": "Hang Seng",           "short": "HSI",    "group": "Global"},
    {"symbol": "000001.SS", "name": "Shanghai Composite",  "short": "SSE",    "group": "Global"},
    {"symbol": "^GSPTSE",   "name": "TSX Composite",       "short": "TSX",    "group": "Global"},
    {"symbol": "^FTSE",     "name": "FTSE 100",            "short": "FTSE",   "group": "Global"},
    {"symbol": "^TWII",     "name": "Taiwan Weighted",     "short": "TAIEX",  "group": "Global"},
    {"symbol": "^GDAXI",    "name": "DAX 40",              "short": "DAX",    "group": "Global"},
    # ── Treasury Yields ───────────────────────────────────────────────────
    {"symbol": "^IRX",      "name": "2Y Treasury",         "short": "US2Y",   "group": "Rates"},
    {"symbol": "^TNX",      "name": "10Y Treasury",        "short": "US10Y",  "group": "Rates"},
    {"symbol": "^TYX",      "name": "30Y Treasury",        "short": "US30Y",  "group": "Rates"},
    # ── Foreign Exchange ──────────────────────────────────────────────────
    {"symbol": "DX-Y.NYB",  "name": "US Dollar Index",     "short": "DXY",    "group": "FX"},
    {"symbol": "USDINR=X",  "name": "USD / INR",           "short": "USDINR", "group": "FX"},
    {"symbol": "USDJPY=X",  "name": "USD / JPY",           "short": "USDJPY", "group": "FX"},
    # ── Crypto ────────────────────────────────────────────────────────────
    {"symbol": "BTC-USD",   "name": "Bitcoin",             "short": "BTC",    "group": "Crypto"},
    {"symbol": "ETH-USD",   "name": "Ethereum",            "short": "ETH",    "group": "Crypto"},
    # ── Commodities ───────────────────────────────────────────────────────
    {"symbol": "GC=F",      "name": "Gold",                "short": "GOLD",   "group": "Commodities"},
    {"symbol": "SI=F",      "name": "Silver",              "short": "SILVER", "group": "Commodities"},
    {"symbol": "CL=F",      "name": "WTI Crude Oil",       "short": "WTI",    "group": "Commodities"},
    # ── Volatility ────────────────────────────────────────────────────────
    {"symbol": "^VIX",      "name": "VIX",                 "short": "VIX",    "group": "Volatility"},
]


SECTOR_ETFS: list[dict] = [
    {"symbol": "XLK",  "name": "Technology"},
    {"symbol": "XLF",  "name": "Financials"},
    {"symbol": "XLV",  "name": "Healthcare"},
    {"symbol": "XLE",  "name": "Energy"},
    {"symbol": "XLI",  "name": "Industrials"},
    {"symbol": "XLY",  "name": "Cons. Discretionary"},
    {"symbol": "XLP",  "name": "Cons. Staples"},
    {"symbol": "XLU",  "name": "Utilities"},
    {"symbol": "XLB",  "name": "Materials"},
    {"symbol": "XLRE", "name": "Real Estate"},
    {"symbol": "XLC",  "name": "Communication"},
]


# Industry-level ETFs nested under each sector. Every ETF selected here has
# 5+ years of trading history so every timeframe resolves. Gives the sector
# page an "industries inside each sector" breakdown a real quant desk uses.
INDUSTRY_ETFS_BY_SECTOR: dict[str, list[dict]] = {
    "Technology": [
        {"symbol": "SMH",  "name": "Semiconductors"},
        {"symbol": "IGV",  "name": "Software"},
        {"symbol": "HACK", "name": "Cybersecurity"},
        {"symbol": "SKYY", "name": "Cloud Computing"},
    ],
    "Financials": [
        {"symbol": "KBE", "name": "Banks"},
        {"symbol": "KIE", "name": "Insurance"},
        {"symbol": "KCE", "name": "Capital Markets"},
        {"symbol": "IAI", "name": "Broker-Dealers"},
    ],
    "Healthcare": [
        {"symbol": "IBB", "name": "Biotechnology"},
        {"symbol": "IHI", "name": "Medical Devices"},
        {"symbol": "IHF", "name": "Healthcare Providers"},
        {"symbol": "XPH", "name": "Pharmaceuticals"},
    ],
    "Energy": [
        {"symbol": "XOP", "name": "Oil & Gas E&P"},
        {"symbol": "OIH", "name": "Oilfield Services"},
        {"symbol": "TAN", "name": "Solar"},
        {"symbol": "IEO", "name": "Oil & Gas Prod."},
    ],
    "Industrials": [
        {"symbol": "ITA", "name": "Aerospace & Defense"},
        {"symbol": "IYT", "name": "Transportation"},
        {"symbol": "XAR", "name": "Aerospace"},
        {"symbol": "PPA", "name": "Defense"},
    ],
    "Cons. Discretionary": [
        {"symbol": "XRT", "name": "Retail"},
        {"symbol": "XHB", "name": "Homebuilders"},
        {"symbol": "ITB", "name": "Home Construction"},
        {"symbol": "PEJ", "name": "Leisure"},
    ],
    "Cons. Staples": [
        {"symbol": "PBJ", "name": "Food & Beverage"},
        {"symbol": "FXG", "name": "Consumer Staples"},
    ],
    "Utilities": [
        {"symbol": "JXI", "name": "Global Utilities"},
        {"symbol": "FAN", "name": "Wind Energy"},
    ],
    "Materials": [
        {"symbol": "GDX",  "name": "Gold Miners"},
        {"symbol": "COPX", "name": "Copper Miners"},
        {"symbol": "LIT",  "name": "Lithium & Battery"},
        {"symbol": "MOO",  "name": "Agribusiness"},
    ],
    "Real Estate": [
        {"symbol": "REZ",  "name": "Residential REITs"},
        {"symbol": "REM",  "name": "Mortgage REITs"},
        {"symbol": "PSR",  "name": "Active Real Estate"},
    ],
    "Communication": [
        {"symbol": "SOCL", "name": "Social Media"},
        {"symbol": "IYZ",  "name": "Telecoms"},
        {"symbol": "PBS",  "name": "Media"},
    ],
}


# Large/mega-cap movers universe. Balanced across sectors so the movers board
# isn't dominated by a single sector. Not exhaustive — kept small for speed.
MOVERS_UNIVERSE_BY_SECTOR: dict[str, list[str]] = {
    "Technology": ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AVGO", "AMD", "CRM", "ORCL", "ADBE", "INTC", "CSCO", "QCOM", "IBM"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP", "V", "MA"],
    "Healthcare": ["UNH", "JNJ", "LLY", "ABBV", "PFE", "MRK", "TMO", "ABT", "DHR", "BMY"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "OXY"],
    "Industrials": ["CAT", "BA", "HON", "GE", "UPS", "RTX", "LMT", "DE"],
    "Cons. Discretionary": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TJX"],
    "Cons. Staples": ["WMT", "PG", "KO", "PEP", "COST", "MDLZ"],
    "Utilities": ["NEE", "DUK", "SO"],
    "Materials": ["LIN", "SHW", "APD"],
    "Real Estate": ["PLD", "AMT", "EQIX"],
    "Communication": ["NFLX", "DIS", "T", "VZ", "TMUS"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIMEFRAMES: list[dict] = [
    {"key": "1d",  "label": "1D",  "days": 1},
    {"key": "7d",  "label": "7D",  "days": 7},
    {"key": "15d", "label": "15D", "days": 15},
    {"key": "1m",  "label": "1M",  "days": 30},
    {"key": "3m",  "label": "3M",  "days": 91},
    {"key": "ytd", "label": "YTD", "days": None},
    {"key": "1y",  "label": "1Y",  "days": 365},
    {"key": "3y",  "label": "3Y",  "days": 365 * 3},
    {"key": "5y",  "label": "5Y",  "days": 365 * 5},
]


def _safe_float(val, default=0.0):
    try:
        v = float(val)
        return default if math.isnan(v) or math.isinf(v) else v
    except Exception:
        return default


def _pct_return(series: pd.Series, days: int | None) -> float | None:
    """Return pct-change over ``days`` ending at the last observation.

    If ``days`` is None, compute YTD (from the first trading day of the year).
    """
    if series is None or len(series) < 2:
        return None
    end = series.iloc[-1]
    if days is None:
        # YTD — first trading day of current year
        year = series.index[-1].year
        year_slice = series[series.index.year == year]
        if len(year_slice) == 0:
            return None
        start = year_slice.iloc[0]
    else:
        cutoff = series.index[-1] - pd.Timedelta(days=days)
        prior = series[series.index <= cutoff]
        if len(prior) == 0:
            # Not enough history — fall back to first point
            start = series.iloc[0]
        else:
            start = prior.iloc[-1]
    if start == 0 or pd.isna(start) or pd.isna(end):
        return None
    return float((end / start - 1.0) * 100.0)


def _batch_history(symbols: list[str], period: str = "6y") -> pd.DataFrame:
    """Download adjusted-close + volume history for ``symbols`` in one call.

    Returns yfinance's multi-column DataFrame (columns like ('Close', 'AAPL')).
    """
    if not symbols:
        return pd.DataFrame()
    try:
        df = yf.download(
            tickers=" ".join(symbols),
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="column",
            threads=True,
        )
    except Exception as exc:
        logger.warning("Batch history failed for %s: %s", symbols, exc)
        df = pd.DataFrame()
    return df


def _single_history(symbol: str, period: str = "6y") -> tuple[pd.Series, pd.Series]:
    """Fetch one ticker directly via ``yf.Ticker`` — a reliable fallback path
    when batched download returns NaN/missing columns for that symbol."""
    try:
        hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        close = hist.get("Close", pd.Series(dtype=float)).dropna()
        volume = hist.get("Volume", pd.Series(dtype=float)).dropna()
        return close, volume
    except Exception as exc:
        logger.debug("Single history failed for %s: %s", symbol, exc)
        return pd.Series(dtype=float), pd.Series(dtype=float)


def _extract_close_volume(df: pd.DataFrame, symbol: str) -> tuple[pd.Series, pd.Series]:
    """Pull Close and Volume series for one symbol from a batched download."""
    if df is None or df.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    try:
        if isinstance(df.columns, pd.MultiIndex):
            # Two layouts exist depending on group_by: ('Close', 'AAPL') or ('AAPL', 'Close')
            close_key = ("Close", symbol) if ("Close", symbol) in df.columns else (
                (symbol, "Close") if (symbol, "Close") in df.columns else None
            )
            vol_key = ("Volume", symbol) if ("Volume", symbol) in df.columns else (
                (symbol, "Volume") if (symbol, "Volume") in df.columns else None
            )
            close = df[close_key] if close_key is not None else pd.Series(dtype=float)
            volume = df[vol_key] if vol_key is not None else pd.Series(dtype=float)
        else:
            # Single-symbol result
            close = df.get("Close", pd.Series(dtype=float))
            volume = df.get("Volume", pd.Series(dtype=float))
        close = close.dropna()
        volume = volume.dropna()
        return close, volume
    except Exception as exc:
        logger.debug("Extract close/volume failed for %s: %s", symbol, exc)
        return pd.Series(dtype=float), pd.Series(dtype=float)


def _resolve_close_volume(
    df: pd.DataFrame,
    symbol: str,
    period: str,
    min_rows: int = 5,
) -> tuple[pd.Series, pd.Series]:
    """Try batch extraction first; if empty / too thin, fall back to single."""
    close, volume = _extract_close_volume(df, symbol)
    if len(close) < min_rows:
        c2, v2 = _single_history(symbol, period=period)
        if len(c2) > len(close):
            close, volume = c2, v2
    return close, volume


def _sparkline(series: pd.Series, points: int = 60) -> list[float]:
    """Downsample ``series`` to ``points`` for a compact sparkline."""
    if series is None or len(series) == 0:
        return []
    if len(series) <= points:
        return [round(float(v), 4) for v in series.values]
    idx = np.linspace(0, len(series) - 1, points).astype(int)
    return [round(float(series.iloc[i]), 4) for i in idx]


def _compute_timeframe_returns(close: pd.Series) -> dict[str, float | None]:
    """Compute % return for every timeframe."""
    out: dict[str, float | None] = {}
    for tf in TIMEFRAMES:
        out[tf["key"]] = _pct_return(close, tf["days"])
    return out


# ---------------------------------------------------------------------------
# Quant analytics
# ---------------------------------------------------------------------------

# Approx trading days per timeframe — used to slice the daily return series.
_TF_TRADING_DAYS: dict[str, int] = {
    "1d": 1, "7d": 5, "15d": 11, "1m": 21, "3m": 63,
    "ytd": 0,  # handled specially
    "1y": 252, "3y": 252 * 3, "5y": 252 * 5,
}


def _daily_returns(close: pd.Series) -> pd.Series:
    """Compute simple daily % returns (0-1 scale)."""
    if close is None or len(close) < 2:
        return pd.Series(dtype=float)
    return close.pct_change().dropna()


def _slice_by_tf(rets: pd.Series, timeframe: str) -> pd.Series:
    """Trim the daily returns series to the requested timeframe."""
    if rets is None or len(rets) == 0:
        return rets
    if timeframe == "ytd":
        year = rets.index[-1].year
        return rets[rets.index.year == year]
    n = _TF_TRADING_DAYS.get(timeframe, 21)
    return rets.tail(n) if n > 0 else rets


def _annualized_vol(rets: pd.Series) -> float | None:
    """Annualised volatility of daily returns in %."""
    if rets is None or len(rets) < 5:
        return None
    s = rets.std(ddof=1)
    if s is None or pd.isna(s):
        return None
    return float(s * math.sqrt(252) * 100.0)


def _sharpe(rets: pd.Series, rf_annual: float = 0.04) -> float | None:
    """Annualised Sharpe ratio of daily returns (risk-free ≈ 4% by default)."""
    if rets is None or len(rets) < 5:
        return None
    rf_daily = rf_annual / 252.0
    excess = rets - rf_daily
    mu = excess.mean()
    s = excess.std(ddof=1)
    if s is None or pd.isna(s) or s == 0:
        return None
    return float((mu / s) * math.sqrt(252))


def _beta_vs(rets: pd.Series, bench_rets: pd.Series) -> float | None:
    """OLS beta of ``rets`` vs ``bench_rets`` over the common overlap."""
    if rets is None or bench_rets is None:
        return None
        
    # Strip timezones for robust joining
    if rets.index.tz is not None:
        rets = rets.copy()
        rets.index = rets.index.tz_localize(None)
    if bench_rets.index.tz is not None:
        bench_rets = bench_rets.copy()
        bench_rets.index = bench_rets.index.tz_localize(None)
        
    aligned = pd.concat([rets, bench_rets], axis=1, join="inner").dropna()
    if len(aligned) < 10:
        return None
    x = aligned.iloc[:, 1]
    y = aligned.iloc[:, 0]
    var_x = x.var(ddof=1)
    if var_x is None or pd.isna(var_x) or var_x == 0:
        return None
    cov = ((x - x.mean()) * (y - y.mean())).sum() / (len(x) - 1)
    return float(cov / var_x)


def _max_drawdown(close: pd.Series) -> float | None:
    """Max drawdown over the series, in % (negative)."""
    if close is None or len(close) < 2:
        return None
    cummax = close.cummax()
    dd = (close / cummax - 1.0)
    if dd.empty:
        return None
    return float(dd.min() * 100.0)


def _trend_persistence(rets: pd.Series) -> float | None:
    """% of days closing up — a simple trend-quality measure."""
    if rets is None or len(rets) < 5:
        return None
    return float((rets > 0).sum() / len(rets) * 100.0)


def _relative_strength(close: pd.Series, bench_close: pd.Series, timeframe: str) -> float | None:
    """Sector % return minus benchmark % return over the timeframe (in pp)."""
    tf_days = next((tf["days"] for tf in TIMEFRAMES if tf["key"] == timeframe), 30)
    s_ret = _pct_return(close, tf_days)
    b_ret = _pct_return(bench_close, tf_days)
    if s_ret is None or b_ret is None:
        return None
    return float(s_ret - b_ret)


def _compute_rotation_stage(
    close: pd.Series, 
    bench_close: pd.Series | None, 
    volume: pd.Series | None
) -> tuple[float | None, str | None, str | None]:
    """Calculate Z-Score of Relative Strength, MACD momentum, and rotation stage."""
    if close is None or len(close) < 50 or bench_close is None or len(bench_close) < 50:
        return None, "Neutral", "Neutral"

    # Strip timezones for robust joining
    c = close.copy()
    b = bench_close.copy()
    if c.index.tz is not None:
        c.index = c.index.tz_localize(None)
    if b.index.tz is not None:
        b.index = b.index.tz_localize(None)

    aligned = pd.concat([c, b], axis=1, join="inner").dropna()
    if len(aligned) < 50:
        return None, "Neutral", "Neutral"

    # RS Ratio
    rs_ratio = aligned.iloc[:, 0] / aligned.iloc[:, 1]
    
    # 50-day Z-Score
    rs_50ma = rs_ratio.rolling(50).mean()
    rs_50std = rs_ratio.rolling(50).std()
    
    if rs_50std.iloc[-1] == 0 or pd.isna(rs_50std.iloc[-1]):
        return None, "Neutral", "Neutral"
        
    z_score = float((rs_ratio.iloc[-1] - rs_50ma.iloc[-1]) / rs_50std.iloc[-1])
    
    # Momentum MACD of RS (12, 26, 9)
    ema12 = rs_ratio.ewm(span=12).mean()
    ema26 = rs_ratio.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    hist = macd - signal
    
    momentum_status = "Accelerating" if hist.iloc[-1] > 0 else "Decelerating"
    
    # Volume divergence
    vol_tilt = 1.0
    if volume is not None and len(volume) >= 20:
        avg_vol_20 = volume.tail(20).mean()
        recent_vol = volume.tail(3).mean()
        if avg_vol_20 > 0:
            vol_tilt = recent_vol / avg_vol_20

    # Composite logic
    stage = "Neutral"
    if z_score > 2.0:
        if momentum_status == "Decelerating" or vol_tilt < 0.9:
            stage = "Saturated / Exhausted"
        else:
            stage = "Overextended"
    elif z_score > 1.0:
        if momentum_status == "Decelerating":
            stage = "Maturing"
        else:
            stage = "Late Uptrend"
    elif z_score > 0.5:
        if momentum_status == "Accelerating":
            stage = "Accelerating"
        else:
            stage = "Consolidating"
    elif z_score > 0.0:
        stage = "Early Uptrend" if momentum_status == "Accelerating" else "Fading"
    elif z_score > -1.0:
        stage = "Weakening" if momentum_status == "Decelerating" else "Recovering"
    elif z_score > -2.0:
        stage = "Laggard"
    else:
        stage = "Oversold"
        
    return z_score, stage, momentum_status


def _quant_stats(
    close: pd.Series,
    bench_close: pd.Series | None,
    volume: pd.Series | None,
    timeframe: str,
) -> dict:
    """Build the full quant-analytics payload for one sector/industry."""
    rets = _daily_returns(close)
    tf_rets = _slice_by_tf(rets, timeframe)
    bench_rets = _daily_returns(bench_close) if bench_close is not None and len(bench_close) else pd.Series(dtype=float)
    tf_bench_rets = _slice_by_tf(bench_rets, timeframe) if len(bench_rets) else pd.Series(dtype=float)

    # Slice close to the same window for max-drawdown computation
    if timeframe == "ytd" and len(close):
        year = close.index[-1].year
        tf_close = close[close.index.year == year]
    else:
        n = _TF_TRADING_DAYS.get(timeframe, 21)
        tf_close = close.tail(n) if n > 0 else close

    def _r(val: float | None, ndigits: int = 2) -> float | None:
        """round() that is safe when val is None or NaN."""
        if val is None:
            return None
        try:
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                return None
            return round(f, ndigits)
        except Exception:
            return None

    vol  = _annualized_vol(tf_rets)
    shr  = _sharpe(tf_rets)
    beta = _beta_vs(tf_rets, tf_bench_rets) if len(tf_bench_rets) else None
    mdd  = _max_drawdown(tf_close)
    pers = _trend_persistence(tf_rets)
    rs   = _relative_strength(close, bench_close, timeframe) if bench_close is not None else None
    
    rs_zscore, stage, momentum = _compute_rotation_stage(close, bench_close, volume)

    return {
        "volatility_ann":    _r(vol,  2),
        "sharpe":            _r(shr,  2),
        "beta_vs_spx":       _r(beta, 2),
        "max_drawdown":      _r(mdd,  2),
        "trend_persistence": _r(pers, 1),
        "rs_vs_spx":         _r(rs,   2),
        "rs_zscore":         _r(rs_zscore, 2),
        "rotation_stage":    stage,
        "momentum_status":   momentum,
    }


# ---------------------------------------------------------------------------
# Indices
# ---------------------------------------------------------------------------

def _build_index_payload(index_def: dict, df: pd.DataFrame, period: str = "2y") -> dict:
    symbol = index_def["symbol"]
    group = index_def.get("group", "Other")
    close, volume = _resolve_close_volume(df, symbol, period=period, min_rows=5)
    if close.empty:
        return {
            "symbol": symbol,
            "name": index_def["name"],
            "short": index_def["short"],
            "group": group,
            "price": None,
            "returns": {},
            "sparkline": [],
            "volume": None,
            "avg_volume_20d": None,
            "volume_ratio": None,
            "high_52w": None,
            "low_52w": None,
            "error": "No data",
        }
    price = float(close.iloc[-1])
    returns = _compute_timeframe_returns(close)
    # 90-day sparkline
    spark = _sparkline(close.tail(90), points=60)
    # Volume context
    cur_vol = float(volume.iloc[-1]) if len(volume) else None
    avg_vol = float(volume.tail(20).mean()) if len(volume) >= 20 else None
    vol_ratio = (cur_vol / avg_vol) if cur_vol and avg_vol and avg_vol > 0 else None
    # 52w high/low
    tail_252 = close.tail(252) if len(close) >= 252 else close
    return {
        "symbol": symbol,
        "name": index_def["name"],
        "short": index_def["short"],
        "group": group,
        "price": round(price, 2),
        "returns": {k: (round(v, 2) if v is not None else None) for k, v in returns.items()},
        "sparkline": spark,
        "volume": cur_vol,
        "avg_volume_20d": avg_vol,
        "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "high_52w": round(float(tail_252.max()), 2) if len(tail_252) else None,
        "low_52w": round(float(tail_252.min()), 2) if len(tail_252) else None,
    }


def _build_indices_sync() -> list[dict]:
    symbols = [idx["symbol"] for idx in INDICES]
    # Indices only need 1Y of history for our longest displayed timeframe;
    # use 2Y for a safety buffer. Smaller period => faster, more reliable.
    df = _batch_history(symbols, period="2y")
    return [_build_index_payload(idx, df, period="2y") for idx in INDICES]


# ---------------------------------------------------------------------------
# Sectors
# ---------------------------------------------------------------------------

def _build_sector_payload(
    sector_def: dict,
    df: pd.DataFrame,
    industries_df: pd.DataFrame,
    bench_close: pd.Series | None = None,
    analytics_timeframe: str = "1m",
    period: str = "6y",
) -> dict:
    symbol = sector_def["symbol"]
    close, volume = _resolve_close_volume(df, symbol, period=period, min_rows=5)
    if close.empty:
        base = {
            "symbol": symbol,
            "name": sector_def["name"],
            "price": None,
            "returns": {},
            "sparkline": [],
            "volume": None,
            "avg_volume_20d": None,
            "volume_ratio": None,
            "industries": [],
            "analytics": {},
            "error": "No data",
        }
        return base
    price = float(close.iloc[-1])
    returns = _compute_timeframe_returns(close)
    spark = _sparkline(close.tail(90), points=60)
    cur_vol = float(volume.iloc[-1]) if len(volume) else None
    avg_vol = float(volume.tail(20).mean()) if len(volume) >= 20 else None
    vol_ratio = (cur_vol / avg_vol) if cur_vol and avg_vol and avg_vol > 0 else None

    analytics = _quant_stats(close, bench_close, volume, analytics_timeframe)

    # Industries nested under this sector
    industries = []
    for ind in INDUSTRY_ETFS_BY_SECTOR.get(sector_def["name"], []):
        i_close, i_volume = _resolve_close_volume(industries_df, ind["symbol"], period=period, min_rows=5)
        if i_close.empty:
            industries.append({
                "symbol": ind["symbol"],
                "name": ind["name"],
                "price": None,
                "returns": {},
                "volume_ratio": None,
                "analytics": {},
                "error": "No data",
            })
            continue
        i_returns = _compute_timeframe_returns(i_close)
        i_cur = float(i_volume.iloc[-1]) if len(i_volume) else None
        i_avg = float(i_volume.tail(20).mean()) if len(i_volume) >= 20 else None
        i_ratio = (i_cur / i_avg) if i_cur and i_avg and i_avg > 0 else None
        i_analytics = _quant_stats(i_close, bench_close, i_volume, analytics_timeframe)
        industries.append({
            "symbol": ind["symbol"],
            "name": ind["name"],
            "price": round(float(i_close.iloc[-1]), 2),
            "returns": {k: (round(v, 2) if v is not None else None) for k, v in i_returns.items()},
            "volume_ratio": round(i_ratio, 2) if i_ratio else None,
            "analytics": i_analytics,
        })

    return {
        "symbol": symbol,
        "name": sector_def["name"],
        "price": round(price, 2),
        "returns": {k: (round(v, 2) if v is not None else None) for k, v in returns.items()},
        "sparkline": spark,
        "volume": cur_vol,
        "avg_volume_20d": avg_vol,
        "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "industries": industries,
        "analytics": analytics,
    }


def _rotation_intelligence(sectors: list[dict], timeframe: str) -> dict:
    """Classify sectors into leaders / laggards / rotating_in / rotating_out.

    Uses a composite money-flow score so the "rotating" buckets are always
    populated — not just when extreme divergences occur.

    flow_score combines:
      + short-term momentum (7D)
      + short-vs-long divergence  (7D/1M pace vs 1Y pace)
      + volume ratio              (>1.0 = above 20D avg)
      - negatives of the above for outflow candidates
    """
    scored = []
    for s in sectors:
        r = s["returns"].get(timeframe)
        r_7d = s["returns"].get("7d")
        r_1m = s["returns"].get("1m")
        r_3m = s["returns"].get("3m")
        r_1y = s["returns"].get("1y")
        vol_ratio = s.get("volume_ratio") or 1.0
        if r is None:
            continue
        scored.append({
            "symbol": s["symbol"],
            "name": s["name"],
            "return": r,
            "short_return": r_7d if r_7d is not None else r_1m,
            "r_7d": r_7d,
            "r_1m": r_1m,
            "r_3m": r_3m,
            "one_year": r_1y,
            "volume_ratio": vol_ratio,
        })
    scored.sort(key=lambda x: x["return"], reverse=True)
    n = len(scored)
    if n == 0:
        return {
            "timeframe": timeframe,
            "leaders": [], "laggards": [],
            "rotating_in": [], "rotating_out": [],
            "breadth_pct": 0.0,
        }
    leaders = scored[: max(3, n // 3)]
    laggards = scored[-max(3, n // 3):]

    # --- Composite money-flow score -----------------------------------------
    # Each component is bounded so no single signal dominates.
    def _pace(short: float | None, long: float | None, divisor: float) -> float:
        """Short-term return vs annualised pace implied by long return."""
        if short is None or long is None:
            return 0.0
        return float(short) - float(long) / divisor

    flow_rows = []
    for s in scored:
        momentum = s["r_7d"] if s["r_7d"] is not None else 0.0  # bias toward 7D
        if s["r_1m"] is not None:
            momentum = 0.6 * momentum + 0.4 * s["r_1m"]
        # Pace divergence: is short-term beating the 1Y run-rate?
        pace = _pace(s["r_1m"], s["one_year"], 12.0)  # 1M vs 1Y/12
        # Volume tilt: 1.0 = neutral, >1.0 = above-avg participation
        vol_tilt = (s["volume_ratio"] - 1.0) * 6.0  # magnify to %-scale
        flow_score = momentum + pace + vol_tilt
        flow_rows.append({**s, "flow_score": round(flow_score, 2), "momentum": round(momentum, 2),
                          "pace": round(pace, 2), "vol_tilt": round(vol_tilt, 2)})

    flow_rows.sort(key=lambda x: x["flow_score"], reverse=True)

    # --- Rotating IN: top flow_score, excluding pure leaders-by-return -------
    # We want sectors where money is *moving in* right now — that can be a
    # leader confirming, or a formerly weak name breaking out.
    rotating_in_all = [r for r in flow_rows if r["flow_score"] > 0]
    rotating_in: list[dict] = []
    for row in rotating_in_all:
        signal = "Strong inflow" if row["flow_score"] > 3 else "Inflow"
        # If the 1Y was weak and now short-term is firm → label mean-reversion
        if row["one_year"] is not None and row["one_year"] < 5 and row["short_return"] and row["short_return"] > 0:
            signal = "Mean-reversion buy"
        rotating_in.append({**row, "signal": signal})
    # Guarantee at least the top-2 flow sectors show even if slightly negative
    if not rotating_in and flow_rows:
        rotating_in = [{**r, "signal": "Least weak"} for r in flow_rows[:2]]

    # --- Rotating OUT: bottom flow_score ------------------------------------
    rotating_out_all = [r for r in flow_rows if r["flow_score"] < 0]
    rotating_out_all.sort(key=lambda x: x["flow_score"])
    rotating_out: list[dict] = []
    for row in rotating_out_all:
        signal = "Strong outflow" if row["flow_score"] < -3 else "Outflow"
        # If 1Y was strong and short-term is weakening → distribution signal
        if row["one_year"] is not None and row["one_year"] > 10 and row["short_return"] and row["short_return"] < 0:
            signal = "Distribution"
        rotating_out.append({**row, "signal": signal})
    if not rotating_out and flow_rows:
        rotating_out = [{**r, "signal": "Least strong"} for r in reversed(flow_rows[-2:])]

    # --- Breadth -------------------------------------------------------------
    positives = sum(1 for s in scored if s["return"] > 0)
    breadth = (positives / len(scored) * 100.0) if scored else 0.0

    return {
        "timeframe": timeframe,
        "leaders": leaders[:4],
        "laggards": laggards[:4],
        "rotating_in": rotating_in[:4],
        "rotating_out": rotating_out[:4],
        "breadth_pct": round(breadth, 1),
    }


def _build_sectors_sync(rotation_timeframe: str = "1m") -> dict:
    sector_symbols = [s["symbol"] for s in SECTOR_ETFS]
    industry_symbols = [
        ind["symbol"]
        for industries in INDUSTRY_ETFS_BY_SECTOR.values()
        for ind in industries
    ]
    # Fetch sector + industry ETFs in one batched call each.
    sector_df = _batch_history(sector_symbols, period="6y")
    industries_df = _batch_history(industry_symbols, period="6y")

    # Benchmark for beta / relative-strength — SPY is the cleanest SPX proxy.
    bench_close, _ = _single_history("SPY", period="6y")

    sectors = [
        _build_sector_payload(
            s, sector_df, industries_df,
            bench_close=bench_close if len(bench_close) else None,
            analytics_timeframe=rotation_timeframe,
            period="6y",
        )
        for s in SECTOR_ETFS
    ]
    rotation = _rotation_intelligence(sectors, rotation_timeframe)

    # Aggregate analytics summary across sectors — feeds the Quant panel cards.
    def _avg(key: str) -> float | None:
        raw = [s.get("analytics", {}).get(key) for s in sectors]
        vals = [float(v) for v in raw if v is not None and not math.isnan(float(v))]
        if not vals:
            return None
        avg = sum(vals) / len(vals)
        return round(avg, 2) if not math.isnan(avg) else None

    # Count how many sectors beat SPX on this timeframe
    beating_spx = sum(
        1 for s in sectors
        if s.get("analytics", {}).get("rs_vs_spx") is not None
        and s["analytics"]["rs_vs_spx"] > 0
    )
    total_with_rs = sum(
        1 for s in sectors if s.get("analytics", {}).get("rs_vs_spx") is not None
    )

    summary = {
        "avg_volatility": _avg("volatility_ann"),
        "avg_sharpe": _avg("sharpe"),
        "avg_beta": _avg("beta_vs_spx"),
        "avg_trend_persistence": _avg("trend_persistence"),
        "beating_spx_count": beating_spx,
        "total_with_rs": total_with_rs,
        # Dispersion: gap between best and worst sector return at this tf.
        # High dispersion ⇒ stock-picker's market; low ⇒ macro-driven.
        "dispersion": _sector_dispersion(sectors, rotation_timeframe),
    }

    return {
        "sectors": sectors,
        "rotation": rotation,
        "summary": summary,
    }


def _sector_dispersion(sectors: list[dict], timeframe: str) -> float | None:
    """Range between top and bottom sector returns at ``timeframe`` (pp)."""
    vals = [
        s.get("returns", {}).get(timeframe)
        for s in sectors
        if s.get("returns", {}).get(timeframe) is not None
    ]
    if len(vals) < 2:
        return None
    return round(float(max(vals) - min(vals)), 2)


# ---------------------------------------------------------------------------
# Top movers (across universe)
# ---------------------------------------------------------------------------

def _build_movers_sync(timeframe: str = "1d", limit: int = 10) -> dict:
    tf_days = next((tf["days"] for tf in TIMEFRAMES if tf["key"] == timeframe), 1)
    all_tickers: list[tuple[str, str]] = [
        (tkr, sec) for sec, lst in MOVERS_UNIVERSE_BY_SECTOR.items() for tkr in lst
    ]
    symbols = [t for t, _ in all_tickers]
    df = _batch_history(symbols, period="1y")
    rows: list[dict] = []
    for tkr, sec in all_tickers:
        close, volume = _extract_close_volume(df, tkr)
        if close.empty or len(close) < 5:
            continue
        ret = _pct_return(close, tf_days)
        if ret is None:
            continue
        cur_vol = float(volume.iloc[-1]) if len(volume) else None
        avg_vol = float(volume.tail(20).mean()) if len(volume) >= 20 else None
        vol_ratio = (cur_vol / avg_vol) if cur_vol and avg_vol and avg_vol > 0 else None
        rows.append({
            "ticker": tkr,
            "sector": sec,
            "return": round(ret, 2),
            "price": round(float(close.iloc[-1]), 2),
            "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
        })
    rows.sort(key=lambda r: r["return"], reverse=True)
    gainers = rows[:limit]
    losers = list(reversed(rows[-limit:]))
    return {"timeframe": timeframe, "gainers": gainers, "losers": losers}


# ---------------------------------------------------------------------------
# LLM narrative helpers
# ---------------------------------------------------------------------------

async def _llm_market_narrative(indices: list[dict], openai_key: str | None, model: str = "gpt-4o") -> str | None:
    if not openai_key:
        return None
    try:
        digest = []
        for ix in indices:
            r = ix.get("returns", {}) or {}
            digest.append(
                f"{ix['short']} {ix.get('price')}  1D {r.get('1d')}%  7D {r.get('7d')}%  "
                f"1M {r.get('1m')}%  YTD {r.get('ytd')}%  1Y {r.get('1y')}%  "
                f"volΔ {ix.get('volume_ratio')}x"
            )
        prompt = (
            "You are a senior quant strategist briefing an institutional client. "
            "Given these real-time index stats, write a concise 3-4 sentence "
            "market overview covering: (1) today's direction and breadth, "
            "(2) likely macro drivers (Fed, inflation, earnings, geopolitics) "
            "based on the return signature, (3) risk tone from VIX and small-cap behavior. "
            "Do not recommend trades. Keep it data-grounded and specific.\n\n"
            + "\n".join(digest)
        )
        resp = await call_llm(
            api_key=openai_key,
            model=model,
            messages=[
                {"role": "system", "content": "You are a succinct market strategist."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.4,
        )
        return (resp or "").strip() or None
    except Exception as exc:
        logger.warning("LLM market narrative failed: %s", exc)
        return None


async def _llm_rotation_intelligence(
    sector_data: dict,
    timeframe: str,
    openai_key: str | None,
    model: str = "gpt-4o",
) -> str | None:
    if not openai_key:
        return None
    try:
        rot = sector_data.get("rotation", {})
        sectors = sector_data.get("sectors", [])
        lines = []
        for s in sectors:
            r = s.get("returns", {}) or {}
            lines.append(
                f"{s['name']:22s} {r.get('7d')}% 7D / {r.get('1m')}% 1M / "
                f"{r.get('3m')}% 3M / {r.get('ytd')}% YTD / {r.get('1y')}% 1Y  "
                f"volΔ {s.get('volume_ratio')}x"
            )
        leaders = ", ".join(f"{x['name']} ({x['return']}%)" for x in rot.get("leaders", []))
        laggards = ", ".join(f"{x['name']} ({x['return']}%)" for x in rot.get("laggards", []))
        prompt = (
            f"You are a quant sector strategist. Timeframe: {timeframe}. "
            f"Breadth: {rot.get('breadth_pct')}% of sectors positive. "
            f"Leaders: {leaders or 'n/a'}. Laggards: {laggards or 'n/a'}.\n\n"
            "Sector returns across timeframes:\n"
            + "\n".join(lines)
            + "\n\nWrite 4-6 sentences diagnosing HOW money is rotating — "
            "risk-on vs risk-off, cyclicals vs defensives, growth vs value, "
            "and cite volume behavior when elevated. Call out any sector "
            "where short-term diverges from 1Y trend (early rotation signal)."
        )
        resp = await call_llm(
            api_key=openai_key,
            model=model,
            messages=[
                {"role": "system", "content": "You are a precise sector rotation analyst."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.4,
        )
        return (resp or "").strip() or None
    except Exception as exc:
        logger.warning("LLM rotation narrative failed: %s", exc)
        return None


async def _llm_movers_explanation(
    movers: dict,
    openai_key: str | None,
    model: str = "gpt-4o",
) -> str | None:
    if not openai_key:
        return None
    try:
        g_lines = [
            f"  ↑ {m['ticker']} ({m['sector']}): +{m['return']}%  volΔ {m.get('volume_ratio')}x"
            for m in movers.get("gainers", [])[:8]
        ]
        l_lines = [
            f"  ↓ {m['ticker']} ({m['sector']}): {m['return']}%  volΔ {m.get('volume_ratio')}x"
            for m in movers.get("losers", [])[:8]
        ]
        prompt = (
            f"Timeframe: {movers.get('timeframe')}.\n\n"
            "TOP GAINERS:\n" + "\n".join(g_lines) + "\n\n"
            "TOP LOSERS:\n" + "\n".join(l_lines) + "\n\n"
            "For each ticker above, in ONE short line explain the most likely "
            "catalyst given your training knowledge (earnings, guidance, macro, "
            "sector flow, M&A chatter, rate moves). If unsure, say so. "
            "Output format: `TICKER: <one-line catalyst>` — nothing else."
        )
        resp = await call_llm(
            api_key=openai_key,
            model=model,
            messages=[
                {"role": "system", "content": "You are a concise market catalyst analyst."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            temperature=0.3,
        )
        return (resp or "").strip() or None
    except Exception as exc:
        logger.warning("LLM movers narrative failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def get_market_overview(openai_key: str | None = None, model: str = "gpt-4o") -> dict:
    indices = await asyncio.to_thread(_build_indices_sync)
    narrative = await _llm_market_narrative(indices, openai_key, model)
    return {
        "indices": indices,
        "narrative": narrative,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_sector_dashboard(
    rotation_timeframe: str = "1m",
    openai_key: str | None = None,
    model: str = "gpt-4o",
) -> dict:
    data = await asyncio.to_thread(_build_sectors_sync, rotation_timeframe)
    intelligence = await _llm_rotation_intelligence(data, rotation_timeframe, openai_key, model)
    return {
        **data,
        "intelligence": intelligence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _digest_sectors_for_llm(sector_data: dict, timeframe: str) -> str:
    """Compact text dump of the current sector snapshot for LLM grounding."""
    rot = sector_data.get("rotation", {}) or {}
    lines: list[str] = []
    lines.append(f"Timeframe: {timeframe}. Breadth: {rot.get('breadth_pct')}% positive.")

    def _fmt_group(title: str, items: list[dict]) -> str:
        if not items:
            return f"{title}: none"
        return f"{title}: " + ", ".join(
            f"{x['name']} ({x['return']:+.2f}%, vol {x.get('volume_ratio', 1):.2f}x)"
            for x in items
        )
    lines.append(_fmt_group("Leaders", rot.get("leaders", [])))
    lines.append(_fmt_group("Laggards", rot.get("laggards", [])))
    lines.append(_fmt_group("Rotating in", rot.get("rotating_in", [])))
    lines.append(_fmt_group("Rotating out", rot.get("rotating_out", [])))

    lines.append("")
    lines.append("Sectors (7D / 1M / 3M / YTD / 1Y / 3Y / 5Y):")
    for s in sector_data.get("sectors", []):
        r = s.get("returns", {}) or {}
        lines.append(
            f"  {s['name']:22s} "
            f"{_safe_float(r.get('7d')):+.2f} / "
            f"{_safe_float(r.get('1m')):+.2f} / "
            f"{_safe_float(r.get('3m')):+.2f} / "
            f"{_safe_float(r.get('ytd')):+.2f} / "
            f"{_safe_float(r.get('1y')):+.2f} / "
            f"{_safe_float(r.get('3y')):+.2f} / "
            f"{_safe_float(r.get('5y')):+.2f}  "
            f"volΔ {s.get('volume_ratio')}x"
        )
        for ind in (s.get("industries") or [])[:4]:
            ir = ind.get("returns", {}) or {}
            lines.append(
                f"      ↳ {ind['name']:20s} "
                f"{_safe_float(ir.get('7d')):+.2f} / "
                f"{_safe_float(ir.get('1m')):+.2f} / "
                f"{_safe_float(ir.get('ytd')):+.2f} / "
                f"{_safe_float(ir.get('1y')):+.2f}"
            )
    return "\n".join(lines)


async def sector_chat(
    question: str,
    timeframe: str = "1m",
    history: list[dict] | None = None,
    openai_key: str | None = None,
    model: str = "gpt-4o",
) -> dict:
    """Ask the LLM anything about the current sector landscape — grounded in
    live sector + industry data. Used by the Market page's chat panel."""
    if not openai_key:
        raise ValueError("OpenAI API key is not configured for this user.")

    # Pull fresh sector data so the model always grounds on current numbers.
    sector_data = await asyncio.to_thread(_build_sectors_sync, timeframe)
    digest = _digest_sectors_for_llm(sector_data, timeframe)

    system_msg = (
        "You are an institutional sector-rotation strategist. Ground every "
        "answer in the live sector + industry data provided below. Cite specific "
        "sector/industry names and percentages. Be specific and quantitative. "
        "If the user asks about something not in the data (e.g. macro news, "
        "a specific stock), answer from general knowledge but clearly flag the "
        "gap. Never invent numbers. Keep responses under 250 words unless asked "
        "for detail.\n\n"
        f"=== LIVE SECTOR SNAPSHOT ===\n{digest}\n=== END ==="
    )
    messages: list[dict] = [{"role": "system", "content": system_msg}]
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question.strip()})

    try:
        answer = await call_llm(
            api_key=openai_key,
            model=model,
            messages=messages,
            max_tokens=700,
            temperature=0.4,
        )
    except Exception as exc:
        logger.warning("Sector chat LLM call failed: %s", exc)
        raise
    return {
        "answer": (answer or "").strip(),
        "timeframe": timeframe,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Investment style ETFs
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Investment style / factor / geography universe
# category → list of {symbol, name}
# ---------------------------------------------------------------------------

STYLE_UNIVERSE: dict[str, list[dict]] = {
    "Factors": [
        {"symbol": "VTV",  "name": "Value"},
        {"symbol": "VUG",  "name": "Growth"},
        {"symbol": "MTUM", "name": "Momentum"},
        {"symbol": "QUAL", "name": "Quality"},
        {"symbol": "USMV", "name": "Low Volatility"},
        {"symbol": "VYM",  "name": "Dividend Yield"},
        {"symbol": "SPGP", "name": "GARP"},
        {"symbol": "RSP",  "name": "Equal Weight"},
        {"symbol": "SPHB", "name": "High Beta"},
    ],
    "Size & Indices": [
        {"symbol": "VV",   "name": "Large Cap"},
        {"symbol": "IJH",  "name": "Mid Cap"},
        {"symbol": "IJR",  "name": "Small Cap"},
        {"symbol": "SPY",  "name": "S&P 500"},
        {"symbol": "QQQ",  "name": "Nasdaq 100"},
        {"symbol": "IWB",  "name": "Russell 1000"},
        {"symbol": "IWM",  "name": "Russell 2000"},
        {"symbol": "IWV",  "name": "Russell 3000"},
        {"symbol": "DIA",  "name": "Dow Jones"},
    ],
    "Geography": [
        {"symbol": "VEA",  "name": "Developed Markets"},
        {"symbol": "VWO",  "name": "Emerging Markets"},
        {"symbol": "VT",   "name": "All World"},
        {"symbol": "VGK",  "name": "Developed Europe"},
        {"symbol": "EWJ",  "name": "Japan"},
        {"symbol": "EWY",  "name": "Korea"},
        {"symbol": "EWC",  "name": "Canada"},
        {"symbol": "EIS",  "name": "Israel"},
        {"symbol": "INDA", "name": "India"},
        {"symbol": "MCHI", "name": "China"},
        {"symbol": "EWZ",  "name": "Brazil"},
        {"symbol": "ILF",  "name": "Latin America"},
    ],
}

# Flat list with category tag — used for bulk download
_ALL_STYLE_ETFS: list[dict] = [
    {**etf, "category": cat}
    for cat, etfs in STYLE_UNIVERSE.items()
    for etf in etfs
]

# Per-category start years (match earliest ETF launch in each group)
_CATEGORY_START: dict[str, int] = {
    "Factors":        2013,   # MTUM/QUAL launched 2013
    "Size & Indices": 2007,
    "Geography":      2008,
}


def _build_style_calendar_returns_sync(category: str | None = None) -> list[dict]:
    """Annual returns for investment-style ETFs, ranked best→worst per year.

    Pass `category` to restrict to one group; None returns all categories merged.
    """
    universe = _ALL_STYLE_ETFS if category is None else [
        e for e in _ALL_STYLE_ETFS if e["category"] == category
    ]
    if not universe:
        return []

    symbols   = [e["symbol"]   for e in universe]
    name_map  = {e["symbol"]: e["name"]     for e in universe}
    cat_map   = {e["symbol"]: e["category"] for e in universe}

    today = datetime.now(timezone.utc).date()
    if category:
        start_year = _CATEGORY_START.get(category, 2010)
    else:
        start_year = min(_CATEGORY_START.values())
    current_year = today.year

    try:
        raw = yf.download(
            symbols,
            start=f"{start_year}-01-01",
            end=today.isoformat(),
            progress=False,
            auto_adjust=True,
        )
        prices = raw["Close"] if hasattr(raw.columns, "levels") else raw[["Close"]]
    except Exception as exc:
        logger.warning("Style calendar returns download failed: %s", exc)
        return []

    years: list[dict] = []
    for year in range(start_year, current_year + 1):
        year_start = f"{year}-01-01"
        year_end   = f"{year}-12-31" if year < current_year else today.isoformat()
        try:
            subset = prices.loc[year_start:year_end]
        except Exception:
            continue
        if len(subset) < 5:
            continue

        styles_for_year: list[dict] = []
        for sym in symbols:
            if sym not in subset.columns:
                continue
            col = subset[sym].dropna()
            if len(col) < 5:
                continue
            ret = float((col.iloc[-1] - col.iloc[0]) / col.iloc[0] * 100)
            styles_for_year.append({
                "symbol":   sym,
                "name":     name_map[sym],
                "category": cat_map[sym],
                "return":   round(ret, 1),
            })

        if not styles_for_year:
            continue

        styles_for_year.sort(key=lambda x: x["return"], reverse=True)
        years.append({
            "year":        year,
            "styles":      styles_for_year,
            "in_progress": year == current_year,
        })

    return years


async def get_style_calendar_returns(category: str | None = None) -> list[dict]:
    """Async wrapper — pass category to restrict to one group."""
    return await asyncio.to_thread(_build_style_calendar_returns_sync, category)


def _build_calendar_returns_sync() -> list[dict]:
    """Return sector ETF annual returns ranked highest→lowest per year.

    Fetches full price history once via yfinance batch download, then computes
    calendar-year returns (Jan 1 → Dec 31) for each sector ETF.  Current
    in-progress year uses Jan 1 → today.
    """
    symbols = [s["symbol"] for s in SECTOR_ETFS]
    name_map = {s["symbol"]: s["name"] for s in SECTOR_ETFS}

    today = datetime.now(timezone.utc).date()
    start_year = 2006
    current_year = today.year

    try:
        raw = yf.download(
            symbols,
            start=f"{start_year}-01-01",
            end=today.isoformat(),
            progress=False,
            auto_adjust=True,
        )
        # yfinance returns MultiIndex columns when multiple tickers
        if hasattr(raw.columns, "levels"):
            prices = raw["Close"]
        else:
            prices = raw[["Close"]]
            prices.columns = symbols[:1]
    except Exception as exc:
        logger.warning("Calendar returns download failed: %s", exc)
        return []

    years: list[dict] = []
    for year in range(start_year, current_year + 1):
        year_start = f"{year}-01-01"
        year_end = f"{year}-12-31" if year < current_year else today.isoformat()
        try:
            subset = prices.loc[year_start:year_end]
        except Exception:
            continue
        if len(subset) < 5:
            continue

        sectors_for_year: list[dict] = []
        for sym in symbols:
            if sym not in subset.columns:
                continue
            col = subset[sym].dropna()
            if len(col) < 5:
                continue
            ret = float((col.iloc[-1] - col.iloc[0]) / col.iloc[0] * 100)
            sectors_for_year.append({
                "symbol": sym,
                "name": name_map[sym],
                "return": round(ret, 1),
            })

        if not sectors_for_year:
            continue

        # Sort best → worst
        sectors_for_year.sort(key=lambda x: x["return"], reverse=True)
        years.append({
            "year": year,
            "sectors": sectors_for_year,
            "in_progress": year == current_year,
        })

    return years


async def get_sector_calendar_returns() -> list[dict]:
    """Async wrapper for calendar-year sector returns."""
    return await asyncio.to_thread(_build_calendar_returns_sync)


async def get_top_movers(
    timeframe: str = "1d",
    limit: int = 10,
    openai_key: str | None = None,
    model: str = "gpt-4o",
) -> dict:
    movers = await asyncio.to_thread(_build_movers_sync, timeframe, limit)
    explanations = await _llm_movers_explanation(movers, openai_key, model)
    return {
        **movers,
        "explanations": explanations,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
