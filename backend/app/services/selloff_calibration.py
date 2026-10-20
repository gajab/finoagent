"""Walk-forward calibration of the Selloff Monitor's analog engine — "has this ever beaten the base rate?"

The research behind this (Welch & Goyal 2008: most return predictors fail out of sample; Campbell &
Thompson 2008: shrinkage helps; Gneiting & Raftery 2007 / Murphy: proper scores; Bradley et al. 2008:
Brier-skill sampling error) says a conditional forecast must be shown NEXT TO its out-of-sample record.
This module replays the engine with NO look-ahead:

  • origins: every 5th session since 2000 ("all") + every session whose 3-session move ≤ −1.5σ ("stress");
  • at each origin t the features are scaled by the EXPANDING std (data ≤ t), candidates are only days
    whose forward window had closed by t (i + H ≤ t), the base rate is the share of up h-session windows
    known by t; the same greedy non-overlapping k-NN as the live engine;
  • P(up) forecasts are shrunk toward that as-of base rate, p̃ = (k_up + m·p₀)/(n + m), with the prior
    strength m tuned on 2000–2012 origins and the skill reported on 2013-onward origins only;
  • Brier skill vs the base rate with a moving-block bootstrap CI (blocks of consecutive origins ≥ H);
  • coverage of the analog P10–P90 fan and of a VIX-implied lognormal cone (the options cone's
    historical proxy) — nominal 80% — so the page can say how each fan has actually behaved;
  • a fan WIDENING factor per horizon (split-conformal style): the 80th percentile of each origin's
    normalized miss |actual − P50| / (P90−P50 or P50−P10), tuned on 2000–2012 and checked on 2013+.

Pure numpy over the index-feature frame; ~2–5 s, memoised per index date (one run per session).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

M_GRID = (0, 10, 20, 40, 80, 160, 320, 640, math.inf)   # ∞ = pure base rate (analogs add nothing)
DEFAULT_M = 40                 # spec default (λ = 0.5 at n = 40) when tuning is impossible
STRESS_SIGMA = -1.5
TUNE_END = "2012-12-31"
EVAL_START = "2000-01-01"
STEP = 5
HS = (1, 5, 20)
_Z10 = 1.2815515655446004
_MEMO: dict[tuple, dict] = {}


def greedy_non_overlap(order: np.ndarray, k: int, horizon: int, n: int) -> list[int]:
    """Walk candidates nearest-first, keeping one only if no kept analog lies within ``horizon`` sessions."""
    blocked = np.zeros(n + horizon + 2, dtype=bool)
    picked: list[int] = []
    for i in order:
        if blocked[i]:
            continue
        picked.append(int(i))
        blocked[max(0, i - horizon): i + horizon + 1] = True
        if len(picked) >= k:
            break
    return picked


def brier(p: np.ndarray, o: np.ndarray) -> float:
    return float(np.mean((p - o) ** 2))


def block_bootstrap_bss(p: np.ndarray, p0: np.ndarray, o: np.ndarray, block: int, b: int = 1000,
                        seed: int = 11) -> tuple[float | None, float | None]:
    """95% CI of BSS = 1 − BS(p)/BS(p0) by a moving-block bootstrap over consecutive origins."""
    n = len(o)
    if n < 2 * block:
        return None, None
    rng = np.random.default_rng(seed)
    starts_max = n - block
    nb = int(math.ceil(n / block))
    out = np.empty(b)
    for j in range(b):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.integers(0, starts_max + 1, nb)])[:n]
        bs_c = brier(p0[idx], o[idx])
        out[j] = 1 - brier(p[idx], o[idx]) / bs_c if bs_c > 0 else np.nan
    out = out[np.isfinite(out)]
    if len(out) < b * 0.5:
        return None, None
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def walk_forward(feat: pd.DataFrame, features: tuple, weights: np.ndarray, k: int = 40, horizon: int = 20) -> dict:
    """Replay the analog engine through history. ``feat`` = selloff_monitor_service.index_features()."""
    cols = list(features)
    n = len(feat)
    if n < 2500:
        return {"available": False, "reason": "Not enough history for a walk-forward check."}
    X = feat[cols].to_numpy(dtype=float)
    valid = np.isfinite(X).all(axis=1)
    closes = feat["close"].to_numpy(dtype=float)
    vix = feat["vix"].to_numpy(dtype=float)
    m3 = feat["move3_sigma"].to_numpy(dtype=float)
    exp_std = feat[cols].where(np.isfinite(feat[cols])).expanding(min_periods=250).std().to_numpy()
    dates = feat.index

    # as-of base rates: share of up h-windows among windows that had CLOSED by t
    up_cum = {}
    for h in HS:
        up = np.zeros(n)
        up[: n - h] = (closes[h:] > closes[: n - h]).astype(float)
        up_cum[h] = np.concatenate([[0.0], np.cumsum(up)])        # up_cum[h][j] = Σ up[0..j-1]

    start = int(np.searchsorted(dates, pd.Timestamp(EVAL_START)))
    last_origin = n - 1 - horizon
    regular = set(range(start, last_origin + 1, STEP))
    stress = {t for t in range(start, last_origin + 1) if np.isfinite(m3[t]) and m3[t] <= STRESS_SIGMA}
    origins = sorted(t for t in (regular | stress) if valid[t])

    rec: list[dict[str, Any]] = []
    kth_regular: list[float] = []
    for t in origins:
        cand = np.where(valid[: t - horizon + 1])[0]
        if len(cand) < 500:
            continue
        scale = exp_std[t].copy()
        if not np.isfinite(scale).all():
            continue
        scale[scale == 0] = 1.0
        d = np.sqrt((((X[cand] - X[t]) / scale) ** 2 * weights).sum(axis=1))
        order_idx = np.argsort(d)
        picked = greedy_non_overlap(cand[order_idx], k, horizon, n)
        if len(picked) < 10:
            continue
        dist_full = np.full(n, np.nan)
        dist_full[cand] = d
        kth = float(np.nanmax(dist_full[picked]))
        r = {"t": t, "regular": t in regular, "stress": t in stress, "kth": kth}
        pk = np.array(picked)
        for h in HS:
            fwd = closes[pk + h] / closes[pk] - 1
            k_up = int((fwd > 0).sum())
            n_known = t - h + 1                                     # windows j ≤ t−h have closed
            p0 = up_cum[h][n_known] / n_known
            actual = closes[t + h] / closes[t] - 1
            sig = vix[t] / 100 * math.sqrt(h / 252) if np.isfinite(vix[t]) and vix[t] > 0 else np.nan
            lo_c = math.exp(-sig * sig / 2 - _Z10 * sig) - 1 if np.isfinite(sig) else np.nan
            hi_c = math.exp(-sig * sig / 2 + _Z10 * sig) - 1 if np.isfinite(sig) else np.nan
            q10, q50, q90 = np.percentile(fwd, [10, 50, 90])
            half = (q90 - q50) if actual >= q50 else (q50 - q10)
            r[h] = {"k_up": k_up, "n": len(picked), "p0": p0, "o": float(actual > 0),
                    "excess": float(abs(actual - q50) / half) if half > 0 else None,
                    "in_fan": bool(np.percentile(fwd, 10) <= actual <= np.percentile(fwd, 90)),
                    "in_cone": bool(lo_c <= actual <= hi_c) if np.isfinite(sig) else None,
                    "in_cone_1sd": bool(abs(math.log(1 + actual)) <= sig) if np.isfinite(sig) else None}
        rec.append(r)
        if r["regular"]:
            kth_regular.append(kth)
    if len(rec) < 100:
        return {"available": False, "reason": "Too few walk-forward origins."}

    tune_cut = pd.Timestamp(TUNE_END)
    tune = [r for r in rec if dates[r["t"]] <= tune_cut]
    test = [r for r in rec if dates[r["t"]] > tune_cut]

    def _p(rows, h, m):
        if math.isinf(m):
            return np.array([x[h]["p0"] for x in rows])
        return np.array([(x[h]["k_up"] + m * x[h]["p0"]) / (x[h]["n"] + m) for x in rows])

    def _o(rows, h):
        return np.array([x[h]["o"] for x in rows])

    def _p0(rows, h):
        return np.array([x[h]["p0"] for x in rows])

    # tune ONE prior strength on 2000–2012 (both horizons) — never on the evaluation years
    m_best, m_scores = DEFAULT_M, {}
    if len(tune) >= 100:
        for m in M_GRID:
            m_scores[m] = sum(brier(_p(tune, h, m), _o(tune, h)) for h in HS)
        m_best = min(m_scores, key=m_scores.get)

    def _score(rows, h, block):
        if len(rows) < 2 * block:
            return {"n": len(rows), "bss_raw": None, "lo": None, "hi": None, "bss_shrunk": None}
        o, p0 = _o(rows, h), _p0(rows, h)
        p = _p(rows, h, m_best)
        p_raw = _p(rows, h, 0)
        bs_c = brier(p0, o)
        lo, hi = block_bootstrap_bss(p_raw, p0, o, block)          # does the ANALOG engine itself have skill?
        return {"n": len(rows), "bss_raw": round(1 - brier(p_raw, o) / bs_c, 4) if bs_c > 0 else None,
                "lo": None if lo is None else round(lo, 4), "hi": None if hi is None else round(hi, 4),
                "bss_shrunk": round(1 - brier(p, o) / bs_c, 4) if bs_c > 0 else None,
                "hit_rate_up": round(float(o.mean()) * 100, 1)}

    def _badge(s):
        if s.get("lo") is None:
            return "insufficient"
        if s["lo"] > 0:
            return "skill"
        if s["hi"] < 0:
            return "worse"
        return "no_skill"

    skill, coverage = {}, {}
    test_all = [r for r in test if r["regular"]]
    test_stress = [r for r in test if r["stress"]]
    for h in HS:
        block_all = max(4, int(math.ceil(horizon / STEP)) * 2)       # consecutive regular origins are 5 sessions apart
        block_stress = max(8, horizon)                              # stress origins can be consecutive sessions
        s_all, s_stress = _score(test_all, h, block_all), _score(test_stress, h, block_stress)
        skill[f"h{h}"] = {"all": s_all, "stress": s_stress, "badge": _badge(s_stress)}
        cov = {}
        for name, rows in (("all", [r for r in rec if r["regular"]]), ("stress", [r for r in rec if r["stress"]])):
            fan = [x[h]["in_fan"] for x in rows]
            cone = [x[h]["in_cone"] for x in rows if x[h]["in_cone"] is not None]
            sd1 = [x[h]["in_cone_1sd"] for x in rows if x[h]["in_cone_1sd"] is not None]
            cov[name] = {"n": len(rows),
                         "analog_p10_p90": round(float(np.mean(fan)) * 100, 1) if fan else None,
                         "vix_cone_p10_p90": round(float(np.mean(cone)) * 100, 1) if cone else None,
                         "vix_cone_1sd": round(float(np.mean(sd1)) * 100, 1) if sd1 else None}
        coverage[f"h{h}"] = cov

    # fan widening: the factor that would have held 80% of outcomes on 2000–2012 origins, checked on 2013+
    fan_factor = {}
    for h in HS:
        f_h = {}
        for name, pick in (("all", lambda x: x["regular"]), ("stress", lambda x: x["stress"])):
            e_tune = np.array([x[h]["excess"] for x in tune if pick(x) and x[h]["excess"] is not None])
            e_test = np.array([x[h]["excess"] for x in test if pick(x) and x[h]["excess"] is not None])
            if len(e_tune) < 50:
                f_h[name] = None
                continue
            f = float(np.percentile(e_tune, 80))
            f_h[name] = {"factor": round(max(f, 0.5), 3), "n_tune": int(len(e_tune)),
                         "test_coverage_raw": round(float(np.mean(e_test <= 1.0)) * 100, 1) if len(e_test) else None,
                         "test_coverage_calibrated": round(float(np.mean(e_test <= f)) * 100, 1) if len(e_test) else None,
                         "n_test": int(len(e_test))}
        fan_factor[f"h{h}"] = f_h

    kq = np.array(sorted(kth_regular))
    return {
        "available": True, "k": k, "horizon": horizon, "m": None if math.isinf(m_best) else m_best,
        "m_is_inf": math.isinf(m_best), "m_scores": {("inf" if math.isinf(a) else str(a)): round(b, 5) for a, b in m_scores.items()},
        "tuned_on": f"{EVAL_START[:4]}–{TUNE_END[:4]}", "evaluated_on": f"{int(TUNE_END[:4]) + 1}–{dates[n - 1].year}",
        "n_origins": len(rec), "n_test_all": len(test_all), "n_test_stress": len(test_stress),
        "skill": skill, "coverage": coverage, "fan_factor": fan_factor,
        "kth_quantiles": [round(float(np.percentile(kq, q)), 3) for q in (50, 75, 90, 95)] if len(kq) else None,
        "kth_sorted": kq,                                           # private: quality percentile lookup (stripped)
        "method": ("Replayed from 2000 with no look-ahead (expanding feature scaling, only analogs whose outcome "
                   "was known, as-of base rate). Shrinkage tuned on 2000–2012; skill measured on 2013 onward. "
                   "Brier skill vs the base rate with a moving-block bootstrap 95% CI."),
    }


def quality_percentile(cal: dict, kth_today: float | None) -> float | None:
    ks = cal.get("kth_sorted")
    if ks is None or kth_today is None or not len(ks):
        return None
    return round(float(np.searchsorted(ks, kth_today) / len(ks) * 100), 1)


def memo_walk_forward(feat: pd.DataFrame, features: tuple, weights: np.ndarray, k: int, horizon: int) -> dict:
    key = (feat.index[-1], len(feat), k, horizon, tuple(np.round(weights, 3)))
    if key not in _MEMO:
        _MEMO.clear()                                               # one entry: the latest session
        _MEMO[key] = walk_forward(feat, features, weights, k, horizon)
    return _MEMO[key]


def public(cal: dict) -> dict:
    return {k: v for k, v in cal.items() if k != "kth_sorted"}


def vif_overlap(h: int) -> float:
    """Variance inflation of a base-rate share computed over OVERLAPPING h-session windows (Gaussian
    orthant approximation: corr of sign indicators = (2/π)·asin(ρ), ρ = (h−j)/h at lag j)."""
    return 1 + 2 * sum((2 / math.pi) * math.asin((h - j) / h) for j in range(1, h))


def holm(pvals: list[float]) -> list[float]:
    """Holm step-down adjusted p-values (same order as the input)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj


def episodes(picked_sorted: list[int], gap: int = 60) -> list[list[int]]:
    """Group analog indices into episodes: a new episode starts after a gap of ≥ ``gap`` sessions."""
    out: list[list[int]] = []
    for i in picked_sorted:
        if out and i - out[-1][-1] < gap:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def episode_bootstrap(groups: list[np.ndarray], b: int = 2000, seed: int = 5) -> tuple[float | None, float | None, float | None]:
    """95% CI and SE of a pooled share when whole episodes are resampled (clustering-robust)."""
    if len(groups) < 3:
        return None, None, None
    rng = np.random.default_rng(seed)
    sums = np.array([g.sum() for g in groups], dtype=float)
    cnts = np.array([len(g) for g in groups], dtype=float)
    idx = rng.integers(0, len(groups), (b, len(groups)))
    shares = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return float(np.percentile(shares, 2.5)), float(np.percentile(shares, 97.5)), float(shares.std(ddof=1))
