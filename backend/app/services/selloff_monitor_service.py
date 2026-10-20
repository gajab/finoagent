"""Selloff Monitor — "does the selloff continue, or does the market recover?"

Nobody knows the next session. This engine turns the question into four DETERMINISTIC
reads a desk would actually look at (no LLM, no invented relationships):

1. State strip      — how big the move was in the market's OWN volatility units (σ), where
                      price sits vs. its trend (50/200-day, 52-week high), the VIX curve,
                      credit spreads and dealer gamma.
2. Stress grid      — is the stress SPREADING? Last-5-session moves of equities, credit,
                      rates, vol, FX, commodities and crypto as z-scores vs. each asset's
                      own pre-window volatility, plus a rule-based diagnosis
                      (equity-only de-risking vs. broad risk-off).
3. History vs options fan — two independent 20-session forecasts of the S&P on ONE chart:
      • history: forward paths from the k closest NON-OVERLAPPING historical analogs of
        today's state (S&P 500 since 1990, k-NN on σ-scaled move + trend + VIX features),
        with sample size, Wilson intervals and the all-days baseline so a "58% up" is never
        shown without the "57% for any day" it must beat;
      • options: the SPY at-the-money straddle term structure → implied 1σ per horizon →
        the SAME percentiles (P10/P25/P50/P75/P90) under a lognormal law. Apples-to-apples.
4. Monday tells     — if-then levels and thresholds that would confirm each side, with live
                      status where data exists (futures-implied open, VIX curve, credit).

Index history comes from ^GSPC (long, clean); every PRICE LEVEL shown is in SPY terms so it
can be drawn on the tradable instrument's chart. Percent moves are identical between the two.
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

INDEX = "^GSPC"
TRADE = "SPY"
HISTORY_START = "1990-01-01"      # ^VIX starts 1990 — the analog features need it

H = 20                            # forward horizon (sessions)
K_ANALOGS = 40                    # closest non-overlapping analogs kept
WINDOW = 3                        # sessions that define "the move" (Wed–Thu–Fri)
VOL_LOOKBACK = 60                 # σ = std of the 60 daily log-returns BEFORE the window
STRESS_LOOKBACK = VOL_LOOKBACK    # same σ convention page-wide (state strip, analogs, grid)
GRID_SESSIONS = 5

# z for the fan percentiles (standard normal quantiles)
_Z = {"p10": -1.2815515655446004, "p25": -0.6744897501960817, "p50": 0.0,
      "p75": 0.6744897501960817, "p90": 1.2815515655446004}
_SQRT_HALF_PI = math.sqrt(math.pi / 2.0)   # 1σ = straddle · √(π/2) / S  (Brenner–Subrahmanyam)

# feature weights for the analog distance (σ-scaled move dominates)
_FEATURES = ("move3_sigma", "move1_sigma", "drawdown_52w", "dist_200d", "log_vix")
_WEIGHTS = np.array([1.5, 1.0, 1.0, 1.0, 1.0])

# (symbol, label, group, kind, stress_sign) — kind: pct (price, shown in %), pts (index level,
# shown in points), bp (yield, shown in bp).  stress_sign: +1 = rising is stress, −1 = falling
# is stress, 0 = context (direction is informative but not unambiguously stress).
STRESS_ASSETS: list[tuple[str, str, str, str, int]] = [
    ("SPY", "S&P 500 (SPY)", "Equities", "pct", -1),
    ("QQQ", "Nasdaq 100 (QQQ)", "Equities", "pct", -1),
    ("IWM", "Russell 2000 (IWM)", "Equities", "pct", -1),
    ("RSP", "S&P 500 equal-weight (RSP)", "Equities", "pct", -1),
    ("HYG", "High-yield bonds (HYG)", "Credit", "pct", -1),
    ("LQD", "Investment-grade bonds (LQD)", "Credit", "pct", 0),
    ("TLT", "20y+ Treasuries (TLT)", "Rates", "pct", 0),
    ("^TNX", "10-year yield", "Rates", "bp", 0),
    ("^VIX", "VIX", "Volatility", "pts", 1),
    ("^VVIX", "VVIX (vol of VIX)", "Volatility", "pts", 1),
    ("^MOVE", "MOVE (bond vol)", "Volatility", "pts", 1),
    ("DX-Y.NYB", "US dollar index", "FX", "pct", 0),
    ("JPY=X", "USD/JPY", "FX", "pct", -1),          # yen strengthening = carry unwind
    ("GC=F", "Gold", "Commodities", "pct", 0),
    ("CL=F", "Crude oil", "Commodities", "pct", 0),
    ("HG=F", "Copper", "Commodities", "pct", -1),
    ("BTC-USD", "Bitcoin", "Crypto", "pct", -1),
]
_EXTRA = ["^VIX3M", "ES=F", "NQ=F"]
GRID_SYMBOLS = [a[0] for a in STRESS_ASSETS] + _EXTRA


def _r(x, nd: int = 2):
    """Round; nan/inf/None → None so the payload JSON-encodes cleanly."""
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(xf):
        return None
    return round(xf, nd)


# ---------------------------------------------------------------------------
# NYSE calendar (forward session dates + expiry dates)
# ---------------------------------------------------------------------------

def _observed(d: _dt.date) -> _dt.date:
    if d.weekday() == 5:
        return d - _dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + _dt.timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    d = _dt.date(year, month, 1)
    d += _dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + _dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> _dt.date:
    d = _dt.date(year, month + 1, 1) - _dt.timedelta(days=1) if month < 12 else _dt.date(year, 12, 31)
    return d - _dt.timedelta(days=(d.weekday() - weekday) % 7)


def nyse_holidays(year: int) -> set[_dt.date]:
    from dateutil.easter import easter
    hol = {
        _observed(_dt.date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),                 # MLK
        _nth_weekday(year, 2, 0, 3),                 # Presidents
        easter(year) - _dt.timedelta(days=2),        # Good Friday
        _last_weekday(year, 5, 0),                   # Memorial
        _observed(_dt.date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),                 # Labor
        _nth_weekday(year, 11, 3, 4),                # Thanksgiving
        _observed(_dt.date(year, 12, 25)),
    }
    if year >= 2022:
        hol.add(_observed(_dt.date(year, 6, 19)))    # Juneteenth
    return hol


def is_session(d: _dt.date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def next_sessions(after: _dt.date, n: int) -> list[_dt.date]:
    out: list[_dt.date] = []
    d = after
    while len(out) < n:
        d += _dt.timedelta(days=1)
        if is_session(d):
            out.append(d)
    return out


def third_friday(year: int, month: int) -> _dt.date:
    return _nth_weekday(year, month, 4, 3)


def market_events(today: _dt.date) -> list[dict]:
    """Calendar mechanics that matter for the next sessions (pure date math)."""
    events: list[dict] = []
    tf = third_friday(today.year, today.month)
    if tf < today and (today - tf).days <= 7:
        events.append({"date": tf.isoformat(), "label": "Monthly options expiry (just passed)",
                       "note": "Hedges tied to the expired contracts are gone. Evidence for index-level pinning or a "
                               "post-expiry volatility release is weak (no pinning found in the S&P index — Golez & "
                               "Jackwerth 2012)."})
    nxt_m = today.month % 12 + 1
    nxt_y = today.year + (1 if today.month == 12 else 0)
    upcoming_opex = tf if tf >= today else third_friday(nxt_y, nxt_m)
    events.append({"date": upcoming_opex.isoformat(), "label": "Monthly options expiry",
                   "note": "Large open interest expires. Pinning is documented for single stocks, not the S&P index."})
    # VIX settlement: the Wednesday 30 days before the NEXT month's third Friday
    for y, m in ((today.year, today.month), (nxt_y, nxt_m)):
        mm, yy = m % 12 + 1, y + (1 if m == 12 else 0)
        vx = third_friday(yy, mm) - _dt.timedelta(days=30)
        if vx >= today:
            events.append({"date": vx.isoformat(), "label": "VIX futures/options expiry",
                           "note": "VIX futures/options settle. 'Vanna/charm' flow stories around it are practitioner "
                                   "lore without peer-reviewed support."})
            break
    return sorted(events, key=lambda e: e["date"])


# ---------------------------------------------------------------------------
# Data fetch (blocking; run in a threadpool)
# ---------------------------------------------------------------------------

def _close(df: pd.DataFrame, field: str, sym: str) -> pd.Series:
    key = (field, sym)
    if df is None or df.empty or key not in df.columns:
        return pd.Series(dtype=float)
    s = df[key].dropna()
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    s.index = pd.DatetimeIndex(s.index).normalize()
    return s.astype(float)


def fetch_market_data() -> dict[str, Any]:
    """Every network read the monitor needs, in one blocking call."""
    import yfinance as yf

    out: dict[str, Any] = {}
    try:
        hist = yf.download(f"{INDEX} ^VIX", start=HISTORY_START, interval="1d", progress=False,
                           auto_adjust=True, group_by="column", threads=True)
        out["index"] = pd.DataFrame({f: _close(hist, f, INDEX) for f in ("Open", "High", "Low", "Close")}).dropna()
        out["vix_hist"] = _close(hist, "Close", "^VIX")
    except Exception as exc:  # pragma: no cover — network
        logger.warning("selloff: index history failed: %s", exc)
        out["index"], out["vix_hist"] = pd.DataFrame(), pd.Series(dtype=float)

    try:
        grid = yf.download(" ".join(GRID_SYMBOLS), period="2y", interval="1d", progress=False,
                           auto_adjust=True, group_by="column", threads=True)
        out["closes"] = {s: _close(grid, "Close", s) for s in GRID_SYMBOLS}
        out["spy_ohlc"] = pd.DataFrame({f: _close(grid, f, TRADE) for f in ("Open", "High", "Low", "Close")}).dropna()
    except Exception as exc:  # pragma: no cover
        logger.warning("selloff: grid download failed: %s", exc)
        out["closes"], out["spy_ohlc"] = {}, pd.DataFrame()

    out["futures"] = {}
    for sym in ("ES=F", "NQ=F"):
        try:
            h = yf.Ticker(sym).history(period="5d", interval="1h")
            if h is not None and not h.empty:
                idx = h.index.tz_convert("America/New_York") if h.index.tz is not None else h.index
                out["futures"][sym] = pd.Series(h["Close"].values, index=idx).dropna()
        except Exception as exc:  # pragma: no cover
            logger.warning("selloff: %s intraday failed: %s", sym, exc)

    out["option_chains"] = []
    try:
        spy = yf.Ticker(TRADE)
        anchor = out["spy_ohlc"].index[-1].date() if len(out.get("spy_ohlc", [])) else _dt.date.today()
        for exp in select_cone_expiries(list(spy.options or []), anchor):
            try:
                ch = spy.option_chain(exp)
                out["option_chains"].append({"expiration": exp, "calls": _chain_rows(ch.calls),
                                             "puts": _chain_rows(ch.puts)})
            except Exception:
                continue
    except Exception as exc:  # pragma: no cover
        logger.warning("selloff: option chains failed: %s", exc)
    return out


def _chain_rows(df) -> list[dict]:
    if df is None or df.empty:
        return []
    cols = [c for c in ("strike", "lastPrice", "bid", "ask", "impliedVolatility") if c in df.columns]
    return df[cols].to_dict("records")


# ---------------------------------------------------------------------------
# 1. Index features (point-in-time; shared by "now" and every historical day)
# ---------------------------------------------------------------------------

def index_features(index: pd.DataFrame, vix: pd.Series) -> pd.DataFrame:
    """Per-day analog features with NO look-ahead (each row uses data up to that day)."""
    c = index["Close"].astype(float)
    lr = np.log(c).diff()
    # σ of the VOL_LOOKBACK daily log-returns that END before the 3-session window
    sigma_pre = lr.rolling(VOL_LOOKBACK).std().shift(WINDOW)
    move3 = np.log(c / c.shift(WINDOW))
    f = pd.DataFrame(index=index.index)
    f["close"] = c
    f["low"] = index["Low"].astype(float)
    f["ret1_pct"] = (c / c.shift(1) - 1) * 100
    f["ret3_pct"] = (c / c.shift(WINDOW) - 1) * 100
    f["sigma_pre_pct"] = sigma_pre * 100
    f["move1_sigma"] = lr / sigma_pre
    f["move3_sigma"] = move3 / (sigma_pre * math.sqrt(WINDOW))
    f["drawdown_52w"] = (c / c.rolling(252, min_periods=200).max() - 1) * 100
    f["dist_200d"] = (c / c.rolling(200).mean() - 1) * 100
    f["log_vix"] = np.log(vix.reindex(index.index).ffill())
    # Daniel & Moskowitz (2016) momentum "panic state" inputs: 24-month market return and 126-day variance
    f["ret504_pct"] = (c / c.shift(504) - 1) * 100
    f["var126"] = lr.rolling(126).var()
    f["vix"] = vix.reindex(index.index).ffill()
    return f


# ---------------------------------------------------------------------------
# 3a. Historical analogs
# ---------------------------------------------------------------------------

def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return centre - half, centre + half


def _first_passage(closes: np.ndarray, i: int, b: float, horizon: int) -> str:
    """Which close-to-close barrier (±b from close[i], as a fraction) is hit first."""
    up, dn = closes[i] * (1 + b), closes[i] * (1 - b)
    for j in range(i + 1, min(i + horizon, len(closes) - 1) + 1):
        if closes[j] >= up:
            return "up"
        if closes[j] <= dn:
            return "down"
    return "neither"


def _horizon_stats(fwd: np.ndarray) -> dict:
    n = int(len(fwd))
    if n == 0:
        return {"n": 0}
    k = int((fwd > 0).sum())
    lo, hi = _wilson(k, n)
    return {"n": n, "p_up": _r(k / n * 100, 1), "p_up_ci": [_r(lo * 100, 1), _r(hi * 100, 1)],
            "median_pct": _r(np.median(fwd), 2), "mean_pct": _r(np.mean(fwd), 2),
            "p10_pct": _r(np.percentile(fwd, 10), 2), "p90_pct": _r(np.percentile(fwd, 90), 2)}


def compare_to_baseline(st: dict, b: dict) -> dict:
    """Edge = analog P(up) − all-days P(up); significant only when the all-days rate falls OUTSIDE
    the analogs' 95% Wilson interval (n≈40 → roughly ±15 pts, so most gaps are noise)."""
    ci = st.get("p_up_ci") or [None, None]
    st["baseline_p_up"] = b.get("p_up")
    st["baseline_median_pct"] = b.get("median_pct")
    st["edge_pts"] = _r((st.get("p_up") or 0) - (b.get("p_up") or 0), 1)
    st["significant"] = bool(ci[0] is not None and b.get("p_up") is not None
                             and not (ci[0] <= b["p_up"] <= ci[1]))
    return st


def _norm_sf2(z: float) -> float:
    """Two-sided normal p-value."""
    return math.erfc(abs(z) / math.sqrt(2))


def _fan_factor(cal: dict | None, h: int, stress: bool) -> float | None:
    """Walk-forward fan-widening factor at session h (linear between the calibrated 1/5/20 points)."""
    ff = (cal or {}).get("fan_factor") or {}
    pts = []
    for hh in (1, 5, 20):
        blk = (ff.get(f"h{hh}") or {}).get("stress" if stress else "all")
        if blk and blk.get("factor"):
            pts.append((hh, blk["factor"]))
    if not pts:
        return None
    xs, ys = zip(*pts)
    return float(np.interp(h, xs, ys))


def find_analogs(feat: pd.DataFrame, k: int = K_ANALOGS, horizon: int = H, calibration: dict | None = None) -> dict:
    """k-NN on standardized features over every day with a complete forward window, keeping
    only matches whose forward windows don't overlap. NOTE: the weights multiply the SQUARED scaled
    difference, so move3's 1.5 is an effective scale of √1.5 ≈ 1.22 (documented, not tuned).

    Honesty layer (research-driven): analogs cluster in a few crises, so P(up) intervals come from an
    EPISODE bootstrap and significance uses a z that includes the overlapping baseline's variance
    inflation, Holm-adjusted across horizons; the displayed best estimate is shrunk toward the base rate
    by the walk-forward-tuned prior strength; the fan is widened by its walk-forward coverage factor."""
    from .selloff_calibration import episode_bootstrap, episodes, holm, quality_percentile, vif_overlap
    cols = list(_FEATURES)
    valid = feat[cols].notna().all(axis=1).to_numpy()
    n_all = len(feat)
    if n_all < 300 or not valid[-1]:
        return {"available": False, "reason": "Not enough index history to match the current state."}

    X = feat[cols].to_numpy(dtype=float)
    closes = feat["close"].to_numpy(dtype=float)
    lows = feat["low"].to_numpy(dtype=float)
    now = X[-1]
    cand = np.where(valid[: n_all - horizon])[0]          # full forward window only
    scale = np.nanstd(X[valid], axis=0)
    scale[scale == 0] = 1.0
    d = np.sqrt((((X[cand] - now) / scale) ** 2 * _WEIGHTS).sum(axis=1))
    order = cand[np.argsort(d)]
    dist_of = dict(zip(cand.tolist(), d.tolist()))

    picked: list[int] = []
    for i in order:
        if all(abs(i - j) > horizon for j in picked):
            picked.append(int(i))
            if len(picked) >= k:
                break
    if len(picked) < 10:
        return {"available": False, "reason": "Too few independent historical matches."}

    hs = np.arange(1, horizon + 1)
    paths = np.array([(closes[i + hs] / closes[i] - 1) * 100 for i in picked])   # (k, H)
    fan = [{"h": int(h), **{q: _r(np.percentile(paths[:, h - 1], float(q[1:])), 3) for q in _Z}}
           for h in hs]
    # walk-forward fan widening (the raw analog fan has historically been too narrow, esp. after selloffs)
    stress_now = bool(np.isfinite(feat["move3_sigma"].iloc[-1]) and feat["move3_sigma"].iloc[-1] <= -1.5)
    fan_cal, factors = [], []
    for f in fan:
        fac = _fan_factor(calibration if (calibration or {}).get("available") else None, f["h"], stress_now)
        if fac is None:
            fan_cal = None
            break
        factors.append(fac)
        fan_cal.append({"h": f["h"], **{q: _r(f["p50"] + fac * (f[q] - f["p50"]), 3) for q in _Z}})

    # all-days baseline over the same evaluable window (overlapping windows — it's a base rate)
    base_idx = np.where(valid[: n_all - horizon])[0]
    base = {}
    for h in (1, 5, horizon):
        fwd = (closes[base_idx + h] / closes[base_idx] - 1) * 100
        base[f"h{h}"] = _horizon_stats(fwd)

    stats = {f"h{h}": _horizon_stats(paths[:, h - 1]) for h in (1, 5, horizon)}
    for key, st in stats.items():
        compare_to_baseline(st, base[key])

    # --- honesty layer -------------------------------------------------------------------------
    picked_sorted = sorted(picked)
    eps = episodes(picked_sorted)
    pos = {i: j for j, i in enumerate(picked)}
    cal = calibration if (calibration or {}).get("available") else None
    m = None if cal is None else (math.inf if cal.get("m_is_inf") else cal.get("m"))
    zs, ps = [], []
    for h in (1, 5, horizon):
        st = stats[f"h{h}"]
        up = (paths[:, h - 1] > 0).astype(float)
        groups = [np.array([up[pos[i]] for i in g]) for g in eps]
        lo, hi, se = episode_bootstrap(groups)
        p_hat, p0 = st["p_up"] / 100, (st["baseline_p_up"] or 0) / 100
        n_base = int(len(base_idx))
        var = (se or 0) ** 2 + p0 * (1 - p0) * vif_overlap(h) / max(n_base, 1)
        z = (p_hat - p0) / math.sqrt(var) if var > 0 else 0.0
        st["p_up_ci_episode"] = [_r(lo * 100, 1), _r(hi * 100, 1)] if lo is not None else None
        st["z"] = _r(z, 2)
        st["wilson_significant"] = st["significant"]
        zs.append(z)
        ps.append(_norm_sf2(z))
        if m is not None:
            k_up = float(up.sum())
            st["p_up_shrunk"] = _r((p0 if math.isinf(m) else (k_up + m * p0) / (len(up) + m)) * 100, 1)
    for (h, st), p_adj, z in zip(((h, stats[f"h{h}"]) for h in (1, 5, horizon)), holm(ps), zs):
        st["p_holm"] = _r(p_adj, 4)
        st["significant"] = bool(p_adj < 0.05)
        st["strength"] = "strong" if st["significant"] and abs(z) >= 3 else "suggestive" if st["significant"] else "none"

    # a one-line filter baseline: every day whose 3-session move was at least as extreme as today's
    m3 = feat["move3_sigma"].to_numpy(dtype=float)
    m3_now = m3[-1]
    filt = base_idx[(m3[base_idx] <= m3_now) if m3_now < 0 else (m3[base_idx] >= m3_now)]
    filter_base = {}
    for h in (1, 5, horizon):
        fwd = closes[filt + h] / closes[filt] - 1
        filter_base[f"h{h}"] = {"n": int(len(filt)), "p_up": _r(float((fwd > 0).mean()) * 100, 1) if len(filt) else None}

    # era split: daily index autocorrelation turned negative after ~1999 (Baltussen, van Bekkum & Da 2019)
    cut = pd.Timestamp("2000-01-01")
    era = {}
    for name, sel in (("pre_2000", [j for j, i in enumerate(picked) if feat.index[i] < cut]),
                      ("from_2000", [j for j, i in enumerate(picked) if feat.index[i] >= cut])):
        era[name] = {"n": len(sel), **{f"p_up_h{h}": (_r(float((paths[sel, h - 1] > 0).mean()) * 100, 1) if sel else None)
                                        for h in (5, horizon)}}

    decades: dict[str, int] = {}
    for i in picked:
        dec = f"{feat.index[i].year // 10 * 10}s"
        decades[dec] = decades.get(dec, 0) + 1
    kth_today = float(max(dist_of[i] for i in picked))

    # path questions an investor actually asks
    new_low_5 = [bool(lows[i + 1: i + 6].min() < lows[i]) for i in picked]
    reclaim_20 = [bool(closes[i + 1: i + horizon + 1].max() >= closes[i - WINDOW]) for i in picked]
    worst_20 = np.array([(closes[i + 1: i + horizon + 1].min() / closes[i] - 1) * 100 for i in picked])

    def _barrier(i: int) -> float:
        return max(abs(closes[i] / closes[i - WINDOW] - 1), float(feat["sigma_pre_pct"].iloc[i]) / 100 * math.sqrt(WINDOW))

    fp = [_first_passage(closes, i, _barrier(i), horizon) for i in picked]
    base_fp = [_first_passage(closes, i, _barrier(i), horizon) for i in base_idx[::3]]   # thinned base

    def _share(xs, v) -> float | None:
        return _r(sum(1 for x in xs if x == v) / len(xs) * 100, 1) if xs else None

    nl_k, rc_k = sum(new_low_5), sum(reclaim_20)
    thin = base_idx[::3]
    base_nl = [bool(lows[i + 1: i + 6].min() < lows[i]) for i in thin]
    down_thin = [i for i in thin if closes[i] < closes[i - WINDOW]]       # any down-3-session day
    base_rc = [bool(closes[i + 1: i + horizon + 1].max() >= closes[i - WINDOW]) for i in down_thin]
    base_worst = np.array([(closes[i + 1: i + horizon + 1].min() / closes[i] - 1) * 100 for i in thin])
    barrier_now = max(abs(closes[-1] / closes[-1 - WINDOW] - 1),
                      float(feat["sigma_pre_pct"].iloc[-1]) / 100 * math.sqrt(WINDOW))
    path = {
        "p_new_low_5d": _r(nl_k / len(picked) * 100, 1),
        "p_new_low_5d_ci": [_r(x * 100, 1) for x in _wilson(nl_k, len(picked))],
        "p_reclaim_pre_move_20d": _r(rc_k / len(picked) * 100, 1),
        "p_reclaim_pre_move_20d_ci": [_r(x * 100, 1) for x in _wilson(rc_k, len(picked))],
        "baseline_p_new_low_5d": _r(np.mean(base_nl) * 100, 1) if base_nl else None,
        "baseline_p_reclaim_pre_move_20d": _r(np.mean(base_rc) * 100, 1) if base_rc else None,
        "worst_close_20d_median_pct": _r(np.median(worst_20), 2),
        "worst_close_20d_p10_pct": _r(np.percentile(worst_20, 10), 2),
        "baseline_worst_close_20d_median_pct": _r(np.median(base_worst), 2) if len(base_worst) else None,
        "baseline_worst_close_20d_p10_pct": _r(np.percentile(base_worst, 10), 2) if len(base_worst) else None,
        "baseline_note": ("Baselines: every 3rd day since 1990 (new low, worst close, first passage); "
                          "every 3rd day that was itself down over 3 sessions (reclaim)."),
        "first_passage": {
            "barrier_pct": _r(barrier_now * 100, 2),
            "up_first": _share(fp, "up"), "down_first": _share(fp, "down"), "neither": _share(fp, "neither"),
            "baseline_up_first": _share(base_fp, "up"), "baseline_down_first": _share(base_fp, "down"),
        },
    }

    idx = feat.index
    # per-analog outcomes the book view reprices on: the S&P move AND the VIX ratio that actually followed
    vix = feat["vix"].to_numpy(dtype=float)
    outcomes = {f"h{h}": [{"date": idx[i].date().isoformat(), "ret_pct": _r(paths[k, h - 1], 3),
                           "vix_ratio": _r(vix[i + h] / vix[i], 4) if vix[i] > 0 and np.isfinite(vix[i + h]) else None}
                          for k, i in enumerate(picked)]
                for h in (1, 5, horizon)}
    top = sorted(picked, key=lambda i: dist_of[i])[:10]
    matches = [{
        "date": idx[i].date().isoformat(), "distance": _r(dist_of[i], 2),
        "ret3_pct": _r(feat["ret3_pct"].iloc[i], 2), "move3_sigma": _r(feat["move3_sigma"].iloc[i], 2),
        "dist_200d": _r(feat["dist_200d"].iloc[i], 1), "drawdown_52w": _r(feat["drawdown_52w"].iloc[i], 1),
        "vix": _r(feat["vix"].iloc[i], 1),
        "fwd5_pct": _r(paths[picked.index(i), 4], 2), "fwd20_pct": _r(paths[picked.index(i), horizon - 1], 2),
        "worst20_pct": _r(worst_20[picked.index(i)], 2),
    } for i in top]

    return {
        "available": True, "n": len(picked), "k_target": k, "horizon": horizon,
        "since": idx[0].date().isoformat(), "through": idx[n_all - 1 - horizon].date().isoformat(),
        "median_distance": _r(np.median([dist_of[i] for i in picked]), 2),
        "features_now": {c: _r(feat[c].iloc[-1], 2) for c in ("ret1_pct", "ret3_pct", "move1_sigma", "move3_sigma",
                                                              "drawdown_52w", "dist_200d", "vix", "sigma_pre_pct")},
        "fan_pct": fan_cal or fan, "fan_pct_raw": fan, "fan_calibrated": fan_cal is not None,
        "fan_factor": {"h1": _r(factors[0], 3), "h5": _r(factors[4], 3), f"h{horizon}": _r(factors[-1], 3),
                       "regime": "stress" if stress_now else "all"} if fan_cal else None,
        "stats": stats, "baseline": base, "filter_baseline": filter_base, "era": era,
        "episodes": {"n": len(eps), "gap_sessions": 60, "decades": decades,
                     "largest": max(len(g) for g in eps) if eps else 0},
        "quality": {"kth_distance": _r(kth_today, 3),
                    "percentile": quality_percentile(cal, kth_today) if cal else None},
        "calibration": None,
        "path": path, "matches": matches, "outcomes": outcomes,
        "method": ("k-nearest historical days (S&P 500 since 1990) on: 3-session move in σ, last-session move "
                   "in σ, drawdown from 52-week high, distance to 200-day, log VIX. Forward windows of the "
                   "kept matches never overlap, so n counts independent episodes."),
    }


# ---------------------------------------------------------------------------
# 3b. Options-implied cone (SPY straddle term structure)
# ---------------------------------------------------------------------------

def select_cone_expiries(exps: list[str], anchor: _dt.date, targets=(1, 7, 14, 28)) -> list[str]:
    """Nearest listed expiry to each calendar-day target after the anchor session (deduped)."""
    dated = []
    for e in exps:
        try:
            dte = (_dt.date.fromisoformat(e) - anchor).days
        except ValueError:
            continue
        if dte >= 1:
            dated.append((dte, e))
    if not dated:
        return []
    out: list[str] = []
    for t in targets:
        e = min(dated, key=lambda x: (abs(x[0] - t), x[0]))[1] if t > 1 else min(dated)[1]
        if e not in out:
            out.append(e)
    return out


def _opt_price(row: dict) -> tuple[float | None, str]:
    bid, ask, last = (float(row.get(k) or 0) for k in ("bid", "ask", "lastPrice"))
    if bid > 0 and ask >= bid:
        return (bid + ask) / 2, "mid"
    if last > 0:
        return last, "last"
    return None, "none"


def atm_straddle(calls: list[dict], puts: list[dict], spot: float) -> tuple[float | None, str]:
    """Straddle value AT spot: linear interpolation between the two listed strikes bracketing
    spot (each strike needs both a call and a put price). Source is "mid" only when every
    quote used was a two-sided mid; otherwise "last" (e.g. weekends, when bid/ask are 0)."""
    cp = {float(r["strike"]): _opt_price(r) for r in calls}
    pp = {float(r["strike"]): _opt_price(r) for r in puts}
    strikes = sorted(k for k in cp if k in pp and cp[k][0] is not None and pp[k][0] is not None)
    if not strikes:
        return None, "none"

    def _src(ks) -> str:
        return "mid" if all(cp[k][1] == "mid" and pp[k][1] == "mid" for k in ks) else "last"

    below = [k for k in strikes if k <= spot]
    above = [k for k in strikes if k >= spot]
    if below and above:
        k0, k1 = below[-1], above[0]
        s0 = cp[k0][0] + pp[k0][0]
        if k1 == k0:
            return s0, _src([k0])
        s1 = cp[k1][0] + pp[k1][0]
        w = (spot - k0) / (k1 - k0)
        return s0 + w * (s1 - s0), _src([k0, k1])
    k = min(strikes, key=lambda x: abs(x - spot))
    return cp[k][0] + pp[k][0], _src([k])


def options_cone(chains: list[dict], spot: float, anchor: _dt.date,
                 sessions: list[_dt.date]) -> dict:
    """Implied 1σ (total, fraction) per expiry from the ATM straddle, then total variance
    linearly interpolated in calendar time to each forward session → lognormal percentiles."""
    points = []
    for ch in chains:
        try:
            exp = _dt.date.fromisoformat(ch["expiration"])
        except (KeyError, ValueError):
            continue
        T = (exp - anchor).days
        strad, src = atm_straddle(ch.get("calls") or [], ch.get("puts") or [], spot)
        if T <= 0 or not strad or strad <= 0:
            continue
        s = strad * _SQRT_HALF_PI / spot
        points.append({"expiration": exp.isoformat(), "days": T, "straddle": _r(strad, 2),
                       "sigma_total_pct": _r(s * 100, 2),
                       "sigma_annual_pct": _r(s / math.sqrt(T / 365.0) * 100, 1), "price_source": src,
                       "_w": s * s})
    points.sort(key=lambda p: p["days"])
    if not points:
        return {"available": False, "reason": "No usable SPY option prices for the cone."}

    days = np.array([p["days"] for p in points], dtype=float)
    w = np.array([p["_w"] for p in points])

    def _w_at(T: float) -> float:
        if T <= days[0]:
            return w[0] * T / days[0]                      # from the origin (w(0)=0)
        if T >= days[-1]:
            return w[-1] * T / days[-1]                    # flat implied vol beyond the last
        return float(np.interp(T, days, w))

    fan = []
    for h, d in enumerate(sessions, start=1):
        wt = _w_at(float((d - anchor).days))
        sd = math.sqrt(wt)
        fan.append({"h": h, **{q: _r((math.exp(-wt / 2 + z * sd) - 1) * 100, 3) for q, z in _Z.items()},
                    "sigma_pct": _r(sd * 100, 3)})
    for p in points:
        p.pop("_w", None)
    return {"available": True, "points": points, "fan_pct": fan,
            "method": ("SPY at-the-money straddle per expiry → implied 1σ = straddle·√(π/2)/spot; total "
                       "variance interpolated linearly in calendar days; lognormal percentiles. "
                       "Risk-neutral: implied vol usually runs above realized, and skew is ignored.")}


# ---------------------------------------------------------------------------
# 2. Cross-asset stress grid
# ---------------------------------------------------------------------------

def _changes(s: pd.Series, kind: str) -> pd.Series:
    """Per-observation change in the unit used for z-scores (log-return for prices/levels,
    difference for yields/spreads quoted in %)."""
    if kind == "bp":
        return s.diff()
    return np.log(s / s.shift(1))


def _raw_move(kind: str, prev: float, cur: float) -> float | None:
    if kind == "bp":
        return cur - prev
    if prev > 0 and cur > 0:
        return math.log(cur / prev)
    return None


def _display_change(prev: float | None, cur: float | None, kind: str) -> float | None:
    if prev is None or cur is None or not np.isfinite(prev) or not np.isfinite(cur):
        return None
    if kind == "pct":
        return (cur / prev - 1) * 100 if prev else None
    if kind == "pts":
        return cur - prev
    return (cur - prev) * 100                                 # % → bp


def _snap(s: pd.Series, d: pd.Timestamp, strict: bool = False):
    """Last observation at (or strictly before) ``d`` → (date, value)."""
    sub = s[s.index < d] if strict else s[s.index <= d]
    if sub.empty:
        return None, None
    return sub.index[-1], float(sub.iloc[-1])


def _cell(s: pd.Series, kind: str, sign: int, sd: float | None, prev, cur) -> dict:
    """One move between two snapshots. z uses σ·√(own observations between them), so a
    Friday→Monday Bitcoin move (3 daily prints) isn't scored like a 1-day move, and a
    snapshot with no new print (holiday, FRED lag) shows no change instead of a fake 0."""
    (pd_, pv), (cd_, cv) = prev, cur
    if pv is None or cv is None or pd_ is None or cd_ is None:
        return {"value": _r(cv, 3), "change": None, "z": None, "stress": None, "obs_date": None}
    steps = int(((s.index > pd_) & (s.index <= cd_)).sum())
    if steps == 0:
        return {"value": _r(cv, 3), "change": None, "z": None, "stress": None,
                "obs_date": cd_.date().isoformat()}
    raw = _raw_move(kind, pv, cv)
    z = raw / (sd * math.sqrt(steps)) if raw is not None and sd else None
    return {"value": _r(cv, 3), "change": _r(_display_change(pv, cv, kind), 2), "z": _r(z, 2),
            "stress": _r(z * sign, 2) if z is not None and sign else None,
            "obs_date": cd_.date().isoformat()}


def stress_grid(closes: dict[str, pd.Series], sessions: list[pd.Timestamp],
                hy_oas: pd.Series | None) -> dict:
    """Last-N-session moves as z-scores vs. each asset's own volatility over the
    STRESS_LOOKBACK observations BEFORE the move window (so the shock can't dilute its own z).
    Every cell compares consecutive snapshots taken at the equity-session dates."""
    closes = dict(closes)
    assets = list(STRESS_ASSETS)
    if hy_oas is not None and len(hy_oas) > STRESS_LOOKBACK:
        closes["HY OAS"] = hy_oas.astype(float)
        assets.append(("HY OAS", "High-yield spread (OAS)", "Credit", "bp", 1))
    last = sessions[-1]
    window_first = sessions[-WINDOW]
    rows_by_group: dict[str, list[dict]] = {}
    for sym, label, group, kind, sign in assets:
        s = closes.get(sym)
        if s is None or s.empty:
            continue
        s = s.dropna().astype(float)
        ch = _changes(s, kind).dropna()
        pre = ch[ch.index < sessions[-WINDOW]].tail(STRESS_LOOKBACK)
        sd = float(pre.std()) if len(pre) >= 20 else None

        cells = []
        prev = _snap(s, sessions[0], strict=True)
        for d in sessions:
            cur = _snap(s, d)
            cells.append({"date": d.date().isoformat(), **_cell(s, kind, sign, sd, prev, cur)})
            prev = cur
        w = _cell(s, kind, sign, sd, _snap(s, window_first, strict=True), _snap(s, last))
        now = None
        if s.index[-1] > last:
            now = {"date": s.index[-1].date().isoformat(),
                   **_cell(s, kind, sign, sd, _snap(s, last), (s.index[-1], float(s.iloc[-1])))}
        row = {"symbol": sym, "label": label, "kind": kind, "stress_sign": sign, "cells": cells, "now": now,
               "window": {k: w[k] for k in ("change", "z", "stress")}}
        if sym == "HY OAS":
            row["as_of"] = s.index[-1].date().isoformat()
            row["note"] = "ICE BofA US High Yield OAS via FRED — published with a one-day lag."
        rows_by_group.setdefault(group, []).append(row)

    # VIX term structure is read as a LEVEL (contango vs inversion), not a change
    vix, vix3m = closes.get("^VIX"), closes.get("^VIX3M")
    if vix is not None and vix3m is not None and not vix.empty and not vix3m.empty:
        ratio = (vix / vix3m.reindex(vix.index).ffill()).dropna()
        cells = []
        for d in sessions:
            _, v = _snap(ratio, d)
            cells.append({"date": d.date().isoformat(), "value": _r(v, 3), "change": None, "z": None,
                          "stress": _r((v - 0.9) / 0.05, 2) if v is not None else None})
        rows_by_group.setdefault("Volatility", []).append({
            "symbol": "VIX/VIX3M", "label": "VIX curve (VIX ÷ VIX3M)", "kind": "ratio", "stress_sign": 1,
            "cells": cells, "now": None, "window": {"change": None, "z": None, "stress": None},
            "note": "Level, not change: below 0.90 = calm contango; 1.00 or above = inverted "
                    "(near-term fear priced above 3-month).",
        })

    order = ["Equities", "Credit", "Rates", "Volatility", "FX", "Commodities", "Crypto"]
    groups = [{"group": g, "rows": rows_by_group[g]} for g in order if g in rows_by_group]
    spy_s = closes.get("SPY")
    return {"sessions": [d.date().isoformat() for d in sessions], "window_sessions": WINDOW,
            "groups": groups, "z_lookback": STRESS_LOOKBACK,
            "credit_model": credit_excess(spy_s, closes.get("HY OAS"), sessions),
            "fts": flight_to_safety(spy_s, closes.get("TLT"), sessions)}


FTS_KAPPA = 1.26


def credit_excess(spy: pd.Series | None, oas: pd.Series | None, sessions: list, lookback: int = 250) -> dict | None:
    """Equities lead credit (Kwan 1996; Hilscher, Pollet & Wilson 2015), so a HY spread move only CONFIRMS
    stress when it exceeds what the equity move already implies. Daily ΔOAS (bp) is regressed on same-day SPY
    % returns over the ``lookback`` days before the window; excess = actual − (α·m + β·ΣSPY) over the window
    days that have an OAS print, z = excess / (σ_resid·√m)."""
    if spy is None or oas is None or len(spy) < 80 or len(oas) < 80:
        return None
    df = pd.concat([spy.pct_change() * 100, oas.diff() * 100], axis=1, join="inner").dropna()
    df.columns = ["r", "d"]
    first, last = sessions[-WINDOW], sessions[-1]
    pre = df[df.index < first].tail(lookback)
    if len(pre) < 60 or float(pre["r"].var()) <= 0:
        return None
    beta = float(np.cov(pre["r"], pre["d"], ddof=1)[0, 1] / pre["r"].var())
    alpha = float(pre["d"].mean() - beta * pre["r"].mean())
    resid_sd = float((pre["d"] - alpha - beta * pre["r"]).std(ddof=2))
    win = df[(df.index >= first) & (df.index <= last)]
    m = len(win)
    out = {"beta_bp_per_pct": _r(beta, 2), "days": m, "as_of": oas.index[-1].date().isoformat(),
           "stale": bool(oas.index[-1] < last), "lookback": len(pre)}
    if m == 0 or resid_sd <= 0:
        return {**out, "expected_bp": None, "actual_bp": None, "excess_bp": None, "excess_z": None}
    expected = alpha * m + beta * float(win["r"].sum())
    actual = float(win["d"].sum())
    return {**out, "expected_bp": _r(expected, 1), "actual_bp": _r(actual, 1), "excess_bp": _r(actual - expected, 1),
            "excess_z": _r((actual - expected) / (resid_sd * math.sqrt(m)), 2)}


def flight_to_safety(spy: pd.Series | None, tlt: pd.Series | None, sessions: list,
                     lookback: int = 250, skip: int = 5) -> dict | None:
    """Baele, Bekaert, Inghelbrecht & Wei (2020) joint-tail flight-to-safety test, per session: bonds up,
    stocks down, and the bivariate-normal density of the pair below that of (κσ_b, −κσ_s), κ = 1.26.
    σ and ρ come from ``lookback`` sessions ending ``skip`` sessions before the grid."""
    if spy is None or tlt is None or len(spy) < lookback + 20 or len(tlt) < lookback + 20:
        return None
    df = pd.concat([spy.pct_change() * 100, tlt.pct_change() * 100], axis=1, join="inner").dropna()
    df.columns = ["s", "b"]
    hist = df[df.index < sessions[0]]
    hist = hist.iloc[: max(0, len(hist) - skip)].tail(lookback)
    if len(hist) < 120:
        return None
    ss, sb = float(hist["s"].std()), float(hist["b"].std())
    rho = float(hist["s"].corr(hist["b"]))
    if ss <= 0 or sb <= 0 or not np.isfinite(rho) or abs(rho) >= 0.99:
        return None
    q_th = 2 * FTS_KAPPA ** 2 / (1 - rho)                      # Q at (κσ_b, −κσ_s)
    days, checked = [], []
    for d in sessions:
        if d not in df.index:
            continue
        x, y = float(df.at[d, "b"]), float(df.at[d, "s"])
        q = (x * x / (sb * sb) - 2 * rho * x * y / (sb * ss) + y * y / (ss * ss)) / (1 - rho * rho)
        hit = bool(x > 0 and y < 0 and q > q_th)
        checked.append({"date": d.date().isoformat(), "spy_pct": _r(y, 2), "tlt_pct": _r(x, 2), "q": _r(q, 2), "fts": hit})
        if hit:
            days.append(d.date().isoformat())
    return {"kappa": FTS_KAPPA, "rho_1y": _r(rho, 2), "q_threshold": _r(q_th, 2), "days": days, "checked": checked}


def _row(grid: dict, sym: str) -> dict | None:
    for g in grid.get("groups", []):
        for r in g["rows"]:
            if r["symbol"] == sym:
                return r
    return None


def diagnose_stress(grid: dict, index_move: dict | None = None) -> dict:
    """Rule-based read of WHAT KIND of selloff this is. Every rule is shown with its data."""
    def wz(sym):
        r = _row(grid, sym)
        return (r or {}).get("window", {}).get("z")

    def wchg(sym):
        r = _row(grid, sym)
        return (r or {}).get("window", {}).get("change")

    def last_val(sym):
        r = _row(grid, sym)
        cells = (r or {}).get("cells") or []
        return cells[-1]["value"] if cells else None

    pts: list[dict] = []
    spy_z, spy_chg = wz("SPY"), wchg("SPY")
    if index_move and index_move.get("move3_sigma") is not None:      # headline numbers (index)
        spy_z, spy_chg = index_move["move3_sigma"], index_move["ret3_pct"]
    equity_stress = spy_z is not None and spy_z <= -1.0
    pts.append({"lens": "Equities", "value": f"S&P 500 {spy_chg:+.2f}% over {WINDOW} sessions ({spy_z:+.1f}σ)"
                if spy_z is not None and spy_chg is not None else "S&P move unavailable",
                "tone": "stress" if equity_stress else "calm"})
    spy_etf = wchg("SPY")

    oas_chg = wchg("HY OAS")
    hyg_z = wz("HYG")
    cm = grid.get("credit_model") or {}
    if cm.get("excess_z") is not None:
        credit = ("confirming" if cm["excess_z"] >= 1.5 and cm["excess_bp"] >= 5 else
                  "softening" if cm["excess_z"] >= 0.5 else "calm")
        pts.append({"lens": "Credit", "value": f"HY spread {cm['actual_bp']:+.0f} bp vs {cm['expected_bp']:+.0f} bp implied by "
                    f"the equity move (excess {cm['excess_bp']:+.0f} bp, {cm['excess_z']:+.1f}σ)"
                    + (f"; FRED through {cm['as_of']}" if cm.get("stale") else ""),
                    "tone": {"confirming": "stress", "softening": "watch", "calm": "calm"}[credit]})
    elif oas_chg is not None:
        credit = "confirming" if oas_chg >= 15 or (hyg_z is not None and hyg_z <= -1.5) else \
                 "calm" if oas_chg <= 5 else "softening"
        pts.append({"lens": "Credit", "value": f"HY spread {oas_chg:+.0f} bp" +
                    (f", HYG {hyg_z:+.1f}σ" if hyg_z is not None else ""),
                    "tone": {"confirming": "stress", "softening": "watch", "calm": "calm"}[credit]})
    elif hyg_z is not None:
        credit = "confirming" if hyg_z <= -1.5 else "calm" if hyg_z > -0.75 else "softening"
        pts.append({"lens": "Credit", "value": f"HYG {hyg_z:+.1f}σ (spread data unavailable)",
                    "tone": {"confirming": "stress", "softening": "watch", "calm": "calm"}[credit]})
    else:
        credit = None

    vix_r = _row(grid, "^VIX")
    vix_w = (vix_r or {}).get("window", {})
    if vix_w.get("change") is not None and vix_w.get("z") is not None:
        vvix_z = wz("^VVIX")
        pts.append({"lens": "Volatility", "value": f"VIX {vix_w['change']:+.1f} pts ({vix_w['z']:+.1f}σ)" +
                    (f", VVIX {vvix_z:+.1f}σ" if vvix_z is not None else ""),
                    "tone": "stress" if vix_w["z"] >= 2 else "watch" if vix_w["z"] >= 1 else "calm"})

    ratio = last_val("VIX/VIX3M")
    vol = None
    if ratio is not None:
        vol = "inverted" if ratio >= 1.0 else "flattening" if ratio >= 0.9 else "contango"
        pts.append({"lens": "Vol curve", "value": f"VIX/VIX3M {ratio:.2f} ({vol})",
                    "tone": {"inverted": "stress", "flattening": "watch", "contango": "calm"}[vol]})

    tlt = wchg("TLT")
    fts = grid.get("fts") or {}
    if fts.get("days"):
        pts.append({"lens": "Rates", "value": "Flight to safety on " + ", ".join(fts["days"]) +
                    f" (Baele et al. joint-tail test, κ={fts['kappa']}: bonds up, stocks down, jointly extreme)",
                    "tone": "watch"})
    elif tlt is not None and spy_chg is not None and spy_chg < 0:
        if tlt > 0.3:
            pts.append({"lens": "Rates", "value": f"Treasuries {tlt:+.2f}% while stocks fell — bonds hedging, but no "
                        "extreme flight-to-safety day", "tone": "watch"})
        elif tlt < -0.3:
            pts.append({"lens": "Rates", "value": f"Treasuries {tlt:+.2f}% with stocks — both falling "
                        "(rates/inflation-driven)", "tone": "stress"})
        else:
            pts.append({"lens": "Rates", "value": f"Treasuries {tlt:+.2f}% — bonds not reacting", "tone": "calm"})

    if fts.get("rho_1y") is not None and fts["rho_1y"] > 0.2:
        pts.append({"lens": "Stock–bond", "value": f"1-year stock–bond correlation {fts['rho_1y']:+.2f}: bonds have not "
                    "been hedging stocks (inflation / real-rate regime)", "tone": "watch"})
    jpy = wchg("JPY=X")
    if jpy is not None:
        carry = jpy <= -1.5 and (vix_w.get("z") or 0) >= 1
        pts.append({"lens": "Yen", "value": f"USD/JPY {jpy:+.2f}%" +
                    (" — yen bid WITH a VIX jump (carry-unwind risk)" if carry else
                     " — yen bid (safe-haven flow; VIX not confirming a carry unwind)" if jpy <= -1.5 else ""),
                    "tone": "stress" if carry else "watch" if jpy <= -1.5 else "calm"})

    rsp, qqq, iwm = wchg("RSP"), wchg("QQQ"), wchg("IWM")
    if rsp is not None and spy_etf is not None:
        gap = rsp - spy_etf
        pts.append({"lens": "Breadth", "value": f"Equal-weight {gap:+.2f} pts vs S&P" +
                    (" — selling concentrated in mega-caps" if gap > 0.4 else
                     " — broad selling beyond mega-caps" if gap < -0.4 else ""),
                    "tone": "stress" if gap < -0.4 else "calm"})
    if qqq is not None and spy_etf is not None and abs(qqq - spy_etf) > 0.4:
        pts.append({"lens": "Leadership", "value": f"Nasdaq 100 {qqq - spy_etf:+.2f} pts vs S&P "
                    f"({'tech-led' if qqq < spy_etf else 'tech held up'})", "tone": "watch"})
    if iwm is not None and spy_etf is not None and abs(iwm - spy_etf) > 0.6:
        pts.append({"lens": "Small caps", "value": f"Russell 2000 {iwm - spy_etf:+.2f} pts vs S&P", "tone": "watch"})

    if not equity_stress:
        label, tone = "No significant equity stress", "calm"
        summary = "The index move is within about 1σ of its recent volatility."
    elif credit == "confirming" and vol == "inverted":
        label, tone = "Broad risk-off", "stress"
        summary = "Credit and the VIX curve confirm the equity selloff — stress is spreading across assets."
    elif credit in ("confirming", "softening") or vol in ("inverted", "flattening"):
        label, tone = "Stress building", "watch"
        summary = "Equities are under pressure and at least one other market (credit or the VIX curve) is starting to confirm."
    else:
        label, tone = "Equity-only de-risking", "watch"
        summary = "Stocks sold off but credit is calm and the VIX curve is in contango — stress has not spread."
    return {"label": label, "tone": tone, "summary": summary, "points": pts,
            "rules": ("Broad risk-off = S&P ≤ −1σ AND credit confirming (HY spread widening ≥1.5σ beyond what the equity "
                      "move implies — equities lead credit) AND VIX ≥ VIX3M. Stress building = S&P ≤ −1σ and credit or the VIX "
                      "curve partly confirming. Equity-only = S&P ≤ −1σ with calm credit and VIX contango. Flight to safety = "
                      "Baele et al. (2020) joint-tail test. Carry unwind = yen ≤ −1.5% AND VIX ≥ +1σ.")}


# ---------------------------------------------------------------------------
# Overnight futures
# ---------------------------------------------------------------------------

def overnight_futures(futures: dict[str, pd.Series], last_session: _dt.date, spy_close: float) -> dict:
    """Futures change since the 4 pm ET cash close of the last session → SPY-implied price."""
    out: dict[str, Any] = {"available": False}
    cutoff = pd.Timestamp(_dt.datetime.combine(last_session, _dt.time(16, 0)), tz="America/New_York")
    for sym, key in (("ES=F", "es"), ("NQ=F", "nq")):
        s = futures.get(sym)
        if s is None or s.empty:
            continue
        idx = s.index if s.index.tz is not None else s.index.tz_localize("America/New_York")
        s = pd.Series(s.values, index=idx)
        ref = s[s.index < cutoff]          # hourly bars are labeled by START: the 15:00 bar closes at 4 pm
        if ref.empty:
            continue
        ref_v = float(ref.iloc[-1])
        later = s[s.index >= cutoff]
        if later.empty:
            out[key] = {"trading": False, "ref": _r(ref_v, 2)}
            continue
        lv = float(later.iloc[-1])
        out[key] = {"trading": True, "ref": _r(ref_v, 2), "last": _r(lv, 2),
                    "change_pct": _r((lv / ref_v - 1) * 100, 2),
                    "as_of": later.index[-1].strftime("%a %b %d %H:%M ET")}
    es = out.get("es")
    if es and es.get("trading"):
        out["available"] = True
        out["spy_implied"] = _r(spy_close * (1 + es["change_pct"] / 100), 2)
    return out


# ---------------------------------------------------------------------------
# 4. Monday tells
# ---------------------------------------------------------------------------

def build_tells(levels: dict, grid: dict, dealer: dict | None, overnight: dict) -> list[dict]:
    px = overnight.get("spy_implied")             # futures-implied SPY, if futures are trading
    live = px is not None
    spot = levels["close"]
    ratio_row = _row(grid, "VIX/VIX3M")
    ratio = ratio_row["cells"][-1]["value"] if ratio_row and ratio_row["cells"] else None
    oas_row = _row(grid, "HY OAS")
    oas_5 = None
    if oas_row:
        vals = [c["change"] for c in oas_row["cells"] if c["change"] is not None]
        oas_5 = sum(vals) if vals else None
    jpy_row = _row(grid, "JPY=X")
    jpy_w = (jpy_row or {}).get("window", {}).get("change")
    vix_z = ((_row(grid, "^VIX") or {}).get("window") or {}).get("z")
    cm = grid.get("credit_model") or {}
    rsp, spy = _row(grid, "RSP"), _row(grid, "SPY")
    rsp_gap = None
    if rsp and spy and rsp["cells"][-1]["change"] is not None and spy["cells"][-1]["change"] is not None:
        rsp_gap = rsp["cells"][-1]["change"] - spy["cells"][-1]["change"]

    def price_tell(side, tid, label, level, below_is_met, detail):
        if level is None:
            return None
        ref = px if live else spot
        dist = (level / ref - 1) * 100 if ref else None
        if live:
            met = (px < level) if below_is_met else (px > level)
            status = "met" if met else "not_met"
        else:
            status = "watch"
        return {"id": tid, "side": side, "label": label, "level": _r(level, 2), "distance_pct": _r(dist, 2),
                "status": status, "detail": detail, "basis": "futures-implied SPY" if live else "Friday close"}

    tells: list[dict | None] = []
    # ---- continuation
    tells.append(price_tell("continuation", "break_low", "Breaks the last session's low", levels["session_low"], True,
                            "A lower low after a lower close means the selloff is still finding sellers."))
    if ratio is not None:
        tells.append({"id": "vix_curve", "side": "continuation", "label": "VIX curve inverts (VIX ≥ VIX3M)",
                      "level": None, "value": _r(ratio, 3), "threshold": 1.0, "unit": "",
                      "status": "met" if ratio >= 1.0 else "not_met",
                      "detail": "Near-term fear priced above 3-month fear is the classic panic signature.",
                      "basis": "last close"})
    if cm.get("excess_z") is not None:
        tells.append({"id": "credit_wide", "side": "continuation", "label": "Credit widens beyond what the equity move implies",
                      "level": None, "value": cm["excess_bp"], "threshold": 5, "unit": " bp excess",
                      "status": "met" if cm["excess_z"] >= 1.5 and cm["excess_bp"] >= 5 else "not_met",
                      "detail": f"HY spread {cm['actual_bp']:+.0f} bp vs {cm['expected_bp']:+.0f} bp expected from the S&P "
                                f"({cm['excess_z']:+.1f}σ). Spreads that widen MORE than stocks imply turn a pullback into a funding problem.",
                      "basis": f"FRED through {cm['as_of']}"})
    elif oas_5 is not None:
        tells.append({"id": "credit_wide", "side": "continuation", "label": "Credit widens (HY spread +20 bp in 5 sessions)",
                      "level": None, "value": _r(oas_5, 1), "threshold": 20, "unit": " bp",
                      "status": "met" if oas_5 >= 20 else "not_met",
                      "detail": "Credit leading equities lower turns a pullback into a funding problem.",
                      "basis": f"FRED through {oas_row.get('as_of')}"})
    ma_label, ma_level = (("50-day", levels["sma50"]) if levels["sma50"] and spot > levels["sma50"]
                          else ("200-day", levels["sma200"]))
    if ma_level is not None:
        if spot < ma_level and ma_label == "200-day":
            tells.append({"id": "trend", "side": "continuation", "label": "Already below the 200-day average",
                          "level": _r(ma_level, 2), "distance_pct": _r((ma_level / spot - 1) * 100, 2), "status": "met",
                          "detail": "Below the 200-day, dips have historically been less reliable to buy.",
                          "basis": "Friday close"})
        else:
            tells.append(price_tell("continuation", "trend", f"Closes below the {ma_label} average", ma_level, True,
                                    "Losing the trend average shifts the regime from pullback to correction."))
    if jpy_w is not None:
        tells.append({"id": "yen", "side": "continuation", "label": "Yen surges with a VIX jump (carry unwind)",
                      "level": None, "value": _r(jpy_w, 2), "threshold": -1.5, "unit": "%",
                      "status": "met" if jpy_w <= -1.5 and (vix_z or 0) >= 1 else "not_met",
                      "detail": "USD/JPY −1.5% or more in 3 sessions AND VIX up ≥1σ. A yen rally alone is a safe-haven flow; "
                                "together with a vol spike it forces carry-trade unwinds that spill into equities.",
                      "basis": "latest"})
    # ---- recovery
    tells.append(price_tell("recovery", "retake_high", "Takes back the last session's high", levels["session_high"], False,
                            "Reversing the whole last selloff day shows buyers absorbed the supply."))
    tells.append(price_tell("recovery", "reclaim", "Reclaims the pre-selloff close", levels["pre_move_close"], False,
                            f"Erases the {WINDOW}-session move — a full V-recovery."))
    if ratio is not None:
        tells.append({"id": "vix_calm", "side": "recovery", "label": "VIX curve back in contango (< 0.90)",
                      "level": None, "value": _r(ratio, 3), "threshold": 0.9, "unit": "",
                      "status": "met" if ratio < 0.9 else "not_met",
                      "detail": "Near-term vol priced well below 3-month vol means the market isn't paying up for protection.",
                      "basis": "last close"})
    if cm.get("excess_z") is not None:
        tells.append({"id": "credit_calm", "side": "recovery", "label": "Credit no worse than the equity move implies",
                      "level": None, "value": cm["excess_bp"], "threshold": None, "unit": " bp excess",
                      "status": "met" if cm["excess_z"] <= 0.5 else "not_met",
                      "detail": "Equity selloffs that credit doesn't amplify have tended to stay equity-only.",
                      "basis": f"FRED through {cm['as_of']}"})
    elif oas_5 is not None:
        tells.append({"id": "credit_calm", "side": "recovery", "label": "Credit stays calm (HY spread ≤ +5 bp in 5 sessions)",
                      "level": None, "value": _r(oas_5, 1), "threshold": 5, "unit": " bp",
                      "status": "met" if oas_5 <= 5 else "not_met",
                      "detail": "Equity selloffs that credit ignores have tended to be shallower.",
                      "basis": f"FRED through {oas_row.get('as_of')}"})
    tells.append({"id": "breadth", "side": "recovery", "label": "Equal-weight beats cap-weight on the bounce",
                  "level": None, "value": _r(rsp_gap, 2), "threshold": 0, "unit": " pts",
                  "status": "watch",
                  "detail": "A bounce led by the average stock (RSP > SPY) is broader and more durable than a mega-cap squeeze. "
                            "Value shown = last session's RSP − SPY.", "basis": "measure on the day"})
    from .selloff_evidence import tell_evidence
    out = [t for t in tells if t is not None]
    for t in out:
        t["evidence"] = tell_evidence(t["id"])            # one registry feeds the chips AND the evidence panel
    return out


def dealer_context(dealer: dict | None, spot: float) -> list[dict]:
    """Dealer gamma as a VOLATILITY regime note — never a direction tell (research: Baltussen, Da, Lammers &
    Martens 2021; signed-data studies show the public sign convention over-labels 'short gamma')."""
    if not dealer:
        return []
    sign = (dealer.get("net_gex") or {}).get("sign")
    flip = (dealer.get("gamma_flip") or {}).get("level")
    short = sign == "short"
    return [{
        "id": "dealer_gamma",
        "label": f"Estimated dealer gamma: {'short' if short else 'long'} (convention-based, SPY)",
        "flip": _r(flip, 2) if flip else None,
        "flip_distance_pct": _r((flip / spot - 1) * 100, 2) if flip and spot else None,
        "detail": ("When dealers are short gamma, intraday moves have tended to extend into the close, and that extension has "
                   "reverted over the following days (Baltussen, Da, Lammers & Martens 2021). A volatility read, not a "
                   "direction call." if short else
                   "Long dealer gamma has tended to coincide with damped intraday volatility. A volatility read, not a "
                   "direction call.")
                  + " Caveat: the public-data sign rule (dealers long calls, short puts) overstates 'short gamma' — signed "
                    "S&P option data show dealers net long gamma most of the time — so treat the sign as uncertain; "
                    "gamma walls and the flip level have no peer-reviewed support as price targets.",
        "evidence": {"grade": "moderate", "note": "Volatility only; sign uncertain. Baltussen et al. 2021 (JFE); "
                                                  "Amaya et al. 2025; Adams et al. (draft).", "ref": "dealer_gamma"},
    }]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _levels(spy: pd.DataFrame) -> dict:
    c = spy["Close"]
    return {
        "close": float(c.iloc[-1]), "date": spy.index[-1].date().isoformat(),
        "session_high": float(spy["High"].iloc[-1]), "session_low": float(spy["Low"].iloc[-1]),
        "prev_high": float(spy["High"].iloc[-2]) if len(spy) > 1 else None,
        "pre_move_close": float(c.iloc[-1 - WINDOW]) if len(c) > WINDOW else None,
        "sma20": float(c.tail(20).mean()) if len(c) >= 20 else None,
        "sma50": float(c.tail(50).mean()) if len(c) >= 50 else None,
        "sma200": float(c.tail(200).mean()) if len(c) >= 200 else None,
        "high_52w": float(spy["High"].tail(252).max()),
    }


def _lean(st: dict, track_badge: str | None = None) -> str:
    """Plain-English verdict on an analog-vs-baseline P(up) gap, honest about sample size AND about the
    engine's out-of-sample record: an in-sample 'significant' tilt is not presented as meaningful when the
    walk-forward check has not shown skill (one voice — the track record wins)."""
    edge = st.get("edge_pts") or 0.0
    side = "recovery" if edge > 0 else "further losses"
    if st.get("significant") and track_badge not in (None, "skill"):
        return (f"a tilt toward {side} in these analogs that the engine's out-of-sample record does not confirm")
    if st.get("significant"):
        return f"a statistically meaningful tilt toward {side}"
    if abs(edge) >= 5:
        return ("leans toward " + ("recovery" if edge > 0 else "further losses") +
                ", but not distinguishable from chance at this sample size")
    return "no edge vs. any day"


def _read(state: dict, analogs: dict, cone: dict, diagnosis: dict, tells: list[dict]) -> list[str]:
    out: list[str] = []
    f = (analogs.get("features_now") or {}) if analogs.get("available") else {}
    r3, s3 = state.get("ret3_pct"), state.get("move3_sigma")
    if r3 is not None and s3 is not None:
        dd, d200 = state["drawdown_52w"], state["dist_200d"]
        out.append(f"The S&P 500 {'fell' if r3 < 0 else 'rose'} {abs(r3):.2f}% over the last {WINDOW} sessions "
                   f"({s3:+.1f}σ vs. its prior {VOL_LOOKBACK}-session volatility); it is {abs(dd):.1f}% below its "
                   f"52-week high and {abs(d200):.1f}% {'above' if d200 >= 0 else 'below'} its 200-day average.")
    if analogs.get("available"):
        st5, st20 = analogs["stats"]["h5"], analogs["stats"][f"h{analogs['horizon']}"]
        cal0 = analogs.get("calibration") or {}
        badge = ((cal0.get("skill") or {}).get("h5") or {}).get("badge") if cal0.get("available") else None
        lean = _lean(st5, badge)
        ci = st5.get("p_up_ci_episode") or st5["p_up_ci"]
        n_ep = (analogs.get("episodes") or {}).get("n")
        out.append(f"In the {analogs['n']} closest historical setups since 1990"
                   f"{f' (from {n_ep} distinct episodes)' if n_ep else ''}, the index was higher "
                   f"5 sessions later {st5['p_up']:.0f}% of the time (episode-robust 95% range {ci[0]:.0f}–"
                   f"{ci[1]:.0f}%) vs. {st5['baseline_p_up']:.0f}% for any day — {lean}. "
                   f"The 10th-percentile 5-session outcome was {st5['p10_pct']:+.1f}% "
                   f"(any day: {analogs['baseline']['h5']['p10_pct']:+.1f}%). Over {analogs['horizon']} sessions: "
                   f"{st20['p_up']:.0f}% higher vs. {st20['baseline_p_up']:.0f}% for any day — {_lean(st20, badge)}.")
        pth = analogs["path"]
        if pth.get("baseline_p_new_low_5d") is not None:
            out.append(f"After those setups the index traded below the last session's low within 5 sessions "
                       f"{pth['p_new_low_5d']:.0f}% of the time (any day: {pth['baseline_p_new_low_5d']:.0f}%) and "
                       f"fully recovered the {WINDOW}-session move within {analogs['horizon']} sessions "
                       f"{pth['p_reclaim_pre_move_20d']:.0f}% of the time (any down-{WINDOW}-session day: "
                       f"{pth['baseline_p_reclaim_pre_move_20d']:.0f}%).")
    cal = (analogs or {}).get("calibration") or {}
    if cal.get("available"):
        sk = (cal.get("skill") or {}).get("h5", {}).get("stress", {})
        if sk.get("bss_raw") is not None:
            verdict = {"skill": "have beaten", "worse": "have done WORSE than", "no_skill": "have not beaten",
                       "insufficient": "cannot yet be scored against"}[cal["skill"]["h5"]["badge"]]
            out.append(f"Track record: replayed since 2000 with no hindsight, this analog engine's 5-session odds after "
                       f"selloffs {verdict} the plain base rate (Brier skill {sk['bss_raw'] * 100:+.1f}%, 95% CI "
                       f"{(sk['lo'] or 0) * 100:+.1f}% to {(sk['hi'] or 0) * 100:+.1f}%, {sk['n']} test origins). Read the "
                       f"history numbers as what happened, not a forecast"
                       + (f"; the calibrated best estimate of P(up) is {st5['p_up_shrunk']:.0f}%."
                          if st5.get("p_up_shrunk") is not None else "."))
        cov = (cal.get("coverage") or {}).get("h5", {}).get("stress", {})
        ff = analogs.get("fan_factor")
        if cov.get("analog_p10_p90") is not None and ff:
            out.append(f"The raw history fan held only {cov['analog_p10_p90']:.0f}% of 5-session outcomes after selloffs "
                       f"(target 80%), so it is drawn widened ×{ff['h5']:.2f}; a VIX-implied cone held "
                       f"{cov['vix_cone_p10_p90']:.0f}% — options ranges usually run wide.")
    if cone.get("available") and cone.get("fan_pct"):
        d1, d5 = cone["fan_pct"][0], cone["fan_pct"][min(4, len(cone["fan_pct"]) - 1)]
        out.append(f"Options price a ±{d1['sigma_pct']:.1f}% 1σ move for the next session and ±{d5['sigma_pct']:.1f}% "
                   f"over 5 sessions.")
    out.append(f"{diagnosis['label']}: {diagnosis['summary']}")
    # The "k of n tells active" line is rendered by the client from the MERGED tell list (these + the
    # Phase 2 breadth tells) with the same counter the tells panel uses, so the two never disagree.
    return out


def _evidence_panel(analogs: dict) -> dict:
    from .selloff_evidence import evidence_summary
    return evidence_summary((analogs or {}).get("calibration"))


def build_selloff_monitor(data: dict, dealer: dict | None = None, hy_oas: pd.Series | None = None,
                          today: _dt.date | None = None) -> dict:
    """Pure assembly from fetched data (no network) — unit-testable with synthetic frames."""
    today = today or _dt.date.today()
    index = data.get("index")
    spy = data.get("spy_ohlc")
    if index is None or index.empty or spy is None or len(spy) < 60:
        return {"available": False, "reason": "Market data unavailable right now."}

    feat = index_features(index, data.get("vix_hist", pd.Series(dtype=float)))
    from .selloff_calibration import memo_walk_forward, public
    try:
        cal = memo_walk_forward(feat, _FEATURES, _WEIGHTS, K_ANALOGS, H)
    except Exception as exc:  # noqa: BLE001 — the track record is context; never block the page on it
        logger.warning("selloff: walk-forward calibration failed: %s", exc)
        cal = {"available": False, "reason": "Walk-forward check failed."}
    analogs = find_analogs(feat, calibration=cal)
    if analogs.get("available"):
        analogs["calibration"] = public(cal)
    lv = _levels(spy)
    anchor = spy.index[-1].date()
    sessions_fwd = next_sessions(anchor, H)
    spot = lv["close"]

    cone = options_cone(data.get("option_chains") or [], spot, anchor, sessions_fwd)

    spy_sessions = list(spy.index[-GRID_SESSIONS:])
    grid = stress_grid(data.get("closes") or {}, spy_sessions, hy_oas)
    now_f = feat.iloc[-1]
    diagnosis = diagnose_stress(grid, {"move3_sigma": _r(now_f["move3_sigma"], 2), "ret3_pct": _r(now_f["ret3_pct"], 2)})
    overnight = overnight_futures(data.get("futures") or {}, anchor, spot)

    now = feat.iloc[-1]
    vix_row = _row(grid, "VIX/VIX3M")
    oas_row = _row(grid, "HY OAS")
    dealer_view = None
    if dealer:
        gl = dealer.get("gamma_levels") or {}
        dealer_view = {
            "sign": (dealer.get("net_gex") or {}).get("sign"),
            "net_gex_millions": (dealer.get("net_gex") or {}).get("value_millions"),
            "flip": (dealer.get("gamma_flip") or {}).get("level"),
            "put_support": (gl.get("put_support") or {}).get("strike"),
            "call_resistance": (gl.get("call_resistance") or {}).get("strike"),
            "hvl": (gl.get("hvl") or {}).get("strike"),
            "as_of": dealer.get("as_of"),
        }
    state = {
        "index_close": _r(now["close"], 2), "index_date": feat.index[-1].date().isoformat(),
        "spy_close": _r(spot, 2), "spy_date": lv["date"],
        "ret1_pct": _r(now["ret1_pct"], 2), "ret3_pct": _r(now["ret3_pct"], 2),
        "ret5_pct": _r((feat["close"].iloc[-1] / feat["close"].iloc[-6] - 1) * 100, 2) if len(feat) > 6 else None,
        "move1_sigma": _r(now["move1_sigma"], 2), "move3_sigma": _r(now["move3_sigma"], 2),
        "sigma_daily_pct": _r(now["sigma_pre_pct"], 2),
        "drawdown_52w": _r(now["drawdown_52w"], 2), "dist_200d": _r(now["dist_200d"], 2),
        "dist_50d": _r((spot / lv["sma50"] - 1) * 100, 2) if lv["sma50"] else None,
        "vix": _r(now["vix"], 2),
        "vix_ratio": vix_row["cells"][-1]["value"] if vix_row else None,
        "hy_oas": oas_row["cells"][-1]["value"] if oas_row else None,
        "hy_oas_as_of": (oas_row or {}).get("as_of"),
        "hy_oas_window_bp": (oas_row or {}).get("window", {}).get("change"),
        "dealer": dealer_view,
        "recent": [{"date": feat.index[i].date().isoformat(), "ret_pct": _r(feat["ret1_pct"].iloc[i], 2),
                    "sigma": _r(feat["move1_sigma"].iloc[i], 2)} for i in range(-GRID_SESSIONS, 0)],
    }

    tells = build_tells(lv, grid, dealer, overnight)
    context_notes = dealer_context(dealer, spot)
    v126 = feat["var126"].dropna()
    var_rank = float((v126 <= v126.iloc[-1]).mean() * 100) if len(v126) else None
    r504 = feat["ret504_pct"].iloc[-1]
    state["momentum_panic_state"] = {
        "on": bool(np.isfinite(r504) and r504 < 0 and var_rank is not None and var_rank >= 70),
        "ret_24m_pct": _r(r504, 1), "var126_pct_rank": _r(var_rank, 0),
        "note": ("Daniel & Moskowitz (2016): after a 24-month market decline with high 126-day variance, momentum is prone "
                 "to crash — on sharp REBOUNDS, when past losers rally hardest. Not an index direction call."),
    }

    # chart: last 60 SPY closes + forward dates + both fans IN SPY PRICE (one conversion, here)
    def _to_px(fan: list[dict]) -> list[dict]:
        return [{"h": p["h"], "date": sessions_fwd[p["h"] - 1].isoformat(),
                 **{q: _r(spot * (1 + p[q] / 100), 2) for q in _Z}} for p in fan]

    hist_tail = spy.tail(60)
    level_lines = [
        {"id": "pre_move", "label": f"Pre-selloff close ({WINDOW} sessions ago)", "price": _r(lv["pre_move_close"], 2), "kind": "recovery"},
        {"id": "session_high", "label": "Last session high", "price": _r(lv["session_high"], 2), "kind": "recovery"},
        {"id": "session_low", "label": "Last session low", "price": _r(lv["session_low"], 2), "kind": "continuation"},
        {"id": "sma50", "label": "50-day average", "price": _r(lv["sma50"], 2), "kind": "trend"},
        {"id": "sma200", "label": "200-day average", "price": _r(lv["sma200"], 2), "kind": "trend"},
    ]
    if dealer_view:
        for key, label in (("flip", "Gamma flip"), ("put_support", "Put support"), ("call_resistance", "Call resistance")):
            if dealer_view.get(key) is not None:
                level_lines.append({"id": key, "label": label, "price": _r(dealer_view[key], 2), "kind": "dealer"})
    chart = {
        "history": [{"date": d.date().isoformat(), "close": _r(v, 2)} for d, v in hist_tail["Close"].items()],
        "anchor": {"date": anchor.isoformat(), "close": _r(spot, 2)},
        "forward_dates": [d.isoformat() for d in sessions_fwd],
        "history_fan": _to_px(analogs["fan_pct"]) if analogs.get("available") else None,
        "options_fan": _to_px(cone["fan_pct"]) if cone.get("available") else None,
        "levels": [lvl for lvl in level_lines if lvl["price"] is not None],
        "spy_implied_open": overnight.get("spy_implied"),
    }

    return {
        "available": True,
        "as_of": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "last_session": anchor.isoformat(),
        "window_sessions": WINDOW,
        "state": state,
        "levels": {k: _r(v, 2) if isinstance(v, float) else v for k, v in lv.items()},
        "stress": {**grid, "diagnosis": diagnosis},
        "analogs": analogs,
        "options_cone": cone,
        "chart": chart,
        "overnight": overnight,
        "tells": tells,
        "context_notes": context_notes,
        "evidence": _evidence_panel(analogs),
        "events": market_events(today),
        "read": _read(state, analogs, cone, diagnosis, tells),
    }


async def get_selloff_monitor(dealer: dict | None = None) -> dict:
    """Network fetch (threadpool) + FRED HY OAS, then the pure assembly."""
    import asyncio
    from .liquidity_service import _fetch_fred

    start = (_dt.date.today() - _dt.timedelta(days=400)).isoformat()
    loop = asyncio.get_running_loop()
    data_task = loop.run_in_executor(None, fetch_market_data)
    oas_task = _fetch_fred("BAMLH0A0HYM2", start)
    data, oas = await asyncio.gather(data_task, oas_task, return_exceptions=True)
    if isinstance(data, Exception):
        logger.exception("selloff monitor fetch failed: %s", data)
        return {"available": False, "reason": "Market data unavailable right now."}
    if isinstance(oas, Exception):
        logger.warning("selloff monitor: HY OAS fetch failed: %s", oas)
        oas = None
    else:
        oas.index = pd.DatetimeIndex(oas.index).normalize()
    return await loop.run_in_executor(None, lambda: build_selloff_monitor(data, dealer, oas))
