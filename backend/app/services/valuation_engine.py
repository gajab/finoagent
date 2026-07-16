"""Structural pro-forma valuation engine — pure, deterministic (no LLM, no network).

The arithmetic layer beneath the debate. It turns EXTRACTED figures + primitive driver
changes into an EPS and a price target, composing the drivers through a real income
statement so covariance is *structural*, not summed:

* **Operating leverage** — a revenue increase flows through at the *incremental* margin
  (fixed costs spread), so margin expands on its own; no agent has to assert it.
* **Trade-offs** — a "price cut for volume" claim raises revenue AND cuts margin in one
  coherent driver set; the P&L nets them (op income can fall while revenue rises).

Nothing here calls an LLM. The agents will only supply the extracted inputs (the driver
magnitudes and the base figures); this module owns the arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .valuation_priors import (
    PE_CAP, PE_FLOOR, PEG_TARGET,
    combine_persistence, persistence_prior, survival_from_tier,
)


@dataclass
class PnL:
    """Latest-year base income statement, in dollars / fractions.

    ``nonop_expense`` is everything between operating income and pre-tax income
    (net interest + other), computed as ``op_income − pretax_income`` so the base
    case reconciles exactly to reported net income.
    """
    revenue: float
    op_income: float
    nonop_expense: float
    tax_rate: float
    shares: float


@dataclass
class OperatingLeverage:
    """Incremental operating economics fitted from history:
    ``Δop_income ≈ inc_margin · Δrevenue``."""
    inc_margin: float        # incremental operating margin (regression slope)
    r2: float
    avg_margin: float        # latest operating margin (the fallback)
    source: str              # "regression" | "avg-margin-fallback"


@dataclass
class Drivers:
    """Primitive changes vs. the base year. Each claim maps onto these; the P&L
    composes them so interactions (leverage, price↔margin) are handled structurally."""
    rev_growth: float = 0.0          # fractional Δ revenue
    margin_delta_bps: float = 0.0    # structural Δ operating margin, bps (+expand / −compress)
    share_change: float = 0.0        # fractional Δ shares (buyback ⇒ negative)
    other_pretax: float = 0.0        # one-off pretax $ (a specific disclosed cost/benefit)


def pnl_anchored(revenue: float, op_income: float, tax_rate: float, shares: float,
                 net_income: float) -> PnL:
    """Build the base P&L so it reconciles EXACTLY to reported net income: back out
    ``nonop_expense`` from the reported bottom line. Drivers then apply as deltas on a
    correct base (base_eps == net_income / shares), instead of drifting on period/
    special-item noise between operating income and the reported number."""
    pretax = net_income / (1 - tax_rate) if tax_rate < 1 else net_income
    return PnL(revenue=revenue, op_income=op_income,
               nonop_expense=op_income - pretax, tax_rate=tax_rate, shares=shares)


def estimate_operating_leverage(revenue_hist, op_income_hist, min_r2: float = 0.5) -> OperatingLeverage:
    """OLS of operating income on revenue → the incremental margin (slope).

    Airlines/cyclicals give lumpy, short samples, so an implausible fit (low R², or a
    slope outside a sane band) falls back to a constant average margin — no operating
    leverage rather than a garbage slope.
    """
    r = [float(x) for x in (revenue_hist or []) if isinstance(x, (int, float))]
    o = [float(x) for x in (op_income_hist or []) if isinstance(x, (int, float))]
    n = min(len(r), len(o))
    avg_margin = (o[-1] / r[-1]) if (n and r[-1]) else 0.0
    if n >= 3:
        rx, oy = np.array(r[-n:]), np.array(o[-n:])
        denom = float(((rx - rx.mean()) ** 2).sum())
        if denom > 0:
            slope = float(((rx - rx.mean()) * (oy - oy.mean())).sum() / denom)
            intercept = float(oy.mean() - slope * rx.mean())
            ss_res = float(((oy - (slope * rx + intercept)) ** 2).sum())
            ss_tot = float(((oy - oy.mean()) ** 2).sum())
            r2 = (1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
            # A real incremental margin is a plausible fraction and ≥ the average
            # (positive operating leverage). Otherwise distrust the short sample.
            if r2 >= min_r2 and avg_margin <= slope < 1.0:
                return OperatingLeverage(round(slope, 4), round(r2, 3), round(avg_margin, 4), "regression")
    return OperatingLeverage(round(avg_margin, 4), 0.0, round(avg_margin, 4), "avg-margin-fallback")


def proforma(base: PnL, lev: OperatingLeverage, d: Drivers) -> dict:
    """Compose the drivers through the income statement. Deltas are applied to the
    ACTUAL base op income (so drivers=0 reproduces the reported figures exactly)."""
    new_rev = base.revenue * (1 + d.rev_growth)
    d_rev = new_rev - base.revenue
    op = (
        base.op_income
        + lev.inc_margin * d_rev                       # operating leverage on the revenue change
        + (d.margin_delta_bps / 1e4) * new_rev          # structural margin shift (mix / price / cost)
        + d.other_pretax
    )
    ni = (op - base.nonop_expense) * (1 - base.tax_rate)
    new_shares = base.shares * (1 + d.share_change)
    eps = ni / new_shares if new_shares else 0.0
    return {
        "revenue": new_rev,
        "op_income": op,
        "op_margin_pct": round(op / new_rev * 100, 2) if new_rev else None,
        "net_income": ni,
        "shares": new_shares,
        "eps": round(eps, 2),
    }


def base_eps(base: PnL, lev: OperatingLeverage) -> float:
    """EPS with no driver changes — should reconcile to reported EPS."""
    return proforma(base, lev, Drivers())["eps"]


def scenario_target(base: PnL, lev: OperatingLeverage, d: Drivers, multiple: float) -> dict:
    """Full scenario → EPS → price target via a multiple (the multiple comes from the
    cross-sectional model later, not from here)."""
    pf = proforma(base, lev, d)
    return {**pf, "multiple": round(multiple, 2), "target": round(pf["eps"] * multiple, 2)}


# ── Cross-sectional warranted multiple (Fix 3) ─────────────────────────────

def warranted_multiple(growth_pct: float, margin_quality: float | None = None,
                       *, peg: float = PEG_TARGET, floor: float = PE_FLOOR,
                       cap: float = PE_CAP) -> float:
    """Fair forward P/E conditioned on the SCENARIO's fundamentals, not the stock's own
    history — so a broken compounder floors at the market base rate instead of its
    glory-days multiple (the "historical multiple trap").

    PEG discipline: fair fwd P/E ≈ PEG × growth% (whole percent), with a quality tilt —
    fatter, more durable operating/FCF margins warrant a higher PEG. Clamped to a
    market-plausible band; low/no growth collapses toward the floor regardless of what
    the name used to trade at.
    """
    g = max(0.0, growth_pct)
    tilt = 1.0
    if margin_quality is not None:
        tilt = min(1.5, max(0.5, 0.5 + 2.0 * margin_quality))
    return round(min(cap, max(floor, peg * tilt * g)), 1)


# ── Claim aggregation — driver-claims → target + range + confidence ────────

@dataclass
class Claim:
    """One quantified driver-claim an agent extracted. The LLM supplies ``driver`` +
    ``magnitude`` + evidence ``tier`` (which it can classify reliably); the engine owns
    every downstream number (survival weight, persistence, multiple, target)."""
    driver: str                        # revenue | margin | buyback | other | multiple
    magnitude: float                   # native unit: rev frac; margin bps; share frac; $; P/E pts
    tier: str = "E5"                   # E1..E5 evidence tier
    persistence_nudge: float = 0.0     # optional mechanism-backed persistence tweak (capped)
    unanswered: bool = False           # a Bear rebuttal landed and went unanswered
    label: str = ""                    # human description (for the waterfall)


# Two distinct adjustments, deliberately NOT conflated:
#   • evidence *survival* shrinks the near-term EPS step (how much to TRUST the claim);
#   • *persistence* shrinks the MULTIPLE (how DURABLE the growth is over the horizon).
# A disclosed "+35% next year" fully hits next year's EPS (survival-adjusted) but, if it
# fades, earns only a low-teens multiple — the fade lives in the P/E, not in next-year EPS.

def _add_flow(d: Drivers, driver: str, magnitude: float, s: float) -> None:
    """Fold one flow claim into the driver bundle at its evidence-survival weight."""
    if driver == "revenue":
        d.rev_growth += magnitude * s
    elif driver == "margin":
        d.margin_delta_bps += magnitude * s
    elif driver == "buyback":
        d.share_change += magnitude * s
    elif driver == "other":
        d.other_pretax += magnitude * s


def _fold(claims, horizon: float, weight_scale: float = 1.0, persist_scale: float = 1.0):
    """→ (Drivers at evidence-survival, sustainable_growth for the multiple, rerate).
    ``sustainable_growth`` = Σ revenue growth × survival × persistence — the durable rate
    the warranted multiple is entitled to pay for."""
    d, sustainable, rerate = Drivers(), 0.0, 0.0
    for c in claims:
        s = min(1.0, max(0.0, survival_from_tier(c.tier, c.unanswered) * weight_scale))
        if c.driver == "multiple":
            rerate += c.magnitude * s
            continue
        _add_flow(d, c.driver, c.magnitude, s)
        if c.driver == "revenue":
            p = min(1.0, combine_persistence(persistence_prior(c.magnitude, horizon),
                                             c.persistence_nudge, c.tier) * persist_scale)
            sustainable += c.magnitude * s * p
    return d, sustainable, rerate


def _scenario(base: PnL, lev: OperatingLeverage, claims, margin_quality, own_hist_pe,
              weight_scale: float, persist_scale: float, horizon: float) -> dict:
    """EPS + warranted multiple + target for one survival/persistence scaling. Bear and
    bull share this: a bear gets both a lower EPS (less survival) AND a lower multiple
    (less persistence) — the realistic double-whammy."""
    d, sustainable, rerate = _fold(claims, horizon, weight_scale, persist_scale)
    pf = proforma(base, lev, d)
    cap = min(PE_CAP, own_hist_pe) if own_hist_pe else PE_CAP
    multiple = round(min(cap, max(PE_FLOOR, warranted_multiple(sustainable * 100, margin_quality) + rerate)), 1)
    return {**pf, "growth_pct": round(d.rev_growth * 100, 1),
            "sustainable_growth_pct": round(sustainable * 100, 1),
            "multiple": multiple, "target": round(pf["eps"] * multiple, 2)}


def _waterfall(base: PnL, lev: OperatingLeverage, claims) -> list:
    """Marginal EPS contribution of each flow claim, in order — the "show your work"
    bridge from base EPS to scenario EPS (survival-adjusted; multiple claims re-rate, so
    they don't appear here)."""
    steps, d = [], Drivers()
    prev = base_eps(base, lev)
    for c in claims:
        if c.driver == "multiple":
            continue
        _add_flow(d, c.driver, c.magnitude, survival_from_tier(c.tier, c.unanswered))
        cur = proforma(base, lev, d)["eps"]
        steps.append({"label": c.label or c.driver, "driver": c.driver, "tier": c.tier,
                      "survival": survival_from_tier(c.tier, c.unanswered),
                      "eps_delta": round(cur - prev, 2)})
        prev = cur
    return steps


def assemble(base: PnL, lev: OperatingLeverage, claims, *, price: float | None = None,
             margin_quality: float | None = None, own_hist_pe: float | None = None,
             horizon: float = 5.0) -> dict:
    """The valuation core: tiered driver-claims → headline target + bear/bull range +
    confidence + a waterfall. This replaces the debate's ad-hoc "valuation bridge" —
    the LLM never multiplies anything; it only supplies claims and evidence tiers."""
    b = _scenario(base, lev, claims, margin_quality, own_hist_pe, 1.00, 1.00, horizon)
    bear = _scenario(base, lev, claims, margin_quality, own_hist_pe, 0.50, 0.70, horizon)
    bull = _scenario(base, lev, claims, margin_quality, own_hist_pe, 1.25, 1.15, horizon)
    tgt = b["target"]
    lo, hi = sorted((bear["target"], bull["target"]))
    width = (hi - lo) / tgt if tgt else 1.0
    out = {
        "base_eps": base_eps(base, lev),
        "eps": b["eps"],
        "growth_pct": b["growth_pct"],
        "multiple": b["multiple"],
        "target": tgt,
        "range": [round(lo, 2), round(hi, 2)],
        "sustainable_growth_pct": b["sustainable_growth_pct"],
        "scenarios": {"bear": bear["target"], "base": tgt, "bull": bull["target"]},
        "confidence": round(max(0.30, min(0.90, 1 - width)), 2),
        "waterfall": _waterfall(base, lev, claims),
    }
    if price:
        out["price"] = round(price, 2)
        out["upside_pct"] = round((tgt / price - 1) * 100, 1)
    return out
