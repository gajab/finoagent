"""First-passage odds for a stop/target trade plan.

Python port of ``frontend/src/beta/ta/firstPassage.ts`` (same model, same names, same conventions) so the
classic Trade-Setups card and the Beta Technical view quote the SAME odds. One deliberate numerical
difference: the fill-time integral uses geometric-panel Gauss-Legendre instead of a fixed Simpson grid, which
is exact where the TS grid is off by up to ~0.5pt (entries within ~0.3% of spot). Everywhere else the two
agree to ~1e-4.

WHY THIS EXISTS. A trade plan "wins" when price touches T1 BEFORE it touches the stop, at any moment inside
the holding window. Scoring it with terminal probabilities — P(S_T >= T1) and P(S_T <= stop) at one date —
(a) ignores the path (a stop that is hit and then recovered is booked as a non-loss), (b) leaves the
"neither level touched" outcome unvalued, (c) feeds Kelly with (1 - p_win) as the loss probability while the
EV uses a different loss probability, and (d) measures probabilities from spot while reward/risk is measured
from entry. For a tight stop that overstates the win chance, the EV and the Kelly size by a wide margin.

THE MODEL. Log-price follows Brownian motion with the risk-neutral drift (r - sigma^2/2) and constant vol
sigma. Entry, stop and target are absorbing/measuring levels:
  * :func:`exit_probs`  — P(profit barrier first / loss barrier first / still inside) by horizon, from the
                          eigenfunction expansion of the two-sided exit problem (closed form + decaying tail).
  * :func:`touch_prob`  — P(a one-sided level is touched by the horizon): reflection principle with drift.
  * :func:`trade_odds`  — a limit entry must FILL first; the post-fill window is whatever is left of the
                          horizon, so the fill-time density is integrated against the two-sided exit odds.

WHAT THESE ODDS ARE NOT. They are market-implied (risk-neutral). Under that measure every stop/target rule is
a fair bet (zero expected P&L apart from carry), so the honest comparison is "odds vs the break-even win
rate" — they can show a plan is priced fairly, they cannot manufacture an edge. For a price martingale with
unlimited time, P(T1 first) = (entry - stop) / (target - stop) EXACTLY, which is the break-even rate.
Therefore NO expected value, Kelly fraction or position size may be derived from these odds. Any edge has to
come from the setup's structure being predictive, which is what the other lenses are for.

Validated against an independent Crank-Nicolson finite-difference solution and a price-path Monte Carlo
(``tests/test_first_passage.py`` pins the same golden numbers as ``frontend/scripts/beta-tests``).
Pure functions, numpy only.
"""

from __future__ import annotations

import math
from numbers import Real

import numpy as np

from .zebra_service import DEFAULT_RISK_FREE

MARKET_TOL = 0.0015          # an entry within 0.15% of spot is "at the market" (no wait for a fill)
_GL_X, _GL_W = (v.tolist() for v in np.polynomial.legendre.leggauss(12))   # Gauss-Legendre rule (plain floats)
_FILL_MASS_TOL = 1e-13       # fill-time mass neglected below the smallest panel
_MAX_SERIES_TERMS = 4000
_UNREACHABLE = 1000.0        # barrier width / sqrt(tau) beyond this: exit probability is < exp(-4e5) == 0


def norm_cdf(z: float) -> float:
    """Standard normal CDF; ``erfc`` keeps full relative accuracy in the tails."""
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


# ── two-sided exit: P(profit barrier first), P(loss barrier first) by horizon ────────────────────────────────────

def _exit_top_forever(down: float, up: float, m: float) -> float:
    """P(top barrier is the first one hit) with no time limit, for unit-variance BM with drift ``m``,
    start 0, barriers -down / +up (scale-function result)."""
    w = down + up
    if abs(m) < 1e-9:
        return down / w
    if m > 0:
        return math.expm1(-2 * m * down) / math.expm1(-2 * m * w)
    return math.exp(2 * m * up) * math.expm1(2 * m * down) / math.expm1(2 * m * w)


def _exit_top_tail(down: float, up: float, m: float, tau: float) -> float:
    """The decaying tail of the exit-through-the-top series, summed to convergence."""
    w = down + up
    n_terms = int(min(_MAX_SERIES_TERMS, max(24, math.ceil(4 * w / math.sqrt(tau)) + 8)))
    n = np.arange(1, n_terms + 1, dtype=float)
    lam = (n * n * math.pi ** 2) / (2 * w * w) + 0.5 * m * m
    sign = np.where(n % 2 == 1, 1.0, -1.0)
    # exp(m*up) of the prefactor is folded into the exponent so a large |m| cannot overflow on its own
    terms = sign * n * np.sin(n * math.pi * down / w) * np.exp(m * up - lam * tau) / lam
    return float((math.pi / (w * w)) * terms.sum())


def exit_probs(down: float, up: float, m: float, tau: float) -> dict:
    """Two-sided exit by scaled time ``tau`` = sigma^2 * T (years). Barriers are log-distances from the start:
    ``down`` = L below, ``up`` = U above; ``m`` = drift / sigma^2 (unit-variance scaling).
    Returns ``{"top", "bottom", "inside"}`` (sums to 1); degenerate inputs leave everything inside."""
    inside_all = {"top": 0.0, "bottom": 0.0, "inside": 1.0}
    if not (down > 0 and up > 0 and tau > 0 and math.isfinite(down) and math.isfinite(up)
            and math.isfinite(tau) and math.isfinite(m)):
        return inside_all
    if (down + up) / math.sqrt(tau) > _UNREACHABLE:      # the series would need >4000 terms; the answer is 0
        return inside_all
    top = min(1.0, max(0.0, _exit_top_forever(down, up, m) - _exit_top_tail(down, up, m, tau)))
    # bottom barrier = the top barrier of the mirrored process (drift -m, distances swapped)
    bottom = min(1.0, max(0.0, _exit_top_forever(up, down, -m) - _exit_top_tail(up, down, -m, tau)))
    if not (math.isfinite(top) and math.isfinite(bottom)):
        return inside_all
    return {"top": top, "bottom": bottom, "inside": max(0.0, 1.0 - top - bottom)}


# ── one-sided touch (fills) ──────────────────────────────────────────────────────────────────────────────────────

def touch_prob(a: float, drift: float, sigma: float, years: float) -> float:
    """P(a level at log-distance ``a`` is touched by T). ``drift`` is the log drift per year TOWARD the level
    (positive = drifting toward it). Reflection principle with drift."""
    if not (a > 0 and sigma > 0 and years > 0):
        return 1.0 if a <= 0 else 0.0
    sd = sigma * math.sqrt(years)
    p = norm_cdf((-a + drift * years) / sd) + math.exp(2 * drift * a / (sigma * sigma)) * norm_cdf((-a - drift * years) / sd)
    return min(1.0, max(0.0, p))


def _touch_density(a: float, drift: float, sigma: float, s: float) -> float:
    """First-passage time density of a level at distance ``a`` with drift ``drift`` toward it."""
    if not s > 0:
        return 0.0
    v = sigma * sigma
    return (a / (sigma * math.sqrt(2 * math.pi * s ** 3))) * math.exp(-((a - drift * s) ** 2) / (2 * v * s))


def _fill_then_exit(a: float, toward: float, sigma: float, t_years: float,
                    down: float, up: float, m: float) -> tuple[float, float, float]:
    """Joint odds of "the limit entry fills at time s" then "the plan exits through the top / the bottom / is
    still inside" over the remaining ``t_years - s``: the first-passage (fill-time) density integrated against
    the two-sided exit odds. Returns the three UNNORMALISED masses (they sum to P(fill)).

    The fill density is sharply peaked at early times when the entry sits close to spot (a plain Simpson grid
    mis-weights it by up to ~0.5pt there), so the window is cut into geometric panels [T/2^(j+1), T/2^j] with a
    Gauss-Legendre rule on each. The panels stop where the closed-form touch probability by that time is below
    ``_FILL_MASS_TOL``, so the neglected mass is bounded exactly, not guessed.
    """
    j = 0
    while j < 64 and touch_prob(a, toward, sigma, t_years / 2 ** j) > _FILL_MASS_TOL:
        j += 1
    edges = [t_years / 2 ** k for k in range(j + 1)]          # T, T/2, ..., T/2^j
    w = l = i = 0.0
    for hi, lo in zip(edges[:-1], edges[1:]):
        half, mid = (hi - lo) / 2.0, (hi + lo) / 2.0
        for x, wt in zip(_GL_X, _GL_W):
            s = mid + half * x
            f = _touch_density(a, toward, sigma, s) * wt * half
            e = exit_probs(down, up, m, sigma * sigma * (t_years - s))
            w += f * e["top"]
            l += f * e["bottom"]
            i += f * e["inside"]
    return w, l, i


# ── the trade plan ───────────────────────────────────────────────────────────────────────────────────────────────

def trade_odds(direction: str, spot: float, entry: float, stop: float, target: float,
               iv: float | None, days: float | None, r: float = DEFAULT_RISK_FREE) -> dict:
    """Honest odds for a stop/target plan.

    ``iv`` is the annualised implied vol as a DECIMAL (0.32) or ``None``; ``days`` the holding window in
    calendar days. Refuses (never guesses) with ``{"ok": False, "reason": ...}`` where ``reason`` is
    ``bad-levels`` | ``no-iv`` | ``no-window`` | ``numerical``.

    On success: ``win`` / ``loss`` / ``inside`` are the outcome odds GIVEN the entry fills inside the window
    (they sum to 1); ``fill_prob`` is ``None`` for an at-the-market entry; ``fill_kind`` is
    ``market`` | ``pullback`` | ``breakout``; ``break_even`` = risk / (risk + reward) is the win rate the plan
    needs before costs; ``resolved_win`` = win / (win + loss) is the share of plans that RESOLVE inside the
    window which reach T1 first (``None`` if none resolve); ``vs_break_even_pts`` = resolved_win - break_even
    in percentage points (~0 = priced fairly, negative = the odds sit below what the R:R needs).

    No EV is reported on purpose: under market-implied odds the unresolved trades carry the offsetting
    expected P&L, so ``win * reward - loss * risk`` alone is biased and the full expectation is ~0 by
    construction.
    """
    if direction not in ("long", "short"):
        return {"ok": False, "reason": "bad-levels"}
    sign = 1 if direction == "long" else -1
    levels = (spot, entry, stop, target)
    if not all(isinstance(x, Real) and math.isfinite(x) and x > 0 for x in levels):
        return {"ok": False, "reason": "bad-levels"}
    if not (sign * (target - entry) > 0 and sign * (entry - stop) > 0):
        return {"ok": False, "reason": "bad-levels"}
    if not (isinstance(iv, Real) and math.isfinite(iv) and iv > 0):
        return {"ok": False, "reason": "no-iv"}
    if not (isinstance(days, Real) and math.isfinite(days) and days > 0):
        return {"ok": False, "reason": "no-window"}

    sigma = float(iv)
    t_years = days / 365.0
    nu = (r - 0.5 * sigma * sigma) * sign                 # log drift along the PROFIT axis (flipped for shorts)
    m = nu / (sigma * sigma)
    up = math.log(target / entry) * sign                  # both > 0 in the profit frame
    down = math.log(entry / stop) * sign
    risk, reward = abs(entry - stop), abs(target - entry)

    # distance of the entry from spot along the profit axis: >0 means the entry sits ABOVE spot (needs a
    # breakout), <0 means it sits BELOW spot (needs a pullback)
    gap = sign * math.log(entry / spot)
    tol = math.log(1 + MARKET_TOL)
    fill_kind = "market"
    fill_prob: float | None = None

    if abs(gap) <= tol:
        e = exit_probs(down, up, m, sigma * sigma * t_years)
        win, loss, inside = e["top"], e["bottom"], e["inside"]
    else:
        fill_kind = "pullback" if gap < 0 else "breakout"
        a = abs(gap)
        toward = -nu if fill_kind == "pullback" else nu   # drift toward the fill level
        fill_prob = touch_prob(a, toward, sigma, t_years)
        w, l, i = _fill_then_exit(a, toward, sigma, t_years, down, up, m)
        # renormalise by the quadrature's own fill mass so the three outcomes sum to one given a fill
        # (it equals ``fill_prob`` to ~1e-13; a zero mass means the entry is effectively unreachable)
        mass = w + l + i
        if mass > 0:
            win, loss, inside = w / mass, l / mass, i / mass
        else:
            win, loss, inside = 0.0, 0.0, 1.0

    if not all(math.isfinite(x) for x in (win, loss, inside)):
        return {"ok": False, "reason": "numerical"}
    break_even = risk / (risk + reward)
    resolved_win = win / (win + loss) if win + loss > 1e-9 else None
    return {
        "ok": True, "days": days, "iv": sigma, "fill_prob": fill_prob, "fill_kind": fill_kind,
        "win": win, "loss": loss, "inside": inside,
        "risk": risk, "reward": reward, "rr": reward / risk,
        "break_even": break_even, "resolved_win": resolved_win,
        "vs_break_even_pts": None if resolved_win is None else (resolved_win - break_even) * 100.0,
    }
