"""Institutional macro-liquidity dashboard.

Aggregates the indicators a macro/quant desk watches to gauge the liquidity,
credit, rates, volatility and cross-asset regime, then derives a composite
risk-on / risk-off score.

Pillars
-------
1. Fed Liquidity & Funding   Net Liquidity (WALCL−TGA−RRP), Bank Reserves, SOFR
2. Financial Conditions      Chicago Fed NFCI, ANFCI, DXY
3. Rates & Yield Curve       2s10s, 3m10s, 10Y real yield, 5y5y fwd inflation
4. Credit Risk               HY OAS, IG OAS, HY−IG differential
5. Volatility & Fear         VIX, MOVE (bond vol), VIX term structure
6. Cross-Asset Signals       Copper/Gold ratio, Gold, Crude, USD/JPY

Data sources (both free / no key):
  * FRED public CSV    — Fed, Treasury, credit, rates, financial conditions
  * yfinance           — SPY, DXY, vol complex, futures, FX

The static "how to read" briefs live on the frontend (``LiquidityPanel.tsx``);
this service returns only the dynamic values, changes, signals and series.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from io import StringIO
from typing import Any, Callable

import httpx
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# Every FRED series the dashboard needs (id → meaning).
_FRED_SERIES = [
    "WALCL",        # Fed total assets ($M, weekly)
    "WTREGEN",      # Treasury General Account ($M, weekly)
    "RRPONTSYD",    # Overnight reverse repo ($B, daily)
    "WRESBAL",      # Reserve balances at the Fed ($M, weekly)
    "SOFR",         # Secured Overnight Financing Rate (%, daily)
    "NFCI",         # Chicago Fed National Financial Conditions Index
    "ANFCI",        # Adjusted NFCI
    "T10Y2Y",       # 10Y − 2Y spread (%, daily)
    "T10Y3M",       # 10Y − 3M spread (%, daily)
    "DFII10",       # 10Y TIPS real yield (%, daily)
    "T5YIFR",       # 5Y/5Y forward inflation expectation (%, daily)
    "BAMLH0A0HYM2", # ICE BofA US High-Yield OAS (%, daily)
    "BAMLC0A0CM",   # ICE BofA US Investment-Grade OAS (%, daily)
]

# yfinance close-only tickers fetched in one batch download.
_YF_BATCH = ["^VIX", "^VIX3M", "^MOVE", "GC=F", "HG=F", "CL=F", "JPY=X", "DX-Y.NYB"]

# Growth & inflation series for the Growth × Inflation regime quadrant.
# Fetched over a longer window than the 24-month dashboard set so the momentum
# z-scores have a stable baseline (4y excludes the 2020 COVID outliers).
_REGIME_FRED = [
    "PAYEMS",    # nonfarm payrolls (monthly, thousands of persons)
    "ICSA",      # initial jobless claims (weekly) — inverted: rising = weaker growth
    "INDPRO",    # industrial production index (monthly)
    "CPILFESL",  # core CPI, ex food & energy (monthly index)
    "PCEPILFE",  # core PCE, ex food & energy (monthly index) — the Fed's gauge
    "PPIFIS",    # PPI final demand (monthly index)
    "T5YIE",     # 5Y breakeven inflation (daily, %)
]


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_fred(series_id: str, start: str) -> pd.Series:
    """Fetch one FRED public CSV series (no API key needed)."""
    url = f"{_FRED_CSV}?id={series_id}&cosd={start}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, follow_redirects=True)
        r.raise_for_status()
    df = pd.read_csv(StringIO(r.text), parse_dates=["observation_date"], index_col="observation_date")
    s = df.iloc[:, 0].replace(".", np.nan).astype(float)
    s.index = pd.DatetimeIndex(s.index)
    return s.dropna()


def _yf_close(df: pd.DataFrame, ticker: str) -> pd.Series:
    """Extract a single ticker's Close column from a batched yf.download frame."""
    if df is None or df.empty:
        return pd.Series(dtype=float)
    key = ("Close", ticker)
    s = df[key] if key in df.columns else pd.Series(dtype=float)
    s = s.dropna()
    if hasattr(s.index, "tz") and s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    return s


def _yf_fetch_all(start: str) -> dict[str, pd.Series]:
    """Blocking yfinance fetch — run in a threadpool.  Returns close series for
    the batch tickers plus SPY close + volume (volume needed for OBV)."""
    out: dict[str, pd.Series] = {}
    try:
        batch = yf.download(
            tickers=" ".join(_YF_BATCH), start=start, interval="1d",
            progress=False, auto_adjust=True, group_by="column", threads=True,
        )
        for t in _YF_BATCH:
            out[t] = _yf_close(batch, t)
    except Exception as exc:  # pragma: no cover — network
        logger.warning("yfinance batch failed: %s", exc)
        for t in _YF_BATCH:
            out[t] = pd.Series(dtype=float)

    try:
        spy = yf.Ticker("SPY").history(start=start, interval="1d", auto_adjust=True)
        spy_close = spy["Close"].dropna() if "Close" in spy else pd.Series(dtype=float)
        spy_vol = spy["Volume"].dropna() if "Volume" in spy else pd.Series(dtype=float)
        for s in (spy_close, spy_vol):
            if hasattr(s.index, "tz") and s.index.tz is not None:
                s.index = s.index.tz_localize(None)
        out["SPY_CLOSE"], out["SPY_VOL"] = spy_close, spy_vol
    except Exception as exc:  # pragma: no cover
        logger.warning("yfinance SPY failed: %s", exc)
        out["SPY_CLOSE"], out["SPY_VOL"] = pd.Series(dtype=float), pd.Series(dtype=float)
    return out


# ---------------------------------------------------------------------------
# Series math
# ---------------------------------------------------------------------------

def _calc_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    if close.empty or volume.empty:
        return pd.Series(dtype=float)
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def _series_points(s: pd.Series, n: int | None = None) -> list[dict]:
    s = s.dropna()
    if n and len(s) > n:
        s = s.iloc[-n:]
    return [{"date": d.strftime("%Y-%m-%d"), "value": round(float(v), 4)} for d, v in s.items()]


def _last(s: pd.Series) -> float | None:
    c = s.dropna()
    return round(float(c.iloc[-1]), 4) if len(c) else None


def _change(s: pd.Series, days: int, mode: str) -> float | None:
    """Change over ``days`` trading observations.  mode 'pct' → percent change,
    'abs' → absolute change in native units."""
    c = s.dropna()
    if len(c) < days + 1:
        return None
    prev, last = float(c.iloc[-days - 1]), float(c.iloc[-1])
    if mode == "pct":
        return round((last - prev) / abs(prev) * 100, 2) if prev else None
    return round(last - prev, 3)


# ---------------------------------------------------------------------------
# Signal logic — each returns 'bullish' | 'bearish' | 'neutral' for risk assets
# ---------------------------------------------------------------------------

def _trend(s: pd.Series, lookback: int = 20) -> float | None:
    c = s.dropna()
    if len(c) < lookback + 1:
        return None
    prev = float(c.iloc[-lookback - 1])
    return (float(c.iloc[-1]) - prev) / abs(prev) * 100 if prev else None


def _sig_trend(s: pd.Series, up_is_bull: bool, min_pct: float = 1.0, lookback: int = 20) -> str:
    t = _trend(s, lookback)
    if t is None or abs(t) < min_pct:
        return "neutral"
    rising = t > 0
    return "bullish" if (rising == up_is_bull) else "bearish"


def _sig_level(v: float | None, bull_below: float | None, bear_above: float | None) -> str:
    if v is None:
        return "neutral"
    if bull_below is not None and v < bull_below:
        return "bullish"
    if bear_above is not None and v > bear_above:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Main assembly
# ---------------------------------------------------------------------------

# Indicators that carry a directional risk signal (feed the regime score).
# Informational-only series (gold, oil, usdjpy) are excluded from scoring.
_SCORED = {
    "net_liquidity", "reserves", "nfci", "anfci", "dxy", "curve_2s10s",
    "curve_3m10s", "real_yield_10y", "hy_oas", "ig_oas", "hy_ig_spread",
    "vix", "move", "vix_term",
}


def _regime(indicators: dict[str, dict]) -> dict[str, Any]:
    bull = sum(1 for k, v in indicators.items() if k in _SCORED and v["signal"] == "bullish")
    bear = sum(1 for k, v in indicators.items() if k in _SCORED and v["signal"] == "bearish")
    neut = sum(1 for k, v in indicators.items() if k in _SCORED and v["signal"] == "neutral")
    total = bull + bear + neut or 1
    score = round((bull - bear) / total * 100)
    if score >= 40:
        label = "Risk-On / Supportive"
    elif score >= 12:
        label = "Mildly Supportive"
    elif score > -12:
        label = "Neutral / Mixed"
    elif score > -40:
        label = "Mildly Cautious"
    else:
        label = "Risk-Off / Restrictive"
    return {"score": score, "label": label, "bullish": bull, "bearish": bear, "neutral": neut}


# ---------------------------------------------------------------------------
# Growth × Inflation regime quadrant
# ---------------------------------------------------------------------------

# (component id, FRED id, label, change window in observations, invert, detail kind)
_GROWTH_SPECS = [
    ("payrolls", "PAYEMS", "Payrolls",              3,  False, "mom_k"),
    ("claims",   "ICSA",   "Jobless claims",        13, True,  "level_k"),
    ("indpro",   "INDPRO", "Industrial production", 3,  False, "yoy_pct"),
]
_INFLATION_SPECS = [
    ("core_cpi",  "CPILFESL", "Core CPI",        3,  False, "yoy_pct"),
    ("core_pce",  "PCEPILFE", "Core PCE",        3,  False, "yoy_pct"),
    ("ppi",       "PPIFIS",   "Producer prices", 3,  False, "yoy_pct"),
    ("breakeven", "T5YIE",    "5Y breakeven",    63, False, "level_pct"),
]

_QUADRANTS = {
    "Reflation": {
        "summary": "Growth and inflation are both accelerating — a classic reflation. Cyclicals and real assets tend to lead; long-duration lags.",
        "tilt": ["Energy", "Materials", "Industrials", "Financials", "Value", "Small-caps"],
    },
    "Goldilocks": {
        "summary": "Growth is firming while inflation cools — the disinflationary-growth backdrop risk assets love.",
        "tilt": ["Technology", "Consumer Discretionary", "Communication Svcs", "Growth", "Long-duration bonds"],
    },
    "Stagflation": {
        "summary": "Inflation is sticky while growth slows — the toughest mix. Favors real assets and defensives; raise cash.",
        "tilt": ["Energy", "Materials", "Consumer Staples", "Health Care", "Utilities", "Commodities", "Cash"],
    },
    "Slowdown": {
        "summary": "Growth and inflation are both fading — a disinflationary slowdown. Quality, duration and defensives outperform.",
        "tilt": ["Utilities", "Consumer Staples", "Health Care", "Treasuries", "Quality", "Large-caps"],
    },
    "Transition / Mixed": {
        "summary": "Growth and inflation momentum are roughly flat — no dominant regime yet. Stay balanced and wait for a cleaner signal.",
        "tilt": ["Balanced", "Quality", "Cash buffer"],
    },
}


def _momentum_z(s: pd.Series, period: int, invert: bool = False) -> float | None:
    """Z-score of the latest ``period``-observation change vs its own history.
    Positive = the series is changing faster than its typical pace (accelerating).
    ``invert`` flips the sign (e.g. rising jobless claims = weakening growth)."""
    c = s.dropna()
    if len(c) < period + 6:
        return None
    chg = c.diff(period).dropna()
    if len(chg) < 6:
        return None
    sd = chg.std(ddof=1)
    if sd is None or pd.isna(sd) or sd == 0:
        return None
    z = float((chg.iloc[-1] - chg.mean()) / sd)
    return -max(-2.5, min(2.5, z)) if invert else max(-2.5, min(2.5, z))


def _fmt_detail(s: pd.Series, kind: str) -> str | None:
    """Human-readable latest read for a regime component."""
    c = s.dropna()
    if c.empty:
        return None
    last = float(c.iloc[-1])
    if kind == "level_pct":
        return f"{last:.2f}%"
    if kind == "level_k":
        return f"{last / 1000:.0f}k" if abs(last) >= 1000 else f"{last:.0f}"
    if kind == "mom_k":
        if len(c) < 2:
            return None
        return f"{last - float(c.iloc[-2]):+,.0f}k"
    if kind == "yoy_pct":
        if len(c) < 13:
            return None
        prev = float(c.iloc[-13])
        return f"{(last / prev - 1) * 100:+.1f}% YoY" if prev else None
    return None


def _regime_axis(specs: list[tuple], fred: dict[str, pd.Series]) -> tuple[float, list[dict]]:
    """Average momentum z across a set of series + per-component breakdown."""
    comps: list[dict] = []
    zs: list[float] = []
    for cid, fid, label, period, invert, kind in specs:
        s = fred.get(fid, pd.Series(dtype=float))
        z = _momentum_z(s, period, invert)
        if z is not None:
            zs.append(z)
            sig = "accelerating" if z > 0.25 else "decelerating" if z < -0.25 else "stable"
        else:
            sig = "n/a"
        comps.append({
            "id": cid,
            "label": label,
            "detail": _fmt_detail(s, kind),
            "z": round(z, 2) if z is not None else None,
            "signal": sig,
        })
    axis = sum(zs) / len(zs) if zs else 0.0
    return axis, comps


def _macro_regime(fred: dict[str, pd.Series]) -> dict[str, Any]:
    """Classify the Growth × Inflation regime from momentum of the regime series."""
    g_axis, g_comps = _regime_axis(_GROWTH_SPECS, fred)
    i_axis, i_comps = _regime_axis(_INFLATION_SPECS, fred)

    dead = 0.20  # both axes within ±dead z → no dominant regime
    if abs(g_axis) <= dead and abs(i_axis) <= dead:
        quadrant = "Transition / Mixed"
    elif g_axis >= 0 and i_axis >= 0:
        quadrant = "Reflation"
    elif g_axis >= 0 and i_axis < 0:
        quadrant = "Goldilocks"
    elif g_axis < 0 and i_axis >= 0:
        quadrant = "Stagflation"
    else:
        quadrant = "Slowdown"

    meta = _QUADRANTS[quadrant]
    return {
        "growth_score": round(max(-100.0, min(100.0, g_axis * 50))),
        "inflation_score": round(max(-100.0, min(100.0, i_axis * 50))),
        "quadrant": quadrant,
        "summary": meta["summary"],
        "tilt": meta["tilt"],
        "growth_components": g_comps,
        "inflation_components": i_comps,
    }


async def get_liquidity_dashboard(months: int = 24) -> dict[str, Any]:
    start = (datetime.now() - timedelta(days=months * 31)).strftime("%Y-%m-%d")
    regime_start = (datetime.now() - timedelta(days=4 * 366)).strftime("%Y-%m-%d")

    # Fetch FRED concurrently + yfinance in a threadpool, all in parallel.
    fred_task = asyncio.gather(*[_fetch_fred(sid, start) for sid in _FRED_SERIES], return_exceptions=True)
    regime_task = asyncio.gather(*[_fetch_fred(sid, regime_start) for sid in _REGIME_FRED], return_exceptions=True)
    yf_task = asyncio.get_event_loop().run_in_executor(None, _yf_fetch_all, start)
    fred_results, regime_results, yf_data = await asyncio.gather(fred_task, regime_task, yf_task)

    fred: dict[str, pd.Series] = {}
    for sid, res in zip(_FRED_SERIES, fred_results):
        if isinstance(res, Exception):
            logger.warning("FRED %s failed: %s", sid, res)
            fred[sid] = pd.Series(dtype=float)
        else:
            fred[sid] = res

    regime_fred: dict[str, pd.Series] = {}
    for sid, res in zip(_REGIME_FRED, regime_results):
        if isinstance(res, Exception):
            logger.warning("FRED regime %s failed: %s", sid, res)
            regime_fred[sid] = pd.Series(dtype=float)
        else:
            regime_fred[sid] = res

    spy_close = yf_data["SPY_CLOSE"]
    spy_vol = yf_data["SPY_VOL"]
    obv = _calc_obv(spy_close, spy_vol)
    trade_idx = spy_close.index  # align FRED (weekly/daily) onto SPY trading days

    def _align(s: pd.Series) -> pd.Series:
        return s.reindex(trade_idx).ffill() if len(trade_idx) and len(s) else s

    # ---- Derived series ----------------------------------------------------
    walcl_b = _align(fred["WALCL"] / 1000.0)        # $M → $B
    tga_b = _align(fred["WTREGEN"] / 1000.0)        # $M → $B
    rrp_b = _align(fred["RRPONTSYD"])               # already $B
    net_liq = (walcl_b - tga_b - rrp_b).dropna()
    reserves_t = _align(fred["WRESBAL"] / 1e6)      # $M → $T

    curve_2s10s = _align(fred["T10Y2Y"] * 100)      # % → bps
    curve_3m10s = _align(fred["T10Y3M"] * 100)      # % → bps
    real_yield = _align(fred["DFII10"])             # %
    infl_5y5y = _align(fred["T5YIFR"])              # %
    hy_oas = _align(fred["BAMLH0A0HYM2"])           # %
    ig_oas = _align(fred["BAMLC0A0CM"])             # %
    hy_ig = ((hy_oas - ig_oas) * 100).dropna()      # % diff → bps
    nfci = _align(fred["NFCI"])
    anfci = _align(fred["ANFCI"])
    sofr = _align(fred["SOFR"])

    vix = yf_data["^VIX"]
    vix3m = yf_data["^VIX3M"]
    move = yf_data["^MOVE"]
    vix_term = (vix / vix3m.reindex(vix.index).ffill()).dropna() if len(vix) and len(vix3m) else pd.Series(dtype=float)
    gold = yf_data["GC=F"]
    copper = yf_data["HG=F"]
    copper_gold = ((copper.reindex(gold.index).ffill() / gold) * 1000).dropna() if len(gold) and len(copper) else pd.Series(dtype=float)
    oil = yf_data["CL=F"]
    usdjpy = yf_data["JPY=X"]
    dxy = yf_data["DX-Y.NYB"]

    n_2y = min(504, len(spy_close)) or None
    n_1y = min(252, len(spy_close)) or 252

    # ---- Build indicator records ------------------------------------------
    # (series, unit, change_mode, signal, chart_points)
    specs: list[tuple[str, pd.Series, str, str, str, int]] = [
        # id,               series,       unit,      mode,  signal,                                            points
        ("net_liquidity",   net_liq,      "$B",      "pct", _sig_trend(net_liq, up_is_bull=True, min_pct=0.3),   n_2y or 504),
        ("reserves",        reserves_t,   "$T",      "pct", _sig_trend(reserves_t, up_is_bull=True, min_pct=0.5), n_1y),
        ("sofr",            sofr,         "%",       "abs", ("bearish" if (_change(sofr, 5, "abs") or 0) > 0.10 else "neutral"), n_1y),
        ("nfci",            nfci,         "index",   "abs", _sig_level(_last(nfci), bull_below=-0.10, bear_above=0.10), n_1y),
        ("anfci",           anfci,        "index",   "abs", _sig_level(_last(anfci), bull_below=-0.10, bear_above=0.10), n_1y),
        ("dxy",             dxy,          "index",   "pct", _sig_trend(dxy, up_is_bull=False, min_pct=0.5),       n_1y),
        ("curve_2s10s",     curve_2s10s,  "bps",     "abs", ("bearish" if (_last(curve_2s10s) or 0) < 0 else "neutral"), n_1y),
        ("curve_3m10s",     curve_3m10s,  "bps",     "abs", ("bearish" if (_last(curve_3m10s) or 0) < 0 else "neutral"), n_1y),
        ("real_yield_10y",  real_yield,   "%",       "abs", _sig_trend(real_yield, up_is_bull=False, min_pct=4.0), n_1y),
        ("inflation_5y5y",  infl_5y5y,    "%",       "abs", ("bearish" if (_last(infl_5y5y) or 0) > 2.6 else "neutral"), n_1y),
        ("hy_oas",          hy_oas,       "%",       "abs", _sig_level(_last(hy_oas), bull_below=3.5, bear_above=5.0), n_1y),
        ("ig_oas",          ig_oas,       "%",       "abs", _sig_level(_last(ig_oas), bull_below=1.0, bear_above=1.5), n_1y),
        ("hy_ig_spread",    hy_ig,        "bps",     "abs", _sig_trend(hy_ig, up_is_bull=False, min_pct=8.0),     n_1y),
        ("vix",             vix,          "level",   "abs", _sig_level(_last(vix), bull_below=15.0, bear_above=20.0), n_1y),
        ("move",            move,         "level",   "abs", _sig_level(_last(move), bull_below=90.0, bear_above=120.0), n_1y),
        ("vix_term",        vix_term,     "ratio",   "abs", _sig_level(_last(vix_term), bull_below=0.95, bear_above=1.0), n_1y),
        ("copper_gold",     copper_gold,  "x1000",   "pct", _sig_trend(copper_gold, up_is_bull=True, min_pct=2.0), n_1y),
        ("gold",            gold,         "$",       "pct", "neutral", n_1y),
        ("oil",             oil,          "$",       "pct", "neutral", n_1y),
        ("usdjpy",          usdjpy,       "fx",      "pct", "neutral", n_1y),
    ]

    indicators: dict[str, dict] = {}
    for ind_id, series, unit, mode, signal, pts in specs:
        indicators[ind_id] = {
            "value": _last(series),
            "unit": unit,
            "change_5d": _change(series, 5, mode),
            "change_20d": _change(series, 20, mode),
            "change_mode": mode,
            "signal": signal,
            "series": _series_points(series, pts),
        }

    return {
        "indicators": indicators,
        "spy": {
            "price": _last(spy_close),
            "change_5d": _change(spy_close, 5, "pct"),
            "change_20d": _change(spy_close, 20, "pct"),
            "series_price": _series_points(spy_close, n_2y or 504),
            "series_obv": _series_points(obv, n_1y),
        },
        "components": {
            "walcl_b": _last(walcl_b),
            "tga_b": _last(tga_b),
            "rrp_b": _last(rrp_b),
        },
        "regime": _regime(indicators),
        "macro_regime": _macro_regime(regime_fred),
        "generated_at": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# LLM narrative
# ---------------------------------------------------------------------------

async def _llm_liquidity_narrative(data: dict, openai_key: str, model: str) -> str:
    from .llm_service import call_llm

    ind = data.get("indicators", {})
    comp = data.get("components", {})
    spy = data.get("spy", {})
    regime = data.get("regime", {})
    mr = data.get("macro_regime", {})

    def _v(k: str) -> str:
        d = ind.get(k, {})
        val, unit = d.get("value"), d.get("unit", "")
        if val is None:
            return "N/A"
        sig = d.get("signal", "")
        c5 = d.get("change_5d")
        c5s = f", 5d Δ {'+' if (c5 or 0) >= 0 else ''}{c5}" if c5 is not None else ""
        return f"{val} {unit} [{sig}{c5s}]"

    prompt = f"""You are a senior macro strategist briefing a portfolio committee. Be specific, quantitative and opinionated. No boilerplate disclaimers.

# Composite Regime: {regime.get('label')} (score {regime.get('score')}/100 — {regime.get('bullish')} bullish / {regime.get('bearish')} bearish signals)
# Growth × Inflation Regime: {mr.get('quadrant')} (growth momentum {mr.get('growth_score')}, inflation momentum {mr.get('inflation_score')}; −100 = decelerating, +100 = accelerating)

## 1. Fed Liquidity & Funding
- Net Fed Liquidity (WALCL−TGA−RRP): {_v('net_liquidity')}  (WALCL ${comp.get('walcl_b')}B / TGA ${comp.get('tga_b')}B / RRP ${comp.get('rrp_b')}B)
- Bank Reserves: {_v('reserves')}
- SOFR (funding rate): {_v('sofr')}

## 2. Financial Conditions
- Chicago Fed NFCI: {_v('nfci')}   (negative = loose)
- Adjusted NFCI: {_v('anfci')}
- US Dollar (DXY): {_v('dxy')}

## 3. Rates & Yield Curve
- 2s10s curve: {_v('curve_2s10s')}   (negative = inverted)
- 3m10s curve: {_v('curve_3m10s')}
- 10Y Real Yield (TIPS): {_v('real_yield_10y')}
- 5y5y Forward Inflation: {_v('inflation_5y5y')}

## 4. Credit Risk
- HY OAS: {_v('hy_oas')}
- IG OAS: {_v('ig_oas')}
- HY−IG differential: {_v('hy_ig_spread')}

## 5. Volatility & Fear
- VIX (equity vol): {_v('vix')}
- MOVE (bond vol): {_v('move')}
- VIX term structure (VIX/VIX3M): {_v('vix_term')}   (>1 = backwardation/stress)

## 6. Cross-Asset
- Copper/Gold (growth proxy): {_v('copper_gold')}
- Gold: {_v('gold')}   Crude: {_v('oil')}   USD/JPY: {_v('usdjpy')}

## Equity reference
- SPY: ${spy.get('price')}  (5d {spy.get('change_5d')}%, 20d {spy.get('change_20d')}%)

Write a tight 5-section brief:
1. **Liquidity & Funding regime** — is the Fed net adding or draining? Reserves/RRP/TGA dynamics, any funding stress in SOFR.
2. **Conditions, rates & the curve** — what NFCI + DXY + real yields + curve shape jointly say about tightness and recession risk.
3. **Credit & volatility** — are credit and vol confirming or contradicting the liquidity signal? Flag any divergence (e.g. tight spreads but rising MOVE).
4. **Cross-asset & SPY confirmation** — is copper/gold, the dollar, and SPY price action consistent with the composite regime?
5. **Positioning takeaways** — 3 concrete, prioritized watch-items / thresholds for the next 2–4 weeks.

Reference the actual numbers. Call out the single most important divergence on the board, and tie the conclusion to the Growth × Inflation regime ({mr.get('quadrant')}) — which sectors it favors and which to fade."""

    return await call_llm(
        api_key=openai_key,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.7,
    )
