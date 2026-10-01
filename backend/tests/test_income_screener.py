"""Income Screener — trade selection (≤4/stock: nearest + farthest per side, A/B only) and expiry gating."""
from datetime import date, timedelta

from app.services.income_screener_service import _pick_expiry, select_trades


def _o(structure, strike, grade, score=70):
    return {"structure": structure, "short_strike": strike, "algo_grade": grade, "desk_score": score}


def test_select_nearest_and_farthest_per_side_ab_only():
    spot = 100.0
    ranked = [
        _o("naked_call", 105, "A"), _o("naked_call", 110, "B"), _o("naked_call", 120, "B"),
        _o("naked_call", 103, "C"),                       # nearer but C → excluded
        _o("cash_secured_put", 95, "B"), _o("cash_secured_put", 85, "A"),
        _o("cash_secured_put", 80, "F"),                  # farther but F → excluded
        _o("iron_condor", 100, "A"),                      # other structures ignored
    ]
    picks = select_trades(ranked, spot, {"A", "B"})
    assert len(picks) == 4
    got = {(p["side"], p["pick"]): p["short_strike"] for p in picks}
    assert got == {("call", "nearest"): 105, ("call", "farthest"): 120,
                   ("put", "nearest"): 95, ("put", "farthest"): 85}


def test_single_qualifier_per_side_gives_one_trade():
    picks = select_trades([_o("naked_call", 110, "A"), _o("cash_secured_put", 90, "D")], 100.0, {"A", "B"})
    assert [(p["side"], p["pick"]) for p in picks] == [("call", "nearest")]


def test_pick_expiry_window_and_earnings():
    today = date.today()
    exps = [(today + timedelta(days=d)).isoformat() for d in (5, 12, 20, 33, 60)]
    (exp, dte), why = _pick_expiry(exps, 10, 40, None)
    assert 10 <= dte <= 40 and why is None
    # earnings in 15 days → only the 12-DTE expiry settles before the print
    (exp, dte), _ = _pick_expiry(exps, 10, 40, today + timedelta(days=15))
    assert dte == 12
    none, why = _pick_expiry(exps, 10, 40, today + timedelta(days=11))
    assert none is None and "earnings" in why
    none, why = _pick_expiry(exps, 70, 90, None)
    assert none is None and "No listed expiry" in why
