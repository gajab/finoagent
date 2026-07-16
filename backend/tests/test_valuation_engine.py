"""Tests for valuation_engine — the structural pro-forma P&L.

Run from backend/:
    source venv/bin/activate
    python -m pytest tests/test_valuation_engine.py -v

The most important test is ``TestCovariance`` — it proves the engine composes drivers
through an income statement (so a price-cut-for-volume can make operating income FALL
even as revenue rises), which is the whole reason a naive Σ(ΔEPS) is wrong. Everything
here is synthetic and deterministic — no network.
"""

import pytest

from app.services.valuation_engine import (
    PnL, Drivers, OperatingLeverage, Claim,
    estimate_operating_leverage, pnl_anchored, proforma, base_eps, scenario_target,
    warranted_multiple, assemble,
)


# ── Operating-leverage regression ──────────────────────────────────────────

class TestOperatingLeverage:
    def test_recovers_incremental_margin(self):
        # op = 0.30·revenue − 10  →  slope 0.30, perfect fit
        rev = [100, 110, 120, 130]
        op = [0.30 * r - 10 for r in rev]         # [20, 23, 26, 29]
        lev = estimate_operating_leverage(rev, op)
        assert lev.source == "regression"
        assert round(lev.inc_margin, 2) == 0.30
        assert lev.r2 >= 0.99
        # incremental (0.30) exceeds the average margin (29/130 ≈ 0.22) → positive leverage
        assert lev.inc_margin > lev.avg_margin

    def test_noisy_sample_falls_back_to_average_margin(self):
        # Near-flat / lumpy op income → implausible slope → fall back, don't fabricate leverage
        rev = [100, 110, 120, 130]
        op = [40, 41, 40, 41]
        lev = estimate_operating_leverage(rev, op)
        assert lev.source == "avg-margin-fallback"
        assert lev.inc_margin == lev.avg_margin      # no leverage assumed

    def test_too_short_history_falls_back(self):
        lev = estimate_operating_leverage([100, 110], [20, 22])
        assert lev.source == "avg-margin-fallback"


# ── Base reconciliation ────────────────────────────────────────────────────

class TestBaseReconciliation:
    def test_anchored_base_reproduces_reported_eps_exactly(self):
        # net income 120, 100 shares → EPS must be exactly 1.20 with no drivers
        base = pnl_anchored(revenue=1000, op_income=200, tax_rate=0.25, shares=100, net_income=120)
        lev = OperatingLeverage(inc_margin=0.35, r2=1.0, avg_margin=0.20, source="regression")
        assert base_eps(base, lev) == 1.20

    def test_nonop_backed_out_from_bottom_line(self):
        # nonop = op − net/(1−tax) = 200 − 120/0.75 = 40
        base = pnl_anchored(revenue=1000, op_income=200, tax_rate=0.25, shares=100, net_income=120)
        assert base.nonop_expense == 40.0


# ── Operating leverage inside the pro-forma ────────────────────────────────

class TestLeverageInProforma:
    def test_revenue_growth_expands_margin(self):
        base = pnl_anchored(revenue=1000, op_income=200, tax_rate=0.25, shares=100, net_income=150)
        lev = OperatingLeverage(inc_margin=0.35, r2=1.0, avg_margin=0.20, source="regression")
        base_margin = base.op_income / base.revenue * 100          # 20%
        pf = proforma(base, lev, Drivers(rev_growth=0.10))
        # op = 200 + 0.35·100 = 235 on revenue 1100 → 21.4% > 20%
        assert pf["op_income"] == pytest.approx(235.0)
        assert pf["op_margin_pct"] > base_margin


# ── THE test: covariance / linearity fallacy ───────────────────────────────

class TestCovariance:
    """A price-cut-for-volume is ONE coupled driver set (revenue up, margin down).
    The structural P&L nets them — and the sign depends on the business's economics,
    which a naive sum-of-independent-ΔEPS cannot capture."""

    def _price_cut(self):
        return Drivers(rev_growth=0.10, margin_delta_bps=-300)   # +10% volume, −300bps margin

    def test_high_fixed_cost_business_op_income_falls(self):
        # Airline-like: thin 10% avg margin, high 30% incremental margin
        base = pnl_anchored(revenue=1000, op_income=100, tax_rate=0.20, shares=100, net_income=70)
        lev = OperatingLeverage(inc_margin=0.30, r2=0.9, avg_margin=0.10, source="regression")
        pf = proforma(base, lev, self._price_cut())
        # op = 100 + 0.30·100 + (−0.03)·1100 = 100 + 30 − 33 = 97  →  FELL despite revenue +10%
        assert pf["op_income"] == pytest.approx(97.0)
        assert pf["op_income"] < base.op_income

    def test_high_incremental_margin_business_op_income_rises(self):
        # Software-like: 40% avg margin, 70% incremental
        base = pnl_anchored(revenue=1000, op_income=400, tax_rate=0.20, shares=100, net_income=300)
        lev = OperatingLeverage(inc_margin=0.70, r2=0.99, avg_margin=0.40, source="regression")
        pf = proforma(base, lev, self._price_cut())
        # op = 400 + 0.70·100 + (−0.03)·1100 = 400 + 70 − 33 = 437  →  ROSE
        assert pf["op_income"] == pytest.approx(437.0)
        assert pf["op_income"] > base.op_income

    def test_same_drivers_opposite_sign_by_economics(self):
        # Identical driver set → opposite operating-income direction: that's the discrimination
        d = self._price_cut()
        thin = pnl_anchored(1000, 100, 0.20, 100, 70)
        fat = pnl_anchored(1000, 400, 0.20, 100, 300)
        thin_lev = OperatingLeverage(0.30, 0.9, 0.10, "regression")
        fat_lev = OperatingLeverage(0.70, 0.99, 0.40, "regression")
        assert proforma(thin, thin_lev, d)["op_income"] < thin.op_income
        assert proforma(fat, fat_lev, d)["op_income"] > fat.op_income


# ── Buyback accretion ──────────────────────────────────────────────────────

class TestBuyback:
    def test_share_reduction_is_accretive(self):
        # EPS large enough that 2-dp rounding doesn't swamp the ~2% signal
        base = pnl_anchored(revenue=1000, op_income=200, tax_rate=0.25, shares=10, net_income=500)
        lev = OperatingLeverage(0.20, 1.0, 0.20, "avg-margin-fallback")
        be = base_eps(base, lev)                    # 500/10 = 50.0
        pf = proforma(base, lev, Drivers(share_change=-0.02))
        # net income unchanged, shares ×0.98 → EPS ×1/0.98 ≈ +2.04%
        assert (pf["eps"] / be - 1) == pytest.approx(0.0204, abs=1e-3)


# ── Scenario → target ──────────────────────────────────────────────────────

class TestScenarioTarget:
    def test_target_is_eps_times_multiple(self):
        base = pnl_anchored(revenue=1000, op_income=200, tax_rate=0.25, shares=100, net_income=150)
        lev = OperatingLeverage(0.20, 1.0, 0.20, "avg-margin-fallback")
        out = scenario_target(base, lev, Drivers(), multiple=15)
        assert out["target"] == round(out["eps"] * 15, 2)


# ── Cross-sectional warranted multiple (Fix 3) ─────────────────────────────

class TestWarrantedMultiple:
    def test_intact_hypergrowth_caps(self):
        # 40% growth, 60% margin → PEG blows past the cap → clamped, not infinite
        assert warranted_multiple(40, 0.60) == 45.0

    def test_broken_compounder_floors_near_market_not_history(self):
        # The historical-multiple trap: growth breaks 40→8, so the fair multiple must
        # collapse toward the market base rate — NOT stay at the glory-days ~40x.
        assert warranted_multiple(8, 0.60) < 25
        assert warranted_multiple(8, 0.60) > 8

    def test_no_growth_hits_floor(self):
        assert warranted_multiple(0, 0.60) == 8.0

    def test_thin_margin_earns_lower_multiple_than_fat_margin(self):
        # Same growth, cyclical thin-margin business warrants less than a fat-margin one
        assert warranted_multiple(15, 0.09) < warranted_multiple(15, 0.60)

    def test_airline_like_is_market_plausible(self):
        # DAL-ish: ~10% growth, ~9% margin → low-double-digit P/E (its real ~10x)
        assert 9 <= warranted_multiple(10, 0.09) <= 13


# ── Claim aggregation → target + range + confidence ────────────────────────

class TestAssemble:
    def _inputs(self):
        base = pnl_anchored(revenue=100e9, op_income=30e9, tax_rate=0.15,
                            shares=10e9, net_income=25e9)          # base EPS 2.50
        lev = OperatingLeverage(inc_margin=0.40, r2=0.9, avg_margin=0.30, source="regression")
        return base, lev

    def test_headline_is_consistent_and_range_brackets_it(self):
        base, lev = self._inputs()
        claims = [
            Claim("revenue", 0.12, "E2", label="+12% revenue (guidance)"),
            Claim("margin", 100, "E1", label="+100bps margin (disclosed)"),
            Claim("buyback", -0.02, "E1", label="2% buyback"),
        ]
        out = assemble(base, lev, claims, price=50.0, margin_quality=0.30, own_hist_pe=25)
        assert out["target"] == pytest.approx(out["eps"] * out["multiple"], abs=0.05)
        lo, hi = out["range"]
        assert lo <= out["target"] <= hi
        assert out["scenarios"]["bear"] <= out["scenarios"]["base"] <= out["scenarios"]["bull"]
        assert 0.30 <= out["confidence"] <= 0.90
        assert out["upside_pct"] == pytest.approx((out["target"] / 50.0 - 1) * 100, abs=0.1)

    def test_waterfall_flows_bridge_base_to_scenario_eps(self):
        base, lev = self._inputs()
        claims = [Claim("revenue", 0.12, "E2"), Claim("margin", 100, "E1")]
        out = assemble(base, lev, claims, margin_quality=0.30)
        bridged = out["base_eps"] + sum(s["eps_delta"] for s in out["waterfall"])
        assert bridged == pytest.approx(out["eps"], abs=0.05)   # multiple claims excluded, so flows reconcile

    def test_historical_multiple_trap_is_fenced(self):
        # own_hist_pe is only a CAP; a scenario that breaks growth still gets the low
        # warranted multiple, not the historical 40x.
        base, lev = self._inputs()
        broken = [Claim("revenue", 0.06, "E4")]      # growth fades to market
        out = assemble(base, lev, broken, margin_quality=0.30, own_hist_pe=40)
        assert out["multiple"] < 25

    def test_unanswered_rebuttal_lowers_the_target(self):
        base, lev = self._inputs()
        strong = [Claim("revenue", 0.12, "E2")]
        weak = [Claim("revenue", 0.12, "E2", unanswered=True)]
        assert (assemble(base, lev, weak, margin_quality=0.30)["target"]
                < assemble(base, lev, strong, margin_quality=0.30)["target"])

    def test_stronger_evidence_earns_more_eps(self):
        base, lev = self._inputs()
        e1 = assemble(base, lev, [Claim("revenue", 0.12, "E1")], margin_quality=0.30)
        e5 = assemble(base, lev, [Claim("revenue", 0.12, "E5")], margin_quality=0.30)
        assert e1["eps"] > e5["eps"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
