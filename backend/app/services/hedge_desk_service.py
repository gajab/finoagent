"""Hedge Desk — institutional pre-trade analytics for a protective overlay.

The income desk scores a standalone premium trade. A HEDGE is a different
animal: you never judge the option in isolation — you judge whether
`stock + overlay` is a better risk-adjusted position than `stock alone`.
Every number here is that *differential*, measured under the market's OWN
risk-neutral density (SVI→RND), so it reconciles with the timing panel.

Two entry points:

  suggest_preferences()  — derive Floor / Downside-cap / Upside-cap / Give-up /
                           Budget from the live surface instead of constants.
  score_hedge_desk()     — a 6-pillar institutional scorecard + desk ranking.

Shared helpers (`_pnl_at_price`, `payoff_distribution_metrics`,
`higher_order_greeks`) are imported rather than re-implemented, so any quantity
computed twice comes from one place and the books add up.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

# ── RND plumbing ───────────────────────────────────────────────────────────
# The curve the backend already ships: pct (% move from spot), cdf_pct,
# pdf_per_pct (probability mass in % per 1% move bucket).


def _grid_from_curve(curve: dict, spot: float):
    """(prices, bucket_weights) from the downsampled RND curve.

    weights has len == len(prices) − 1 to match `payoff_distribution_metrics`,
    which integrates on bucket midpoints.
    """
    pct = np.asarray(curve.get("pct") or [], dtype=float)
    pdf = np.asarray(curve.get("pdf_per_pct") or [], dtype=float)
    if pct.size < 3 or pdf.size != pct.size or not spot:
        return None, None
    prices = spot * (1.0 + pct / 100.0)
    step = np.diff(pct)
    w = 0.5 * (pdf[:-1] + pdf[1:]) / 100.0 * step      # trapezoid mass per bucket
    s = w.sum()
    if s <= 0:
        return None, None
    return prices, w / s


def _pct_at_cdf(curve: dict, target_prob: float) -> Optional[float]:
    """% move where P(S_T ≤ level) == target_prob (target in 0..1)."""
    pct = curve.get("pct") or []
    cdf = curve.get("cdf_pct") or []
    if len(pct) < 2 or len(cdf) != len(pct):
        return None
    t = min(max(target_prob * 100.0, 0.01), 99.99)
    return float(np.interp(t, np.asarray(cdf, float), np.asarray(pct, float)))


def _weighted_cvar(pnl: np.ndarray, w: np.ndarray, alpha: float = 0.05) -> float:
    """Expected shortfall: probability-weighted mean of the worst `alpha` mass."""
    order = np.argsort(pnl)
    p, ww = pnl[order], w[order]
    c = np.cumsum(ww)
    keep = c <= alpha
    if not keep.any():                       # alpha smaller than the first bucket
        return float(p[0])
    tail_w = ww[keep]
    used = tail_w.sum()
    if used < alpha and keep.sum() < len(p):  # top up with a slice of the next bucket
        i = int(keep.sum())
        extra = alpha - used
        return float((np.sum(p[keep] * tail_w) + p[i] * extra) / alpha)
    return float(np.sum(p[keep] * tail_w) / used) if used > 0 else float(p[0])


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ── 1. Algorithmic preferences — replaces constant form defaults ───────────

def _optimize_floor(curve: dict, spot: float, puts: dict, budget_pct: float) -> Optional[dict]:
    """Search the REAL put chain for the floor with the best protection per dollar.

    For every liquid put 2–35% OTM, integrate under the market's own density:
      relief     — share of the naked CVaR95 tail the put removes
      efficiency — $ of tail removed per $ of premium
      breach     — P(S_T < K)
    Objective = 0.6·relief + 0.4·efficiency (each normalised across candidates),
    restricted to puts that fit the budget. If none fit, return the best one
    and let the caller finance it.
    """
    prices, w = _grid_from_curve(curve or {}, spot)
    if prices is None or not puts:
        return None
    s_mid = 0.5 * (prices[:-1] + prices[1:])
    naked = s_mid - spot                                   # per share
    cvar_naked = _weighted_cvar(naked, w)
    if cvar_naked >= 0:
        return None
    cands = []
    for k, q in puts.items():
        mid = float(getattr(q, "mid", 0) or 0)
        k = float(k)
        otm = (1 - k / spot) * 100
        if mid <= 0 or otm < 2 or otm > 35:
            continue
        hedged = naked + np.maximum(k - s_mid, 0.0) - mid
        red = _weighted_cvar(hedged, w) - cvar_naked
        if red <= 0:
            continue
        cands.append({
            "strike": k, "floor_pct": otm, "cost_pct": mid / spot * 100,
            "relief": red / abs(cvar_naked), "eff": red / mid,
            "breach": float(np.sum(w[s_mid < k])) * 100,
        })
    if not cands:
        return None
    fits = [c for c in cands if c["cost_pct"] <= budget_pct]
    pool = fits or cands
    # Most protection that still buys at a competitive rate: among strikes whose
    # $-efficiency is within 70% of the best on the chain, take the one removing
    # the most tail (ties → cheaper). This stops before premium turns wasteful
    # (the near-ATM puts), without under-insuring with a lottery-ticket wing.
    emax = max(c["eff"] for c in pool) or 1
    eligible = [c for c in pool if c["eff"] >= 0.70 * emax] or pool
    for c in pool:
        c["obj"] = c["relief"] - 1e-6 * c["cost_pct"] if c in eligible else -1.0
    best = max(pool, key=lambda c: c["obj"])
    best["eff_vs_best"] = best["eff"] / emax
    best["fits_budget"] = bool(fits)
    best["n_scanned"] = len(cands)
    return best


def _financing_call(calls: dict, spot: float, shortfall_pct: float, momentum: bool) -> Optional[dict]:
    """Highest call strike whose premium still covers the budget shortfall —
    i.e. fund the floor while surrendering as little upside as possible."""
    if not calls or shortfall_pct <= 0:
        return None
    min_gap = 8.0 if momentum else 4.0
    ok = []
    for k, q in calls.items():
        mid = float(getattr(q, "mid", 0) or 0)
        k = float(k)
        up = (k / spot - 1) * 100
        if mid <= 0 or up < min_gap or up > 60:
            continue
        if mid / spot * 100 >= shortfall_pct:
            ok.append((up, k, mid / spot * 100))
    if not ok:
        return None
    up, k, prem = max(ok)                                  # furthest-out strike that still funds it
    return {"strike": k, "cap_pct": up, "premium_pct": prem}


def suggest_preferences(market: dict, spot: float, horizon_days: int,
                        beta: Optional[float] = None,
                        puts: Optional[dict] = None,
                        calls: Optional[dict] = None) -> Optional[dict]:
    """Derive the protection preferences for THIS ticker and horizon.

    Budget  — a slice of the horizon's own 1σ move, tilted by VRP and β.
    Floor   — optimised over the live put chain: the strike with the best blend of
              tail relief and $-efficiency that fits the budget (falls back to a
              regime breach-probability rule when no chain is available).
    Upside  — only if the optimal floor overruns the budget: sell the furthest-out
              call that still covers the shortfall (wider into momentum).
    Downside cap — only when still short of budget AND vol-of-vol is calm; never
              sell the crash wing in a gappy regime.
    """
    rnd = (market or {}).get("rnd") or {}
    curve = rnd.get("curve")
    if not curve:
        return None

    score = float(market.get("score") or 50)
    verdict = market.get("verdict") or "Fair"
    vrp_pts = market.get("vrp_pts")
    skew_pts = market.get("skew_pts")
    atm_iv_pct = market.get("atm_iv_pct")
    ret_1m = market.get("ret_1m_pct")
    drawdown = market.get("drawdown_52w_pct")
    vol_of_vol = ((rnd.get("heston") or {}).get("vol_of_vol")) or 0.0
    T = max(horizon_days, 1) / 365.0
    momentum = (ret_1m or 0) > 4 and (drawdown or -99) > -6
    why: list[str] = []

    # ---- Budget ----
    iv = (atm_iv_pct or 25.0) / 100.0
    one_sigma = iv * math.sqrt(T)
    budget = 0.13 * one_sigma
    if vrp_pts is not None:
        budget *= _clamp(1.0 - (vrp_pts / 100.0) / 0.12, 0.55, 1.45)
    if beta and beta > 1.2:
        budget *= 1.0 + min((beta - 1.2) * 0.15, 0.25)
    budget_pct = round(_clamp(budget * 100.0, 0.3, 6.0), 1)
    why.append(f"Budget {budget_pct}% ≈ 13% of {one_sigma * 100:.1f}% — this name's one-sigma "
               f"move over {horizon_days}d"
               + (f", trimmed for a {vrp_pts:+.1f}pt variance premium" if vrp_pts and vrp_pts > 2 else "")
               + (f", widened for β {beta:.2f}" if beta and beta > 1.2 else "") + ".")

    # ---- Floor: optimise on the real chain, else regime rule ----
    opt = _optimize_floor(curve, spot, puts or {}, budget_pct) if puts else None
    upside_cap_pct = downside_cap_pct = 0
    if opt:
        floor_pct = int(round(_clamp(opt["floor_pct"], 2, 35)))
        target_breach = opt["breach"]
        why.append(f"Floor −{floor_pct}% (${opt['strike']:g} put) is the best of {opt['n_scanned']} "
                   f"strikes scanned: removes {opt['relief'] * 100:.0f}% of the tail at "
                   f"{opt['eff']:.1f}× protection per $ ({opt.get('eff_vs_best', 1) * 100:.0f}% of the chain's best rate), costs {opt['cost_pct']:.2f}%, "
                   f"{target_breach:.0f}% chance of being breached.")
        shortfall = opt["cost_pct"] - budget_pct
        if shortfall > 0.05:
            fin = _financing_call(calls or {}, spot, shortfall, momentum)
            if fin:
                upside_cap_pct = int(round(fin["cap_pct"]))
                why.append(f"That floor overruns the budget by {shortfall:.2f}%, so the desk sells the "
                           f"+{upside_cap_pct}% call (${fin['strike']:g}) — the furthest-out strike "
                           f"that still covers it, so you give up as little upside as possible"
                           + (" (kept wide: the name is trending near its highs)." if momentum else "."))
            elif vol_of_vol and vol_of_vol <= 1.2 and (skew_pts or 0) > 2:
                deep = _pct_at_cdf(curve, 0.03)
                downside_cap_pct = int(round(_clamp(-(deep or -35.0), floor_pct + 12.0, 55.0)))
                why.append(f"No call covers the shortfall, so the desk sells the −{downside_cap_pct}% "
                           f"put wing (3% breach odds) to subsidise the floor.")
            else:
                why.append(f"The floor runs {shortfall:.2f}% over budget and no financing fits — "
                           f"consider a deeper floor or a larger budget.")
        else:
            why.append("It fits the budget outright — no cap needed, you keep the full upside.")
    else:
        if score >= 65:
            target_breach, regime = 18.0, "protection is cheap"
        elif score >= 45:
            target_breach, regime = 13.0, "mixed pricing"
        else:
            target_breach, regime = 9.0, "protection is rich"
        floor_move = _pct_at_cdf(curve, target_breach / 100)
        floor_pct = int(round(_clamp(-(floor_move or -10.0), 2.0, 35.0)))
        why.append(f"Floor −{floor_pct}% sits at a {target_breach:.0f}% market-implied breach "
                   f"probability — {regime} ({verdict.lower()}). (No live chain: regime rule used.)")

    if vol_of_vol and vol_of_vol > 1.2 and not downside_cap_pct:
        why.append(f"Crash wing left unsold: vol-of-vol {vol_of_vol * 100:.0f}% signals gap risk.")

    return {
        "floor_pct": int(floor_pct),
        "downside_cap_pct": int(downside_cap_pct),
        "upside_cap_pct": int(upside_cap_pct),
        "giveup_pct": 0,
        "budget_pct": float(budget_pct),
        "target_breach_pct": round(float(target_breach)),
        "regime": verdict,
        "needs_financing": bool(upside_cap_pct or downside_cap_pct),
        "optimized": bool(opt),
        "horizon_days": int(horizon_days),
        "why": why,
    }


# ── 2. Institutional scorecard ─────────────────────────────────────────────

_PILLARS = [
    ("tail_efficiency", "Tail efficiency", 0.26),
    ("cost_carry",      "Cost & carry",    0.20),
    ("protection",      "Protection quality", 0.20),
    ("risk_reward",     "Risk / reward",   0.16),
    ("regime_fit",      "Regime fit",      0.12),
    ("execution",       "Execution",       0.06),
]


def score_hedge_desk(hedges: list[dict], *, spot: float, shares: float,
                     notional: float, curve: dict, market: dict,
                     horizon_days: int, budget_pct: float,
                     pnl_at_price, higher_order_greeks=None,
                     payoff_metrics=None) -> Optional[dict]:
    """Rank every candidate structure on a hedging-specific desk scorecard.

    `pnl_at_price(price, shares, spot, legs)` is injected so the payoff is the
    exact same helper the rest of the hedging engine uses.
    """
    prices, w = _grid_from_curve(curve or {}, spot)
    if prices is None:
        return None

    rnd = (market or {}).get("rnd") or {}
    iv_rank = market.get("iv_rank")
    vrp_pts = market.get("vrp_pts")
    vol_of_vol = ((rnd.get("heston") or {}).get("vol_of_vol")) or 0.0

    naked = shares * (prices - spot)
    naked_mid = (naked[:-1] + naked[1:]) / 2.0
    cvar_naked = _weighted_cvar(naked_mid, w)
    exp_naked_loss = float(np.sum(w * np.maximum(-naked_mid, 0.0)))
    exp_naked_gain = float(np.sum(w * np.maximum(naked_mid, 0.0)))

    naked_metrics = None
    if payoff_metrics:
        naked_metrics = payoff_metrics(prices, naked, w, notional,
                                       -abs(shares * spot), horizon_days)

    rows: list[dict] = []
    for h in hedges:
        legs = h.get("legs") or []
        if not legs:
            continue
        hedged = np.array([pnl_at_price(float(p), shares, spot, legs) for p in prices])
        hedged_mid = (hedged[:-1] + hedged[1:]) / 2.0
        overlay_mid = hedged_mid - naked_mid          # option-only P&L incl. premium

        net_cost = float(h.get("net_cost") or 0.0)    # +debit / −credit
        cost_pct = abs(float(h.get("cost_pct_of_notional") or 0.0))

        # --- core differentials, all under the market's own density ---
        cvar_hedged = _weighted_cvar(hedged_mid, w)
        cvar_reduction = cvar_hedged - cvar_naked      # >0 = tail removed
        exp_overlay = float(np.sum(w * overlay_mid))   # ≈ −premium if fairly priced
        loss_mask = naked_mid < 0
        indemnity = float(np.sum(w[loss_mask] * np.maximum(overlay_mid[loss_mask], 0.0)))
        forfeited = float(np.sum(w[~loss_mask] * np.maximum(-overlay_mid[~loss_mask], 0.0)))
        coverage = indemnity / exp_naked_loss if exp_naked_loss > 1e-9 else 0.0

        hedged_metrics = None
        if payoff_metrics:
            hedged_metrics = payoff_metrics(prices, hedged, w, notional,
                                            -abs(float(h.get("max_loss") or 0.0)), horizon_days)

        # --- pillars ---
        # Tail efficiency needs BOTH: capital efficiency ($ of tail removed per $
        # spent) AND meaningful relief (how much of the naked tail actually went
        # away). A free structure that barely hedges is not a good hedge — so a
        # credit gets full marks on the efficiency leg but still has to earn the
        # relief leg.
        spend = max(net_cost, 0.0)
        if spend < 1.0:                               # credit / ~free structure
            eff_ratio = 99.0 if cvar_reduction > 0 else 0.0
        else:
            eff_ratio = cvar_reduction / spend
        relief = cvar_reduction / abs(cvar_naked) if abs(cvar_naked) > 1e-9 else 0.0
        p_tail = _clamp(0.55 * _clamp(relief / 0.60)          # 60% of the tail ⇒ full
                        + 0.45 * _clamp(eff_ratio / 8.0))     # $8 per $1 ⇒ full

        bud = max(budget_pct, 0.25)
        p_cost = _clamp(1.0 - cost_pct / (bud * 1.6))
        if net_cost < 0:
            p_cost = max(p_cost, 0.92)                # a credit hedge is cheap by construction
        theta_drag = abs(float(h.get("theta_per_day") or 0.0)) * max(horizon_days, 1) / max(notional, 1)
        p_cost = _clamp(0.75 * p_cost + 0.25 * (1.0 - _clamp(theta_drag / 0.03)))

        probs = h.get("probs") or {}
        gap = (probs.get("p_below_buffer_pct") or 0.0) / 100.0   # tail re-opens below a sold put
        p_prot = _clamp(coverage / 0.75) * (1.0 - _clamp(gap / 0.25) * 0.35)

        rr = indemnity / forfeited if forfeited > 1e-9 else (3.0 if indemnity > 0 else 0.0)
        p_rr = _clamp(rr / 2.0)
        if hedged_metrics and naked_metrics:
            oh, on = hedged_metrics.get("omega"), naked_metrics.get("omega")
            if oh and on and on > 0:
                p_rr = _clamp(0.65 * p_rr + 0.35 * _clamp((oh / on) / 1.6))

        sr = h.get("smile_risk") or {}
        short_vol = bool(sr.get("short_vol"))
        rich = (iv_rank is not None and iv_rank > 60) or (vrp_pts is not None and vrp_pts > 6)
        cheap = (iv_rank is not None and iv_rank < 40) or (vrp_pts is not None and vrp_pts < 2)
        if short_vol:
            p_regime = 0.80 if rich else (0.35 if cheap else 0.55)
            if vol_of_vol > 1.0:
                p_regime -= 0.22                      # short vol into a gappy tape
        else:
            p_regime = 0.82 if cheap else (0.40 if rich else 0.58)
        p_regime = _clamp(p_regime)

        spread_cost, prem = 0.0, 0.0
        thin = 0
        for lg in legs:
            mid = float(lg.get("mid") or 0.0)
            prem += abs(mid) * float(lg.get("contracts") or 1) * 100.0
            spread_cost += abs(float(lg.get("ask") or 0) - float(lg.get("bid") or 0)) \
                * 0.5 * float(lg.get("contracts") or 1) * 100.0
            if (lg.get("oi") or 0) < 250:
                thin += 1
        slip = spread_cost / prem if prem > 1e-9 else 0.5
        p_exec = _clamp(1.0 - slip / 0.18) * (1.0 - 0.12 * thin) * (1.0 - 0.05 * max(0, len(legs) - 2))
        p_exec = _clamp(p_exec)

        pillars = {"tail_efficiency": p_tail, "cost_carry": p_cost, "protection": p_prot,
                   "risk_reward": p_rr, "regime_fit": p_regime, "execution": p_exec}
        composite = sum(pillars[k] * wgt for k, _lbl, wgt in _PILLARS) * 100.0

        # --- why: the two strongest and the weakest pillar ---
        ranked = sorted(_PILLARS, key=lambda t: pillars[t[0]] * t[2], reverse=True)
        weakest = min(_PILLARS, key=lambda t: pillars[t[0]])
        rows.append({
            "id": h.get("id"),
            "score": round(composite),
            "grade": ("A" if composite >= 78 else "B" if composite >= 64
                      else "C" if composite >= 50 else "D"),
            "pillars": {k: round(v * 100) for k, v in pillars.items()},
            "hedge_efficiency": round(eff_ratio, 2) if eff_ratio < 90 else None,
            "cvar_reduction": round(cvar_reduction, 0),
            "tail_relief_pct": round(relief * 100, 1),
            "cvar_hedged": round(cvar_hedged, 0),
            "expected_overlay_pnl": round(exp_overlay, 0),
            "expected_indemnity": round(indemnity, 0),
            "upside_forfeited": round(forfeited, 0),
            "loss_coverage_pct": round(coverage * 100, 1),
            "reward_ratio": round(rr, 2) if rr < 90 else None,
            "omega_hedged": (hedged_metrics or {}).get("omega"),
            "omega_naked": (naked_metrics or {}).get("omega"),
            "sortino_hedged": (hedged_metrics or {}).get("sortino"),
            "strengths": [lbl for _k, lbl, _w in ranked[:2]],
            "weakness": weakest[1],
            "short_vol": short_vol,
        })

    if not rows:
        return None
    rows.sort(key=lambda r: r["score"], reverse=True)
    top = rows[0]
    by_id = {r["id"]: r for r in rows}

    eff = top.get("hedge_efficiency")
    verdict = (
        f"{top['grade']} · {top['score']}/100 — removes {_usd(top['cvar_reduction'])} of "
        f"tail risk"
        + (f" for every $1 spent: {eff:.1f}×" if eff else " at no net premium")
        + f"; covers {top['loss_coverage_pct']:.0f}% of expected losses, "
        f"forfeits {_usd(top['upside_forfeited'])} of expected upside."
    )
    return {
        "ranked": rows,
        "by_id": by_id,
        "pick_id": top["id"],
        "verdict": verdict,
        "cvar_naked": round(cvar_naked, 0),
        "expected_naked_loss": round(exp_naked_loss, 0),
        "expected_naked_gain": round(exp_naked_gain, 0),
        "omega_naked": (naked_metrics or {}).get("omega"),
        "pillar_labels": {k: lbl for k, lbl, _w in _PILLARS},
        "pillar_weights": {k: wgt for k, _lbl, wgt in _PILLARS},
    }


def _usd(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"${abs(v):,.0f}"
