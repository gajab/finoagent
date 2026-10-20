"""Concentration Ladder: lot allocation (tax-first), collar structure pick, tax math reconciliation, timing gates."""
import math
from datetime import date

import numpy as np
import pytest

import app.services.concentration_ladder_service as cl
from app.services.concentration_ladder_service import (
    CONTRACT, allocate_lots, split_lots, pick_structure, lognormal_density, rung_economics, _rung_after_tax,
    timing_assessment, build_plan, constructive_sale_flag, unhedged_liq,
)
from app.services.hedging_service import _bs_greeks  # noqa: F401  (import sanity)
from app.services.quote_providers import OptionQuote
from app.services.stock_service import bs_price

R, ST, LT = 0.045, 0.408, 0.238
SPOT = 100.0


def _q(strike, right, bid, ask, iv=0.3, oi=500):
    return OptionQuote(strike=strike, right=right, expiration="2027-10-15", bid=bid, ask=ask, last=(bid + ask) / 2,
                       mid=(bid + ask) / 2, iv=iv, oi=oi, volume=50)


def _chain(T=1.0, sigma=0.30, spot=SPOT):
    """Synthetic BS chain (flat vol) with a 2% half-spread — strikes 50..200 step 5."""
    calls, puts = {}, {}
    for k in range(50, 205, 5):
        c, p = bs_price(spot, k, T, R, sigma, "call"), bs_price(spot, k, T, R, sigma, "put")
        calls[float(k)] = _q(k, "C", round(c * 0.98, 2), round(c * 1.02 + 0.01, 2), sigma)
        puts[float(k)] = _q(k, "P", round(p * 0.98, 2), round(p * 1.02 + 0.01, 2), sigma)
    return calls, puts


class TestLots:
    def test_underwater_lots_are_set_aside_and_gain_lots_sorted_highest_basis_first(self):
        sp = split_lots([{"shares": 100, "cost_basis": 20}, {"shares": 300, "cost_basis": 60},
                         {"shares": 200, "cost_basis": 130}], SPOT)
        assert [l["basis"] for l in sp["gain_lots"]] == [60, 20]
        assert sp["underwater_lots"][0]["basis"] == 130
        assert sp["unrealized_gain"] == pytest.approx(300 * 40 + 100 * 80)
        assert sp["underwater_loss"] == pytest.approx(-6000)

    def test_junk_lots_are_dropped(self):
        assert split_lots([{"shares": 0, "cost_basis": 10}, {"shares": 5, "cost_basis": -1}], SPOT)["all_shares"] == 0


class TestAllocation:
    def _rungs(self, years=(2027, 2027, 2028, 2028), kc=130.0, net=0.0):
        return [{"kc": kc, "net": net, "year": y, "alloc": []} for y in years]

    def test_highest_basis_lots_are_called_away_first(self):
        lots = split_lots([{"shares": 400, "cost_basis": 20}, {"shares": 400, "cost_basis": 80}], SPOT)["gain_lots"]
        rungs = self._rungs()
        allocate_lots(rungs, lots, covered_shares=800, mode="deferral")
        assert [a["basis"] for a in rungs[0]["alloc"]] == [80]            # first rung takes the 80-basis lot
        assert [a["basis"] for a in rungs[-1]["alloc"]] == [20]           # the cheapest basis lands last
        assert sum(a["shares"] for r in rungs for a in r["alloc"]) == 800

    def test_whole_contracts_only_and_remainder_goes_to_earliest_rungs(self):
        lots = split_lots([{"shares": 1000, "cost_basis": 50}], SPOT)["gain_lots"]
        rungs = self._rungs(years=(2027, 2028, 2029))
        allocate_lots(rungs, lots, covered_shares=1000, mode="deferral")
        sizes = [sum(a["shares"] for a in r["alloc"]) for r in rungs]
        assert sizes == [400, 300, 300] and all(s % CONTRACT == 0 for s in sizes)

    def test_budget_mode_caps_each_years_worst_case_gain(self):
        lots = split_lots([{"shares": 2000, "cost_basis": 50}], SPOT)["gain_lots"]
        rungs = self._rungs(years=(2027, 2027, 2028, 2028), kc=130.0)       # 80/sh worst-case gain
        used: dict = {}
        left = allocate_lots(rungs, lots, covered_shares=2000, mode="budget", budget_by_year={"*": 80_000.0},
                             used_by_year=used)
        assert used[2027] <= 80_000 + 1e-6 and used[2028] <= 80_000 + 1e-6
        placed = sum(a["shares"] for r in rungs for a in r["alloc"])
        assert placed == 2000 and not left                                   # 1000/yr × $80 = $80k each: fits exactly

    def test_budget_leftover_is_reported_when_budget_too_small(self):
        lots = split_lots([{"shares": 2000, "cost_basis": 50}], SPOT)["gain_lots"]
        rungs = self._rungs(years=(2027, 2028), kc=130.0)
        left = allocate_lots(rungs, lots, covered_shares=2000, mode="budget", budget_by_year={"*": 40_000.0},
                             used_by_year={})
        assert sum(l["shares"] for l in left) == 2000 - 500 - 500


class TestStructure:
    def test_zero_cost_cap_is_funded_at_executable_prices(self):
        calls, puts = _chain()
        s = pick_structure(calls, puts, SPOT, 15.0)
        assert s["zero_cost"] and s["call"].bid >= s["put"].ask
        # the next strike up is NOT zero-cost → the chosen cap is the highest one that funds the put
        higher = [k for k in sorted(calls) if k > s["kc"] and calls[k].bid > 0]
        if higher:
            assert calls[higher[0]].bid < s["put"].ask
        assert s["kp"] <= SPOT * 0.85 and s["band_pct"] >= cl.MIN_BAND_PCT

    def test_band_guard_deepens_the_floor_instead_of_a_constructive_sale(self):
        calls, puts = _chain(sigma=0.12)                     # very low vol → zero-cost cap is tight at a −5% floor
        s = pick_structure(calls, puts, SPOT, 5.0)
        assert s["floor_deepened"] or s["band_pct"] >= cl.MIN_BAND_PCT
        assert constructive_sale_flag(8.0)["risk"] == "high"
        assert constructive_sale_flag(20.0)["risk"] == "elevated"
        assert constructive_sale_flag(30.0)["risk"] == "low"

    def test_no_liquid_options_returns_none(self):
        assert pick_structure({}, {}, SPOT, 15.0) is None


def _econ(dens, *, kp=85.0, kc=125.0, c0=6.0, p0=6.0, b=40.0, n=100, defer=True, tenor=420):
    return rung_economics(dens, kp=kp, kc=kc, c0=c0, p0=p0, b=b, n=n, st=ST, lt=LT, tenor_days=tenor,
                          defer_put_loss=defer, spot=SPOT, r=R)


class TestTaxMath:
    def test_called_branch_gain_and_tax_exact(self):
        o = _rung_after_tax(np.array([140.0]), kp=85, kc=125, c0=6, p0=5, b=40, st=ST, lt=LT, put_rate=LT, defer_put_loss=True)
        assert o["gain"][0] == pytest.approx(125 - 40 + 6 - 5)
        assert o["tax"][0] == pytest.approx(LT * (125 - 40 + 6 - 5))
        # liquidation value when called = proceeds + option cash − tax (stock already sold)
        assert o["liq"][0] == pytest.approx(125 + 1 - LT * 86)

    def test_unassigned_premium_is_short_term_and_put_cost_is_deferred_not_deducted(self):
        o = _rung_after_tax(np.array([100.0]), kp=85, kc=125, c0=6, p0=6, b=40, st=ST, lt=LT, put_rate=LT, defer_put_loss=True)
        assert o["tax"][0] == pytest.approx(ST * 6)                          # taxed on the call premium; NO offset for the put
        # …but the put cost is capitalised: liquidating later uses basis 40 + 6
        assert o["liq"][0] == pytest.approx(100 + 0 - ST * 6 - LT * (100 - 46))
        flat = _rung_after_tax(np.array([100.0]), kp=85, kc=125, c0=6, p0=6, b=40, st=ST, lt=LT, put_rate=LT, defer_put_loss=False)
        assert flat["tax"][0] == pytest.approx(ST * 6 + LT * (0 - 6))        # without deferral the put loss would offset

    def test_put_gain_recognised_only_beyond_its_premium(self):
        kw = dict(kp=85, kc=125, c0=6, p0=6, b=40, st=ST, lt=LT, put_rate=LT, defer_put_loss=True)
        deep = _rung_after_tax(np.array([60.0]), **kw)                       # payoff 25, net +19
        assert deep["tax"][0] == pytest.approx(ST * 6 + LT * 19)
        shallow = _rung_after_tax(np.array([82.0]), **kw)                    # payoff 3 < premium 6 → loss deferred
        assert shallow["tax"][0] == pytest.approx(ST * 6)

    def test_expected_tax_equals_numeric_integral_of_the_scenario_function(self):
        d = lognormal_density(SPOT, 0.30, 420 / 365.0, R)
        e = _econ(d, n=1)
        kw = e["kw"]
        o = _rung_after_tax(d.K, **kw)
        p_c = d.prob_above(125.0)
        below = d.K < 125.0
        num = p_c * float(_rung_after_tax(np.array([125.0]), **kw)["tax"][0]) + \
            float(np.trapezoid(np.where(below, o["tax"] * d.pdf, 0), d.K)) * (1 - p_c) / float(np.trapezoid(np.where(below, d.pdf, 0), d.K))
        assert e["expected_tax"] == pytest.approx(num, rel=1e-9)

    def test_probabilities_sum_and_are_sane(self):
        d = lognormal_density(SPOT, 0.30, 1.0, R)
        assert d.prob_below(1e-3) < 1e-6 and d.prob_below(1e6) == pytest.approx(1.0, abs=1e-6)
        e = _econ(d)
        assert 0 < e["p_called"] < 0.5 and 0 < e["p_floor"] < 0.5

    def test_worst_case_equals_called_branch_times_shares(self):
        e = _econ(lognormal_density(SPOT, 0.30, 1.0, R), n=300)
        assert e["worst_gain"] == pytest.approx((125 - 40 + 6 - 6) * 300)
        assert e["worst_tax"] == pytest.approx(LT * e["worst_gain"])

    def test_zero_cost_collar_beats_holding_in_a_crash_and_lags_in_a_melt_up(self):
        e = _econ(lognormal_density(SPOT, 0.30, 1.0, R), n=1)
        S = np.array([50.0, 100.0, 200.0])
        liq = _rung_after_tax(S, **e["kw"])["liq"]
        un = unhedged_liq(S, 40.0, LT)
        assert liq[0] > un[0] and liq[2] < un[2]


class TestTiming:
    def _kw(self, **o):
        base = dict(spot=100, gain_pct=60.0, embedded_tax=50_000, headline={"cap_pct": 24.0, "expiry": "2027-10-15"},
                    vol={"skew_pts": 3.0, "iv_hv_ratio": 1.2}, trend={"ext_200": 0.22, "rsi14": 66, "off_high_pct": 2.0},
                    earnings_days=60, liq_spread=3.0, has_gain_lots=True, floor_pct=15.0)
        base.update(o)
        return base

    def test_great_conditions_put_on_now(self):
        t = timing_assessment(**self._kw())
        assert t["verdict"] == "PUT_ON_NOW" and t["score"] >= cl.VERDICT_NOW

    def test_poor_conditions_wait_with_triggers(self):
        t = timing_assessment(**self._kw(headline={"cap_pct": 7.0, "expiry": "x"}, vol={"skew_pts": 14, "iv_hv_ratio": 0.8},
                                        trend={"ext_200": -0.2, "rsi14": 30, "off_high_pct": 35}, earnings_days=3,
                                        liq_spread=14))
        assert t["verdict"] == "WAIT" and t["triggers"]

    def test_hard_gates(self):
        assert timing_assessment(**self._kw(has_gain_lots=False))["verdict"] == "NOT_APPLICABLE"
        assert timing_assessment(**self._kw(gain_pct=4.0, embedded_tax=300))["verdict"] == "JUST_SELL"
        assert timing_assessment(**self._kw(headline=None))["verdict"] == "WAIT"

    def test_weights_sum_to_100(self):
        assert cl.W_COLLAR + cl.W_SKEW + cl.W_VRP + cl.W_TREND + cl.W_EVENT + cl.W_LIQ == 100


def _market(ticker="XYZ", dtes=((420, "2027-12-17"), (780, "2028-12-15"))):
    rungs = []
    for dte, exp in dtes:
        calls, puts = _chain(T=dte / 365.0)
        st = pick_structure(calls, puts, SPOT, 15.0)
        rungs.append({"expiry": exp, "dte": dte, "year": int(exp[:4]), "dens": lognormal_density(SPOT, 0.30, dte / 365.0, R),
                      "struct": st, "atm_iv": 0.30, "skew_pts": 4.0})
    return {"ticker": ticker, "spot": SPOT, "rungs": rungs, "iv": {"atm_iv": 0.30, "skew_pts": 4.0},
            "ctx": {"hv30": 0.25, "next_earnings": None},
            "trend": {"ext_200": 0.2, "rsi14": 62, "off_high_pct": 3.0, "ext_50": 0.05, "ret_3m_pct": 10}}


class TestBuildPlan:
    TAX = {"st_rate_pct": 40.8, "lt_rate_pct": 23.8}

    def _plan(self, **kw):
        pos = [{"ticker": "XYZ", "lots": [{"shares": 2000, "cost_basis": 30}, {"shares": 1000, "cost_basis": 70},
                                          {"shares": 500, "cost_basis": 140}]}]
        args = dict(tax=self.TAX, mode="deferral", budget=None, defer_put_loss=True, r=R, today=date(2026, 10, 13),
                    floor_pct=15.0, coverage_pct=100.0)
        args.update(kw)
        return build_plan([_market()], pos, **args)

    def test_rungs_year_totals_and_underwater_lots_reconcile(self):
        p = self._plan()
        pos = p["positions"][0]
        assert sum(r["shares"] for r in pos["ladder"]) == pos["summary"]["covered_shares"] == 3000
        assert pos["summary"]["underwater_shares"] == 500 and any("LOSS" in n for n in pos["notes"])
        # per-rung → per-year → exact sums
        assert sum(y["worst_case_tax"] for y in p["by_year"]) == pytest.approx(sum(r["worst_case_tax"] for r in pos["ladder"]), abs=2)
        assert sum(y["expected_tax"] for y in p["by_year"]) == pytest.approx(sum(r["expected_tax"] for r in pos["ladder"]), abs=2)

    def test_worst_case_gain_is_called_branch_exactly(self):
        pos = self._plan()["positions"][0]
        for r in pos["ladder"]:
            per_share = r["call"]["strike"] - r["avg_basis"] + r["call"]["bid"] - r["put"]["ask"]
            assert r["worst_case_gain"] == pytest.approx(per_share * r["shares"], rel=1e-4)

    def test_budget_mode_respects_the_annual_budget(self):
        p = self._plan(mode="budget", budget=100_000.0)
        for y in p["by_year"]:
            assert y["worst_case_gain"] <= 100_000 + 1
            assert y["within_budget"] is True
        assert any("not placed" in n for n in p["positions"][0]["notes"])

    def test_scenario_table_matches_unhedged_baseline(self):
        r = self._plan()["positions"][0]["ladder"][0]
        row0 = next(s for s in r["scenarios"] if s["move_pct"] == 0)
        S = row0["price"]
        assert row0["unhedged_after_tax"] == pytest.approx(unhedged_liq(np.array([S]), r["avg_basis"], LT)[0] * r["shares"], abs=2)
        crash = next(s for s in r["scenarios"] if s["move_pct"] == -40)
        assert crash["vs_unhedged"] > 0                                       # the floor pays in a crash

    def test_deferral_value_is_exact_closed_form(self):
        p = self._plan()
        pos = p["positions"][0]
        expect = sum(LT * (SPOT - r["avg_basis"]) * r["shares"] * (1 - math.exp(-R * r["dte"] / 365.0)) for r in pos["ladder"])
        assert p["deferral"]["pv_value_of_deferral"] == pytest.approx(expect, abs=3)

    def test_error_market_is_passed_through(self):
        out = build_plan([{"ticker": "BAD", "error": "no options"}], [{"ticker": "BAD", "lots": []}], tax=self.TAX,
                         mode="deferral", budget=None, defer_put_loss=True, r=R, today=date(2026, 10, 13),
                         floor_pct=15.0, coverage_pct=100.0)
        assert out["positions"][0]["error"] == "no options"

    def test_december_expiry_gets_a_tax_year_note(self):
        flags = [f for r in self._plan()["positions"][0]["ladder"] for f in r["flags"]]
        assert any("January expiry" in f for f in flags)


class TestFlex:
    def test_dates_snap_to_january_and_are_distinct_future_weekdays(self):
        ds = cl.flex_dates(date(2026, 10, 13), 4, 5 * 365)
        assert len(ds) == len({d for d, _ in ds}) and all(dte >= cl.MIN_RUNG_DTE for _, dte in ds)
        assert all(date.fromisoformat(d).weekday() < 5 for d, _ in ds)
        assert any(d.endswith("-01-15") or d[5:7] == "01" for d, _ in ds)

    def test_model_collar_is_zero_cost_with_a_solved_cap(self):
        iv = lambda k: 0.30
        s = cl.pick_structure_model(SPOT, 2.0, R, iv, 15.0, 1.5, "2028-10-13")
        assert s["zero_cost"] and s["call"].bid >= s["put"].ask - 1e-9
        assert s["kp"] == 85.0 and s["band_pct"] >= cl.MIN_BAND_PCT and s["model"]
        # one dollar higher cap would NOT fund the put (the cap is the highest whole-dollar zero-cost strike)
        nxt = bs_price(SPOT, s["kc"] + 1, 2.0, R, 0.285, "call")
        assert nxt < s["put"].ask + 0.02

    def test_dealer_markup_lowers_the_cap(self):
        iv = lambda k: 0.30
        a = cl.pick_structure_model(SPOT, 2.0, R, iv, 15.0, 0.0, "x")["kc"]
        b = cl.pick_structure_model(SPOT, 2.0, R, iv, 15.0, 3.0, "x")["kc"]
        assert b < a

    def test_flex_plan_carries_rfq_and_skips_listed_oi_flags(self):
        iv = lambda k: 0.30
        rungs = []
        for exp in ("2028-01-14", "2029-01-15"):
            dte = (date.fromisoformat(exp) - date(2026, 10, 13)).days
            rungs.append({"expiry": exp, "dte": dte, "year": int(exp[:4]), "dens": lognormal_density(SPOT, 0.30, dte / 365, R),
                          "struct": cl.pick_structure_model(SPOT, dte / 365, R, iv, 15.0, 1.5, exp), "atm_iv": 0.3, "skew_pts": 3.0})
        m = {"ticker": "XYZ", "spot": SPOT, "rungs": rungs, "iv": {"atm_iv": 0.3, "skew_pts": 3.0},
             "ctx": {"hv30": 0.25, "next_earnings": None}, "trend": {"ext_200": 0.2, "rsi14": 62, "off_high_pct": 3.0}}
        pos = [{"ticker": "XYZ", "lots": [{"shares": 2000, "cost_basis": 30}]}]
        p = build_plan([m], pos, tax={"st_rate_pct": 40.8, "lt_rate_pct": 23.8}, mode="deferral", budget=None,
                       defer_put_loss=True, r=R, today=date(2026, 10, 13), floor_pct=15.0, coverage_pct=100.0)
        for r in p["positions"][0]["ladder"]:
            assert r["venue"] == "FLEX/OTC" and r["rfq"]["style"] == "European" and r["rfq"]["contracts"] == r["contracts"]
            assert any("INDICATIVE" in f for f in r["flags"]) and not any("open interest" in f for f in r["flags"])

    def test_distant_zero_cost_cap_is_limited_and_surplus_buys_a_tighter_floor(self):
        iv = lambda k: 0.40
        s = cl.pick_structure_model(SPOT, 5.0, R, iv, 15.0, 1.0, "2031-10-13")
        assert s["kc"] <= SPOT * (1 + cl.MAX_CAP_PCT / 100) and s["zero_cost"]
        assert s["floor_tightened"] and s["kp"] > 85.0
        assert s["call"].bid >= s["put"].ask - 1e-9 and s["band_pct"] >= cl.MIN_BAND_PCT


def _menu(sigma=0.30, T=1.4, floor=15.0, call_prob=0.20):
    calls, puts = _chain(T=T, sigma=sigma)
    dens = lognormal_density(SPOT, sigma, T, R)
    return cl.build_structures(calls, puts, SPOT, floor, dens, call_prob), dens


class TestStructureMenu:
    def test_menu_has_the_four_institutional_structures(self):
        cands, _ = _menu()
        ids = {c["id"] for c in cands}
        assert {"collar", "spread_collar", "overwrite"} <= ids
        ow = next(c for c in cands if c["id"] == "overwrite")
        assert ow["kp"] == 0 and ow["put"] is None and ow["p0"] == 0 and ow["net"] == pytest.approx(ow["call"].bid)

    def test_put_spread_costs_less_than_the_outright_put_and_funds_a_higher_cap(self):
        cands, _ = _menu()
        col = next(c for c in cands if c["id"] == "collar")
        sp = next(c for c in cands if c["id"] == "spread_collar")
        assert sp["p0"] < col["p0"] and sp["kp2"] < sp["kp"] and sp["kc"] >= col["kc"]

    def test_income_structures_are_net_credits(self):
        cands, _ = _menu()
        inc = [c for c in cands if c["id"] in ("income_spread", "overwrite")]
        assert inc and all(c["net"] > 0 for c in inc)

    def test_spread_payoff_is_capped_at_the_spread_width(self):
        kw = dict(kp=85, kp2=65, kc=125, c0=4, p0=3, b=40, st=ST, lt=LT, put_rate=LT, defer_put_loss=True)
        o = _rung_after_tax(np.array([50.0, 65.0, 80.0, 100.0]), **kw)
        S = np.array([50.0, 65.0, 80.0, 100.0])
        pay = o["pretax"] - S - (4 - 3)                                      # strip stock value and net premium
        assert pay == pytest.approx([20.0, 20.0, 5.0, 0.0])                  # capped at the 85−65 width; zero above the long strike
        o2 = _rung_after_tax(np.array([80.0]), **kw)
        assert o2["pretax"][0] == pytest.approx(80 + 5 + 1)                  # 85−80 payoff + net premium 1

    def test_overwrite_has_no_put_leg_in_the_tax_math(self):
        kw = dict(kp=0.0, kp2=0.0, kc=120, c0=5, p0=0.0, b=40, st=ST, lt=LT, put_rate=LT, defer_put_loss=True)
        o = _rung_after_tax(np.array([100.0, 130.0]), **kw)
        assert o["tax"][0] == pytest.approx(ST * 5)                          # lapses → ST premium
        assert o["tax"][1] == pytest.approx(LT * (120 - 40 + 5))             # assigned → LT gain incl. premium
        assert o["liq"][0] == pytest.approx(100 + 5 - ST * 5 - LT * (100 - 40))

    def test_expected_tax_reconciles_for_a_put_spread_too(self):
        d = lognormal_density(SPOT, 0.30, 1.4, R)
        e = rung_economics(d, kp=85, kp2=65, kc=140, c0=6, p0=3, b=40, n=1, st=ST, lt=LT, tenor_days=511,
                           defer_put_loss=True, spot=SPOT, r=R)
        kw = e["kw"]
        o = _rung_after_tax(d.K, **kw)
        pc = d.prob_above(140.0)
        below = d.K < 140.0
        num = pc * float(_rung_after_tax(np.array([140.0]), **kw)["tax"][0]) + \
            float(np.trapezoid(np.where(below, o["tax"] * d.pdf, 0), d.K)) * (1 - pc) / float(np.trapezoid(np.where(below, d.pdf, 0), d.K))
        assert e["expected_tax"] == pytest.approx(num, rel=1e-9)

    def test_tail_metrics_hedge_beats_unhedged_overwrite_barely_helps(self):
        cands, d = _menu()
        by = {c["id"]: cl.structure_metrics(c, d, SPOT, 40.0, ST, LT, 511, True, R) for c in cands}
        assert by["collar"]["protection"] > by["spread_collar"]["protection"] > by["overwrite"]["protection"] >= 0
        assert by["collar"]["upside"] <= by["overwrite"]["upside"] <= 1.0
        assert by["overwrite"]["stress_loss_pct"] > by["collar"]["stress_loss_pct"]

    def test_loss_limit_excludes_the_unhedged_overwrite_and_stance_changes_the_pick(self):
        cands, d = _menu()
        pick = lambda stance, ml: cl.choose_structure(cands, d, SPOT, 40.0, ST, LT, 511, True, R, stance, ml)
        s_prot, menu = pick("protect", 30.0)
        assert s_prot["kp"] > 0                                              # protect never picks the unhedged overwrite
        assert next(m for m in menu if m["id"] == "overwrite")["eligible"] is False
        s_inc, _ = pick("income", 80.0)                                      # loose limit + income stance → most credit funded
        assert s_inc["net"] >= s_prot["net"]
        assert sum(1 for m in menu if m["recommended"]) == 1

    def test_if_nothing_is_eligible_the_least_bad_structure_is_returned(self):
        cands, d = _menu()
        s_, menu = cl.choose_structure(cands, d, SPOT, 40.0, ST, LT, 511, True, R, "income", 0.0)
        assert s_ is not None and sum(1 for m in menu if m["recommended"]) == 1

    def test_overlay_options_per_cycle_numbers(self):
        calls, _ = _chain(T=45 / 365.0, sigma=0.30)
        ov = cl.overlay_options(calls, SPOT, 45, R)
        assert ov and all(o["strike"] > SPOT and o["bid"] > 0 for o in ov)
        lo, hi = ov[0], ov[-1]
        assert lo["strike"] >= hi["strike"] and lo["p_assign_cycle_pct"] <= hi["p_assign_cycle_pct"]
        assert hi["p_assign_12m_pct"] == pytest.approx((1 - (1 - hi["p_assign_cycle_pct"] / 100) ** (365 / 45)) * 100, abs=0.2)

    def test_plan_carries_menu_income_and_overlay(self):
        m = _market()
        calls, puts = _chain(T=420 / 365.0)
        m["overlay"] = cl.overlay_options(_chain(T=45 / 365.0)[0], SPOT, 45, R)
        for r_ in m["rungs"]:
            r_["cands"] = cl.build_structures(*_chain(T=r_["dte"] / 365.0), SPOT, 15.0, r_["dens"], 0.20)
        pos = [{"ticker": "XYZ", "lots": [{"shares": 3000, "cost_basis": 30}]}]
        p = build_plan([m], pos, tax={"st_rate_pct": 40.8, "lt_rate_pct": 23.8}, mode="deferral", budget=None,
                       defer_put_loss=True, r=R, today=date(2026, 10, 13), floor_pct=15.0, coverage_pct=50.0, stance="income",
                       max_loss_pct=45.0)
        pos0 = p["positions"][0]
        for r_ in pos0["ladder"]:
            assert sum(1 for x in r_["menu"] if x["recommended"]) == 1 and r_["structure"]["id"] == next(x["id"] for x in r_["menu"] if x["recommended"])
            assert r_["tax_note"]
        assert pos0["overlay"]["outside_ladder"] is True and pos0["overlay"]["shares"] == 1500
        assert p["income"]["net_premium_total"] == pytest.approx(sum(r_["net_credit_total"] for r_ in pos0["ladder"]))
        import json
        json.dumps(p, default=float)
