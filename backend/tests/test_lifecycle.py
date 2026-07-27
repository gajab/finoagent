"""Tests for lifecycle_service — the Risk / PM / Trader desk metrics."""

import numpy as np
import pytest

from app.services.lifecycle_service import (
    algorithmic_exit,
    higher_order_greeks,
    payoff_distribution_metrics,
    pm_ratios,
    portfolio_risk,
)


class TestHigherOrderGreeks:
    def test_covered_call_delta_is_stock_minus_short_call(self):
        # 100 shares (+100Δ) short a 55 call → net delta well under 100.
        g = higher_order_greeks(
            [{"strike": 55, "right": "C", "sign": -1, "qty": 1, "iv": 0.30, "dte_years": 30 / 365}],
            spot=53, stock_shares=100,
        )
        assert 40 < g["net_delta"] < 100
        assert g["net_theta"] > 0            # short option collects decay
        assert g["net_vega"] < 0             # short vol
        assert g["net_gamma"] < 0            # short gamma

    def test_long_call_has_positive_vanna_and_all_keys(self):
        g = higher_order_greeks(
            [{"strike": 100, "right": "C", "sign": 1, "qty": 1, "iv": 0.30, "dte_years": 30 / 365}],
            spot=100,
        )
        for k in ("net_delta", "net_gamma", "net_vega", "net_theta", "net_vanna", "net_charm", "net_volga"):
            assert k in g
        assert g["net_vega"] > 0             # long vol
        assert g["net_theta"] < 0            # long option bleeds theta

    def test_greeks_scale_with_quantity(self):
        one = higher_order_greeks([{"strike": 100, "right": "C", "sign": 1, "qty": 1, "iv": 0.3, "dte_years": 0.1}], 100)
        five = higher_order_greeks([{"strike": 100, "right": "C", "sign": 1, "qty": 5, "iv": 0.3, "dte_years": 0.1}], 100)
        assert abs(five["net_vega"] - 5 * one["net_vega"]) < 0.05

    def test_expired_leg_contributes_nothing(self):
        g = higher_order_greeks([{"strike": 100, "right": "C", "sign": 1, "qty": 1, "iv": 0.3, "dte_years": 0}], 100, stock_shares=50)
        assert g["net_delta"] == 50.0        # only the stock leg

    def test_vanna_sign_flips_across_the_money(self):
        # ∂Δ/∂σ > 0 for an OTM call (more vol → more chance ITM), < 0 for ITM.
        otm = higher_order_greeks([{"strike": 120, "right": "C", "sign": 1, "qty": 1, "iv": 0.3, "dte_years": 30 / 365}], 100)
        itm = higher_order_greeks([{"strike": 80, "right": "C", "sign": 1, "qty": 1, "iv": 0.3, "dte_years": 30 / 365}], 100)
        assert otm["net_vanna"] > 0 and itm["net_vanna"] < 0

    def test_charm_pulls_otm_delta_down_itm_delta_up(self):
        # Δ decays toward 0 for an OTM call (charm<0) and toward 1 for ITM (charm>0).
        otm = higher_order_greeks([{"strike": 120, "right": "C", "sign": 1, "qty": 1, "iv": 0.3, "dte_years": 30 / 365}], 100)
        itm = higher_order_greeks([{"strike": 80, "right": "C", "sign": 1, "qty": 1, "iv": 0.3, "dte_years": 30 / 365}], 100)
        assert otm["net_charm"] < 0 and itm["net_charm"] > 0

    def test_put_call_parity_synthetic_long_is_pure_forward(self):
        # +call −put at the same strike = a forward: Δ≈+100, and vega/gamma/vanna cancel.
        syn = higher_order_greeks([
            {"strike": 100, "right": "C", "sign": 1, "qty": 1, "iv": 0.3, "dte_years": 30 / 365},
            {"strike": 100, "right": "P", "sign": -1, "qty": 1, "iv": 0.3, "dte_years": 30 / 365},
        ], 100)
        assert abs(syn["net_delta"] - 100) < 0.5
        assert abs(syn["net_vega"]) < 0.1 and abs(syn["net_gamma"]) < 1e-3
        assert abs(syn["net_vanna"]) < 0.05


class TestPmRatios:
    def _covered_call_scan(self):
        prices = np.linspace(53 * 0.6, 53 * 1.4, 49)
        pnl = [100 * (min(p, 55) - 50) + 200 for p in prices]
        return [{"price": float(p), "pnl_at_expiry": float(x)} for p, x in zip(prices, pnl)]

    def test_covered_call_ratios_are_positive(self):
        r = pm_ratios(self._covered_call_scan(), 53, 0.30, 30, capital=5000, max_loss=-4800)
        assert r["omega"] > 1                 # income trade: gains dominate
        assert r["sortino"] > 0
        assert r["calmar"] is not None
        assert r["expected_return_pct"] is not None

    def test_degenerate_inputs_return_none(self):
        r = pm_ratios([], 53, 0.30, 30, 5000, -4800)
        assert r["omega"] is None and r["sortino"] is None
        r2 = pm_ratios(self._covered_call_scan(), 53, 0, 30, 5000, -4800)  # no vol
        assert r2["omega"] is None

    def test_omega_below_one_for_poor_trade(self):
        # A payoff that mostly loses → Omega < 1.
        prices = np.linspace(80, 120, 49)
        pnl = [-(abs(p - 100)) * 50 for p in prices]   # loses everywhere except ATM
        scen = [{"price": float(p), "pnl_at_expiry": float(x)} for p, x in zip(prices, pnl)]
        r = pm_ratios(scen, 100, 0.30, 30, capital=1000, max_loss=-1000)
        assert r["omega"] is None or r["omega"] < 1

    def test_no_downside_caps_ratios(self):
        # A guaranteed-profit payoff (box) has no downside → capped, not None/blank.
        scen = [{"price": float(p), "pnl_at_expiry": 500.0} for p in np.linspace(80, 120, 49)]
        r = pm_ratios(scen, 100, 0.30, 30, capital=10000, max_loss=None)
        assert r["omega"] == 99.99 and r["sortino"] == 99.99


class TestDistributionMetricsConsistency:
    """EV, Exp-Return, Omega and Sortino come from ONE weights array, so they can
    NEVER disagree in sign — the bug where EV was +$108 but Exp-Return −1.5%."""

    def _dram(self):
        from app.services.trade_math import expiry_payoff
        legs = [{"strike": 30, "right": "C", "sign": 1, "qty": 1},
                {"strike": 60, "right": "P", "sign": 1, "qty": 1},
                {"strike": 35, "right": "P", "sign": -1, "qty": 4},
                {"strike": 30, "right": "P", "sign": 1, "qty": 3},
                {"strike": 70, "right": "C", "sign": -1, "qty": 1}]
        prices = list(np.linspace(28, 86, 201))
        pnls = [expiry_payoff(legs, p, -2936) for p in prices]
        return prices, pnls

    def _weights(self, prices, spot, iv, dte):
        import math
        mids = [(prices[i] + prices[i + 1]) / 2 for i in range(len(prices) - 1)]
        return [math.exp(-0.5 * (math.log(m / spot) / (iv * math.sqrt(dte / 365))) ** 2) for m in mids]

    def test_signs_agree_low_iv(self):
        prices, pnls = self._dram()
        d = payoff_distribution_metrics(prices, pnls, self._weights(prices, 56.9, 0.35, 93), 2936, -1936, 93)
        pos = d["expected_value"] > 0
        assert pos == (d["expected_return_pct"] > 0) == (d["omega"] > 1) == (d["sortino"] > 0)

    def test_signs_agree_high_iv(self):
        # Even when a wide (skew-inflated) vol drags EV negative, all four flip together.
        prices, pnls = self._dram()
        d = payoff_distribution_metrics(prices, pnls, self._weights(prices, 56.9, 1.10, 93), 2936, -1936, 93)
        neg = d["expected_value"] < 0
        assert neg == (d["expected_return_pct"] < 0) == (d["omega"] < 1) == (d["sortino"] < 0)

    def test_ev_matches_dollar_and_pct(self):
        prices, pnls = self._dram()
        d = payoff_distribution_metrics(prices, pnls, self._weights(prices, 56.9, 0.35, 93), 2936, -1936, 93)
        # Exp-Return% is exactly EV / capital.
        assert abs(d["expected_return_pct"] - d["expected_value"] / 2936 * 100) < 0.05

    def test_empty_weights_returns_none(self):
        d = payoff_distribution_metrics([50, 60], [0, 100], [0.0], 1000, -500, 30)
        assert d["expected_value"] is None and d["omega"] is None


class TestPortfolioRisk:
    def _positions(self):
        return [
            {"ticker": "DRAM", "spot": 53, "net_delta": 24.0, "net_gamma": -0.67, "net_vega": -5.7, "iv": 0.30},
            {"ticker": "SMH", "spot": 590, "net_delta": -0.01, "net_gamma": 0.0, "net_vega": -0.13, "iv": 0.28},
        ]

    def test_var_ordering_and_positivity(self):
        r = portfolio_risk(self._positions(), horizon_days=1, n_sims=20000)
        assert r["var_99"] >= r["var_95"] > 0          # 99% tail ≥ 95% tail
        assert r["cvar_95"] >= r["var_95"]             # CVaR ≥ VaR
        assert r["n_underlyings"] == 2

    def test_stress_crash_loses_for_short_vol_book(self):
        r = portfolio_risk(self._positions(), n_sims=5000)
        crash = next(s for s in r["stress_tests"] if "Crash" in s["name"])
        assert crash["pnl"] < 0                         # short-vega book hurt by a crash+vol spike

    def test_aggregates_same_ticker(self):
        pos = [
            {"ticker": "AAPL", "spot": 200, "net_delta": 50, "net_gamma": 0.1, "net_vega": 10, "iv": 0.25},
            {"ticker": "AAPL", "spot": 200, "net_delta": -30, "net_gamma": -0.05, "net_vega": -4, "iv": 0.25},
        ]
        r = portfolio_risk(pos, n_sims=5000)
        assert r["n_underlyings"] == 1                  # one book
        assert r["by_underlying"][0]["net_delta"] == 20.0

    def test_empty_portfolio_is_safe(self):
        r = portfolio_risk([], n_sims=1000)
        assert r["var_95"] is None and r["stress_tests"] == []

    def test_var_is_reproducible_with_seed(self):
        a = portfolio_risk(self._positions(), n_sims=10000, seed=42)
        b = portfolio_risk(self._positions(), n_sims=10000, seed=42)
        assert a["var_95"] == b["var_95"]

    def test_var_scales_linearly_with_delta(self):
        one = portfolio_risk([{"ticker": "X", "spot": 100, "net_delta": 100, "net_gamma": 0, "net_vega": 0, "iv": 0.3}], n_sims=40000, seed=1)
        two = portfolio_risk([{"ticker": "X", "spot": 100, "net_delta": 200, "net_gamma": 0, "net_vega": 0, "iv": 0.3}], n_sims=40000, seed=1)
        assert abs(two["var_95"] / one["var_95"] - 2.0) < 0.05

    def test_linear_var_matches_parametric(self):
        # 1-day 95% VaR of a pure-delta book ≈ 1.645·Δ·S·σ·√(1/365).
        import math
        r = portfolio_risk([{"ticker": "X", "spot": 100, "net_delta": 100, "net_gamma": 0, "net_vega": 0, "iv": 0.3}], n_sims=80000, seed=3)
        analytic = 1.645 * 100 * 100 * 0.3 * math.sqrt(1 / 365)
        assert abs(r["var_95"] - analytic) / analytic < 0.06

    def test_stress_is_monotonic_in_shock_size(self):
        # For a short-vega/short-gamma book, a bigger down-shock loses more.
        r = portfolio_risk(self._positions(), n_sims=5000)
        s = {x["name"]: x["pnl"] for x in r["stress_tests"]}
        assert s["Crash −20% + vol spike"] < s["Selloff −10% + vol"] < s["Drift −5% + vol"]


class TestAlgorithmicExit:
    """Tier-2 scored exit engine — base quality + lifecycle adjustments + overrides."""

    _GOOD = {"omega": 2.5, "pop": 92, "sortino": 3.0, "expected_return_pct": 2.0}
    _BAD = {"omega": 0.8, "pop": 45, "sortino": -0.1, "expected_return_pct": -1.5}

    def test_fresh_strong_trade_strong_holds(self):
        r = algorithmic_exit(self._GOOD, 400, 20000, -19000, 100, 0.1, 40, captured_pct=10, unrealized_pnl=10)
        assert r["signal"] == "STRONG_HOLD" and r["score"] >= 70
        assert 0 <= r["base_quality"] <= 100 and set(r["subscores"]) == {"edge", "pop", "sortino", "tail", "carry"}

    def test_profit_capture_downgrades(self):
        # Same pristine structure loses conviction as profit is banked.
        s10 = algorithmic_exit(self._GOOD, 400, 20000, -19000, 100, 0.1, 20, 10, 10)["score"]
        s60 = algorithmic_exit(self._GOOD, 400, 20000, -19000, 100, 0.1, 20, 60, 60)["score"]
        assert s60 < s10

    def test_85pct_capture_forces_close(self):
        r = algorithmic_exit(self._GOOD, 400, 20000, -19000, 100, 0.1, 15, captured_pct=90, unrealized_pnl=90)
        assert r["signal"] == "CLOSE" and any("captured" in o for o in r["overrides"])

    def test_near_max_loss_forces_close(self):
        r = algorithmic_exit(self._GOOD, 400, 20000, -1000, 100, 0.1, 30, captured_pct=5, unrealized_pnl=-850)
        assert r["signal"] == "CLOSE"

    def test_expiry_gamma_override(self):
        r = algorithmic_exit(self._GOOD, 400, 20000, -19000, 100, 0.1, 1, captured_pct=20, unrealized_pnl=20)
        assert r["signal"] in ("CONSIDER_CLOSE", "CLOSE")

    def test_weak_trade_closes(self):
        r = algorithmic_exit(self._BAD, 5000, 20000, -19000, 100, 0.0, 30, captured_pct=5, unrealized_pnl=5)
        assert r["signal"] in ("CONSIDER_CLOSE", "CLOSE") and r["score"] < 45

    def test_buildup_is_auditable(self):
        r = algorithmic_exit(self._GOOD, 400, 20000, -19000, 100, 0.1, 20, 60, 60)
        # hold_score = base_quality + Σ adjustment pts (before clamping/overrides)
        expect = max(0, min(100, r["base_quality"] + sum(a["pts"] for a in r["adjustments"])))
        assert abs(r["score"] - expect) <= 1


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
