"""Defending a CREDIT SPREAD (AAOI 80/70 put spread, spot $81): the live roll search prices the whole spread as one package,
every option explains itself against the position you already hold, the income policy prefers a free improvement over booking
the loss, and the earnings print is read off the term structure — all through the real handlers with only the network stubbed."""
import math

import pytest

from app.services import trade_repair_service as tr
from app.services.trade_repair_service import repair_alternatives, identify_wing, implied_event_move, attach_steps
import spread_world as W

AAOI_LEGS = [{"strike": 80, "right": "P", "sign": -1, "qty": 1, "entry": 2.60},
             {"strike": 70, "right": "P", "sign": 1, "qty": 1, "entry": 2.26}]


@pytest.fixture
def flow(monkeypatch):
    return W.run_flow(monkeypatch, earn_days=28, cur_dte=8)          # print AFTER this expiry; Nov 20 75/65 is the roll in question


def _roll(out, short, long_, dte=None):
    return next((a for a in out["alternatives"] if a.get("roll_meta") and a["roll_meta"].get("wing")
                 and a["roll_meta"]["wing"]["short"] == short and a["roll_meta"]["wing"]["long"] == long_
                 and (dte is None or a["roll_meta"]["dte"] == dte)), None)


# ── the live search prices the SPREAD as one package ─────────────────────────────────────────────
class TestSpreadRollSearch:
    def test_phase1_defers_a_clean_vertical_to_the_live_search_and_carries_the_wing(self, flow):
        p1, _ = flow
        assert p1["structure"] == "put_credit_spread" and p1["roll_search"] == "pending"
        assert p1["wing"] == {"right": "P", "qty": 1, "short_strike": 80.0, "long_strike": 70.0, "width": 10.0,
                              "short_entry": 2.6, "long_entry": 2.26, "net_entry": pytest.approx(0.34), "kept_legs": 0}
        assert p1["held"]["collected"] == 34.0

    def test_every_candidate_is_a_same_width_net_credit_or_even_defined_risk_spread(self, flow):
        _, out = flow
        rolls = [a for a in out["alternatives"] if a.get("roll_meta")]
        assert rolls and out["roll_search"] == "done"
        for a in rolls:
            w = a["roll_meta"]["wing"]
            assert w["width"] == 10.0 and w["old_short"] == 80.0 and w["old_long"] == 70.0       # same width, no widening to chase credit
            assert a["net_cash"] >= 0                                                              # no new money
            assert a["defined_risk"] is True and a["max_loss"] is not None
            opt = [l for l in a["legs"] if l["right"] in ("P", "C")]
            assert len(opt) == 2 and {l["action"] for l in opt} == {"SELL", "BUY"}
            assert all(l["expiry"] == a["roll_meta"]["expiry"] for l in opt)                       # both new legs carry the real expiry

    def test_strikes_only_move_to_the_SAFE_side(self, flow):
        # a roll must never raise the short put's strike (that raises risk) — only down / same
        _, out = flow
        assert all(a["roll_meta"]["strike"] <= 80.0 for a in out["alternatives"] if a.get("roll_meta"))

    def test_the_users_trade_idea_is_on_the_menu_Nov20_75_65_at_a_credit(self, flow):
        _, out = flow
        a = _roll(out, 75.0, 65.0, dte=36)
        assert a is not None and a["roll_meta"]["expiry"] == W.iso(36) and a["net_cash"] > 0
        assert "75/65" in a["name"] and "Nov 20" in a["name"]

    def test_the_old_spread_is_bought_back_whole_and_the_loss_is_carried_into_the_payoff(self, flow):
        p1, out = flow
        a = _roll(out, 75.0, 65.0, dte=36)
        held = {(l["right"], l["strike"]): l for l in p1["held"]["legs"]}
        mark = held[("P", 80.0)]["mark"] - held[("P", 70.0)]["mark"]                              # what the old spread costs to close, per share
        new_credit = next(l for l in a["legs"] if l["action"] == "SELL")["price"] - next(l for l in a["legs"] if l["action"] == "BUY")["price"]
        assert a["net_cash"] == pytest.approx((new_credit - mark) * 100, abs=3)
        # ALL-IN best case = what the original spread collected + the roll's net cash (the loss on the old spread is already netted in)
        assert a["max_gain"] == pytest.approx(34 + a["net_cash"], abs=3)
        # ...and the worst case is the width minus everything collected
        assert a["max_loss"] == pytest.approx(-(1000 - a["max_gain"]), abs=3)
        # break-even = new short strike − every dollar collected on the position
        assert a["breakevens"][0] == pytest.approx(75 - a["max_gain"] / 100, abs=0.06)

    def test_a_roll_improves_the_payoff_you_hold_at_roughly_zero_expected_cost(self, flow):
        p1, out = flow
        a = _roll(out, 75.0, 65.0, dte=36)
        hold = next(x for x in out["alternatives"] if x["category"] == "hold")
        assert a["max_loss"] > hold["max_loss"] and a["max_gain"] > hold["max_gain"]               # better worst AND best case
        assert a["pop_pct"] >= hold["pop_pct"] and abs(a["d_ev"]) <= 20                            # better odds, ≈ no EV cost

    def test_the_search_reads_the_wing_not_a_lone_short(self, monkeypatch):
        # regression: a spread used to be skipped (two blind +45d rolls, both landing on Dec 18)
        p1, out = W.run_flow(monkeypatch, earn_days=28, cur_dte=8)
        assert out["roll_search"] == "done" and not any(a.get("plain_roll") for a in out["alternatives"])
        assert len([a for a in out["alternatives"] if a.get("roll_meta")]) >= 3

    def test_a_condor_wing_roll_keeps_the_other_side_untouched(self, monkeypatch):
        legs = [("put", "SELL", 80), ("put", "BUY", 70), ("call", "SELL", 95), ("call", "BUY", 105)]
        p1, out = W.run_flow(monkeypatch, earn_days=28, cur_dte=8, legs=legs, entry=(2.6, 2.26, 1.1, 0.5))
        assert p1["structure"] == "iron_condor" and p1["wing"]["kept_legs"] == 2
        rolls = [a for a in out["alternatives"] if a.get("roll_meta")]
        assert rolls
        for a in rolls:
            calls = [l for l in a["legs"] if l["right"] == "C"]
            assert {(l["action"], l["strike"]) for l in calls} == {("SELL", 95.0), ("BUY", 105.0)}   # call side as it was
            assert all(l["dte_days"] == 8 for l in calls)                                           # …still expiring with the original
        s = rolls[0]["steps"]
        assert not any(x["right"] == "C" for x in s["close"] + s["open"]) and len([x for x in s["keep"] if x["right"] == "C"]) == 2

    def test_no_even_cost_roll_says_so_and_drops_the_stray_fixed_rolls(self, monkeypatch):
        # print INSIDE a 22d trade: the old spread is dear to buy back and nothing further out reaches even
        p1, out = W.run_flow(monkeypatch, earn_days=21, cur_dte=22)
        assert out["roll_search"] == "done" and not any(a.get("roll_meta") for a in out["alternatives"])
        assert "even cost or better" in out["roll_note"]
        assert not any(a["category"] == "roll" for a in out["alternatives"])                       # one source of truth: none

    def test_a_healthy_spread_skips_the_search_and_just_holds(self, monkeypatch):
        p1, out = W.run_flow(monkeypatch, earn_days=28, cur_dte=8, posture="healthy", p_touch=8.0, vrp=5.0, trend=3.0)
        assert p1["roll_search"] == "skipped" and out["desk_recommendation"]["category"] == "hold"
        assert not any(a["category"] == "roll" for a in out["alternatives"])
        assert out["timing"]["verdict"] == "hold"


# ── every option explains itself against the position you already hold ───────────────────────────
class TestStepsAndAllIn:
    def test_a_spread_roll_says_what_to_close_and_what_to_open(self, flow):
        _, out = flow
        s = _roll(out, 75.0, 65.0, dte=36)["steps"]
        assert [(x["action"], x["strike"]) for x in s["close"]] == [("BUY", 80.0), ("SELL", 70.0)]    # buy back the short, sell the long
        assert [(x["action"], x["strike"]) for x in s["open"]] == [("SELL", 75.0), ("BUY", 65.0)]
        assert s["keep"] == [] and s["stock"] is None and s["reconciled"] is True
        assert s["close_cash"] + s["open_cash"] == pytest.approx(s["net_cash"], abs=2)
        assert all(x["expiry"] == W.iso(8) for x in s["close"]) and all(x["expiry"] == W.iso(36) for x in s["open"])

    def test_all_in_bridges_the_old_trade_to_the_new_one(self, flow):
        _, out = flow
        a = _roll(out, 75.0, 65.0, dte=36)
        ai = a["all_in"]
        assert ai["collected_at_open"] == 34.0                                       # what the original spread paid
        assert ai["locked_in"] == pytest.approx(34 - 282, abs=6)                     # closing it realizes entry − mark (a LOSS)
        assert ai["net_cash_today"] == a["net_cash"] and ai["best_case"] == a["max_gain"] and ai["worst_case"] == a["max_loss"]
        assert ai["breakevens"] == a["breakevens"]
        # the bridge: locked-in loss + the new spread's credit = the all-in best case
        new_credit = next(l for l in a["legs"] if l["action"] == "SELL")["price"] - next(l for l in a["legs"] if l["action"] == "BUY")["price"]
        assert ai["best_case"] == pytest.approx(ai["locked_in"] + new_credit * 100, abs=4)

    def test_hold_keeps_everything_and_close_closes_everything(self, flow):
        _, out = flow
        hold = next(x for x in out["alternatives"] if x["category"] == "hold")["steps"]
        close = next(x for x in out["alternatives"] if x["category"] == "exit")["steps"]
        assert hold["close"] == [] and hold["open"] == [] and len(hold["keep"]) == 2
        assert len(close["close"]) == 2 and close["open"] == [] and close["keep"] == []
        assert close["net_cash"] == close["close_cash"] and close["net_cash"] < 0     # buying the spread back costs cash

    @pytest.mark.parametrize("kw", [
        dict(legs=[{"strike": 105, "right": "P", "sign": -1, "qty": 2, "entry": 2.10}], spot=104.0, dte_days=25, atm_iv=0.28),
        dict(legs=[{"strike": 700, "right": "C", "sign": -1, "qty": 1, "entry": 0.90}], spot=629.5, dte_days=22, atm_iv=0.542),
        dict(legs=[{"strike": 320, "right": "C", "sign": -1, "qty": 1, "entry": 8.0}], spot=312.0, dte_days=21, atm_iv=0.55,
             stock={"shares": 100, "basis": 290.0}),
        dict(legs=AAOI_LEGS, spot=81.0, dte_days=8, atm_iv=0.85),
        dict(legs=AAOI_LEGS + [{"strike": 95, "right": "C", "sign": -1, "qty": 1, "entry": 1.1},
                               {"strike": 105, "right": "C", "sign": 1, "qty": 1, "entry": 0.5}], spot=81.0, dte_days=8, atm_iv=0.85),
    ])
    def test_every_alternative_the_engine_builds_reconciles_its_cash(self, kw):
        m = repair_alternatives(**kw)
        assert m["alternatives"]
        for a in m["alternatives"]:
            assert a["steps"]["reconciled"], (a["name"], a["steps"])
            assert a["all_in"]["best_case"] == a["max_gain"] and a["all_in"]["net_cash_today"] == a["net_cash"]
            if a["category"] != "assignment":
                assert a["steps"]["close_cash"] + a["steps"]["open_cash"] + ((a["steps"]["stock"] or {}).get("cash") or 0) == pytest.approx(a["net_cash"], abs=3 + 0.5 * 6)

    def test_the_wheel_is_let_assign_not_buy_back(self):
        m = repair_alternatives(legs=[{"strike": 105, "right": "P", "sign": -1, "qty": 2, "entry": 2.10}], spot=104.0, dte_days=25, atm_iv=0.28)
        w = next(a for a in m["alternatives"] if a["category"] == "assignment")["steps"]
        assert w["close"] == [] and w["notes"] and "ASSIGNED" in w["notes"][0]

    def test_held_shares_are_not_resold_by_an_option_only_move(self):
        m = repair_alternatives(legs=[{"strike": 320, "right": "C", "sign": -1, "qty": 1, "entry": 8.0}], spot=312.0, dte_days=21,
                                atm_iv=0.55, stock={"shares": 100, "basis": 290.0})
        roll = next(a for a in m["alternatives"] if a["name"].startswith("Roll out at the same"))
        assert roll["steps"]["stock"] is None                                          # untouched, not "sell 100 sh"

    def test_buying_the_cover_lists_the_shares_as_the_only_new_purchase(self):
        m = repair_alternatives(legs=[{"strike": 700, "right": "C", "sign": -1, "qty": 1, "entry": 0.90}], spot=629.5, dte_days=22, atm_iv=0.542)
        c = next(a for a in m["alternatives"] if a["name"].startswith("Cover it"))["steps"]
        assert c["stock"]["action"] == "BUY" and c["stock"]["qty"] == 100 and c["stock"]["cash"] == pytest.approx(-62950, abs=1)
        assert c["close"] == [] and c["open"] == [] and len(c["keep"]) == 1


# ── the print, as the market prices it ───────────────────────────────────────────────────────────
class TestImpliedEarningsMove:
    def test_reads_the_event_off_front_vs_back_variance(self):
        # σ_far²·T − σ_near²·T over the far tenor: (0.92² − 0.80²)·(36/365) = 0.02059 → 14.35%
        assert implied_event_move(0.80, 0.92, 8, 36, 28) == pytest.approx(math.sqrt((0.92 ** 2 - 0.80 ** 2) * 36 / 365) * 100, abs=0.06)

    @pytest.mark.parametrize("args", [
        (0.80, 0.92, 8, 36, 40),     # the print is AFTER the far expiry → the far month doesn't contain it
        (0.80, 0.92, 8, 36, 5),      # …or BEFORE the near expiry → both months carry it, nothing to isolate
        (0.92, 0.80, 8, 36, 28),     # back month cheaper than the front → can't isolate an event
        (None, 0.92, 8, 36, 28), (0.80, 0.92, 8, 36, None),
    ])
    def test_returns_none_when_the_event_cannot_be_isolated(self, args):
        assert implied_event_move(*args) is None

    def test_is_capped_so_a_bad_chain_cannot_print_nonsense(self):
        assert implied_event_move(0.20, 3.0, 1, 60, 10) == 60.0

    def test_phase1_exposes_it_and_the_position_of_the_print(self, flow):
        p1, _ = flow
        assert p1["event"]["position"] == "after" and p1["event"]["days"] == 28 and p1["event"]["implied_move_pct"] == pytest.approx(15.7, abs=1.5)

    def test_a_print_inside_the_trade_is_flagged_inside(self, monkeypatch):
        p1, _ = W.run_flow(monkeypatch, earn_days=21, cur_dte=22)
        assert p1["event"]["position"] == "inside" and p1["event"]["implied_move_pct"] is None


class TestEventRiskScalesWithExposure:
    def _menu(self, em):
        m = repair_alternatives(legs=AAOI_LEGS, spot=81.0, dte_days=8, atm_iv=0.85)
        m["recoverability"].update(posture="marginal", p_touch=62.0, vrp_pct=-6.0, trend_pct=-35.0, iv_pct=85, hv_pct=90)
        m["context"] = {"earnings": {"days": 28, "date": W.iso(28)}}
        m["event"] = {"days": 28, "implied_move_pct": em, "position": "after"}
        return m

    def _points_for(self, m, short, long_):
        legs = [{"action": "SELL", "right": "P", "strike": short, "qty": 1, "dte_days": 36, "price": 3.0},
                {"action": "BUY", "right": "P", "strike": long_, "qty": 1, "dte_days": 36, "price": 1.0}]
        a = {"name": "x", "category": "roll", "group": "adjust", "legs": legs, "net_cash": 10, "max_loss": -900, "max_gain": 100,
             "defined_risk": True, "ev": -250, "d_ev": 0, "d_pop": 0, "pop_pct": 50, "breakevens": [74], "scenarios": [], "roll_meta": None}
        m["alternatives"].append(a)
        tr.rank_defenses(m)
        return a["event_risk"]["points"], a["event_risk"]["note"]

    def test_a_short_at_the_money_of_the_print_is_charged_more_than_one_well_outside_it(self):
        near, _ = self._points_for(self._menu(16.0), 80, 70)
        far, _ = self._points_for(self._menu(16.0), 60, 50)
        assert near > far >= 6.0 and near <= 16.0

    def test_the_note_quotes_the_distance_the_implied_move_and_the_odds(self):
        _, note = self._points_for(self._menu(16.0), 75, 65)
        assert "7.4% below spot" in note and "±16%" in note and "odds the print alone takes it out" in note

    def test_without_an_implied_move_the_floor_applies(self):
        pts, note = self._points_for(self._menu(None), 75, 65)
        assert pts == 6.0 and "wing caps the loss" in note


# ── identify_wing ────────────────────────────────────────────────────────────────────────────────
class TestIdentifyWing:
    def _h(self, *legs):
        return [dict(zip(("right", "sign", "strike", "qty", "entry"), l)) for l in legs]

    def test_a_put_credit_spread(self):
        w = identify_wing(self._h(("P", -1, 80, 1, 2.6), ("P", 1, 70, 1, 2.26)), 81.0)
        assert w["short_strike"] == 80 and w["long_strike"] == 70 and w["width"] == 10 and w["kept"] == [] and w["net_entry"] == pytest.approx(0.34)

    def test_the_TESTED_side_of_a_condor_is_the_wing_and_the_other_side_is_kept(self):
        h = self._h(("P", -1, 80, 1, 2.6), ("P", 1, 70, 1, 2.26), ("C", -1, 120, 1, 1.0), ("C", 1, 130, 1, 0.4))
        w = identify_wing(h, 81.0)
        assert w["right"] == "P" and len(w["kept"]) == 2 and {k["right"] for k in w["kept"]} == {"C"}
        assert identify_wing(h, 119.0)["right"] == "C"                                   # spot near the call side → THAT wing

    @pytest.mark.parametrize("legs", [
        [("P", -1, 80, 1, 2.6)],                                                            # lone short
        [("P", -1, 80, 1, 2.6), ("C", -1, 90, 1, 1.0)],                                     # strangle
        [("P", -1, 80, 1, 2.6), ("P", 1, 90, 1, 5.0)],                                      # DEBIT-oriented (long above short)
        [("P", -1, 80, 2, 2.6), ("P", 1, 70, 1, 2.26)],                                     # ratio — unequal size
        [("P", -1, 80, 1, 2.6), ("P", 1, 70, 1, 2.26), ("P", 1, 60, 1, 1.0)],               # two longs
    ])
    def test_everything_else_is_not_a_clean_wing(self, legs):
        assert identify_wing(self._h(*legs), 81.0) is None


# ── the income policy ────────────────────────────────────────────────────────────────────────────
class TestIncomePolicy:
    def test_a_free_improvement_beats_booking_the_loss_and_says_why(self, flow):
        _, out = flow
        d = out["desk_recommendation"]
        assert d["category"] == "roll" and d["policy"] == "income" and d["policy_over"]["over"].startswith("Hold")
        assert d["reasons"][0].startswith("Income policy:") and "no term for the premium you keep collecting" in d["reasons"][0]
        assert any(r.startswith("Versus holding —") for r in d["reasons"])

    def test_the_pick_is_the_NEAREST_sufficient_extension_not_the_far_OTM_sliver(self, flow):
        _, out = flow
        d = out["desk_recommendation"]
        # Jan 15 65/55 scored a few points higher on P(profit) but collects ~$53 over 92 days at risk — "bank the loss and hide"
        assert d["name"].startswith("Roll the $80/70 put spread → $75/65 · Nov 20")
        pick = next(a for a in out["alternatives"] if a["name"] == d["name"])
        assert pick["roll_meta"]["dte"] == 36

    def test_the_versus_holding_line_quotes_the_all_in_numbers(self, flow):
        _, out = flow
        line = next(r for r in out["desk_recommendation"]["reasons"] if r.startswith("Versus holding —"))
        assert "worst case -966 → -898" in line and "best case +34 → +102" in line and "break-even $79.66 → $73.98" in line and "P(profit) 54% → 58%" in line

    def test_close_is_scored_like_every_other_row_a_loss_forecloses_recovery(self, flow):
        _, out = flow
        close = next(a for a in out["alternatives"] if a["category"] == "exit")
        assert close["score_breakdown"]["recovery"] == 0                                    # P(profit) 0% vs holding 54%
        assert close["score_breakdown"]["risk"] == pytest.approx(100 - 248 / 8000 * 100, abs=1)   # its max loss is the loss it LOCKS IN

    def test_closing_a_winner_is_scored_by_the_same_formula_as_every_other_row(self):
        # banking a gain makes P(profit) 100%; the axis is the CHANGE vs holding (95% here) → 50 + 2×5 = 60, not a flat gift
        m = repair_alternatives(legs=[{"strike": 80, "right": "P", "sign": -1, "qty": 1, "entry": 6.0}], spot=90.0, dte_days=20, atm_iv=0.5)
        m["recoverability"].update(posture="marginal", p_touch=60, vrp_pct=-25, trend_pct=-30)
        tr.rank_defenses(m)
        close = next(a for a in m["alternatives"] if a["category"] == "exit")
        hold_pop = next(a for a in m["alternatives"] if a["category"] == "hold")["pop_pct"]
        assert close["ev"] > 0 and close["score_breakdown"]["recovery"] == min(100, round(50 + 2 * (100 - hold_pop)))
        assert close["score_breakdown"]["recovery"] > 50                                    # a gain locked in IS an improvement

    def test_hold_stays_the_pick_when_no_income_viable_fix_exists(self, monkeypatch):
        p1, out = W.run_flow(monkeypatch, earn_days=21, cur_dte=22)                          # no even-cost roll exists
        d = out["desk_recommendation"]
        assert d["category"] in ("hold", "exit") and d["policy"] is None
        if d["category"] == "hold":
            assert any("No fix clears the bar" in r for r in d["reasons"])

    def test_a_fix_that_costs_money_must_beat_closing_clearly(self):
        # naked call: the only "fixes" are a debit wing or a worse-odds roll-down → Close keeps the benefit of a coin-flip
        m = repair_alternatives(legs=[{"strike": 700, "right": "C", "sign": -1, "qty": 1, "entry": 0.90}], spot=629.5, dte_days=22, atm_iv=0.542)
        m["recoverability"].update(posture="marginal", p_touch=44.7, vrp_pct=-19.4, trend_pct=723.4, iv_pct=54.2, hv_pct=67.2)
        tr.drop_plain_rolls(m)
        tr.rank_defenses(m)
        d = m["desk_recommendation"]
        assert d["category"] == "exit" and d["policy"] is None
        assert d["reasons"][0].startswith("Closing scores") and "has to beat closing clearly" in d["reasons"][0]

    def test_the_policy_never_overrides_a_healthy_trade(self):
        m = repair_alternatives(legs=AAOI_LEGS, spot=95.0, dte_days=30, atm_iv=0.5)
        m["recoverability"].update(posture="healthy", p_touch=5, vrp_pct=5, trend_pct=3)
        tr.drop_plain_rolls(m)
        tr.rank_defenses(m)
        assert m["desk_recommendation"]["category"] == "hold" and m["desk_recommendation"]["policy"] is None

    def test_a_viable_fix_that_is_genuinely_worse_is_not_forced(self):
        m = repair_alternatives(legs=AAOI_LEGS, spot=81.0, dte_days=8, atm_iv=0.85)
        m["recoverability"].update(posture="marginal", p_touch=62, vrp_pct=-6, trend_pct=-35)
        hold = next(a for a in m["alternatives"] if a["category"] == "hold")
        bad = {"name": "Bad roll", "category": "roll", "group": "adjust", "legs": [], "net_cash": 10, "max_loss": hold["max_loss"], "max_gain": 100,
               "defined_risk": True, "ev": hold["ev"] - 99, "d_ev": -99, "d_pop": -2, "pop_pct": hold["pop_pct"] - 2, "breakevens": [], "scenarios": [],
               "roll_meta": {"dte": 30, "expiry": "2026-11-14", "strike": 70.0, "credit_total": 10, "new_breakeven": None, "p_otm": None,
                     "p_otm_source": None, "scores": {"structure": 0, "cushion": 0}, "structure": {}}}
        m["alternatives"].append(bad)
        tr.drop_plain_rolls(m)
        tr.rank_defenses(m)
        # it passes every viability gate (credit, defined, ΔEV just inside the tolerance, odds −2pt) yet scores far below Hold
        assert bad["desk_score"] < next(a for a in m["alternatives"] if a["category"] == "hold")["desk_score"] - tr.INCOME_VIABLE_SLACK
        assert m["desk_recommendation"]["name"] != "Bad roll"


# ── when to act ──────────────────────────────────────────────────────────────────────────────────
class TestTiming:
    def test_the_print_after_this_expiry_gives_a_window_a_deadline_and_the_implied_move(self, flow):
        _, out = flow
        t = out["timing"]
        assert t["verdict"] == "before_print" and t["act_by"] == W.iso(27) and "Nov 11" in t["title"] and "earnings Nov 12" in t["title"]
        joined = " ".join(t["points"])
        assert "land AFTER this expiry" in joined and "±16%" in joined
        assert "Through the print: Nov 20" in joined and "INSIDE the move" in joined         # the PICK's $75 short sits 7.4% out vs ±16%
        assert t["vol"]["implied_move_pct"] == pytest.approx(15.7, abs=1.5) and t["vol"]["earnings_days"] == 28

    def test_it_names_a_pre_print_roll_when_one_clears_the_bar(self, monkeypatch):
        # a deeper-ITM spot gives an even-cost roll that ends before the print
        p1, out = W.run_flow(monkeypatch, earn_days=36, cur_dte=8)
        rolls = [a for a in out["alternatives"] if a.get("roll_meta")]
        pre = [a for a in rolls if a["roll_meta"]["dte"] < 36]
        if pre:
            assert any(p.startswith("Cleanest:") for p in out["timing"]["points"])
        else:
            assert any("No roll that ends before the print clears the bar" in p for p in out["timing"]["points"])

    def test_a_print_inside_the_trade_says_defend_BEFORE_it(self, monkeypatch):
        _, out = W.run_flow(monkeypatch, earn_days=21, cur_dte=22)
        t = out["timing"]
        assert t["verdict"] == "before_print" and "Defend before the print" in t["title"] and "INSIDE this trade's life" in " ".join(t["points"])

    def test_acute_pressure_overrides_the_window_with_act_now(self, monkeypatch):
        _, out = W.run_flow(monkeypatch, earn_days=28, cur_dte=8, p_touch=92.0)
        t = out["timing"]
        assert t["verdict"] == "now" and "Act now" in t["title"] and any("P(touch) 92%" in p for p in t["points"])
        assert any("land AFTER this expiry" in p for p in t["points"])                        # the print is still mentioned

    def test_cheap_premium_says_it_is_a_poor_time_to_resell(self, monkeypatch):
        _, out = W.run_flow(monkeypatch, earn_days=28, cur_dte=8, earnings=False, vrp=-25.0)
        t = out["timing"]
        assert t["verdict"] == "vol_cheap" and "Poor time to re-sell" in t["title"] and any("poorly paid" in p for p in t["points"])

    def test_rich_premium_says_roll_now(self, monkeypatch):
        _, out = W.run_flow(monkeypatch, earn_days=28, cur_dte=8, earnings=False, vrp=18.0, p_touch=60.0)
        assert out["timing"]["verdict"] == "vol_rich" and "Good time to roll" in out["timing"]["title"]

    def test_a_quiet_marginal_trade_has_no_timing_edge_and_says_so(self, monkeypatch):
        _, out = W.run_flow(monkeypatch, earn_days=28, cur_dte=8, earnings=False, vrp=0.0, p_touch=58.0)
        assert out["timing"]["verdict"] == "neutral" and out["timing"]["act_by"] is None

    def test_every_payload_carries_a_timing_read(self, flow):
        p1, out = flow
        assert p1["timing"]["verdict"] and out["timing"]["points"] and isinstance(out["timing"]["points"], list)


# ── the screen must not contradict itself (each of these was a real wording bug on the AAOI screen) ──
class TestNoSelfContradiction:
    def test_hold_as_the_FALLBACK_describes_what_it_keeps_not_that_no_fix_clears_the_bar(self, flow):
        _, out = flow
        d = out["desk_recommendation"]
        assert d["category"] == "roll" and d["runner_up"]["name"].startswith("Hold")
        txt = " ".join(d["runner_up"]["reasons"])
        assert "No fix clears the bar" not in txt                       # the pick IS a fix — that sentence is false here
        assert txt.startswith("Do nothing:") and "worst case -966" in txt and "break-even stays $79.66" in txt and "P(profit) stays 54%" in txt

    def test_hold_as_the_PICK_still_says_no_fix_clears_the_bar(self, monkeypatch):
        _, out = W.run_flow(monkeypatch, earn_days=21, cur_dte=22)
        d = out["desk_recommendation"]
        assert d["category"] == "hold" and d["reasons"][0].startswith("No fix clears the bar")

    def test_timing_is_candid_that_the_pick_sells_the_print_from_inside_the_move(self, flow):
        _, out = flow
        line = next(p for p in out["timing"]["points"] if p.startswith("Through the print"))
        assert "INSIDE the move" in line and "odds the print alone takes it out" in line and "price of the even-cost roll" in line
        assert "risk is defined" in line
        # …and it points at the roll that does clear the move, with its (smaller) credit, instead of contradicting the pick
        assert "A roll that clears the move exists" in line and "pays less" in line
        assert "Only worth it with the short outside" not in " ".join(out["timing"]["points"])   # the old line that argued against the pick

    def test_the_pick_reasons_do_not_repeat_what_the_steps_block_already_shows(self, flow):
        _, out = flow
        r = out["desk_recommendation"]["reasons"]
        assert any(x.startswith("Versus holding —") for x in r)
        assert not any(x.startswith("net credit") for x in r)           # the steps block shows net cash and the legs
        assert not any(x.startswith("max loss") or x.startswith("recovery odds") for x in r)   # the "versus holding" line carries them
        assert any(x.startswith("Expected P&L") and "fair-value re-timing" in x for x in r)     # …and says honestly where the gain is
        assert any("P(OTM)" in x for x in r)

    def test_a_runner_up_that_is_a_roll_keeps_its_own_numbers(self, monkeypatch):
        _, out = W.run_flow(monkeypatch, earn_days=28, cur_dte=8)
        # the other rolls are listed with the standard bullets (only the PICK gets the condensed all-in line)
        other = next(a for a in out["alternatives"] if a.get("roll_meta") and a["name"] != out["desk_recommendation"]["name"])
        assert other["steps"]["reconciled"] and other["roll_meta"]["wing"]["width"] == 10.0


class TestCoinFlipIsSaidOutLoud:
    def test_hold_vs_close_within_a_few_points_is_called_a_coin_flip_with_what_it_depends_on(self, monkeypatch):
        _, out = W.run_flow(monkeypatch, earn_days=21, cur_dte=22)             # print inside, no even-cost roll: Hold 54 vs Close 53
        d = out["desk_recommendation"]
        assert d["category"] == "hold"
        line = next(r for r in d["reasons"] if r.startswith("Hold and Close are a coin-flip"))
        assert "54 vs 53" in line and "same expected P&L" in line and "appetite" in line and "48% chance" in line

    def test_a_clear_gap_is_not_dressed_up_as_a_coin_flip(self, flow):
        _, out = flow
        assert not any("coin-flip" in r for r in out["desk_recommendation"]["reasons"])

    def test_a_healthy_trade_never_gets_it(self, monkeypatch):
        _, out = W.run_flow(monkeypatch, earn_days=28, cur_dte=8, posture="healthy", p_touch=8.0, vrp=5.0, trend=3.0)
        assert not any("coin-flip" in r for r in out["desk_recommendation"]["reasons"])


class TestWarRoomSeesTheSameStory:
    """The LLM war room is fed the desk payload; if it can't see the timing / earnings / income policy it can recommend Close right
    after the desk said otherwise — the "two voices on one screen" problem. The LLM call is stubbed; the PROMPT is what's under test."""

    def _prompt(self, monkeypatch, payload):
        import asyncio
        import json
        from types import SimpleNamespace
        import app.routers.saved_strategy_router as router
        seen = {}

        async def fake_llm(**kw):
            seen["messages"] = kw["messages"]
            return json.dumps({"quant": {}, "risk": {}, "pm": {}, "verdict": {"primary_action": "x", "why": "y", "alternates": []}})

        async def fake_key(*a, **k):
            return "k"

        class Res:
            def scalar_one_or_none(self):
                return SimpleNamespace(id=1, ticker="AAOI")

        class DB:
            async def execute(self, *a, **k):
                return Res()

        monkeypatch.setattr(router, "call_llm", fake_llm)
        monkeypatch.setattr(router, "get_user_api_key", fake_key)
        asyncio.run(router.run_defend_committee(strategy_id=1, body=router.DefendPayloadIn(defend=payload), quote_source="yfinance",
                                                user=SimpleNamespace(id=1), db=DB()))
        return seen["messages"][0]["content"], seen["messages"][1]["content"]

    def test_the_context_carries_timing_earnings_and_the_income_policy_reasoning(self, monkeypatch, flow):
        _, out = flow
        sys_p, user_p = self._prompt(monkeypatch, out)
        assert "WHEN TO ACT (already computed): [before_print] Window: now → Nov 11" in user_p
        assert "EARNINGS: earnings " in user_p and "AFTER this expiry" in user_p and "±15.7% move" in user_p
        assert "Income policy: doing nothing scores 60" in user_p                    # the pick's own reasoning, verbatim
        assert "Roll the $80/70 put spread → $75/65 · Nov 20" in user_p

    def test_the_prompt_tells_the_pm_this_is_an_income_desk(self, monkeypatch, flow):
        _, out = flow
        sys_p, _ = self._prompt(monkeypatch, out)
        assert "This is an INCOME desk" in sys_p and "do NOT recommend Close just because it scores higher" in sys_p
        assert "or close. This is an INCOME desk" in sys_p and "You are given a DETERMINISTIC DESK PICK" in sys_p      # the splice reads as English

    def test_a_payload_without_timing_or_event_still_builds(self, monkeypatch, flow):
        _, out = flow
        slim = {k: v for k, v in out.items() if k not in ("timing", "event")}
        _, user_p = self._prompt(monkeypatch, slim)
        assert "WHEN TO ACT (already computed): n/a" in user_p and "EARNINGS: none known" in user_p
