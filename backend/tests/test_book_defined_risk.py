"""Manage Book must respect protection: a defined-risk spread / iron condor is not 'naked', its capital is the spread width,
and a fix must never tell you to buy a wing you already hold (real case: an AAOI iron condor was told to buy its own 70P)."""
import pytest

import app.services.book_tail_risk as bt
from app.services.book_tail_risk import _committed_capital, _remediate_position, _offender_targets
from test_book_covered_and_fixes import _pos, _leg

R = 0.045


def _l(strike, right, sign, qty=1, bid=None, ask=None, iv=0.8, dte=18, exp="2026-10-30"):
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    return _leg(strike, right, sign, qty, iv=iv, dte=dte, exp=exp, bid=bid, ask=ask, mid=mid)


def _aaoi():
    # the user's actual position: short 80P / long 70P  +  short 155C / long 160C, Oct 30, quotes as on their screen
    legs = [_l(80, "P", -1, bid=0.75, ask=1.00), _l(70, "P", +1, bid=0.0, ask=0.90),
            _l(160, "C", +1, bid=0.15, ask=0.50), _l(155, "C", -1, bid=0.30, ask=0.60)]
    p = _pos(legs, spot=115.0, ticker="AAOI", beta=3.0, structure="options_spread",
             strikes={"2026-10-30": {"P": [60.0, 65.0, 70.0, 75.0, 80.0], "C": [155.0, 160.0, 165.0]}})
    p["capital"] = _committed_capital(legs)
    return p


class TestCommittedCapital:
    def test_an_iron_condor_ties_up_its_wing_widths_not_the_short_strike_notional(self):
        legs = _aaoi()["legs"]
        assert _committed_capital(legs) == (80 - 70) * 100 + (160 - 155) * 100 == 1500          # was $23,500 (80×100 + 155×100)

    def test_naked_shorts_are_still_cash_secured_at_strike_notional(self):
        assert _committed_capital([_l(400, "C", -1, qty=4)]) == 160000
        assert _committed_capital([_l(90, "P", -1, qty=2)]) == 18000

    def test_a_credit_spread_in_lots_is_width_times_contracts(self):
        assert _committed_capital([_l(95, "P", -1, qty=3), _l(85, "P", +1, qty=3)]) == 10 * 100 * 3

    def test_a_partly_protected_short_is_width_for_the_covered_part_and_notional_for_the_rest(self):
        assert _committed_capital([_l(95, "P", -1, qty=3), _l(85, "P", +1, qty=1)]) == 10 * 100 * 1 + 95 * 100 * 2

    def test_a_debit_spread_is_its_width(self):
        assert _committed_capital([_l(90, "P", +1), _l(80, "P", -1)]) == 1000


class TestProtectedFix:
    def test_a_condors_put_side_is_never_told_to_buy_the_wing_it_already_holds(self):
        rem = _remediate_position(_aaoi(), "down", R)
        assert rem["protected"] is True
        opts = [rem["recommended"]] + ([rem["alt"]] if rem["alt"] else [])
        assert all(o["action"] != "cap" for o in opts)                                  # no "BUY 70P (vs your short 80P)"
        assert not any("BUY" in o["legs"] and "70P" in o["legs"] for o in opts)
        rec = rem["recommended"]
        assert rec["action"] == "close" and "CLOSE the 80/70P put spread" in rec["legs"] and "already defined-risk" in rec["detail"]

    def test_closing_a_spread_buys_the_short_and_sells_the_long_at_the_bid(self):
        rec = _remediate_position(_aaoi(), "down", R)["recommended"]
        assert rec["cost"] == round(1.00 * 100 - 0.0 * 100, 0)                           # short ask $1.00 in; the 70P has NO bid → worth $0 out
        p = _aaoi()
        assert round(_remediate_position({**p, "legs": [l if l["strike"] != 70 else {**l, "bid": 0.30} for l in p["legs"]]}, "down", R)["recommended"]["cost"]) == round(100 - 30)

    def test_closing_removes_that_side_so_the_tail_after_is_not_a_fake_profit(self):
        rem = _remediate_position(_aaoi(), "down", R)
        assert rem["tail_before"] < 0 and rem["recommended"]["tail_after"] <= 0 + 50       # never the old +$1,956 "after fix"
        assert abs(rem["tail_before"]) <= 1000                                              # a $10-wide spread can lose at most ~$1,000

    def test_the_call_side_is_protected_too(self):
        rem = _remediate_position(_aaoi(), "up", R)
        assert rem["protected"] is True and "CLOSE the 155/160C call spread" in rem["recommended"]["legs"]

    def test_a_naked_short_still_gets_a_cap_and_a_close(self):
        naked = _pos([_l(80, "P", -1, bid=0.75, ask=1.0)], spot=115.0, ticker="N", beta=3.0,
                     strikes={"2026-10-30": {"P": [60.0, 65.0, 70.0, 75.0, 80.0], "C": []}})
        rem = _remediate_position(naked, "down", R)
        assert rem["protected"] is False
        acts = {rem["recommended"]["action"], (rem["alt"] or {}).get("action")}
        assert acts == {"cap", "close"}

    def test_a_partly_protected_short_buys_only_the_uncovered_remainder(self):
        legs = [_l(80, "P", -1, qty=3, bid=0.75, ask=1.0), _l(70, "P", +1, qty=1, bid=0.2, ask=0.4)]
        p = _pos(legs, spot=115.0, ticker="P", beta=3.0, strikes={"2026-10-30": {"P": [60.0, 65.0, 70.0, 75.0, 80.0], "C": []}})
        rem = _remediate_position(p, "down", R)
        assert rem["protected"] is False
        cap = next(o for o in [rem["recommended"], rem["alt"]] if o and o["action"] == "cap")
        assert cap["legs"].startswith("BUY 2×")                                              # 3 short − 1 long = 2 uncovered


def test_naked_risk_is_offered_before_a_defined_risk_spread_even_when_the_spread_loses_more():
    condor = _aaoi()
    naked = _pos([_l(100, "P", -1, qty=1, bid=0.5, ask=0.7, iv=0.5)], spot=115.0, ticker="NKD", beta=1.0,
                 strikes={"2026-10-30": {"P": [70.0, 80.0, 90.0, 100.0], "C": []}})
    out = _offender_targets([condor, naked], "down", R, top=2)
    assert out[0]["ticker"] == "NKD" and out[0]["protected"] is False
    assert out[1]["ticker"] == "AAOI" and out[1]["protected"] is True


def test_a_condor_no_longer_dominates_a_sectors_capital_concentration():
    legs = _aaoi()["legs"]
    old_basis = 80 * 100 + 155 * 100                       # short strikes at full notional
    assert _committed_capital(legs) < old_basis / 10


def test_a_small_defined_risk_spread_is_not_offered_as_a_fix_but_a_material_one_is():
    condor = _aaoi()                                                   # worst case ≈ $936 on a ~$700k book → noise
    big = _pos([_l(80, "P", -1, qty=60, bid=0.75, ask=1.0), _l(70, "P", +1, qty=60, bid=0.1, ask=0.3)], spot=115.0, ticker="BIG", beta=3.0,
               strikes={"2026-10-30": {"P": [60.0, 65.0, 70.0, 75.0, 80.0], "C": []}})
    big["capital"] = _committed_capital(big["legs"])
    filler = _pos([_l(100, "P", -1, bid=0.5, ask=0.7)], spot=115.0, ticker="F", beta=1.0, strikes={"2026-10-30": {"P": [70.0, 80.0, 90.0, 100.0], "C": []}})
    filler["capital"] = 700000.0                                       # a book of ~$700k
    small = _offender_targets([condor, filler], "down", R, top=3)
    assert [t["ticker"] for t in small] == ["F"]                       # the $1.5k condor is dropped
    material = _offender_targets([big, filler], "down", R, top=3)
    assert {t["ticker"] for t in material} == {"BIG", "F"} and next(t for t in material if t["ticker"] == "BIG")["protected"]


# ── Deployed (Reg-T) is the SAME number My Trades shows, shown next to the %-limit capital ──────────────
class TestRegTDeployed:
    def _calc(self, legs, spot):
        from app.routers.saved_strategy_router import calc_reg_t_margin
        return calc_reg_t_margin([{"right": l["right"], "action": "SELL" if l["sign"] < 0 else "BUY", "strike": l["strike"], "qty": l["qty"]} for l in legs], spot, "reg_t")

    def test_an_option_position_deploys_the_apps_single_reg_t_margin(self):
        p = _aaoi()
        got = bt._reg_t_deployed(p, {}, 115.0)
        assert got == round(self._calc(p["legs"], 115.0), 0) and got > 0

    def test_a_defined_risk_condor_deploys_far_less_than_a_naked_short_of_the_same_strikes(self):
        condor = _aaoi()
        naked = _pos([_l(80, "P", -1, bid=0.75, ask=1.0)], spot=115.0)
        assert bt._reg_t_deployed(condor, {}, 115.0) < bt._reg_t_deployed(naked, {}, 115.0) * 2

    def test_a_stock_backed_trade_deploys_the_stock_held_like_my_trades(self):
        cc = _pos([_l(65, "C", -1)], shares=100, structure="covered_call")
        assert bt._reg_t_deployed(cc, {"shares": 100, "avg_cost": 58.0}, 60.0) == 5800

    def test_a_margin_failure_never_breaks_the_desk(self):
        assert bt._reg_t_deployed({"legs": [{"bad": 1}]}, {}, 60.0) is None


def test_the_book_reports_deployed_reg_t_as_the_sum_of_its_positions(monkeypatch):
    import asyncio, json
    from types import SimpleNamespace
    import app.services.derivative_income_service as dis
    import app.services.quote_providers as qp
    from test_book_hedge_plan import _Provider, _call_book
    book = _call_book(2)
    for i, p in enumerate(book):
        p["trade_id"] = i + 1; p["reg_t"] = 1000.0 * (i + 1); p["iv_audit"] = {"chain": 1, "solved": 0, "fallback": 0}; p["indicative"] = False
    by_id = {p["trade_id"]: p for p in book}
    async def fake_pos(strategy, *a, **k): return dict(by_id[strategy.id])
    async def fake_none(*a, **k): return [], None
    async def fake_factor(*a, **k): return None
    async def fake_sofr(): return (R, None)
    monkeypatch.setattr(bt, "_position_greeks", fake_pos); monkeypatch.setattr(bt, "_compute_betas", lambda tk: {t: 1.4 for t in tk})
    monkeypatch.setattr(bt, "_index_puts", fake_none); monkeypatch.setattr(bt, "_vix_calls", fake_none)
    monkeypatch.setattr(bt, "_vix_levels", lambda: (18.0, 19.0)); monkeypatch.setattr(bt, "_book_factor_exposure", fake_factor)
    monkeypatch.setattr(dis, "_get_sofr", fake_sofr); monkeypatch.setattr(qp, "get_provider", lambda *a, **k: _Provider())
    out = asyncio.run(bt.compute_book_tail_risk([SimpleNamespace(id=1), SimpleNamespace(id=2)], "yfinance", SimpleNamespace(id=1), None))
    assert out["deployed_reg_t"] == 3000
    assert sorted(n["reg_t"] for n in out["concentration_view"]["names"]) == [1000, 2000]
    json.dumps(out)
