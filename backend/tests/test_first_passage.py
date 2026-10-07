"""First-passage odds for a stop/target plan (app/services/first_passage.py).

Pins the SAME independent reference numbers as the Beta's TypeScript tests
(``frontend/scripts/beta-tests/test_taodds.cjs`` + ``golden/*.py``), so the classic Trade-Setups card and the Beta
Technical view cannot drift apart:

  * the two-sided exit odds against a Crank-Nicolson finite-difference solution (PDE_GOLDEN), plus a live
    low-resolution re-solve here so the golden numbers themselves are not taken on faith;
  * the whole plan (limit-entry fill, then target-vs-stop) against a GBM price-path Monte Carlo (MC_GOLDEN);
  * the fill-time integral against ``scipy.integrate.quad`` (the TS Simpson grid is up to ~0.5pt off for
    entries within ~0.3% of spot; the Python port must not be);
  * the exact identity P(T1 first) = (entry - stop) / (target - stop) for a price martingale with unlimited
    time, i.e. market-implied odds are a FAIR bet — the reason no EV / Kelly is derived from them.
"""
import math

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.sparse import diags
from scipy.sparse.linalg import splu

from app.services.first_passage import (
    DEFAULT_RISK_FREE, exit_probs, norm_cdf, touch_prob, trade_odds, _touch_density,
)

# (L, U, sigma, days, r, top, bottom) — Crank-Nicolson PDE, 4000x4000 grid (ref_exit_pde.py → fp_golden.json)
PDE_GOLDEN = [
    (0.0164, 0.104, 0.3, 30, 0.045, 0.11492781460645332, 0.8424822717202645),
    (0.04, 0.08, 0.3, 30, 0.045, 0.28964500300394136, 0.6229565763237223),
    (0.05, 0.05, 0.4, 15, 0.045, 0.46999336186654334, 0.4803877406840664),
    (0.025, 0.06, 0.25, 10, 0.045, 0.1410599706391594, 0.5422875133286911),
    (0.08, 0.03, 0.35, 45, 0.045, 0.7233594751002657, 0.2746003488672288),
    (0.03, 0.03, 0.2, 5, 0.045, 0.20363171448566833, 0.19613693066327018),
    (0.1, 0.25, 0.5, 90, 0.045, 0.22507373385556617, 0.6940356268644864),
    (0.015, 0.02, 0.45, 20, 0.0, 0.42428939307221364, 0.5757106067730005),
]

# (name, direction, spot, entry, stop, target, iv, days, fill, win, loss, inside) — price-path Monte Carlo,
# 200k paths x 2000 steps with continuity-corrected barriers (ref_plan_montecarlo.py → joint_golden.json)
MC_GOLDEN = [
    ('long pullback (NVDA-style)', 'long', 238.9, 233.67, 229.87, 259.31, 0.3, 30,
     0.7975, 0.09751097178683385, 0.8169655172413793, 0.08552351097178679),
    ('long breakout', 'long', 100.0, 103.0, 98.0, 112.0, 0.35, 20,
     0.71576, 0.21681150106180844, 0.46315804180172127, 0.32003045713647027),
    ('short pullback (rally fill)', 'short', 50.0, 52.0, 54.5, 45.0, 0.4, 25,
     0.70229, 0.10185963063691637, 0.5750331059818593, 0.3231072633812243),
    ('long at market', 'long', 60.0, 60.05, 57.5, 66.0, 0.28, 15,
     1.0, 0.092025, 0.45072, 0.45725499999999997),
    ('short at market', 'short', 80.0, 79.9, 84.0, 72.0, 0.33, 12,
     1.0, 0.07818, 0.414795, 0.5070250000000001),
]


def _m(sigma, r):
    return (r - 0.5 * sigma * sigma) / (sigma * sigma)


class TestNormCdf:
    def test_known_values_core_and_tails(self):
        assert norm_cdf(0) == pytest.approx(0.5, abs=1e-15)
        assert norm_cdf(1.959963984540054) == pytest.approx(0.975, abs=1e-12)
        assert norm_cdf(-3) == pytest.approx(0.0013498980316301, abs=1e-13)
        assert norm_cdf(-6) == pytest.approx(9.865876450377e-10, abs=1e-15)
        assert norm_cdf(6) == pytest.approx(1 - 9.865876450377e-10, abs=1e-15)
        assert norm_cdf(1) + norm_cdf(-1) == pytest.approx(1, abs=1e-15)
        assert norm_cdf(math.inf) == 1.0 and norm_cdf(-math.inf) == 0.0


class TestExitProbs:
    @pytest.mark.parametrize("L,U,sigma,days,r,top,bottom", PDE_GOLDEN)
    def test_matches_pde_golden(self, L, U, sigma, days, r, top, bottom):
        e = exit_probs(L, U, _m(sigma, r), sigma * sigma * days / 365)
        # the golden is itself a finite-difference number (~1e-8 off the exact series), so 1e-6 is tight
        assert e["top"] == pytest.approx(top, abs=1e-6)
        assert e["bottom"] == pytest.approx(bottom, abs=1e-6)
        assert e["top"] + e["bottom"] + e["inside"] == pytest.approx(1, abs=1e-12)

    def test_matches_a_live_low_resolution_pde(self):
        """Independent re-solve (Crank-Nicolson, Rannacher start) so the pinned numbers are not circular."""
        def pde_top(L, U, m, tau, nx=1000, nt=1000):
            x = np.linspace(-L, U, nx + 1)
            h = x[1] - x[0]
            a, b = 0.5 / h ** 2, m / (2 * h)
            A = diags([np.full(nx - 2, a - b), np.full(nx - 1, -2 * a), np.full(nx - 2, a + b)], [-1, 0, 1], format="csc")
            I = diags([np.ones(nx - 1)], [0], format="csc")
            dt = tau / nt
            s = np.zeros(nx - 1)
            s[-1] = a + b                                        # upper boundary value 1 feeds the last interior row
            v = np.zeros(nx - 1)
            quarter = splu((I - 0.25 * dt * A).tocsc())
            for _ in range(4):                                   # Rannacher: damp the corner discontinuity
                v = quarter.solve(v + 0.25 * dt * s)
            lu, rhs = splu((I - 0.5 * dt * A).tocsc()), (I + 0.5 * dt * A).tocsc()
            for _ in range(nt - 1):
                v = lu.solve(rhs @ v + dt * s)
            return float(np.interp(0.0, x[1:-1], v))

        for L, U, sigma, days, r, top, bottom in PDE_GOLDEN[:5]:
            m, tau = _m(sigma, r), sigma * sigma * days / 365
            e = exit_probs(L, U, m, tau)
            assert e["top"] == pytest.approx(pde_top(L, U, m, tau), abs=1.5e-3)
            assert e["bottom"] == pytest.approx(pde_top(U, L, -m, tau), abs=1.5e-3)

    def test_unlimited_time_collapses_to_the_closed_form(self):
        e = exit_probs(0.03, 0.06, 0.0, 50)                      # driftless: P(top) = L / (L + U)
        assert e["top"] == pytest.approx(0.03 / 0.09, abs=1e-9)
        assert e["bottom"] == pytest.approx(0.06 / 0.09, abs=1e-9)
        assert e["inside"] == pytest.approx(0, abs=1e-9)
        d = exit_probs(0.03, 0.06, 1.5, 50)                      # with drift: scale-function result
        assert d["top"] == pytest.approx((1 - math.exp(-2 * 1.5 * 0.03)) / (1 - math.exp(-2 * 1.5 * 0.09)), abs=1e-9)
        n = exit_probs(0.06, 0.03, -1.5, 50)                     # negative drift branch is the mirror image
        assert n["bottom"] == pytest.approx(d["top"], abs=1e-9)

    def test_degenerate_inputs_leave_everything_inside_never_nan(self):
        inside = {"top": 0.0, "bottom": 0.0, "inside": 1.0}
        for args in [(0.03, 0.06, 0.0, 0.0), (0.0, 0.06, 0.0, 0.01), (0.03, 0.06, math.nan, 0.01),
                     (0.03, 0.06, 0.0, 1e-12), (0.03, math.inf, 0.0, 0.01)]:
            assert exit_probs(*args) == inside

    def test_huge_drift_does_not_overflow(self):
        e = exit_probs(0.02, 0.05, 450.0, 1e-3)                  # sigma ~ 0.01 → m ~ 450
        assert all(math.isfinite(v) for v in e.values()) and sum(e.values()) == pytest.approx(1, abs=1e-9)


class TestTouchProb:
    def test_driftless_is_twice_the_terminal_tail(self):
        a, sg, T = 0.05, 0.3, 30 / 365
        assert touch_prob(a, 0, sg, T) == pytest.approx(2 * norm_cdf(-a / (sg * math.sqrt(T))), abs=1e-12)

    def test_drift_toward_the_level_makes_a_touch_likelier(self):
        a, sg, T = 0.05, 0.3, 30 / 365
        assert touch_prob(a, 0.4, sg, T) > touch_prob(a, 0, sg, T) > touch_prob(a, -0.4, sg, T)

    def test_degenerate(self):
        assert touch_prob(0.0, 0.0, 0.3, 0.1) == 1.0 and touch_prob(0.05, 0.0, 0.3, 0.0) == 0.0


class TestTradeOdds:
    @pytest.mark.parametrize("name,direction,spot,entry,stop,target,iv,days,fill,win,loss,inside", MC_GOLDEN)
    def test_matches_monte_carlo_golden(self, name, direction, spot, entry, stop, target, iv, days, fill, win, loss, inside):
        o = trade_odds(direction, spot, entry, stop, target, iv, days)
        assert o["ok"], name
        assert o["win"] == pytest.approx(win, abs=0.012), name        # same tolerances as the TS tests (MC noise)
        assert o["loss"] == pytest.approx(loss, abs=0.015), name
        assert o["inside"] == pytest.approx(inside, abs=0.015), name
        if fill < 0.999:
            assert o["fill_prob"] == pytest.approx(fill, abs=0.012), name
        else:
            assert o["fill_prob"] is None and o["fill_kind"] == "market", name
        assert o["win"] + o["loss"] + o["inside"] == pytest.approx(1, abs=1e-9), name

    @pytest.mark.parametrize("case", [
        ("long", 100, 99.7, 97, 106, 0.30, 20),           # entry 0.3% from spot: fill density spikes at t≈0
        ("long", 100, 99.84, 98, 104, 0.20, 5),
        ("short", 100, 100.4, 103, 94, 0.25, 15),
        ("long", 238.9, 233.67, 229.87, 259.31, 0.30, 30),
        ("long", 100, 103, 98, 112, 0.35, 20),            # breakout fill
        ("long", 100, 90, 85, 130, 0.50, 120),            # deep pullback, long window
        ("long", 100, 100.2, 99, 101, 1.20, 3),
    ])
    def test_fill_integral_matches_adaptive_quadrature(self, case):
        direction, spot, entry, stop, target, iv, days = case
        sign = 1 if direction == "long" else -1
        sigma, T, r = iv, days / 365, DEFAULT_RISK_FREE
        nu = (r - 0.5 * sigma * sigma) * sign
        m = nu / (sigma * sigma)
        up, down = math.log(target / entry) * sign, math.log(entry / stop) * sign
        gap = sign * math.log(entry / spot)
        a, toward = abs(gap), (-nu if gap < 0 else nu)
        ref = {}
        for key in ("top", "bottom", "inside"):
            f = lambda s, key=key: _touch_density(a, toward, sigma, s) * exit_probs(down, up, m, sigma * sigma * (T - s))[key]
            tc = a * a / (sigma * sigma)
            pts = [tc * x for x in (0.02, 0.1, 0.5, 2, 8) if tc * x < T]
            ref[key] = quad(f, 0, T, points=pts or None, limit=1000, epsabs=1e-13, epsrel=1e-12)[0]
        mass = sum(ref.values())
        o = trade_odds(*case[:5], iv, days)
        assert o["win"] == pytest.approx(ref["top"] / mass, abs=2e-5)
        assert o["loss"] == pytest.approx(ref["bottom"] / mass, abs=2e-5)
        assert o["inside"] == pytest.approx(ref["inside"] / mass, abs=2e-5)
        assert mass == pytest.approx(touch_prob(a, toward, sigma, T), abs=1e-9)   # the fill mass IS the closed form

    def test_fill_kind_follows_the_entry(self):
        k = lambda d, spot, entry, stop, target: trade_odds(d, spot, entry, stop, target, 0.3, 20)["fill_kind"]
        assert k("long", 100, 97, 95, 110) == "pullback" and k("long", 100, 103, 99, 112) == "breakout"
        assert k("short", 100, 103, 106, 92) == "pullback" and k("short", 100, 97, 101, 90) == "breakout"
        assert k("long", 100, 100.1, 97, 108) == "market"

    def test_the_plan_the_classic_engine_rated_best_is_not_a_positive_edge(self):
        """spot 238.90 / entry 233.67 / stop 229.87 / T1 259.31 / IV 30% / 30d — the reported regression."""
        o = trade_odds("long", 238.90, 233.67, 229.87, 259.31, 0.30, 30)
        assert o["ok"] and o["fill_kind"] == "pullback"
        assert o["fill_prob"] == pytest.approx(0.797, abs=0.005)
        # the plan is mostly stopped out: ~10% reach T1 first, ~82% stop out first, ~8-9% unresolved
        assert o["win"] == pytest.approx(0.0975, abs=0.005)
        assert o["loss"] == pytest.approx(0.8171, abs=0.005)
        assert o["inside"] == pytest.approx(0.0853, abs=0.005)
        # the terminal probability the classic card used — P(S_T >= T1) = N(d2) from SPOT — said 16.9%
        t = 30 / 365
        p_terminal = norm_cdf((math.log(238.90 / 259.31) + (DEFAULT_RISK_FREE - 0.5 * 0.30 ** 2) * t) / (0.30 * math.sqrt(t)))
        assert p_terminal == pytest.approx(0.169, abs=0.002)
        assert p_terminal > o["win"] + 0.05
        # break-even needs risk / (risk + reward) = 3.80 / 29.44 = 12.9% (1 / (1 + 6.75)); the plan sits BELOW it
        assert o["rr"] == pytest.approx(6.7474, abs=1e-3) and o["break_even"] == pytest.approx(3.80 / 29.44, abs=1e-12)
        assert o["resolved_win"] == pytest.approx(0.1066, abs=0.005) and o["resolved_win"] < o["break_even"]
        assert o["vs_break_even_pts"] < 0

    def test_market_implied_odds_are_an_exactly_fair_bet(self):
        """Price martingale (r=0), unlimited time: P(T1 first) = (entry-stop)/(target-stop) = the break-even rate."""
        for stop, target in [(95, 112), (90, 130), (98, 103)]:
            o = trade_odds("long", 100, 100, stop, target, 0.3, 40000, r=0.0)
            assert abs(o["vs_break_even_pts"]) < 0.05, (stop, target)
            assert o["resolved_win"] == pytest.approx((100 - stop) / (target - stop), abs=5e-4)
        s = trade_odds("short", 100, 100, 105, 88, 0.3, 40000, r=0.0)
        assert abs(s["vs_break_even_pts"]) < 0.05

    def test_no_resolution_in_the_window_invents_no_edge(self):
        o = trade_odds("long", 100, 100, 70, 160, 0.05, 1)
        assert o["resolved_win"] is None and o["vs_break_even_pts"] is None
        assert o["inside"] == pytest.approx(1, abs=1e-6)

    def test_unreachable_pullback_is_reported_not_hidden(self):
        o = trade_odds("long", 100, 60, 55, 90, 0.1, 5)           # a 40% pullback in 5 days at 10% vol
        assert o["ok"] and o["fill_prob"] < 1e-9

    def test_breakeven_and_rr_are_exact(self):
        o = trade_odds("long", 100, 100, 96, 112, 0.3, 30)
        assert o["rr"] == pytest.approx(3.0, abs=1e-12) and o["break_even"] == pytest.approx(0.25, abs=1e-12)

    def test_bad_inputs_are_refused_with_a_reason_never_guessed(self):
        base = dict(direction="long", spot=100, entry=100, stop=95, target=110, iv=0.3, days=30)
        refuse = lambda **kw: trade_odds(**{**base, **kw})
        assert refuse(stop=101) == {"ok": False, "reason": "bad-levels"}          # stop on the profit side
        assert refuse(target=99) == {"ok": False, "reason": "bad-levels"}
        assert refuse(direction="short") == {"ok": False, "reason": "bad-levels"}  # levels point the wrong way
        assert refuse(direction="flat") == {"ok": False, "reason": "bad-levels"}
        assert refuse(spot=math.nan) == {"ok": False, "reason": "bad-levels"}
        assert refuse(iv=None) == {"ok": False, "reason": "no-iv"}
        assert refuse(iv=0) == {"ok": False, "reason": "no-iv"}
        assert refuse(days=0) == {"ok": False, "reason": "no-window"}
        assert refuse(days=None) == {"ok": False, "reason": "no-window"}

    def test_numpy_scalars_are_accepted(self):
        o = trade_odds("long", np.float64(100), np.float64(100), np.float64(95), np.float64(110), np.float64(0.3), np.int64(30))
        assert o["ok"]

    def test_a_longer_window_never_lowers_the_chance_of_resolving(self):
        f = lambda d: (lambda o: o["win"] + o["loss"])(trade_odds("long", 100, 100, 95, 110, 0.3, d))
        assert f(5) < f(15) < f(45) < f(120)

    def test_outcomes_are_conditional_on_the_fill(self):
        """Given a fill the three outcomes are a distribution (sum to 1); ``fill_prob`` carries the rest."""
        o = trade_odds("long", 100, 96, 94, 108, 0.3, 30)
        assert o["win"] + o["loss"] + o["inside"] == pytest.approx(1, abs=1e-9)
        assert 0 < o["fill_prob"] < 1
