"""Famous-trader rule-sets as TICKER-level strategies (Technical tab → "Every setup and named strategy").

The Trade Manager (``trade_manager_service``) already runs 15 traders' published mechanical criteria on a position's
chart (``trader_lenses.trader_lenses``) and shows them as "Famous-trader consensus" in My Trades → Evidence. This module
runs the SAME rule-sets (no re-implementation — one source of truth for the rules) on any ticker's daily history and
reshapes them for a strategy board: each trader's checklist with measured values, the trader's OWN exit level with its
distance from price (and whether price has already lost it), reference levels that can be drawn on the chart, and — where
a matching exit was backtested — how that kind of exit actually behaved.

Honesty is part of the payload: the stored backtest (``trade_manager_service.BACKTEST``) found NO directional edge in
these rule-sets at 21/63 days, so the board frames them as a discipline / structure read, never a forecast, and
``edge_note`` carries that finding. Rules are the traders' widely-published criteria — a systematic reading, not a claim
about what any individual trader did.

PURE core (``build_trader_strategies``: numpy in, dict out) + a thin yfinance wrapper (``compute_trader_strategies``).
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

import numpy as np

from . import trader_lenses as TL

logger = logging.getLogger(__name__)

# ── catalogue: every trader the Trade Manager runs, grouped for the board ───────────────────────────────────────────
FAMILIES: dict[str, str] = {
    "trend": "Trend following",
    "leaders": "Leaders & momentum",
    "timing": "Pullbacks & timing",
    "volatility": "Volatility & volume",
}
# trader key → family. The two traders that already have a full named-strategy screen are flagged ``named_elsewhere``.
CATALOGUE: dict[str, dict[str, Any]] = {
    "minervini":     {"family": "leaders"},
    "oneil":         {"family": "leaders"},
    "weinstein":     {"family": "trend"},
    "turtles":       {"family": "trend"},
    "ptj":           {"family": "trend"},
    "druckenmiller": {"family": "leaders"},
    "raschke":       {"family": "timing"},
    "connors":       {"family": "timing", "named_elsewhere": "connors_rsi2"},
    "qullamaggie":   {"family": "leaders", "named_elsewhere": "qullamaggie"},
    "livermore":     {"family": "leaders"},
    "carter":        {"family": "volatility"},
    "ichimoku":      {"family": "trend"},
    "elder":         {"family": "timing"},
    "wyckoff":       {"family": "volatility"},
    "cta":           {"family": "trend"},
}
SHORT_NAME = {
    "minervini": "Minervini", "oneil": "O'Neil", "weinstein": "Weinstein", "turtles": "Turtles", "ptj": "Paul Tudor Jones",
    "druckenmiller": "Druckenmiller", "raschke": "Raschke", "connors": "Connors", "qullamaggie": "Qullamägi", "livermore": "Livermore",
    "carter": "Carter", "ichimoku": "Ichimoku", "elder": "Elder", "wyckoff": "Wyckoff / VSA", "cta": "Trend CTAs",
}
# why a trader can be missing from the output
NEEDS = {
    "weinstein": "Needs about 200 daily bars (a 30-week average and a weekly stage).",
    "ichimoku": "Needs about 80 daily bars (the Ichimoku cloud looks 52 bars back, plotted 26 ahead).",
}

# Is the trader's published EXIT a close beyond a price line (so a breached line means their exit rule has triggered), or is
# the exit something else (relative strength, an oscillator, a bar colour) with the drawn line only a reference? The lens'
# ``exit_level`` is the nearest drawable price either way; this flag keeps the UI from overstating what a breach means.
EXIT_IS_LEVEL: dict[str, bool] = {
    "minervini": True, "oneil": True, "weinstein": True, "turtles": True, "ptj": True, "raschke": True, "qullamaggie": True,
    "livermore": True, "ichimoku": True, "wyckoff": True, "cta": True,
    "druckenmiller": False,   # relative-strength line / benchmark 200d
    "carter": False,          # squeeze-momentum histogram turning
    "elder": False,           # impulse bar colour / weekly tide
    "connors": False,         # exits on STRENGTH (close above the 5d MA / RSI(2) > 70), not on a loss
}

# ── reference levels worth drawing per trader: (label, how to read it from the indicator suite, role) ─────────────────
# role: "exit" = the trader's own stop (equals the lens' exit_level), "trigger" = their entry line, "reference" = context.


def _g(d: Optional[dict], *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


_LEVELS: dict[str, list[tuple[str, tuple, str]]] = {
    # labels stay ≤ 11 characters: they are printed next to the price in a narrow chart gutter (~20 characters in all)
    "minervini": [("50d avg", ("sma", "50"), "exit"), ("150d avg", ("sma", "150"), "reference"), ("200d avg", ("sma", "200"), "reference")],
    "oneil": [("21d EMA", ("ema", "21"), "reference"), ("50d avg", ("sma", "50"), "exit")],
    "weinstein": [("30-wk avg", ("weekly", "sma30"), "exit")],
    "turtles": [("20d high", ("prior_range", "hi_20"), "trigger"), ("10d low", ("prior_range", "lo_10"), "exit"),
                ("20d low", ("prior_range", "lo_20"), "reference"), ("55d low", ("prior_range", "lo_55"), "reference")],
    "ptj": [("200d avg", ("sma", "200"), "exit")],
    "druckenmiller": [("21d EMA", ("ema", "21"), "exit")],
    "raschke": [("20d EMA", ("ema", "20"), "exit")],
    "connors": [("10d avg", ("sma", "10"), "exit")],
    "qullamaggie": [("10d EMA", ("ema", "10"), "exit"), ("20d EMA", ("ema", "20"), "reference")],
    "livermore": [("20d pivot", ("prior_range", "hi_20"), "exit")],
    "carter": [("Lower band", ("bollinger", "lower"), "exit"), ("Upper band", ("bollinger", "upper"), "reference")],
    "ichimoku": [("Kijun", ("ichimoku", "kijun"), "exit"), ("Tenkan", ("ichimoku", "tenkan"), "reference"),
                 ("Cloud top", ("ichimoku", "cloud_top"), "reference"), ("Cloud low", ("ichimoku", "cloud_bottom"), "reference")],
    "elder": [("13d EMA", ("ema", "13"), "exit")],
    "wyckoff": [("Range low", ("prior_range", "lo_20"), "exit"), ("Range high", ("prior_range", "hi_20"), "reference")],
    "cta": [("Chand 3×ATR", ("chandelier", "long_stop"), "exit"), ("Chand 5×ATR", ("chandelier", "long_stop_wide"), "reference"),
            ("Supertrend", ("supertrend", "line"), "reference")],
}

# ── how the closest BACKTESTED exit behaved (copied from trade_manager_service.BACKTEST["exit_rules"]; a test pins them) ─
_EXIT_BACKTEST_ROWS = {
    "hold126":    {"label": "hold 126 days (no exit)",                      "mean": 7.8, "win": 65, "p5": -20.3, "worst": -74.2, "hold": 124},
    "minervini3": {"label": "Minervini ⅓ at 50d / ⅓ at 150d / ⅓ at 200d",   "mean": 3.7, "win": 47, "p5": -11.5, "worst": -51.7, "hold": 64},
    "sma200":     {"label": "exit on a close below the 200d SMA",           "mean": 5.0, "win": 46, "p5": -15.4, "worst": -56.3, "hold": 84},
    "sma150":     {"label": "exit on a close below the 150d SMA",           "mean": 4.4, "win": 42, "p5": -13.8, "worst": -55.1, "hold": 75},
    "sma50":      {"label": "exit on a close below the 50d SMA",            "mean": 1.8, "win": 39, "p5": -8.7,  "worst": -44.4, "hold": 34},
    "chand3":     {"label": "Chandelier 3×ATR trail",                       "mean": 1.2, "win": 40, "p5": -7.2,  "worst": -30.1, "hold": 24},
    "ema21":      {"label": "exit on a close below the 21 EMA",             "mean": 0.8, "win": 38, "p5": -6.0,  "worst": -30.1, "hold": 16},
}
# trader → (backtested exit row, exact | closest)
_EXIT_MATCH = {
    "minervini": ("minervini3", "exact"), "oneil": ("sma50", "exact"), "ptj": ("sma200", "exact"), "cta": ("chand3", "exact"),
    "weinstein": ("sma150", "closest"), "raschke": ("ema21", "closest"),
}
EDGE_NOTE = (
    "Backtested on 99 liquid US stocks and ETFs, 2016-2026 (52.6k point-in-time samples): none of these 15 rule-sets, nor their "
    "consensus, predicts the next 21- or 63-day return (information coefficient −0.02 to +0.01, |t| < 1.2). Treat each one as a "
    "discipline and a structure read — what that trader would require and where they would get out — not as a forecast."
)


def _clean(x: Any) -> Any:
    """JSON-safe: numpy scalars → python, NaN/inf → None, recurse into containers."""
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _num(x) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _stop_block(level: Optional[float], price: Optional[float], atr: Optional[float]) -> Optional[dict]:
    """The trader's own exit as a LONG-frame stop: distance from price and whether price has ALREADY lost it (then it is a
    level to reclaim, not a future exit)."""
    lv, px = _num(level), _num(price)
    if lv is None or px is None or px <= 0:
        return None
    a = _num(atr)
    return {
        "level": round(lv, 2),
        "distance_pct": round((lv - px) / px * 100.0, 2),                       # negative = below price
        "atr_multiple": round((px - lv) / a, 2) if a and a > 0 else None,        # positive = below price
        "breached": bool(px <= lv),
    }


def _levels(key: str, ind: dict) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple] = set()
    for label, path, role in _LEVELS.get(key, []):
        v = _num(_g(ind, *path))
        if v is None or v <= 0:
            continue
        sig = (label, round(v, 2))
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"label": label, "price": round(v, 2), "role": role})
    return out


def _exit_backtest(key: str) -> Optional[dict]:
    m = _EXIT_MATCH.get(key)
    if not m:
        return None
    row = _EXIT_BACKTEST_ROWS[m[0]]
    return {"match": m[1], **row, "baseline": {**_EXIT_BACKTEST_ROWS["hold126"]},
            "note": "Backtest of entering on bullish rule-set readings and leaving on this kind of exit (126-day max hold)."}


def _grade(met: int, total: int) -> str:
    if total <= 0:
        return "no_data"
    f = met / total
    return "all" if met == total else "most" if f >= 0.67 else "some" if f >= 0.34 else "few"


def build_trader_strategies(o, h, l, c, v, idx=None, bench_c=None) -> Optional[dict]:
    """Run all 15 rule-sets on one daily OHLCV history. PURE. ``None`` when there is too little history for the suite."""
    ind = TL.indicator_suite(o, h, l, c, v, idx, bench_c)
    if not ind:
        return None
    lenses = TL.trader_lenses(ind)
    price, atr = _num(ind.get("price")), _num(ind.get("atr14"))
    by_key = {l_["key"]: l_ for l_ in lenses}

    rows: list[dict] = []
    for key, meta in CATALOGUE.items():
        base = {"key": key, "trader": SHORT_NAME[key], "family": meta["family"], "named_elsewhere": meta.get("named_elsewhere")}
        lens = by_key.get(key)
        if lens is None:
            rows.append({**base, "available": False, "reason": NEEDS.get(key, "Not enough history."), "rules": [], "met": 0, "total": 0,
                         "grade": "no_data", "stance": "neutral", "d": 0.0, "levels": [], "stop": None, "exit_is_level": False, "exit_backtest": None})
            continue
        stop = _stop_block(lens.get("exit_level"), price, atr)
        rows.append({
            **base, "available": True, "full_name": lens["trader"], "philosophy": lens["philosophy"], "rules": lens["rules"],
            "met": lens["met"], "total": lens["total"], "grade": _grade(lens["met"], lens["total"]),
            "stance": lens["stance"], "d": lens["d"], "note": lens.get("note") or "",
            "exit_rule": lens["exit_rule"], "exit_is_level": EXIT_IS_LEVEL[key], "stop": stop, "levels": _levels(key, ind),
            "exit_backtest": _exit_backtest(key),
        })
    scored = [r for r in rows if r["available"]]
    consensus = TL.lens_consensus([by_key[r["key"]] for r in scored])
    return _clean({
        "price": price, "atr14": atr, "bars": ind.get("bars"), "families": FAMILIES,
        "consensus": consensus, "strategies": rows, "edge_note": EDGE_NOTE,
        "meta": {"unavailable": [r["key"] for r in rows if not r["available"]], "benchmark": bool(bench_c is not None and len(bench_c) >= 64)},
    })


def compute_trader_strategies(stock) -> Optional[dict]:
    """Blocking (run in an executor): 2y daily history + SPY benchmark → ``build_trader_strategies``."""
    import yfinance as yf
    try:
        hist = stock.history(period="2y", interval="1d").dropna(subset=["Close"])
    except Exception as exc:  # noqa: BLE001
        logger.info("trader-strategies history failed: %s", exc)
        return None
    if hist is None or len(hist) < 60:
        return None
    bench = None
    try:
        b = yf.Ticker("SPY").history(period="2y", interval="1d")["Close"].dropna()
        bench = b.reindex(hist.index, method="ffill").dropna().values
    except Exception:  # noqa: BLE001
        bench = None
    out = build_trader_strategies(hist["Open"].values, hist["High"].values, hist["Low"].values,
                                  hist["Close"].values, hist["Volume"].values, hist.index, bench)
    if out is not None:
        out["as_of"] = str(hist.index[-1])[:10]
    return out
