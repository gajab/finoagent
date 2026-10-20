"""Single-leg short call breakeven: covered (stock + short call) vs NAKED (no stock) — different risk sides."""
from app.services.derivative_income_service import _single_leg_income
from app.services.quote_providers.base import OptionQuote


def _call(covered: bool):
    q = OptionQuote(strike=78.0, right="C", expiration="2026-10-23", bid=0.65, ask=0.75, last=0.70, mid=0.70,
                    iv=0.95, oi=5000, volume=1200)
    return _single_leg_income(
        "covered_call", "Covered Call", q, spot=69.39, dte=4, exp="2026-10-23", rnd=None, r=0.04,
        sofr_pct=3.9, atm_iv=0.95, iv_hv_ratio=1.5, richness="rich", european=False, earnings_before=None,
        macro=[], min_prob=0.0, min_income=0.0, ticker="NFLX", allow_illiquid=True, covered=covered)


def test_naked_call_breakeven_is_strike_plus_premium():
    o = _call(covered=False)
    assert o["capital_basis"] == "reg_t_margin"
    assert o["breakeven"] == 78.70          # was 68.66 (spot − premium, the covered formula)


def test_covered_call_breakeven_is_spot_minus_premium():
    o = _call(covered=True)
    assert o["capital_basis"] == "covered_stock"
    assert o["breakeven"] == 68.69
