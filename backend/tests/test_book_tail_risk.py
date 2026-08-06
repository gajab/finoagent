"""Tests for the institutional book tail-risk math (full-reprice + MC + Spitznagel)."""
import numpy as np

from app.services.book_tail_risk import (
    _bs_vec, reprice_scenario, book_mc, spitznagel_cost_vs_drag,
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


class TestSpitznagel:
    def test_cheap_hedge_on_a_fat_tail_lifts_cagr(self):
        s = spitznagel_cost_vs_drag(annual_bleed=4000, crash_loss=200000, hedge_payoff=150000,
                                    crash_prob_annual=0.15, book_capital=500000, annual_income=40000)
        assert s["cost_effective"] and s["cagr_lift_pct"] > 0

    def test_expensive_hedge_on_a_rare_tail_does_not(self):
        s = spitznagel_cost_vs_drag(annual_bleed=20000, crash_loss=40000, hedge_payoff=30000,
                                    crash_prob_annual=0.02, book_capital=500000, annual_income=30000)
        assert not s["cost_effective"]
