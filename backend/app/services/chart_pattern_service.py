"""Classical chart-pattern recognition — the shapes discretionary traders draw by hand.

There is no reliable turnkey library for these *multi-swing geometric* patterns (ta-lib only
covers single-candle shapes), so this is a rule-based geometric engine built on **scipy** swing
pivots — the professional standard. From a clean ZigZag of alternating swing highs/lows it
recognises:

  reversal      — Double / Triple Top & Bottom, Head-&-Shoulders (+ inverse), Rounding Bottom
  continuation  — Ascending / Descending / Symmetrical Triangle, Bull / Bear Flag, Pennant,
                  Cup-&-Handle, VCP (volatility contraction), Rising / Falling Wedge
  overlay       — Fibonacci retracement of the dominant swing

Every detection returns *drawable geometry* (pivot points + trend/necklines, indexed to the OHLC
series so the chart can overlay them), a breakout trigger, a measured-move target, a confidence,
and a plain-English **education** block explaining exactly where the shape sits on THIS chart and
how to spot it yourself. Pure numpy/scipy, best-effort — never raises.
"""
from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema


# ---------------------------------------------------------------------------
# fetch + swing detection
# ---------------------------------------------------------------------------

def _history(stock, period: str = "1y", interval: str = "1d"):
    try:
        df = stock.history(period=period, interval=interval)
        if df is None or df.empty:
            return None
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        return df if len(df) >= 40 else None
    except Exception:  # noqa: BLE001
        return None


def _date_str(x) -> str:
    try:
        return pd.Timestamp(x).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return str(x)[:10]


def _atr(df, period: int = 14) -> float:
    h, l, c = df["High"].values.astype(float), df["Low"].values.astype(float), df["Close"].values.astype(float)
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    n = min(period, len(tr))
    return float(np.mean(tr[-n:])) if n else 0.0


def _zigzag(df, order: int = 5) -> list[dict]:
    """Alternating swing highs/lows via ``argrelextrema``. Consecutive same-kind pivots are
    collapsed to the more extreme one so the result strictly alternates H, L, H, L…"""
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    n = len(highs)
    if n < 2 * order + 3:
        return []
    hi = argrelextrema(highs, np.greater, order=order)[0]
    lo = argrelextrema(lows, np.less, order=order)[0]
    piv = [(int(i), float(highs[i]), "H") for i in hi] + [(int(i), float(lows[i]), "L") for i in lo]
    piv.sort(key=lambda x: x[0])
    if not piv:
        return []
    merged: list[tuple] = [piv[0]]
    for i, pr, k in piv[1:]:
        li, lpr, lk = merged[-1]
        if k == lk:
            if (k == "H" and pr >= lpr) or (k == "L" and pr <= lpr):
                merged[-1] = (i, pr, k)
        else:
            merged.append((i, pr, k))
    return [{"idx": i, "price": round(pr, 2), "kind": k, "date": _date_str(df.index[i])} for i, pr, k in merged]


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def _similar(a: float, b: float, tol: float = 0.03) -> bool:
    m = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / m <= tol


def _P(df, piv: dict, label: str) -> dict:
    return {"idx": piv["idx"], "date": piv["date"], "price": piv["price"], "label": label}


def _pt(df, idx: int, price: float, label: str) -> dict:
    idx = int(max(0, min(idx, len(df) - 1)))
    return {"idx": idx, "date": _date_str(df.index[idx]), "price": round(float(price), 2), "label": label}


def _line(df, i1: int, p1: float, i2: int, p2: float, label: str, kind: str) -> dict:
    return {"from": _pt(df, i1, p1, ""), "to": _pt(df, i2, p2, ""), "label": label, "kind": kind}


def _fit_line(idxs, prices):
    """Least-squares line price = m·idx + b over the given pivots → (slope, intercept)."""
    if len(idxs) < 2:
        return 0.0, float(prices[0]) if prices else 0.0
    m, b = np.polyfit(np.asarray(idxs, float), np.asarray(prices, float), 1)
    return float(m), float(b)


def _at(m: float, b: float, x: int) -> float:
    return m * x + b


def _r(x, nd: int = 2):
    try:
        xf = float(x)
        return round(xf, nd) if np.isfinite(xf) else None
    except (TypeError, ValueError):
        return None


def _pattern(ptype, name, category, direction, status, confidence, *, points, lines,
             breakout, target, stop, as_of_idx, education, extra=None) -> dict:
    d = {
        "type": ptype, "name": name, "category": category, "direction": direction,
        "status": status, "confidence": round(float(confidence), 2),
        "points": points, "lines": lines, "breakout": breakout, "target": target,
        "stop": _r(stop) if stop is not None else None, "as_of_idx": int(as_of_idx),
        "education": education,
    }
    if extra:
        d.update(extra)
    return d


def _tgt(price, close, method):
    price = round(float(price), 2)
    return {"price": price, "method": method,
            "pct": round((price - close) / close * 100, 1) if close else None}


# ---------------------------------------------------------------------------
# reversal patterns
# ---------------------------------------------------------------------------

def _double_top(z, df, close, atr):
    for s in range(len(z) - 1, 1, -1):
        a, b, c = z[s - 2], z[s - 1], z[s]
        if not (a["kind"] == "H" and b["kind"] == "L" and c["kind"] == "H"):
            continue
        p1, trough, p2 = a["price"], b["price"], c["price"]
        if not _similar(p1, p2, 0.03):                          # the two tops must be ~equal (≤3%)
            continue
        peak = max(p1, p2); neck = trough; depth = peak - neck
        if depth <= 0 or depth < 0.6 * atr:
            continue
        broke = close < neck
        # INVALIDATION: a higher-high after peak 2 means the top was overrun (an uptrend, not a top);
        # and a still-FORMING top must have rolled over into the lower half toward the neckline —
        # not be pinned back up near the peaks (that's just resistance / a failed top).
        post_high = df["High"].values[c["idx"] + 1:]
        if post_high.size and float(post_high.max()) > peak * 1.005:
            continue
        if not broke and close > neck + 0.55 * depth:
            continue
        conf = 0.55 + 0.2 * (1 - abs(p1 - p2) / peak) + (0.2 if broke else 0.0)
        tgt = neck - depth
        return _pattern(
            "double_top", "Double Top", "reversal", "bearish",
            "broken_out" if broke else "forming", min(0.95, conf),
            points=[_P(df, a, "Peak 1"), _P(df, b, "Neckline"), _P(df, c, "Peak 2")],
            lines=[_line(df, b["idx"], neck, len(df) - 1, neck, "Neckline (support)", "neckline")],
            breakout={"level": round(neck, 2), "side": "down"},
            target=_tgt(tgt, close, "neckline − pattern height"),
            stop=round(peak + 0.4 * atr, 2), as_of_idx=c["idx"],
            education={
                "what": "Two peaks at roughly the same price with a trough between — buyers twice failed to break higher. A bearish reversal.",
                "where": f"Peak 1 ≈ ${p1} and Peak 2 ≈ ${p2} are the two 'M' tops; the neckline is the trough at ${round(neck,2)}.",
                "how_to_spot": "Look for an 'M': two highs within ~3% of each other separated by a clear dip. The dip low is the neckline.",
                "confirms": f"A daily close below the ${round(neck,2)} neckline confirms it; the measured target is ${round(tgt,2)} (neckline minus the peak-to-neckline height).",
                "invalidates": f"A close back above the peaks at ${round(peak + 0.4 * atr, 2)} — that's the protective stop.",
            })
    return None


def _double_bottom(z, df, close, atr):
    for s in range(len(z) - 1, 1, -1):
        a, b, c = z[s - 2], z[s - 1], z[s]
        if not (a["kind"] == "L" and b["kind"] == "H" and c["kind"] == "L"):
            continue
        p1, peak, p2 = a["price"], b["price"], c["price"]
        if not _similar(p1, p2, 0.03):                          # the two bottoms must be ~equal (≤3%)
            continue
        trough = min(p1, p2); neck = peak; height = neck - trough
        if height <= 0 or height < 0.6 * atr:
            continue
        broke = close > neck
        # INVALIDATION (mirror of the double top): a lower-low after bottom 2 breaks the base; and a
        # still-FORMING bottom must have rallied into the upper half toward the neckline.
        post_low = df["Low"].values[c["idx"] + 1:]
        if post_low.size and float(post_low.min()) < trough * 0.995:
            continue
        if not broke and close < neck - 0.55 * height:
            continue
        conf = 0.55 + 0.2 * (1 - abs(p1 - p2) / max(neck, 1e-9)) + (0.2 if broke else 0.0)
        tgt = neck + height
        return _pattern(
            "double_bottom", "Double Bottom", "reversal", "bullish",
            "broken_out" if broke else "forming", min(0.95, conf),
            points=[_P(df, a, "Bottom 1"), _P(df, b, "Neckline"), _P(df, c, "Bottom 2")],
            lines=[_line(df, b["idx"], neck, len(df) - 1, neck, "Neckline (resistance)", "neckline")],
            breakout={"level": round(neck, 2), "side": "up"},
            target=_tgt(tgt, close, "neckline + pattern height"),
            stop=round(trough - 0.4 * atr, 2), as_of_idx=c["idx"],
            education={
                "what": "Two troughs at roughly the same price with a peak between — sellers twice failed to push lower. A bullish reversal ('W').",
                "where": f"Bottom 1 ≈ ${p1} and Bottom 2 ≈ ${p2} are the two lows; the neckline is the peak at ${round(neck,2)}.",
                "how_to_spot": "Look for a 'W': two lows within ~3% of each other with a bounce between. The bounce high is the neckline.",
                "confirms": f"A daily close above the ${round(neck,2)} neckline confirms it; the measured target is ${round(tgt,2)}.",
                "invalidates": f"A close below the lows at ${round(trough - 0.4 * atr, 2)} — that's the protective stop.",
            })
    return None


def _head_shoulders(z, df, close, atr, inverse=False):
    """H&S = H,L,H(higher),L,H with the middle peak highest and the shoulders ~equal; neckline is
    the line through the two troughs. Inverse mirrors with lows."""
    want = ["L", "H", "L", "H", "L"] if inverse else ["H", "L", "H", "L", "H"]
    for s in range(len(z) - 1, 3, -1):
        seq = z[s - 4:s + 1]
        if len(seq) < 5 or [p["kind"] for p in seq] != want:
            continue
        ls, t1, head, t2, rs = seq
        lsp, hp, rsp = ls["price"], head["price"], rs["price"]
        if inverse:
            if not (hp < lsp and hp < rsp and _similar(lsp, rsp, 0.06)):
                continue
        else:
            if not (hp > lsp and hp > rsp and _similar(lsp, rsp, 0.06)):
                continue
        # the neckline connects the two troughs — they MUST sit at ~the same level (a roughly
        # horizontal neckline). Troughs at very different prices ($287 vs $331) produce an absurd
        # steep-diagonal neckline (the HD false positive).
        if not _similar(t1["price"], t2["price"], 0.05):
            continue
        m, b = _fit_line([t1["idx"], t2["idx"]], [t1["price"], t2["price"]])
        neck_now = _at(m, b, len(df) - 1)
        neck_head = _at(m, b, head["idx"])
        height = (neck_head - hp) if inverse else (hp - neck_head)
        if height <= 0 or height < 0.8 * atr:
            continue
        broke = close > neck_now if inverse else close < neck_now
        # INVALIDATION: price back beyond the HEAD after the right shoulder means the pattern failed;
        # a still-forming one must be heading toward the neckline break, not pinned at the head.
        if not inverse:
            post = df["High"].values[rs["idx"] + 1:]
            if (post.size and float(post.max()) > hp * 1.005) or (not broke and close > neck_now + 0.6 * height):
                continue
        else:
            post = df["Low"].values[rs["idx"] + 1:]
            if (post.size and float(post.min()) < hp * 0.995) or (not broke and close < neck_now - 0.6 * height):
                continue
        tgt = neck_now + height if inverse else neck_now - height
        conf = 0.55 + 0.2 * (1 - abs(lsp - rsp) / max(lsp, rsp)) + (0.2 if broke else 0.0)
        direction = "bullish" if inverse else "bearish"
        name = "Inverse Head & Shoulders" if inverse else "Head & Shoulders"
        stop = round((min(lsp, rsp) - 0.4 * atr) if inverse else (max(lsp, rsp) + 0.4 * atr), 2)
        return _pattern(
            "inverse_head_shoulders" if inverse else "head_shoulders", name, "reversal", direction,
            "broken_out" if broke else "forming", min(0.95, conf),
            points=[_P(df, ls, "Left shoulder"), _P(df, head, "Head"), _P(df, rs, "Right shoulder"),
                    _P(df, t1, "Neck 1"), _P(df, t2, "Neck 2")],
            lines=[_line(df, t1["idx"], t1["price"], len(df) - 1, neck_now, "Neckline", "neckline")],
            breakout={"level": round(neck_now, 2), "side": "up" if inverse else "down"},
            target=_tgt(tgt, close, "neckline ± head height"),
            stop=stop, as_of_idx=rs["idx"],
            education={
                "what": (("A bottoming pattern: a low (head) between two higher lows (shoulders) — sellers exhausted. Bullish reversal."
                          if inverse else
                          "A topping pattern: a high (head) between two lower highs (shoulders) — buyers exhausted. Bearish reversal.")),
                "where": f"Left shoulder ${lsp}, head ${hp}, right shoulder ${rsp}; the neckline runs through the two intervening turns.",
                "how_to_spot": "Three pushes where the middle is the most extreme and the outer two are similar. Draw the neckline through the two reaction points between them.",
                "confirms": f"A close {'above' if inverse else 'below'} the neckline (~${round(neck_now,2)}) confirms; measured target ${round(tgt,2)}.",
                "invalidates": (f"A close back {'below' if inverse else 'above'} the right shoulder at ${stop} — "
                                f"that's the protective stop (the head at ${hp} is the last-ditch line).")
            })
    return None


def _rounding_bottom(z, df, close, atr):
    """Smooth U over a long window: fit a parabola to closes; positive curvature + low residual +
    price near/above the right rim = a bullish rounding bottom (saucer)."""
    c = df["Close"].values.astype(float)
    n = len(c)
    win = min(120, n)
    if win < 60:
        return None
    seg = c[-win:]
    x = np.arange(win, dtype=float)
    try:
        a2, a1, a0 = np.polyfit(x, seg, 2)
    except Exception:  # noqa: BLE001
        return None
    if a2 <= 0:                                        # must be convex (smile)
        return None
    fit = a2 * x * x + a1 * x + a0
    ss_res = float(np.sum((seg - fit) ** 2))
    ss_tot = float(np.sum((seg - seg.mean()) ** 2)) or 1e-9
    r2 = 1 - ss_res / ss_tot
    vertex = -a1 / (2 * a2)
    if r2 < 0.6 or not (win * 0.2 < vertex < win * 0.8):   # smooth, with the low in the middle
        return None
    rim = float(max(seg[0], seg[-1]))
    trough = float(seg.min())
    depth = rim - trough
    if depth < 1.2 * atr:
        return None
    broke = close >= rim
    tgt = rim + depth
    conf = min(0.9, 0.5 + 0.3 * r2 + (0.1 if broke else 0.0))
    lo_idx = n - win + int(round(vertex))
    return _pattern(
        "rounding_bottom", "Rounding Bottom", "reversal", "bullish",
        "broken_out" if broke else "forming", conf,
        points=[_pt(df, n - win, seg[0], "Left rim"), _pt(df, lo_idx, trough, "Base"),
                _pt(df, n - 1, seg[-1], "Right rim")],
        lines=[_line(df, n - win, rim, n - 1, rim, "Rim (breakout)", "resistance")],
        breakout={"level": round(rim, 2), "side": "up"},
        target=_tgt(tgt, close, "rim + cup depth"),
        stop=trough + 0.5 * depth, as_of_idx=n - 1,
        education={
            "what": "A long, smooth 'U' — a gradual handover from sellers to buyers over months. Bullish accumulation.",
            "where": f"The base sits near ${round(trough,2)}; the rim (breakout line) is ${round(rim,2)}.",
            "how_to_spot": "A gentle, symmetric bowl in the closes — no sharp V. Price rounds down then back up to the prior rim.",
            "confirms": f"A close above the ${round(rim,2)} rim confirms; target ${round(tgt,2)} (rim + bowl depth).",
            "invalidates": "A sharp breakdown below the base voids the accumulation read.",
        })


# ---------------------------------------------------------------------------
# continuation patterns
# ---------------------------------------------------------------------------

def _triangle(z, df, close, atr):
    if len(z) < 4:
        return None
    recent = z[-6:]
    hs = [p for p in recent if p["kind"] == "H"]
    ls = [p for p in recent if p["kind"] == "L"]
    if len(hs) < 2 or len(ls) < 2:
        return None
    x_now = len(df) - 1
    mh, bh = _fit_line([p["idx"] for p in hs], [p["price"] for p in hs])
    ml, bl = _fit_line([p["idx"] for p in ls], [p["price"] for p in ls])
    res, sup = _at(mh, bh, x_now), _at(ml, bl, x_now)
    if res <= sup:
        return None
    flat = 0.0006 * close
    hi_flat, lo_flat = abs(mh) < flat, abs(ml) < flat
    rising_lows, falling_highs = ml > flat, mh < -flat
    height = max(p["price"] for p in hs) - min(p["price"] for p in ls)
    first = min(p["idx"] for p in hs + ls)

    def sup_line():
        return _line(df, ls[0]["idx"], _at(ml, bl, ls[0]["idx"]), x_now, sup, "Support trendline", "trendline")

    def res_line():
        return _line(df, hs[0]["idx"], _at(mh, bh, hs[0]["idx"]), x_now, res, "Resistance trendline", "trendline")

    if hi_flat and rising_lows:
        lvl = float(np.mean([p["price"] for p in hs])); broke = close > lvl
        return _pattern(
            "ascending_triangle", "Ascending Triangle", "continuation", "bullish",
            "broken_out" if broke else "forming", 0.6 + (0.15 if broke else 0.0),
            points=[_P(df, p, "Higher low") for p in ls] + [_P(df, p, "Resistance touch") for p in hs],
            lines=[_line(df, first, lvl, x_now, lvl, "Flat resistance", "resistance"), sup_line()],
            breakout={"level": round(lvl, 2), "side": "up"},
            target=_tgt(lvl + height, close, "resistance + triangle height"),
            stop=round(sup - 0.4 * atr, 2), as_of_idx=x_now,
            education={
                "what": "A flat ceiling with rising lows — buyers get more aggressive while sellers defend one price. Bullish continuation.",
                "where": f"Resistance is flat near ${round(lvl,2)}; support rises from ${ls[0]['price']} to ${round(sup,2)}.",
                "how_to_spot": "Horizontal line across the highs, an up-sloping line under the lows, squeezing toward the ceiling.",
                "confirms": f"A close above ${round(lvl,2)} breaks out; target ${round(lvl+height,2)} (add the triangle height).",
                "invalidates": f"A close below the rising support at ${round(sup - 0.4 * atr, 2)} — that's the protective stop.",
            })
    if lo_flat and falling_highs:
        lvl = float(np.mean([p["price"] for p in ls])); broke = close < lvl
        return _pattern(
            "descending_triangle", "Descending Triangle", "continuation", "bearish",
            "broken_out" if broke else "forming", 0.6 + (0.15 if broke else 0.0),
            points=[_P(df, p, "Lower high") for p in hs] + [_P(df, p, "Support touch") for p in ls],
            lines=[_line(df, first, lvl, x_now, lvl, "Flat support", "support"), res_line()],
            breakout={"level": round(lvl, 2), "side": "down"},
            target=_tgt(lvl - height, close, "support − triangle height"),
            stop=round(res + 0.4 * atr, 2), as_of_idx=x_now,
            education={
                "what": "A flat floor with falling highs — sellers get more aggressive while buyers defend one price. Bearish continuation.",
                "where": f"Support is flat near ${round(lvl,2)}; resistance falls from ${hs[0]['price']} to ${round(res,2)}.",
                "how_to_spot": "Horizontal line across the lows, a down-sloping line over the highs, squeezing toward the floor.",
                "confirms": f"A close below ${round(lvl,2)} breaks down; target ${round(lvl-height,2)}.",
                "invalidates": f"A close above the falling resistance at ${round(res + 0.4 * atr, 2)} — that's the protective stop.",
            })
    if falling_highs and rising_lows:
        broke = close > res or close < sup
        side = "up" if close >= (res + sup) / 2 else "down"
        direction = "bullish" if side == "up" else "bearish"
        lvl = res if side == "up" else sup
        tgt = (lvl + height) if side == "up" else (lvl - height)
        return _pattern(
            "symmetrical_triangle", "Symmetrical Triangle", "continuation", direction,
            "broken_out" if broke else "forming", 0.55 + (0.15 if broke else 0.0),
            points=[_P(df, p, "Lower high") for p in hs] + [_P(df, p, "Higher low") for p in ls],
            lines=[res_line(), sup_line()],
            breakout={"level": round(lvl, 2), "side": side},
            target=_tgt(tgt, close, "apex ± triangle height"),
            stop=(sup - 0.4 * atr) if side == "up" else (res + 0.4 * atr), as_of_idx=x_now,
            education={
                "what": "Lower highs AND higher lows coiling into an apex — a compression that usually resolves in the prior trend's direction.",
                "where": f"Resistance falls to ${round(res,2)}, support rises to ${round(sup,2)} — they converge just ahead.",
                "how_to_spot": "Two converging trendlines forming a sideways wedge; volatility dries up into the point.",
                "confirms": f"A close beyond either line ({round(sup,2)}–{round(res,2)}) triggers; measured move ≈ the triangle's height.",
                "invalidates": "A failed break that snaps straight back inside is a trap — wait for a decisive close.",
            })
    return None


def _flag(z, df, close, atr, bull=True):
    if len(z) < 3:
        return None
    a, b, c = z[-3], z[-2], z[-1]
    if bull:
        if not (a["kind"] == "L" and b["kind"] == "H" and c["kind"] == "L"):
            return None
        pole, retr, lvl = b["price"] - a["price"], b["price"] - c["price"], b["price"]
    else:
        if not (a["kind"] == "H" and b["kind"] == "L" and c["kind"] == "H"):
            return None
        pole, retr, lvl = a["price"] - b["price"], c["price"] - b["price"], b["price"]
    if pole <= 3 * atr or not (0.1 * pole < retr < 0.5 * pole):
        return None
    broke = (close > lvl) if bull else (close < lvl)
    tgt = (lvl + pole) if bull else (lvl - pole)
    # Single source of truth: the stop = the flag's far boundary + a buffer, and the invalidation
    # text cites the SAME number (a flag is broken by a close out the wrong side of its channel).
    stop = round((c["price"] - 0.4 * atr) if bull else (c["price"] + 0.4 * atr), 2)
    name = "Bull Flag" if bull else "Bear Flag"
    return _pattern(
        "bull_flag" if bull else "bear_flag", name, "continuation", "bullish" if bull else "bearish",
        "broken_out" if broke else "forming", 0.6 + (0.15 if broke else 0.0),
        points=[_P(df, a, "Pole start"), _P(df, b, "Pole end / breakout"), _P(df, c, "Flag")],
        lines=[_line(df, a["idx"], a["price"], b["idx"], b["price"], "Pole", "pole"),
               _line(df, b["idx"], lvl, len(df) - 1, lvl, "Breakout", "resistance" if bull else "support")],
        breakout={"level": round(lvl, 2), "side": "up" if bull else "down"},
        target=_tgt(tgt, close, "breakout ± pole height"),
        stop=stop, as_of_idx=c["idx"],
        education={
            "what": (("A sharp rally (the pole) then a shallow, orderly pullback (the flag) — a pause that refreshes. Bullish continuation."
                      if bull else
                      "A sharp drop (the pole) then a shallow bounce (the flag) — a pause before more downside. Bearish continuation.")),
            "where": f"The pole runs ${a['price']}→${b['price']}; the flag is the shallow drift back to ${c['price']}.",
            "how_to_spot": "One strong, near-vertical thrust, then a small counter-trend channel retracing less than half of it.",
            "confirms": f"A close {'above' if bull else 'below'} the ${round(lvl,2)} flag {'high' if bull else 'low'} resumes the move; target ${round(tgt,2)} (project the pole from the breakout).",
            "invalidates": (f"A close back {'below' if bull else 'above'} the flag {'low' if bull else 'high'} at "
                            f"${stop} breaks the flag — that's the protective stop (a healthy flag also shouldn't "
                            f"retrace more than ~half the pole)."),
        })


def _cup_handle(z, df, close, atr):
    c = df["Close"].values.astype(float)
    n = len(c)
    handle = min(20, max(5, n // 12))
    win = min(140, n - handle)
    if win < 50:
        return None
    seg = c[-(win + handle):-handle]
    left_rim, right_rim = float(seg[0]), float(seg[-1])
    # A cup RETURNS to the level it started from — its two rims sit at ~the same price. Without
    # this, a V-recovery / uptrend that blew through the old rim gets mislabelled a cup (QQQ bug).
    if not _similar(left_rim, right_rim, 0.06):
        return None
    rim = (left_rim + right_rim) / 2.0
    base = float(seg.min()); depth = rim - base
    base_idx = int(np.argmin(seg))
    if depth < max(1.2 * atr, 0.08 * rim) or depth > 0.5 * rim:   # a real bowl: not shallow, not absurdly deep
        return None
    if not (len(seg) * 0.25 < base_idx < len(seg) * 0.75):       # the low sits in the MIDDLE (a bowl, not a slope)
        return None
    if float(seg.max()) > rim * 1.05:                            # the cup interior must not spike above the rim
        return None
    x = np.arange(len(seg), dtype=float)
    try:
        a2, a1, a0 = np.polyfit(x, seg, 2)
    except Exception:  # noqa: BLE001
        return None
    if a2 <= 0:
        return None
    fit = a2 * x * x + a1 * x + a0
    r2 = 1 - float(np.sum((seg - fit) ** 2)) / (float(np.sum((seg - seg.mean()) ** 2)) or 1e-9)
    if r2 < 0.55:
        return None
    hseg = c[-handle:]
    handle_high, handle_low = float(hseg.max()), float(hseg.min())
    # a valid handle drifts DOWN in the upper third of the cup and stays shallow
    if handle_low < rim - 0.5 * depth or handle_high > rim * 1.03:
        return None
    broke = close > rim
    tgt = rim + depth
    conf = min(0.9, 0.5 + 0.3 * r2 + (0.1 if broke else 0.0))
    return _pattern(
        "cup_handle", "Cup & Handle", "continuation", "bullish",
        "broken_out" if broke else "forming", conf,
        points=[_pt(df, n - win - handle, seg[0], "Left rim"),
                _pt(df, n - handle - (len(seg) - int(np.argmin(seg))), base, "Cup base"),
                _pt(df, n - handle, seg[-1], "Right rim"), _pt(df, n - 1, close, "Handle")],
        lines=[_line(df, n - win - handle, rim, n - 1, rim, "Rim (buy line)", "resistance")],
        breakout={"level": round(rim, 2), "side": "up"},
        target=_tgt(tgt, close, "rim + cup depth"),
        stop=round(handle_low - 0.4 * atr, 2), as_of_idx=n - 1,
        education={
            "what": "A rounded 'U' base (the cup) followed by a small, shallow drift lower (the handle) near the rim — accumulation before a breakout. Bullish.",
            "where": f"The cup base is ~${round(base,2)}, the rim (buy line) is ${round(rim,2)}, and the recent drift is the handle.",
            "how_to_spot": "A smooth bowl (not a sharp V) that returns to its starting rim, then a tight pullback of a few percent in the last couple of weeks.",
            "confirms": f"A close above the ${round(rim,2)} rim on rising volume triggers; target ${round(tgt,2)} (rim + cup depth).",
            "invalidates": f"A close below the handle low at ${round(handle_low - 0.4 * atr, 2)} — that's the protective stop (a handle that sinks past the mid-cup isn't a proper handle either).",
        })


def _vcp(z, df, close, atr):
    legs = []
    for i in range(len(z) - 1, 0, -1):
        if z[i]["kind"] == "L" and z[i - 1]["kind"] == "H":
            hi, lo = z[i - 1]["price"], z[i]["price"]
            legs.append((z[i - 1]["idx"], z[i]["idx"], (hi - lo) / max(hi, 1e-9), hi, lo))
        if len(legs) >= 3:
            break
    legs = legs[::-1]
    if len(legs) < 2:
        return None
    depths = [d for _, _, d, _, _ in legs]
    if not all(depths[k + 1] < depths[k] * 0.8 for k in range(len(depths) - 1)) or depths[-1] > 0.12:
        return None
    pivot = max((p["price"] for p in z[-6:] if p["kind"] == "H"), default=close)
    broke = close > pivot
    tgt = pivot * (1 + depths[0])                       # project the first (deepest) base off the pivot
    conf = min(0.88, 0.55 + 0.1 * len(legs) + (0.1 if broke else 0.0))
    pts = []
    for j, (hidx, lidx, d, hi, lo) in enumerate(legs):
        pts.append(_pt(df, hidx, hi, f"High {j+1}"))
        pts.append(_pt(df, lidx, lo, f"Contraction {j+1} (−{round(d*100,1)}%)"))
    return _pattern(
        "vcp", "Volatility Contraction (VCP)", "continuation", "bullish",
        "broken_out" if broke else "forming", conf,
        points=pts,
        lines=[_line(df, z[-6]["idx"] if len(z) >= 6 else legs[0][0], pivot, len(df) - 1, pivot, "Pivot (buy line)", "resistance")],
        breakout={"level": round(pivot, 2), "side": "up"},
        target=_tgt(tgt, close, "pivot + first-base depth"),
        stop=legs[-1][4] - 0.3 * atr, as_of_idx=len(df) - 1,
        education={
            "what": "A staircase of progressively SMALLER pullbacks (e.g. −20%, then −12%, then −6%) as supply dries up — Minervini's setup before a breakout. Bullish.",
            "where": "Each labelled contraction is tighter than the last; the flat 'pivot' line above is the buy trigger.",
            "how_to_spot": "Count the pullbacks left→right — each should be shallower than the one before, with volume falling into the tightest one.",
            "confirms": f"A close above the ${round(pivot,2)} pivot on a volume surge triggers; a common target is the pivot plus the first base's depth (~${round(tgt,2)}).",
            "invalidates": "A pullback DEEPER than the previous one breaks the contraction sequence.",
        })


def _wedge(z, df, close, atr):
    if len(z) < 4:
        return None
    recent = z[-6:]
    hs = sorted([p for p in recent if p["kind"] == "H"], key=lambda p: p["idx"])
    ls = sorted([p for p in recent if p["kind"] == "L"], key=lambda p: p["idx"])
    if len(hs) < 2 or len(ls) < 2:
        return None
    hp = [p["price"] for p in hs]; lp = [p["price"] for p in ls]
    desc = lambda v: all(v[i] > v[i + 1] for i in range(len(v) - 1))   # strictly lower
    asc = lambda v: all(v[i] < v[i + 1] for i in range(len(v) - 1))    # strictly higher
    # A wedge is defined by its PIVOTS: a falling wedge is lower-highs AND lower-lows; a rising wedge
    # is higher-highs AND higher-lows. Reject anything that isn't monotonic (the GOOG bug labelled a
    # $382 higher-high a "falling high"). This also prevents boundary violations by construction.
    falling = desc(hp) and desc(lp)
    rising = asc(hp) and asc(lp)
    if not (falling or rising):
        return None
    x_now = len(df) - 1
    first = min(p["idx"] for p in hs + ls)
    mh, _ = _fit_line([p["idx"] for p in hs], hp)
    ml, _ = _fit_line([p["idx"] for p in ls], lp)
    # ENVELOPE lines: keep the fitted slope but anchor the intercept so the upper line sits on/above
    # EVERY high and the lower line on/below every low — a true boundary, not a line drawn through the
    # middle of the candles.
    bh = max(p["price"] - mh * p["idx"] for p in hs)
    bl = min(p["price"] - ml * p["idx"] for p in ls)
    span0 = _at(mh, bh, first) - _at(ml, bl, first)
    span1 = _at(mh, bh, x_now) - _at(ml, bl, x_now)
    if span1 <= 0 or span1 >= span0 * 0.85:            # must be converging
        return None
    flat = 0.0006 * close
    res, sup = _at(mh, bh, x_now), _at(ml, bl, x_now)

    def lines():
        return [_line(df, hs[0]["idx"], _at(mh, bh, hs[0]["idx"]), x_now, res, "Upper line", "trendline"),
                _line(df, ls[0]["idx"], _at(ml, bl, ls[0]["idx"]), x_now, sup, "Lower line", "trendline")]

    if rising and mh > flat and ml > flat:             # rising wedge → bearish
        broke = close < sup
        stop = round(res + 0.4 * atr, 2)
        return _pattern(
            "rising_wedge", "Rising Wedge", "continuation", "bearish",
            "broken_out" if broke else "forming", 0.55 + (0.15 if broke else 0.0),
            points=[_P(df, p, "Higher high") for p in hs] + [_P(df, p, "Higher low") for p in ls],
            lines=lines(), breakout={"level": round(sup, 2), "side": "down"},
            target=_tgt(sup - span0, close, "lower line − wedge mouth"), stop=stop, as_of_idx=x_now,
            education={
                "what": "Both lines slope UP but the lows rise faster than the highs — momentum fading into a narrowing rise. Usually resolves DOWN (bearish).",
                "where": f"Upper line ~${round(res,2)}, lower line ~${round(sup,2)}, converging as price grinds higher.",
                "how_to_spot": "An up-tilted wedge that gets thinner — each rally shrinks even as price ticks up (higher highs AND higher lows).",
                "confirms": f"A close below the rising support (~${round(sup,2)}) triggers the drop.",
                "invalidates": f"A close above the upper line at ${stop} — that's the protective stop.",
            })
    if falling and mh < -flat and ml < -flat:          # falling wedge → bullish
        broke = close > res
        stop = round(sup - 0.4 * atr, 2)
        return _pattern(
            "falling_wedge", "Falling Wedge", "continuation", "bullish",
            "broken_out" if broke else "forming", 0.55 + (0.15 if broke else 0.0),
            points=[_P(df, p, "Lower high") for p in hs] + [_P(df, p, "Lower low") for p in ls],
            lines=lines(), breakout={"level": round(res, 2), "side": "up"},
            target=_tgt(res + span0, close, "upper line + wedge mouth"), stop=stop, as_of_idx=x_now,
            education={
                "what": "Both lines slope DOWN but the highs fall faster than the lows — selling exhausting into a narrowing drop. Usually resolves UP (bullish).",
                "where": f"Upper line ~${round(res,2)}, lower line ~${round(sup,2)}, converging as price grinds lower.",
                "how_to_spot": "A down-tilted wedge that gets thinner — each push down shrinks (lower highs AND lower lows), price staying inside the lines.",
                "confirms": f"A close above the falling resistance (~${round(res,2)}) triggers the rally.",
                "invalidates": f"A close below the lower line at ${stop} — that's the protective stop.",
            })
    return None


def _fibonacci(z, df, close, window: int = 63):
    """Fibonacci grid of the dominant RECENT swing (the extreme low↔high of the last ~3 months,
    ordered by time), NOT some ancient completed leg — anchoring to a low price already ran far past
    is useless (OKTA at $170 anchored to a $73 base). Returns:
      • ``levels``      — retracements (0–100%): pullback / support zones INSIDE the swing.
      • ``extensions``  — 127.2 / 141.4 / 161.8 / 200 / 261.8%: the price-projection TARGETS *beyond*
                          the swing — i.e. where price is headed after a breakout.
    ``position`` says whether price is mid-swing (use retracements) or has broken out past the
    extreme (use the extensions as targets)."""
    n = len(df)
    if n < 20:
        return None

    def swing(w):
        seg = max(0, n - w)
        hi_i = seg + int(np.argmax(df["High"].values[seg:]))
        lo_i = seg + int(np.argmin(df["Low"].values[seg:]))
        return hi_i, float(df["High"].values[hi_i]), lo_i, float(df["Low"].values[lo_i])

    hi_i, hi_p, lo_i, lo_p = swing(window)
    if (hi_p - lo_p) < 0.05 * close:                      # too flat lately → widen to find a real swing
        hi_i, hi_p, lo_i, lo_p = swing(min(n, 130))
    full_rng = hi_p - lo_p
    if full_rng <= 0:
        return None
    # Orient the swing by what price is ACTUALLY doing — not merely which absolute extreme is newer.
    # A pullback-then-recovery (window high OLDER than its low, but price has since bounced well off
    # that low) is an UP move; labelling it a down-swing projects targets DOWN toward $0/negative
    # (the CRDO bug: $308→$177 "down", 262% target −$36). So:
    if hi_i >= lo_i:                                       # the high is the most recent extreme → up-swing
        a_i, a_p, b_i, b_p, up = lo_i, lo_p, hi_i, hi_p, True
    elif close > lo_p + 0.30 * full_rng:                  # low is newer but price recovered → up-swing off it
        seg2 = df["High"].values[lo_i:]
        b_i = lo_i + int(np.argmax(seg2)); b_p = float(df["High"].values[b_i])
        a_i, a_p, up = lo_i, lo_p, True
    else:                                                  # low is newer and price stayed down → down-swing
        a_i, a_p, b_i, b_p, up = hi_i, hi_p, lo_i, lo_p, False
    lo_lvl, hi_lvl = (a_p, b_p) if up else (b_p, a_p)
    rng = hi_lvl - lo_lvl
    if rng <= 0:
        return None

    def at(r):                                             # retracement level (inside the swing)
        return round((hi_lvl - r * rng) if up else (lo_lvl + r * rng), 2)

    def ext(e):                                            # extension level (projected beyond the swing end)
        return round((lo_lvl + e * rng) if up else (hi_lvl - e * rng), 2)

    levels = [{"ratio": r, "price": at(r)} for r in (0.236, 0.382, 0.5, 0.618, 0.786)]
    # never surface a non-positive target (a deep down-swing 262% extension can go negative)
    extensions = [{"ratio": e, "price": ext(e)} for e in (1.272, 1.414, 1.618, 2.0, 2.618) if ext(e) > 0]
    # where is price now: inside the swing (retracement play) or broken out past the extreme (targets)?
    broke_out = (close >= hi_lvl) if up else (close <= lo_lvl)
    nxt = next((x for x in extensions if (x["price"] > close if up else x["price"] < close)), None)
    zone = None
    if not broke_out:
        band = sorted([{"ratio": 0.0, "price": at(0.0)}, *levels, {"ratio": 1.0, "price": at(1.0)}],
                      key=lambda x: x["price"])
        for k in range(len(band) - 1):
            if band[k]["price"] <= close <= band[k + 1]["price"]:
                zone = {"low": band[k], "high": band[k + 1]}; break

    return {
        "direction": "up" if up else "down",
        "swing": {"from": _pt(df, a_i, a_p, "Swing low" if up else "Swing high"),
                  "to": _pt(df, b_i, b_p, "Swing high" if up else "Swing low")},
        "levels": levels,
        "extensions": extensions,
        "position": "broken_out" if broke_out else "in_retracement",
        "in_zone": zone,
        "next_target": nxt,
        "education": {
            "what": (f"Fibonacci on the recent {'up' if up else 'down'} swing ${round(a_p,2)}→${round(b_p,2)}. "
                     f"The 23.6–78.6% levels are RETRACEMENTS (where a pullback tends to find support); the "
                     f"127–262% EXTENSIONS are price TARGETS once the swing extreme is broken."),
            "where": ("Price has broken out past the swing extreme — the retracements are behind it; the live "
                      f"targets are the extensions ({', '.join('$'+str(e['price']) for e in extensions[:3])}…)."
                      if broke_out else
                      "Price is still inside the swing — the 61.8% level (the 'golden pocket') is the classic "
                      "spot a trend pullback holds before resuming."),
            "how_to_spot": "Anchor 0% at the swing end and 100% at the start; extensions project the same ratios BEYOND the end.",
            "confirms": (f"After a breakout, the first extension {'above' if up else 'below'} price "
                         f"(${nxt['price']} = {nxt['ratio']}×) is the nearest measured target."
                         if nxt else "A hold at a retracement level, then continuation, is the tell."),
            "invalidates": f"Losing the 78.6% retracement (${at(0.786)}) warns the whole swing may fully reverse.",
        },
    }


_CONTINUATION = [
    lambda z, df, c, a: _triangle(z, df, c, a),
    lambda z, df, c, a: _flag(z, df, c, a, bull=True),
    lambda z, df, c, a: _flag(z, df, c, a, bull=False),
    lambda z, df, c, a: _cup_handle(z, df, c, a),
    lambda z, df, c, a: _vcp(z, df, c, a),
    lambda z, df, c, a: _wedge(z, df, c, a),
]


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

_RECENCY = 30       # a pattern is "current" only if its right edge is within the last ~6 weeks


def _now_str() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _series(df, n: int = 260) -> dict:
    s = df.tail(n)
    return {
        "timestamps": [_date_str(t) for t in s.index],
        "open": [_r(x) for x in s["Open"].values],
        "high": [_r(x) for x in s["High"].values],
        "low": [_r(x) for x in s["Low"].values],
        "close": [_r(x) for x in s["Close"].values],
        "volume": [_r(x, 0) for x in s["Volume"].values] if "Volume" in s else [],
        "offset": int(len(df) - len(s)),          # subtract from a pattern idx to index into the series
    }


_REVERSAL = [
    lambda z, df, c, a: _double_top(z, df, c, a),
    lambda z, df, c, a: _double_bottom(z, df, c, a),
    lambda z, df, c, a: _head_shoulders(z, df, c, a, inverse=False),
    lambda z, df, c, a: _head_shoulders(z, df, c, a, inverse=True),
    lambda z, df, c, a: _rounding_bottom(z, df, c, a),
]


def compute_chart_patterns(stock, period: str = "1y", interval: str = "1d") -> dict | None:
    """Detect classical chart patterns. Default ~1y daily (the dedicated Patterns page); callers with a
    shorter horizon (e.g. Defend on a 60–90 DTE trade) pass a matched window so patterns are RECENT and
    relevant, not an 8-month-old double top. Returns the drawable OHLC series plus a ranked list of
    patterns (most recent / highest-confidence first). Best-effort; never raises."""
    try:
        df = _history(stock, period, interval)
        if df is None:
            return None
        close = float(df["Close"].values[-1])
        atr = _atr(df) or (close * 0.02)
        z = _zigzag(df, order=5)
        found: list[dict] = []
        for det in _REVERSAL + _CONTINUATION:
            try:
                p = det(z, df, close, atr)
            except Exception:  # noqa: BLE001
                p = None
            if p:
                found.append(p)
        fib = _fibonacci(z, df, close)
        n = len(df)
        # Only surface patterns that are ACTIONABLE NOW: completed near the right edge (not ancient
        # history), with a correctly-sided, sane, not-yet-reached target. This kills the stale
        # "double top from 6 months ago" noise and broken geometry (negative / 50%-away targets).
        cleaned: list[dict] = []
        for p in found:
            if p["as_of_idx"] < n - _RECENCY:
                continue
            tgt = (p.get("target") or {}).get("price")
            bk = (p.get("breakout") or {}).get("level")
            side = (p.get("breakout") or {}).get("side")
            if not tgt or tgt <= 0 or bk is None:
                continue
            if side == "down" and not tgt < bk:
                continue
            if side == "up" and not tgt > bk:
                continue
            if abs(tgt - close) / close > 0.5:                      # >50% away on a 1y daily chart = noise
                continue
            if p["status"] == "broken_out":                          # already played out → no edge left
                if (side == "down" and close <= tgt) or (side == "up" and close >= tgt):
                    continue
            # a still-FORMING pattern must be compact (target within 25%) — no 40%+ year-spanning
            # "measured moves" that price is nowhere near yet.
            if p["status"] != "broken_out" and abs(tgt - close) / close > 0.25:
                continue
            cleaned.append(p)
        # de-dupe by type, then merge near-identical structures (same direction + breakout within
        # 3%, e.g. a Double Bottom and Inverse H&S on the same lows) keeping the best confidence.
        best: dict[str, dict] = {}
        for p in cleaned:
            if p["type"] not in best or p["confidence"] > best[p["type"]]["confidence"]:
                best[p["type"]] = p
        ranked = sorted(best.values(), key=lambda p: (-(p["status"] == "broken_out"), -p["confidence"]))
        patterns: list[dict] = []
        for p in ranked:
            if any(p["direction"] == q["direction"] and q["breakout"].get("level")
                   and abs(p["breakout"]["level"] - q["breakout"]["level"]) / close < 0.03 for q in patterns):
                continue
            patterns.append(p)
        # A forming BULLISH reversal AND a forming BEARISH reversal at once (often sharing pivots,
        # e.g. HD's Double Top whose Peak-1 is the Double Bottom's neckline) = a choppy RANGE, not a
        # setup. Keep the decisively stronger side; if it's a coin-flip (<0.08 apart), show neither.
        fr = [p for p in patterns if p["category"] == "reversal" and p["status"] != "broken_out"]
        bull = max((p for p in fr if p["direction"] == "bullish"), key=lambda p: p["confidence"], default=None)
        bear = max((p for p in fr if p["direction"] == "bearish"), key=lambda p: p["confidence"], default=None)
        if bull and bear:
            if abs(bull["confidence"] - bear["confidence"]) < 0.08:
                drop = {id(p) for p in fr if p["direction"] in ("bullish", "bearish")}
            else:
                weaker = bull if bull["confidence"] <= bear["confidence"] else bear
                drop = {id(p) for p in fr if p["direction"] == weaker["direction"]}
            patterns = [p for p in patterns if id(p) not in drop]
        patterns = patterns[:3]
        return {
            "price": round(close, 2),
            "as_of": _now_str(),
            "atr": round(atr, 2),
            "series": _series(df),
            "patterns": patterns,
            "fibonacci": fib,
            "meta": {"n_pivots": len(z), "n_bars": int(len(df))},
        }
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# annotated PNG export (mplfinance) — the downloadable/shareable chart
# ---------------------------------------------------------------------------

def render_pattern_png(series: dict, pattern: dict, ticker: str = "") -> bytes | None:
    """Render one detected pattern as an annotated candlestick PNG: the shape's trend/necklines,
    the breakout trigger, the measured-move target and the labelled pivots. Best-effort."""
    try:
        import io
        import matplotlib
        matplotlib.use("Agg")
        import mplfinance as mpf
        import numpy as _np

        ts = pd.to_datetime(series["timestamps"])
        data = {"Open": series["open"], "High": series["high"], "Low": series["low"], "Close": series["close"]}
        df = pd.DataFrame(data, index=ts).astype(float)
        has_vol = bool(series.get("volume"))
        if has_vol:
            df["Volume"] = pd.Series(series["volume"], index=ts).astype(float)
        off = int(series.get("offset", 0))
        last = len(df) - 1

        def pos(idx):
            return max(0, min(int(idx) - off, last))

        alines = [[(df.index[pos(ln["from"]["idx"])], ln["from"]["price"]),
                   (df.index[pos(ln["to"]["idx"])], ln["to"]["price"])] for ln in pattern.get("lines", [])]
        hlines, hcolors = [], []
        bk = (pattern.get("breakout") or {}).get("level")
        if bk:
            hlines.append(bk); hcolors.append("#f59e0b")
        tg = (pattern.get("target") or {}).get("price")
        if tg:
            hlines.append(tg); hcolors.append("#16a34a" if pattern.get("direction") == "bullish" else "#dc2626")

        marker = _np.full(len(df), _np.nan)
        for p in pattern.get("points", []):
            marker[pos(p["idx"])] = p["price"]
        addplots = []
        if _np.isfinite(marker).any():
            addplots.append(mpf.make_addplot(marker, type="scatter", markersize=90, marker="o", color="#8b5cf6"))

        kwargs = dict(
            type="candle", style="yahoo", volume=has_vol, figscale=1.15, figratio=(16, 9),
            title=f"\n{ticker} — {pattern.get('name','')}  ({pattern.get('direction','')}, {pattern.get('status','')})",
            addplot=addplots or None, returnfig=True,
        )
        if hlines:
            kwargs["hlines"] = dict(hlines=hlines, colors=hcolors, linestyle="--", linewidths=1.1)
        if alines:
            kwargs["alines"] = dict(alines=alines, colors=["#8b5cf6"] * len(alines), linewidths=1.4)
        fig, _axes = mpf.plot(df, **{k: v for k, v in kwargs.items() if v is not None})
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=115, bbox_inches="tight")
        import matplotlib.pyplot as plt
        plt.close(fig)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
