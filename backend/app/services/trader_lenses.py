"""Institutional indicator suite + "what would the famous traders do" rule lenses.

PURE (numpy in, dicts out — no I/O). Feeds the Trade Manager (trade_manager_service.py).

Two layers:

* ``indicator_suite`` — the raw, auditable indicator readings (trend stack, momentum + divergence, ADX/DI,
  volatility regime / squeeze, volume-flow, Ichimoku, supertrend / chandelier / Donchian exit levels,
  relative strength vs the benchmark, weekly stage). Facts only.
* ``trader_lenses`` — each famous trader's published ruleset evaluated on those facts: every rule carries
  its measured value and a met / not-met flag, plus that trader's own EXIT trigger level. A lens' ``d``
  (−1..+1 bullishness) and ``stance`` are the *algorithm's* reading; the Trade Manager strips them before
  the evidence goes to the LLM (the LLM gets the rules + values, never our judgement).

Rules are the traders' widely-published mechanical criteria — a systematic reading, not a claim about what
any individual trader actually did.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np


# ── small numeric helpers ────────────────────────────────────────────────────

def _f(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def _r(x, nd: int = 2):
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return None
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def sma(a: np.ndarray, w: int) -> np.ndarray:
    a = _f(a)
    out = np.full(a.shape, np.nan)
    if len(a) >= w > 0:
        c = np.cumsum(np.insert(a, 0, 0.0))
        out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def ema(a: np.ndarray, span: int) -> np.ndarray:
    a = _f(a)
    out = np.empty_like(a)
    if len(a) == 0:
        return out
    k = 2.0 / (span + 1.0)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1 - k)
    return out


def _wilder(a: np.ndarray, n: int) -> np.ndarray:
    a = _f(a)
    out = np.full(a.shape, np.nan)
    if len(a) < n:
        return out
    out[n - 1] = np.nanmean(a[:n])
    for i in range(n, len(a)):
        out[i] = (out[i - 1] * (n - 1) + a[i]) / n
    return out


def true_range(h, l, c) -> np.ndarray:
    h, l, c = _f(h), _f(l), _f(c)
    pc = np.concatenate([[c[0]], c[:-1]])
    return np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))


def atr(h, l, c, n: int = 14) -> np.ndarray:
    return _wilder(true_range(h, l, c), n)


def rsi(c, n: int = 14) -> np.ndarray:
    c = _f(c)
    out = np.full(c.shape, np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    au, ad = up[:n].mean(), dn[:n].mean()
    for i in range(n, len(c)):
        if i > n:
            au = (au * (n - 1) + up[i - 1]) / n
            ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
    return out


def adx_di(h, l, c, n: int = 14):
    """(ADX, +DI, −DI) — Wilder."""
    h, l, c = _f(h), _f(l), _f(c)
    if len(c) < 2 * n + 2:
        nan = np.full(c.shape, np.nan)
        return nan, nan, nan
    up, dn = h[1:] - h[:-1], l[:-1] - l[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(h, l, c)[1:]
    atr_w, p_w, m_w = _wilder(tr, n), _wilder(pdm, n), _wilder(mdm, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi, mdi = 100 * p_w / atr_w, 100 * m_w / atr_w
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    adx_ = _wilder(np.nan_to_num(dx, nan=0.0), n)
    pad = lambda x: np.concatenate([[np.nan], x])           # noqa: E731 — realign to len(c)
    return pad(adx_), pad(pdi), pad(mdi)


def _last(a) -> Optional[float]:
    a = _f(a)
    if a.size == 0:
        return None
    v = a[-1]
    return None if (math.isnan(v) or math.isinf(v)) else float(v)


def _pct(a, b) -> Optional[float]:
    """(a/b − 1)·100, None-safe."""
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def supertrend(h, l, c, n: int = 10, mult: float = 3.0):
    """(direction[+1/−1], line). Classic ATR supertrend."""
    h, l, c = _f(h), _f(l), _f(c)
    a = atr(h, l, c, n)
    hl2 = (h + l) / 2.0
    ub, lb = hl2 + mult * a, hl2 - mult * a
    dirn = np.ones(len(c))
    line = np.full(len(c), np.nan)
    fub, flb = ub.copy(), lb.copy()
    for i in range(1, len(c)):
        if np.isnan(a[i]):
            continue
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1] or np.isnan(fub[i - 1])) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1] or np.isnan(flb[i - 1])) else flb[i - 1]
        if dirn[i - 1] == 1:
            dirn[i] = -1 if c[i] < flb[i] else 1
        else:
            dirn[i] = 1 if c[i] > fub[i] else -1
        line[i] = flb[i] if dirn[i] == 1 else fub[i]
    return dirn, line


def swing_points(h, l, left: int = 4, right: int = 4):
    """Confirmed swing highs / lows → [(idx, price)]."""
    h, l = _f(h), _f(l)
    sh, sl = [], []
    gap = max(left, right)
    for i in range(left, len(h) - right):
        if h[i] == np.max(h[i - left:i + right + 1]):
            if sh and i - sh[-1][0] <= gap and h[i] <= sh[-1][1]:   # equal / lower neighbour of the same peak → one swing
                continue
            if sh and i - sh[-1][0] <= gap:                         # a higher neighbour supersedes the earlier one
                sh.pop()
            sh.append((i, float(h[i])))
        if l[i] == np.min(l[i - left:i + right + 1]):
            if sl and i - sl[-1][0] <= gap and l[i] >= sl[-1][1]:
                continue
            if sl and i - sl[-1][0] <= gap:
                sl.pop()
            sl.append((i, float(l[i])))
    return sh, sl


def _divergence(c, ind, lookback: int = 40, order: int = 3) -> Optional[str]:
    """Classic price/oscillator divergence over the last ``lookback`` bars: 'bearish' (price HH, osc LH),
    'bullish' (price LL, osc HL) or None."""
    c, ind = _f(c), _f(ind)
    if len(c) < lookback + 2 * order:
        return None
    seg_c, seg_i = c[-lookback:], ind[-lookback:]
    sh, sl = swing_points(seg_c, seg_c, order, order)
    hi = [p for p in sh][-2:]
    lo = [p for p in sl][-2:]
    if len(hi) == 2 and seg_c[hi[1][0]] > seg_c[hi[0][0]] and seg_i[hi[1][0]] < seg_i[hi[0][0]] - 1.0:
        return "bearish"
    if len(lo) == 2 and seg_c[lo[1][0]] < seg_c[lo[0][0]] and seg_i[lo[1][0]] > seg_i[lo[0][0]] + 1.0:
        return "bullish"
    return None


def _weekly(o, h, l, c, v, idx):
    """Daily → weekly OHLCV (no pandas dependency in the signature; idx is a DatetimeIndex)."""
    import pandas as pd
    df = pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "v": v}, index=idx)
    w = df.resample("W-FRI").agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()
    return w["o"].values, w["h"].values, w["l"].values, w["c"].values, w["v"].values


# ── the indicator suite ──────────────────────────────────────────────────────

def indicator_suite(o, h, l, c, v, idx=None, bench_c=None) -> dict:
    """Latest readings of the full institutional indicator set. ``bench_c`` = benchmark (SPY) closes
    aligned to the same trailing window (optional)."""
    o, h, l, c, v = _f(o), _f(h), _f(l), _f(c), _f(v)
    n = len(c)
    if n < 60:
        return {}
    px = float(c[-1])
    out: dict = {"bars": n, "price": _r(px)}

    # ── trend stack
    s = {k: sma(c, k) for k in (10, 20, 50, 100, 150, 200)}
    e = {k: ema(c, k) for k in (8, 10, 13, 20, 21, 50)}
    out["sma"] = {str(k): _r(_last(s[k])) for k in s}
    out["ema"] = {str(k): _r(_last(e[k])) for k in e}
    s200 = s[200]
    out["sma200_slope_1m_pct"] = _r(_pct(_last(s200), float(s200[-22])) if n >= 222 and not np.isnan(s200[-22]) else None)
    out["pct_vs_sma"] = {str(k): _r(_pct(px, _last(s[k]))) for k in (20, 50, 150, 200)}
    out["pct_vs_ema"] = {str(k): _r(_pct(px, _last(e[k]))) for k in (10, 21)}

    # ── momentum
    r14, r2, r5 = rsi(c, 14), rsi(c, 2), rsi(c, 5)
    out["rsi14"], out["rsi2"], out["rsi5"] = _r(_last(r14), 1), _r(_last(r2), 1), _r(_last(r5), 1)
    macd = ema(c, 12) - ema(c, 26)
    sig = ema(macd, 9)
    hist = macd - sig
    out["macd"] = {"line": _r(macd[-1], 3), "signal_line": _r(sig[-1], 3), "hist": _r(hist[-1], 3),
                   "hist_slope_3d": _r(hist[-1] - hist[-4], 3) if n > 4 else None,
                   "above_zero": bool(macd[-1] > 0)}
    out["roc"] = {str(k): _r(_pct(px, float(c[-1 - k])) if n > k else None, 1) for k in (21, 63, 126, 252)}
    # stochastic %K/%D (14,3)
    ll14 = np.array([np.min(l[max(0, i - 13):i + 1]) for i in range(n)])
    hh14 = np.array([np.max(h[max(0, i - 13):i + 1]) for i in range(n)])
    with np.errstate(divide="ignore", invalid="ignore"):
        k_raw = 100 * (c - ll14) / (hh14 - ll14)
    k_s = sma(np.nan_to_num(k_raw, nan=50.0), 3)
    out["stoch"] = {"percent_k": _r(_last(k_s), 1), "percent_d": _r(_last(sma(np.nan_to_num(k_s, nan=50.0), 3)), 1)}
    out["rsi_divergence"] = _divergence(c, r14)
    macd_div = _divergence(c, hist * 10)
    out["macd_divergence"] = macd_div

    # ── trend strength
    adx_, pdi, mdi = adx_di(h, l, c)
    out["adx"] = {"adx": _r(_last(adx_), 1), "plus_di": _r(_last(pdi), 1), "minus_di": _r(_last(mdi), 1),
                  "adx_slope_5d": _r(float(adx_[-1] - adx_[-6]), 1) if n > 40 and not np.isnan(adx_[-6]) else None}
    up_ix = int(np.argmax(h[-25:]))
    dn_ix = int(np.argmin(l[-25:]))
    out["aroon"] = {"up": _r(100 * (25 - (24 - up_ix)) / 25, 0), "down": _r(100 * (25 - (24 - dn_ix)) / 25, 0)}

    # ── volatility regime & squeeze
    a14 = atr(h, l, c, 14)
    atr_now = _last(a14)
    out["atr14"] = _r(atr_now)
    out["atr_pct"] = _r(atr_now / px * 100 if atr_now else None)
    atrp = a14[~np.isnan(a14)][-252:]
    out["atr_percentile_1y"] = _r(100.0 * float(np.mean(atrp <= atr_now)) if atr_now and len(atrp) > 30 else None, 0)
    bb_mid, bb_sd = sma(c, 20), np.array([np.std(c[max(0, i - 19):i + 1], ddof=0) for i in range(n)])
    bb_up, bb_lo = bb_mid + 2 * bb_sd, bb_mid - 2 * bb_sd
    with np.errstate(divide="ignore", invalid="ignore"):
        bw = (bb_up - bb_lo) / bb_mid
        pctb = (c - bb_lo) / (bb_up - bb_lo)
    bwv = bw[~np.isnan(bw)][-252:]
    kel_mid, kel_atr = ema(c, 20), atr(h, l, c, 10)
    kc_up, kc_lo = kel_mid + 1.5 * kel_atr, kel_mid - 1.5 * kel_atr
    squeeze_on = bool(bb_up[-1] < kc_up[-1] and bb_lo[-1] > kc_lo[-1]) if not np.isnan(kc_up[-1]) else None
    out["bollinger"] = {"pct_b": _r(_last(pctb), 2), "bandwidth_pct": _r(_last(bw) * 100 if _last(bw) is not None else None, 1),
                        "bandwidth_percentile_1y": _r(100.0 * float(np.mean(bwv <= bw[-1])) if len(bwv) > 30 else None, 0),
                        "upper": _r(bb_up[-1]), "lower": _r(bb_lo[-1])}
    out["squeeze_on"] = squeeze_on
    # TTM-style momentum: close − midpoint of (Donchian mid, SMA20) linear-regressed over 20 bars
    dm = (np.array([np.max(h[max(0, i - 19):i + 1]) for i in range(n)]) +
          np.array([np.min(l[max(0, i - 19):i + 1]) for i in range(n)])) / 2.0
    mom_series = c - (dm + sma(c, 20)) / 2.0
    out["squeeze_momentum"] = {"value": _r(_last(mom_series), 3),
                               "rising": bool(mom_series[-1] > mom_series[-3]) if n > 3 else None}
    rets = np.diff(np.log(c))
    out["realized_vol"] = {str(k): _r(float(np.std(rets[-k:], ddof=1) * math.sqrt(252) * 100), 1) for k in (10, 21, 63) if len(rets) >= k}
    out["adr20_pct"] = _r(float(np.mean((h[-20:] - l[-20:]) / c[-20:]) * 100))
    # Volatility forecast. Backtest: the SHORT/LONG realized-vol ratio is the one technical input that carries
    # information about forward dispersion beyond the vol level (|z| of the next 21d: 0.73 → 0.92 across ratio quartiles).
    rv21_, rv63_ = out["realized_vol"].get("21"), out["realized_vol"].get("63")
    out["vol_forecast"] = {
        "rv21": rv21_, "rv63": rv63_,
        "ratio_21_63": _r(rv21_ / rv63_, 2) if (rv21_ and rv63_) else None,
        "blend_ann_pct": _r(0.4 * rv21_ + 0.6 * rv63_, 1) if (rv21_ and rv63_) else (rv21_ or rv63_),
        "regime": ("expanding" if (rv21_ and rv63_ and rv21_ / rv63_ >= 1.4) else
                   "compressing" if (rv21_ and rv63_ and rv21_ / rv63_ <= 0.75) else "stable") if (rv21_ and rv63_) else None,
    }
    # Academic time-series momentum (Moskowitz-Ooi-Pedersen): 12-month return skipping the most recent month
    out["tsmom_12_1_pct"] = _r((float(c[-22]) / float(c[-253]) - 1.0) * 100.0, 1) if n >= 253 else None

    # ── volume / flow
    avg20 = float(np.mean(v[-21:-1])) if n > 21 else float(np.mean(v))
    out["rvol"] = _r(float(v[-1]) / avg20 if avg20 else None)
    chg = np.diff(c, prepend=c[0])
    obv = np.cumsum(np.sign(chg) * v)
    out["obv_trend_20d"] = "up" if obv[-1] > obv[-21] else "down"
    out["obv_divergence"] = _divergence(c, obv / (np.max(np.abs(obv)) + 1e-9) * 100.0, 40)
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        mfm = ((c - l) - (h - c)) / rng
    mfv = np.nan_to_num(mfm, nan=0.0) * v
    out["cmf20"] = _r(float(np.sum(mfv[-20:]) / (np.sum(v[-20:]) + 1e-9)), 3)
    tp = (h + l + c) / 3.0
    mf = tp * v
    pos = np.where(np.diff(tp, prepend=tp[0]) > 0, mf, 0.0)
    neg = np.where(np.diff(tp, prepend=tp[0]) < 0, mf, 0.0)
    out["mfi14"] = _r(100.0 - 100.0 / (1.0 + float(np.sum(pos[-14:]) / (np.sum(neg[-14:]) + 1e-9))), 1)
    up_vol = float(np.sum(v[-20:][chg[-20:] > 0]))
    dn_vol = float(np.sum(v[-20:][chg[-20:] < 0]))
    out["up_down_volume_ratio_20d"] = _r(up_vol / dn_vol if dn_vol else None)
    dist = 0
    acc = 0
    for i in range(n - 25, n):
        if i < 1:
            continue
        if c[i] < c[i - 1] * 0.998 and v[i] > v[i - 1]:
            dist += 1
        if c[i] > c[i - 1] * 1.002 and v[i] > v[i - 1]:
            acc += 1
    out["distribution_days_25d"], out["accumulation_days_25d"] = dist, acc

    # ── structure: highs/lows/channels
    hh = lambda k: float(np.max(h[-k:])) if n >= k else float(np.max(h))      # noqa: E731
    ll = lambda k: float(np.min(l[-k:])) if n >= k else float(np.min(l))      # noqa: E731
    out["range"] = {"hi_20": _r(hh(20)), "lo_20": _r(ll(20)), "hi_55": _r(hh(55)), "lo_55": _r(ll(55)),
                    "hi_252": _r(hh(252)), "lo_252": _r(ll(252)),
                    "pct_from_52w_high": _r(_pct(px, hh(252))), "pct_above_52w_low": _r(_pct(px, ll(252)))}
    out["prior_range"] = {"hi_20": _r(float(np.max(h[-21:-1]))) if n > 21 else None,
                          "lo_20": _r(float(np.min(l[-21:-1]))) if n > 21 else None,
                          "hi_10": _r(float(np.max(h[-11:-1]))) if n > 11 else None,
                          "lo_10": _r(float(np.min(l[-11:-1]))) if n > 11 else None,
                          "lo_55": _r(float(np.min(l[-56:-1]))) if n > 56 else None}
    st_dir, st_line = supertrend(h, l, c, 10, 3.0)
    out["supertrend"] = {"direction": "up" if st_dir[-1] > 0 else "down", "line": _r(_last(st_line)),
                         "flipped_recently": bool(np.any(st_dir[-5:] != st_dir[-1]))}
    a22 = atr(h, l, c, 22)
    ok22 = not np.isnan(a22[-1])
    out["chandelier"] = {"long_stop": _r(hh(22) - 3.0 * a22[-1]) if ok22 else None,
                         "short_stop": _r(ll(22) + 3.0 * a22[-1]) if ok22 else None,
                         # 5×ATR: the WIDE volatility-adjusted exit — backtests (2016-26, 99 names) show 2-3×ATR trails whipsaw
                         "long_stop_wide": _r(hh(22) - 5.0 * a22[-1]) if ok22 else None,
                         "short_stop_wide": _r(ll(22) + 5.0 * a22[-1]) if ok22 else None}
    # Ichimoku (9/26/52)
    tenkan = (np.max(h[-9:]) + np.min(l[-9:])) / 2.0
    kijun = (np.max(h[-26:]) + np.min(l[-26:])) / 2.0
    if n >= 78:
        # cloud as plotted NOW = senkou computed 26 bars ago
        sa_ago = ((np.max(h[-35:-26]) + np.min(l[-35:-26])) / 2.0 + (np.max(h[-52:-26]) + np.min(l[-52:-26])) / 2.0) / 2.0
        sb_ago = (np.max(h[-78:-26]) + np.min(l[-78:-26])) / 2.0
        top, bot = max(sa_ago, sb_ago), min(sa_ago, sb_ago)
        out["ichimoku"] = {"tenkan": _r(tenkan), "kijun": _r(kijun), "cloud_top": _r(top), "cloud_bottom": _r(bot),
                           "price_vs_cloud": "above" if px > top else "below" if px < bot else "inside",
                           "tk_bullish": bool(tenkan > kijun), "chikou_above_price": bool(px > c[-27])}
    # Floor pivots from the prior session
    ph, pl, pc = float(h[-2]), float(l[-2]), float(c[-2])
    pv = (ph + pl + pc) / 3.0
    out["pivots"] = {"pp": _r(pv), "r1": _r(2 * pv - pl), "s1": _r(2 * pv - ph),
                     "r2": _r(pv + (ph - pl)), "s2": _r(pv - (ph - pl))}
    sh, sl_ = swing_points(h, l, 4, 4)
    out["swings"] = {"last_swing_highs": [{"i": n - 1 - i, "price": _r(p)} for i, p in sh[-3:]],
                     "last_swing_lows": [{"i": n - 1 - i, "price": _r(p)} for i, p in sl_[-3:]]}
    # Gaps
    gaps = [(o[i] - c[i - 1]) / c[i - 1] * 100 for i in range(n - 10, n) if i > 0]
    out["gaps_10d"] = {"largest_pct": _r(max(gaps, key=abs)) if gaps else None,
                       "n_over_2pct": int(sum(1 for g in gaps if abs(g) >= 2.0))}
    # 3/10 oscillator (Raschke)
    osc = sma(c, 3) - sma(c, 10)
    out["osc_3_10"] = {"value": _r(_last(osc), 3), "rising": bool(osc[-1] > osc[-2]) if n > 12 else None}
    # Elder impulse (EMA13 slope + MACD-hist slope)
    ema13 = e[13]
    imp_up = bool(ema13[-1] > ema13[-2] and hist[-1] > hist[-2])
    imp_dn = bool(ema13[-1] < ema13[-2] and hist[-1] < hist[-2])
    out["elder_impulse"] = "green" if imp_up else "red" if imp_dn else "blue"
    # VSA / Wyckoff-style climax + spring / upthrust
    spread = h - l
    avg_spread = float(np.mean(spread[-21:-1])) if n > 21 else float(np.mean(spread))
    last_close_pos = float((c[-1] - l[-1]) / (h[-1] - l[-1])) if h[-1] > l[-1] else 0.5
    out["vsa"] = {
        "buying_climax": bool(out["rvol"] and out["rvol"] >= 2.2 and spread[-1] > 1.5 * avg_spread and last_close_pos < 0.4
                              and out["range"]["pct_from_52w_high"] is not None and out["range"]["pct_from_52w_high"] > -5),
        "selling_climax": bool(out["rvol"] and out["rvol"] >= 2.2 and spread[-1] > 1.5 * avg_spread and last_close_pos > 0.6
                               and out["range"]["pct_above_52w_low"] is not None and out["range"]["pct_above_52w_low"] < 15),
        "spring": bool(n > 25 and np.min(l[-5:]) < np.min(l[-25:-5]) and c[-1] > np.min(l[-25:-5])),
        "upthrust": bool(n > 25 and np.max(h[-5:]) > np.max(h[-25:-5]) and c[-1] < np.max(h[-25:-5])),
        "no_demand_up_bars": int(sum(1 for i in range(n - 5, n) if c[i] > c[i - 1] and v[i] < 0.8 * np.mean(v[-21:-1]))),
    }

    # ── weekly stage (Weinstein) + weekly MACD (Elder tide)
    if idx is not None and n >= 200:
        try:
            _, wh, wl, wc, _ = _weekly(o, h, l, c, v, idx)
            if len(wc) >= 40:
                w30 = sma(wc, 30)
                slope = _pct(float(w30[-1]), float(w30[-5])) if not np.isnan(w30[-5]) else None
                above = wc[-1] > w30[-1]
                if above and (slope or 0) > 0.4:
                    stage = 2
                elif (not above) and (slope or 0) < -0.4:
                    stage = 4
                elif above and (slope or 0) <= 0.4:
                    stage = 3 if np.nanmax(wc[-30:]) > 1.15 * np.nanmin(wc[-30:]) and wc[-1] < 0.97 * np.max(wc[-30:]) else 1
                else:
                    stage = 1 if abs(slope or 0) <= 0.4 else 4
                wm = ema(wc, 12) - ema(wc, 26)
                whist = wm - ema(wm, 9)
                out["weekly"] = {"sma30": _r(float(w30[-1])), "sma30_slope_5w_pct": _r(slope), "price_above_sma30": bool(above),
                                 "stage": int(stage), "macd_hist": _r(whist[-1], 3),
                                 "macd_hist_rising": bool(whist[-1] > whist[-2]),
                                 "weekly_rsi14": _r(_last(rsi(wc, 14)), 1)}
        except Exception:  # noqa: BLE001 — weekly is a bonus read
            pass

    # ── relative strength vs benchmark
    if bench_c is not None and len(bench_c) >= 64:
        b = _f(bench_c)
        m = min(len(b), n)
        bb_, cc_ = b[-m:], c[-m:]
        rs = {}
        for k in (21, 63, 126, 252):
            if m > k:
                rs[str(k)] = _r((cc_[-1] / cc_[-1 - k] - bb_[-1] / bb_[-1 - k]) * 100, 1)
        line = cc_ / bb_
        out["relative_strength"] = {
            "excess_return_pct": rs,
            "rs_line_vs_50d": "above" if line[-1] > np.mean(line[-50:]) else "below",
            "rs_line_new_high_90d": bool(line[-1] >= np.max(line[-90:])) if m >= 90 else None,
            "benchmark_above_200d": bool(bb_[-1] > np.mean(bb_[-200:])) if m >= 200 else None,
            "benchmark_1m_pct": _r(_pct(float(bb_[-1]), float(bb_[-22])) if m > 22 else None, 1),
        }
    return out


# ── the trader lenses ────────────────────────────────────────────────────────

def _rule(rid: str, text: str, value, met: Optional[bool]) -> dict:
    return {"id": rid, "rule": text, "value": value, "met": (None if met is None else bool(met))}


def _lens(key: str, trader: str, philosophy: str, rules: list[dict], exit_level: Optional[float],
          exit_rule: str, d: float, note: str = "", bearish_rules: Optional[list[str]] = None) -> dict:
    scored = [r for r in rules if r["met"] is not None]
    met = sum(1 for r in scored if r["met"])
    d = max(-1.0, min(1.0, d))
    return {
        "key": key, "trader": trader, "philosophy": philosophy, "rules": rules,
        "met": met, "total": len(scored),
        "exit_level": _r(exit_level), "exit_rule": exit_rule,
        # algorithm's reading (STRIPPED before the evidence goes to the LLM):
        "d": round(d, 3),
        "stance": "bullish" if d >= 0.35 else "bearish" if d <= -0.35 else "neutral",
        "note": note,
    }


def trader_lenses(ind: dict) -> list[dict]:
    """Evaluate each famous trader's mechanical ruleset on the indicator suite."""
    if not ind:
        return []
    px = ind["price"]
    sma_ = {k: v for k, v in (ind.get("sma") or {}).items()}
    ema_ = {k: v for k, v in (ind.get("ema") or {}).items()}
    s50, s150, s200 = sma_.get("50"), sma_.get("150"), sma_.get("200")
    rng, prior = ind.get("range") or {}, ind.get("prior_range") or {}
    rs = ind.get("relative_strength") or {}
    ex = (rs.get("excess_return_pct") or {})
    wk = ind.get("weekly") or {}
    adx = (ind.get("adx") or {})
    atr_v = ind.get("atr14") or 0.0
    out: list[dict] = []
    gt = lambda a, b: (None if a is None or b is None else a > b)            # noqa: E731

    # 1 ── Mark Minervini — Trend Template (8 criteria) + his sell discipline
    r = [
        _rule("px_gt_150_200", "Price > 150d & 200d SMA", f"{px} vs {s150}/{s200}", None if None in (s150, s200) else px > s150 and px > s200),
        _rule("150_gt_200", "150d SMA > 200d SMA", f"{s150} vs {s200}", gt(s150, s200)),
        _rule("200_rising", "200d SMA rising ≥ 1 month", ind.get("sma200_slope_1m_pct"), None if ind.get("sma200_slope_1m_pct") is None else ind["sma200_slope_1m_pct"] > 0),
        _rule("50_gt_150_gt_200", "50d > 150d > 200d", f"{s50}/{s150}/{s200}", None if None in (s50, s150, s200) else s50 > s150 > s200),
        _rule("px_gt_50", "Price > 50d SMA", f"{px} vs {s50}", gt(px, s50)),
        _rule("30pct_off_low", "≥ 30% above 52-wk low", rng.get("pct_above_52w_low"), None if rng.get("pct_above_52w_low") is None else rng["pct_above_52w_low"] >= 30),
        _rule("within_25_high", "Within 25% of 52-wk high", rng.get("pct_from_52w_high"), None if rng.get("pct_from_52w_high") is None else rng["pct_from_52w_high"] >= -25),
        _rule("rs_leader", "Outperforming the market over 6-12 months", ex.get("126") if ex.get("126") is not None else ex.get("252"),
              None if (ex.get("126") is None and ex.get("252") is None) else (ex.get("126") if ex.get("126") is not None else ex.get("252")) > 0),
    ]
    ext50 = (ind.get("pct_vs_sma") or {}).get("50")
    r.append(_rule("not_climactic", "Not extended > 25% above the 50d (climax-run risk)", ext50, None if ext50 is None else ext50 <= 25))
    ok = sum(1 for x in r[:8] if x["met"]) / 8.0
    out.append(_lens("minervini", "Mark Minervini", "Trade only Stage-2 leaders; sell when the 50d fails or the run goes climactic.",
                     r, s50, "Close below the 50d SMA on expanding volume (or a 7-8% stop from entry)",
                     d=(ok - 0.5) * 2 - (0.25 if (ext50 or 0) > 25 else 0)))

    # 2 ── William O'Neil — CANSLIM technical discipline
    dd, ad = ind.get("distribution_days_25d", 0), ind.get("accumulation_days_25d", 0)
    r = [
        _rule("above_21ema", "Holding the 21d EMA", (ind.get("pct_vs_ema") or {}).get("21"), None if (ind.get("pct_vs_ema") or {}).get("21") is None else ind["pct_vs_ema"]["21"] > 0),
        _rule("above_50", "Holding the 50d SMA", f"{px} vs {s50}", gt(px, s50)),
        _rule("dist_days", "Distribution days (25 sessions) < 5", dd, dd < 5),
        _rule("accum_gt_dist", "Accumulation days ≥ distribution days", f"{ad} vs {dd}", ad >= dd),
        _rule("updown_vol", "Up-volume ≥ down-volume (20d)", ind.get("up_down_volume_ratio_20d"), None if ind.get("up_down_volume_ratio_20d") is None else ind["up_down_volume_ratio_20d"] >= 1),
        _rule("near_highs", "Within 15% of the 52-wk high", rng.get("pct_from_52w_high"), None if rng.get("pct_from_52w_high") is None else rng["pct_from_52w_high"] >= -15),
    ]
    ok = sum(1 for x in r if x["met"]) / len(r)
    out.append(_lens("oneil", "William O'Neil", "Buy strength on volume; the market tells you — heavy distribution and a lost 50d are the exit.",
                     r, s50, "Heavy-volume close below the 50d, or ≥ 5 distribution days in 25 sessions", d=(ok - 0.5) * 2))

    # 3 ── Stan Weinstein — Stage analysis (30-wk SMA)
    if wk:
        stg = wk["stage"]
        r = [
            _rule("stage", "Stage (1 base · 2 advance · 3 top · 4 decline)", stg, stg == 2),
            _rule("above_30w", "Price above rising 30-wk SMA", f"slope {wk.get('sma30_slope_5w_pct')}%", bool(wk.get("price_above_sma30")) and (wk.get("sma30_slope_5w_pct") or 0) > 0),
            _rule("weekly_macd", "Weekly MACD histogram rising", wk.get("macd_hist"), bool(wk.get("macd_hist_rising"))),
        ]
        dmap = {2: 0.8, 1: 0.0, 3: -0.3, 4: -0.85}
        out.append(_lens("weinstein", "Stan Weinstein", "Only own Stage-2 advances; a break of the 30-wk MA into Stage 3/4 is a sell.",
                         r, wk.get("sma30"), "Weekly close below the 30-week SMA", d=dmap.get(stg, 0.0),
                         note=f"Stage {stg}"))

    # 4 ── Turtles (Dennis / Eckhardt) — Donchian breakout system + N-stop
    hi20, lo10, lo20, lo55 = rng.get("hi_20"), prior.get("lo_10"), prior.get("lo_20"), prior.get("lo_55")
    r = [
        _rule("brk_20", "At / above the 20-day high (System-1 entry zone)", f"{px} vs {prior.get('hi_20')}", None if prior.get("hi_20") is None else px >= prior["hi_20"] * 0.995),
        _rule("above_10d_low", "Above the 10-day low (System-1 exit channel)", f"{px} vs {lo10}", gt(px, lo10)),
        _rule("above_20d_low", "Above the 20-day low (System-2 exit channel)", f"{px} vs {lo20}", gt(px, lo20)),
        _rule("above_55d_low", "Above the 55-day low", f"{px} vs {lo55}", gt(px, lo55)),
    ]
    ok = sum(1 for x in r if x["met"]) / len(r)
    out.append(_lens("turtles", "Richard Dennis · Turtles", "Pure channel breakout; exit when price takes out the 10/20-day opposite extreme; risk = 2N (ATR).",
                     r, lo10, f"Close below the 10-day low {lo10} (2N stop = {_r(px - 2 * atr_v)})", d=(ok - 0.5) * 2))

    # 5 ── Paul Tudor Jones — 200-day MA + asymmetric reward/risk
    p200 = (ind.get("pct_vs_sma") or {}).get("200")
    r = [
        _rule("above_200", "Price above the 200d MA ('my metric for everything')", p200, None if p200 is None else p200 > 0),
        _rule("200_slope", "200d MA sloping up", ind.get("sma200_slope_1m_pct"), None if ind.get("sma200_slope_1m_pct") is None else ind["sma200_slope_1m_pct"] > 0),
        _rule("trend_follow_mom", "63-day momentum positive", (ind.get("roc") or {}).get("63"), None if (ind.get("roc") or {}).get("63") is None else ind["roc"]["63"] > 0),
    ]
    ok = sum(1 for x in r if x["met"]) / len(r)
    out.append(_lens("ptj", "Paul Tudor Jones", "Stay on the right side of the 200d; take only ≥ 5:1 reward/risk; never average a loser.",
                     r, s200, "Close below the 200d MA", d=(ok - 0.5) * 2))

    # 6 ── Stan Druckenmiller — liquidity/trend + relative strength + market tape
    r = [
        _rule("rs_3m", "Beating the market over 3 months", ex.get("63"), None if ex.get("63") is None else ex["63"] > 0),
        _rule("rs_line", "RS line above its 50d", rs.get("rs_line_vs_50d"), None if not rs else rs.get("rs_line_vs_50d") == "above"),
        _rule("mkt_trend", "Market (benchmark) above its 200d", rs.get("benchmark_above_200d"), rs.get("benchmark_above_200d")),
        _rule("mom_21", "21-day momentum positive", (ind.get("roc") or {}).get("21"), None if (ind.get("roc") or {}).get("21") is None else ind["roc"]["21"] > 0),
    ]
    scored = [x for x in r if x["met"] is not None]
    ok = (sum(1 for x in scored if x["met"]) / len(scored)) if scored else 0.5
    out.append(_lens("druckenmiller", "Stan Druckenmiller", "Concentrate where trend, liquidity and relative strength agree; cut fast when the tape or leadership turns.",
                     r, ema_.get("21"), "Relative-strength line breaks below its 50d, or the benchmark loses its 200d", d=(ok - 0.5) * 2))

    # 7 ── Linda Raschke — Holy Grail (ADX>30 pullback to 20 EMA) + 3/10 oscillator
    e20 = ema_.get("20") or ema_.get("21")
    near20 = None if e20 is None or not atr_v else abs(px - e20) <= 0.75 * atr_v
    adxv = adx.get("adx")
    osc = ind.get("osc_3_10") or {}
    r = [
        _rule("adx30", "ADX > 30 (a real trend)", adxv, None if adxv is None else adxv > 30),
        _rule("pullback_20", "Price within ¾ ATR of the 20 EMA (the Holy-Grail pullback)", f"{px} vs {e20}", near20),
        _rule("di_dir", "+DI above −DI", f"{adx.get('plus_di')}/{adx.get('minus_di')}", None if adx.get("plus_di") is None else adx["plus_di"] > adx["minus_di"]),
        _rule("osc_310", "3/10 oscillator positive & rising", osc.get("value"), None if osc.get("value") is None else (osc["value"] > 0 and bool(osc.get("rising")))),
    ]
    dir_sign = 1 if (adx.get("plus_di") or 0) >= (adx.get("minus_di") or 0) else -1
    strength = 0.0 if adxv is None else max(0.0, min(1.0, (adxv - 15) / 25))
    out.append(_lens("raschke", "Linda Raschke", "Trade the pullback in a trending market; exit the moment the 3/10 momentum rolls over against you.",
                     r, e20, "Close through the 20 EMA against the trend, or the 3/10 oscillator crossing zero", d=dir_sign * (0.3 + 0.7 * strength)))

    # 8 ── Larry Connors — RSI(2) mean reversion, with the 200d filter
    r2, s5 = ind.get("rsi2"), sma_.get("10")
    r = [
        _rule("above_200", "Above the 200d (only buy dips in uptrends)", p200, None if p200 is None else p200 > 0),
        _rule("rsi2_low", "RSI(2) < 10 = oversold entry; > 70 = take-profit", r2, None if r2 is None else r2 < 10),
        _rule("rsi2_hot", "RSI(2) > 70 (short-term overbought → exit)", r2, None if r2 is None else r2 > 70),
    ]
    d = 0.0
    if r2 is not None and p200 is not None:
        d = (0.6 if r2 < 10 and p200 > 0 else -0.5 if r2 > 90 and p200 > 0 else 0.3 if p200 > 0 else -0.3)
    out.append(_lens("connors", "Larry Connors", "Short-term mean reversion: buy extreme weakness in an uptrend, sell the first strength (close > 5d MA / RSI2 > 70).",
                     r, s5, "Exit on a close above the 5-day MA or RSI(2) > 70 (it is a short-term trade)", d=d,
                     note=f"RSI2 {r2}"))

    # 9 ── Qullamägi — momentum-burst surfing the 10/20 EMA, ADR gate
    e10 = ema_.get("10")
    adr = ind.get("adr20_pct")
    r = [
        _rule("above_10_20", "Close above the 10 & 20 EMA", f"{px} vs {e10}/{e20}", None if None in (e10, e20) else px > e10 and px > e20),
        _rule("ema10_gt_20", "10 EMA above 20 EMA", f"{e10} vs {e20}", gt(e10, e20)),
        _rule("adr", "ADR(20) ≥ 4% (enough range to be worth trading)", adr, None if adr is None else adr >= 4.0),
        _rule("mom_63", "Strong 3-month momentum (> 25%)", (ind.get("roc") or {}).get("63"), None if (ind.get("roc") or {}).get("63") is None else ind["roc"]["63"] > 25),
    ]
    ok = sum(1 for x in r if x["met"]) / len(r)
    out.append(_lens("qullamaggie", "Kristjan Qullamägi", "Ride the 10/20 EMA surf; the trailing stop is the first daily close below the 10 (or 20) EMA.",
                     r, e10, "First daily close below the 10 EMA (fast) / 20 EMA (slow)", d=(ok - 0.5) * 2))

    # 10 ── Jesse Livermore — pivotal points / line of least resistance
    brk = None if prior.get("hi_20") is None else px >= prior["hi_20"]
    rv = ind.get("rvol")
    r = [
        _rule("pivot_break", "Closing above the prior 20-day high (pivotal point)", f"{px} vs {prior.get('hi_20')}", brk),
        _rule("vol_confirm", "Volume expansion (RVOL ≥ 1.5) on the break", rv, None if rv is None else rv >= 1.5),
        _rule("least_resistance", "50d SMA rising (line of least resistance is up)", (ind.get("pct_vs_sma") or {}).get("50"), None if s50 is None else px > s50),
        _rule("no_failure", "No close back inside the prior range after a break", None, None),
    ]
    ok_scored = [x for x in r if x["met"] is not None]
    ok = (sum(1 for x in ok_scored if x["met"]) / len(ok_scored)) if ok_scored else 0.5
    out.append(_lens("livermore", "Jesse Livermore", "Add only at confirmed pivotal points; a break that fails back into the range is a signal to leave.",
                     r, prior.get("hi_20"), "A close back below the breakout pivot (prior 20-day high)", d=(ok - 0.5) * 2))

    # 11 ── John Carter — TTM Squeeze
    sq, sm = ind.get("squeeze_on"), ind.get("squeeze_momentum") or {}
    r = [
        _rule("squeeze", "Bollinger Bands inside Keltner Channels (energy coiling)", sq, sq),
        _rule("mom_sign", "Squeeze momentum positive", sm.get("value"), None if sm.get("value") is None else sm["value"] > 0),
        _rule("mom_rising", "Squeeze momentum rising", sm.get("rising"), sm.get("rising")),
    ]
    d = 0.0 if sm.get("value") is None else (0.5 if sm["value"] > 0 else -0.5) * (1.0 if sm.get("rising") else 0.6) * (1 if sm["value"] > 0 else 1)
    if sm.get("value") is not None and sm["value"] > 0 and not sm.get("rising"):
        d = 0.15
    if sm.get("value") is not None and sm["value"] < 0 and sm.get("rising"):
        d = -0.15
    out.append(_lens("carter", "John Carter", "Volatility compresses before it expands; trade the release in the momentum direction, exit when momentum fades.",
                     r, ind.get("bollinger", {}).get("lower"), "Squeeze-momentum histogram turning against the position", d=d,
                     note="squeeze ON" if sq else "no squeeze"))

    # 12 ── Goichi Hosoda — Ichimoku
    ich = ind.get("ichimoku")
    if ich:
        r = [
            _rule("cloud", "Price above the cloud", ich["price_vs_cloud"], ich["price_vs_cloud"] == "above"),
            _rule("tk", "Tenkan above Kijun", f"{ich['tenkan']} vs {ich['kijun']}", ich["tk_bullish"]),
            _rule("chikou", "Chikou (lagging) span above price", ich["chikou_above_price"], ich["chikou_above_price"]),
        ]
        d = (sum(1 for x in r if x["met"]) / 3.0 - 0.5) * 2
        if ich["price_vs_cloud"] == "inside":
            d *= 0.4
        out.append(_lens("ichimoku", "Ichimoku (Hosoda)", "Trend, momentum and support in one frame; price in/under the cloud is no-man's-land or a downtrend.",
                         r, ich["kijun"], "Close below the Kijun-sen (or into the cloud)", d=d))

    # 13 ── Alexander Elder — Triple Screen + Impulse
    r = [
        _rule("tide", "Weekly MACD histogram rising (the tide)", wk.get("macd_hist"), wk.get("macd_hist_rising")),
        _rule("wave", "Daily Stochastic not overbought (< 80)", (ind.get("stoch") or {}).get("percent_k"), None if (ind.get("stoch") or {}).get("percent_k") is None else ind["stoch"]["percent_k"] < 80),
        _rule("impulse", "Impulse system green (EMA13 & MACD-hist both rising)", ind.get("elder_impulse"), ind.get("elder_impulse") == "green"),
    ]
    imp = ind.get("elder_impulse")
    d = (0.6 if imp == "green" else -0.6 if imp == "red" else 0.0) + (0.2 if wk.get("macd_hist_rising") else -0.2 if wk else 0.0)
    out.append(_lens("elder", "Alexander Elder", "Trade with the weekly tide, time the entry/exit with the daily wave; a red impulse bar vetoes new longs.",
                     r, ema_.get("13"), "Impulse turns red / weekly tide turns down", d=d))

    # 14 ── Wyckoff / VSA — effort vs result
    vsa = ind.get("vsa") or {}
    r = [
        _rule("buy_climax", "Buying climax (huge volume + wide spread + weak close at highs)", vsa.get("buying_climax"), None if vsa.get("buying_climax") is None else not vsa["buying_climax"]),
        _rule("upthrust", "No upthrust (break of highs that closes back inside)", vsa.get("upthrust"), None if vsa.get("upthrust") is None else not vsa["upthrust"]),
        _rule("no_demand", "No 'no-demand' up-bars (low-volume rallies) in the last 5", vsa.get("no_demand_up_bars"), None if vsa.get("no_demand_up_bars") is None else vsa["no_demand_up_bars"] < 2),
        _rule("spring", "Spring (undercut + reclaim) — bullish", vsa.get("spring"), vsa.get("spring")),
        _rule("sell_climax", "Selling climax (capitulation at lows) — bullish", vsa.get("selling_climax"), vsa.get("selling_climax")),
    ]
    d = 0.0
    d += -0.6 if vsa.get("buying_climax") else 0
    d += -0.5 if vsa.get("upthrust") else 0
    d += -0.2 if (vsa.get("no_demand_up_bars") or 0) >= 2 else 0
    d += 0.5 if vsa.get("spring") else 0
    d += 0.4 if vsa.get("selling_climax") else 0
    out.append(_lens("wyckoff", "Wyckoff / VSA", "Read effort vs result: climactic volume without progress = the smart money is distributing (or absorbing).",
                     r, prior.get("lo_20"), "Upthrust / buying-climax at highs, or a break of the range low on volume", d=d))

    # 15 ── Trend-following CTAs (Dunn / Seykota) — supertrend + chandelier
    stn, ch = ind.get("supertrend") or {}, ind.get("chandelier") or {}
    r = [
        _rule("supertrend", "Supertrend(10,3) direction up", stn.get("direction"), None if not stn else stn["direction"] == "up"),
        _rule("chandelier", "Price above the Chandelier long-stop", f"{px} vs {ch.get('long_stop')}", gt(px, ch.get("long_stop"))),
        _rule("ema_stack", "EMA 10 > 20 > 50", f"{e10}/{e20}/{ema_.get('50')}", None if None in (e10, e20, ema_.get("50")) else e10 > e20 > ema_["50"]),
    ]
    ok = sum(1 for x in r if x["met"]) / len(r)
    out.append(_lens("cta", "Trend-following CTAs (Seykota / Dunn)", "Cut losses short, ride winners: a volatility-scaled trailing stop is the only exit.",
                     r, ch.get("long_stop"), f"Close below the Chandelier stop {ch.get('long_stop')} / supertrend flip", d=(ok - 0.5) * 2))
    return out


def lens_consensus(lenses: list[dict]) -> dict:
    """Aggregate directional read across lenses: mean d, agreement, bull/bear counts. (Algorithm judgement.)"""
    if not lenses:
        return {"d": 0.0, "bull": 0, "bear": 0, "neutral": 0, "n": 0}
    ds = [l["d"] for l in lenses]
    return {"d": round(float(np.mean(ds)), 3),
            "bull": sum(1 for l in lenses if l["stance"] == "bullish"),
            "bear": sum(1 for l in lenses if l["stance"] == "bearish"),
            "neutral": sum(1 for l in lenses if l["stance"] == "neutral"),
            "n": len(lenses)}
