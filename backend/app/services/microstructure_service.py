"""Microstructure & multi-timeframe volume-profile analytics.

Where `institutional_ta_service` gives ONE volume-profile + smart-money read on a single
timeframe, this module builds the **microstructure map** a discretionary or systematic
trader actually navigates by:

  • Volume-Profile Hierarchy — POC / VAH / VAL / **LVNs** at three horizons (Macro
    3-Month·Daily, Swing 15-Day·30-Min, Micro 5-Day·5-Min). Agreement across horizons is
    a high-conviction level; an LVN (thin price) is where price travels fast — a spot for
    stops and a breakout accelerant.
  • Naked / Virgin POCs — prior *session* Points of Control that price left behind and has
    NOT retested. Unfinished auction business — they act as magnets / profit targets.
  • Anchored VWAP (AVWAP) Matrix — the volume-weighted average price since a catalyst
    (YTD open, last earnings, the 52-week high, the 52-week low). Institutions defend
    these; price above the AVWAP = buyers in control since that event, and vice-versa.

Pure numpy/pandas, best-effort: any degenerate input yields ``None``/empty fields, never
raises — so a thin/illiquid name can't break the technical response. The heavy per-bar
math (POC / value area) is **reused** from ``institutional_ta_service`` rather than
reimplemented.
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

from .institutional_ta_service import _volume_profile, _f


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _r(x, nd: int = 2):
    """Round, mapping nan/inf/None to ``None`` so the payload JSON-encodes cleanly."""
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(xf):
        return None
    return round(xf, nd)


def _side(level: float | None, price: float) -> str | None:
    if level is None or price is None:
        return None
    return "above" if level >= price else "below"


def _dist_pct(level: float | None, price: float) -> float | None:
    """Signed distance of *level* from spot as a % of spot (+ = above)."""
    if level is None or not price:
        return None
    return _r((level - price) / price * 100.0, 2)


# ---------------------------------------------------------------------------
# Low-Volume Nodes (thin prices between high-volume shelves)
# ---------------------------------------------------------------------------

def _low_volume_nodes(bins: list[dict], lvn_frac: float = 0.45, max_keep: int = 5) -> list[dict]:
    """LVNs = local *troughs* in the volume-by-price histogram that sit well below the
    high-volume shelves on either side. Price accelerates through them, so they mark
    breakout levels and low-risk stop placement (little trade to absorb a reversal).

    ``bins`` is the ascending price→volume histogram from :func:`_volume_profile`.
    A bin qualifies when it is a local minimum, its ``pct`` is under ``lvn_frac`` of the
    profile's peak, AND it is a genuine valley — some bin with more volume exists on BOTH
    sides (which also excludes the profile edges)."""
    n = len(bins)
    if n < 3:
        return []
    pct = [float(b.get("pct", 0.0)) for b in bins]
    peak = max(pct) if pct else 0.0
    if peak <= 0:
        return []
    out: list[dict] = []
    for i in range(1, n - 1):
        if pct[i] > lvn_frac * peak:
            continue
        if not (pct[i] <= pct[i - 1] and pct[i] <= pct[i + 1]):
            continue                                  # not a local trough
        higher_left = any(pct[j] > pct[i] for j in range(0, i))
        higher_right = any(pct[j] > pct[i] for j in range(i + 1, n))
        if higher_left and higher_right:              # a valley between two shelves
            out.append({"price": _r(bins[i]["price"]), "pct": _r(pct[i], 1)})
    out.sort(key=lambda x: (x["pct"] if x["pct"] is not None else 1e9))   # thinnest first
    return out[:max_keep]


# ---------------------------------------------------------------------------
# Anchored VWAP
# ---------------------------------------------------------------------------

def _anchored_vwap(highs, lows, closes, volumes, anchor_idx: int) -> float | None:
    """Volume-weighted average of the typical price ((H+L+C)/3) from ``anchor_idx``→now."""
    h, l, c, v = _f(highs), _f(lows), _f(closes), _f(volumes)
    n = len(c)
    if anchor_idx is None or anchor_idx < 0 or anchor_idx >= n:
        return None
    tp = (h[anchor_idx:] + l[anchor_idx:] + c[anchor_idx:]) / 3.0
    vv = v[anchor_idx:]
    denom = float(np.nansum(vv))
    if denom <= 0:
        return None
    return _r(float(np.nansum(tp * vv)) / denom, 2)


def _avwap_entry(highs, lows, closes, volumes, dates, anchor_idx, label, price) -> dict | None:
    if anchor_idx is None:
        return None
    val = _anchored_vwap(highs, lows, closes, volumes, anchor_idx)
    if val is None:
        return None
    anchor_date = None
    try:
        anchor_date = _date_str(dates[anchor_idx])
    except Exception:
        pass
    return {"label": label, "anchor_date": anchor_date, "value": val,
            "distance_pct": _dist_pct(val, price), "side": _side(val, price)}


# ---------------------------------------------------------------------------
# Naked / Virgin POCs — prior session POCs price hasn't retested
# ---------------------------------------------------------------------------

def _session_poc(highs, lows, closes, volumes) -> float | None:
    """POC of a single session's bars (reuses the shared volume-profile engine)."""
    vp = _volume_profile(highs, lows, closes, volumes, n_bins=12)
    return vp["poc"] if vp else None


def _naked_pocs(session_dates, highs, lows, closes, volumes, price,
                skip_recent: int = 1, max_keep: int = 6) -> list[dict]:
    """A session's POC is *naked/virgin* when no LATER bar's [low, high] range has
    contained it — unfinished auction business that price tends to return to.

    ``session_dates`` is a per-bar list of ``date`` (or 'YYYY-MM-DD' str) grouping bars
    into sessions. The most recent ``skip_recent`` sessions are excluded: their POCs are
    trivially "untested" (nothing has traded after them) and sit on top of spot, so they
    carry no magnet signal."""
    h, l = _f(highs), _f(lows)
    n = len(h)
    if n < 10 or not price:
        return []
    # group bar indices by session, preserving chronological order
    order: list = []
    groups: dict = {}
    for i, d in enumerate(session_dates):
        key = _date_str(d)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)

    candidates = order[:-skip_recent] if skip_recent and len(order) > skip_recent else order
    today = _dt.date.today()
    naked: list[dict] = []
    for key in candidates:
        idxs = groups[key]
        last_idx = idxs[-1]
        poc = _session_poc(h[idxs[0]:last_idx + 1], l[idxs[0]:last_idx + 1],
                           _f(closes)[idxs[0]:last_idx + 1], _f(volumes)[idxs[0]:last_idx + 1])
        if poc is None:
            continue
        after_l, after_h = l[last_idx + 1:], h[last_idx + 1:]
        if after_h.size and bool(np.any((after_l <= poc) & (after_h >= poc))):
            continue                                  # retested → not naked
        age = None
        try:
            age = (today - _to_date(key)).days
        except Exception:
            pass
        naked.append({"price": _r(poc), "date": key, "age_days": age,
                      "distance_pct": _dist_pct(poc, price), "side": _side(poc, price)})

    # de-dupe near-equal levels, keep the nearest to spot first
    naked.sort(key=lambda x: abs(x["distance_pct"]) if x["distance_pct"] is not None else 1e9)
    out: list[dict] = []
    for nk in naked:
        if any(abs(nk["price"] - o["price"]) / max(o["price"], 1e-9) < 0.003 for o in out):
            continue
        out.append(nk)
        if len(out) >= max_keep:
            break
    return out


# ---------------------------------------------------------------------------
# date coercion (yfinance indexes vary: tz-aware intraday, naive daily)
# ---------------------------------------------------------------------------

def _to_date(x) -> _dt.date:
    if isinstance(x, _dt.date) and not isinstance(x, _dt.datetime):
        return x
    return pd.Timestamp(x).to_pydatetime().date()


def _date_str(x) -> str:
    return _to_date(x).strftime("%Y-%m-%d")


def _last_earnings_date(stock) -> _dt.date | None:
    """Most recent PAST earnings report date, from earnings_dates / earnings_history."""
    cands: list[_dt.date] = []
    for attr in ("earnings_dates", "earnings_history"):
        try:
            df = getattr(stock, attr)
            if df is not None and not df.empty:
                for d in df.index:
                    try:
                        cands.append(_to_date(d))
                    except Exception:
                        continue
        except Exception:
            continue
    today = _dt.date.today()
    past = [d for d in cands if d <= today]
    return max(past) if past else None


# ---------------------------------------------------------------------------
# timeframe volume-profile builder
# ---------------------------------------------------------------------------

def _tf_profile(hist, label: str, period: str, interval: str) -> dict | None:
    if hist is None or getattr(hist, "empty", True) or len(hist) < 5:
        return None
    vp = _volume_profile(hist["High"].values, hist["Low"].values,
                         hist["Close"].values, hist["Volume"].values, n_bins=24)
    if not vp:
        return None
    return {
        "label": label, "period": period, "interval": interval,
        "poc": vp["poc"], "vah": vp["vah"], "val": vp["val"],
        "value_area_pct": vp["value_area_pct"],
        "lvns": _low_volume_nodes(vp["bins"]),
        "bins": vp["bins"],
    }


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def compute_microstructure(stock) -> dict | None:
    """Full microstructure map for a yfinance ``Ticker``. Returns ``None`` only if no
    usable price data exists at all; otherwise every section is best-effort and may be
    empty. Fetches five histories (cached upstream by the router)."""
    try:
        # NOTE: yfinance ``period`` only accepts fixed tokens (1d/5d/1mo/3mo/6mo/1y/…),
        # so the 15-day and 60-day intraday windows must be dated with start/end.
        macro = _safe_history(stock, "3mo", "1d")
        swing = _safe_history_days(stock, 15, "30m")
        micro = _safe_history(stock, "5d", "5m")
        # 55d (not 60) — Yahoo requires the intraday start to be strictly inside the
        # rolling 60-day window, and rejects a request anchored exactly at the edge.
        naked_src = _safe_history_days(stock, 55, "30m")
        daily = _safe_history(stock, "1y", "1d")

        # spot: prefer the freshest close available
        price = None
        for df in (micro, swing, macro, naked_src, daily):
            if df is not None and not df.empty:
                price = float(df["Close"].values[-1])
                break
        if price is None:
            return None

        profiles = {
            "macro": _tf_profile(macro, "Macro · 3-Month / Daily", "3mo", "1d"),
            "swing": _tf_profile(swing, "Swing · 15-Day / 30-Min", "15d", "30m"),
            "micro": _tf_profile(micro, "Micro · 5-Day / 5-Min", "5d", "5m"),
        }

        naked = []
        if naked_src is not None and not naked_src.empty:
            dates = [_to_date(t) for t in naked_src.index]
            naked = _naked_pocs(dates, naked_src["High"].values, naked_src["Low"].values,
                                naked_src["Close"].values, naked_src["Volume"].values, price)

        avwap = _build_avwap_matrix(daily, stock, price)

        return {
            "price": _r(price),
            "as_of": _now_str(),
            "timeframe_profiles": profiles,
            "naked_pocs": naked,
            "avwap": avwap,
            "price_series": _price_series(daily if daily is not None else macro),
        }
    except Exception:  # noqa: BLE001 — never break the technical response
        return None


def _build_avwap_matrix(daily, stock, price: float) -> dict:
    """AVWAPs anchored to YTD open, last earnings, and the 52-week high & low."""
    out: dict = {"ytd": None, "earnings": None, "high_52w": None, "low_52w": None}
    if daily is None or daily.empty or len(daily) < 5:
        return out
    h = daily["High"].values
    l = daily["Low"].values
    c = daily["Close"].values
    v = daily["Volume"].values
    dates = [_to_date(t) for t in daily.index]
    n = len(c)

    # YTD open — first bar of the current calendar year
    year = _dt.date.today().year
    ytd_idx = next((i for i, d in enumerate(dates) if d.year == year), None)
    out["ytd"] = _avwap_entry(h, l, c, v, dates, ytd_idx, "YTD Open", price)

    # 52-week high / low anchors
    hi_idx = int(np.argmax(h))
    lo_idx = int(np.argmin(l))
    out["high_52w"] = _avwap_entry(h, l, c, v, dates, hi_idx, "52-Wk High", price)
    out["low_52w"] = _avwap_entry(h, l, c, v, dates, lo_idx, "52-Wk Low", price)

    # last earnings — anchor at the first daily bar on/after the report date
    ed = _last_earnings_date(stock)
    if ed is not None:
        e_idx = next((i for i, d in enumerate(dates) if d >= ed), None)
        if e_idx is not None and e_idx < n - 1:       # need bars after the anchor
            out["earnings"] = _avwap_entry(h, l, c, v, dates, e_idx, "Last Earnings", price)
    return out


def _price_series(df, n: int = 130) -> dict | None:
    """Recent daily closes for the chart the overlay levels are drawn on (~6 months so
    most naked-POC / AVWAP levels fall inside the visible range)."""
    if df is None or getattr(df, "empty", True):
        return None
    s = df.tail(n)
    try:
        return {"timestamps": [_date_str(t) for t in s.index],
                "closes": [_r(x) for x in s["Close"].values]}
    except Exception:
        return None


def _safe_history(stock, period: str, interval: str):
    try:
        df = stock.history(period=period, interval=interval)
        if df is None or df.empty:
            return None
        return df.dropna(subset=["Close"])
    except Exception:
        return None


def _safe_history_days(stock, days: int, interval: str):
    """Intraday window sized in calendar days via start/end (``period`` has no such token).
    Intraday data has a ~60-day availability limit, which ``days`` stays within."""
    end = _dt.datetime.now()
    start = end - _dt.timedelta(days=days)
    try:
        df = stock.history(start=start, end=end, interval=interval)
        if df is None or df.empty:
            return None
        return df.dropna(subset=["Close"])
    except Exception:
        return None


def _now_str() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
