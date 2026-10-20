"""Selloff Monitor Phase 3 — "your book under this tape".

Runs the user's OPEN option trades through the monitor's OWN scenarios, so the page answers
"what does this tape mean for MY trades", not just "what might the S&P do":

  • History — every one of the k historical analogs: the S&P move that actually followed (1/5/20
    sessions) AND the VIX ratio that actually followed. Each name moves β × S&P; every leg is fully
    repriced at the horizon (decay banked, legs expiring inside the horizon settle at intrinsic) with
    implied vols scaled by that analog's VIX ratio → a book P&L DISTRIBUTION (a short-gamma book's
    P&L is not a monotone function of the index, so percentiles of the index are not percentiles of P&L).
  • Options — the options-implied P10/P50/P90 index moves, with a spot-vol rule FITTED on the same
    analogs (log VIX ratio = a + b·log(1+move)), so options and history use one vol model.
  • Tell levels — break of the last session's low, reclaim of the pre-selloff close, a repeat of the move.
  • Exposure — β-weighted delta in SPY shares, θ/day, vega, gamma; per-trade P&L in the bad / good case,
    which short strikes get breached, and how far each nearest short strike is in σ of a 5-session move.

Reuses Manage Book's machinery so the numbers reconcile: `_position_greeks` (live quotes, IV sanity,
covered-share netting), `_compute_betas` (winsorized 1y β vs the S&P), and `reprice_scenario` (the
same full-reprice routine, with its horizon/iv_scale options). Deterministic; no LLM.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import math
from typing import Any

import numpy as np

from .book_tail_risk import _MULT, reprice_scenario
from .selloff_monitor_service import _r

logger = logging.getLogger(__name__)

HORIZONS = (1, 5, 20)
FOCUS_H = 5
CURVE_RANGE_PCT = (-10.0, 10.0, 0.5)
VOL_SCALE_BOUNDS = (0.4, 3.0)


# ---------------------------------------------------------------------------
# vol rule + repricing
# ---------------------------------------------------------------------------

def fit_vol_rule(outcomes: list[dict]) -> dict | None:
    """OLS of log(VIX ratio) on log(1 + S&P move) across the analog outcomes at one horizon.
    The intercept carries the state's vol drift (e.g. vol mean-reverting after a spike); the slope is the
    spot-vol sensitivity in THIS kind of tape."""
    pts = [(math.log(1 + o["ret_pct"] / 100), math.log(o["vix_ratio"])) for o in outcomes
           if o.get("vix_ratio") and o["vix_ratio"] > 0 and o.get("ret_pct") is not None and o["ret_pct"] > -99]
    if len(pts) < 8:
        return None
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    if float(np.var(x)) <= 0:
        return None
    b = float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1))
    a = float(y.mean() - b * x.mean())
    resid = y - (a + b * x)
    r2 = 1 - float(np.sum(resid ** 2) / np.sum((y - y.mean()) ** 2)) if np.sum((y - y.mean()) ** 2) > 0 else None
    return {"a": round(a, 4), "b": round(b, 3), "n": len(pts), "r2": _r(r2, 2)}


def vol_scale(rule: dict | None, mkt_pct: float) -> float:
    if not rule:
        return 1.0
    v = math.exp(rule["a"] + rule["b"] * math.log(1 + mkt_pct / 100))
    return min(VOL_SCALE_BOUNDS[1], max(VOL_SCALE_BOUNDS[0], v))


def _pnl(positions: list[dict], mkt_pct: float, days: float, iv_scale: float, r: float) -> float:
    return reprice_scenario(positions, mkt_pct / 100.0, 0.0, r, days=days, iv_scale=iv_scale)


def _pct(a: np.ndarray, q: float) -> float:
    return float(np.percentile(a, q))


def _dist(pnl: np.ndarray, dates: list[str]) -> dict:
    i_worst, i_best = int(np.argmin(pnl)), int(np.argmax(pnl))
    return {"n": int(len(pnl)), "p10": _r(_pct(pnl, 10), 0), "p50": _r(_pct(pnl, 50), 0), "p90": _r(_pct(pnl, 90), 0),
            "mean": _r(float(pnl.mean()), 0), "p_loss": _r(float((pnl < 0).mean() * 100), 1),
            "worst": _r(float(pnl[i_worst]), 0), "worst_date": dates[i_worst],
            "best": _r(float(pnl[i_best]), 0), "best_date": dates[i_best]}


# ---------------------------------------------------------------------------
# per-trade view
# ---------------------------------------------------------------------------

def _short_legs(p: dict) -> list[dict]:
    return [lg for lg in p.get("legs", []) if lg["sign"] < 0]


def nearest_short(p: dict, days: float) -> dict | None:
    """Nearest short strike and its distance in σ of a ``days``-calendar-day move at that leg's own IV."""
    best = None
    for lg in _short_legs(p):
        sd = p["spot"] * max(lg["iv"], 0.01) * math.sqrt(max(days, 1) / 365.0)
        z = abs(lg["strike"] - p["spot"]) / sd if sd > 0 else None
        itm = (lg["right"] == "P" and p["spot"] < lg["strike"]) or (lg["right"] == "C" and p["spot"] > lg["strike"])
        cand = {"strike": _r(lg["strike"], 2), "right": lg["right"], "exp": lg.get("exp"),
                "distance_pct": _r((lg["strike"] / p["spot"] - 1) * 100, 2), "z": _r(-z if itm else z, 2), "itm": itm}
        if best is None or (cand["z"] is not None and (best["z"] is None or cand["z"] < best["z"])):
            best = cand
    return best


def breached(p: dict, mkt_pct: float) -> list[str]:
    """Short strikes that end in the money if the S&P moves ``mkt_pct`` (name moves β × that)."""
    s1 = p["spot"] * (1 + p.get("beta", 1.0) * mkt_pct / 100)
    out = []
    for lg in _short_legs(p):
        if (lg["right"] == "P" and s1 < lg["strike"]) or (lg["right"] == "C" and s1 > lg["strike"]):
            out.append(f"{lg['strike']:g}{lg['right']}")
    return out


# ---------------------------------------------------------------------------
# assembly (pure)
# ---------------------------------------------------------------------------

def book_scenarios(positions: list[dict], r: float, monitor: dict) -> dict:
    """Every scenario of the monitor applied to the book. ``positions`` = book_tail_risk position dicts
    with ``beta`` (and ``purpose``) stamped; ``monitor`` = the Selloff Monitor payload (v2)."""
    an = monitor.get("analogs") or {}
    if not an.get("available") or not an.get("outcomes"):
        return {"available": False, "reason": "Historical analogs unavailable — nothing to replay the book on."}
    chart, cone, state, levels = monitor["chart"], monitor.get("options_cone") or {}, monitor["state"], monitor["levels"]
    anchor = _dt.date.fromisoformat(chart["anchor"]["date"])
    fwd = [_dt.date.fromisoformat(d) for d in chart["forward_dates"]]
    days_of = {h: (fwd[h - 1] - anchor).days for h in HORIZONS if h <= len(fwd)}
    has_cov = any(p.get("cov_shares") for p in positions)
    with_down = [{**p, "down_stock": True} for p in positions] if has_cov else None

    horizons, rules = [], {}
    for h, days in days_of.items():
        outs = [o for o in an["outcomes"].get(f"h{h}", []) if o.get("vix_ratio") and o.get("ret_pct") is not None]
        if not outs:
            continue
        rule = fit_vol_rule(outs)
        rules[h] = rule
        dates = [o["date"] for o in outs]
        pnl = np.array([_pnl(positions, o["ret_pct"], days, o["vix_ratio"], r) for o in outs])
        hist = _dist(pnl, dates)
        hist["points"] = [{"date": o["date"], "mkt_pct": o["ret_pct"], "vix_ratio": o["vix_ratio"], "pnl": _r(v, 0)}
                          for o, v in zip(outs, pnl)]
        opt = []
        if cone.get("available") and cone.get("fan_pct") and len(cone["fan_pct"]) >= h:
            fp = cone["fan_pct"][h - 1]
            for q in ("p10", "p50", "p90"):
                m = fp[q]
                opt.append({"q": q, "mkt_pct": _r(m, 2), "iv_scale": _r(vol_scale(rule, m), 3),
                            "pnl": _r(_pnl(positions, m, days, vol_scale(rule, m), r), 0)})
        row = {"h": h, "days": days, "history": hist, "options": opt, "vol_rule": rule}
        if with_down is not None:
            row["history_with_stock"] = _dist(np.array([_pnl(with_down, o["ret_pct"], days, o["vix_ratio"], r)
                                                        for o in outs]), dates)
        horizons.append(row)
    if not horizons:
        return {"available": False, "reason": "No analog outcomes with VIX data to replay the book on."}
    by_h = {row["h"]: row for row in horizons}
    focus = by_h.get(FOCUS_H) or horizons[0]
    fh, fdays, frule = focus["h"], focus["days"], focus["vol_rule"]

    # tell levels as scenarios (SPY levels → % from the last close)
    close = levels["close"]
    lvl = []
    ret3 = state.get("ret3_pct")
    for lid, label, price, h in (
        ("break_low", "Breaks the last session's low (next session)", levels.get("session_low"), 1),
        ("reclaim", "Reclaims the pre-selloff close (5 sessions)", levels.get("pre_move_close"), FOCUS_H),
    ):
        if price and h in days_of:
            m = (price / close - 1) * 100
            lvl.append({"id": lid, "label": label, "h": h, "mkt_pct": _r(m, 2),
                        "pnl": _r(_pnl(positions, m, days_of[h], vol_scale(rules.get(h), m), r), 0)})
    if ret3 is not None and FOCUS_H in days_of:
        m = -abs(ret3)
        lvl.append({"id": "repeat", "label": f"The selloff repeats ({m:+.2f}% over 5 sessions)", "h": FOCUS_H,
                    "mkt_pct": _r(m, 2), "pnl": _r(_pnl(positions, m, days_of[FOCUS_H], vol_scale(rules.get(FOCUS_H), m), r), 0)})

    lo, hi, step = CURVE_RANGE_PCT
    grid = np.arange(lo, hi + step / 2, step)
    curve = {"h": fh, "days": fdays, "points": [{"mkt_pct": _r(m, 2), "pnl": _r(_pnl(positions, float(m), fdays, vol_scale(frule, float(m)), r), 0)}
                                                 for m in grid]}

    # per-trade: the market's bad / good case at the focus horizon (history P10 / P90 of the S&P move)
    fan = {f["h"]: f for f in an.get("fan_pct") or []}
    bad_m = fan.get(fh, {}).get("p10")
    good_m = fan.get(fh, {}).get("p90")
    trades = []
    for p in positions:
        row = {"trade_id": p.get("trade_id"), "name": p.get("name"), "ticker": p["ticker"], "purpose": p.get("purpose", "untagged"),
               "beta": _r(p.get("beta", 1.0), 2), "spot": _r(p["spot"], 2), "capital": _r(p.get("capital"), 0),
               "nearest_short": nearest_short(p, fdays)}
        if bad_m is not None:
            row["pnl_bad"] = _r(_pnl([p], bad_m, fdays, vol_scale(frule, bad_m), r), 0)
            row["breached_bad"] = breached(p, bad_m)
        if good_m is not None:
            row["pnl_good"] = _r(_pnl([p], good_m, fdays, vol_scale(frule, good_m), r), 0)
            row["breached_good"] = breached(p, good_m)
        trades.append(row)
    trades.sort(key=lambda t: (t.get("pnl_bad") is None, t.get("pnl_bad") or 0))
    by_purpose: dict[str, float] = {}
    for t in trades:
        if t.get("pnl_bad") is not None:
            by_purpose[t["purpose"]] = by_purpose.get(t["purpose"], 0.0) + t["pnl_bad"]

    # exposures (same definitions as Manage Book)
    spy = state.get("spy_close") or 0
    bw = sum(p.get("net_delta", 0.0) * p.get("beta", 1.0) * p["spot"] for p in positions)
    gamma_1pct = sum(0.5 * p.get("net_gamma", 0.0) * (p.get("beta", 1.0) * p["spot"] * 0.01) ** 2 for p in positions)
    capital = sum(p.get("capital") or 0 for p in positions)
    exposures = {
        "beta_delta_spy": _r(bw / spy, 0) if spy else None,
        "dollar_delta_per_1pct": _r(bw * 0.01, 0),
        "gamma_per_1pct_sq": _r(gamma_1pct, 0),
        "theta_per_day": _r(sum(p.get("net_theta", 0.0) for p in positions), 0),
        "vega_per_pt": _r(sum(p.get("net_vega", 0.0) for p in positions), 0),
        "capital": _r(capital, 0),
        "short_legs": sum(len(_short_legs(p)) for p in positions),
    }

    return {
        "available": True, "focus_h": fh, "horizons": horizons, "levels": lvl, "curve": curve,
        "bad_case_mkt_pct": _r(bad_m, 2), "good_case_mkt_pct": _r(good_m, 2),
        "trades": trades, "by_purpose_bad": {k: _r(v, 0) for k, v in by_purpose.items()},
        "exposures": exposures, "has_covered_stock": has_cov,
        "assumptions": [
            "Each name moves β × the S&P (β = 1-year daily, winsorized −0.5…3 — the same as Manage Book); no stock-specific noise.",
            "History: implied vols scale with the VIX ratio that actually followed each analog. Options and level scenarios use a spot-vol rule fitted on those same analogs.",
            "Every leg is fully repriced at the horizon (decay banked); legs that expire inside it settle at intrinsic.",
            "Option overlay only: shares behind covered calls count their gains, not their losses, unless you include stock losses; shares held outside your trades are not included.",
            "Live quotes with implied vols checked against the quote's own price (Manage Book's IV sanity).",
        ],
    }


def book_read(res: dict) -> list[str]:
    if not res.get("available"):
        return []
    out = []
    focus = next((h for h in res["horizons"] if h["h"] == res["focus_h"]), res["horizons"][0])
    hd = focus["history"]
    out.append(f"Your book across the {hd['n']} historical outcomes, {focus['h']} sessions out: down in "
               f"{hd['p_loss']:.0f}% of them; median {_usd(hd['p50'])}, bad case (P10) {_usd(hd['p10'])}, "
               f"worst {_usd(hd['worst'])} (after {hd['worst_date']}).")
    opt = {o["q"]: o for o in focus.get("options") or []}
    if "p10" in opt and "p90" in opt:
        out.append(f"At the options-implied range ({opt['p10']['mkt_pct']:+.1f}% to {opt['p90']['mkt_pct']:+.1f}% for the S&P) "
                   f"the book makes {_usd(opt['p10']['pnl'])} to {_usd(opt['p90']['pnl'])}.")
    ex = res["exposures"]
    if ex.get("beta_delta_spy") is not None:
        out.append(f"Exposure: β-weighted {ex['beta_delta_spy']:+,.0f} SPY shares ({_usd(ex['dollar_delta_per_1pct'])} per 1% S&P move "
                   f"before convexity), θ {_usd(ex['theta_per_day'])}/day, vega {_usd(ex['vega_per_pt'])} per vol point.")
    tested = [t for t in res["trades"] if t.get("breached_bad")]
    if tested:
        out.append(f"In the bad case (S&P {res['bad_case_mkt_pct']:+.1f}% over {res['focus_h']} sessions), "
                   f"{len(tested)} trade{'s' if len(tested) != 1 else ''} would have short strikes in the money: "
                   + ", ".join(f"{t['ticker']} {'/'.join(t['breached_bad'])}" for t in tested[:6])
                   + ("…" if len(tested) > 6 else "") + ".")
    elif res.get("bad_case_mkt_pct") is not None:
        out.append(f"In the bad case (S&P {res['bad_case_mkt_pct']:+.1f}% over {res['focus_h']} sessions) no short strike ends in the money.")
    return out


def _usd(v) -> str:
    if v is None:
        return "—"
    return f"{'−' if v < 0 else '+'}${abs(v):,.0f}"


# ---------------------------------------------------------------------------
# orchestration (network: live quotes + betas)
# ---------------------------------------------------------------------------

async def compute_book_under_tape(strategies: list, purpose_of: dict, quote_source: str, user, db,
                                  monitor: dict) -> dict:
    from .book_tail_risk import _compute_betas, _position_greeks, _skip_reason
    from .derivative_income_service import _get_sofr
    from .quote_providers import get_provider

    provider = get_provider(quote_source, user=user, db=db)
    try:
        r = (await _get_sofr())[0]
    except Exception:  # noqa: BLE001
        r = 0.045
    today = _dt.date.today()
    spot_cache, chain_cache, positions, skipped = {}, {}, [], []
    for s in strategies:
        reason = None
        try:
            p = await _position_greeks(s, provider, r, today, spot_cache, chain_cache)
        except Exception as exc:  # noqa: BLE001
            logger.debug("selloff book: greeks failed for %s: %s", getattr(s, "id", "?"), exc)
            p, reason = None, f"error: {str(exc)[:80]}"
        if p:
            positions.append(p)
        else:
            skipped.append({"trade_id": getattr(s, "id", None), "ticker": getattr(s, "ticker", None),
                            "reason": reason or _skip_reason(s, spot_cache)})
    coverage = {"requested": len(strategies), "analysed": len(positions), "skipped": skipped}
    if not positions:
        return {"available": False, "reason": "No open option trades with live quotes to run through the scenarios.",
                "coverage": coverage}
    betas = await asyncio.to_thread(_compute_betas, [p["ticker"] for p in positions])
    for p in positions:
        p["beta"] = betas.get(p["ticker"], 1.0)
        p["purpose"] = purpose_of.get(p.get("trade_id"), "untagged")
    res = await asyncio.to_thread(book_scenarios, positions, r, monitor)
    res["coverage"] = coverage
    res["quote_source"] = quote_source
    res["monitor_session"] = monitor.get("last_session")
    res["as_of"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    res["indicative"] = any(p.get("indicative") for p in positions)
    res["read"] = book_read(res)
    return res


__all__ = ["book_scenarios", "book_read", "compute_book_under_tape", "fit_vol_rule", "vol_scale",
           "nearest_short", "breached", "HORIZONS", "FOCUS_H", "_MULT"]
