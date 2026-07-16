"""Empirical priors for the valuation engine — the reference-class constants and tables
that fence the LLM's residual judgment. Pure, deterministic, no network.

Everything here is a **market-wide prior** (the same for every stock), not per-name
discretion. The LLM never emits these numbers; it only classifies a claim's evidence tier
(reliable) and the tables/functions below turn that into a survival weight, a persistence
prior, or a warranted multiple.

The functional forms are documented empirical approximations (e.g. Mauboussin-style
mean-reversion, PEG discipline). Their few coefficients are market-calibrated priors that a
fuller build would fit to a live cross-section — but they are constants, not vibes.
"""

from __future__ import annotations

import math

# ── Evidence tier → survival weight ────────────────────────────────────────
# The LLM classifies the tier; this maps it to a probability the claim holds.
EVIDENCE_SURVIVAL = {
    "E1": 0.90,   # extracted disclosed figure (10-K / 10-Q / earnings release)
    "E2": 0.65,   # management guidance
    "E3": 0.60,   # analyst consensus
    "E4": 0.35,   # historical-trend extrapolation
    "E5": 0.10,   # pure narrative ("strong brand")
}
UNANSWERED_REBUTTAL_HAIRCUT = 0.25


def survival_from_tier(tier: str, unanswered_rebuttal: bool = False) -> float:
    """Survival weight for a claim from its evidence tier; a landed-but-unanswered
    rebuttal knocks it down (so ignoring the Bear mechanically lowers the number)."""
    s = EVIDENCE_SURVIVAL.get((tier or "").upper(), 0.10)
    if unanswered_rebuttal:
        s = max(0.0, s - UNANSWERED_REBUTTAL_HAIRCUT)
    return round(s, 3)


# ── Fade / persistence prior ───────────────────────────────────────────────
# Growth/margin advantages mean-revert toward the market; the higher the starting
# level, the faster it reverts (measured across large cross-sections). Modeled as
# exponential decay with a level-dependent half-life.
MARKET_GROWTH = 0.06   # long-run nominal growth anchor (fraction)


def persistence_prior(start_level: float, horizon_years: float) -> float:
    """P(the excess advantage largely persists over the horizon), 0..1.

    ``start_level`` is the driver's rate as a fraction (e.g. 0.40 growth, or a margin
    gap). Near-market levels are durable; top-decile levels fade fast — e.g. 40% growth
    persists ~15% over 5y, 10% growth ~45%, market-level ~85%.
    """
    excess = abs(start_level) - MARKET_GROWTH
    if excess <= 0:
        return 0.85
    half_life = max(1.0, 5.0 * math.exp(-3.0 * excess))   # shrinks as excess grows
    return round(math.exp(-math.log(2) * horizon_years / half_life), 3)


def combine_persistence(prior: float, llm_nudge: float = 0.0, evidence_tier: str = "E5",
                        cap: float = 0.20) -> float:
    """Shrink the LLM's nudge toward the empirical prior. Only strong (E1/E2) *mechanism*
    evidence — a disclosed backlog / contract / capacity commitment — earns a bigger,
    still-capped, move. Narrative (E4/E5) can't move persistence at all."""
    weight = {"E1": 0.5, "E2": 0.35}.get((evidence_tier or "").upper(), 0.0)
    nudge = max(-cap, min(cap, llm_nudge)) * weight
    return round(min(1.0, max(0.0, prior + nudge)), 3)


# ── Market anchors for the warranted multiple ──────────────────────────────
MARKET_FWD_PE = 18.0
PEG_TARGET = 1.6         # market-normal PEG: fair fwd P/E ≈ PEG × growth%
PE_FLOOR = 8.0
PE_CAP = 45.0
