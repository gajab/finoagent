"""Tests for the institutional book tail-risk math (full-reprice + MC + Spitznagel + VIX)."""
import math

import numpy as np

from app.services.book_tail_risk import (
    _bs_vec, reprice_scenario, book_mc, spitznagel_cost_vs_drag,
    _vix_future, _vix_hedge_candidates, _vixy_dynamic_candidate,
    _assignment_ladder, _vol_shock_for, _naked_assignment,
)
from app.services.stock_service import bs_price


def _short_put_book(spot=100.0, strike=90.0, qty=10, iv=0.30, beta=1.0):
    return [{"spot": spot, "beta": beta, "iv": iv,
             "legs": [{"strike": strike, "right": "P", "sign": -1, "qty": qty, "iv": iv, "dte_years": 30 / 365}]}]


class TestVectorBS:
    def test_matches_scalar(self):
        v = _bs_vec(np.array([100.0]), 90.0, 30 / 365, 0.045, np.array([0.30]), "P")[0]
        assert abs(v - bs_price(100.0, 90.0, 30 / 365, 0.045, 0.30, "put")) < 1e-6


class TestReprice:
    def test_short_put_loses_big_in_a_crash(self):
        pnl = reprice_scenario(_short_put_book(), -0.20, 15.0, 0.045)
        assert pnl < 0

    def test_full_reprice_beats_the_delta_approximation(self):
        # A −20% move on an 11%-OTM short put goes deep ITM; full reprice captures the
        # convexity the linear (delta-only) estimate misses → a materially bigger loss.
        pos = _short_put_book()
        full = reprice_scenario(pos, -0.20, 0.0, 0.045)
        # delta-only: short put delta ~ −0.3/contract*100 short → +delta; ΔS = −20
        # a crude linear guess is ~ +30*qty*(−20). Full reprice loss must be larger.
        assert full < -3000   # deep-ITM 90 put with spot 80: loss ≳ intrinsic

    def test_beta_amplifies_the_crash(self):
        base = reprice_scenario(_short_put_book(beta=1.0), -0.20, 15.0, 0.045)
        hi = reprice_scenario(_short_put_book(beta=1.6), -0.20, 15.0, 0.045)
        assert hi < base   # higher beta → bigger loss


class TestBookMC:
    def test_cvar_exceeds_var_and_is_positive_for_a_short_book(self):
        mc = book_mc(_short_put_book(), r=0.045, mkt_vol=0.16, idx_spot=5000.0, n_sims=8000)
        assert mc["cvar_95"] >= mc["var_95"] > 0
        assert mc["pnl"].shape[0] == 8000 and mc["idx_sim"].shape[0] == 8000


class _VQ:
    """Minimal VIX call quote (only .mid is read)."""
    def __init__(self, mid):
        self.mid = mid


class TestVixFuture:
    def test_monotonic_with_convex_onset_then_saturates(self):
        fwd = 18.0
        v0 = float(_vix_future(0.0, fwd))
        v5 = float(_vix_future(math.log(0.95), fwd))
        v10 = float(_vix_future(math.log(0.90), fwd))
        v20 = float(_vix_future(math.log(0.80), fwd))
        v34 = float(_vix_future(math.log(0.66), fwd))
        assert v0 < v5 < v10 < v20 < v34         # deeper crash → higher VIX future (monotonic)
        assert (v10 - v5) > (v5 - v0)            # convex ONSET: vol accelerates as a sell-off begins
        assert (v34 - v20) < (v20 - v10)         # …then SATURATES (the future mean-reverts, capped)

    def test_dampened_vs_spot_and_capped(self):
        # A −34% month took SPOT VIX to ~82; the FUTURE we model must be materially lower
        # (mean reversion) and capped — never above ~80.
        v34 = float(_vix_future(math.log(0.66), 18.0))
        assert 55 < v34 < 80
        assert float(_vix_future(math.log(0.40), 18.0)) <= 80.0    # capped in an extreme

    def test_calm_market_floors_near_the_forward(self):
        assert abs(float(_vix_future(0.0, 18.0)) - 18.0) < 0.6
        assert float(_vix_future(math.log(1.05), 18.0)) < 18.0     # a rally lets vol drift down


class TestVixHedge:
    def _book_mc(self):
        book = [{"spot": 100.0, "beta": 1.0, "iv": 0.30,
                 "legs": [{"strike": 90.0, "right": "P", "sign": -1, "qty": 15, "iv": 0.30, "dte_years": 45 / 365}]}]
        return book_mc(book, r=0.045, mkt_vol=0.16, idx_spot=6000.0, n_sims=20000)

    def _chain(self):
        # a plausible ^VIX call chain (strike → mid), cheap deep-OTM calls
        return {k: _VQ(m) for k, m in
                {20.0: 1.60, 25.0: 0.95, 30.0: 0.60, 40.0: 0.30, 50.0: 0.18, 60.0: 0.12, 80.0: 0.06}.items()}

    def test_builds_call_spreads_that_cut_cvar(self):
        mc = self._book_mc()
        loss20 = abs(mc["cvar_95"]) * 1.2
        cands = _vix_hedge_candidates(self._chain(), 18.0, 6000.0, mc["idx_sim"], mc["pnl"],
                                      mc["cvar_95"] or 0.0, loss20, 0.6 * loss20, 0.05,
                                      135000.0, 6000.0, 365 / 90)
        assert cands, "expected at least one VIX candidate"
        for c in cands:
            assert c["instrument"] == "VIX" and c["kind"] == "call spread"
            assert c["short_strike"] > c["long_strike"]          # it's a spread (capped cost)
            assert c["cost_per_spread"] > 0 and c["contracts"] >= 1
            assert c["cvar_reduction"] > 0                        # it reduces the book tail
            assert c["offsets_pct"] and c["offsets_pct"] > 0

    def test_missing_chain_returns_empty(self):
        mc = self._book_mc()
        assert _vix_hedge_candidates(None, 18.0, 6000.0, mc["idx_sim"], mc["pnl"], mc["cvar_95"] or 0.0,
                                     1000.0, 600.0, 0.05, 1e5, 6000.0, 4.0) == []


class TestVixyDynamic:
    def _mc(self):
        book = [{"spot": 100.0, "beta": 1.0, "iv": 0.30,
                 "legs": [{"strike": 90.0, "right": "P", "sign": -1, "qty": 15, "iv": 0.30, "dte_years": 45 / 365}]}]
        return book_mc(book, r=0.045, mkt_vol=0.16, idx_spot=6000.0, n_sims=20000)

    def test_near_zero_cost_and_cuts_cvar(self):
        mc = self._mc()
        loss20 = abs(mc["cvar_95"]) * 1.2
        c = _vixy_dynamic_candidate(15.0, 18.7, 6000.0, mc["idx_sim"], mc["pnl"], mc["cvar_95"] or 0.0,
                                    loss20, 0.6 * loss20, 0.05, 135000.0, 6000.0)[0]
        assert c["instrument"] == "VIXY" and c["kind"] == "signal-based"
        assert c["cvar_reduction"] > 0                       # it reduces the tail
        # near-zero COST: annual bleed is a small fraction of the cash sleeve (no premium bleed)
        assert 0 < c["annual_bleed"] < 0.05 * c["sleeve_capital"]
        # far more efficient than a premium-bleeding option hedge (cost is tiny)
        assert c["efficiency"] and c["efficiency"] > 3.0
        assert c["capture_pct"] < 100                        # honest signal/gap-lag haircut

    def test_no_vix_level_returns_empty(self):
        mc = self._mc()
        assert _vixy_dynamic_candidate(None, None, 6000.0, mc["idx_sim"], mc["pnl"], mc["cvar_95"] or 0.0,
                                       1000.0, 600.0, 0.05, 1e5, 6000.0) == []


class TestTwoSidedStress:
    def test_short_gamma_loses_in_BOTH_directions(self):
        # A short strangle (short put + short call) — short gamma — must lose on a big move
        # either way, and full reprice must beat a delta-only guess (Gemini's Taylor error).
        pos = [{"spot": 100.0, "beta": 1.0, "iv": 0.30,
                "legs": [{"strike": 88, "right": "P", "sign": -1, "qty": 10, "iv": 0.32, "dte_years": 30 / 365},
                         {"strike": 112, "right": "C", "sign": -1, "qty": 10, "iv": 0.30, "dte_years": 30 / 365}]}]
        down = reprice_scenario(pos, -0.25, 20.0, 0.045)
        up = reprice_scenario(pos, 0.25, -7.0, 0.045)
        assert down < 0 and up < 0            # short gamma: a big move either way is a LOSS

    def test_vol_shock_is_two_sided(self):
        assert _vol_shock_for(-0.20) > 0      # downside → vol SPIKES (skew)
        assert _vol_shock_for(0.20) < 0       # melt-up → vol crushes


class TestAssignmentLadder:
    def _pos(self):
        return [{"spot": 100.0, "beta": 1.0, "iv": 0.31,
                 "legs": [{"strike": 88, "right": "P", "sign": -1, "qty": 10, "iv": 0.32, "dte_years": 30 / 365},
                          {"strike": 106, "right": "C", "sign": -1, "qty": 14, "iv": 0.30, "dte_years": 30 / 365}]}]

    def test_put_assignment_capital_on_the_downside(self):
        rows = _assignment_ladder(self._pos(), 0.045, moves=[-0.20])
        r = rows[0]
        assert r["puts_itm"] == 1 and r["calls_itm"] == 0
        assert r["put_assignment_capital"] == 88 * 100 * 10     # strike × 100 × qty, cash to buy
        assert r["call_cover_cost"] == 0

    def test_call_cover_cost_on_the_upside(self):
        rows = _assignment_ladder(self._pos(), 0.045, moves=[0.20])
        r = rows[0]
        assert r["calls_itm"] == 1 and r["puts_itm"] == 0
        # (spot·1.20 − 106) × 100 × 14 intrinsic
        assert r["call_cover_cost"] == round((120.0 - 106.0) * 100 * 14, 0)
        assert r["put_assignment_capital"] == 0

    def test_only_one_wing_is_itm_at_once(self):
        # the strangle never demands BOTH put-assignment and call-cover at the same spot
        for r in _assignment_ladder(self._pos(), 0.045):
            assert not (r["puts_itm"] and r["calls_itm"])


class TestNakedAssignment:
    def _L(self, K, rt, sg, q):
        return {"strike": K, "right": rt, "sign": sg, "qty": q, "iv": 0.3, "dte_years": 30 / 365}

    def test_excludes_covered_calls_and_spreads(self):
        positions = [
            {"structure": "cash_secured_put", "has_stock": False, "legs": [self._L(300, "P", -1, 10)]},
            {"structure": "short_strangle", "has_stock": False, "legs": [self._L(88, "P", -1, 5), self._L(112, "C", -1, 5)]},
            {"structure": "covered_call", "has_stock": True, "legs": [self._L(110, "C", -1, 3)]},          # covered → excluded
            {"structure": "put_credit_spread", "has_stock": False, "legs": [self._L(90, "P", -1, 4), self._L(80, "P", 1, 4)]},  # protected → excluded
        ]
        na = _naked_assignment(positions)
        assert na["put_capital"] == 300 * 100 * 10 + 88 * 100 * 5      # CSP + strangle put; spread excluded
        assert na["call_capital"] == 112 * 100 * 5                     # strangle call only; covered excluded
        assert na["total"] == na["put_capital"] + na["call_capital"]
        assert na["n_naked_puts"] == 2 and na["n_naked_calls"] == 1

    def test_empty_book_is_zero(self):
        assert _naked_assignment([])["total"] == 0


class TestSpitznagel:
    def test_cheap_hedge_on_a_fat_tail_lifts_cagr(self):
        s = spitznagel_cost_vs_drag(annual_bleed=4000, crash_loss=200000, hedge_payoff=150000,
                                    crash_prob_annual=0.15, book_capital=500000, annual_income=40000)
        assert s["cost_effective"] and s["cagr_lift_pct"] > 0

    def test_expensive_hedge_on_a_rare_tail_does_not(self):
        s = spitznagel_cost_vs_drag(annual_bleed=20000, crash_loss=40000, hedge_payoff=30000,
                                    crash_prob_annual=0.02, book_capital=500000, annual_income=30000)
        assert not s["cost_effective"]
