"""Regression: the PLACED-trade focus for a short strangle must price the USER'S EXACT
strikes, not re-derive a near-ATM band.

The bug: `_build_focus_opp` dispatched short_strangle to `_short_strangle`, which derives
its OWN near-delta strikes. So scoring a held far-OTM GLD 300/470 strangle silently scored
a ~ATM strangle → keep-prob ~50%, cushion ~0, and the management read cratered to
STRONG_CLOSE 0/100. `_focus_strangle` prices the exact legs instead.
"""
from app.services.quote_providers.base import OptionQuote
from app.services.derivative_income_service import _focus_strangle


def _q(strike, right, mid, iv):
    return OptionQuote(strike=strike, right=right, expiration="2026-09-18",
                       bid=mid * 0.9, ask=mid * 1.1, last=mid, mid=mid, iv=iv, oi=200, volume=20)


class TestFocusStrangle:
    # GLD ~ 399.54; the user's real trade: short 300 put + short 470 call, both far OTM.
    SPOT = 399.54

    def _opp(self, put_k=300.0, call_k=470.0):
        return _focus_strangle(
            _q(put_k, "P", 0.17, 0.27), _q(call_k, "C", 1.05, 0.22),
            spot=self.SPOT, dte=42, exp="2026-09-18", rnd=None, r=0.045, atm_iv=0.236,
            iv_hv_ratio=1.0, richness="fair", european=False, ticker="GLD")

    def test_prices_the_users_exact_strikes_not_a_re_derived_band(self):
        o = self._opp()
        assert o["band_low"] == 300.0 and o["band_high"] == 470.0   # the trade the user HOLDS
        assert o["put_short"] == 300.0 and o["call_short"] == 470.0

    def test_far_otm_strangle_has_high_keep_prob(self):
        # 300/470 around spot 399.5 → the band is wide; keep-prob (finish in band) must be high,
        # NOT the ~50% a mis-derived ATM band produced.
        o = self._opp()
        assert o["prob_keep_pct"] >= 85.0

    def test_cushion_is_the_nearest_breach_and_positive_while_both_otm(self):
        o = self._opp()
        # min(spot-put, call-spot)/spot = min(99.5, 70.5)/399.5 ≈ 17.6%, positive (not a false "tested").
        assert o["cushion_pct"] > 10.0
        assert abs(o["cushion_pct"] - (min(self.SPOT - 300.0, 470.0 - self.SPOT) / self.SPOT * 100)) < 0.1

    def test_naked_strangle_reports_undefined_downside_and_margin_capital(self):
        o = self._opp()
        assert o["max_loss"] is None                 # undefined (naked both wings)
        assert o["collateral"] > 0                    # Reg-T naked margin, not cash-secured
        assert o["premium"] > 0

    def test_missing_leg_returns_none(self):
        assert _focus_strangle(None, _q(470.0, "C", 1.05, 0.22), spot=self.SPOT, dte=42,
                               exp="2026-09-18", rnd=None, r=0.045, atm_iv=0.236,
                               iv_hv_ratio=1.0, richness="fair", european=False, ticker="GLD") is None
