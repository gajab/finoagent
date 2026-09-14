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


class TestOptionOverlayOnly:
    """Manage Book is OPTION-income management: the long shares behind a covered call are a
    separate core holding, NOT counted in the crash/CVaR P&L (stock only marks the call covered +
    sizes assignment). Default reprice is options-only; a covered call therefore PROFITS in a crash."""
    def _covered(self):   # long 100 sh + short 1 OTM call, spot 100
        return [{"ticker": "AAA", "spot": 100.0, "beta": 1.0, "iv": 0.30, "shares": 100,
                 "legs": [{"strike": 110, "right": "C", "sign": -1, "qty": 1, "iv": 0.30, "dte_years": 45 / 365}]}]

    def test_default_ignores_the_shares(self):
        b = self._covered()
        no_sh = [{**b[0], "shares": 0}]
        assert reprice_scenario(b, -0.30, 20, 0.045) == reprice_scenario(no_sh, -0.30, 20, 0.045)

    def test_covered_call_overlay_profits_in_a_crash(self):
        b = self._covered()
        # short call decays to zero in a selloff → the overlay is a GAIN either way down.
        assert reprice_scenario(b, -0.10, 8, 0.045) > 0 and reprice_scenario(b, -0.50, 45, 0.045) > 0

    def test_include_stock_true_is_opt_in(self):
        b = self._covered()
        assert reprice_scenario(b, -0.30, 20, 0.045, include_stock=True) < reprice_scenario(b, -0.30, 20, 0.045)


class TestLossByNameAndGrid:
    def _book(self):
        return [
            {"ticker": "AAA", "spot": 100.0, "beta": 1.1, "iv": 0.30, "shares": 100,
             "legs": [{"strike": 110, "right": "C", "sign": -1, "qty": 1, "iv": 0.30, "dte_years": 45 / 365}]},
            {"ticker": "CCC", "spot": 80.0, "beta": 0.9, "iv": 0.28, "shares": 0,
             "legs": [{"strike": 72, "right": "P", "sign": -1, "qty": 3, "iv": 0.28, "dte_years": 45 / 365}]},
        ]

    def test_loss_by_name_is_options_only_and_sorted_worst_first(self):
        from app.services.book_tail_risk import _loss_by_name
        rows = _loss_by_name(self._book(), 0.045, -0.20, 15.0)
        assert [r["pnl"] for r in rows] == sorted(r["pnl"] for r in rows)   # worst (most negative) first
        assert all("stock_pnl" not in r for r in rows)                      # no stock split — options only
        aaa = next(r for r in rows if r["ticker"] == "AAA")
        ccc = next(r for r in rows if r["ticker"] == "CCC")
        assert aaa["pnl"] > 0 and ccc["pnl"] < 0    # covered call gains on the downside; the CSP loses

    def test_scenario_grid_shape_and_short_gamma_valley(self):
        from app.services.book_tail_risk import _scenario_grid, _GRID_SPOT, _GRID_VOL
        g = _scenario_grid(self._book(), 0.045, 100000.0)
        assert len(g["rows"]) == len(_GRID_SPOT) and all(len(row["cells"]) == len(_GRID_VOL) for row in g["rows"])
        vi0, vhi = _GRID_VOL.index(0.0), _GRID_VOL.index(30.0)
        flat = {row["move_pct"]: row["cells"] for row in g["rows"]}
        # a −20% month loses at flat vol; and rising vol adds loss (short vega) at that spot.
        assert flat[-20.0][vi0]["pnl"] < 0
        assert flat[-20.0][vhi]["pnl"] < flat[-20.0][vi0]["pnl"]


class TestPositionRemediation:
    """Fixes must be POSITION-SPECIFIC with REAL legs, ranked by risk-vs-remaining-premium."""
    EXP = "2026-10-17"
    CS = [100, 105, 110, 115, 120, 125, 130, 140, 150, 160, 175, 200]
    PS = [60, 70, 80, 85, 90, 95, 100, 105, 110]

    def _naked_call(self):
        return {"ticker": "NVDA", "name": "NVDA", "spot": 115.0, "beta": 1.4, "shares": 0, "capital": 240000,
                "structure": "naked_call", "strikes_by_exp": {self.EXP: {"C": self.CS, "P": self.PS}},
                "legs": [{"strike": 120, "right": "C", "sign": -1, "qty": 5, "iv": 0.45, "dte_years": 45/365, "exp": self.EXP, "dte_days": 45}]}

    def test_targets_use_real_listed_strikes(self):
        from app.services.book_tail_risk import _offender_targets
        t = _offender_targets([self._naked_call()], "up", 0.045, top=1)[0]
        cap = t["recommended"] if t["recommended"]["action"] == "cap" else t["alt"]
        assert cap and "130C" in cap["legs"]            # 130 is a REAL listed strike beyond the short 120
        assert cap["cost"] > 0 and cap["tail_after"] > t["tail_before"]   # caps the loss

    def test_cap_preferred_over_close_when_it_keeps_premium_cheaper(self):
        from app.services.book_tail_risk import _offender_targets
        t = _offender_targets([self._naked_call()], "up", 0.045, top=1)[0]
        assert t["recommended"]["action"] == "cap"      # more loss-cut per $ AND keeps premium
        assert t["recommended"]["premium_kept"] > 0

    def test_why_cites_risk_and_remaining_premium(self):
        from app.services.book_tail_risk import _offender_targets
        t = _offender_targets([self._naked_call()], "up", 0.045, top=1)[0]
        assert "squeeze loss" in t["why"] and "premium" in t["why"]
        assert t["risk"] < 0

    def test_no_offenders_on_the_safe_side(self):
        from app.services.book_tail_risk import _offender_targets
        # a naked short CALL has no DOWNSIDE tail worth flagging
        assert _offender_targets([self._naked_call()], "down", 0.045) == []


class TestRiskScorecard:
    """Standardized institutional guardrails: value vs limit, status, and a quantified fix per breach."""
    def _sc(self, **over):
        from app.services.book_tail_risk import _risk_scorecard
        base = dict(
            positions=[], r=0.045,
            book_capital=800000, annual_income=180000,
            scenarios=[{"label": "GFC −50%", "move_pct": -0.50, "pnl": -120000},
                       {"label": "−20%", "move_pct": -0.20, "pnl": -40000},
                       {"label": "Squeeze +35%", "move_pct": 0.35, "pnl": -30000}],
            cvar_95=40000, var_95=24000, beta_delta_notional=-40000, beta_delta_spy=-70, spy_price=560,
            concentration=[], factor_exposure={"by_name": [{"ticker": "NVDA", "capital": 240000}],
                                               "clusters": [], "sectors": []},
            naked_assignment={"total": 700000},
            hedge_menu=[{"label": "SPX ps", "instrument": "SPX", "long_strike": 5400, "short_strike": 4800,
                         "contracts": 3, "annual_bleed": 9000, "cvar_reduction": 30000, "crash_payoff_20": 25000}])
        base.update(over)
        return _risk_scorecard(**base)

    def test_extreme_tail_breach_gets_a_hedge_fix(self):
        sc = self._sc()   # GFC −120k = 15% of 800k > 10% breach
        et = next(c for c in sc["checks"] if c["key"] == "extreme_tail")
        assert et["status"] == "breach" and et.get("fix") and "SPX" in et["fix"]["headline"]

    def test_cvar_guardrail(self):
        et = next(c for c in self._sc()["checks"] if c["key"] == "cvar")
        assert et["status"] == "breach"          # 40k/800k = 5% > 3%
        assert et["fix"]["headline"].startswith("Add")

    def test_single_name_concentration_targets_the_name(self):
        c = next(x for x in self._sc()["checks"] if x["key"] == "name_conc")
        assert c["status"] == "breach"           # NVDA 240k/800k = 30% > 25%
        assert "NVDA" in c["fix"]["headline"]
        assert "targets" in c["fix"]             # position-specific fixes attached (empty when positions=[])

    def test_symmetry_pass_when_balanced(self):
        sc = self._sc(scenarios=[{"label": "−20%", "move_pct": -0.20, "pnl": -40000},
                                 {"label": "+20%", "move_pct": 0.20, "pnl": -38000}])
        sym = next(c for c in sc["checks"] if c["key"] == "symmetry")
        assert sym["status"] == "pass"

    def test_grade_and_sort_breaches_first(self):
        sc = self._sc()
        assert sc["grade"] == "At risk" and sc["n_breach"] >= 1
        statuses = [c["status"] for c in sc["checks"]]
        assert statuses == sorted(statuses, key=lambda s: {"breach": 0, "warn": 1, "pass": 2}[s])

    def test_clean_book_is_sound(self):
        sc = self._sc(scenarios=[{"label": "−20%", "move_pct": -0.20, "pnl": -20000},
                                 {"label": "+20%", "move_pct": 0.20, "pnl": -18000}],
                      cvar_95=12000, var_95=9000, beta_delta_notional=-20000,
                      factor_exposure={"by_name": [{"ticker": "A", "capital": 100000}, {"ticker": "B", "capital": 100000}],
                                       "clusters": [], "sectors": []})
        assert sc["grade"] == "Sound" and sc["n_breach"] == 0


class TestTwoSidedVerdict:
    """The verdict must be set by the WORSE tail (down OR up) — a melt-up-dominant short-call book
    was wrongly called 'Contained' because only the −20% downside was scored."""
    def _v(self, scen, **kw):
        from app.services.book_tail_risk import _verdict_and_actions
        base = dict(short_vol=True, concentration=[], hedge_menu=[], cvar_pct=None, beta_delta_spy=0)
        base.update(kw)
        return _verdict_and_actions(scenarios=scen, **base)

    def test_meltup_dominant_raises_level_and_names_upside(self):
        scen = [{"label": "−20%", "move_pct": -0.20, "pct_of_capital": -4.0},
                {"label": "Squeeze +35%", "move_pct": 0.35, "pct_of_capital": -13.6}]
        v = self._v(scen)
        assert v["level"] == "Moderate"                       # 13.6% up-tail, NOT Contained off the 4% down
        assert "upside" in v["summary"].lower() or "melt-up" in v["summary"].lower()
        assert any("MELT-UP" in a or "call SPREAD" in a for a in v["actions"])

    def test_downside_dominant_unchanged(self):
        scen = [{"label": "−20%", "move_pct": -0.20, "pct_of_capital": -25.0},
                {"label": "GFC −50%", "move_pct": -0.50, "pct_of_capital": -48.0},
                {"label": "Squeeze +35%", "move_pct": 0.35, "pct_of_capital": -3.0}]
        v = self._v(scen)
        assert v["level"] == "Dangerous"
        assert "−20%" in v["summary"]

    def test_well_sized_both_sides_is_contained(self):
        scen = [{"label": "−20%", "move_pct": -0.20, "pct_of_capital": -3.0},
                {"label": "Squeeze +35%", "move_pct": 0.35, "pct_of_capital": -2.0}]
        assert self._v(scen)["level"] == "Contained"


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
