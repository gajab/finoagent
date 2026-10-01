"""Bond math — reference values (Excel PRICE/YIELD docs) + reconciliation invariants."""
from datetime import date

import pytest

from app.services import bond_math as bm


def _xl_bond():
    return bm.BondSpec(maturity=date(2017, 11, 15), coupon=0.0575, freq=2, day_count="30/360")


def test_excel_price_reference():
    # Microsoft docs: PRICE(2/15/2008, 11/15/2017, 5.75%, 6.5%, 100, 2, 0) = 94.63436
    p = bm.clean_from_yield(_xl_bond(), date(2008, 2, 15), 0.065)
    assert abs(p - 94.63436) < 1e-4


def test_excel_yield_reference():
    # Microsoft docs: YIELD(2/15/2008, 11/15/2016, 5.75%, 95.04287, 100, 2, 0) = 6.5%
    spec = bm.BondSpec(maturity=date(2016, 11, 15), coupon=0.0575, freq=2, day_count="30/360")
    y = bm.yield_from_clean(spec, date(2008, 2, 15), 95.04287)
    assert abs(y - 0.065) < 1e-6


def test_par_bond_yields_coupon_and_dv01_reconciles():
    spec = bm.BondSpec(maturity=date(2036, 3, 15), coupon=0.045, freq=2, day_count="ACT/ACT")
    a = bm.analytics(spec, date(2026, 3, 15), clean=100.0)
    assert abs(a["ytm"] - 0.045) < 1e-9
    assert abs(a["dv01"] - a["eff_duration"] * a["dirty"] * 1e-4) < 1e-12
    krd = bm.key_rate_durations(spec, date(2026, 3, 15), a["ytw"], a["eff_duration"])
    assert abs(sum(krd.values()) - a["eff_duration"]) < 1e-9


def test_premium_callable_ytw_is_call():
    spec = bm.BondSpec(maturity=date(2036, 6, 1), coupon=0.05, freq=2, calls=[(date(2029, 6, 1), 100.0)])
    a = bm.analytics(spec, date(2026, 9, 28), clean=106.0)
    assert a["ytw_kind"] == "call" and a["ytw"] < a["ytm"]
    assert a["eff_duration"] < a["mod_duration"]   # call option shortens duration


def test_muni_after_tax_equals_pretax_and_tey_grossed_up():
    spec = bm.BondSpec(maturity=date(2031, 9, 28), coupon=0.035, freq=2)
    r = bm.TaxRates(fed=0.35, state=0.0, niit=0.0)
    tr = bm.TaxTreatment(fed_taxable=False, state_taxable=False, muni=True)
    out = bm.after_tax_yield(spec, date(2026, 9, 28), 100.0, r, tr)
    assert abs(out["after_tax"] - out["pre_tax"]) < 1e-9
    assert abs(out["tey"] - out["after_tax"] / 0.65) < 1e-9


def test_tips_ref_cpi_interpolation():
    cpi = {(2026, 6): 330.0, (2026, 7): 331.0}
    # Oct 16: M-3 = Jul? No — M-3 of Oct is Jul, M-2 is Aug → need those; use Sep 1 → M-3 Jun, M-2 Jul
    assert abs(bm.ref_cpi(date(2026, 9, 1), cpi) - 330.0) < 1e-12
    assert abs(bm.ref_cpi(date(2026, 9, 16), cpi) - (330.0 + 15 / 30 * 1.0)) < 1e-12


# ---------------------------------------------------------------------------
# Inflation path (short-term 3y, then long-run average)
# ---------------------------------------------------------------------------
def test_inflation_path_compounds_short_then_long():
    p = bm.InflationPath(0.04, 0.02)
    assert p.factor(2) == pytest.approx(1.04 ** 2)
    assert p.factor(10) == pytest.approx(1.04 ** 3 * 1.02 ** 7)
    assert p.avg(3) == pytest.approx(0.04) and 0.02 < p.avg(30) < 0.025
    assert p.rate_at(2.9) == 0.04 and p.rate_at(3) == 0.02
    assert bm.as_path(0.025) == bm.InflationPath(0.025, 0.025)


def test_tips_after_tax_yield_uses_the_path_and_flat_path_matches_a_rate():
    spec = bm.BondSpec(coupon=0.0125, maturity=date(2036, 7, 15), freq=2, day_count="ACT/ACT", issue=date(2026, 7, 15))
    settle = date(2026, 9, 29)
    rates, tr = bm.TaxRates(fed=0.32, state=0.0685, niit=0.038), bm.TaxTreatment(state_taxable=False, tips=True)
    flat = bm.after_tax_yield(spec, settle, 98.0, rates, tr, inflation=0.025)
    same = bm.after_tax_yield(spec, settle, 98.0, rates, tr, inflation=bm.InflationPath(0.025, 0.025))
    assert flat["after_tax"] == pytest.approx(same["after_tax"], abs=1e-12)
    hot = bm.after_tax_yield(spec, settle, 98.0, rates, tr, inflation=bm.InflationPath(0.05, 0.025))
    assert hot["after_tax"] > flat["after_tax"] and hot["pre_tax"] == pytest.approx(flat["pre_tax"])
    # tax-advantaged TIPS: after-tax is the NOMINAL yield (real + expected inflation), real kept separately
    ira = bm.after_tax_yield(spec, settle, 98.0, rates, bm.TaxTreatment(taxable_account=False, tips=True), inflation=0.025)
    assert ira["after_tax"] == pytest.approx((1 + ira["pre_tax"]) * 1.025 - 1, abs=1e-9)
    assert ira["after_tax_real"] == pytest.approx(ira["pre_tax"])
