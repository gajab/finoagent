"""Portfolio optimization service — min-volatility + HRP + discrete allocation.

This is the compute layer behind ``GET /api/portfolio/optimize``. It takes the
user's holdings, pulls aligned daily price history (the same yfinance "house
pattern" the TLH service uses), and runs two allocators from PyPortfolioOpt:

* **Minimum-volatility** mean-variance (Ledoit-Wolf shrunk covariance) — the
  lowest-risk long-only portfolio on the efficient frontier.
* **Hierarchical Risk Parity (HRP)** — a robust, clustering-based allocator that
  needs no return estimates and no covariance inversion.

For each target it also produces a **DiscreteAllocation** trade list (integer
share deltas vs. the current book) and the points needed to draw the efficient
frontier with a "you are here" marker.

Everything heavy (yfinance, cvxpy solves) runs in a worker thread; the router
just awaits :func:`optimize_portfolio` and caches the JSON.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time as _time
from typing import Iterable, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# PyPortfolioOpt pulls in cvxpy; guard the import so the app still boots on an
# image that predates the requirements bump (the endpoint then reports it
# cleanly instead of 500-ing at import time).
try:
    from pypfopt import (
        EfficientFrontier,
        HRPOpt,
        expected_returns,
        objective_functions,
        risk_models,
    )
    from pypfopt.discrete_allocation import DiscreteAllocation

    _PFOPT_AVAILABLE = True
    _PFOPT_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - only hit on un-rebuilt images
    _PFOPT_AVAILABLE = False
    _PFOPT_IMPORT_ERROR = str(exc)

# Asset types we can price as a continuous return series. Options / cash / bonds
# don't fit mean-variance cleanly, so they're excluded (and reported back).
_OPTIMIZABLE_TYPES = {"STOCK", "ETF", "MUTUAL_FUND"}

_MIN_OBS = 60          # trading days of overlapping history required per name
_DEFAULT_LOOKBACK = "2y"
_TRADING_DAYS = 252
_FRONTIER_POINTS = 30
_HOLD_EPS = 1e-9       # |delta shares| below this is a HOLD
_BENCHMARK = "SPY"     # market proxy for CAPM expected returns

# In-process history cache. Constraint tweaking (max-weight / sector cap /
# turnover cost) re-solves the same universe repeatedly, so caching the raw
# download keeps those re-solves off the network. Keyed by (tickers, lookback).
_HISTORY_TTL = 6 * 3600
_history_cache: dict[tuple, tuple[float, dict]] = {}


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------

def _f(x, digits: int = 6) -> Optional[float]:
    """JSON-safe float: round, and turn NaN/inf into ``None``."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, digits)


def _pct(x) -> Optional[float]:
    """Fraction -> percent, rounded to 2dp, NaN-safe."""
    v = _f(x, 8)
    return None if v is None else round(v * 100, 2)


# ---------------------------------------------------------------------------
# Price history (blocking; called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _parse_close(data, tickers: list[str]) -> dict[str, pd.Series]:
    """Pull a {ticker: close_series} map out of a yf.download frame.

    Mirrors the defensive single-vs-multi-ticker handling used elsewhere in the
    codebase (TLH / portfolio_service).
    """
    out: dict[str, pd.Series] = {}
    if data is None or getattr(data, "empty", True):
        return out
    cols = data.columns
    if isinstance(cols, pd.MultiIndex):
        if "Close" not in cols.get_level_values(0):
            return out
        close = data["Close"]
        if isinstance(close, pd.Series):
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
            out[tickers[0]] = close.dropna()
    return out


def _download_prices_sync(tickers: list[str], period: str) -> dict[str, pd.Series]:
    try:
        data = yf.download(
            " ".join(tickers), period=period, interval="1d",
            progress=False, auto_adjust=True, group_by="column", threads=True,
        )
    except Exception as exc:
        logger.warning("Optimizer history download failed (%d tickers): %s", len(tickers), exc)
        return {}
    return _parse_close(data, tickers)


async def _get_series_cached(tickers: list[str], lookback: str) -> dict[str, pd.Series]:
    """Download close series, memoized in-process (6 h) by (tickers, lookback)."""
    key = (tuple(sorted(tickers)), lookback)
    now = _time.monotonic()
    hit = _history_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    series = await asyncio.to_thread(_download_prices_sync, tickers, lookback)
    if series:
        _history_cache[key] = (now + _HISTORY_TTL, series)
    return series


def _latest_prices_sync(tickers: list[str]) -> dict[str, float]:
    """Best-effort live-ish price per ticker via fast_info (for $ sizing)."""
    out: dict[str, float] = {}
    for t in tickers:
        try:
            fi = yf.Ticker(t).fast_info
            p = getattr(fi, "last_price", None)
            if p:
                out[t.upper()] = float(p)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Trade list
# ---------------------------------------------------------------------------

def _build_trades(
    weights: dict[str, float],
    latest: pd.Series,
    current_shares: dict[str, float],
    current_value: dict[str, float],
    total_value: float,
) -> tuple[list[dict], float]:
    """Turn target weights into an integer-share buy/sell list vs. the book."""
    alloc: dict[str, int] = {}
    leftover = total_value
    # Only allocate names with a meaningful target weight.
    target_nonzero = {t: w for t, w in weights.items() if w > 1e-4}
    if target_nonzero and total_value > 0:
        try:
            da = DiscreteAllocation(
                target_nonzero,
                latest.reindex(target_nonzero.keys()).dropna(),
                total_portfolio_value=total_value,
            )
            alloc, leftover = da.greedy_portfolio()  # pure-python, no solver
        except Exception as exc:
            logger.warning("DiscreteAllocation failed: %s", exc)
            alloc, leftover = {}, total_value

    trades: list[dict] = []
    for ticker in sorted(set(current_shares) | set(weights)):
        price = _f(latest.get(ticker), 4)
        cur_sh = float(current_shares.get(ticker, 0.0))
        tgt_sh = float(alloc.get(ticker, 0))
        delta = tgt_sh - cur_sh
        if abs(delta) <= _HOLD_EPS:
            action = "HOLD"
        else:
            action = "BUY" if delta > 0 else "SELL"
        cur_val = current_value.get(ticker, 0.0)
        tgt_val = (tgt_sh * price) if price else None
        trades.append({
            "ticker": ticker,
            "action": action,
            "price": price,
            "current_shares": _f(cur_sh, 4),
            "target_shares": int(tgt_sh),
            "delta_shares": _f(delta, 4),
            "current_weight": _pct(cur_val / total_value if total_value else 0),
            "target_weight": _pct(weights.get(ticker, 0.0)),
            "current_value": _f(cur_val, 2),
            "target_value": _f(tgt_val, 2),
        })
    # Biggest moves first.
    trades.sort(key=lambda r: abs(r["delta_shares"] or 0) * (r["price"] or 0), reverse=True)
    return trades, _f(leftover, 2) or 0.0


# ---------------------------------------------------------------------------
# Expected returns
# ---------------------------------------------------------------------------

def _expected_returns(prices: pd.DataFrame, benchmark: Optional[pd.Series], rf: float) -> pd.Series:
    """Annualized expected returns via CAPM, benchmarked to SPY.

    Raw ``mean_historical_return`` annualizes the average daily return, so a name
    that had a big run explodes (a ~6x name → ~600%/yr), which then dominates the
    max-Sharpe point and the frontier height. CAPM (rf + β·equity-risk-premium) is
    bounded by the name's beta to the market, so it stays realistic. Falls back to
    CAPM vs the book's own average, then to a hard-clipped historical mean.

    NB: only the *display* (frontier / max-Sharpe / expected-return stats) depends
    on this — min-vol and HRP weights don't use expected returns at all.
    """
    try:
        if benchmark is not None and len(benchmark) > 0:
            mkt = benchmark.reindex(prices.index).dropna()
            if len(mkt) >= _MIN_OBS:
                return expected_returns.capm_return(
                    prices, market_prices=mkt.to_frame(),
                    risk_free_rate=rf, frequency=_TRADING_DAYS,
                )
    except Exception as exc:
        logger.info("CAPM(SPY) expected returns failed (%s); trying default market.", exc)
    try:
        return expected_returns.capm_return(prices, risk_free_rate=rf, frequency=_TRADING_DAYS)
    except Exception as exc:
        logger.info("CAPM(default) failed (%s); clipping historical mean.", exc)
        mu = expected_returns.mean_historical_return(prices, frequency=_TRADING_DAYS)
        return mu.clip(lower=-0.9, upper=1.5)  # last-resort guard against absurd μ


# ---------------------------------------------------------------------------
# Frontier curve
# ---------------------------------------------------------------------------

def _frontier_points(mu: pd.Series, S: pd.DataFrame, rf: float) -> list[dict]:
    """Sample (volatility, return) along the long-only efficient frontier."""
    pts: list[dict] = []
    try:
        lo = EfficientFrontier(mu, S, weight_bounds=(0, 1))
        lo.min_volatility()
        r_lo, _, _ = lo.portfolio_performance(risk_free_rate=rf)
        r_hi = float(mu.max()) * 0.999  # long-only can't beat the best single name
        if not (math.isfinite(r_lo) and math.isfinite(r_hi)) or r_hi <= r_lo:
            return pts
        for target in np.linspace(r_lo, r_hi, _FRONTIER_POINTS):
            try:
                ef = EfficientFrontier(mu, S, weight_bounds=(0, 1))
                ef.efficient_return(float(target))
                ret, vol, _ = ef.portfolio_performance(risk_free_rate=rf)
                pts.append({"volatility": _pct(vol), "expected_return": _pct(ret)})
            except Exception:
                continue
    except Exception as exc:
        logger.warning("Frontier sampling failed: %s", exc)
    return pts


# ---------------------------------------------------------------------------
# Constrained / turnover-aware minimum-variance
# ---------------------------------------------------------------------------

def _turnover(w_target: dict, current_w: dict) -> float:
    """One-way turnover fraction = ½·Σ|w_target − w_current| (0 = no trades)."""
    names = set(w_target) | set(current_w)
    return 0.5 * sum(
        abs(float(w_target.get(n, 0.0)) - float(current_w.get(n, 0.0))) for n in names
    )


def _min_vol_weights(
    mu: pd.Series, S: pd.DataFrame, keep: list[str], rf: float, adjustments: list[str], *,
    max_weight: Optional[float], sector_max: Optional[float],
    sector_map: Optional[dict], tc_bps: float, l2_gamma: float,
    w_prev: Optional[np.ndarray],
) -> tuple[dict, float, float, float]:
    """Solve minimum-variance with optional caps / turnover penalty (cvxpy).

    Returns (weights, expected_return, volatility, sharpe). Raises if the
    constrained program is infeasible — the caller relaxes and retries.
    """
    n = len(keep)
    upper = 1.0
    if max_weight is not None:
        upper = float(max_weight)
        floor = 1.0 / n
        if upper < floor - 1e-12:  # can't fully invest long-only if cap < 1/N
            adjustments.append(
                f"per-name cap raised to {round(floor * 100, 1)}% (1/N) so the book stays fully invested"
            )
            upper = floor

    ef = EfficientFrontier(mu, S, weight_bounds=(0, upper))
    if l2_gamma and l2_gamma > 0:
        ef.add_objective(objective_functions.L2_reg, gamma=float(l2_gamma))
    if tc_bps and tc_bps > 0 and w_prev is not None:
        # transaction_cost penalizes L1 distance from w_prev by k (per unit turnover)
        ef.add_objective(objective_functions.transaction_cost, w_prev=w_prev, k=float(tc_bps) / 10000.0)
    if sector_max is not None and sector_map:
        mapper = {t: (sector_map.get(t) or "Other") for t in keep}
        upper_by_sector = {s: float(sector_max) for s in set(mapper.values())}
        ef.add_sector_constraints(mapper, {}, upper_by_sector)

    ef.min_volatility()
    w = {k: float(v) for k, v in ef.clean_weights().items()}
    r, v, sh = ef.portfolio_performance(risk_free_rate=rf)
    return w, r, v, sh


def _min_vol_robust(
    mu, S, keep, rf, opts: dict, adjustments: list[str],
) -> tuple[dict, float, float, float]:
    """Try the fully-constrained solve; relax sector caps, then everything,
    if the problem is infeasible — so the endpoint always returns *a* portfolio."""
    try:
        return _min_vol_weights(mu, S, keep, rf, adjustments, **opts)
    except Exception as exc:
        logger.info("Constrained min-vol infeasible (%s); relaxing.", exc)
    if opts.get("sector_max") is not None:
        adjustments.append("sector caps dropped — infeasible for this book")
        try:
            return _min_vol_weights(mu, S, keep, rf, adjustments, **{**opts, "sector_max": None})
        except Exception:
            pass
    adjustments.append("relaxed to plain minimum-variance — constraints were infeasible")
    return _min_vol_weights(
        mu, S, keep, rf, adjustments,
        max_weight=None, sector_max=None, sector_map=None, tc_bps=0.0, l2_gamma=0.0, w_prev=None,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _not_available(reason: str, excluded: list[dict] | None = None) -> dict:
    return {"available": False, "reason": reason, "excluded": excluded or []}


async def optimize_portfolio(
    holdings: Iterable,
    *,
    risk_free_rate: float = 0.045,
    lookback: str = _DEFAULT_LOOKBACK,
    max_weight: Optional[float] = None,
    sector_max: Optional[float] = None,
    sector_map: Optional[dict] = None,
    transaction_cost_bps: float = 0.0,
    l2_gamma: float = 0.0,
) -> dict:
    """Run min-vol + HRP on the user's holdings and build the response payload.

    ``holdings`` is any iterable of objects exposing ``.ticker``, ``.shares`` and
    ``.asset_type`` (i.e. ``PortfolioHolding`` rows).

    Constraints below apply to the **minimum-volatility** (mean-variance) target
    only — HRP is a clustering allocator and doesn't take weight/sector caps:

    * ``max_weight``     — per-name cap as a fraction (e.g. ``0.10`` = 10%).
    * ``sector_max``     — per-sector cap as a fraction; needs ``sector_map``.
    * ``sector_map``     — ``{ticker: sector}``; names absent map to "Other".
    * ``transaction_cost_bps`` — L1 turnover penalty vs. the current book, in
      basis points; higher ⇒ fewer/smaller trades.
    * ``l2_gamma``       — L2 weight-dispersion penalty (kills tiny corners).
    """
    if not _PFOPT_AVAILABLE:
        return _not_available(
            f"Optimization engine not installed on this image "
            f"({_PFOPT_IMPORT_ERROR or 'PyPortfolioOpt missing'}). Rebuild the "
            f"Docker image after the requirements bump."
        )

    # 1) Aggregate shares per ticker and split out non-optimizable asset types.
    shares_by_ticker: dict[str, float] = {}
    excluded: list[dict] = []
    excluded_seen: set[str] = set()
    for h in holdings:
        ticker = (h.ticker or "").upper().strip()
        if not ticker or (h.shares or 0) <= 0:
            continue
        atype = (getattr(h, "asset_type", None) or "STOCK").upper()
        if atype not in _OPTIMIZABLE_TYPES:
            if ticker not in excluded_seen:
                excluded.append({"ticker": ticker, "reason": f"{atype.lower()} not supported"})
                excluded_seen.add(ticker)
            continue
        shares_by_ticker[ticker] = shares_by_ticker.get(ticker, 0.0) + float(h.shares)

    if len(shares_by_ticker) < 2:
        return _not_available(
            "Need at least 2 stock/ETF/fund holdings to optimize.", excluded
        )

    tickers = sorted(shares_by_ticker)
    dl_tickers = sorted(set(tickers) | {_BENCHMARK})  # +benchmark for CAPM

    # 2) History (in-process cached) + latest prices (blocking → worker thread).
    series = await _get_series_cached(dl_tickers, lookback)
    benchmark_series = series.get(_BENCHMARK)
    latest_map = await asyncio.to_thread(_latest_prices_sync, tickers)

    keep: list[str] = []
    for t in tickers:
        s = series.get(t)
        if s is not None and len(s) >= _MIN_OBS:
            keep.append(t)
        elif t not in excluded_seen:
            excluded.append({"ticker": t, "reason": "insufficient price history"})
            excluded_seen.add(t)

    if len(keep) < 2:
        return _not_available(
            "Not enough overlapping price history among your holdings.", excluded
        )

    prices = pd.concat({t: series[t] for t in keep}, axis=1).dropna(how="any")
    if prices.shape[0] < _MIN_OBS or prices.shape[1] < 2:
        return _not_available(
            "Holdings don't share enough common trading days to build a covariance matrix.",
            excluded,
        )
    keep = list(prices.columns)

    # Latest price per name: prefer live fast_info, fall back to last close.
    latest = pd.Series(
        {t: latest_map.get(t, float(prices[t].iloc[-1])) for t in keep}
    )

    # 3) Current book weights + market value (from latest prices).
    current_shares = {t: shares_by_ticker[t] for t in keep}
    current_value = {t: current_shares[t] * float(latest[t]) for t in keep}
    total_value = sum(current_value.values())
    if total_value <= 0:
        return _not_available("Could not value the current holdings.", excluded)
    current_weights = {t: current_value[t] / total_value for t in keep}

    # 4) Expected returns + shrunk covariance (the mean-variance inputs).
    def _compute() -> dict:
        # CAPM (benchmarked to SPY) keeps expected returns realistic — raw
        # historical means explode for big-runup names and poison max-Sharpe.
        mu = _expected_returns(prices, benchmark_series, risk_free_rate)
        S = risk_models.CovarianceShrinkage(prices, frequency=_TRADING_DAYS).ledoit_wolf()

        # Current portfolio's risk/return on the same axes as the frontier.
        w_cur = np.array([current_weights[t] for t in keep])
        cur_ret = float(w_cur @ mu.values)
        cur_vol = float(np.sqrt(w_cur @ S.values @ w_cur))
        cur_sharpe = (cur_ret - risk_free_rate) / cur_vol if cur_vol > 0 else None

        results: dict[str, dict] = {}

        # --- Min-volatility (mean-variance) with optional caps / turnover cost ---
        try:
            adjustments: list[str] = []
            opts = {
                "max_weight": max_weight, "sector_max": sector_max, "sector_map": sector_map,
                "tc_bps": transaction_cost_bps, "l2_gamma": l2_gamma, "w_prev": w_cur,
            }
            w, r, v, sh = _min_vol_robust(mu, S, keep, risk_free_rate, opts, adjustments)
            trades, leftover = _build_trades(w, latest, current_shares, current_value, total_value)
            results["min_volatility"] = {
                "method": "min_volatility",
                "label": "Minimum Volatility",
                "weights": {k: _pct(val) for k, val in w.items()},
                "expected_return": _pct(r), "volatility": _pct(v), "sharpe": _f(sh, 3),
                "trades": trades, "leftover_cash": leftover,
                "turnover": _pct(_turnover(w, current_weights)),
                "constrained": bool(max_weight or sector_max or transaction_cost_bps or l2_gamma),
                "adjustments": adjustments,
            }
        except Exception as exc:
            logger.warning("min_volatility failed: %s", exc)

        # --- Hierarchical Risk Parity (unconstrained by construction) ---
        try:
            rets = prices.pct_change().dropna(how="any")
            hrp = HRPOpt(rets)
            hrp.optimize()
            w = {k: float(v) for k, v in hrp.clean_weights().items()}
            r, v, sh = hrp.portfolio_performance(risk_free_rate=risk_free_rate)
            trades, leftover = _build_trades(w, latest, current_shares, current_value, total_value)
            results["hrp"] = {
                "method": "hrp",
                "label": "Hierarchical Risk Parity",
                "weights": {k: _pct(val) for k, val in w.items()},
                "expected_return": _pct(r), "volatility": _pct(v), "sharpe": _f(sh, 3),
                "trades": trades, "leftover_cash": leftover,
                "turnover": _pct(_turnover(w, current_weights)),
                "constrained": False,
                "adjustments": [],
            }
        except Exception as exc:
            logger.warning("HRP failed: %s", exc)

        # --- Frontier + max-Sharpe marker (for the chart) ---
        frontier = _frontier_points(mu, S, risk_free_rate)
        markers: dict[str, dict] = {}
        try:
            ms = EfficientFrontier(mu, S, weight_bounds=(0, 1))
            ms.max_sharpe(risk_free_rate=risk_free_rate)
            r, v, _ = ms.portfolio_performance(risk_free_rate=risk_free_rate)
            markers["max_sharpe"] = {"volatility": _pct(v), "expected_return": _pct(r)}
        except Exception as exc:
            logger.warning("max_sharpe marker failed: %s", exc)

        return {
            "current": {
                "weights": {t: _pct(current_weights[t]) for t in keep},
                "expected_return": _pct(cur_ret),
                "volatility": _pct(cur_vol),
                "sharpe": _f(cur_sharpe, 3),
            },
            "targets": results,
            "frontier": frontier,
            "markers": markers,
        }

    payload = await asyncio.to_thread(_compute)

    if not payload["targets"]:
        return _not_available("Optimization did not converge for this set of holdings.", excluded)

    return {
        "available": True,
        "as_of": pd.Timestamp(prices.index[-1]).date().isoformat(),
        "lookback": lookback,
        "risk_free_rate": _pct(risk_free_rate),
        "total_value": _f(total_value, 2),
        "tickers": keep,
        "excluded": excluded,
        "settings": {
            "max_weight": _pct(max_weight) if max_weight is not None else None,
            "sector_max": _pct(sector_max) if sector_max is not None else None,
            "transaction_cost_bps": transaction_cost_bps or 0,
            "l2_gamma": l2_gamma or 0,
            "sectors_available": bool(sector_map),
        },
        **payload,
    }
