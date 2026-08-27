"""Tests for the institutional multi-leg trade-repair engine (BS-priced, greeks, defined-risk)."""
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
        # capping BOTH tails must give a far shallower max loss than the naked roll-down
        assert condor["max_loss"] > _by(r, "Roll down")["max_loss"]

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
        assert any("Roll up" in a["name"] for a in r["alternatives"])
        assert any("Reverse-jade" in a["name"] for a in r["alternatives"])
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
        # Every option spread in every alternative must have DISTINCT strikes with real width.
        chain = _coarse_chain(104.0, [x * 5 for x in range(14, 26)])   # strikes every $5
        r = repair_alternatives(legs=[{"strike": 105, "right": "P", "sign": -1, "qty": 2, "entry": 2.10}],
                                spot=104.0, dte_days=25, atm_iv=0.5, chains=chain)
        for a in r["alternatives"]:
            opt = [lg for lg in a["legs"] if lg["right"] in ("P", "C")]
            for right in ("P", "C"):
                strikes = [lg["strike"] for lg in opt if lg["right"] == right and lg["dte_days"]]
                assert len(strikes) == len(set(strikes)), f"{a['name']} has duplicate {right} strikes {strikes}"

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
