"""Tests for the institutional multi-leg trade-repair engine (BS-priced, greeks, defined-risk)."""
import pytest

from app.services import trade_repair_service as tr
from app.services.trade_repair_service import repair_alternatives
from app.services.stock_service import bs_price


def _coarse_chain(spot, strikes, iv_p=0.5, iv_c=0.5):
    """A live chain on WIDELY-spaced strikes — the condition that used to collapse a spread."""
    ch = {}
    for dte in (25, 70):
        ch[dte] = {float(k): {"P": {"mid": round(bs_price(spot, k, dte / 365, 0.045, iv_p, "put"), 2), "iv": iv_p},
                              "C": {"mid": round(bs_price(spot, k, dte / 365, 0.045, iv_c, "call"), 2), "iv": iv_c}}
                   for k in strikes}
    return ch


def _wmt():
    # WMT CSP short put @105, ~10% correction → spot near/through the strike, tested.
    return repair_alternatives(legs=[{"strike": 105, "right": "P", "sign": -1, "qty": 2, "entry": 2.10}],
                               spot=104.0, dte_days=25, atm_iv=0.28)


def _by(r, needle):
    return next(a for a in r["alternatives"] if needle in a["name"])


class TestRepairMenu:
    def test_flags_tested_and_prices_the_loss(self):
        r = _wmt()
        assert r["tested"] is True and r["cushion_pct"] < 5 and r["unrealized_pnl"] < 0

    def test_close_is_the_flat_benchmark(self):
        close = _by(_wmt(), "Close")
        assert len({s["pnl"] for s in close["scenarios"]}) == 1
        assert close["max_loss"] == close["max_gain"] == _wmt()["unrealized_pnl"]

    def test_jade_lizard_is_upside_risk_free_and_multi_leg(self):
        jade = _by(_wmt(), "Jade-lizard")
        assert jade["upside_risk_free"] is True                 # sized so credit ≥ width
        # only a DOWNSIDE (put) breakeven — no upside breakeven when it's risk-free up
        assert all(b < 106 for b in jade["breakevens"])
        assert len([lg for lg in jade["legs"] if lg["right"] in ("P", "C")]) == 3   # put + call spread
        assert jade["greeks"]["theta"] > 0                      # harvests decay

    def test_iron_condor_has_the_tightest_defined_max_loss(self):
        r = _wmt()
        condor = _by(r, "iron condor")
        assert condor["defined_risk"] is True
        # capping BOTH tails must give a far shallower max loss than the naked short put it started as
        # (the naked "Roll down & out" this used to compare against is a DEBIT on a tested put, so the
        # credit-only rule removes it — the hold baseline is the same naked-put exposure)
        assert condor["max_loss"] > r["hold"]["max_loss"]

    def test_wheel_is_defined_because_stock_covers_the_call(self):
        wheel = _by(_wmt(), "assignment") if any("assignment" == a["category"] for a in _wmt()["alternatives"]) else _by(_wmt(), "covered call")
        assert wheel["defined_risk"] is True                    # long stock covers the short call
        assert wheel["max_loss"] is not None

    def test_delta_hedge_is_undefined_upside(self):
        hedge = _by(_wmt(), "Delta-hedge")
        assert hedge["defined_risk"] is False and hedge["max_loss"] is None   # short stock → unbounded up

    def test_every_alt_carries_the_institutional_read(self):
        for a in _wmt()["alternatives"]:
            assert "greeks" in a and set(a["greeks"]) == {"delta", "gamma", "theta", "vega"}
            assert "scenarios" in a and any(s["move_pct"] == 0 for s in a["scenarios"])
            assert "rationale" in a and a["rationale"]

    def test_short_call_gets_mirrored_repairs(self):
        r = repair_alternatives(legs=[{"strike": 100, "right": "C", "sign": -1, "qty": 1, "entry": 1.8}],
                                spot=101.0, dte_days=25, atm_iv=0.30)
        assert any("Reverse-jade" in a["name"] for a in r["alternatives"])
        assert any(a["name"].startswith("Cap the tail — buy the") and "call" in a["name"] for a in r["alternatives"])
        # the strike-moving roll is mirrored too — offered when it is a CREDIT (an OTM call rolls up for one)
        far = repair_alternatives(legs=[{"strike": 100, "right": "C", "sign": -1, "qty": 1, "entry": 1.8}],
                                  spot=90.0, dte_days=25, atm_iv=0.30)
        assert any("Roll up" in a["name"] for a in far["alternatives"])
        assert not any(a["category"] == "assignment" for a in r["alternatives"])   # can't wheel a short call


class TestStructuralRepairs:
    def _condor(self, spot=101.0):
        # iron condor with the PUT side tested (spot near the short 100 put).
        return repair_alternatives(legs=[
            {"strike": 100, "right": "P", "sign": -1, "qty": 1, "entry": 1.6},
            {"strike": 95, "right": "P", "sign": 1, "qty": 1, "entry": 0.7},
            {"strike": 115, "right": "C", "sign": -1, "qty": 1, "entry": 1.4},
            {"strike": 120, "right": "C", "sign": 1, "qty": 1, "entry": 0.6}],
            spot=spot, dte_days=25, atm_iv=0.26)

    def test_condor_is_classified_and_gets_structural_menu(self):
        r = self._condor()
        assert r["structure"] == "iron_condor" and r["short_right"] == "P"   # put side is tested
        names = " ".join(a["name"] for a in r["alternatives"])
        assert "whole structure out" in names and "tested put wing" in names and "keep the call side" in names
        # NO single-leg overlays (jade / wheel) on a multi-leg structure
        assert not any("Jade" in a["name"] or a["category"] == "assignment" for a in r["alternatives"])

    def test_roll_whole_keeps_all_four_legs(self):
        roll = next(a for a in self._condor()["alternatives"] if "whole structure" in a["name"])
        assert len([lg for lg in roll["legs"] if lg["right"] in ("P", "C")]) == 4

    def test_close_tested_wing_keeps_the_safe_wing(self):
        close_wing = next(a for a in self._condor()["alternatives"] if "keep the call side" in a["name"])
        legs = [lg for lg in close_wing["legs"] if lg["right"] in ("P", "C")]
        assert len(legs) == 2 and all(lg["right"] == "C" for lg in legs)   # only the call spread remains

    def test_spreads_never_collapse_to_one_strike(self):
        # The OKTA bug: on a coarse chain a spread's two legs snapped to the SAME strike.
        # Every option spread in every alternative must have DISTINCT strikes with real width — where a
        # "spread" means legs sharing an EXPIRY. The same strike at two different expiries is a CALENDAR
        # (the gamma hedge), which is legitimate, so key on (strike, expiry) rather than strike alone.
        chain = _coarse_chain(104.0, [x * 5 for x in range(14, 26)])   # strikes every $5
        r = repair_alternatives(legs=[{"strike": 105, "right": "P", "sign": -1, "qty": 2, "entry": 2.10}],
                                spot=104.0, dte_days=25, atm_iv=0.5, chains=chain)
        for a in r["alternatives"]:
            opt = [lg for lg in a["legs"] if lg["right"] in ("P", "C")]
            for right in ("P", "C"):
                keys = [(lg["strike"], lg["dte_days"]) for lg in opt if lg["right"] == right and lg["dte_days"]]
                assert len(keys) == len(set(keys)), f"{a['name']} has a collapsed {right} spread {keys}"

    def test_low_value_overlay_is_skipped(self):
        # A barely-tested short call (spot well below strike) → a far-OTM reverse-jade collects a
        # trivial credit for real downside risk. A professional wouldn't suggest it — it's dropped.
        chain = _coarse_chain(165.0, [x * 5 for x in range(24, 41)])
        r = repair_alternatives(legs=[{"strike": 195, "right": "C", "sign": -1, "qty": 1, "entry": 0.9}],
                                spot=165.0, dte_days=25, atm_iv=0.5, chains=chain)
        assert not any("Reverse-jade" in a["name"] for a in r["alternatives"])

    def test_live_chain_mid_is_used_when_supplied(self):
        # a fat chain mid on the tested short must flow into the close P&L (vs a thin BS price).
        chains = {25: {100.0: {"P": {"mid": 5.0, "iv": 0.40}}}}   # short put now worth $5 (deep)
        r = repair_alternatives(legs=[{"strike": 100, "right": "P", "sign": -1, "qty": 1, "entry": 1.6}],
                                spot=101.0, dte_days=25, atm_iv=0.26, chains=chains)
        assert "live chain" in r["pricing"]
        # entry credit 1.6, now worth 5.0 → close realizes ≈ (1.6−5.0)*100 = −340
        assert abs(r["unrealized_pnl"] - (-340)) < 5


# ── math accuracy: exact breakevens, call-side basis ─────────────────────────────────────────
class TestExactMath:
    def test_short_call_breakeven_is_strike_plus_credit(self):
        # $700 call sold for $0.90 → 700.90. The old grid-scan interpolated across the strike kink → 697.68.
        r = repair_alternatives(legs=[{"strike": 700, "right": "C", "sign": -1, "qty": 1, "entry": 0.90}],
                                spot=629.5, dte_days=22, atm_iv=0.542)
        assert r["hold"]["breakevens"] == [700.9]
        assert r["recoverability"]["breakeven"] == 700.9

    def test_put_and_spread_breakevens_are_exact(self):
        p = repair_alternatives(legs=[{"strike": 100, "right": "P", "sign": -1, "qty": 1, "entry": 1.60}],
                                spot=101.0, dte_days=25, atm_iv=0.26)
        assert p["hold"]["breakevens"] == [98.4]
        s = repair_alternatives(legs=[{"strike": 100, "right": "P", "sign": -1, "qty": 1, "entry": 2.0},
                                      {"strike": 95, "right": "P", "sign": 1, "qty": 1, "entry": 0.8}],
                                spot=101.0, dte_days=25, atm_iv=0.26)
        assert s["hold"]["breakevens"] == [98.8]

    def test_naked_call_effective_basis_is_strike_plus_credit(self):
        # called away = SELL at K and keep the credit → 700.90, not the put formula K−C (699.10)
        r = repair_alternatives(legs=[{"strike": 700, "right": "C", "sign": -1, "qty": 1, "entry": 0.90}],
                                spot=629.5, dte_days=22, atm_iv=0.542)
        assert r["assignment"]["effective_basis"] == 700.9
        assert "700.90" in r["assignment"]["consequence"]


# ── the P(touch) bug: a hot week must not pin the barrier probability at ~100% ───────────────
class TestTouchProbability:
    RAW_HOT_WEEK = 7.234                                            # "+723%/yr" — AMD's last week, extrapolated

    def test_raw_momentum_saturates_but_the_shared_drift_does_not(self):
        raw = tr.barrier_touch_prob(629.5, 700.0, 0.542, 22, self.RAW_HOT_WEEK, "C")
        fixed = tr.barrier_touch_prob(629.5, 700.0, 0.542, 22, tr.touch_drift(self.RAW_HOT_WEEK), "C")
        assert raw > 0.95                                           # the bug: "100% chance it's breached"
        assert 0.30 < fixed < 0.55

    def test_touch_is_about_twice_the_terminal_itm_probability(self):
        # the rule of thumb the risk read itself states, and what the assignment lens's P(ITM) implies
        r = repair_alternatives(legs=[{"strike": 700, "right": "C", "sign": -1, "qty": 1, "entry": 0.90}],
                                spot=629.5, dte_days=22, atm_iv=0.542)
        touch = tr.barrier_touch_prob(629.5, 700.0, 0.542, 22, tr.touch_drift(self.RAW_HOT_WEEK), "C") * 100
        assert 1.5 < touch / r["assignment"]["p_itm"] < 3.0

    def test_touch_drift_caps_shrinks_and_tolerates_none(self):
        assert tr.touch_drift(None) == 0.0
        assert tr.touch_drift(50.0) == tr.touch_drift(1.0)          # capped at ±100%/yr
        assert tr.touch_drift(-50.0) == tr.touch_drift(-1.0)
        assert 0.045 < tr.touch_drift(1.0) < 1.0                    # shrunk toward the risk-neutral rate
        assert tr.touch_drift(0.045) == 0.045                       # at the anchor → unchanged


# ── the "-ve Structure" row: sign-aware text ─────────────────────────────────────────────────
class TestStructureFactor:
    def test_call_price_already_through_resistance_is_a_breakout_not_a_ceiling(self):
        f = tr.structure_factor(629.5, 604.58, 623.84, need_up=False)   # AMD: 0.9% ABOVE the 15d resistance
        assert f["favorable"] is False
        assert "ABOVE resistance" in f["detail"] and "breakout" in f["detail"]
        assert "-" not in f["detail"].split("%")[0].split()[-1]          # no "-0.9%"
        assert "ceiling that can cap" not in f["detail"]

    def test_call_with_resistance_overhead_is_a_ceiling(self):
        f = tr.structure_factor(620.0, 600.0, 640.0, need_up=False)
        assert f["favorable"] is True and "3.2% above — a ceiling that can cap the rise" in f["detail"]

    def test_put_mirror(self):
        assert tr.structure_factor(100.0, 95.0, 110.0, need_up=True)["favorable"] is True
        broken = tr.structure_factor(90.0, 95.0, 110.0, need_up=True)
        assert broken["favorable"] is False and "BELOW support" in broken["detail"]

    def test_level_note_states_each_gap_direction(self):
        n = tr.level_note(629.5, 604.58, 623.84)
        assert "resistance $623.84 (0.9% below)" in n and "THROUGH the range top" in n
        assert "-0." not in n


# ── merge: the roll search and the desk pick are ONE ranked menu ──────────────────────────────
class TestMergedRecommendation:
    def _menu(self):
        from defend_fixtures import merged_amd_menu
        return merged_amd_menu()

    def test_roll_candidates_are_full_alternatives_from_the_same_engine(self):
        m = self._menu()
        rolls = [a for a in m["alternatives"] if a.get("roll_meta")]
        assert len(rolls) == 6
        for a in rolls:
            assert a["category"] == "roll" and a["group"] == "adjust"
            assert set(a["greeks"]) == {"delta", "gamma", "theta", "vega"} and a["scenarios"]
            assert a["pop_pct"] is not None and a["ev"] is not None
            assert a["d_ev"] is not None and a["d_pop"] is not None          # Δ-vs-hold via the SAME helper
            assert a["desk_score"] is not None and set(a["score_breakdown"]) == {"edge", "risk", "recovery", "market_fit"}
            assert all(lg["expiry"] for lg in a["legs"] if lg["right"] in ("P", "C"))

    def test_roll_breakeven_reconciles_with_the_search_exactly(self):
        # two engines must not print two different breakevens for the same roll (was: grid-scan vs exact)
        from defend_fixtures import amd_candidates
        cands = {(c["expiry"], c["strike"]): c for c in amd_candidates()}
        for a in [x for x in self._menu()["alternatives"] if x.get("roll_meta")]:
            c = cands[(a["roll_meta"]["expiry"], a["roll_meta"]["strike"])]
            assert abs(a["breakevens"][0] - c["new_breakeven"]) <= 0.02, (a["name"], a["breakevens"], c["new_breakeven"])

    def test_roll_net_cash_is_the_searchs_credit_and_no_new_money(self):
        for a in [x for x in self._menu()["alternatives"] if x.get("roll_meta")]:
            assert a["net_cash"] == a["roll_meta"]["credit_total"] and a["net_cash"] >= 0

    def test_fixed_horizon_fallback_rolls_are_dropped_once_the_search_speaks(self):
        m = self._menu()
        assert not any(a.get("plain_roll") for a in m["alternatives"])
        assert not any(a["name"].startswith(("Roll up & out", "Roll out at the same")) for a in m["alternatives"])

    def test_one_recommendation_and_it_explains_the_best_roll(self):
        d = self._menu()["desk_recommendation"]
        assert d["name"] and d["desk_score"] is not None
        assert any("Best credit roll" in r for r in d["reasons"])              # the search can't contradict the pick silently
        assert "nudge" not in d                                                # the old "go check Optimize the roll" bridge is gone

    def test_close_pick_offers_a_real_fix_as_the_keep_alive_runner_up(self):
        m = self._menu()
        d = m["desk_recommendation"]
        assert d["category"] == "exit" and d["runner_up"]["role"] == "fix"
        runner = next(a for a in m["alternatives"] if a["name"] == d["runner_up"]["name"])
        assert runner["group"] == "adjust" and runner["category"] not in ("hold", "exit")

    def test_avoid_flags_only_material_deficits(self):
        m = self._menu()
        avoid = {x["name"] for x in m["desk_recommendation"]["avoid"]}
        by = {a["name"]: a for a in m["alternatives"]}
        for name in avoid:
            a = by[name]
            assert not a["defined_risk"] and (a["d_ev"] <= -350 or a["d_pop"] <= -8), (name, a["d_ev"], a["d_pop"])
        # a fair-value credit roll (ΔE[P&L] ≈ $1, ΔPoP −2) is NOT a trap
        assert not any(n.startswith("Roll to the $700 call · Oct 23") for n in avoid)

    def test_hold_and_close_are_scored_on_the_same_scale(self):
        m = self._menu()
        scored = {a["category"]: a["desk_score"] for a in m["alternatives"] if a["category"] in ("hold", "exit")}
        assert set(scored) == {"hold", "exit"} and all(isinstance(v, int) for v in scored.values())

    def test_healthy_trade_still_picks_hold_with_no_keep_alive_runner_up(self):
        m = repair_alternatives(legs=[{"strike": 160, "right": "C", "sign": -1, "qty": 1, "entry": 3.0}],
                                spot=120.0, dte_days=40, atm_iv=0.35)
        m["recoverability"].update(posture="healthy", p_touch=12, vrp_pct=5, trend_pct=3)
        tr.drop_plain_rolls(m)
        tr.rank_defenses(m)
        d = m["desk_recommendation"]
        assert d["category"] == "hold" and d["runner_up"] is None
        assert not any(a["category"] == "roll" for a in m["alternatives"])     # no stray +45d roll for a healthy trade

    def test_stressed_naked_call_can_be_capped_with_a_wing(self):
        m = self._menu()
        cap = next(a for a in m["alternatives"] if a["name"].startswith("Cap the tail"))
        assert cap["defined_risk"] and cap["max_loss"] is not None and cap["net_cash"] < 0   # a small, known debit
        assert cap["max_loss"] > -0.15 * 70000                                 # vs UNLIMITED on the naked call


class TestRollsAreCreditOnly:
    """The defense principle for a lone short: NO net new money — a roll must be a credit."""

    def test_no_debit_roll_survives_on_a_lone_short(self):
        cases = [
            dict(legs=[{"strike": 105, "right": "P", "sign": -1, "qty": 2, "entry": 2.10}], spot=104.0, dte_days=25, atm_iv=0.28),
            dict(legs=[{"strike": 100, "right": "C", "sign": -1, "qty": 1, "entry": 1.8}], spot=101.0, dte_days=25, atm_iv=0.30),
            dict(legs=[{"strike": 100, "right": "C", "sign": -1, "qty": 1, "entry": 1.8}], spot=90.0, dte_days=25, atm_iv=0.30),
            dict(legs=[{"strike": 700, "right": "C", "sign": -1, "qty": 1, "entry": 0.90}], spot=629.5, dte_days=22, atm_iv=0.542),
        ]
        for kw in cases:
            for a in repair_alternatives(**kw)["alternatives"]:
                if a["name"].startswith("Roll"):
                    assert a["net_cash"] >= 0, (kw["spot"], a["name"], a["net_cash"])

    def test_a_tested_trades_only_credit_roll_is_the_same_strike_one(self):
        # buying back an ITM leg to move the strike costs money; pushing the SAME strike out in time pays
        r = repair_alternatives(legs=[{"strike": 105, "right": "P", "sign": -1, "qty": 2, "entry": 2.10}],
                                spot=104.0, dte_days=25, atm_iv=0.28)
        rolls = [a["name"] for a in r["alternatives"] if a["name"].startswith("Roll")]
        assert rolls == ["Roll out at the same $105 strike (+45d)"]


# ── far tenor, forward vol, financing: no phantom edge from the SHAPE of the vol surface ─────────────
def _ts_chain(spot, ivs, k_lo=0.6, k_hi=1.5):
    """Live-style chains {dte: {strike: {P/C: {mid, iv}}}} with a flat IV per tenor — i.e. a chosen TERM STRUCTURE."""
    return {dte: {float(k): {"P": {"mid": round(bs_price(spot, k, dte / 365, 0.045, iv, "put"), 2), "iv": iv},
                             "C": {"mid": round(bs_price(spot, k, dte / 365, 0.045, iv, "call"), 2), "iv": iv}}
                  for k in range(int(spot * k_lo), int(spot * k_hi), 5)} for dte, iv in ivs.items()}


DDOG_LEG = {"strike": 320, "right": "C", "sign": -1, "qty": 1, "entry": 8.0}


def _ddog(ivs, far_dte=56, **kw):
    return repair_alternatives(legs=[dict(DDOG_LEG)], spot=312.0, dte_days=21, atm_iv=list(ivs.values())[0],
                               chains=_ts_chain(312.0, ivs), near_expiry="2026-10-16", far_expiry="2026-11-20",
                               far_dte=far_dte, **kw)


class TestFarTenorAndTermStructure:
    def test_far_leg_is_built_at_the_real_far_expiry_not_the_nominal_45_days(self):
        # target +45d = 66d resolves to the listed Nov 20 = 56d; the leg must say (and be valued as) 56d, not 66d
        cal = _by(_ddog({21: 0.66, 56: 0.50}), "Calendarised")
        far = next(lg for lg in cal["legs"] if lg["action"] == "BUY")
        assert far["dte_days"] == 56 and far["expiry"] == "2026-11-20"
        assert "~56d" in cal["name"] and "66" not in cal["name"]

    def test_without_a_far_chain_it_falls_back_to_the_nominal_tenor(self):
        r = repair_alternatives(legs=[dict(DDOG_LEG)], spot=312.0, dte_days=21, atm_iv=0.5)
        far = next(lg for lg in _by(r, "Calendarised")["legs"] if lg["action"] == "BUY")
        assert far["dte_days"] == 66

    def test_names_and_labels_use_the_real_roll_length(self):
        r = _ddog({21: 0.50, 56: 0.58})
        assert any("+35d" in a["name"] for a in r["alternatives"] if a["name"].startswith("Roll"))

    def test_forward_vol_properties(self):
        f = tr._fwd_iv
        assert f(0.50, 0.50, 56, 21) == pytest.approx(0.50)                 # flat surface → flat forward
        assert f(0.50, 0.66, 56, 21) < 0.50 < f(0.58, 0.50, 56, 21)         # backwardation lowers it, contango raises it
        assert f(0.50, 0.66, 56, 21) == pytest.approx(0.372, abs=0.005)     # (0.5²·56 − 0.66²·21)/35 → 37.2%
        assert f(0.10, 0.90, 56, 21) == pytest.approx(0.05)                 # absurd surface → floored at half the far IV
        assert f(0.50, 0.50, 21, 56) == 0.50                                # not a longer leg → unchanged

    @pytest.mark.parametrize("ivs", [{21: 0.66, 56: 0.50}, {21: 0.50, 56: 0.58}, {21: 0.55, 56: 0.55}])
    def test_a_fairly_priced_calendar_has_no_phantom_edge_in_any_term_structure(self, ivs):
        # regression for the DDOG screen: a rich front used to book +$300…$520 of "edge" (far leg marked at today's
        # whole-life IV, at the wrong tenor) and the calendar beat Close. Fair pricing ⇒ ΔE[P&L] ≈ 0.
        r = _ddog(ivs)
        assert abs(_by(r, "Calendarised")["d_ev"]) <= 40, (ivs, _by(r, "Calendarised")["d_ev"])
        # the re-center calendar only qualified for the menu (must beat closing) BECAUSE of the phantom edge; under
        # fair pricing it may correctly be absent — but if it is offered, its edge must be ≈ 0 too
        for a in r["alternatives"]:
            if a["name"].startswith("Re-center into a call calendar"):
                assert abs(a["d_ev"]) <= 40, (ivs, a["d_ev"])

    def test_a_far_dated_short_has_no_phantom_edge_in_contango(self):
        # priced at the back-month IV, so it must be diffused at it too (was +$375 on a fair roll)
        r = _ddog({21: 0.50, 56: 0.58})
        roll = next(a for a in r["alternatives"] if a["name"].startswith("Roll up & out"))
        assert abs(roll["d_ev"]) <= 60

    def test_a_rich_front_no_longer_makes_a_calendar_the_pick(self):
        r = _ddog({21: 0.66, 56: 0.50})
        r["structure"] = "short_call"
        r["recoverability"].update(posture="marginal", p_touch=52, vrp_pct=-8, trend_pct=40, iv_pct=66, hv_pct=72)
        r["iv_structure"] = {"term_label": "front rich (backwardated)"}
        tr.drop_plain_rolls(r)
        tr.rank_defenses(r)
        assert r["desk_recommendation"]["category"] != "calendar"
        cal = _by(r, "Calendarised")
        assert cal["desk_score"] < r["desk_recommendation"]["desk_score"] - 5


class TestFinancing:
    def test_buying_stock_is_charged_the_return_its_cash_would_have_earned(self):
        # +r·T·outlay of pure drift on ~$31k of stock for 3 weeks was booked as ~+$83 of "edge" on the collar
        r = _ddog({21: 0.55, 56: 0.55})
        assert abs(_by(r, "Collar it")["d_ev"]) <= 30
        assert abs(_by(r, "Cover it")["d_ev"]) <= 40

    def test_debit_option_structures_are_barely_touched(self):
        base = _ddog({21: 0.55, 56: 0.55})
        assert abs(_by(base, "Cap the tail")["d_ev"]) <= 5                  # a $300 debit for 3 weeks is ~$0.25 of carry


class TestCoveredCallMenu:
    def _covered(self):
        return repair_alternatives(legs=[dict(DDOG_LEG)], spot=312.0, dte_days=21, atm_iv=0.55,
                                   chains=_ts_chain(312.0, {21: 0.55, 56: 0.55}), far_dte=56,
                                   stock={"shares": 100, "basis": 290.0})

    def test_it_does_not_offer_to_buy_shares_it_already_owns(self):
        names = [a["name"] for a in self._covered()["alternatives"]]
        assert not any(n.startswith("Cover it") for n in names)
        assert not any(n.startswith("Delta-hedge") for n in names)          # the shares already hedge the call
        assert not any(n.startswith("Cap the tail") for n in names)         # already defined by the shares

    def test_the_collar_is_just_the_protective_put_on_the_existing_shares(self):
        r = self._covered()
        c = _by(r, "Collar the shares")
        assert c["defined_risk"] is True and c["net_cash"] > -1500          # a put premium, not $31k of stock
        assert not any(lg["right"] == "STK" and lg["action"] == "BUY" and lg["qty"] != 100 for lg in c["legs"])
        assert not any(a["name"].startswith("Collar it — buy stock") for a in r["alternatives"])


# ── earnings: a leg that outlives the print carries risk the diffusion model can't price ───────────
def _ranked(ivs=None, *, earn_days=41, earn_date="2026-11-05", posture="marginal", p_touch=52, far_spans=True, **kw):
    r = _ddog(ivs or {21: 0.60, 56: 0.52})
    r["structure"] = "short_call"
    r["recoverability"].update(posture=posture, p_touch=p_touch, vrp_pct=-8, trend_pct=40, iv_pct=60, hv_pct=65)
    if earn_days is not None:
        r["context"] = {"earnings": {"days": earn_days, "date": earn_date, "before_expiry": earn_days <= 21}}
    r["iv_structure"] = {"term_label": "front rich (backwardated)", **({"far_spans_earnings": True} if far_spans else {})}
    tr.drop_plain_rolls(r)
    tr.rank_defenses(r)
    return r


class TestEarnings:
    def test_a_calendar_whose_long_far_leg_spans_earnings_is_deducted_and_says_why(self):
        with_e, without = _ranked(), _ranked(earn_days=None, far_spans=False)
        cal, cal0 = _by(with_e, "Calendarised"), _by(without, "Calendarised")
        assert cal["event_risk"]["points"] == 10 and "earnings" in cal["event_risk"]["note"]
        assert "not a defense" in cal["event_risk"]["note"]
        assert cal0["event_risk"] is None
        assert cal["desk_score"] <= cal0["desk_score"] - 9                  # a DIRECT deduction, not a 0.15-weighted nudge

    def test_no_earnings_data_means_no_deduction_anywhere(self):
        assert all(a.get("event_risk") is None for a in _ranked(earn_days=None, far_spans=False)["alternatives"])

    def test_past_earnings_are_ignored(self):
        assert all(a.get("event_risk") is None for a in _ranked(earn_days=-3)["alternatives"])

    def test_a_short_leg_that_outlives_the_print_is_the_heaviest_deduction(self):
        # naked short call spanning: −15; the same leg BEFORE the print: 0
        cands = [dict(expiry="2026-10-23", dte=28, strike=320.0, right="C", new_price=14.0, iv=0.6, roll_net_cash=400,
                      credit_per_share=4.0, new_breakeven=332.0, new_capital=32000, p_otm=62.0, p_otm_source="RND",
                      spans_earnings=False, structure={"cleared_count": 3, "levels_available": 6},
                      scores={"composite": 70, "structure": 60, "cushion": 50, "probability": 62, "credit": 50}, why="pre-print"),
                 dict(expiry="2026-11-20", dte=56, strike=320.0, right="C", new_price=26.0, iv=0.5, roll_net_cash=1600,
                      credit_per_share=16.0, new_breakeven=344.0, new_capital=32000, p_otm=58.0, p_otm_source="RND",
                      spans_earnings=True, structure={"cleared_count": 3, "levels_available": 6},
                      scores={"composite": 70, "structure": 60, "cushion": 50, "probability": 58, "credit": 80}, why="through the print")]
        r = _ranked()
        mark = bs_price(312.0, 320.0, 21 / 365, 0.045, 0.60, "call")
        built = tr.build_roll_alternatives(candidates=cands, tested={"right": "C", "strike": 320.0, "qty": 1, "entry": 8.0},
                                           tested_mark=mark, hold=r["hold"], spot=312.0, r=0.045, stock=None, iv_default=0.6)
        r["alternatives"] += built
        tr.rank_defenses(r)
        pre = next(a for a in r["alternatives"] if a.get("roll_meta") and a["roll_meta"]["dte"] == 28)
        thru = next(a for a in r["alternatives"] if a.get("roll_meta") and a["roll_meta"]["dte"] == 56)
        assert pre["event_risk"] is None
        assert thru["event_risk"]["points"] == 15 and "NAKED short call" in thru["event_risk"]["note"]

    def test_a_wing_capped_short_takes_the_smaller_deduction(self):
        r = _ranked()
        cap = _by(r, "Cap the tail")
        for lg in cap["legs"]:
            lg["dte_days"] = 56                                             # both legs now outlive the print, one capping the other
        cap["event_risk"] = None
        tr.rank_defenses(r)
        assert _by(r, "Cap the tail")["event_risk"]["points"] == 6

    def test_a_covered_call_through_the_print_is_not_called_naked(self):
        r = repair_alternatives(legs=[dict(DDOG_LEG)], spot=312.0, dte_days=60, atm_iv=0.55, stock={"shares": 100, "basis": 290.0})
        r["recoverability"].update(posture="marginal", p_touch=52, vrp_pct=-8, trend_pct=40, iv_pct=55, hv_pct=60)
        r["context"] = {"earnings": {"days": 41, "date": "2026-11-05", "before_expiry": True}}
        tr.rank_defenses(r)
        hold = next(a for a in r["alternatives"] if a["category"] == "hold")
        assert hold["event_risk"]["points"] == 10 and "NAKED short call" not in hold["event_risk"]["note"]

    def test_the_current_expiry_running_through_earnings_hits_hold_and_close_says_it_removes_it(self):
        r = _ranked(earn_days=10, earn_date="2026-10-05", far_spans=False)
        hold = next(a for a in r["alternatives"] if a["category"] == "hold")
        assert hold["event_risk"]["points"] == 15
        d = r["desk_recommendation"]
        if d["category"] == "exit":
            assert any("removes the exposure to earnings" in x for x in d["reasons"])

    def test_the_pick_tells_you_the_print_lands_after_this_expiry(self):
        d = _ranked()["desk_recommendation"]
        assert any("land AFTER this expiry" in x for x in d["reasons"])

    def test_a_healthy_trade_gets_no_earnings_lecture(self):
        d = _ranked(posture="healthy", p_touch=8, far_spans=False)["desk_recommendation"]
        assert not any("AFTER this expiry" in x for x in d["reasons"])

    def test_term_structure_is_not_scored_when_the_back_month_spans_earnings(self):
        # a clean rich front earns the calendar +10 fit; with the print in the back month the read is contaminated → 0
        clean = _by(_ranked(earn_days=None, far_spans=False), "Calendarised")["score_breakdown"]["market_fit"]
        dirty = _by(_ranked(earn_days=41, far_spans=True), "Calendarised")["score_breakdown"]["market_fit"]
        assert clean - dirty >= 9
