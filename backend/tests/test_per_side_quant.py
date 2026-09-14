"""Tests for per_side_quant — the call/put leg-tab decomposition.

The load-bearing guarantee here is the USER-TRUST INVARIANT: any per-leg value the UI
shows MUST sum to its trade-level aggregate. So the two sides' greeks and premium have to
reconcile to the whole trade, and the NON-additive ratios (Omega/Sortino/POP/CVaR) must be
absent from the side blocks (they stay trade-level only). We also prove single-type
structures return None (no side tabs), and that the binding short is the nearest-spot leg.

Run from backend/:
    python -m pytest tests/test_per_side_quant.py -v
"""

import pytest

from app.services.desk_review_service import per_side_quant
from app.services.lifecycle_service import higher_order_greeks
from app.services.desk_review_service import (
    _opp_legs, _factor_scope, _factor_dimension, _merge_scoped, _side_max_loss,
    _FACTOR_TAXONOMY,
)


SPOT = 100.0


def _leg(strike, typ, action, iv=30.0, mid=2.0, delta=0.30):
    return {"strike": strike, "type": typ, "action": action, "iv": iv, "mid": mid, "delta": delta}


def _strangle(**over):
    """A short strangle: SELL 110 call + SELL 90 put, 30 DTE, spot 100."""
    opp = dict(
        structure="short_strangle", dte=30, atm_iv_pct=30.0, contracts=1,
        legs=[_leg(110, "call", "SELL", iv=28.0, mid=1.8, delta=0.30),
              _leg(90, "put", "SELL", iv=34.0, mid=2.1, delta=-0.28)],
    )
    opp.update(over)
    return opp


class TestReconciliation:
    def test_two_sides_present_for_strangle(self):
        ps = per_side_quant(_strangle(), SPOT)
        assert ps is not None
        assert [s["key"] for s in ps["sides"]] == ["call", "put"]
        assert all(s["legs_n"] == 1 for s in ps["sides"])

    def test_side_greeks_sum_to_trade_greeks(self):
        opp = _strangle()
        ps = per_side_quant(opp, SPOT)
        whole = higher_order_greeks(_opp_legs(opp), SPOT)
        for key in ("net_delta", "net_gamma", "net_vega", "net_theta"):
            side_sum = round(sum(s["greeks"][key] for s in ps["sides"]), 2)
            # the sides are computed independently, then rounded — allow a hair of rounding slack
            assert side_sum == pytest.approx(whole[key], abs=0.05), key
            # and the helper's own reconcile block agrees with higher_order_greeks
            assert ps["reconcile"][key] == pytest.approx(whole[key], abs=0.01), key

    def test_premium_reconciles_and_is_a_credit(self):
        ps = per_side_quant(_strangle(), SPOT)
        # both legs are SHORT → each side collects a positive credit
        assert all(s["premium"] > 0 for s in ps["sides"])
        total = round(sum(s["premium"] for s in ps["sides"]), 2)
        assert total == pytest.approx(ps["reconcile"]["premium"], abs=0.01)
        # 1.8 + 2.1 collected × 100 = 390
        assert total == pytest.approx(390.0, abs=0.01)

    def test_no_nonadditive_ratios_leak_into_sides(self):
        ps = per_side_quant(_strangle(), SPOT)
        for s in ps["sides"]:
            for banned in ("omega", "sortino", "calmar", "pop", "cvar_95", "desk_score", "score"):
                assert banned not in s, f"{banned} must stay trade-level, not per-side"


class TestBindingSideReads:
    def test_breach_and_moneyness_come_from_the_short_on_each_side(self):
        ps = per_side_quant(_strangle(), SPOT)
        by = {s["key"]: s for s in ps["sides"]}
        assert by["call"]["short_strike"] == 110
        assert by["put"]["short_strike"] == 90
        # both shorts sit 10% from spot → cushion ~10%, a real (0,100) touch probability
        for k in ("call", "put"):
            assert by[k]["moneyness"]["cushion_pct"] == pytest.approx(10.0, abs=0.1)
            pt = by[k]["breach"]["prob_touch_pct"]
            assert pt is not None and 0.0 < pt < 100.0

    def test_binding_short_is_nearest_spot_leg_on_a_condor(self):
        # iron condor: short 110c / long 120c, short 90p / long 80p — binding shorts are 110 & 90
        opp = dict(
            structure="iron_condor", dte=30, atm_iv_pct=30.0, contracts=1,
            legs=[_leg(110, "call", "SELL", mid=1.8, delta=0.30),
                  _leg(120, "call", "BUY", mid=0.7, delta=0.15),
                  _leg(90, "put", "SELL", mid=2.1, delta=-0.28),
                  _leg(80, "put", "BUY", mid=0.9, delta=-0.14)],
        )
        ps = per_side_quant(opp, SPOT)
        by = {s["key"]: s for s in ps["sides"]}
        assert by["call"]["short_strike"] == 110 and by["call"]["legs_n"] == 2
        assert by["put"]["short_strike"] == 90 and by["put"]["legs_n"] == 2
        # defined-risk wings still reconcile
        whole = higher_order_greeks(_opp_legs(opp), SPOT)
        assert round(sum(s["greeks"]["net_delta"] for s in ps["sides"]), 2) == pytest.approx(whole["net_delta"], abs=0.05)


class TestSingleTypeStructuresGetNoTabs:
    def test_naked_put_returns_none(self):
        opp = dict(structure="cash_secured_put", dte=30, atm_iv_pct=30.0, contracts=1,
                   legs=[_leg(90, "put", "SELL", mid=2.1, delta=-0.28)])
        assert per_side_quant(opp, SPOT) is None

    def test_call_vertical_returns_none(self):
        opp = dict(structure="call_credit_spread", dte=30, atm_iv_pct=30.0, contracts=1,
                   legs=[_leg(110, "call", "SELL", mid=1.8, delta=0.30),
                         _leg(120, "call", "BUY", mid=0.7, delta=0.15)])
        assert per_side_quant(opp, SPOT) is None

    def test_no_spot_returns_none(self):
        assert per_side_quant(_strangle(), 0.0) is None


class TestFactorTaxonomyAndScope:
    """The registry that powers BOTH the dimension grouping (#1) and the per-side re-grade (#2)."""

    def test_breach_and_moneyness_are_leg_scoped(self):
        assert _factor_scope("Breach risk") == "leg"
        assert _factor_scope("Moneyness") == "leg"
        assert _factor_scope("Skew / IV-edge") == "leg"

    def test_regime_and_vrp_are_trade_scoped(self):
        assert _factor_scope("Trend drift") == "trade"
        assert _factor_scope("VRP") == "trade"
        assert _factor_scope("Term structure") == "trade"

    def test_unmapped_factor_defaults_to_trade(self):
        assert _factor_scope("Some Unknown Factor") == "trade"
        assert _factor_dimension("Some Unknown Factor") is None

    def test_every_taxonomy_factor_has_a_valid_scope(self):
        for label, (dim, scope) in _FACTOR_TAXONOMY.items():
            assert scope in ("leg", "trade"), label
            assert isinstance(dim, str) and dim


class TestMergeScoped:
    """A per-side factor list = LEG factors from the side re-grade, TRADE factors from the whole grade."""

    def test_leg_factors_taken_from_side_trade_from_whole(self):
        whole = [{"label": "Breach risk", "points": -5}, {"label": "VRP", "points": 3},
                 {"label": "Trend drift", "points": -2}]
        side = [{"label": "Breach risk", "points": -1}, {"label": "VRP", "points": 99},
                {"label": "Trend drift", "points": 99}]
        merged = {f["label"]: f["points"] for f in _merge_scoped(whole, side)}
        assert merged["Breach risk"] == -1     # leg-scoped → side value
        assert merged["VRP"] == 3              # trade-scoped → whole value (side's 99 ignored)
        assert merged["Trend drift"] == -2     # trade-scoped → whole value

    def test_side_only_leg_factor_is_kept(self):
        whole = [{"label": "VRP", "points": 3}]
        side = [{"label": "VRP", "points": 3}, {"label": "Undefined risk", "points": -4}]
        merged = {f["label"]: f["points"] for f in _merge_scoped(whole, side)}
        assert merged["Undefined risk"] == -4  # leg-scoped, only on the side → kept


class TestSideMaxLoss:
    """The per-side Undefined-risk flag: naked side → None (unbounded), spread side → finite."""

    def test_naked_short_call_side_is_unbounded(self):
        legs = [{"strike": 110, "type": "call", "action": "SELL"}]
        assert _side_max_loss(legs, is_put=False) is None

    def test_call_spread_side_is_defined(self):
        legs = [{"strike": 110, "type": "call", "action": "SELL"},
                {"strike": 120, "type": "call", "action": "BUY"}]
        assert _side_max_loss(legs, is_put=False) is not None

    def test_naked_short_put_side_is_unbounded(self):
        legs = [{"strike": 90, "type": "put", "action": "SELL"}]
        assert _side_max_loss(legs, is_put=True) is None

    def test_put_spread_side_is_defined(self):
        legs = [{"strike": 90, "type": "put", "action": "SELL"},
                {"strike": 80, "type": "put", "action": "BUY"}]
        assert _side_max_loss(legs, is_put=True) is not None

    def test_long_only_side_is_defined(self):
        legs = [{"strike": 80, "type": "put", "action": "BUY"}]
        assert _side_max_loss(legs, is_put=True) == 0.0
